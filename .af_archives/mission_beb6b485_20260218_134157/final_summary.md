# AI-StoryForge Security Hardening — Final 3-Cycle Summary

**Date:** 2026-02-18
**Target:** `/home/vader/AI-AtlasForge/workspace/AI-StoryForge/`
**Framework:** FastAPI + Starlette + SlowAPI (NOT Flask)
**Test Suite:** `tests/test_security_regression.py` — Categories A through L

---

## Executive Summary

Over 3 cycles, 12 attack surfaces were hardened across AI-StoryForge's startup, runtime, and web layer. The regression test suite grew from 0 to 58 passing tests (16 per cycle approximately). The final run on 2026-02-18 shows:

```
Total: 58 passed, 0 failed, 1 skipped
[ALL SECURITY REGRESSION TESTS PASSED]
```

---

## Cycle 1 — Startup and Path Security

**Surfaces hardened:** 4
**Tests added:** A (5), B (5), C (5), D (7) = **22 tests**

### 1. sys.path.insert Removal from run.py and web/app.py (CWE-427)
**File:** `run.py`, `web/app.py`
**Issue:** Both files called `sys.path.insert(0, str(Path(__file__).parent))` at module level. If the parent directory was group/world-writable, an attacker could shadow any stdlib or third-party module (e.g., place `uvicorn.py` in the project root to hijack execution).
**Fix:** Removed `sys.path.insert()` from both files. The project root is already on `sys.path` when uvicorn imports the app from `run.py`.
**Tests:** D1 (run.py no insert), D2 (app.py no insert), D3 (project root not world-writable), D4 (no world-writable sys.path entries), D5 (_validate_sys_path_security exists), D6 (_preflight_port_bind exists), D7 (no bare import sys in app.py)

### 2. TOCTOU-Free Port Binding (_preflight_port_bind)
**File:** `run.py`
**Issue:** No explicit port validation before uvicorn startup. Port conflicts produced opaque tracebacks. Race condition possible between port check and bind.
**Fix:** `_preflight_port_bind()` does an actual `socket.bind()` before uvicorn starts. Converts `EADDRINUSE` into a clear, actionable `SystemExit` message with `lsof` instructions.
**Tests:** B0-B4 (importable, free port succeeds, occupied port triggers sys.exit, concurrent safe, ephemeral pattern)

### 3. Sys Path Security Validation (_validate_sys_path_security)
**File:** `run.py`
**Issue:** No runtime check for world-writable entries in sys.path.
**Fix:** `_validate_sys_path_security()` scans sys.path at startup, warns if any project-root entries are world/group-writable.
**Tests:** D4, D5

### 4. Config Port Single Source of Truth
**File:** `config.py`, `run.py`
**Issue:** Multiple potential sources for port derivation.
**Fix:** `STORYFORGE_PORT` env var → `Config.PORT` → `uvicorn.run(port=Config.PORT)`. No secondary reads.
**Tests:** A3, A4, A5, C1, C2, C3, C5

---

## Cycle 2 — Runtime and Data Security

**Surfaces hardened:** 4
**Tests added:** E (5), F (4), G (5), H (4) = **18 tests** (total: 40 + 2 live = 42)

### 5. Secret Redaction from Log Output (CWE-532)
**File:** `web/app.py`
**Issue:** Python's logging module emits `LogRecord.getMessage()` verbatim. SDK exceptions (e.g., Anthropic `AuthenticationError`) embed API keys in their message text. Any `logger.exception()` call could leak keys to `storyforge.log`.
**Fix:** `SecretRedactionFilter` — a `logging.Filter` subclass that replaces known secret values with `[REDACTED]` before any handler emits the record. Collects 8 secret types from Config and environment. Only redacts strings ≥8 chars to avoid false positives. Attached to ALL handlers on the `storyforge` logger.
**Tests:** E1-E5

### 6. Weak/Default Session Secret Detection (CWE-321)
**File:** `run.py`
**Issue:** `Config.SESSION_SECRET = "change-me-storyforge-dev-secret"` is the public default. Anyone can forge session cookies with this known value.
**Fix:** `_check_session_secret()` startup preflight. In DEBUG mode: emits `warnings.warn` + stderr. In production: `sys.exit()`. A strong 32+ char random secret passes silently.
**Tests:** F1-F4

### 7. DB Path Traversal Rejection (CWE-22)
**File:** `config.py`
**Issue:** `STORYFORGE_DB_PATH=../../../etc/passwd` would cause the application to use the system passwd file as its SQLite database.
**Fix:** `_validate_db_path()` resolves the path and checks it against a list of sensitive system directories (`/etc`, `/proc`, `/sys`, `/dev`, `/root`, `/boot`). Raises `RuntimeError` on traversal attempts.
**Tests:** G1-G5

### 8. Output Path Containment in Download Endpoint (CWE-22)
**File:** `web/routes.py`
**Issue:** `download_project()` took `zip_path` from the database and served it without verifying the path stayed within `OUTPUTS_DIR`. DB injection could redirect downloads to arbitrary files.
**Fix:** `zip_path.relative_to(Config.OUTPUTS_DIR.resolve())` — raises `ValueError` (→ HTTP 403) if the path escapes the output directory.
**Tests:** H1-H4

---

## Cycle 3 — Web Layer Hardening (FINAL)

**Surfaces hardened:** 4
**Tests added:** I (4), J (4), K (4), L (4) = **16 tests** (total: 58 + 1 live skip = 58 pass)

### 9. Rate Limiting on High-Risk Endpoints
**Files:** `web/routes.py`, `web/admin_routes.py`
**Issue:** Story generation (`POST /api/projects`) and file download (`GET /api/projects/{id}/download`) had no per-route rate limits. Admin POST endpoints (grant credits, suspend user, toggle admin, cancel project) also unprotected.
**Fix:**
- `POST /api/projects`: `@limiter.limit("5/minute")` — AI generation is expensive
- `GET /api/projects/{id}/download`: `@limiter.limit("20/minute")`
- `POST /admin/users/{id}/credits`: `@limiter.limit("20/minute")`
- `POST /admin/users/{id}/suspend`: `@limiter.limit("20/minute")`
- `POST /admin/users/{id}/admin`: `@limiter.limit("20/minute")`
- `POST /admin/projects/{id}/cancel`: `@limiter.limit("20/minute")`
- `POST /auth/reset-password`: `@limiter.limit(_AUTH_RATE_LIMIT)` *(added in final BUILDING pass)*
- `GET /admin/export/users`: `@limiter.limit("10/minute")` *(added in final BUILDING pass)*
- `GET /admin/export/financials`: `@limiter.limit("10/minute")` *(added in final BUILDING pass)*

**Tests:** I1-I4

### 10. Content Security Policy Header
**File:** `web/app.py`
**Issue:** `security_headers_middleware` set X-Frame-Options, X-Content-Type-Options, Referrer-Policy, and Permissions-Policy, but no Content-Security-Policy header. XSS attacks could inject scripts.
**Fix:** Added CSP to middleware:
```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
img-src 'self' data:; connect-src 'self' wss: ws:; frame-ancestors 'none'
```
- `connect-src wss: ws:` required for WebSocket connections
- `style-src 'unsafe-inline'` required for inline CSS in templates
- `frame-ancestors 'none'` supersedes X-Frame-Options (both kept for compatibility)

**Tests:** J1-J4 (live HTTP checks verify header presence and directive values)

### 11. Session Cookie Flags (Verified Correct)
**File:** `web/app.py`
**Audit result:** Correctly configured:
- `same_site="lax"` ✓
- `https_only=not Config.DEBUG` (Secure=True in production) ✓
- `HttpOnly=True` — Starlette's `SessionMiddleware` hardcodes this internally ✓
- Cookie name `"storyforge_session"` (non-default) ✓

No changes required. Tests confirm configuration.
**Tests:** K1-K4

### 12. CSRF Protection Audit (Verified Complete)
**Files:** `web/limiter.py`, `web/routes.py`, `web/admin_routes.py`
**Audit result:** Custom CSRF implementation in `web/limiter.py` (`verify_csrf()`, `_csrf_token_from_request()`). All form-POSTing endpoints call `verify_csrf()`. JSON API endpoints do not require CSRF (browser SOP prevents cross-origin JSON POSTs without CORS preflight).

No changes required. Tests confirm coverage.
**Tests:** L1-L4

### 13. Residual sys.path.insert Removal
**Files:** `web/routes.py:27`, `web/admin_routes.py:13`
**Issue:** Both files still had `sys.path.insert(0, str(Path(__file__).parent.parent))` — the same CWE-427 vector removed from `run.py` and `web/app.py` in Cycle 1. These were missed because test D2 only checked `web/app.py`.
**Fix:** Removed from both files, replaced with comment explaining the rationale.

### 14. Dependency Audit
**Tool:** `pip-audit v2.10.0`
**Result:** No known vulnerabilities found in `requirements.txt` (12 packages).
Notable packages checked: fastapi, uvicorn, starlette, jinja2, pydantic, itsdangerous, websockets, google-generativeai, google-genai, google-auth.

---

## Test Suite Summary

| Category | Tests | Surface |
|----------|-------|---------|
| A — PATH Manipulation | 5 | run.py startup, port via Config |
| B — Port Binding Race | 5 | _preflight_port_bind() TOCTOU-free |
| C — Config-Port Drift | 5 | STORYFORGE_PORT → Config.PORT → uvicorn |
| D — Writable-Path Injection | 7 | sys.path.insert removal, directory permissions |
| E — Secret Redaction | 5 | SecretRedactionFilter log protection |
| F — Default Secret Detection | 4 | _check_session_secret() weak secret check |
| G — DB Path Traversal | 5 | _validate_db_path() containment |
| H — Output Path Escape | 4 | Download endpoint OUTPUTS_DIR containment |
| I — Rate Limit Coverage | 4 | High-risk endpoint rate limiting |
| J — Content Security Policy | 4 | CSP header presence and directives |
| K — Secure Cookie Flags | 4 | SameSite, Secure, HttpOnly |
| L — CSRF Token Presence | 4 | verify_csrf() on mutating POST endpoints |
| LIVE — Runtime Verification | 2+1skip | Server health, port binding |
| **TOTAL** | **58 pass, 0 fail, 1 skip** | |

---

## Remaining Known Issues

1. **D3 Warning: Group-writable project root** — The project directory is mode `0o40775` (group-writable). This is a warning, not a test failure (test D3 only fails on world-writable). In a multi-user production environment this should be `0o40755`.

2. **sys.path.insert in test file** — `tests/test_security_regression.py:42` still has `sys.path.insert(0, str(PROJECT_ROOT))` to ensure the test can import `config`. This is intentional and safe (test code, not production web code).

3. **No HTTPS in development** — `https_only=not Config.DEBUG` means the session cookie lacks the `Secure` flag in DEBUG mode. This is acceptable for local development but requires TLS termination (nginx/load balancer) before the `Secure` flag is meaningful in production.

4. ~~**`slowapi` missing from requirements.txt**~~ — **FIXED** in final BUILDING pass: `slowapi>=0.1.9` added to `requirements.txt`. `anthropic` remains absent (out of scope).

5. **CORS wildcard in DEBUG mode** — `allow_origins=["*"]` when `Config.DEBUG=True`. Acceptable for development; production is locked to `Config.APP_BASE_URL`.

---

## Security Posture Assessment

**Before hardening (pre-Cycle 1):** Critical startup vulnerabilities — import hijack via sys.path.insert, TOCTOU port conflicts, no secret protection, no path traversal validation, no CSP, no rate limits on AI endpoints.

**After Cycle 3 (current):**

| Layer | Posture |
|-------|---------|
| Startup | **Strong** — TOCTOU-free port binding, sys.path validated, weak secrets blocked in prod |
| Secret Protection | **Strong** — All known secrets redacted from logs, strong session secret enforced |
| Path Security | **Strong** — DB path traversal rejected, output path containment enforced |
| Web Headers | **Good** — X-Frame-Options, CSP, X-Content-Type-Options, Referrer-Policy, Permissions-Policy all set |
| Session Security | **Good** — SameSite=Lax, HttpOnly, Secure(prod) all configured |
| CSRF | **Good** — Custom token validation on all form POSTs |
| Rate Limiting | **Good** — Auth, billing, story generation, download, admin actions all rate-limited |
| Dependencies | **Good** — No known CVEs (pip-audit clean) |
| Input Validation | **Moderate** — Email regex, password length, concept line length checked; story title sanitized |
| Authentication | **Moderate** — Password hashing via database layer; OAuth state validated; session user cached per request |

**Overall:** The application has progressed from a security-naive state to a well-hardened SaaS foundation. The most significant remaining gap is infrastructure-level (HTTPS/TLS configuration, which is handled at the deployment layer, not application layer).
