# AtlasForge WebProxy — Exploration Report

Scope: `/home/vader/AI-AtlasForge/WebProxy/` (symlink target: `/mnt/ForgeRealm/AI-AtlasForge/WebProxy/`).
Live service confirmed running: systemd user unit `atlasforge-web-proxy.service`,
PID bound to `service.py --host 127.0.0.1 --port 8765`, up since 2026-07-01.
`/health` and `/stats` were queried read-only during this exploration; no writes,
restarts, or config changes were made anywhere in the stack.

All line numbers below refer to `WebProxy/service.py` unless a different file is named.

---

## 1. Architecture — request flow, caching, rate limiting, error handling

### Request flow

```
Claude Code subagent
  -> WebSearch/WebFetch/WebResearch/PaperFetch/ImageSearch (MCP tool call)
  -> mcp_server.py  (stdin/stdout JSON-RPC 2.0 process, spawned per Claude session)
       - validates args (length caps, SSRF pre-check on URLs, domain-filter typing)
       - POSTs to http://127.0.0.1:8765/<endpoint>
  -> service.py Flask app (systemd-managed, long-lived, threaded=True)
       - re-validates URL structure + resolves/pins DNS + SSRF-checks every address
       - checks FileCache for a fresh hit; on miss, calls the provider/fetcher
       - normalizes the result into a JSON envelope, writes it to FileCache
  -> external wire: Brave API / DuckDuckGo HTML scrape / DDGS backends / arbitrary
     target site / Reddit .json API / arXiv or PDF host
  <- JSON response flows back through service.py -> mcp_server.py
       - mcp_server.py re-formats the JSON into a plain-text tool result
         (_format_search_results / _format_fetch_results) and optionally mirrors
         the raw JSON into the active investigation's artifacts directory
  <- plain text returned to the model as the WebSearch/WebFetch/... tool result
```

Two independent processes are involved per call: the MCP server (one process per
Claude session, talking JSON-RPC over stdio) and the always-on Flask service
(one process for the whole machine, talking HTTP). The MCP server holds **no
state** of its own beyond stage-guard bookkeeping for the unrelated
`AtlasForge*` artifact tools — all caching, rate-limit-adjacent logic, and
fetch mechanics live in `service.py`.

There is a second entry path that does not go through `mcp_server.py` at all:
`WebProxy/client.py` (a plain `requests` wrapper) and ad-hoc HTTP callers can
hit `127.0.0.1:8765` directly. Since the proxy binds to loopback only, this is
local-machine-only, but it means **service.py's own validation is the actual
enforcement boundary**, not a redundant backstop behind the MCP layer. See
Section 4 and 5.

### Caching

Every one of `/search`, `/fetch`, `/paper/fetch`, `/image_search` is a
read-through cache in front of the live call:

- Cache key = `f"{prefix}_{sha256(json.dumps(sorted_payload))}"` (`_cache_key`,
  lines 469–471). Prefixes: `search_`, `fetch_`, `paper_fetch_`,
  `image_search_`.
  - The `/fetch` cache key is **`{"url": url}` only** — `max_chars` is
    deliberately excluded from the key (line 1986). The proxy always fetches
    and caches the **full untruncated** page (`fetch_page(url, max_chars=-1)`,
    line 1993) and applies the caller's requested truncation afterward via
    `_apply_max_chars()` (lines 486–523), on both fresh fetches and cache
    hits. One cached page therefore serves any `max_chars` request.
- TTLs (env-overridable module constants, lines 88–90):
  - Search: `SEARCH_TTL_S` = 1800s (30 min)
  - Fetch: `FETCH_TTL_S` = 86400s (24h) — this is where the MCP tool
    description's "cached for 24h" claim is actually enforced
  - Paper: `PAPER_TTL_S` = 7 × 86400s (7 days)
- Expiry is **lazy, not swept**: `FileCache.get()` (lines 751–784) reads the
  stored `_cached_at` timestamp and returns `None` (cache miss) if the entry
  is stale, unparseable, or has a `_cached_at` in the future (clock-skew
  guard). Nothing ever deletes a stale file proactively — it just sits on
  disk until the same key is written again.
- Storage: flat JSON files under `atlasforge_data/web_proxy_cache/` (default;
  overridable via `ATLASFORGE_WEB_PROXY_CACHE_DIR`), one file per cache key,
  written atomically (temp file + `os.replace`). Confirmed on disk — the live
  cache directory (currently resolved to
  `/mnt/ForgeRealm/AI-AtlasForge/WebProxy/atlasforge_data/web_proxy_cache/`)
  contains `search_<sha256>.json`, `fetch_<sha256>.json`, and
  `paper_fetch_<sha256>.json` files matching this scheme, plus 269+
  subdirectories under `atlasforge_data/paper_artifacts/`.
- No cache invalidation endpoint or LRU/size eviction exists anywhere. `GET
  /cache` is read-only observability, capped at `MAX_OBSERVABILITY_ENTRIES`
  (default 1000) entries *listed*, not a cap on how many can exist.
- Downloaded images are cached as raw binary files (not JSON) under
  `atlasforge_data/web_proxy_cache/images/`, keyed by `sha256(url)[:32] + ext`
  — i.e., content-addressed by source URL, not by content hash, so a URL that
  starts serving different image bytes silently overwrites the old file.

### Rate limiting

**There is none.** No retry/backoff (`urllib3.Retry`, manual retry loop,
exponential backoff) anywhere in the codebase. No per-domain throttle, no
token bucket, no outbound concurrency semaphore. The only rate-limit-adjacent
code is reactive classification, not prevention: `_brave_fallback_reason()`
(lines 815–826) inspects a caught Brave exception for HTTP 402/429 purely to
decide whether to cascade to DuckDuckGo — it never slows down or retries the
Brave call itself. See Section 4 for the practical implications.

### Concurrency

Flask runs via the Werkzeug dev server with `threaded=True` (`main()`, line
~2388) — every request gets its own thread, but this is explicitly the
*development* server, not a production WSGI server (no gunicorn/uwsgi/waitress
anywhere in the stack). No global lock, no `Flask-Limiter`, no per-route
throttling middleware. `MAX_CONTENT_LENGTH` (tied to `MAX_REQUEST_BYTES`,
default 2 MiB) is a request-body size cap, not a rate limiter. `--debug` is
restricted to loopback-only binds as a safety measure unrelated to
concurrency.

### Error handling

- Timeouts: `DEFAULT_TIMEOUT_S` env-overridable, default 20s for
  search/paper/generic calls; per-request `timeout_s` validated as a finite
  positive float; JS rendering has its own `JS_RENDER_TIMEOUT_S` (30s default)
  plus a post-load hydration wait (`JS_RENDER_HYDRATE_MS`, 2500ms default).
- Uniform per-endpoint pattern: wrap core logic in try/except, generate a
  short correlation ID (`uuid4().hex[:12]`), log with `logger.exception`, and
  return a JSON error body containing `error`, `correlation_id`, and only
  caller-supplied context (query/url) — internal exception text is
  deliberately **not** echoed back for `UnsafeUrlError` at the runtime
  (post-DNS) check, to avoid leaking topology info. The **pre-DNS structural**
  reject does return its specific reason string, since that's derived purely
  from caller input and is safe to disclose. This is a genuinely careful
  asymmetry — it closes an SSRF information-disclosure oracle without making
  every error message useless.
- Target-site errors: `response.raise_for_status()` plus an explicit
  `200 <= status < 300` assertion means **any non-2xx from the target site
  becomes a generic HTTP 502 "fetch failed"** from the proxy — the caller
  never sees the target's actual 403/404/500, only a correlation ID to grep
  the proxy's own logs. Reddit is the one exception: it often returns 200
  with an HTML interstitial (rate-limit/login wall) instead of JSON, so that
  path is handled by a content-type gate rather than a status check.
- `/research` and `/image_search` (with `fetch_top_n`) isolate per-item
  failures — one bad URL in a batch produces an error entry in the results
  array rather than failing the whole call.
- SSRF: enforced twice — a cheap pre-DNS structural check
  (`_validate_url_structure`, scheme/control-char/legacy-numeric-IP checks)
  and a DNS-resolution-time check (`_resolve_first_safe_ip`) that inspects
  **every** address a hostname resolves to, not just the first (defends
  against DNS round-robin tricks). The resolved IP is then pinned into the
  actual outbound TCP connection via a custom `HTTPAdapter`
  (`PinnedIPAdapter`) that rewrites the connection target to the
  already-validated IP while preserving Host header and TLS SNI — this closes
  the classic DNS-rebinding TOCTOU gap where a second independent resolution
  inside urllib3 could land on a different, unsafe address than the one that
  was checked. The adapter also refuses to route through any
  `HTTP_PROXY`/`HTTPS_PROXY` env var, closing a side-channel around the pin.
  `mcp_server.py` performs its own equivalent SSRF/hostname checks before ever
  reaching the wire, so a well-behaved MCP-path caller is checked twice; a
  caller that skips the MCP layer (Section 5) still hits service.py's checks,
  which are the real backstop.

---

## 2. Full capability inventory

All five tools are declared in `mcp_server.py`'s `TOOLS` list and dispatched
in `handle_tool_call()`.

### WebSearch
- **Input:** `query` (required, string, min length 2), `allowed_domains`
  (array of strings), `blocked_domains` (array of strings).
- **Behavior:** POSTs `{"query": query, "count": 10}` to `/search` (count is
  hardcoded to 10 at the MCP layer regardless of caller intent — there is no
  way to request a different count through this tool; only `WebResearch`
  exposes `count`). Domain filters are applied **client-side in
  mcp_server.py**, after the proxy already returned results — filtering
  happens by exact-or-suffix hostname match (`_host_matches`), with strict
  typing enforcement (a bare string passed as `allowed_domains` is rejected
  rather than silently iterated char-by-char).
- **Backend:** `SearchProvider.search()` — Brave (if key configured) → DDG
  HTML scrape → DDGS multi-backend fallback (google/bing/yahoo/mojeek/
  wikipedia), auto-cascading on failure or empty results.
- **Output:** plain-text listing of title/URL/snippet per result, plus
  provider name and cache-hit flag.

### WebFetch
- **Input:** `url` (required), `prompt` (declared but explicitly ignored —
  the schema keeps it only so the tool signature matches Claude Code's
  built-in `WebFetch`, which the proxy is meant to transparently replace).
- **Behavior:** validates/SSRF-checks the URL, then POSTs
  `{"url": url, "max_chars": 100000}` to `/fetch` (100,000 is
  `MAX_MAX_CHARS`, the MCP layer's ceiling — effectively "no meaningful cap"
  for ordinary pages).
- **Auto-routing:** Reddit URLs (`*.reddit.com`) are silently redirected
  server-side to Reddit's `.json` API instead of HTML scraping. Image URLs
  (detected by response `Content-Type`, not file extension) are saved locally
  instead of text-extracted.
- **Output:** raw title, headings, body text, and up to 30 links (plain-text
  rendering built by `_format_fetch_results`). No summarization at any layer
  — this is a hard architectural commitment ("Returns raw extracted content
  ... No summarization").
- **Caching:** 24h TTL, keyed by URL only (see Section 1).

### WebResearch
- **Input:** `query` (required), `count` (default 5), `fetch_top_n` (default
  3), `max_chars` (default 12000, per-page).
- **Behavior:** single combined call — proxy does the search, then fetches
  the top N result pages internally and returns everything in one payload.
  Per-page fetch failures are isolated (a bad URL surfaces as `FETCH ERROR:
  <url> — <reason>` inline rather than failing the whole call).
- **Output:** search results block followed by one fetch-result block per
  successfully fetched page.

### PaperFetch
- **Input:** `url` (paper landing page or direct PDF URL, required),
  `max_chars` (default -1 = return all extracted text).
- **Behavior:** arXiv `/abs/` URLs are rewritten to `/pdf/` automatically;
  other URLs are fetched as-is expecting a PDF. Downloads the PDF (capped at
  50 MiB), extracts text page-by-page via `pypdf` (falling back to `PyPDF2`
  if `pypdf` isn't installed), persists `paper.pdf` + `paper.txt` +
  `metadata.json` under a content-addressed artifact directory
  (`sha256(pdf_url:sha256(bytes))[:24]`).
- **Output:** local artifact paths, SHA-256, byte length, `pages_extracted` /
  `page_count`, extractor name, truncation flag, and the extracted text
  itself. If extraction fails (corrupt/encrypted PDF, or neither PDF library
  installed), the call still "succeeds" at the HTTP layer — it returns the
  raw PDF plus an `extraction_error` field and empty text, rather than
  failing outright.
- **Caching:** 7-day TTL, keyed by URL.

### ImageSearch
- **Input:** `query` (required), `count` (default 5), `fetch_top_n` (default
  0 — no download unless requested), `safesearch` (`off`/`moderate`/`on`,
  default `off`).
- **Behavior:** same Brave→DDG→DDGS provider cascade as WebSearch, using
  image-specific backends (DDGS image backend defaults to `bing` only, vs.
  the wider 5-backend list for web search). If `fetch_top_n > 0`, downloads
  that many result images locally (content-type-sniffed + magic-byte
  validated, saved under `atlasforge_data/web_proxy_cache/images/`,
  dimensions read via Pillow if installed).
- **Output:** per-result title/image URL/source page URL/dimensions, plus a
  "Downloaded images" block listing local paths and byte sizes for anything
  actually fetched.

### AtlasForge* stage-guard tools (adjacent, not web-fetch related)
`AtlasForgeGetStagePolicy`, `AtlasForgeSubmitPlan`, `AtlasForgeWriteStageNote`,
`AtlasForgeSubmitReview`, `AtlasForgeSubmitPatchSummary`,
`AtlasForgeWriteMutationArtifact` are also served by the same
`mcp_server.py` process but are unrelated to web fetching — they're a
stage-gated artifact-writing guard for the conductor pipeline (PLANNING /
BUILDING / TESTING / ANALYZING / CYCLE_END / COMPLETE / REVIEW), each with its
own whitelist of writable paths/extensions per stage. Mentioned here for
completeness since they share the MCP process and TOOLS list, but they are
out of scope for "web proxy" behavior proper.

---

## 3. Content processing pipeline — what a page looks like by the time a model sees it

This is the load-bearing question, traced end to end for a normal (non-Reddit,
non-image, non-PDF) HTML page:

1. **Fetch:** `requests.Session` with a `PinnedIPAdapter`, headers
   `User-Agent: AI-AtlasForge-WebProxy/0.1 (local research proxy; contact
   admin if traffic is unexpected)` and `Accept-Language: en-US,en;q=0.8`.
   The UA is honestly self-identifying — not spoofed as a browser — for
   ordinary page fetches (Reddit and JS-rendered fetches are the two
   exceptions; see below). Body is streamed and capped at `MAX_FETCH_BYTES`
   (5 MiB default); exceeding the cap raises and discards the partial
   response rather than returning truncated garbage.
2. **Encoding:** `response.encoding` is captured before the streaming read
   closes the response (an explicit ordering fix — encoding is an attribute
   read off headers, not the stream). Decoded with the declared encoding;
   falls back through `UnicodeDecodeError` → same encoding with
   `errors="replace"`, and `LookupError` (bogus encoding name) → UTF-8 with
   `errors="replace"`.
3. **Optional JS render:** if enabled (default on) and either (a) the host is
   on a hardcoded allowlist (`chatgpt.com, chat.openai.com, claude.ai,
   poe.com, x.com, twitter.com, linkedin.com` — `js_render.py` line 48) or (b)
   the plain fetch looks thin (`text_length < 200` or `status_code == 403`)
   combined with heuristics (Cloudflare challenge markers, SPA framework
   fingerprints like `__next`/`__nuxt`, script-heavy/paragraph-poor body) —
   Playwright launches headless Chromium, navigates with
   `wait_until="networkidle"` (gracefully degrading if that never fires,
   common for SPAs with polling), waits an extra 2500ms hydration buffer, and
   extracts both the rendered HTML and `page.inner_text("body")`. The longer
   of BeautifulSoup-on-rendered-HTML vs. Playwright's own inner-text wins.
   Missing Playwright install → silent no-op, plain-requests result stands.
4. **Parse:** BeautifulSoup with the `lxml` parser. `script`, `style`,
   `noscript`, `svg`, `iframe`, `canvas` tags are `.decompose()`d outright —
   nothing else is explicitly stripped (no `nav`/`header`/`footer`/ad-class
   removal list).
5. **Main-content selection:** `soup.find("main") or soup.find("article") or
   soup.body or soup` — a flat 4-step fallback chain, **not** a
   readability-style density/scoring algorithm, not trafilatura, not
   newspaper3k. Whichever semantic container exists first wins wholesale;
   there's no attempt to detect boilerplate *within* that container beyond
   the tag-decompose step above.
6. **Text assembly:** every `h1, h2, h3, p, li, blockquote` inside the chosen
   container is whitespace-normalized and joined with blank lines between
   them. If none of those tags exist inside the container (uncommon but real
   for some pages), it falls back to `container.get_text("\n", strip=True)`
   as a single blob — the boilerplate-tolerant fallback path.
7. **Headings:** every `h1`/`h2`/`h3` inside the container, flattened into a
   list with no hierarchy/nesting preserved.
8. **Links:** every `<a href>` inside the container, resolved to absolute
   URLs, filtered to http/https only, link text capped at 160 chars. Only the
   **first 30** are rendered into the final tool-result text
   (`_format_fetch_results` in `mcp_server.py`); the full list is present in
   the underlying JSON.
9. **Whitespace normalization**, then **truncation**: `text[:max_chars]` — a
   raw character slice, not word- or sentence-boundary aware, applied
   **after** all cleaning/joining. Because the cache stores the untruncated
   text and truncation is reapplied per-request via `_apply_max_chars()`, the
   `max_chars` a model actually sees can vary between calls to the same URL
   without a re-fetch.
10. **Final shape delivered to the model** (via `_format_fetch_results`):
    plain text with a `URL:` / `Title:` / cache-hit / cache-JSON-path header
    block, then a `Headings:` bullet list (if any), then the assembled body
    text, then a trailing `Links:` section (first 30, `[text](url)`
    markdown-ish format). No JSON, no HTML tags, no metadata beyond what's in
    that header block — a single flat text document.

**Reddit deviates entirely** from steps 4–10: it's a JSON API response
(`.json` suffix on `www.reddit.com`, forced `limit=500&depth=10`), parsed into
a recursive listing walker that extracts structured posts (title, author,
score, num_comments, flair, subreddit, `selftext` capped at 2000 chars) and
comments (author, score, `body` capped at 2000 chars, depth), with best-effort
"load more comments" expansion via a separate `morechildren.json` batch call
(up to 200 IDs, chunks of 100). The final text blob is a hand-formatted digest
(`[score | Nc] title` / `[score] u/author: body`), not the BeautifulSoup
pipeline at all. Reddit fetches use a spoofed desktop Chrome User-Agent,
unlike the honest UA used elsewhere.

**Images** never go through the text pipeline — detected by
server-declared `Content-Type` (not URL extension), magic-byte validated
against the declared type, saved to a content-addressed local path, and only
`width`/`height` (via Pillow, if installed) plus byte length/content-type are
returned as text.

**PDFs** never go through the HTML pipeline either — `pypdf`/`PyPDF2`
page-by-page `.extract_text()`, pages joined with `--- Page N ---` markers.

---

## 4. Honest gaps and weaknesses

1. **No retry/backoff/rate-limiting anywhere.** A transient network blip,
   a Brave 429, or a flaky target site produces an immediate failure (502 to
   the model) with no retry. Combined with no outbound concurrency cap, an
   investigation that fans out many parallel `WebResearch`/`ImageSearch`
   calls has no protection against hammering a single upstream (Brave, or a
   single target domain) faster than that upstream tolerates — the only
   throttle in the whole stack is Flask's own thread scheduling.

2. **No robots.txt handling at all.** No `robots.txt` fetch, no
   crawl-delay honoring, no user-agent-based exclusion. This is a design gap
   the docs implicitly own ("Returns unfiltered, verbatim content... survives
   domain blocks") but it means the proxy will fetch anything within its SSRF
   allowlist regardless of what the target site's robots policy says.

3. **Main-content selection is a naive 4-step fallback, not a real
   readability algorithm.** `main` → `article` → `body` → whole soup, with no
   scoring, no link-density heuristic, no boilerplate classifier. Pages that
   put real content in a `<div>` without a semantic `<main>`/`<article>`
   wrapper (extremely common on older or hand-rolled sites, and on many news
   sites that wrap content in framework-specific divs) fall through to
   "whole body text" — which then includes nav menus, footers, cookie
   banners, related-article widgets, etc., all flattened into the same text
   blob with no way for the model to distinguish them from the actual
   article. This is the single biggest fidelity risk in the pipeline: a page
   without clean semantic HTML gets noisy output, silently, with no signal
   to the caller that extraction quality was poor (there's no "confidence" or
   "extraction_quality" field, only success/failure).

4. **Truncation is a raw character slice with no boundary-awareness**, and
   happens after all the cleaning/joining — so a `max_chars`-truncated result
   can end mid-sentence, mid-word, or mid-HTML-entity-decoded-character with
   no ellipsis marker or "truncated" flag exposed at the MCP-formatted-text
   layer (the underlying JSON does have `truncated`/`text_length` for
   PaperFetch, but plain `/fetch` responses don't expose a `truncated`
   boolean to the caller — the model has no built-in way to know it received
   a truncated vs. complete page short of comparing `text_length` against
   what it asked for).

5. **JS rendering is heuristic-gated, not universal, and the heuristics are
   coarse.** Only a 7-domain hardcoded allowlist always renders; everything
   else only renders if the plain-HTTP result already looks thin
   (`text_length < 200` or a 403) *and* matches a small set of SPA/Cloudflare
   signatures. A JS-heavy page that happens to server-render enough boilerplate
   text to clear the 200-char threshold (e.g., a cookie-consent-heavy SPA
   shell with a few hundred characters of nav/footer text but no real content)
   will never trigger rendering and will silently return junk instead of the
   real content. There's also no distinction in the final output between
   "rendered and still got junk" vs. "never attempted rendering" beyond an
   internal `rendered: true/false` flag that isn't surfaced in the
   plain-text tool result at all (`_format_fetch_results` doesn't mention it).

6. **Pagination is entirely unhandled.** There is no concept of "next page"
   anywhere in the fetch pipeline — a paginated article, forum thread, or
   search-results page is fetched exactly once, as-is; whatever content
   exists in that single response is all the model ever sees. No test in
   `tests/test_service.py` exercises pagination-following.

7. **Binary content beyond images/PDFs has no defined behavior.** The
   Content-Type branches only cover `image/*` (→ `_save_image`) and the
   PDF-specific `/paper/fetch` endpoint; a `/fetch` call against, say, a
   `.zip`, `.docx`, `.csv`, or arbitrary `application/octet-stream` URL falls
   through to the HTML/text decode path — it will attempt to decode
   arbitrary binary bytes as text (with `errors="replace"` masking the
   resulting garbage) rather than detecting and rejecting/redirecting
   non-text, non-image, non-PDF content types.

8. **Target-site HTTP status is fully swallowed.** Every non-2xx from the
   target becomes an undifferentiated 502 "fetch failed" from the model's
   point of view — a 404 (page genuinely doesn't exist), a 403 (blocked/
   paywalled), and a 500 (target's own bug) are all indistinguishable to the
   caller without cross-referencing proxy logs by correlation ID. A retry
   strategy or smarter caller can't make an informed decision (e.g., "don't
   retry a 404 but do retry a 503") because the distinguishing signal is
   discarded before it reaches the model.

9. **`/stats` is a cache-directory re-scan, not a lifetime counter, and
   disagrees with its own docs.** `docs/LOCAL_WEB_PROXY.md` documents
   `searches_total`/`fetches_total`/`provider_breakdown` fields; the live
   endpoint actually returns `cached_searches`/`cached_fetches`/
   `cached_image_searches`/`cached_images`/`providers` — confirmed by
   querying the running `/stats` endpoint directly. These are **counts of
   still-present cache files**, not true lifetime request counts (a
   cache-key hit 50 times still counts once; an expired-but-not-yet-
   overwritten file still counts, since the stats scan doesn't apply the TTL
   filter that `FileCache.get()` does). Anyone relying on the dashboard
   numbers as "how many searches has this thing done" is reading a
   different metric than the docs describe or than the number implies.

10. **The systemd unit template under `WebProxy/systemd/` still says
    `/opt/ai-atlasforge`**, while the actually-installed unit at
    `~/.config/systemd/user/atlasforge-web-proxy.service` correctly points at
    `/home/vader/AI-AtlasForge` — confirmed by diffing the two files
    directly. This is presumably rewritten by `install.sh` on setup, but the
    checked-in template is misleading if read on its own as documentation of
    "where this runs."

11. **`client.py` bypasses `mcp_server.py`'s validation layer by
    construction** (it talks straight to the HTTP service). Practically
    harmless today because `service.py` independently enforces its own SSRF/
    size/length checks, so a direct caller isn't a security hole — but any
    validation that exists *only* in `mcp_server.py` and not in `service.py`
    (e.g., the MCP layer's stricter arg-shape checks, or its
    `allowed_domains`/`blocked_domains` filtering, which is MCP-side-only and
    has no equivalent in the HTTP API at all) is silently skipped by anyone
    using `client.py` or curling the port directly.

12. **DuckDuckGo result URLs are not unwrapped.** The DDG HTML-scrape path
    takes `a.result__a`'s `href` verbatim; DuckDuckGo's HTML results are
    sometimes internal redirect/tracking URLs rather than the final
    destination. There's no `urllib.parse` unwrap of a `uddg=` param or
    similar, so a caller (or a subsequent `WebResearch` auto-fetch of that
    URL) may fetch DDG's redirector rather than the real target, depending on
    what DDG's HTML currently emits.

---

## 5. Extension points

Where new functionality would plug in cleanly, based on how the existing
seams are structured:

- **New fetch backends (e.g., a headless-browser-first strategy, or a
  dedicated PDF-vs-HTML-vs-binary router):** `fetch_page()` in `service.py`
  is already the single dispatch point that branches on Reddit-detection,
  image-detection, and JS-render-worthiness before falling through to the
  BeautifulSoup path. A new content-type-specific handler (e.g. `.docx`,
  `.csv`) would slot in as another branch here, following the same pattern as
  `_save_image()`/`fetch_reddit()` — detect via `Content-Type`, dispatch to a
  dedicated extractor, return a `FetchResponse`-shaped dict via `.to_dict()`.

- **New content processors / better main-content extraction:** the entire
  cleaning pipeline lives in one function, `extract_page_content()` (lines
  ~1262–1318). Swapping the naive `main/article/body` fallback chain for a
  real readability algorithm (e.g. porting in `trafilatura` or
  `readability-lxml`) is a self-contained change — the function's input
  (`html`, `url`, `max_chars`) and output shape (`title`, `meta_description`,
  `headings`, `text`, `links`) are already the stable contract consumed by
  both the caching layer and `mcp_server.py`'s formatter, so a smarter
  implementation could replace the internals without touching either
  neighbor.

- **JS-render heuristics:** `should_render()` and `_JS_RENDER_HOSTS` in
  `js_render.py` are the two places to extend (add hosts to the allowlist, or
  add signatures to the SPA/Cloudflare heuristic). The Playwright
  invocation itself (`render()`) is isolated behind `is_available()`, so
  swapping engines (e.g. to a remote browser-rendering service) only requires
  matching that same `render(url, timeout_s) -> {html, inner_text} | None`
  contract.

- **New search providers:** `SearchProvider` centralizes the
  Brave→DuckDuckGo→DDGS cascade behind `search()`/`search_images()`. Adding a
  fourth provider means adding one more `search_<provider>()` method and one
  more fallback branch in `_search_duckduckgo_or_ddgs_fallback()` (or a
  parallel cascade function) — the provider-tagging convention
  (`source`/`fallback_from`/`fallback_reason` keys via
  `_with_fallback_metadata()`) already exists and would just need the new
  provider name threaded through.

- **Rate limiting / retry, if added:** there is currently no seam for this at
  all — it would need to be introduced fresh, most naturally as a wrapper
  around the individual `requests` calls inside `search_brave()`,
  `search_duckduckgo()`, `fetch_page()`'s HTTP call, and `fetch_paper()`,
  or as a shared helper (e.g. `_request_with_retry()`) that all four call
  sites route through. The existing `PinnedIPAdapter`/session-creation
  pattern (`_pinned_session()`) is the natural place to also attach a
  `urllib3.util.Retry` policy, since every fetch path already constructs its
  session through that one function.

- **New MCP tools:** `mcp_server.py`'s `TOOLS` list plus a new branch in
  `handle_tool_call()` is the whole surface — the existing tools (especially
  `PaperFetch`, which is the newest/most specialized) are a template for
  argument validation (`_require_url`, `_clamp_int`), proxy dispatch
  (`_proxy_post`), and result formatting (`_format_fetch_results`).

---

## Notes on verification

- Confirmed live via direct (read-only) queries during this exploration:
  `GET /health` → `{"ok": true, "service": "atlasforge-web-proxy", ...}`;
  `GET /stats` → real counts (759 cached searches, 216 cached fetches, 8
  cached images, providers `brave: 517, ddgs: 182, duckduckgo: 60,
  duckduckgo_images: 3, brave_images: 1`), confirming Brave is actively
  configured and in use on this machine right now, and confirming the
  `/stats` field-naming mismatch against `docs/LOCAL_WEB_PROXY.md` (gap #9).
- `/home/vader/AI-AtlasForge` is a symlink to `/mnt/ForgeRealm/AI-AtlasForge`;
  the live `cache_dir` reported by `/health` resolves through that link.
- The installed systemd unit at `~/.config/systemd/user/` was diffed against
  the checked-in template at `WebProxy/systemd/atlasforge-web-proxy.service`
  — they differ only in the `/opt/ai-atlasforge` vs.
  `/home/vader/AI-AtlasForge` path substitution (gap #10).
- No code was modified, no service was restarted, and no cache/config state
  was altered. This file is the only write produced by this exploration.
