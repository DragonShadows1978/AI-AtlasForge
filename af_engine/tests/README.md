# af_engine Test Suite

End-to-end integration test suite for the modular af_engine that validates real mission execution.

## Overview

This test suite provides comprehensive coverage for the AtlasForge modular engine including:

- **Full lifecycle testing**: PLANNING → BUILDING → TESTING → ANALYZING → CYCLE_END → COMPLETE
- **Iteration counter behavior**: Validates iteration stays at 0 for success, increments on revision
- **Stage transition validation**: Ensures stages transition in correct order
- **Tool permission enforcement**: Verifies InitGuard enforces stage-specific restrictions
- **Failure scenario simulation**: Tests timeout, needs_revision, needs_replanning paths
- **Multi-cycle budget handling**: Tests cycle_budget, continuation prompts, budget exhaustion

## Quick Start

```bash
# Run all tests
pytest af_engine/tests/ -v

# Run regression tests only (recommended for CI)
pytest af_engine/tests/ -m regression -v

# Run specific test category
pytest af_engine/tests/ -m regression_iteration_counter
pytest af_engine/tests/ -m regression_needs_revision
pytest af_engine/tests/ -m regression_needs_replanning

# Exclude slow tests
pytest af_engine/tests/ -m "not slow"

# Run with parallel execution
pytest af_engine/tests/ -n auto -v
```

## Test Structure

```
af_engine/tests/
├── README.md                       # This documentation
├── conftest.py                     # Shared fixtures
├── markers.py                      # Marker documentation & reference
├── test_e2e_integration.py         # Full lifecycle tests
├── test_iteration_logic.py         # Iteration counter behavior
├── test_cycle_budget.py            # Multi-cycle budget tests
├── test_stage_restrictions.py      # Tool permission enforcement
├── test_failure_scenarios.py       # Error handling tests
├── test_conductor_timeout.py       # Timeout/retry handling
├── test_init_guard.py              # InitGuard enforcement
├── test_pipeline_integration.py    # Full pipeline mock tests
├── test_orchestrator.py            # Orchestrator unit tests
├── test_cycle_manager.py           # Cycle manager unit tests
├── test_prompt_factory.py          # Prompt generation tests
├── test_integration_manager.py     # Event system tests
└── test_real_claude_integration.py # Optional real API smoke tests
```

## Test Markers

### Regression Markers

These markers identify tests that prevent previously-fixed bugs from reoccurring:

| Marker | Purpose |
|--------|---------|
| `@pytest.mark.regression` | General regression test |
| `@pytest.mark.regression_iteration_counter` | Iteration counter bugs |
| `@pytest.mark.regression_needs_revision` | needs_revision functionality |
| `@pytest.mark.regression_needs_replanning` | needs_replanning functionality |
| `@pytest.mark.regression_cycle_budget` | Cycle budget handling |
| `@pytest.mark.regression_timeout_retry` | Timeout/retry logic |

### Test Type Markers

| Marker | Purpose |
|--------|---------|
| `@pytest.mark.unit` | Unit tests (isolated components) |
| `@pytest.mark.integration` | Integration tests (multiple components) |
| `@pytest.mark.slow` | Long-running tests |
| `@pytest.mark.real_claude` | Tests requiring actual Claude API |

### Running by Marker

```bash
# All regression tests
pytest -m regression

# Specific regression category
pytest -m regression_iteration_counter
pytest -m "regression_needs_revision or regression_needs_replanning"

# Integration tests only
pytest -m integration

# Everything except slow tests
pytest -m "not slow"
```

## Key Fixtures

### Mission Factory

Creates fully-initialized mission state dictionaries:

```python
def test_example(mission_factory):
    mission = mission_factory(
        mission_id="test_123",
        cycle_budget=3,
        current_cycle=1,
        iteration=0,
        current_stage="PLANNING"
    )
```

### Claude Response Factory

Creates mock Claude responses for each stage:

```python
def test_example(claude_response_factory):
    response = claude_response_factory("ANALYZING", status="needs_revision")
```

### Orchestrator Factory

Creates StageOrchestrator instances with mocked dependencies:

```python
def test_example(orchestrator_factory, mission_factory):
    mission = mission_factory(cycle_budget=1)
    orch = orchestrator_factory(mission=mission)
```

### Stage Handlers

Individual stage handler fixtures:

```python
def test_example(analyzing_handler, stage_context_factory):
    context = stage_context_factory()
    response = {"status": "success", ...}
    result = analyzing_handler.process_response(response, context)
```

### Pre-built Response Fixtures

```python
def test_revision_path(needs_revision_response, analyzing_handler):
    result = analyzing_handler.process_response(needs_revision_response, context)
    assert result.output.get("_increment_iteration") is True

def test_replanning_path(needs_replanning_response, analyzing_handler):
    result = analyzing_handler.process_response(needs_replanning_response, context)
    assert result.next_stage == "PLANNING"
```

## Adding New Tests

### Adding a Regression Test

When fixing a bug:

1. **Write a failing test first** that exposes the bug
2. **Fix the bug** in the implementation
3. **Verify the test passes**
4. **Add appropriate markers**

```python
@pytest.mark.regression
@pytest.mark.regression_iteration_counter
def test_iteration_not_incremented_on_success():
    """Regression test for bug #123: iteration incremented on success.

    This test ensures iteration counter stays at 0 throughout a successful
    mission that doesn't require revision or replanning.
    """
    # Test implementation
    ...
```

### Adding an Integration Test

```python
@pytest.mark.integration
def test_full_pipeline_with_revision(orchestrator_factory, claude_response_factory):
    """Test complete pipeline with one revision cycle."""
    # Setup
    orch = orchestrator_factory()

    # Exercise the system through multiple stages
    ...

    # Verify end state
    assert orch.state_manager.iteration == 1
```

### Adding a Unit Test

```python
@pytest.mark.unit
def test_analyzing_handler_parses_status(analyzing_handler):
    """Unit test for AnalyzingStageHandler.process_response."""
    response = {"status": "needs_revision", ...}
    result = analyzing_handler.process_response(response, context)
    assert result.next_stage == "BUILDING"
```

## Test Categories

### 1. Iteration Counter Tests (`test_iteration_logic.py`)

Validates:
- Iteration stays at 0 for successful pass-through
- Iteration increments exactly once per revision
- Iteration persists correctly to disk
- Multiple revision cycles handled correctly

### 2. Needs Revision/Replanning Tests

Validates:
- `needs_revision` returns to BUILDING with iteration+1
- `needs_replanning` returns to PLANNING with iteration+1
- `_increment_iteration` flag set correctly in handler output

### 3. Cycle Budget Tests (`test_cycle_budget.py`)

Validates:
- `cycle_budget` is respected
- Continuation prompts generated correctly
- Mission completes when budget exhausted
- Cycle history maintained

### 4. Tool Restriction Tests (`test_stage_restrictions.py`, `test_init_guard.py`)

Validates:
- Stage-specific tool permissions enforced
- Write path restrictions enforced
- Blocked tools rejected with clear errors

### 5. Timeout/Retry Tests (`test_conductor_timeout.py`)

Validates:
- MAX_CLAUDE_RETRIES respected
- None responses handled gracefully
- Signal files work correctly for communication

## CI Integration

Recommended CI configuration:

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install pytest pytest-xdist pytest-cov
          pip install -r requirements.txt

      - name: Run regression tests
        run: pytest af_engine/tests/ -m regression --tb=short

      - name: Run full test suite
        run: pytest af_engine/tests/ -v --tb=short

      - name: Run parallel tests
        run: pytest af_engine/tests/ -n auto
```

## Running Real Claude API Tests

Some tests validate against the actual Claude API. These are **skipped by default** because they:

- Require a valid `ANTHROPIC_API_KEY`
- Consume API credits
- Take longer to run

To run them:

```bash
# Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Run real Claude tests
pytest af_engine/tests/ -m real_claude -v
```

## Parallel Execution

Tests are designed to be independent and can run in parallel:

```bash
# Install pytest-xdist
pip install pytest-xdist

# Run in parallel (auto-detect CPU count)
pytest af_engine/tests/ -n auto

# Run with specific worker count
pytest af_engine/tests/ -n 4
```

## Troubleshooting

### Import Errors

If you see import errors, ensure the af_engine root is in your path:

```bash
export PYTHONPATH=/path/to/AI-AtlasForge:$PYTHONPATH
```

Or run from the AtlasForge root:

```bash
cd /path/to/AI-AtlasForge
pytest af_engine/tests/ -v
```

### Fixture Not Found

Ensure `conftest.py` is present in the tests directory. Fixtures are automatically discovered from this file.

### Test Isolation Issues

If tests fail when run together but pass individually, there may be shared state issues. Run with `-n 1` to debug:

```bash
pytest af_engine/tests/test_specific.py -n 1 -v
```

## Coverage

Generate a coverage report:

```bash
pytest af_engine/tests/ --cov=af_engine --cov-report=html
open htmlcov/index.html
```

## Contributing

When adding tests:

1. Place tests in the appropriate module based on what's being tested
2. Use descriptive test names that explain what's being verified
3. Add docstrings explaining the test purpose and any regression context
4. Apply appropriate markers (`@pytest.mark.regression`, etc.)
5. Update this README if adding new test categories or markers
