# Red Team Findings — brt_20260303_181240_agent0

---BUG---
File: /home/vader/AI-AtlasForge/atlasforge_conductor.py
Line: 702-764
Severity: HIGH
Type: race_condition
Description: The stream thread `_stdout_thread` is started but never joined before `_recon` reads from the same stream file at line 764, creating a time-of-check/time-of-use race where reconstruction may read a partially-written stream file and return an empty or truncated response.
Reproduction: 1. Enable Claude streaming mode (provider="claude", _stream_file set). 2. Run invoke_llm() with a fast-completing Claude process under CPU load. 3. _recon at line 764 reads _stream_file while _stdout_thread is still writing. blind_agent_runner.py correctly calls stream_thread.join(timeout=5) at line 681 but atlasforge_conductor.py::invoke_llm has no join() call.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/hierarchical_framework.py
Line: 464-526
Severity: HIGH
Type: race_condition
Description: The stream_thread started at line 464-470 for HierarchicalFramework worker agents is never joined. The code uses a fixed 0.5-second sleep at line 525 as a heuristic before calling _asm_reconstruct. Under CPU load or slow I/O, the thread may not have flushed all JSONL chunks, causing _asm_reconstruct to return truncated or empty response and silently fail.
Reproduction: Run hierarchical_framework.py worker agent spawn under CPU load. The 0.5s sleep is insufficient and stream_thread.join() is absent, unlike blind_agent_runner.py which correctly joins.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/investigation_engine.py
Line: 690-725
Severity: HIGH
Type: race_condition
Description: In invoke_claude with provider="claude", streaming thread t is started at line 696 but never joined before _recon reads the stream file at line 725. After proc.wait() returns, the thread may still be writing the final chunks. No join() call exists anywhere in this code path.
Reproduction: Call invoke_claude(prompt, model=ModelType.CLAUDE_SONNET) with any prompt under load. The stream thread may not have completed by the time _recon reconstructs the response, producing an empty or truncated result.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/dashboard_modules/investigation.py
Line: 431-433
Severity: MODERATE
Type: security
Description: _safe_filename only strips CR and LF characters but does not strip semicolons, double quotes, or other characters that allow Content-Disposition parameter injection. A filename like 'foo; type=text/html' passes through unchanged, allowing injection of extra parameters into the Content-Disposition header.
Reproduction: Access /api/investigation/<id>/export?format=pdf with investigation_id value containing '; type=text/html'. The resulting header becomes: Content-Disposition: attachment; filename=investigation_foo; type=text/html.pdf which some HTTP clients parse as setting Content-Type.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/adversarial_testing/mutation_testing.py
Line: 317-333
Severity: MODERATE
Type: missing_validation
Description: MutationTester.__init__ does not validate that max_mutants is positive. Passing max_mutants=0 causes the generate_mutants() condition at line 357 to always be True (any non-empty list > 0), then k = min(0, len) = 0, and random.sample(indexed_mutations, 0) returns an empty list, silently producing zero mutants with no error. Passing max_mutants=-5 avoids the branch entirely because no list len > -5 is False — meaning all mutations are used, silently overriding the intended limit.
Reproduction: MutationTester(max_mutants=0).generate_mutants("x = 1 + 2") returns [] with no warning. MutationTester(max_mutants=-1).generate_mutants(valid_code) returns ALL mutations instead of respecting the limit.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/adversarial_testing/mutation_testing.py
Line: 317-333
Severity: MODERATE
Type: missing_validation
Description: MutationTester.__init__ does not validate that sample_ratio is in the range (0, 1]. Passing sample_ratio=0.0 triggers the < 1.0 branch (line 513), computes sample_size = max(1, int(len * 0.0)) = 1, and silently samples exactly 1 mutant. Passing sample_ratio=-0.5 also triggers the branch and also samples exactly 1 mutant due to the max(1,...) guard. Callers expecting a "no sample" behavior from sample_ratio=0.0 get unexpected results.
Reproduction: MutationTester(sample_ratio=0.0) with 100 mutants samples exactly 1 instead of 0 or raising an error.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/adversarial_testing/mutation_testing.py
Line: 449-450
Severity: MODERATE
Type: logic_error
Description: subprocess.TimeoutExpired causes mutant.killed = True with no error flag set. Timed-out mutants are counted as "killed" in the mutation score (line 525: killed = sum(m.killed and not m.error)), inflating the score. A timeout means the test suite hung — not that the tests detected the mutation. The mutation score is therefore overestimated when timeout occurs.
Reproduction: Create a mutant that causes the test suite to hang. test_mutant() times out, sets killed=True, and the score calculation at line 531 counts it as a genuine kill, inflating mutation_score.
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/adversarial_testing/property_testing.py
Line: 307-329
Severity: MODERATE
Type: missing_validation
Description: InputGenerator.dicts() does not have a count <= 0 guard, unlike integers(), strings(), lists(), and floats() which all guard against zero/negative count. Calling dicts(count=0) or dicts(count=-1) still returns the 6 hard-coded edge cases instead of an empty list, violating the consistent API contract. Code that calls dicts() with count=0 expecting an empty list receives 6 items.
Reproduction: InputGenerator().dicts(count=0) returns a non-empty list of 6 GeneratedInput items instead of [].
---END BUG---

---BUG---
File: /home/vader/AI-AtlasForge/adversarial_testing/property_testing.py
Line: 440-449
Severity: MODERATE
Type: logic_error
Description: In test_property, count_per_type = max(1, self.max_inputs // len(input_types)). If input_types is an empty list [], the early return at line 444 guards against the division. However if input_types contains unknown types (not "int", "str", "float", "list") the type-dispatch loop at lines 451-458 silently adds zero inputs for those types. total_inputs in run_property_testing (line 614) still adds self.max_inputs for each property even when zero inputs were generated, causing total_inputs_generated to be inflated and inaccurate.
Reproduction: Call test_property with property_spec containing input_types=["dict"] (not in dispatch list). inputs remains empty, no violations found, but total_inputs_generated counts self.max_inputs anyway.
---END BUG---

---SUSPECTED---
File: /home/vader/AI-AtlasForge/atlasforge_conductor.py
Line: 2060-2083
Description: When HAS_RESPONSE_ADAPTER is False and JSON parsing fails, timeout_retries is incremented at line 2072 for what is actually a parse failure, not a transport-level timeout. After MAX_CLAUDE_RETRIES=3 parse failures in a row with no adapter, the mission halts. This conflates transport failures with parse failures. Cannot fully confirm all code paths without running under no-adapter conditions.
---END SUSPECTED---

---SUSPECTED---
File: /home/vader/AI-AtlasForge/dashboard_modules/investigation.py
Line: 1132-1148
Description: In the markdown export path, user-controlled values (query, status, tags) are embedded directly into md_header without any escaping. If these contain markdown injection characters (e.g. LaTeX-style math, raw HTML, or script tags) and the markdown is later auto-rendered to HTML by a downstream consumer, XSS is possible. Cannot confirm downstream rendering without tracing the full display pipeline.
---END SUSPECTED---

---SUSPECTED---
File: /home/vader/AI-AtlasForge/adversarial_testing/blind_agent_runner.py
Line: 720-726
Description: _asm_complete is called after a time.sleep(0.3) to "let streaming thread flush". If the stream thread is still writing at that point and then _asm_complete marks the agent as done in the dashboard, the dashboard may show a completed state while stream content is still being written. This is distinct from the stdout race (stream_thread.join was called at line 681) — the 0.3s sleep only covers the complete notification path, not the stdout drain path. Cannot confirm if this causes visible user-facing corruption without testing under load.
---END SUSPECTED---
