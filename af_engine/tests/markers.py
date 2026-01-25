"""
Regression Test Markers for af_engine Test Suite.

This module documents all custom pytest markers used in the test suite.
The markers are registered in conftest.py's pytest_configure function.

USAGE:
    # Run all regression tests
    pytest -m regression

    # Run specific regression category
    pytest -m regression_iteration_counter
    pytest -m regression_needs_replanning
    pytest -m regression_needs_revision
    pytest -m regression_cycle_budget
    pytest -m regression_timeout_retry

    # Run all unit tests
    pytest -m unit

    # Run all integration tests
    pytest -m integration

    # Exclude slow tests
    pytest -m "not slow"

    # Run tests requiring real Claude API
    pytest -m real_claude

MARKER REFERENCE:
    @pytest.mark.regression
        General regression test marker. Use for any test that prevents
        a previously-fixed bug from reoccurring.

    @pytest.mark.regression_iteration_counter
        Tests for iteration counter bugs. Ensures:
        - Iteration stays at 0 for successful missions
        - Iteration increments exactly once per revision cycle
        - Iteration is persisted correctly

    @pytest.mark.regression_needs_replanning
        Tests for needs_replanning functionality. Ensures:
        - ANALYZING returns to PLANNING when needs_replanning
        - Iteration increments when replanning
        - _increment_iteration flag is set correctly

    @pytest.mark.regression_needs_revision
        Tests for needs_revision functionality. Ensures:
        - ANALYZING returns to BUILDING when needs_revision
        - Iteration increments when revising
        - _increment_iteration flag is set correctly

    @pytest.mark.regression_cycle_budget
        Tests for cycle budget handling. Ensures:
        - cycle_budget is respected
        - Continuation prompts generated correctly
        - Mission completes when budget exhausted
        - Cycle history maintained

    @pytest.mark.regression_timeout_retry
        Tests for timeout/retry handling. Ensures:
        - MAX_CLAUDE_RETRIES is respected
        - None responses handled gracefully
        - Empty responses handled gracefully

    @pytest.mark.unit
        Unit tests that test individual components in isolation.
        These tests should be fast and not require external services.

    @pytest.mark.integration
        Integration tests that test multiple components together.
        May require more setup but still use mocked external services.

    @pytest.mark.slow
        Tests that take a long time to run.
        Use: pytest -m "not slow" to skip these.

    @pytest.mark.real_claude
        Tests that require actual Claude API calls.
        These tests are skipped by default and require:
        - Valid ANTHROPIC_API_KEY
        - Network access
        - API credits

BUG CATEGORIES:

1. ITERATION COUNTER BUGS
   Prior bugs:
   - Iteration incrementing on every stage transition (should only on revision)
   - Iteration incrementing on success path (should stay at 0)
   - Iteration not persisting to disk

   Test coverage:
   - test_iteration_logic.py::TestIterationZeroOnSuccess
   - test_iteration_logic.py::TestIterationIncrementOnNeedsRevision
   - test_iteration_logic.py::TestIterationIncrementOnNeedsReplanning
   - test_iteration_logic.py::TestMultipleRevisionCycles
   - test_iteration_logic.py::TestIterationPersistence

2. NEEDS_REPLANNING BUGS
   Prior bugs:
   - ANALYZING not recognizing needs_replanning status
   - Not returning to PLANNING stage
   - Not incrementing iteration

   Test coverage:
   - test_iteration_logic.py::TestIterationIncrementOnNeedsReplanning
   - test_e2e_integration.py (integration paths)

3. NEEDS_REVISION BUGS
   Prior bugs:
   - ANALYZING not recognizing needs_revision status
   - Not returning to BUILDING stage
   - Not incrementing iteration

   Test coverage:
   - test_iteration_logic.py::TestIterationIncrementOnNeedsRevision
   - test_e2e_integration.py (integration paths)

4. CYCLE_BUDGET BUGS
   Prior bugs:
   - Mission not completing when budget exhausted
   - Continuation prompts missing/malformed
   - Cycle history not persisted

   Test coverage:
   - test_cycle_budget.py::TestSingleCycleMission
   - test_cycle_budget.py::TestMultiCycleMission
   - test_cycle_budget.py::TestCycleManagerBehavior
   - test_cycle_budget.py::TestCycleHistoryTracking

5. TIMEOUT/RETRY BUGS
   Prior bugs:
   - Crash on None response from Claude
   - MAX_CLAUDE_RETRIES not respected
   - Poor error recovery

   Test coverage:
   - test_failure_scenarios.py::TestNoneResponseHandling
   - test_failure_scenarios.py::TestEmptyResponseHandling
   - test_failure_scenarios.py::TestOrchestratorErrorRecovery

ADDING NEW REGRESSION TESTS:

When fixing a bug:
1. Write a test that FAILS with the bug present
2. Fix the bug
3. Verify the test PASSES
4. Mark the test with appropriate regression markers
5. Add documentation here

Example:
    @pytest.mark.regression
    @pytest.mark.regression_iteration_counter
    def test_iteration_not_incremented_on_success():
        '''Regression test for bug #123: iteration incremented on success.'''
        # ... test code ...

RUNNING REGRESSION TESTS IN CI:

The recommended CI configuration:

    # Run all regression tests (fast)
    pytest -m regression --tb=short

    # Run full test suite
    pytest --tb=short

    # Run with coverage
    pytest -m regression --cov=af_engine --cov-report=html
"""

# Marker strings for programmatic use
MARKERS = {
    "regression": "marks tests as regression tests",
    "regression_iteration_counter": "regression tests for iteration counter bugs",
    "regression_needs_replanning": "regression tests for needs_replanning functionality",
    "regression_needs_revision": "regression tests for needs_revision functionality",
    "regression_cycle_budget": "regression tests for cycle budget handling",
    "regression_timeout_retry": "regression tests for timeout/retry logic",
    "unit": "marks tests as unit tests",
    "integration": "marks tests as integration tests",
    "slow": "marks tests as slow (deselect with '-m \"not slow\"')",
    "real_claude": "marks tests requiring actual Claude API",
}


def get_marker_help() -> str:
    """Get formatted help text for all markers."""
    lines = ["Available pytest markers:", ""]
    for marker, description in MARKERS.items():
        lines.append(f"  @pytest.mark.{marker}")
        lines.append(f"      {description}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    print(get_marker_help())
