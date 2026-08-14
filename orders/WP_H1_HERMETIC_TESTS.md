# WP-H1 — WebProxy: hermetic tests (stop test runs from touching live cache/stats) + doc knobs

YOUR WRITABLE TARGET is `/mnt/ForgeRealm/wt-webproxy-r2` — edits and test
runs AUTHORIZED. This is a git worktree of AI-AtlasForge, now detached at
current `main`. Work ONLY inside it.

## Boundaries

- WRITABLE: `/mnt/ForgeRealm/wt-webproxy-r2/WebProxy/**`.
- READ-ONLY: everything else. Sandbox has NO network — offline verification
  only.
- NO git operations except read-only `git diff --stat`. The lead commits.
- NO subagents. DO NOT touch the live proxy on 8765, its data dirs, or
  systemd.

## Context — the defect (observed live today, receipts held by lead)

`create_app()` (`WebProxy/service.py` ~line 2958) constructs
`FileCache(CACHE_DIR)` and immediately calls `cache.start_sweeper()`, which
runs a deletion sweep. `CACHE_DIR`, `STATS_PATH`, `PAPER_ARTIFACT_DIR`, and
`IMAGE_CACHE_DIR` are module-level constants resolved from
`Path(__file__)`. Consequence: any pytest run inside a deployed checkout
sweeps and writes the REAL service data dirs — today a full-suite run in the
live checkout deleted ~25k expired live cache files (before the operator
intended) and wrote test fixture entries + test counter increments into the
live cache dir and `web_proxy_stats.json`. Tests must be hermetic no matter
where the suite runs.

## Deliverables

### D1 — sweeper leaves create_app()

`create_app(start_sweeper: bool = False)`: the sweeper thread + initial
sweep run ONLY when the flag is true. `main()` passes `start_sweeper=True`.
No test may trigger a sweep implicitly. Existing R2 sweep tests that call
`sweep()` directly are unaffected.

### D2 — hermetic test fixture

New `WebProxy/tests/conftest.py` with an AUTOUSE fixture that, for every
test, monkeypatches ALL live-path module constants in `WebProxy.service`
(and any module that captured them at import, e.g. `IMAGE_CACHE_DIR`) to
per-test tmp paths: `CACHE_DIR`, `STATS_PATH`, `PAPER_ARTIFACT_DIR`,
`IMAGE_CACHE_DIR`. Existing tests that already patch these keep working
(their patches simply win). Also patch the equivalent in
`WebProxy/mcp_server.py` if it captures any of these paths at import —
check; if it doesn't, say so in the report.

### D3 — canary test

A test that asserts, at test time, that `service.CACHE_DIR` /
`service.STATS_PATH` / `service.IMAGE_CACHE_DIR` / `service.PAPER_ARTIFACT_DIR`
do NOT point inside `Path(service.__file__).parent` — i.e. the autouse
fixture is live. If someone removes the conftest, this test fails.

### D4 — doc knobs (small, additive)

Add to the env-knob documentation in `WebProxy/docs/LOCAL_WEB_PROXY.md`
(same table/section style as existing entries, no restructuring):
`ATLASFORGE_WEB_PROXY_MAX_REDIRECTS` (default 5),
`ATLASFORGE_WEB_PROXY_JS_RENDER_MIN_TEXT_LEN` (default 200),
`ATLASFORGE_WEB_PROXY_EXTRACTOR` (auto|trafilatura|bs4, default auto).
One line each; plus one sentence in the fetch section: redirects are
followed with per-hop SSRF re-validation, results carry
`resolved_url`/`redirect_chain`, errors carry `target_status`.

## Gates (registered; run all, report verbatim)

1. Baseline first (offline deselects):

   ```
   python3 -m pytest WebProxy/tests/ -q \
     --deselect WebProxy/tests/test_mcp_server.py::TestWebFetchProxyContract::test_webfetch_requests_content_from_proxy \
     --deselect WebProxy/tests/test_mcp_server.py::TestPaperFetchProxyContract::test_paperfetch_uses_paper_endpoint \
     --deselect WebProxy/tests/test_mcp_server.py::TestRequireUrlSchemeWhitelist::test_accepts_http \
     --deselect WebProxy/tests/test_mcp_server.py::TestRequireUrlSchemeWhitelist::test_accepts_https \
     --deselect WebProxy/tests/test_mcp_server.py::TestIter3SsrfProtection::test_public_url_still_accepted
   ```

   Expected `360 passed, 5 deselected`. Deviation → STOP and report.
2. Full suite green after edits (same deselects), including the new canary.
3. Hermeticity proof: before the suite, record
   `find WebProxy/atlasforge_data -type f | sort` (in YOUR worktree); run the
   full suite; re-record; the two listings MUST be identical (no file
   created, deleted, or touched). Include both listings (or their diff,
   empty) in the report.

## Honesty rails

RED is a result; receipts verbatim; no monitor-idling; no fabricated claims.

## Done

Final message MUST contain: `git diff --stat`, baseline + final pytest
summaries, new test names + status, the gate-3 hermeticity diff (empty), the
D2 answer about mcp_server.py import-time paths, and a **Residuals** section.
