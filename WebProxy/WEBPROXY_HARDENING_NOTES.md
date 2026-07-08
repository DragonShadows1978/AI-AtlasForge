# WebProxy fetch-path hardening (2026-07-08)

Scoped work order: retry/backoff + Reddit 403 ladder + generic 403 UA-retry.
No commit. Live `atlasforge-web-proxy.service` / port **8765** untouched.

## What changed

### `WebProxy/service.py`

| Area | Functions / symbols | Behavior |
|------|---------------------|----------|
| Env helpers | `_float_env`, `_bool_env` | Parse floats / on-off flags with sane fallbacks |
| Config | `BROWSER_USER_AGENT`, `REDDIT_ALT_USER_AGENT`, `RETRY_MAX_ATTEMPTS` (3), `RETRY_BASE_DELAY_S` (0.5), `RETRY_AFTER_MAX_S` (60), `RETRY_JITTER`, `UA_RETRY_ON_403`, `REDDIT_OLD_FALLBACK` | All env-overridable |
| Retry core | `_retry_after_seconds`, `_compute_backoff_s`, `_session_get_with_retry` | Bounded exp backoff + jitter; retry **only** 429/502/503/504 + connection/timeout class errors; **never** 401/403/404 |
| Reddit | `_reddit_json_url(host=)`, `_reddit_primary_user_agent`, `_reddit_fetch_candidates`, `_reddit_get_json`, `fetch_reddit` | www → old.reddit on 403; alt Firefox UA on final step; contract/shape unchanged |
| Generic fetch | `fetch_page` | Outbound GET via `_session_get_with_retry`; on 403, **one** re-GET with `BROWSER_USER_AGENT` |

Operator's prior uncommitted edit preserved: `app.run(..., threaded=True)`.

### `WebProxy/tests/test_fetch_hardening.py` (new)

Mocked HTTP only — no network, no bind.

### Not touched

- `WebProxy/mcp_server.py` (operator investigation-JSON capture left intact)
- `ENVIRONMENT.md`
- systemd unit / live proxy process
- robots.txt, readability swap (§4 #2/#3 OOS)

## Retry / fallback design

```
fetch_page(url)
  if reddit → fetch_reddit
  else:
    GET with _session_get_with_retry (≤3 attempts, base 0.5s + jitter)
      transient HTTP {429,502,503,504} or Connection/Timeout → sleep → retry
      429 + Retry-After → sleep(min(parsed, RETRY_AFTER_MAX_S))
      401/403/404 → return immediately (no transient retry)
    if status==403 and UA≠BROWSER_UA and UA_RETRY_ON_403:
      GET once more with BROWSER_USER_AGENT (+ browser Accept)
    _stream_capped_body → extract_page_content → (existing JS-render path)

fetch_reddit(url)
  candidates:
    1. www.reddit.com/.json + primary Chrome-ish UA
    2. old.reddit.com/.json + primary UA          [REDDIT_OLD_FALLBACK]
    3. old.reddit.com/.json + Firefox alt UA
  each candidate: _session_get_with_retry + stream
  on HTTP 403 only → next candidate; other statuses raise immediately
```

Output shape (`title` / `meta_description` / `headings` / `text` / `links` / …) unchanged — cache + `mcp_server` contract stable.

### Env knobs

| Variable | Default | Role |
|----------|---------|------|
| `ATLASFORGE_WEB_PROXY_RETRY_ATTEMPTS` | `3` | Max GET attempts for transient failures |
| `ATLASFORGE_WEB_PROXY_RETRY_BASE_S` | `0.5` | Exp backoff base |
| `ATLASFORGE_WEB_PROXY_RETRY_AFTER_MAX_S` | `60` | Cap on Retry-After |
| `ATLASFORGE_WEB_PROXY_RETRY_JITTER` | `1` | Add U(0, base) jitter |
| `ATLASFORGE_WEB_PROXY_UA_RETRY_ON_403` | `1` | Browser-UA once on HTML 403 |
| `ATLASFORGE_WEB_PROXY_BROWSER_USER_AGENT` | Chrome/121 Linux | UA used for 403 retry |
| `ATLASFORGE_WEB_PROXY_REDDIT_OLD_FALLBACK` | `1` | Enable old.reddit ladder |
| `ATLASFORGE_WEB_PROXY_REDDIT_ALT_USER_AGENT` | Firefox/122 | Last Reddit ladder UA |
| `ATLASFORGE_REDDIT_USER_AGENT` | (browser Chrome) | Primary Reddit UA |

## Test results

```
WebProxy/tests/test_fetch_hardening.py  — 13 passed
WebProxy/tests/ (full)                  — 317 passed
```

Mock assertions covered:

| Case | Assertion |
|------|-----------|
| (a) 429 → retry + backoff | 2 GETs; sleep == Retry-After `2.0` |
| (b) 403 not transient-retried; UA-retry once | default UA → browser UA; sleeps empty; fail path = 2 GETs not 3 |
| (c) reddit www 403 → old.reddit | both hosts seen; `resolved_url` starts with old.reddit; post parsed |
| (d) success shape | keys title/meta_description/headings/text/links/text_length present |
| bonus | 502 retry then success; connection-error ×2 then success; 302 still fails without ladder |

## Live smoke (throwaway port **8799** only)

Started: `python3 WebProxy/service.py --host 127.0.0.1 --port 8799` with isolated cache dir.
Live 8765 (PID 1458) never restarted. 8799 killed after smoke.

| URL | Proxy HTTP | Result |
|-----|------------|--------|
| `https://example.com/` (control) | 200 | `status_code=200`, title "Example Domain", text_length 129 |
| `https://x.ai/news/grok-4-5` | 502 | UA-retry **did fire** (`fetch 403 UA-retry once` in log); still target 403 after browser UA |
| Reddit ML thread (www) | 502 | Ladder **did fire** (www 403 → old 403 → old+altUA 403); all blocked |

### Named residuals (verified still failing)

1. **`https://x.ai/news/grok-4-5`** — Cloudflare 403 survives browser User-Agent swap. Needs more than UA (likely TLS fingerprint / JS challenge / IP reputation). Existing JS-render path never sees the 403 body because `_stream_capped_body` still raises on non-2xx after the UA retry fails.
2. **Reddit www + old.reddit `.json`** — machine-level block (`403 Blocked`) on all three ladder steps. Host rewrite + UA rotation insufficient; residual is IP/WAF block, not mere www vs old preference.

## Operator uncommitted edits preserved

`git diff --stat` still shows operator work on `ENVIRONMENT.md`, `WebProxy/mcp_server.py` (investigation JSON capture), `WebProxy/tests/test_mcp_server.py`, plus this hardening additive delta on `WebProxy/service.py` (includes their `threaded=True`). No reverts.

## Honest limits

- Transient retries help flaky 429/5xx and network blips; they do **not** unblock hard CDN 403s.
- UA-retry recovers sites that only filter the honest research UA; not full Cloudflare bot management.
- Reddit ladder helps when www is blocked but old is open; not when Reddit blocks the egress IP entirely.
- Status still swallowed as proxy 502 to callers (§4 gap #8 unchanged).
- No robots.txt, no readability library, no search-provider retry (search path out of scope).
