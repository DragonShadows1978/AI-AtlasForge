# WP-R1 — WebProxy: safe redirect following + honest status passthrough + 403 render ladder

YOUR WRITABLE TARGET is `/mnt/ForgeRealm/wt-webproxy-r1` — edits, test runs,
and local server smoke runs AUTHORIZED. This is a git worktree of
AI-AtlasForge on branch `webproxy/upgrade-2026-08`. Work ONLY inside it.

Run the work FIRST, report after. Do not ask permission for anything
non-destructive; a registered order IS the permission. Questions are allowed
only for genuine spec contradictions.

## Boundaries

- WRITABLE: `/mnt/ForgeRealm/wt-webproxy-r1/WebProxy/**` and
  `/mnt/ForgeRealm/wt-webproxy-r1/docs/**` (if you need a doc touch, prefer
  `WebProxy/docs/LOCAL_WEB_PROXY.md`). Temp files under
  `/mnt/ForgeRealm/wt-webproxy-r1/tmp_wp_r1/` (create it; it is gitignored by
  being untracked — fine).
- READ-ONLY: everything else on this machine.
- NO git operations of any kind (no add/commit/branch/stash). The lead
  commits.
- NO subagents. You decompose nothing; you execute this order yourself.
- DO NOT touch the live proxy on port 8765 or the systemd unit
  `atlasforge-web-proxy`. Never start/stop/restart it. Your smoke instance
  uses port **8791** with an isolated cache dir under `tmp_wp_r1/`.
- Two sibling seats are editing OTHER regions of `WebProxy/service.py` and
  `WebProxy/mcp_server.py` in parallel worktrees. Keep your diff surgical:
  no reformatting, no renames of existing symbols, no function reordering,
  no drive-by cleanups outside this order's scope.

## Context

`WebProxy/service.py` is a Flask fetch/search proxy with careful SSRF
enforcement: `_validate_url_structure` (pre-DNS structural check),
`_resolve_first_safe_ip` (checks EVERY resolved address), and
`PinnedIPAdapter` (pins the validated IP into the outbound TCP connection,
preserving Host + SNI, refusing env proxies). Because a redirect could escape
the pin, every call site sets `allow_redirects=False` — and any non-2xx from
the target becomes a generic proxy 502 ("fetch failed", correlation ID only).

Real-world cost, from the live journal (2026-08-10): a fetch of
`https://huggingface.co/.../resolve/main/config.json` died on its HTTP 307 to
the CDN. doi.org, shorteners, http→https and www-canonicalization hops all
fail the same way. And callers can't distinguish 404 vs 403 vs 503, so agent
retry decisions are blind.

Prior hardening (committed `e67c137b`, see `WebProxy/WEBPROXY_HARDENING_NOTES.md`):
`_session_get_with_retry` (bounded backoff, retries only 429/502/503/504 +
connection/timeout), 403 browser-UA re-GET, Reddit www→old ladder. A named
residual from that work: on 403 the JS-render path never sees the body
because `_stream_capped_body` raises on non-2xx — fix that here (D3).

## Deliverables

### D1 — Safe redirect following (SSRF laws below are REGISTERED GATES)

Implement manual redirect following for the generic fetch path
(`fetch_page`), the paper path (`fetch_paper`), and the image download path.
Leave `fetch_reddit` exactly as-is (its ladder has its own contract + tests).

Mechanics:
- Follow 301/302/303/307/308 up to `ATLASFORGE_WEB_PROXY_MAX_REDIRECTS`
  (env, default 5, min 0 = old behavior).
- Each hop: resolve `Location` (relative allowed) against the current URL,
  then run the FULL validation pipeline on the new URL —
  `_validate_url_structure` + `_resolve_first_safe_ip` — and build a FRESH
  pinned session for the new host/IP. `allow_redirects=False` stays on every
  underlying request; only your loop follows hops.
- Loop detection: track visited URLs; a repeat aborts with a clear error.
- Reject any hop whose URL carries userinfo (`user:pass@host`) or a
  non-http(s) scheme.
- Record the chain: final result JSON gains `resolved_url` (final URL) and
  `redirect_chain` (list of hop URLs, possibly empty). Cache key stays the
  ORIGINAL request URL (unchanged contract).
- 303 semantics are trivially GET-safe here (all our calls are GET).

SSRF laws (violating any of these = failed order, not "standard practice"):
1. Every hop is re-validated and re-pinned; no request may ever connect to an
   address that fails `_ip_is_unsafe`.
2. No reliance on requests/urllib3's own redirect handling anywhere.
3. Env proxies remain refused on every hop (PinnedIPAdapter already does
   this — don't route around it).

### D2 — Honest status passthrough

When the final response (after retries, UA-retry, and the D3 render ladder)
is non-2xx, the caller must be able to tell what actually happened:

- `/fetch`, `/paper/fetch`, and per-item errors in `/research` /
  `/image_search` error bodies gain: `target_status` (int),
  `target_status_class` (`"client_error"` / `"server_error"`),
  `final_url`, plus the existing `error` + `correlation_id`.
- Keep the endpoint's own HTTP response code AS IT IS TODAY (502 for
  upstream failure) so `client.py` and existing consumers don't break —
  the new fields ride in the JSON body.
- `WebProxy/mcp_server.py`: when a proxy call fails, surface the target
  status in the tool-result text the model sees, e.g.
  `Fetch failed: target returned HTTP 404 (Not Found) for <final_url>`
  vs the current opaque "fetch failed". Timeouts/connection errors (no
  status) keep a distinct message ("no HTTP response (connection/timeout)").

### D3 — 403/challenge render ladder

On a final 403 whose Content-Type is HTML: capture the body (capped at
`MAX_FETCH_BYTES` via the existing streaming helpers — do NOT let raise-on-
non-2xx discard it), run it through `js_render.should_render()`; if it looks
like a JS challenge/SPA and Playwright is available, attempt `render()`. If
the render produces real content (rendered text length ≥ 200), return it as a
success with `js_rendered: true` and `original_status: 403` in the JSON.
Otherwise fail per D2 with `target_status: 403`.

## Gates (registered; run all, report verbatim)

1. Full suite green: `cd /mnt/ForgeRealm/wt-webproxy-r1 && python3 -m pytest WebProxy/tests/ -q`
   (baseline is green before your edits — verify that first; if baseline is
   RED, STOP and report).
2. New tests in `WebProxy/tests/` (mocked HTTP, no live network needed)
   covering at minimum:
   - 307 followed, content returned, `redirect_chain` recorded;
   - relative `Location` resolved;
   - mid-chain redirect to a private IP (mock DNS) REJECTED;
   - redirect loop aborts at the cap;
   - 404 error body carries `target_status: 404`;
   - 403 HTML + challenge markers routes into the render attempt (render
     mocked).
3. Live smoke on port 8791 (isolated cache dir, then kill the instance):
   - `https://huggingface.co/datasets/hf-internal-testing/fixtures_ats/resolve/main/.gitattributes`
     (or any hf `resolve/` URL — they 307 to the CDN) fetches OK through the
     redirect;
   - `http://example.com/` follows to https and returns the page;
   - a known-404 URL (e.g. `https://example.com/definitely-not-here-404`)
     returns the D2 error body with `target_status: 404`.
   Paste the actual curl outputs (trim bodies).

## Honesty rails

- RED is a result. A failed gate gets reported with receipts, never papered
  over or threshold-adjusted.
- No monitor-idling: run gates synchronously to completion.
- Do not fabricate receipts; every claim in your report must be
  reproducible from the worktree.

## Done

Your final message MUST contain, verbatim:
- `git diff --stat` output (run `git diff --stat` is READ-ONLY and allowed;
  it is the only git command you may run).
- Full pytest summary line(s) for gate 1 (before-edit baseline AND final).
- The new-test names and their pass status.
- The three smoke outputs from gate 3.
- A **Residuals** section: anything not covered, known weaknesses, follow-ups.
