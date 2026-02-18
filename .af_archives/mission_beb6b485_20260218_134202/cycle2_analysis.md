# Cycle 2 Analysis: StoryForge Secondary Surface Hardening

**Date:** 2026-02-18  
**Cycle:** 2 of 3  
**Target:** `/home/vader/AI-AtlasForge/workspace/AI-StoryForge/`  
**Test result:** 42 passed, 0 failed, 1 skipped (LIVE3 by design)

---

## Summary

Cycle 2 extended the security hardening from Cycle 1 (primary attack surfaces) to four
secondary surfaces: secret leakage in logs, weak session secrets, database path traversal,
and output directory injection.

---

## Changes Made

### 1. Secret Redaction Filter (`web/app.py`)

**Class:** `SecretRedactionFilter(logging.Filter)`

**How it works:**
- Collects live secret values from `Config` and environment at instantiation
- Only includes secrets with `len >= 8` to prevent false-positive redaction of short values
- On each log record: calls `record.getMessage()` to get the rendered message, replaces all
  secret substrings with `[REDACTED]`, then sets `record.msg = cleaned; record.args = ()`
  so no downstream handler can re-expand the original secret from format args

**Attachment:**
- Filter instance `_secret_filter` added to both `_handler` (file) and `_stream` (debug stderr)
- All child loggers of `storyforge` inherit this protection automatically

**Secrets protected:** `SESSION_SECRET`, `SMTP_PASSWORD`, `STRIPE_SECRET_KEY`,
`STRIPE_WEBHOOK_SECRET`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GITHUB_OAUTH_CLIENT_SECRET`,
`ANTHROPIC_API_KEY` (env), `GOOGLE_API_KEY` (env)

**Verified:** E1-E5 all pass. `logs/storyforge.log` contains no plaintext secret values.

---

### 2. Session Secret Strength Check (`run.py`)

**Function:** `_check_session_secret()`

**How it works:**
- Defines `_DEFAULT_SESSION_SECRETS` frozenset of known-weak values
- Checks if `Config.SESSION_SECRET` is in the set OR has `len < 32`
- **DEBUG mode:** emits `warnings.warn()` and prints to stderr (allows dev startup)
- **Production mode (`DEBUG=False`):** calls `sys.exit()` with actionable message including
  the `python3 -c "import secrets; print(secrets.token_hex(32))"` fix command

**Called from:** `main()` after `_validate_sys_path_security()`, before database init

**Mirrors:** Django's `SECRET_KEY` validation pattern

**Verified:** F1-F4 all pass. Default secret triggers warning; strong secret passes silently.

---

### 3. Database Path Traversal Validation (`config.py`)

**Function:** `_validate_db_path(raw_path_str, home_path) -> Path`

**How it works:**
1. Splits raw path string on `/` and checks for `..` components — raises `RuntimeError`
   with diagnostic if found
2. Calls `Path(raw).resolve()` to eliminate symlinks and canonicalize
3. Checks resolved path against `_SYSTEM_DIR_PREFIXES` (`/etc/`, `/proc/`, `/sys/`,
   `/dev/`, `/root/`, `/boot/`) — raises `RuntimeError` if matched
4. Returns the resolved `Path` object

**Applied at:** `Config.DB_PATH = _validate_db_path(_raw_db, STORYFORGE_HOME)` —
runs at module import time, fails fast before any database operation

**Rejected examples:**
- `STORYFORGE_DB_PATH=../../../etc/passwd` → `RuntimeError` (traversal component)
- `STORYFORGE_DB_PATH=../../../tmp/evil.db` → `RuntimeError` (traversal component)

**Verified:** G1-G5 all pass.

---

### 4. Output Path Containment Check (`web/routes.py`)

**Location:** `download_project()` endpoint (line ~1350)

**How it works:**
- After retrieving `zip_path` from database: `zip_path = Path(project["zip_path"]).resolve()`
- Calls `zip_path.relative_to(Config.OUTPUTS_DIR.resolve())` — raises `ValueError` if
  `zip_path` does not start with `OUTPUTS_DIR`
- On `ValueError`: returns HTTP 403 Forbidden

**Defense-in-depth:** Even if a database record is corrupted or injected, the server
cannot serve arbitrary files outside `OUTPUTS_DIR`.

**Verified:** H4 passes (AST check confirms `relative_to` + `OUTPUTS_DIR` presence).

---

## Test Suite Expansion

Added 18 new tests across 4 categories to `tests/test_security_regression.py`:

| Category | Tests | Description |
|----------|-------|-------------|
| E | 5 | SecretRedactionFilter existence, redaction behavior, false-positive prevention, handler attachment, in-stream verification |
| F | 4 | Default secret detection, `_check_session_secret()` existence, weak-secret warning, strong-secret pass |
| G | 5 | `_validate_db_path()` existence, traversal rejection (x2), valid path acceptance, `Config.DB_PATH` integrity |
| H | 4 | Filename sanitization (traversal titles x2), normal title preservation, download endpoint containment |

**Total test count: 42 passed (was 24 in Cycle 1), 0 failed, 1 skipped (LIVE3 by design)**

---

## Success Criteria Verification

| Criterion | Status |
|-----------|--------|
| All categories A-H pass (0 failures) | ✓ 42/42 passed |
| No API keys/secrets in log files | ✓ grep found 0 matches |
| Default secret triggers warning in DEBUG | ✓ F3 passes |
| Default secret causes exit in production | ✓ Implemented, F4 verifies strong passes |
| `STORYFORGE_DB_PATH=../../../tmp/evil.db` rejected | ✓ G3 passes |
| `../../../etc/passwd` traversal rejected | ✓ G2 passes |
| Output filename sanitization prevents path separators | ✓ H1, H2 pass |
| `zip_path` validated against `OUTPUTS_DIR` | ✓ H4 passes |
| Live server on port 5100 responds throughout | ✓ LIVE1/LIVE2 pass |

---

## Residual Observations

1. **Project root is group-writable** (`mode=0o40775`): this was pre-existing (Cycle 1
   noted it). Not introduced by Cycle 2 changes. D3 WARN is advisory, not a failure.

2. **`_sanitize_filename` preserves dots**: `../../etc/passwd` → `....etcpasswd`.
   Dots are valid filename characters and cause no path traversal — only `/` separators
   enable directory escape, and those are correctly stripped. H1/H2 tests verify this.

3. **`SecretRedactionFilter._refresh_secrets()` is called once at startup**: if secrets
   rotate at runtime (unlikely), the filter would use stale values. For this application
   pattern, one-time collection at startup is correct.

---

## Files Modified

| File | Change |
|------|--------|
| `web/app.py` | Added `SecretRedactionFilter` class + `import os`; attached to both log handlers |
| `run.py` | Added `_check_session_secret()`, `_DEFAULT_SESSION_SECRETS`, `_MIN_SECRET_LEN`; `import warnings`; call in `main()` |
| `config.py` | Added `_validate_db_path()`, `_SYSTEM_DIR_PREFIXES`; applied to `DB_PATH` |
| `web/routes.py` | Added `OUTPUTS_DIR` containment check in `download_project()` |
| `tests/test_security_regression.py` | Added `import logging`; added `run_category_e/f/g/h()`; updated `main()` |
