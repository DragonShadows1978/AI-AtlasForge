# WP-R2 — WebProxy: cache eviction, real lifetime stats, production WSGI serving

YOUR WRITABLE TARGET is `/mnt/ForgeRealm/wt-webproxy-r2` — edits and test
runs AUTHORIZED. This is a git worktree of AI-AtlasForge on branch
`webproxy/upgrade-2026-08`. Work ONLY inside it.

## Boundaries

- WRITABLE: `/mnt/ForgeRealm/wt-webproxy-r2/WebProxy/**`. Temp files under
  `/mnt/ForgeRealm/wt-webproxy-r2/tmp_wp_r2/` (create it).
- READ-ONLY: everything else. Your sandbox has NO network — all verification
  is offline (pytest, tmp-dir cache fixtures). The lead runs live smoke
  after merge.
- NO git operations except read-only `git diff --stat`. The lead commits.
- NO subagents.
- DO NOT touch the live proxy on port 8765, its cache directory
  (`/mnt/ForgeRealm/AI-AtlasForge/WebProxy/atlasforge_data/`), or any
  systemd unit. All cache tests use temp dirs inside your worktree.
- Two sibling seats are editing OTHER regions of `WebProxy/service.py` and
  `WebProxy/mcp_server.py` in parallel worktrees (fetch/redirect layer;
  extraction pipeline). Keep your diff surgical: no reformatting, no renames
  of existing symbols, no function reordering, no drive-by cleanups.

## Context

- `FileCache` (`WebProxy/service.py` ~line 932) is a read-through JSON file
  cache under `atlasforge_data/web_proxy_cache/` (plus raw image files under
  `images/`). Expiry is lazy on read; NOTHING ever deletes files. The live
  cache is currently 1.7 GB / 25,000+ files and grows forever.
- `/stats` re-scans the cache directory and returns counts of still-present
  files (`cached_searches`, `cached_fetches`, `providers`, ...). The docs
  (`WebProxy/docs/LOCAL_WEB_PROXY.md`) promise `searches_total` /
  `fetches_total` / `provider_breakdown` lifetime counters — which don't
  exist. The dashboard widget reads this endpoint via `WebProxy/stats.py`.
- `main()` (~line 2680) serves via Flask's Werkzeug dev server
  (`threaded=True`) — fine for loopback but it's still the dev server.
  **waitress 3.0.2 is already installed** in user site-packages.

TTL constants: search 1800s, fetch 86400s, paper 7d — keyed by filename
prefix (`search_`, `fetch_`, `paper_fetch_`, `image_search_`).

## Deliverables

### D1 — Cache eviction sweep

- New `FileCache` method(s) implementing a sweep that:
  1. deletes entries whose age exceeds their prefix's TTL by more than a
     grace factor (env `ATLASFORGE_WEB_PROXY_CACHE_STALE_GRACE`, default
     2.0 × TTL — an expired file is useless after TTL anyway; the grace just
     avoids racing a concurrent lazy read);
  2. then, if the cache dir (INCLUDING `images/`) still exceeds
     `ATLASFORGE_WEB_PROXY_CACHE_MAX_BYTES` (env, default 2000000000),
     deletes oldest-mtime-first until under the cap.
- Runs once at service startup and then on a daemon-thread interval
  (`ATLASFORGE_WEB_PROXY_CACHE_SWEEP_INTERVAL_S`, env, default 3600;
  0 disables the periodic sweep AND the startup sweep).
- Deletion must tolerate races (file vanished, permission error → log at
  debug/warning and continue; never crash the service).
- Log one summary line per sweep: files deleted, bytes freed, bytes
  remaining.

### D2 — Real lifetime counters

- Persistent counters in `atlasforge_data/web_proxy_stats.json` (atomic
  write via temp+`os.replace`, loaded at startup, corrupt file → start
  fresh with a warning, never crash).
- Count at minimum: `searches_total`, `fetches_total`, `paper_fetches_total`,
  `image_searches_total`, `research_total`, cache hits per class
  (`search_cache_hits`, `fetch_cache_hits`, ...), and lifetime
  `provider_breakdown` (increment on each live provider call).
- Increment points live in the endpoint handlers / provider cascade. Writes
  may be batched/debounced (e.g. flush every N increments or T seconds and
  at exit) — pick something simple and document it. Thread-safety: guard
  with a `threading.Lock`.
- `/stats` returns BOTH the new lifetime counters (using the documented
  names: `searches_total`, `fetches_total`, `provider_breakdown`) AND all
  existing keys (`cached_searches`, `cached_fetches`, `cached_images`,
  `cached_image_searches`, `providers`, `cache_dir`) so the dashboard widget
  keeps working unchanged. Check `WebProxy/stats.py` and the dashboard's
  consumption; adapt `stats.py` only if it would break.
- Update `WebProxy/docs/LOCAL_WEB_PROXY.md`: the `/stats` example must match
  reality (it currently doesn't — fix the docs to the new true shape), and
  add the new env knobs to the docs. Confine doc edits to the stats section
  + env-knob additions (a sibling seat may add its own doc lines; don't
  restructure the file).

### D3 — Production WSGI serving via waitress

- In `main()`: if waitress is importable and `--debug` is NOT set, serve via
  `waitress.serve(app, host=..., port=..., threads=N)` with
  `ATLASFORGE_WEB_PROXY_THREADS` (env, default 16). Otherwise fall back to
  the current `app.run(..., threaded=True)` path unchanged.
- Log which server is serving at startup.
- Guarded import — waitress absent must not crash anything.
- The `--debug`-implies-loopback-only guard stays as-is.

## Gates (registered; run all, report verbatim)

1. Baseline first: `cd /mnt/ForgeRealm/wt-webproxy-r2 && python3 -m pytest WebProxy/tests/ -q`
   must be green BEFORE your edits; if RED, STOP and report.
2. Full suite green after your edits.
3. New tests covering at minimum:
   - sweep deletes a stale-past-grace file and keeps a fresh one (tmp dir);
   - size-cap eviction removes oldest-first until under cap, images
     included in accounting;
   - sweep survives a file deleted out from under it (race tolerance);
   - counters increment on search/fetch and persist across a simulated
     restart (write, reload, values survive);
   - corrupt stats JSON → fresh start, no crash;
   - `/stats` (test client) contains BOTH old keys and new lifetime keys;
   - `main()` server selection: waitress present + no debug → waitress path;
     `--debug` → Flask path (mock `waitress.serve`, don't actually bind).

## Honesty rails

- RED is a result. Report failed gates with receipts; never adjust a
  threshold after seeing results.
- No monitor-idling: run gates synchronously to completion.
- Every claim in your report must be reproducible from the worktree.

## Done

Your final message MUST contain, verbatim:
- `git diff --stat` output.
- Full pytest summary line(s): baseline AND final.
- New-test names and pass status.
- The chosen flush/debounce policy for counter persistence, stated
  explicitly.
- A **Residuals** section: anything not covered, known weaknesses,
  follow-ups.
