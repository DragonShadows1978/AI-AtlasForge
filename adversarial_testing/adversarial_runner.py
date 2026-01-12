"""
Adversarial Runner - Main orchestrator for adversarial testing.

This module coordinates all adversarial testing components:
1. Red Team Analysis - Fresh agents trying to break the code
2. Mutation Testing - Verify tests actually catch bugs
3. Property Testing - Generate edge cases automatically
4. Blind Validation - Independent spec verification

The runner produces a comprehensive TestSuiteReport with epistemic metrics.
"""

import json
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from experiment_framework import ModelType

# Local imports
from .red_team_agent import RedTeamAgent, RedTeamResult
from .mutation_testing import MutationTester, MutationResult
from .property_testing import PropertyTester, PropertyTestResult
from .blind_validator import BlindValidator, ValidationResult
from .epistemic_metrics import (
    EpistemicMetrics,
    EpistemicScore,
    TestSuiteReport,
    RigorLevel
)


@dataclass
class AdversarialConfig:
    """Configuration for adversarial testing run."""
    # General
    mission_id: str = "default"
    model: ModelType = ModelType.CLAUDE_SONNET
    timeout_seconds: int = 300  # 5 minutes total

    # Red team settings
    enable_red_team: bool = True
    red_team_model: Optional[ModelType] = None  # Defaults to main model

    # Mutation testing settings
    enable_mutation: bool = True
    max_mutants: int = 30
    mutation_timeout_per_mutant: int = 30

    # Property testing settings
    enable_property: bool = True
    max_inputs: int = 50
    property_model: Optional[ModelType] = None

    # Blind validation settings
    enable_blind_validation: bool = True
    validation_model: Optional[ModelType] = None

    # Parallel execution
    enable_parallel: bool = True
    max_workers: int = 3


@dataclass
class AdversarialResults:
    """Complete results from adversarial testing run."""
    config: AdversarialConfig
    report: TestSuiteReport
    started_at: str
    completed_at: str
    total_duration_ms: float
    success: bool = True
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "config": {
                "mission_id": self.config.mission_id,
                "model": self.config.model.value,
                "components_enabled": {
                    "red_team": self.config.enable_red_team,
                    "mutation": self.config.enable_mutation,
                    "property": self.config.enable_property,
                    "blind_validation": self.config.enable_blind_validation
                }
            },
            "report": self.report.to_dict() if self.report else None,
            "timing": {
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "total_duration_ms": self.total_duration_ms
            },
            "success": self.success,
            "errors": self.errors
        }

    def save(self, filepath: Optional[Path] = None) -> Path:
        """Save results to JSON file."""
        if filepath is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filepath = Path(f"adversarial_results_{self.config.mission_id}_{timestamp}.json")

        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

        return filepath

    @property
    def is_passing(self) -> bool:
        """Check if adversarial testing passed."""
        if not self.report or not self.report.epistemic_score:
            return False

        score = self.report.epistemic_score
        return score.rigor_level in [RigorLevel.STRONG, RigorLevel.RIGOROUS]

    @property
    def needs_attention(self) -> bool:
        """Check if there are critical issues that need attention."""
        if not self.report:
            return True

        # Check for critical red team findings
        if self.report.red_team_result:
            if self.report.red_team_result.critical_findings:
                return True

        # Check for spec drift
        if self.report.validation_result:
            if self.report.validation_result.spec_drift_detected:
                if self.report.validation_result.drift_severity in ["high", "critical"]:
                    return True

        # Check mutation score
        if self.report.mutation_result and self.report.mutation_result.score:
            if self.report.mutation_result.score.score < 0.6:
                return True

        return False


class AdversarialRunner:
    """
    Orchestrates comprehensive adversarial testing.

    Usage:
        runner = AdversarialRunner(config)
        results = runner.run_full_suite(
            code_path=Path("my_code.py"),
            test_command="pytest tests/",
            specification="Original requirements..."
        )

        if not results.is_passing:
            print("Issues found:", results.report.epistemic_score.recommendations)
    """

    def __init__(self, config: Optional[AdversarialConfig] = None):
        """
        Initialize adversarial runner.

        Args:
            config: Configuration for the run (uses defaults if not provided)
        """
        self.config = config or AdversarialConfig()

        # Initialize components based on config
        self.red_team_agent = RedTeamAgent(
            model=self.config.red_team_model or self.config.model,
            timeout_seconds=self.config.timeout_seconds // 4
        ) if self.config.enable_red_team else None

        self.mutation_tester = MutationTester(
            max_mutants=self.config.max_mutants,
            timeout_per_mutant=self.config.mutation_timeout_per_mutant
        ) if self.config.enable_mutation else None

        self.property_tester = PropertyTester(
            model=self.config.property_model or self.config.model,
            max_inputs=self.config.max_inputs
        ) if self.config.enable_property else None

        self.blind_validator = BlindValidator(
            model=self.config.validation_model or self.config.model,
            timeout_seconds=self.config.timeout_seconds // 3
        ) if self.config.enable_blind_validation else None

        self.metrics = EpistemicMetrics()

    def run_full_suite(
        self,
        code_path: Path,
        test_command: str = "",
        specification: str = "",
        function_names: Optional[List[str]] = None,
        self_test_findings: int = 0,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> AdversarialResults:
        """
        Run the complete adversarial testing suite.

        Args:
            code_path: Path to the code to test
            test_command: Command to run existing tests (for mutation testing)
            specification: Original specification (for blind validation)
            function_names: Specific functions to property test (auto-detected if not provided)
            self_test_findings: Number of issues found by self-tests (for metrics)
            progress_callback: Optional callback for progress updates

        Returns:
            AdversarialResults with complete report
        """
        start_time = datetime.now()
        code_path = Path(code_path)

        results = AdversarialResults(
            config=self.config,
            report=TestSuiteReport(
                mission_id=self.config.mission_id,
                code_path=str(code_path),
                timestamp=start_time.isoformat()
            ),
            started_at=start_time.isoformat(),
            completed_at="",
            total_duration_ms=0
        )

        # Read code
        if not code_path.exists():
            results.success = False
            results.errors.append(f"Code file not found: {code_path}")
            return results

        code = code_path.read_text()

        def log_progress(msg: str):
            if progress_callback:
                progress_callback(msg)

        # Run components (parallel or sequential)
        if self.config.enable_parallel and self.config.max_workers > 1:
            self._run_parallel(
                results, code, code_path, test_command, specification,
                function_names, log_progress
            )
        else:
            self._run_sequential(
                results, code, code_path, test_command, specification,
                function_names, log_progress
            )

        # Compute epistemic score
        results.report.self_test_passed = 0  # Would come from actual test run
        results.report.self_test_failed = self_test_findings
        results.report.epistemic_score = self.metrics.compute_full_score(
            red_team=results.report.red_team_result,
            mutation_result=results.report.mutation_result,
            property_result=results.report.property_result,
            validation_result=results.report.validation_result,
            self_test_findings=self_test_findings
        )

        # Finalize
        end_time = datetime.now()
        results.completed_at = end_time.isoformat()
        results.total_duration_ms = (end_time - start_time).total_seconds() * 1000

        log_progress(f"Adversarial testing complete. Score: {results.report.epistemic_score.overall_score:.0%}")

        return results

    def _run_sequential(
        self,
        results: AdversarialResults,
        code: str,
        code_path: Path,
        test_command: str,
        specification: str,
        function_names: Optional[List[str]],
        log_progress: Callable[[str], None]
    ):
        """Run all components sequentially."""

        # 1. Red Team Analysis
        if self.red_team_agent and self.config.enable_red_team:
            log_progress("Running red team analysis...")
            try:
                results.report.red_team_result = self.red_team_agent.analyze_code(
                    code=code,
                    description=f"Code from {code_path.name}",
                    session_id=f"rt_{self.config.mission_id}"
                )
                log_progress(f"Red team found {results.report.red_team_result.total_issues} issues")
            except Exception as e:
                results.errors.append(f"Red team failed: {e}")

        # 2. Mutation Testing
        if self.mutation_tester and self.config.enable_mutation and test_command:
            log_progress("Running mutation testing...")
            try:
                results.report.mutation_result = self.mutation_tester.run_mutation_testing(
                    code_path=code_path,
                    test_command=test_command
                )
                if results.report.mutation_result.score:
                    log_progress(f"Mutation score: {results.report.mutation_result.score.score:.0%}")
            except Exception as e:
                results.errors.append(f"Mutation testing failed: {e}")

        # 3. Property Testing
        if self.property_tester and self.config.enable_property:
            log_progress("Running property testing...")
            try:
                # Auto-detect functions if not provided
                if not function_names:
                    function_names = self._extract_function_names(code)

                for func_name in function_names[:3]:  # Limit to 3 functions
                    prop_result = self.property_tester.run_property_testing(
                        code=code,
                        function_name=func_name
                    )
                    if results.report.property_result is None:
                        results.report.property_result = prop_result
                    else:
                        # Merge results
                        results.report.property_result.violations.extend(prop_result.violations)
                        results.report.property_result.properties_tested.extend(prop_result.properties_tested)
                        results.report.property_result.total_inputs_generated += prop_result.total_inputs_generated

                if results.report.property_result:
                    log_progress(f"Property testing: {len(results.report.property_result.violations)} violations")
            except Exception as e:
                results.errors.append(f"Property testing failed: {e}")

        # 4. Blind Validation
        if self.blind_validator and self.config.enable_blind_validation and specification:
            log_progress("Running blind validation...")
            try:
                results.report.validation_result = self.blind_validator.validate(
                    specification=specification,
                    implementation=code
                )
                log_progress(f"Validation: {results.report.validation_result.overall_status.value}")
            except Exception as e:
                results.errors.append(f"Blind validation failed: {e}")

    def _run_parallel(
        self,
        results: AdversarialResults,
        code: str,
        code_path: Path,
        test_command: str,
        specification: str,
        function_names: Optional[List[str]],
        log_progress: Callable[[str], None]
    ):
        """Run components in parallel where possible."""

        tasks = {}

        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            # Submit tasks
            if self.red_team_agent and self.config.enable_red_team:
                tasks['red_team'] = executor.submit(
                    self.red_team_agent.analyze_code,
                    code=code,
                    description=f"Code from {code_path.name}",
                    session_id=f"rt_{self.config.mission_id}"
                )
                log_progress("Submitted red team analysis...")

            if self.blind_validator and self.config.enable_blind_validation and specification:
                tasks['validation'] = executor.submit(
                    self.blind_validator.validate,
                    specification=specification,
                    implementation=code
                )
                log_progress("Submitted blind validation...")

            # Mutation and property testing are harder to parallelize safely
            # Run them sequentially after parallel tasks complete

            # Collect parallel results
            for name, future in tasks.items():
                try:
                    result = future.result(timeout=self.config.timeout_seconds)
                    if name == 'red_team':
                        results.report.red_team_result = result
                        log_progress(f"Red team found {result.total_issues} issues")
                    elif name == 'validation':
                        results.report.validation_result = result
                        log_progress(f"Validation: {result.overall_status.value}")
                except Exception as e:
                    results.errors.append(f"{name} failed: {e}")

        # Run mutation testing (modifies files, must be sequential)
        if self.mutation_tester and self.config.enable_mutation and test_command:
            log_progress("Running mutation testing...")
            try:
                results.report.mutation_result = self.mutation_tester.run_mutation_testing(
                    code_path=code_path,
                    test_command=test_command
                )
                if results.report.mutation_result.score:
                    log_progress(f"Mutation score: {results.report.mutation_result.score.score:.0%}")
            except Exception as e:
                results.errors.append(f"Mutation testing failed: {e}")

        # Run property testing
        if self.property_tester and self.config.enable_property:
            log_progress("Running property testing...")
            try:
                if not function_names:
                    function_names = self._extract_function_names(code)

                for func_name in function_names[:3]:
                    prop_result = self.property_tester.run_property_testing(
                        code=code,
                        function_name=func_name
                    )
                    if results.report.property_result is None:
                        results.report.property_result = prop_result
                    else:
                        results.report.property_result.violations.extend(prop_result.violations)
                        results.report.property_result.properties_tested.extend(prop_result.properties_tested)
                        results.report.property_result.total_inputs_generated += prop_result.total_inputs_generated

                if results.report.property_result:
                    log_progress(f"Property testing: {len(results.report.property_result.violations)} violations")
            except Exception as e:
                results.errors.append(f"Property testing failed: {e}")

    def _extract_function_names(self, code: str) -> List[str]:
        """Extract function names from code using AST."""
        import ast

        try:
            tree = ast.parse(code)
            functions = []
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    # Skip private and dunder methods
                    if not node.name.startswith('_'):
                        functions.append(node.name)
            return functions
        except SyntaxError:
            return []

    def run_quick_analysis(
        self,
        code: str,
        description: str = ""
    ) -> RedTeamResult:
        """
        Run quick red team analysis only.

        Useful for fast feedback during development.

        Args:
            code: Code to analyze
            description: Description of the code

        Returns:
            RedTeamResult
        """
        if not self.red_team_agent:
            self.red_team_agent = RedTeamAgent(model=self.config.model)

        return self.red_team_agent.analyze_code(code, description)


def run_adversarial_testing(
    code_path: Path,
    test_command: str = "",
    specification: str = "",
    mission_id: str = "default",
    model: ModelType = ModelType.CLAUDE_SONNET,
    progress_callback: Optional[Callable[[str], None]] = None
) -> AdversarialResults:
    """
    Convenience function to run adversarial testing.

    Args:
        code_path: Path to code to test
        test_command: Command to run existing tests
        specification: Original specification
        mission_id: Mission identifier
        model: Model to use
        progress_callback: Progress callback

    Returns:
        AdversarialResults
    """
    config = AdversarialConfig(
        mission_id=mission_id,
        model=model
    )
    runner = AdversarialRunner(config)
    return runner.run_full_suite(
        code_path=code_path,
        test_command=test_command,
        specification=specification,
        progress_callback=progress_callback
    )


if __name__ == "__main__":
    # Self-test
    print("Adversarial Runner - Self Test")
    print("=" * 50)

    # Create test file
    test_code = '''
def add(a, b):
    """Add two numbers."""
    return a + b

def divide(a, b):
    """Divide a by b."""
    return a / b  # Bug: no zero check

def get_user_data(user_id):
    """Get user data from database."""
    import os
    # Bug: SQL injection vulnerability
    query = f"SELECT * FROM users WHERE id = {user_id}"
    return query
'''

    test_spec = """
Requirements:
1. add(a, b) must return the sum of two numbers
2. divide(a, b) must safely handle division by zero
3. get_user_data must be secure against injection
"""

    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(test_code)
        test_file = Path(f.name)

    print(f"Testing file: {test_file}")
    print("\nRunning quick analysis (red team only)...")

    config = AdversarialConfig(
        mission_id="self_test",
        model=ModelType.CLAUDE_HAIKU,  # Use Haiku for speed
        enable_mutation=False,  # Skip mutation (needs test file)
        enable_property=False,  # Skip property (needs more setup)
        enable_parallel=False
    )

    runner = AdversarialRunner(config)

    # Quick analysis
    quick_result = runner.run_quick_analysis(test_code, "Utility functions")
    print(f"\nQuick analysis found {quick_result.total_issues} issues")

    # Full suite (without mutation and property for self-test)
    print("\nRunning blind validation...")
    results = runner.run_full_suite(
        code_path=test_file,
        specification=test_spec,
        progress_callback=lambda msg: print(f"  {msg}")
    )

    print(f"\nResults:")
    print(f"  Success: {results.success}")
    print(f"  Total duration: {results.total_duration_ms:.0f}ms")

    if results.report.epistemic_score:
        score = results.report.epistemic_score
        print(f"  Overall score: {score.overall_score:.0%}")
        print(f"  Rigor level: {score.rigor_level.value}")

        if score.recommendations:
            print("\n  Recommendations:")
            for rec in score.recommendations[:3]:
                print(f"    - {rec[:80]}...")

    # Cleanup
    test_file.unlink(missing_ok=True)

    print("\nAdversarial runner self-test complete!")
