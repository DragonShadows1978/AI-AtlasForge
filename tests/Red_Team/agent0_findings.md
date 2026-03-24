# Red Team Findings — brt_20260313_222121_agent0

---BUG---
File: /home/vader/AI-AtlasForge/atlasforge_conductor.py
Line: 808
Severity: CRITICAL
Type: crash
Description: When provider is "claude" and _stream_file is falsy (None/empty), no branch in the if/elif/else block at lines 780-807 assigns the `response` variable, causing a NameError crash on `response[:200]` at line 808.
Reproduction: Set LLM provider to "claude". Trigger invoke_llm() in a code path where agent stream registration fails (exception at lines 682-684 sets _stream_file=None). Process returns code 0 but NameError fires at line 808 before the successful return.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/adversarial_testing/mission_drift_validator.py
Line: 568-570
Severity: HIGH
Type: null_check
Description: result.evaluator_reasoning is typed as `str` but from_dict() (line 100) does not guard it against non-string values from deserialized JSON; if evaluator_reasoning is None or a non-string, calling .startswith() at line 568 raises AttributeError, bypassing the error-suppression logic entirely.
Reproduction: Load a MissionDriftResult from_dict() where the JSON has {"evaluator_reasoning": null}. Call _update_tracking_state() — the .startswith() call at line 568 raises AttributeError rather than treating the result as an evaluation error.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/context_watcher/context_watcher.py
Line: 102-107
Severity: MODERATE
Type: missing_validation
Description: _safe_int_env() accepts and returns negative integers without clamping; values like TIME_BASED_HANDOFF_MINUTES=-1 or STAGE_TIMEOUT_MINUTES=-999 are silently stored and then used as timeouts/thresholds, which can cause immediate spurious handoffs or disable time-based protection entirely.
Reproduction: Set env var TIME_BASED_HANDOFF_MINUTES=-1 before import. TIME_BASED_HANDOFF_MINUTES will equal -1. Any comparison `elapsed > TIME_BASED_HANDOFF_MINUTES` will be True immediately, triggering constant spurious handoffs.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/context_watcher/context_watcher.py
Line: 166-167
Severity: MODERATE
Type: incorrect_behavior
Description: CODEX_GRACEFUL_HEADROOM_TOKENS and CODEX_EMERGENCY_HEADROOM_TOKENS accept negative values from _safe_int_env(); the guard `if CODEX_GRACEFUL_HEADROOM_TOKENS > 0` correctly skips them, but a negative value can also be produced by a typo like "-0" which parses as 0 and silently disables the feature.
Reproduction: Set CODEX_GRACEFUL_HEADROOM_TOKENS="-5". _safe_int_env returns -5; the > 0 guard skips it, so no headroom is applied — not semantically wrong but silently ignores the operator's intent with no warning logged.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/af_engine/core/archival.py
Line: 443
Severity: LIGHT
Type: exception_handling
Description: The except clause at line 443 catches (OSError, UnicodeDecodeError, ValueError) but not PermissionError (which is a subclass of OSError on Python 3 but was not always so) nor MemoryError for extremely large transcript files; more critically, execution continues and returns a partial usage dict after the error — callers may not realize the returned data is incomplete.
Reproduction: Open a transcript file that is larger than available RAM or has restricted read permissions on some platforms; the error is logged but a zeroed/partial usage dict is silently returned to the caller.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/af_engine/core/archival.py
Line: 521-524
Severity: MODERATE
Type: security
Description: When mission_id is empty/None, a fallback mission_id is constructed from created_at using only string replace — if created_at is None or a non-string type, calling .replace() on line 523 raises AttributeError; if created_at is an attacker-supplied string containing OS path separators that survive the [:19] truncation, the path traversal check at line 541 may still be the only defense, which is correct but the fallback construction is fragile.
Reproduction: Pass a mission dict with mission_id=None and created_at=None. Line 523 calls None.replace() -> AttributeError inside the outer try block, causing archive_mission_transcripts to return {"success": False, "errors": [], ...} with no informative message.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/dashboard_static/src/modals.js
Line: 85
Severity: HIGH
Type: security
Description: currentHealthFilter is loaded verbatim from localStorage (user-controlled) and interpolated directly into a CSS selector string passed to document.querySelector() without sanitization; a crafted localStorage value like `] .injected` causes a SyntaxError from querySelector, and a value like `[attr=val]` could select unintended elements or be used as a CSS selector injection primitive.
Reproduction: In browser console: localStorage.setItem('rec_sort_filter_state', JSON.stringify({version:2, healthFilter:'[data-secret]', sortField:'priority_score', sortDirection:'desc', tagFilter:''})); reload page — loadRecSortState() calls document.querySelector('.rec-health-stat.[data-secret]') which is syntactically invalid and throws an uncaught DOMException from querySelector.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/dashboard_static/src/modals.js
Line: 1132
Severity: HIGH
Type: security
Description: Same CSS selector injection as line 85 — filterByHealth(status) also interpolates `status` (user-controlled via button click, but the same currentHealthFilter loaded from localStorage) into document.querySelector() without sanitization.
Reproduction: Same localStorage manipulation as above; filterByHealth() calls document.querySelector(`.rec-health-stat.${currentHealthFilter.replace('_', '-')}`) — the replace only converts underscores and does not strip special CSS selector characters.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/research_agent/web_researcher.py
Line: 498-505
Severity: MODERATE
Type: null_check
Description: extract_insights() passes the return value of invoke_fresh_llm() directly to _extract_json() without checking if response is None first; when invoke_fresh_llm returns (None, error), _extract_json(None) is called — the function tries json.loads(None), which raises TypeError that is caught by the outer except, but only logged at DEBUG level, silently returning {}.
Reproduction: Configure the LLM to time out (timeout_seconds=0 or unreachable endpoint). invoke_fresh_llm returns (None, "timeout:0s"). Line 505 calls self._extract_json(None); json.loads(None) raises TypeError; caught at line 506 and silently returns {}.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/research_agent/web_researcher.py
Line: 487-490
Severity: LIGHT
Type: incorrect_behavior
Description: When results list is empty, the join at line 487 produces an empty string results_text; the template substitution produces a prompt asking the LLM to analyze zero results, wasting an LLM call; the guard at line 566 (`if result.results:`) prevents calling extract_insights when the outer results are empty, but internal callers of extract_insights directly can still pass an empty list.
Reproduction: Call researcher.extract_insights("topic", []) directly. results_text becomes "", a full LLM call is made with an empty context, and the result depends on the LLM's hallucination rather than real data.
---END BUG---

---SUSPECTED---
File: /home/vader/AI-AtlasForge/af_engine/integrations/post_mission_hooks.py
Line: 197-202
Description: deliverables is joined with "\n" (line 202) and written to env["AF_DELIVERABLES"]; if any deliverable string itself contains newlines, the resulting AF_DELIVERABLES env var will have extra newlines, corrupting the delimiter-based format. Consumers parsing by line would then mis-split deliverables. However, whether any consumer actually parses AF_DELIVERABLES by newline is not confirmed from this file alone.
---END SUSPECTED---

---SUSPECTED---
File: /home/vader/AI-AtlasForge/adversarial_testing/blind_agent_runner.py
Line: 474-477
Description: Symlink check for results_file and findings_file uses is_symlink() at check time, then opens the file — TOCTOU race window: an attacker could replace a non-symlink file with a symlink between the check and the open() call. The window is small but non-zero in a multi-process environment.
---END SUSPECTED---

---SUSPECTED---
File: /home/vader/AI-AtlasForge/dashboard_modules/core.py
Line: 741-753
Description: api_add_recommendation() accepts a "suggested_cycles" field from the JSON body without validation (data.get("suggested_cycles", 3) at line 748); it is stored directly in the recommendation dict. The update endpoint at line 977-989 validates 1-10, but the add endpoint has no such bounds check, allowing cycles values like -1, 0, or 999999 to be stored.
---END SUSPECTED---

---SUSPECTED---
File: /home/vader/AI-AtlasForge/suggestion_storage.py
Line: 126-132
Description: The _get_connection() context manager calls conn.commit() after yield and conn.rollback() on exception, but the `finally: conn.close()` at line 133 fires regardless. If rollback() succeeds and raises a new exception (rollback_err), the original exception is suppressed and only rollback_err is re-raised by the warning logger -- except the code calls logger.warning and then raise, so the original exception is preserved. Appears safe but worth noting the double-exception path is not tested.
---END SUSPECTED---
