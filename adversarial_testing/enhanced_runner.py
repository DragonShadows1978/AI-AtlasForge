"""
Enhanced Adversarial Runner - Production-ready runner with all Cycle 2 features.

Integrates:
1. Cost estimation before running
2. Quick vs Full mode support
3. Vulnerability pattern database
4. Resilient error handling
5. Progress reporting
6. Budget tracking
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
from enum import Enum

sys.path.insert(0, str(Path(__file__).parent.parent))

from experiment_framework import ModelType

from .adversarial_runner import (
    AdversarialRunner,
    AdversarialConfig,
    AdversarialResults
)
from .cost_estimator import (
    CostEstimator,
    CostEstimate,
    BudgetTracker,
    CostTier
)
from .vulnerability_database import (
    VulnerabilityDatabase,
    VulnerabilityCategory,
    record_red_team_findings
)
from .resilience import (
    ResilientRunner,
    ProgressTracker,
    RetryConfig,
    ErrorType
)
from .red_team_agent import RedTeamAgent, RedTeamResult


class AdversarialMode(Enum):
    """Testing modes with different cost/thoroughness tradeoffs."""
    QUICK = "quick"        # Fast feedback, red team only, Haiku
    STANDARD = "standard"  # Balanced, all components, Sonnet
    FULL = "full"          # Comprehensive, all components + extra passes, Sonnet
    THOROUGH = "thorough"  # Maximum rigor, all components, Opus


@dataclass
class EnhancedConfig(AdversarialConfig):
    """Extended configuration with Cycle 2 features."""
    # Mode
    mode: AdversarialMode = AdversarialMode.STANDARD

    # Budget control
    budget_limit: Optional[float] = None  # Max USD to spend
    require_budget_approval: bool = True  # Require user approval if over threshold
    approval_threshold: float = 0.10  # Ask for approval above this

    # Vulnerability database
    enable_vuln_db: bool = True
    vuln_db_path: Optional[Path] = None

    # Resilience
    enable_resilience: bool = True
    max_retries: int = 3

    # Enhanced red team (use historical patterns)
    use_historical_patterns: bool = True


@dataclass
class EnhancedResults(AdversarialResults):
    """Extended results with Cycle 2 additions."""
    cost_estimate: Optional[CostEstimate] = None
    actual_cost: Optional[Dict[str, Any]] = None
    patterns_recorded: List[str] = field(default_factory=list)
    resilience_summary: Optional[Dict[str, Any]] = None
    mode_used: str = ""

    def to_dict(self) -> dict:
        base = super().to_dict()
        base.update({
            "cost_estimate": self.cost_estimate.to_dict() if self.cost_estimate else None,
            "actual_cost": self.actual_cost,
            "patterns_recorded": self.patterns_recorded,
            "resilience_summary": self.resilience_summary,
            "mode_used": self.mode_used
        })
        return base


class EnhancedAdversarialRunner:
    """
    Production-ready adversarial testing runner.

    Features:
    - Cost estimation before running
    - Quick/Standard/Full/Thorough modes
    - Vulnerability pattern learning
    - Resilient error handling
    - Progress reporting

    Usage:
        runner = EnhancedAdversarialRunner(
            mode=AdversarialMode.STANDARD,
            budget_limit=1.00
        )

        # Get cost estimate first
        estimate = runner.estimate_cost(code_path)
        print(f"Estimated cost: ${estimate.total_estimated_cost:.4f}")

        # Run with approval
        results = runner.run(
            code_path=Path("my_code.py"),
            specification="Original requirements...",
            progress_callback=print
        )

        # Check results
        print(f"Score: {results.report.epistemic_score.overall_score:.0%}")
        print(f"Patterns learned: {len(results.patterns_recorded)}")
    """

    # Mode presets
    MODE_CONFIGS = {
        AdversarialMode.QUICK: {
            "model": ModelType.CLAUDE_HAIKU,
            "enable_red_team": True,
            "enable_mutation": False,
            "enable_property": False,
            "enable_blind_validation": False,
            "enable_parallel": False,
            "timeout_seconds": 120
        },
        AdversarialMode.STANDARD: {
            "model": ModelType.CLAUDE_SONNET,
            "enable_red_team": True,
            "enable_mutation": True,
            "enable_property": True,
            "enable_blind_validation": True,
            "enable_parallel": True,
            "timeout_seconds": 300
        },
        AdversarialMode.FULL: {
            "model": ModelType.CLAUDE_SONNET,
            "enable_red_team": True,
            "enable_mutation": True,
            "enable_property": True,
            "enable_blind_validation": True,
            "enable_parallel": True,
            "timeout_seconds": 600,
            "max_mutants": 50,
            "max_inputs": 100
        },
        AdversarialMode.THOROUGH: {
            "model": ModelType.CLAUDE_OPUS,
            "enable_red_team": True,
            "enable_mutation": True,
            "enable_property": True,
            "enable_blind_validation": True,
            "enable_parallel": True,
            "timeout_seconds": 900,
            "max_mutants": 100,
            "max_inputs": 200
        }
    }

    def __init__(
        self,
        config: Optional[EnhancedConfig] = None,
        mode: Optional[AdversarialMode] = None,
        budget_limit: Optional[float] = None,
        mission_id: str = "default"
    ):
        """
        Initialize enhanced runner.

        Args:
            config: Full configuration (overrides other params)
            mode: Testing mode (quick/standard/full/thorough)
            budget_limit: Maximum budget in USD
            mission_id: Mission identifier
        """
        if config:
            self.config = config
        else:
            # Build config from mode
            mode = mode or AdversarialMode.STANDARD
            mode_settings = self.MODE_CONFIGS[mode].copy()

            self.config = EnhancedConfig(
                mission_id=mission_id,
                mode=mode,
                budget_limit=budget_limit,
                **mode_settings
            )

        # Initialize components
        self.cost_estimator = CostEstimator()
        self.budget_tracker = BudgetTracker(budget_limit=self.config.budget_limit)

        if self.config.enable_vuln_db:
            self.vuln_db = VulnerabilityDatabase(
                db_path=self.config.vuln_db_path
            )
        else:
            self.vuln_db = None

        if self.config.enable_resilience:
            self.resilient_runner = ResilientRunner(
                retry_config=RetryConfig(max_retries=self.config.max_retries),
                progress_callback=None  # Set in run()
            )
        else:
            self.resilient_runner = None

        # Core runner
        self.runner = AdversarialRunner(self.config)

    def estimate_cost(
        self,
        code_path: Optional[Path] = None,
        code_text: Optional[str] = None,
        specification: str = ""
    ) -> CostEstimate:
        """
        Estimate cost before running.

        Returns CostEstimate with breakdown and warnings.
        """
        return self.cost_estimator.estimate_full_suite(
            code_path=code_path,
            code_text=code_text,
            specification=specification,
            model=self.config.model,
            enable_red_team=self.config.enable_red_team,
            enable_mutation=self.config.enable_mutation,
            enable_property=self.config.enable_property,
            enable_validation=self.config.enable_blind_validation,
            budget_limit=self.config.budget_limit
        )

    def compare_modes(
        self,
        code_path: Optional[Path] = None,
        code_text: Optional[str] = None,
        specification: str = ""
    ) -> Dict[str, CostEstimate]:
        """
        Compare costs across different modes.

        Returns dict with estimate for each mode.
        """
        estimates = {}

        for mode in AdversarialMode:
            mode_settings = self.MODE_CONFIGS[mode]
            estimates[mode.value] = self.cost_estimator.estimate_full_suite(
                code_path=code_path,
                code_text=code_text,
                specification=specification,
                model=mode_settings["model"],
                enable_red_team=mode_settings["enable_red_team"],
                enable_mutation=mode_settings["enable_mutation"],
                enable_property=mode_settings["enable_property"],
                enable_validation=mode_settings.get("enable_blind_validation", True)
            )

        return estimates

    def _get_enhanced_red_team_prompt(self) -> str:
        """Get enhanced prompt with historical vulnerability patterns."""
        if not self.vuln_db or not self.config.use_historical_patterns:
            return ""

        return self.vuln_db.generate_prompt_enhancement(max_patterns=5)

    def run(
        self,
        code_path: Path,
        specification: str = "",
        test_command: str = "",
        progress_callback: Optional[Callable[[str], None]] = None,
        skip_cost_check: bool = False
    ) -> EnhancedResults:
        """
        Run adversarial testing with all enhancements.

        Args:
            code_path: Path to code to test
            specification: Original specification
            test_command: Command for mutation testing
            progress_callback: Progress callback
            skip_cost_check: Skip budget/cost checking

        Returns:
            EnhancedResults with full report
        """
        start_time = datetime.now()
        code_path = Path(code_path)

        # Initialize results
        results = EnhancedResults(
            config=self.config,
            report=None,
            started_at=start_time.isoformat(),
            completed_at="",
            total_duration_ms=0,
            mode_used=self.config.mode.value
        )

        def log(msg: str):
            if progress_callback:
                progress_callback(msg)

        # Step 1: Cost estimation
        if not skip_cost_check:
            log("Estimating costs...")
            results.cost_estimate = self.estimate_cost(
                code_path=code_path,
                specification=specification
            )
            log(f"Estimated cost: ${results.cost_estimate.total_estimated_cost:.4f} ({results.cost_estimate.cost_tier.value})")

            # Check budget
            if self.config.budget_limit:
                if results.cost_estimate.total_estimated_cost > self.config.budget_limit:
                    log(f"WARNING: Estimated cost exceeds budget of ${self.config.budget_limit:.2f}")
                    # Continue but warn - in production, might want to abort

        # Step 2: Setup progress tracking
        if self.resilient_runner:
            self.resilient_runner.progress_callback = log

        # Step 3: Get enhanced red team prompt if available
        pattern_enhancement = self._get_enhanced_red_team_prompt()
        if pattern_enhancement:
            log(f"Enhanced with {self.vuln_db.stats['total_patterns']} historical patterns")

        # Step 4: Run the suite
        log(f"Running {self.config.mode.value} mode adversarial testing...")

        def run_suite():
            return self.runner.run_full_suite(
                code_path=code_path,
                test_command=test_command,
                specification=specification,
                progress_callback=log
            )

        if self.resilient_runner:
            base_results = self.resilient_runner.run_with_resilience(
                func=run_suite,
                component="adversarial_suite",
                timeout=self.config.timeout_seconds
            )
            results.resilience_summary = self.resilient_runner.get_error_summary()
        else:
            try:
                base_results = run_suite()
            except Exception as e:
                base_results = None
                results.errors.append(str(e))

        # Step 5: Process results
        if base_results:
            results.report = base_results.report
            results.success = base_results.success
            results.errors.extend(base_results.errors)

            # Step 6: Record findings in vulnerability database
            if self.vuln_db and results.report and results.report.red_team_result:
                rt_result = results.report.red_team_result
                if rt_result.findings:
                    log(f"Recording {len(rt_result.findings)} patterns to database...")
                    findings_data = [
                        {
                            "category": f.category.value if hasattr(f.category, 'value') else str(f.category),
                            "severity": f.severity,
                            "title": f.title,
                            "description": f.description,
                            "affected_code": f.affected_code,
                            "reproduction_steps": f.reproduction_steps,
                            "suggested_fix": f.suggested_fix
                        }
                        for f in rt_result.findings
                    ]
                    record_red_team_findings(
                        db=self.vuln_db,
                        mission_id=self.config.mission_id,
                        code_path=str(code_path),
                        findings=findings_data
                    )
                    results.patterns_recorded = [f.title for f in rt_result.findings]

        # Step 7: Record actual cost
        results.actual_cost = self.budget_tracker.to_dict()

        # Finalize
        end_time = datetime.now()
        results.completed_at = end_time.isoformat()
        results.total_duration_ms = (end_time - start_time).total_seconds() * 1000

        if results.report and results.report.epistemic_score:
            log(f"Complete. Score: {results.report.epistemic_score.overall_score:.0%} "
                f"({results.report.epistemic_score.rigor_level.value})")

        return results

    def run_quick(
        self,
        code_path: Path,
        description: str = "",
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> RedTeamResult:
        """
        Run quick red team analysis only.

        Fast feedback mode using Haiku, returns just red team results.
        """
        def log(msg: str):
            if progress_callback:
                progress_callback(msg)

        log("Running quick red team analysis...")

        # Create quick-mode agent
        agent = RedTeamAgent(
            model=ModelType.CLAUDE_HAIKU,
            timeout_seconds=120
        )

        # Get code
        code_path = Path(code_path)
        code = code_path.read_text()

        # Run analysis
        result = agent.analyze_code(
            code=code,
            description=description or f"Code from {code_path.name}"
        )

        log(f"Found {result.total_issues} issues "
            f"({len(result.critical_findings)} critical, {len(result.high_findings)} high)")

        return result


def run_adversarial_with_estimation(
    code_path: Path,
    specification: str = "",
    mode: AdversarialMode = AdversarialMode.STANDARD,
    budget_limit: Optional[float] = None,
    progress_callback: Optional[Callable[[str], None]] = None
) -> EnhancedResults:
    """
    Convenience function for running with cost estimation.

    Usage:
        results = run_adversarial_with_estimation(
            code_path=Path("my_code.py"),
            specification="Requirements...",
            mode=AdversarialMode.STANDARD,
            budget_limit=1.00,
            progress_callback=print
        )
    """
    runner = EnhancedAdversarialRunner(
        mode=mode,
        budget_limit=budget_limit
    )

    return runner.run(
        code_path=code_path,
        specification=specification,
        progress_callback=progress_callback
    )


if __name__ == "__main__":
    # Self-test
    print("Enhanced Adversarial Runner - Self Test")
    print("=" * 60)

    import tempfile

    # Create test file
    test_code = '''
def divide(a, b):
    """Divide a by b."""
    return a / b  # Bug: no zero check

def process_query(user_input):
    """Process user query."""
    import os
    os.system(f"echo {user_input}")  # Bug: command injection
'''

    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(test_code)
        test_file = Path(f.name)

    print(f"\nTest file: {test_file}")

    # Test cost comparison
    print("\n1. Cost Comparison Across Modes:")
    runner = EnhancedAdversarialRunner(mode=AdversarialMode.STANDARD)
    comparisons = runner.compare_modes(code_path=test_file)
    for mode_name, estimate in comparisons.items():
        print(f"   {mode_name}: ${estimate.total_estimated_cost:.4f}")

    # Test quick mode
    print("\n2. Quick Mode Analysis:")
    quick_result = runner.run_quick(
        code_path=test_file,
        progress_callback=lambda msg: print(f"   {msg}")
    )
    print(f"   Found {quick_result.total_issues} issues")

    # Test full run with mock (skip actual API calls)
    print("\n3. Enhanced Runner Configuration:")
    enhanced = EnhancedAdversarialRunner(
        mode=AdversarialMode.QUICK,
        budget_limit=0.10
    )
    print(f"   Mode: {enhanced.config.mode.value}")
    print(f"   Model: {enhanced.config.model.value}")
    print(f"   Budget: ${enhanced.config.budget_limit}")
    print(f"   Red team: {enhanced.config.enable_red_team}")
    print(f"   Mutation: {enhanced.config.enable_mutation}")
    print(f"   Property: {enhanced.config.enable_property}")
    print(f"   Validation: {enhanced.config.enable_blind_validation}")

    # Cleanup
    test_file.unlink(missing_ok=True)

    print("\nEnhanced runner self-test complete!")
