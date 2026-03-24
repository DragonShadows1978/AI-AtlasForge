# Red Team Findings — brt_20260313_222121_agent2

---BUG---
File: /home/vader/AI-AtlasForge/dashboard_static/src/modals.js
Line: 268-273
Severity: HIGH
Type: logic_error
Description: The click event listener on the recommendations container is registered with `{ once: true }`, which causes it to self-remove after the first click, making all subsequent recommendation item clicks silently ignored.
Reproduction: 1. Load the recommendations panel with multiple items. 2. Click any recommendation item — modal opens. 3. Close the modal. 4. Click any other recommendation item — nothing happens because the event listener was removed after the first click.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/adversarial_testing/mission_drift_validator.py
Line: 487
Severity: HIGH
Type: null_check
Description: `parsed.get("reasoning", "")` returns `None` (not `""`) when the LLM JSON contains `"reasoning": null`. That None is stored in `result.evaluator_reasoning`, then `.startswith()` is called on it at line 568, raising `AttributeError: 'NoneType' object has no attribute 'startswith'` and crashing the drift validation loop.
Reproduction: Return a JSON response from the evaluator LLM with `{"reasoning": null, "drift_detected": false, ...}`. The `_update_tracking_state` call raises `AttributeError` at line 568.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/adversarial_testing/mission_drift_validator.py
Line: 484-486
Severity: HIGH
Type: type_confusion
Description: `parsed.get("added_scope", [])` and `parsed.get("lost_focus", [])` accept whatever type the LLM returns without validation. If the LLM returns a string instead of a list (e.g., `"added_scope": "feature creep"`), `result.added_scope[:5]` returns a 5-character substring, and the warning message at line 650 iterates over individual characters instead of list items, corrupting output.
Reproduction: Return a JSON response where `"added_scope"` is a plain string. The warning message will iterate characters: `- f`, `- e`, `- a`, etc.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/adversarial_testing/blind_agent_runner.py
Line: 64-67
Severity: MODERATE
Type: security
Description: `_ALLOWED_WORKSPACE_ROOTS` includes `/home/vader/AI-AtlasForge` (the entire repo root), not just the `workspace/` subdirectory. Any caller can pass `workspace_dir=Path("/home/vader/AI-AtlasForge/state")` or similar and the path validation passes, giving adversarial agents write access to the SQLite mission database and state files.
Reproduction: Call `BlindAgentRedTeam().launch_parallel_team(workspace_dir=Path("/home/vader/AI-AtlasForge/state"), ...)`. The workspace guard accepts it as a subdirectory of the allowed root.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/adversarial_testing/mission_drift_validator.py
Line: 448
Severity: MODERATE
Type: null_check
Description: `response.startswith("ERROR:")` is called on line 448 after only checking `response is None`. If `invoke_fresh_llm` returns a non-string, non-None value on an unusual code path, `startswith` raises `AttributeError`. The `is None` guard does not protect against other non-string types.
Reproduction: Patch `invoke_fresh_llm` to return `[]` instead of a string. The check `response.startswith("ERROR:")` raises `AttributeError`.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/adversarial_testing/mission_drift_validator.py
Line: 556-558
Severity: MODERATE
Type: logic_error
Description: The running average for `average_similarity` is initialized to `1.0` (line 125 of the dataclass). On the first validation call (`n=1`), the formula `(1.0 * 0 + similarity) / 1` correctly discards the initial value. However for velocity tracking, `similarity_history` starts empty and only gets the actual similarity appended later via `update_velocity()`. The `average_similarity=1.0` initial value is a ghost reading that inflates the mean for early cycles, causing the drift acceleration threshold to be artificially permissive at mission start.
Reproduction: Observe that after 2 validations with `semantic_similarity=0.5`, `average_similarity` becomes 0.5, but the velocity is computed between the real data points only. The ghost 1.0 starting value is correctly excluded from the incremental formula so the average is fine, but the initial `average_similarity=1.0` is reported in serialized state before the first validation call.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/atlasforge_conductor.py
Line: 785-787
Severity: LIGHT
Type: incorrect_behavior
Description: When stream reconstruction fails, the function sets `response = ""` and logs the error, but continues to return as if `proc.returncode == 0` (success). Callers receive `("", None)` — identical to a successful empty response — so higher-level retry or fallback logic cannot distinguish a reconstruction failure from a legitimately empty LLM output.
Reproduction: Corrupt a JSONL stream file so `reconstruct_text_from_stream_file` raises. The conductor returns `("", None)` with no indication of failure beyond the log line.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/af_engine/core/archival.py
Line: 443
Severity: LIGHT
Type: exception_handling
Description: The except clause catches `ValueError` alongside `OSError` and `UnicodeDecodeError`. A `ValueError` raised inside the try block by a path other than transcript JSON parsing (e.g., a malformed datetime string in a called function) would be silently swallowed and logged only as a transcript parse error, masking the true failure origin.
Reproduction: Trigger a `ValueError` from within the parsing loop body via a non-JSON-related operation (e.g., passing an integer to a function that expects a string).
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/context_watcher/context_watcher.py
Line: 93-101
Severity: NONBLOCKING
Type: logic_error
Description: `CONTEXT_WATCHER_ENABLED` and `TIME_BASED_HANDOFF_ENABLED` are module-level constants defined at lines 93-101, BEFORE the `_safe_int_env` helper is defined at line 102. These two constants use string comparisons (not int conversion) so they are not affected today. However any future developer adding a module-level `int()` conversion above line 102 will cause an import-time crash because the helper does not exist yet at that point.
Reproduction: Add `SOME_INT = int(os.environ.get("X", "5"))` between lines 93 and 101. Import fails.
---END BUG---

---SUSPECTED---
File: /home/vader/AI-AtlasForge/adversarial_testing/blind_agent_runner.py
Line: 473-481
Description: The symlink guard checks `results_file.is_symlink()` then immediately calls `open(results_file, 'w')`. There is a TOCTOU race window between the check and the open: an attacker with filesystem access could create a symlink at `results_file` in between the two calls. The protection is best-effort, not atomic.
---END SUSPECTED---

---SUSPECTED---
File: /home/vader/AI-AtlasForge/adversarial_testing/mission_drift_validator.py
Line: 568-570
Description: The `is_evaluation_error` detection only covers three exact string patterns. An LLM error response starting with "Error:" (no "Evaluation" prefix), "error:" (lowercase), or "evaluation error" (without colon) would not be detected as an error and could fall through to increment `failure_count`, potentially triggering a spurious HALT.
---END SUSPECTED---

---SUSPECTED---
File: /home/vader/AI-AtlasForge/dashboard_static/src/modals.js
Line: 204-208
Description: The `itemClass` CSS class string is constructed from `rec.source_type` and `rec.mission_type` server values without calling `escapeHtml()`. If either field contains a quote or space character, the resulting `class="..."` attribute in the injected innerHTML could be malformed or inject extra CSS classes. The values appear to be server-side enum strings but are not validated client-side.
---END SUSPECTED---

---SUSPECTED---
File: /home/vader/AI-AtlasForge/adversarial_testing/blind_agent_runner.py
Line: 519
Description: `executor.shutdown(wait=True)` is called in the normal completion path, then the `finally` block calls `executor.shutdown(wait=False, cancel_futures=True)` a second time. Python 3.9+ documents this as safe (no-op), but the minimum Python version for this codebase is not confirmed. On Python 3.8 or earlier, double-shutdown may raise `RuntimeError`.
---END SUSPECTED---

---SUSPECTED---
File: /home/vader/AI-AtlasForge/af_engine/integrations/post_mission_hooks.py
Line: 202
Description: `"\n".join(str(d) for d in deliverables if d is not None)` — if any deliverable string itself contains a newline character, a consumer that splits AF_DELIVERABLES on newline would see more entries than were originally in the list. The data integrity of the env var depends on deliverable strings being newline-free, which is not validated.
---END SUSPECTED---
