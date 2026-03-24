# Red Team Findings — brt_20260313_222121_agent1

---BUG---
File: /home/vader/AI-AtlasForge/dashboard_modules/core.py
Line: 581
Severity: HIGH
Type: missing_validation
Description: In the fallback branch (when MissionConfig is unavailable), `int(data.get('cycle_budget', 3))` is called without a try/except, so a non-numeric cycle_budget value from user-controlled JSON input raises an unhandled ValueError that propagates as a 500 error instead of a 400 validation error.
Reproduction: POST /api/mission with JSON body {"problem_statement": "test", "cycle_budget": "not-a-number"} when af_engine.mission_config is not importable; causes an unhandled ValueError stack trace.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/dashboard_modules/core.py
Line: 721-725
Severity: MODERATE
Type: missing_validation
Description: The `source_type` query parameter from `request.args.get('source_type')` is passed directly to `storage.get_filtered(source_type=source_type_filter)` with no allowlist validation at the route level; a caller can pass any arbitrary string as a filter value without hitting a validation error before the storage layer.
Reproduction: GET /api/recommendations?source_type=<arbitrary_value> - no route-level enum check rejects unknown source_type values before forwarding to storage.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/adversarial_testing/blind_agent_runner.py
Line: 64-67
Severity: MODERATE
Type: missing_validation
Description: `_ALLOWED_WORKSPACE_ROOTS` is hardcoded to absolute paths for a specific local user (`/home/vader/AI-AtlasForge/workspace` and `/home/vader/AI-AtlasForge`), making the workspace path validation always fail when deployed under any other username or root directory, rendering the runner non-functional without a code change.
Reproduction: Instantiate BlindAgentRedTeam and call launch_parallel_team() with a valid workspace path outside /home/vader/ — raises ValueError even for a legitimately safe path.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/adversarial_testing/blind_agent_runner.py
Line: 474-481
Severity: MODERATE
Type: race_condition
Description: The symlink check (`is_symlink()`) and file creation (`open(..., 'w')`) are two separate syscalls with no atomic guarantee between them; a TOCTOU race window exists where an attacker can replace a regular file with a symlink between the check and the open, defeating the symlink protection.
Reproduction: Between the is_symlink() call at line 474 and the open() at line 478, replace results_file with a symlink to an arbitrary file; the open() follows the symlink and truncates the target.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/af_engine/core/archival.py
Line: 558
Severity: LIGHT
Type: exception_handling
Description: Inside the per-transcript copy loop, the exception handler catches bare `Exception` rather than specific I/O types (e.g., `shutil.Error`, `OSError`); this swallows unexpected errors and makes it harder to distinguish I/O failures from programming errors.
Reproduction: Introduce a TypeError inside the copy loop; it will be silently caught, logged as a copy failure, and treated identically to an IOError.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/adversarial_testing/blind_agent_runner.py
Line: 676
Severity: LIGHT
Type: exception_handling
Description: The `_drain_stderr_bg` inner function uses a bare `except Exception: pass` that silently swallows all errors from the stderr drain background thread, including OSError and IOError; stderr data loss on pipe errors goes completely undetected and unlogged.
Reproduction: Close the subprocess stderr pipe unexpectedly; the resulting OSError is silently discarded with no log entry, making the failure invisible.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/adversarial_testing/blind_agent_runner.py
Line: 722-724
Severity: LIGHT
Type: exception_handling
Description: The fallback `proc.stdout.read(1024 * 1024)` call on the non-streaming path uses `except Exception: pass` — if stdout read fails, `stdout_text` remains an empty string and the failure is completely silent with no log message, causing the agent to silently return no results.
Reproduction: Close proc.stdout prematurely; the read raises an OSError which is swallowed and stdout_text stays empty with no warning.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/adversarial_testing/mission_drift_validator.py
Line: 568-570
Severity: MODERATE
Type: logic_error
Description: The evaluator error detection checks only three hardcoded string patterns ("Evaluation error:", "Parse error:", "Failed to parse JSON response"); any other error string format returned by the evaluator (e.g., "Error:", "LLM error:", "Timeout:") will cause `is_evaluation_error` to be False, and the result will be counted as a real drift failure, potentially triggering a false HALT.
Reproduction: Return an evaluator_reasoning string of "Error: network timeout" with drift_detected=True and severity=HIGH — it bypasses is_evaluation_error and increments failure_count as a real drift event.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/dashboard_modules/core.py
Line: 279-283
Severity: LIGHT
Type: logic_error
Description: The `/api/start/<mode>` route in `core.py` line 281 has its own inline allowlist `{"rd", "free"}` that is independent of and duplicates `_ALLOWED_MODES` in `dashboard_v2.py` line 424; the two can silently drift out of sync if one is updated without the other, causing inconsistent mode validation between the blueprint route and the start_claude() function.
Reproduction: Add a new allowed mode to `_ALLOWED_MODES` in dashboard_v2.py but not to the inline check in core.py — calls to /api/start/<new_mode> get a 400 from core.py even though dashboard_v2.py would accept it.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/suggestion_storage.py
Line: 127-132
Severity: LIGHT
Type: exception_handling
Description: When rollback fails inside `_get_connection`, the rollback error is logged via `logger.warning` but is not chained onto the re-raised original exception (no `raise X from Y`), making simultaneous DB-error + rollback-error scenarios impossible to diagnose from the exception chain alone.
Reproduction: Cause both a DB operation error and a rollback failure; the warning is logged but the rollback_err is lost from the exception traceback chain.
---END BUG---

---SUSPECTED---
File: /home/vader/AI-AtlasForge/dashboard_modules/core.py
Line: 169, 178, 193, 206
Description: Multiple `except Exception: pass` (no logging, no re-raise) in `api_health()` silently mark service checks as False for any exception type, including AttributeError or NameError caused by uninitialized module-level globals (STATE_DIR, MISSION_PATH being None); a misconfigured blueprint init would silently report unhealthy services with no diagnostic information.
---END SUSPECTED---

---SUSPECTED---
File: /home/vader/AI-AtlasForge/dashboard_modules/core.py
Line: 391-396
Description: `story_workspace` path traversal guard uses `str(story_workspace) + "/"` prefix check against `str(project_base.resolve()) + "/"`. If `project_base` (`/media/vader/TIE-FIGHTER/...`) does not exist on the filesystem, `Path.resolve()` may not fully canonicalize symlinks in the path, potentially weakening the traversal guard.
---END SUSPECTED---

---SUSPECTED---
File: /home/vader/AI-AtlasForge/adversarial_testing/blind_agent_runner.py
Line: 670-677
Description: `_stderr_chunks` is a plain list appended to from `_drain_stderr_bg` background thread and read from the main thread. While a thread join before reading provides a happens-before guarantee in CPython, if any code path reads _stderr_chunks before the join completes (e.g., on an exception branch), there is a potential data race.
---END SUSPECTED---
