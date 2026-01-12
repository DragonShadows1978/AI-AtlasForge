# Adversarial Testing Framework

A comprehensive framework for epistemically rigorous testing that breaks the "painter who loves their own work" problem.

## Overview

Traditional testing has a fundamental flaw: the same entity that builds the code designs the tests. This creates blind spots where tests naturally pass because they're designed around the implementation rather than against it.

This framework addresses this by:
1. **Red Team Analysis** - Fresh AI agents with no knowledge of implementation try to break your code
2. **Mutation Testing** - Verify tests catch actual bugs by mutating the code
3. **Property Testing** - Generate edge cases you didn't think of
4. **Blind Validation** - Independent spec checking to catch "drift"

## Quick Start

### Simple Usage

```python
from pathlib import Path
from adversarial_testing import AdversarialRunner, AdversarialConfig

config = AdversarialConfig(
    mission_id="my_project",
    enable_mutation=False  # Skip if no test suite exists
)

runner = AdversarialRunner(config)
results = runner.run_full_suite(
    code_path=Path("my_code.py"),
    specification="Requirements: Must handle division by zero safely",
    progress_callback=print
)

print(f"Score: {results.report.epistemic_score.overall_score:.0%}")
print(f"Rigor: {results.report.epistemic_score.rigor_level.value}")
```

### Quick Mode (Fast Feedback)

```python
from adversarial_testing import EnhancedAdversarialRunner, AdversarialMode

runner = EnhancedAdversarialRunner(mode=AdversarialMode.QUICK)
result = runner.run_quick(
    code_path=Path("my_code.py"),
    description="API endpoints for user management"
)

print(f"Found {result.total_issues} issues")
for finding in result.findings:
    print(f"[{finding.severity}] {finding.title}")
```

### With Cost Estimation

```python
from adversarial_testing import (
    EnhancedAdversarialRunner,
    AdversarialMode
)

runner = EnhancedAdversarialRunner(
    mode=AdversarialMode.STANDARD,
    budget_limit=1.00  # Max $1.00
)

# Check cost before running
estimate = runner.estimate_cost(code_path=Path("my_code.py"))
print(f"Estimated: ${estimate.total_estimated_cost:.4f}")

if estimate.total_estimated_cost < 1.00:
    results = runner.run(
        code_path=Path("my_code.py"),
        specification="Requirements...",
        progress_callback=print
    )
```

## Testing Modes

| Mode | Model | Components | Typical Cost | Use Case |
|------|-------|------------|--------------|----------|
| `QUICK` | Haiku | Red team only | $0.01-0.05 | Fast feedback during dev |
| `STANDARD` | Sonnet | All components | $0.10-0.50 | Regular testing |
| `FULL` | Sonnet | All + extra passes | $0.50-2.00 | Pre-release validation |
| `THOROUGH` | Opus | Maximum rigor | $2.00-10.00 | Critical systems |

## Components

### Red Team Agent
Spawns a fresh Claude instance with no implementation knowledge to adversarially analyze code.

```python
from adversarial_testing import RedTeamAgent

agent = RedTeamAgent(model=ModelType.CLAUDE_HAIKU)
result = agent.analyze_code(
    code=source_code,
    description="User authentication module"
)

for finding in result.findings:
    print(f"[{finding.severity}] {finding.category.value}: {finding.title}")
    print(f"  Fix: {finding.suggested_fix}")
```

### Mutation Testing
Verifies test quality by mutating code and checking if tests catch the bugs.

```python
from adversarial_testing import MutationTester

tester = MutationTester(max_mutants=30)
result = tester.run_mutation_testing(
    code_path=Path("my_module.py"),
    test_command="pytest tests/test_my_module.py"
)

print(f"Mutation score: {result.score.score:.0%}")
print(f"Killed: {result.score.killed_mutants}/{result.score.total_mutants}")
```

### Property Testing
Generates adversarial inputs automatically.

```python
from adversarial_testing import PropertyTester

tester = PropertyTester(max_inputs=50)
result = tester.run_property_testing(
    code=source_code,
    function_name="process_data"
)

print(f"Violations: {len(result.violations)}")
for v in result.violations:
    print(f"  Property {v.property_name} failed: {v.error_message}")
```

### Blind Validator
Validates implementation against original specification without seeing the code first.

```python
from adversarial_testing import BlindValidator

validator = BlindValidator()
result = validator.validate(
    specification="Must return sum of two numbers, handling overflow",
    implementation=source_code
)

print(f"Status: {result.overall_status.value}")
print(f"Spec drift: {result.spec_drift_detected}")
```

## Cost Management

### Cost Estimation

```python
from adversarial_testing import CostEstimator

estimator = CostEstimator()

# Compare modes
for mode in ['quick', 'standard', 'full']:
    estimate = estimator.estimate_full_suite(
        code_text=source_code,
        model=ModelType.CLAUDE_SONNET
    )
    print(f"{mode}: ${estimate.total_estimated_cost:.4f}")
```

### Budget Tracking

```python
from adversarial_testing import BudgetTracker

tracker = BudgetTracker(budget_limit=1.00)

# Check before expensive operation
if tracker.can_spend(0.25):
    # run operation
    tracker.record_spend(0.23, "red_team", tokens=2500)

print(f"Spent: ${tracker.spent:.4f}")
print(f"Remaining: ${tracker.remaining:.4f}")
```

## Vulnerability Pattern Database

The framework learns from findings across missions.

```python
from adversarial_testing import VulnerabilityDatabase

db = VulnerabilityDatabase()

# See common patterns
for pattern in db.get_common_patterns(limit=5):
    print(f"- {pattern.name}: {pattern.occurrences} occurrences")

# Get enhanced prompt for red team
enhancement = db.generate_prompt_enhancement()

# Export for analysis
db.export_patterns(Path("vulnerability_report.json"))
```

## Resilience Features

### Automatic Retry

```python
from adversarial_testing import with_retry, RetryConfig

@with_retry(RetryConfig(max_retries=3, initial_delay=1.0))
def flaky_api_call():
    # Automatically retries on timeout/network errors
    return make_external_call()
```

### Progress Tracking

```python
from adversarial_testing import ProgressTracker

tracker = ProgressTracker("Analysis", total_items=5)
tracker.set_callback(lambda r: print(r))

with tracker.stage("Red Team"):
    tracker.item_complete("file1.py")
    tracker.item_complete("file2.py")
```

### Graceful Degradation

```python
from adversarial_testing import ResilientRunner

resilient = ResilientRunner(progress_callback=print)

result = resilient.run_with_resilience(
    func=lambda: expensive_operation(),
    component="validation",
    timeout=60
)

# Returns None if all retries fail, doesn't crash
if result is None:
    print("Skipping due to errors")
```

## Epistemic Metrics

The framework produces a comprehensive epistemic score:

| Metric | Weight | Description |
|--------|--------|-------------|
| Mutation Score | 30% | Percentage of mutants killed by tests |
| Adversarial Score | 25% | Inverse of issues found by red team |
| Property Score | 20% | Properties without violations |
| Spec Alignment | 25% | Requirements validated by blind check |

### Rigor Levels

| Level | Score | Meaning |
|-------|-------|---------|
| `RIGOROUS` | >= 95% | Comprehensive adversarial validation |
| `STRONG` | >= 85% | Solid adversarial coverage |
| `MODERATE` | >= 70% | Reasonable testing |
| `WEAK` | >= 50% | Basic testing only |
| `INSUFFICIENT` | < 50% | Tests prove nothing |

## Common Failure Patterns

### SQL Injection
```python
# BAD
query = f"SELECT * FROM users WHERE id = {user_id}"

# GOOD
cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
```

### Division by Zero
```python
# BAD
def ratio(a, b):
    return a / b

# GOOD
def ratio(a, b):
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b
```

### Unhandled Empty Input
```python
# BAD
def first_item(items):
    return items[0]

# GOOD
def first_item(items):
    if not items:
        raise IndexError("Cannot get first item of empty sequence")
    return items[0]
```

### Command Injection
```python
# BAD
os.system(f"echo {user_input}")

# GOOD
subprocess.run(["echo", user_input], shell=False)
```

## API Reference

### AdversarialConfig

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mission_id` | str | "default" | Unique identifier |
| `model` | ModelType | SONNET | Model to use |
| `timeout_seconds` | int | 300 | Total timeout |
| `enable_red_team` | bool | True | Run red team analysis |
| `enable_mutation` | bool | True | Run mutation testing |
| `enable_property` | bool | True | Run property testing |
| `enable_blind_validation` | bool | True | Run blind validation |
| `enable_parallel` | bool | True | Parallelize where possible |

### EnhancedConfig (extends AdversarialConfig)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mode` | AdversarialMode | STANDARD | Testing mode |
| `budget_limit` | float | None | Max USD to spend |
| `enable_vuln_db` | bool | True | Use vulnerability database |
| `enable_resilience` | bool | True | Enable retry logic |
| `use_historical_patterns` | bool | True | Enhance prompts with patterns |

## File Structure

```
adversarial_testing/
├── __init__.py              # Package exports
├── adversarial_runner.py    # Core orchestrator
├── enhanced_runner.py       # Production runner (Cycle 2)
├── red_team_agent.py        # Red team spawning
├── mutation_testing.py      # Mutation analysis
├── property_testing.py      # Property-based testing
├── blind_validator.py       # Spec validation
├── epistemic_metrics.py     # Scoring system
├── cost_estimator.py        # Cost estimation (Cycle 2)
├── vulnerability_database.py # Pattern storage (Cycle 2)
├── resilience.py            # Error handling (Cycle 2)
└── README.md                # This file
```

## Integration with RDE

The adversarial testing framework is integrated into the R&D Engine's TESTING stage.

```python
# In rd_engine.py TESTING prompt:
from adversarial_testing import AdversarialRunner, AdversarialConfig
from experiment_framework import ModelType

config = AdversarialConfig(
    mission_id="your_mission",
    model=ModelType.CLAUDE_HAIKU,
    enable_mutation=False
)

runner = AdversarialRunner(config)
results = runner.run_full_suite(
    code_path=Path("path/to/code.py"),
    specification="Original requirements..."
)
```

## Best Practices

1. **Always get cost estimate first** - Avoid surprise API bills
2. **Use QUICK mode during development** - Save STANDARD/FULL for CI
3. **Enable vulnerability database** - Learn from past findings
4. **Set budget limits** - Prevent runaway costs
5. **Review red team findings** - They catch real bugs
6. **Don't ignore spec drift** - It indicates design/impl mismatch
7. **Target 80%+ mutation score** - Below that, tests are unreliable
