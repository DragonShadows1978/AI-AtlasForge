# WP-R3 — WebProxy: Reddit .rss fallback + Medium author-feed fallback + js_render timeout budget

YOUR WRITABLE TARGET is `/mnt/ForgeRealm/wt-webproxy-r3` — edits, test runs,
and local server smoke runs AUTHORIZED. This is a git worktree of
AI-AtlasForge on branch `webproxy/rss-fallbacks-2026-08` (based on main
8c66f29d). Work ONLY inside it.

Run the work FIRST, report after. Do not ask permission for anything
non-destructive; a registered order IS the permission. Questions are allowed
only for genuine spec contradictions.

## Boundaries

- WRITABLE: `/mnt/ForgeRealm/wt-webproxy-r3/WebProxy/**`. Temp files under
  `/mnt/ForgeRealm/wt-webproxy-r3/tmp_wp_r3/` (create it).
- READ-ONLY: everything else on this machine.
- NO git operations of any kind (no add/commit/branch/stash). The lead
  commits. (`git diff --stat` read-only is the one allowed exception, for
  the Done report.)
- NO subagents. You decompose nothing; you execute this order yourself.
- DO NOT touch the live proxy on port 8765 or the systemd unit
  `atlasforge-web-proxy`. Never start/stop/restart it. Your smoke instance
  uses port **8792** with an isolated cache dir under `tmp_wp_r3/`.
- HERMETIC-TESTS LAW: `create_app()` stays side-effect-free; the conftest
  autouse fixture redirects all data paths — never weaken it, never re-add
  side effects to `create_app()`. `test_hermetic_paths.py` must stay green.
- Keep the diff surgical: no reformatting, no renames of existing symbols,
  no drive-by cleanups outside this order's scope.

## Context (verified by the lead, 2026-08-28)

Reddit shut down unauthenticated `.json` access in late May 2026. Enforcement
is TLS-fingerprint + IP-reputation, so UA changes do not help. Observed from
this machine today:

- `www.reddit.com/<post>/.json` → **403** (any UA)
- `old.reddit.com/<post>` and `<post>/.json` → **302** to
  `/login/?reason=lor2` (logged-out wall)
- `www.reddit.com/<post>` HTML → 200 but an ~8.5KB empty JS shell (no
  server-rendered content)
- `www.reddit.com/<post>/.rss` → **200**, real Atom XML: entry[0] = the OP
  post (author, title, HTML content), entries[1..] = comments. Verified on
  `https://www.reddit.com/r/consciousness/comments/1w0n1m2/.rss` (17
  entries).

So every rung of the existing `fetch_reddit` ladder (www .json → old .json →
old + alt UA, `service.py` ~2136–2230) is now dead. The `.rss` surface is the
only working anonymous route. Reddit has said RSS is "on notice", so build
this as a fallback rung, not a replacement — if `.json` ever works again
(e.g. future OAuth wiring), it stays preferred.

Medium (Cloudflare) 403s both plain fetch and the headless-chromium ladder
from this machine. BUT the author RSS feed is served open:
`https://medium.com/feed/@<author>` → 200, full article bodies in
`<content:encoded>`. Verified today on `medium.com/feed/@shedlesky` (the
target article, 272k chars, was fully present).

js_render (`js_render.py` `render()`, ~line 175): `page.goto(url,
wait_until="networkidle", timeout=timeout_s*1000)` already catches the
timeout and falls through to capture — but the goto burns the ENTIRE budget
first (30s), so callers with their own 30s ceiling (the MCP client) give up
before capture returns. Net effect: any site that never reaches networkidle
(Medium et al.) reads as a hard "Proxy timed out after 30s" even though
content was loadable.

## Deliverables

### D1 — Reddit `.rss` fallback rung

Extend `fetch_reddit`'s ladder: when every `.json` rung has failed (non-200,
including 403 and 302 — redirects are NOT followed on the reddit path, its
existing contract), try the Atom feed:

- URL: original post URL on `www.reddit.com`, path with `/.rss` appended
  (same derivation style as `_reddit_json_url`). Query params from the
  original URL are dropped (same as the json derivation).
- The derived URL goes through the FULL existing validation + pinning
  pipeline (`_validate_url_structure`, `_resolve_first_safe_ip`,
  `PinnedIPAdapter`) exactly like every other fetch. SSRF laws from WP-R1
  apply unchanged and are REGISTERED GATES: no hop, no derived URL, ever
  connects to an address failing `_ip_is_unsafe`; no reliance on
  requests/urllib3 redirect handling; env proxies stay refused.
- Parse the Atom XML (stdlib `xml.etree.ElementTree`; namespace
  `http://www.w3.org/2005/Atom`). Entry[0] = post; subsequent entries =
  comments. Build the SAME result shape `fetch_reddit` returns today for
  json (post title/author/text + comments list) so `/fetch` consumers and
  `mcp_server.py` formatting keep working unchanged. Strip HTML from
  `<content>` to text (the repo already has extraction helpers — reuse; do
  not hand-roll a new HTML stripper if one exists).
- Result JSON gains `source_format: "rss"` (json path: `"json"`).
  Comment count from RSS is whatever the feed carries (~top 16); that is
  accepted and needs no workaround.
- Works for post URLs AND subreddit listing URLs (`/r/<sub>/`, `/r/<sub>/hot`
  etc.) — reddit serves `.rss` for those too; listing entries map to the
  existing listing result shape. If the existing code distinguishes these
  cases for json, mirror that structure. If listing support turns out
  not to exist in the current json path, scope D1 to post URLs only and say
  so in Residuals.
- Cache key: the ORIGINAL request URL (unchanged contract).

### D2 — Medium author-feed fallback

In the generic fetch path, when the final outcome for a `medium.com` URL of
shape `/@<author>/<slug>-<hex12>` is a 403 (after the existing UA-retry and
render ladder), attempt the author feed:

- Fetch `https://medium.com/feed/@<author>` through the normal validated +
  pinned pipeline.
- Find the `<item>` whose `<link>` (or guid) contains the trailing hex id of
  the requested URL. If absent (article too old for the feed window), fail
  per the existing D2/WP-R1 error contract (`target_status: 403`) plus a
  note that the feed was tried; list this limit in Residuals.
- On match: extract `<content:encoded>`, run it through the existing
  extraction pipeline (trafilatura/bs4 path) to text, return as a SUCCESS
  with `source_format: "author_feed"` and `original_status: 403`.
- Scope: `medium.com` only (plus `<custom>.medium.com` author subdomains if
  the URL-shape parse is clean; otherwise medium.com only — state which in
  the report). No generalized "try RSS on any 403" — that is future work.

### D3 — js_render timeout budget

Fix the budget burn so a never-idle page yields its DOM instead of a caller
timeout:

- `goto` waits `domcontentloaded` (fast), then a bounded settle wait
  (`ATLASFORGE_WEB_PROXY_JS_SETTLE_MS`, default 4000) before capture, instead
  of spending the whole budget hoping for networkidle. Preserve the existing
  capture/should_render logic and the min-text-length success threshold.
- Total render wall-time must stay comfortably under 25s for a page that
  never idles (leave headroom under the MCP client's 30s ceiling). Verify
  with a timed run and paste the timing.
- Keep behavior for pages that load normally unchanged (they were already
  captured post-goto).

## Gates (registered; run all, report verbatim)

1. Baseline first: `cd /mnt/ForgeRealm/wt-webproxy-r3 && python3 -m pytest
   WebProxy/tests/ -q`. You HAVE network; the DNS-dependent tests run. If
   baseline is RED, STOP and report.
2. Same command green after your edits.
3. New tests (mocked HTTP, no live network needed) covering at minimum:
   - all json rungs 403 → rss rung fetched, post + comments parsed,
     `source_format: "rss"`;
   - rss rung ALSO failing → honest error with `target_status` from the last
     json rung;
   - derived `.rss` URL goes through validation (mock a private-IP
     resolution → REJECTED);
   - medium 403 → author feed fetched, item matched by hex id, success with
     `source_format: "author_feed"`;
   - medium 403 + article absent from feed → error, `target_status: 403`;
   - js_render: never-idle page (mocked) captures after settle, well under
     the ceiling.
4. Live smoke on port 8792 (isolated cache dir under `tmp_wp_r3/`; kill the
   instance when done):
   - `https://www.reddit.com/r/consciousness/comments/1w0n1m2/consciousness_explained_how_the_brain_creates_the/`
     returns the post + comments via rss (`source_format: "rss"`);
   - `https://old.reddit.com/r/consciousness/comments/1w0n1m2/consciousness_explained_how_the_brain_creates_the/`
     (old-reddit form) ALSO succeeds via the ladder;
   - `https://medium.com/@shedlesky/consciousness-explained-how-the-brain-creates-the-mind-and-soul-fb6bebfcd566`
     returns article text via the author feed (`source_format:
     "author_feed"`), total time < 30s.
   Paste the actual curl outputs (trim bodies to a few hundred chars).

## Honesty rails

- RED is a result. A failed gate gets reported with receipts, never papered
  over or threshold-adjusted.
- No monitor-idling: run gates synchronously to completion.
- Do not fabricate receipts; every claim in your report must be reproducible
  from the worktree.
- This order authorizes standard HTTP fetches of public endpoints
  (reddit `.rss`, medium author feeds) only. NO fingerprint spoofing, NO
  third-party mirror/bypass services, NO login/session automation. If an
  endpoint blocks you, that is a RESULT — report it.

## Done

Your final message MUST contain, verbatim:
- `git diff --stat` output.
- Full pytest summary line(s) for baseline AND final.
- The new-test names and their pass status.
- The three smoke outputs from gate 4 with timings.
- A **Residuals** section: anything not covered, known weaknesses, follow-ups
  (at minimum: RSS comment-depth limit, feed-window limit for old Medium
  articles, reddit-OAuth as the durable successor).
