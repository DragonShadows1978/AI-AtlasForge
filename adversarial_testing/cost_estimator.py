"""
Cost Estimator - Estimate API costs before running adversarial testing.

This module helps prevent runaway costs by:
1. Estimating API calls before running
2. Providing budget limits
3. Tracking actual costs during execution
"""

from dataclasses import dataclass, field
from typing import Dict, Optional
from enum import Enum
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from experiment_framework import ModelType


class CostTier(Enum):
    """Cost tier categories."""
    CHEAP = "cheap"      # < $0.10
    MODERATE = "moderate"  # $0.10 - $1.00
    EXPENSIVE = "expensive"  # $1.00 - $10.00
    VERY_EXPENSIVE = "very_expensive"  # > $10.00


@dataclass
class ModelPricing:
    """Pricing info for a model (per 1K tokens)."""
    input_price: float  # USD per 1K input tokens
    output_price: float  # USD per 1K output tokens
    name: str


# Approximate pricing as of late 2024 (USD per 1K tokens)
MODEL_PRICING: Dict[ModelType, ModelPricing] = {
    ModelType.CLAUDE_HAIKU: ModelPricing(
        input_price=0.00025,
        output_price=0.00125,
        name="Claude Haiku"
    ),
    ModelType.CLAUDE_SONNET: ModelPricing(
        input_price=0.003,
        output_price=0.015,
        name="Claude Sonnet"
    ),
    ModelType.CLAUDE_OPUS: ModelPricing(
        input_price=0.015,
        output_price=0.075,
        name="Claude Opus"
    ),
    ModelType.MINI_MIND: ModelPricing(
        input_price=0.0,
        output_price=0.0,
        name="Mini Mind (Local Free)"
    ),
}


@dataclass
class ComponentEstimate:
    """Estimated cost for a single component."""
    component_name: str
    estimated_calls: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost: float
    model: ModelType

    def __str__(self) -> str:
        return (f"{self.component_name}: ~{self.estimated_calls} calls, "
                f"~{self.estimated_input_tokens + self.estimated_output_tokens} tokens, "
                f"${self.estimated_cost:.4f}")


@dataclass
class CostEstimate:
    """Complete cost estimate for an adversarial testing run."""
    components: Dict[str, ComponentEstimate] = field(default_factory=dict)
    total_estimated_cost: float = 0.0
    total_estimated_calls: int = 0
    total_estimated_tokens: int = 0
    cost_tier: CostTier = CostTier.CHEAP
    warnings: list = field(default_factory=list)

    def add_component(self, estimate: ComponentEstimate):
        """Add a component estimate."""
        self.components[estimate.component_name] = estimate
        self.total_estimated_cost += estimate.estimated_cost
        self.total_estimated_calls += estimate.estimated_calls
        self.total_estimated_tokens += (
            estimate.estimated_input_tokens + estimate.estimated_output_tokens
        )
        self._update_tier()

    def _update_tier(self):
        """Update cost tier based on total."""
        if self.total_estimated_cost < 0.10:
            self.cost_tier = CostTier.CHEAP
        elif self.total_estimated_cost < 1.00:
            self.cost_tier = CostTier.MODERATE
        elif self.total_estimated_cost < 10.00:
            self.cost_tier = CostTier.EXPENSIVE
        else:
            self.cost_tier = CostTier.VERY_EXPENSIVE

    def to_dict(self) -> dict:
        return {
            "components": {
                name: {
                    "estimated_calls": c.estimated_calls,
                    "estimated_input_tokens": c.estimated_input_tokens,
                    "estimated_output_tokens": c.estimated_output_tokens,
                    "estimated_cost": c.estimated_cost,
                    "model": c.model.value
                }
                for name, c in self.components.items()
            },
            "totals": {
                "estimated_cost": self.total_estimated_cost,
                "estimated_calls": self.total_estimated_calls,
                "estimated_tokens": self.total_estimated_tokens,
                "cost_tier": self.cost_tier.value
            },
            "warnings": self.warnings
        }

    def __str__(self) -> str:
        lines = ["=" * 50]
        lines.append("COST ESTIMATE")
        lines.append("=" * 50)

        for name, comp in self.components.items():
            lines.append(f"  {comp}")

        lines.append("-" * 50)
        lines.append(f"TOTAL: ${self.total_estimated_cost:.4f} ({self.cost_tier.value})")
        lines.append(f"       ~{self.total_estimated_calls} API calls")
        lines.append(f"       ~{self.total_estimated_tokens:,} tokens")

        if self.warnings:
            lines.append("\nWARNINGS:")
            for warning in self.warnings:
                lines.append(f"  - {warning}")

        return "\n".join(lines)


class CostEstimator:
    """
    Estimates API costs before running adversarial testing.

    Usage:
        estimator = CostEstimator()
        estimate = estimator.estimate_full_suite(
            code_path=Path("my_code.py"),
            config=adversarial_config
        )
        print(estimate)

        if estimate.total_estimated_cost > budget:
            print("Over budget! Consider using quick mode.")
    """

    # Average token counts based on typical usage
    AVG_CODE_TOKENS = 2000  # Average code file
    AVG_PROMPT_TOKENS = 500  # Average prompt overhead
    AVG_RESPONSE_TOKENS = 1500  # Average response length

    # Component-specific estimates
    RED_TEAM_CALLS = 1  # One call per analysis
    MUTATION_CALLS_PER_MUTANT = 0  # Mutation testing doesn't use API
    PROPERTY_CALLS = 1  # One call to generate inputs
    VALIDATION_CALLS = 1  # One call for validation

    def __init__(self, pricing: Optional[Dict[ModelType, ModelPricing]] = None):
        """Initialize with optional custom pricing."""
        self.pricing = pricing or MODEL_PRICING

    def _get_pricing(self, model: ModelType) -> ModelPricing:
        """Get pricing for a model."""
        return self.pricing.get(model, MODEL_PRICING[ModelType.CLAUDE_SONNET])

    def _estimate_cost(
        self,
        model: ModelType,
        input_tokens: int,
        output_tokens: int
    ) -> float:
        """Calculate cost for given token counts."""
        pricing = self._get_pricing(model)
        return (
            (input_tokens / 1000) * pricing.input_price +
            (output_tokens / 1000) * pricing.output_price
        )

    def estimate_red_team(
        self,
        code_size_tokens: int,
        model: ModelType
    ) -> ComponentEstimate:
        """Estimate cost for red team analysis."""
        input_tokens = code_size_tokens + self.AVG_PROMPT_TOKENS
        output_tokens = self.AVG_RESPONSE_TOKENS

        return ComponentEstimate(
            component_name="Red Team Analysis",
            estimated_calls=self.RED_TEAM_CALLS,
            estimated_input_tokens=input_tokens,
            estimated_output_tokens=output_tokens,
            estimated_cost=self._estimate_cost(model, input_tokens, output_tokens),
            model=model
        )

    def estimate_mutation_testing(
        self,
        num_mutants: int,
        model: ModelType
    ) -> ComponentEstimate:
        """Estimate cost for mutation testing (currently no API calls)."""
        # Mutation testing runs locally, no API cost
        return ComponentEstimate(
            component_name="Mutation Testing",
            estimated_calls=0,
            estimated_input_tokens=0,
            estimated_output_tokens=0,
            estimated_cost=0.0,
            model=model
        )

    def estimate_property_testing(
        self,
        num_functions: int,
        model: ModelType
    ) -> ComponentEstimate:
        """Estimate cost for property testing."""
        # One call per function to generate inputs
        calls = num_functions
        input_tokens = self.AVG_PROMPT_TOKENS * calls
        output_tokens = self.AVG_RESPONSE_TOKENS * calls

        return ComponentEstimate(
            component_name="Property Testing",
            estimated_calls=calls,
            estimated_input_tokens=input_tokens,
            estimated_output_tokens=output_tokens,
            estimated_cost=self._estimate_cost(model, input_tokens, output_tokens),
            model=model
        )

    def estimate_blind_validation(
        self,
        code_size_tokens: int,
        spec_size_tokens: int,
        model: ModelType
    ) -> ComponentEstimate:
        """Estimate cost for blind validation."""
        input_tokens = code_size_tokens + spec_size_tokens + self.AVG_PROMPT_TOKENS
        output_tokens = self.AVG_RESPONSE_TOKENS

        return ComponentEstimate(
            component_name="Blind Validation",
            estimated_calls=self.VALIDATION_CALLS,
            estimated_input_tokens=input_tokens,
            estimated_output_tokens=output_tokens,
            estimated_cost=self._estimate_cost(model, input_tokens, output_tokens),
            model=model
        )

    def estimate_tokens_from_text(self, text: str) -> int:
        """Rough estimate of tokens from text (4 chars per token average)."""
        return len(text) // 4

    def estimate_full_suite(
        self,
        code_path: Optional[Path] = None,
        code_text: Optional[str] = None,
        specification: str = "",
        model: ModelType = ModelType.CLAUDE_SONNET,
        enable_red_team: bool = True,
        enable_mutation: bool = True,
        enable_property: bool = True,
        enable_validation: bool = True,
        num_functions: int = 3,
        max_mutants: int = 30,
        budget_limit: Optional[float] = None
    ) -> CostEstimate:
        """
        Estimate full adversarial testing suite cost.

        Args:
            code_path: Path to code file (optional)
            code_text: Code text directly (optional)
            specification: Original specification
            model: Model to use
            enable_red_team: Whether red team is enabled
            enable_mutation: Whether mutation testing is enabled
            enable_property: Whether property testing is enabled
            enable_validation: Whether blind validation is enabled
            num_functions: Number of functions to property test
            max_mutants: Maximum mutants to generate
            budget_limit: Optional budget limit for warnings

        Returns:
            CostEstimate with breakdown
        """
        estimate = CostEstimate()

        # Get code size
        if code_path and code_path.exists():
            code_text = code_path.read_text()

        code_size = self.estimate_tokens_from_text(code_text or "")
        spec_size = self.estimate_tokens_from_text(specification)

        # Estimate each component
        if enable_red_team:
            red_team_model = model
            estimate.add_component(
                self.estimate_red_team(code_size, red_team_model)
            )

        if enable_mutation:
            estimate.add_component(
                self.estimate_mutation_testing(max_mutants, model)
            )

        if enable_property:
            property_model = model
            estimate.add_component(
                self.estimate_property_testing(num_functions, property_model)
            )

        if enable_validation:
            validation_model = model
            estimate.add_component(
                self.estimate_blind_validation(code_size, spec_size, validation_model)
            )

        # Add warnings
        if estimate.cost_tier == CostTier.EXPENSIVE:
            estimate.warnings.append(
                "This run is estimated to cost more than $1.00. "
                "Consider using quick mode or a cheaper model."
            )
        elif estimate.cost_tier == CostTier.VERY_EXPENSIVE:
            estimate.warnings.append(
                "WARNING: This run is estimated to cost more than $10.00! "
                "Consider using quick mode with Haiku."
            )

        if budget_limit and estimate.total_estimated_cost > budget_limit:
            estimate.warnings.append(
                f"BUDGET EXCEEDED: Estimated ${estimate.total_estimated_cost:.2f} "
                f"> budget ${budget_limit:.2f}"
            )

        if code_size > 10000:
            estimate.warnings.append(
                f"Large codebase detected ({code_size:,} tokens). "
                "Consider running on specific files instead of entire codebase."
            )

        return estimate

    def estimate_quick_mode(
        self,
        code_path: Optional[Path] = None,
        code_text: Optional[str] = None
    ) -> CostEstimate:
        """
        Estimate cost for quick mode (Haiku, red team only).

        Quick mode is designed for fast feedback during development.
        """
        return self.estimate_full_suite(
            code_path=code_path,
            code_text=code_text,
            model=ModelType.CLAUDE_HAIKU,
            enable_red_team=True,
            enable_mutation=False,
            enable_property=False,
            enable_validation=False
        )

    def compare_modes(
        self,
        code_path: Optional[Path] = None,
        code_text: Optional[str] = None,
        specification: str = ""
    ) -> Dict[str, CostEstimate]:
        """
        Compare costs between quick and full modes.

        Returns dict with 'quick' and 'full' estimates.
        """
        quick = self.estimate_quick_mode(code_path, code_text)

        full = self.estimate_full_suite(
            code_path=code_path,
            code_text=code_text,
            specification=specification,
            model=ModelType.CLAUDE_SONNET
        )

        return {
            "quick": quick,
            "full": full
        }


@dataclass
class BudgetTracker:
    """
    Tracks actual costs during execution.

    Usage:
        tracker = BudgetTracker(budget_limit=1.00)

        # Before each API call
        if tracker.can_spend(estimated_cost):
            result = make_api_call()
            tracker.record_spend(actual_cost, "red_team")
        else:
            print("Budget exhausted!")
    """
    budget_limit: Optional[float] = None
    spent: float = 0.0
    calls_made: int = 0
    tokens_used: int = 0
    spending_by_component: Dict[str, float] = field(default_factory=dict)

    @property
    def remaining(self) -> Optional[float]:
        """Get remaining budget, or None if unlimited."""
        if self.budget_limit is None:
            return None
        return max(0, self.budget_limit - self.spent)

    @property
    def is_exhausted(self) -> bool:
        """Check if budget is exhausted."""
        if self.budget_limit is None:
            return False
        return self.spent >= self.budget_limit

    def can_spend(self, amount: float) -> bool:
        """Check if we can spend the given amount."""
        if self.budget_limit is None:
            return True
        return (self.spent + amount) <= self.budget_limit

    def record_spend(
        self,
        amount: float,
        component: str,
        tokens: int = 0
    ):
        """Record a spend."""
        self.spent += amount
        self.calls_made += 1
        self.tokens_used += tokens

        if component not in self.spending_by_component:
            self.spending_by_component[component] = 0
        self.spending_by_component[component] += amount

    def to_dict(self) -> dict:
        return {
            "budget_limit": self.budget_limit,
            "spent": self.spent,
            "remaining": self.remaining,
            "calls_made": self.calls_made,
            "tokens_used": self.tokens_used,
            "by_component": self.spending_by_component
        }

    def __str__(self) -> str:
        lines = ["Budget Tracker:"]
        if self.budget_limit:
            lines.append(f"  Budget: ${self.budget_limit:.2f}")
            lines.append(f"  Spent: ${self.spent:.4f}")
            lines.append(f"  Remaining: ${self.remaining:.4f}")
        else:
            lines.append(f"  Budget: Unlimited")
            lines.append(f"  Spent: ${self.spent:.4f}")
        lines.append(f"  Calls: {self.calls_made}")
        lines.append(f"  Tokens: {self.tokens_used:,}")
        return "\n".join(lines)


if __name__ == "__main__":
    # Self-test
    print("Cost Estimator - Self Test")
    print("=" * 50)

    estimator = CostEstimator()

    # Test with sample code
    sample_code = '''
def process_data(data):
    """Process incoming data."""
    results = []
    for item in data:
        if item > 0:
            results.append(item * 2)
    return results

def validate_input(user_input):
    """Validate user input."""
    if not user_input:
        raise ValueError("Empty input")
    return user_input.strip()
'''

    print("\n1. Quick Mode Estimate:")
    quick_estimate = estimator.estimate_quick_mode(code_text=sample_code)
    print(quick_estimate)

    print("\n2. Full Mode Estimate:")
    full_estimate = estimator.estimate_full_suite(
        code_text=sample_code,
        specification="Process data and validate input",
        model=ModelType.CLAUDE_SONNET
    )
    print(full_estimate)

    print("\n3. Mode Comparison:")
    comparison = estimator.compare_modes(
        code_text=sample_code,
        specification="Process data and validate input"
    )
    print(f"Quick: ${comparison['quick'].total_estimated_cost:.4f}")
    print(f"Full:  ${comparison['full'].total_estimated_cost:.4f}")
    print(f"Savings: ${comparison['full'].total_estimated_cost - comparison['quick'].total_estimated_cost:.4f}")

    print("\n4. Budget Tracker:")
    tracker = BudgetTracker(budget_limit=0.05)
    tracker.record_spend(0.01, "red_team", 1000)
    tracker.record_spend(0.02, "validation", 2000)
    print(tracker)
    print(f"Can spend $0.03? {tracker.can_spend(0.03)}")

    print("\nCost estimator self-test complete!")
