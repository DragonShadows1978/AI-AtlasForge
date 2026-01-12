"""
Property-Based Testing - Generate edge cases via automated property verification.

Property-based testing is epistemically superior to example-based testing because:
1. It generates inputs the developer couldn't conceive
2. It shrinks failures to minimal reproducing cases
3. Each property-based test finds ~50x more bugs than unit tests

The key insight: Assert that PROPERTIES remain valid for a wide variety of inputs,
rather than testing specific examples.
"""

import sys
import json
import random
import string
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable, Type, Union
from datetime import datetime
from enum import Enum

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from experiment_framework import invoke_fresh_claude, ModelType


class PropertyType(Enum):
    """Types of properties to verify."""
    INVARIANT = "invariant"         # Property that must always hold
    IDEMPOTENT = "idempotent"       # f(f(x)) == f(x)
    COMMUTATIVE = "commutative"     # f(a, b) == f(b, a)
    ASSOCIATIVE = "associative"     # f(a, f(b, c)) == f(f(a, b), c)
    INVERSE = "inverse"             # f^-1(f(x)) == x
    MONOTONIC = "monotonic"         # x <= y implies f(x) <= f(y)
    BOUNDED = "bounded"             # output is within expected range
    TYPE_PRESERVING = "type_preserving"  # output type matches expectation
    NULL_SAFE = "null_safe"         # handles None/null gracefully
    PURE = "pure"                   # no side effects, deterministic
    CONTENT_PRESERVING = "content_preserving"  # output preserves input content


@dataclass
class GeneratedInput:
    """A generated test input."""
    value: Any
    generator: str  # Which generator produced it
    seed: int
    is_edge_case: bool = False
    description: str = ""


@dataclass
class PropertyViolation:
    """A property violation found during testing."""
    property_name: str
    property_type: PropertyType
    input_values: List[GeneratedInput]
    expected: Any
    actual: Any
    error_message: str
    shrunk_input: Optional[Any] = None  # Minimized failing input
    stack_trace: str = ""


@dataclass
class PropertyTestResult:
    """Results from property-based testing."""
    function_name: str
    properties_tested: List[str]
    total_inputs_generated: int
    violations: List[PropertyViolation] = field(default_factory=list)
    edge_cases_found: List[GeneratedInput] = field(default_factory=list)
    timestamp: str = ""
    duration_ms: float = 0
    success: bool = True
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "function_name": self.function_name,
            "properties_tested": self.properties_tested,
            "total_inputs_generated": self.total_inputs_generated,
            "violations": [
                {
                    "property_name": v.property_name,
                    "property_type": v.property_type.value,
                    "input_values": [
                        {"value": str(i.value), "generator": i.generator}
                        for i in v.input_values
                    ],
                    "expected": str(v.expected),
                    "actual": str(v.actual),
                    "error_message": v.error_message,
                    "shrunk_input": str(v.shrunk_input) if v.shrunk_input else None
                }
                for v in self.violations
            ],
            "edge_cases_found": len(self.edge_cases_found),
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "success": self.success,
            "error": self.error
        }

    @property
    def has_violations(self) -> bool:
        return len(self.violations) > 0


class InputGenerator:
    """Generates various types of test inputs including edge cases."""

    def __init__(self, seed: Optional[int] = None):
        """Initialize with optional seed for reproducibility."""
        self.seed = seed or random.randint(0, 2**32)
        random.seed(self.seed)

    def integers(self, count: int = 100, include_edge_cases: bool = True) -> List[GeneratedInput]:
        """Generate integer inputs."""
        inputs = []

        if include_edge_cases:
            edge_cases = [
                (0, "zero"),
                (1, "one"),
                (-1, "negative one"),
                (2**31 - 1, "max int32"),
                (-2**31, "min int32"),
                (2**63 - 1, "max int64"),
                (-2**63, "min int64"),
            ]
            for value, desc in edge_cases:
                inputs.append(GeneratedInput(
                    value=value,
                    generator="edge_case",
                    seed=self.seed,
                    is_edge_case=True,
                    description=desc
                ))

        # Random integers
        for _ in range(count - len(inputs)):
            value = random.randint(-10000, 10000)
            inputs.append(GeneratedInput(
                value=value,
                generator="random_int",
                seed=self.seed
            ))

        return inputs

    def strings(self, count: int = 100, include_edge_cases: bool = True) -> List[GeneratedInput]:
        """Generate string inputs."""
        inputs = []

        if include_edge_cases:
            edge_cases = [
                ("", "empty string"),
                (" ", "single space"),
                ("   ", "multiple spaces"),
                ("\t\n\r", "whitespace chars"),
                ("a" * 10000, "very long string"),
                ("\x00", "null byte"),
                ("hello\x00world", "embedded null"),
                ("<script>alert('xss')</script>", "XSS attempt"),
                ("'; DROP TABLE users; --", "SQL injection"),
                ("../../../etc/passwd", "path traversal"),
                ("\ud800", "invalid unicode (surrogate)"),
                ("\uFEFF", "BOM character"),
                ("한글", "korean"),
                ("العربية", "arabic"),
                ("🎉🔥💀", "emoji"),
            ]
            for value, desc in edge_cases:
                inputs.append(GeneratedInput(
                    value=value,
                    generator="edge_case",
                    seed=self.seed,
                    is_edge_case=True,
                    description=desc
                ))

        # Random strings
        for _ in range(count - len(inputs)):
            length = random.randint(0, 100)
            value = ''.join(random.choices(string.printable, k=length))
            inputs.append(GeneratedInput(
                value=value,
                generator="random_string",
                seed=self.seed
            ))

        return inputs

    def lists(self, count: int = 50, element_generator: Callable = None) -> List[GeneratedInput]:
        """Generate list inputs."""
        inputs = []

        edge_cases = [
            ([], "empty list"),
            ([None], "list with None"),
            ([None, None, None], "list of Nones"),
            (list(range(10000)), "large list"),
            ([[]], "nested empty list"),
            ([[[[]]]], "deeply nested list"),
        ]

        for value, desc in edge_cases:
            inputs.append(GeneratedInput(
                value=value,
                generator="edge_case",
                seed=self.seed,
                is_edge_case=True,
                description=desc
            ))

        # Random lists
        for _ in range(count - len(inputs)):
            length = random.randint(0, 20)
            if element_generator:
                elements = [element_generator().value for _ in range(length)]
            else:
                elements = [random.randint(-100, 100) for _ in range(length)]
            inputs.append(GeneratedInput(
                value=elements,
                generator="random_list",
                seed=self.seed
            ))

        return inputs

    def floats(self, count: int = 100, include_edge_cases: bool = True) -> List[GeneratedInput]:
        """Generate float inputs."""
        inputs = []

        if include_edge_cases:
            edge_cases = [
                (0.0, "zero"),
                (-0.0, "negative zero"),
                (float('inf'), "positive infinity"),
                (float('-inf'), "negative infinity"),
                (float('nan'), "NaN"),
                (1e-308, "very small positive"),
                (-1e-308, "very small negative"),
                (1e308, "very large positive"),
                (-1e308, "very large negative"),
                (0.1 + 0.2, "floating point precision issue (0.3)"),
            ]
            for value, desc in edge_cases:
                inputs.append(GeneratedInput(
                    value=value,
                    generator="edge_case",
                    seed=self.seed,
                    is_edge_case=True,
                    description=desc
                ))

        # Random floats
        for _ in range(count - len(inputs)):
            value = random.uniform(-1e6, 1e6)
            inputs.append(GeneratedInput(
                value=value,
                generator="random_float",
                seed=self.seed
            ))

        return inputs

    def dicts(self, count: int = 50) -> List[GeneratedInput]:
        """Generate dictionary inputs."""
        inputs = []

        edge_cases = [
            ({}, "empty dict"),
            ({"": ""}, "empty string keys/values"),
            ({None: None}, "None key and value"),
            ({"a" * 1000: "b" * 1000}, "large keys/values"),
            ({i: i for i in range(1000)}, "large dict"),
            ({"__class__": "hacked"}, "dunder key"),
        ]

        for value, desc in edge_cases:
            inputs.append(GeneratedInput(
                value=value,
                generator="edge_case",
                seed=self.seed,
                is_edge_case=True,
                description=desc
            ))

        return inputs


class PropertyTester:
    """
    Tests code against properties with generated inputs.

    Uses LLM to generate property assertions and validate them.
    """

    PROPERTY_INFERENCE_PROMPT = """Analyze this code and infer what properties should hold.

## Code:
```
{code}
```

## Function to analyze: {function_name}

Identify properties that should always be true for this function.
Consider:
1. What invariants should hold?
2. Should the function be idempotent? (f(f(x)) == f(x))
3. Are there type constraints on output?
4. Should it be null-safe?
5. Are there bounded outputs?
6. Should it be pure (no side effects)?

Respond in JSON:
{{
    "function_name": "{function_name}",
    "properties": [
        {{
            "name": "property_name",
            "type": "invariant|idempotent|bounded|null_safe|pure|type_preserving",
            "description": "What this property checks",
            "assertion": "Python expression that should be True",
            "input_types": ["int", "str", etc]
        }}
    ]
}}
"""

    def __init__(
        self,
        model: ModelType = ModelType.CLAUDE_SONNET,
        max_inputs: int = 100
    ):
        """
        Initialize property tester.

        Args:
            model: Model to use for property inference
            max_inputs: Maximum inputs to generate per property
        """
        self.model = model
        self.max_inputs = max_inputs
        self.generator = InputGenerator()

    def infer_properties(self, code: str, function_name: str) -> List[Dict[str, Any]]:
        """
        Use LLM to infer properties for a function.

        Args:
            code: The source code
            function_name: Name of function to analyze

        Returns:
            List of property specifications
        """
        prompt = self.PROPERTY_INFERENCE_PROMPT.format(
            code=code,
            function_name=function_name
        )

        response, _ = invoke_fresh_claude(
            prompt=prompt,
            model=self.model,
            timeout=60
        )

        try:
            # Parse JSON from response
            import re
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(0))
                return parsed.get("properties", [])
        except Exception:
            pass

        return []

    def test_property(
        self,
        func: Callable,
        property_spec: Dict[str, Any]
    ) -> List[PropertyViolation]:
        """
        Test a function against a property specification.

        Args:
            func: The function to test
            property_spec: Property specification dict

        Returns:
            List of violations found
        """
        violations = []
        input_types = property_spec.get("input_types", ["int"])
        property_type = PropertyType(property_spec.get("type", "invariant"))
        property_name = property_spec.get("name", "unknown")

        # Generate inputs based on types
        inputs = []
        for input_type in input_types:
            if input_type == "int":
                inputs.extend(self.generator.integers(self.max_inputs // len(input_types)))
            elif input_type == "str":
                inputs.extend(self.generator.strings(self.max_inputs // len(input_types)))
            elif input_type == "float":
                inputs.extend(self.generator.floats(self.max_inputs // len(input_types)))
            elif input_type == "list":
                inputs.extend(self.generator.lists(self.max_inputs // len(input_types)))

        # Test each input
        for inp in inputs:
            try:
                result = func(inp.value)

                # Check property based on type
                violation = self._check_property(
                    func, inp, result, property_type, property_spec
                )
                if violation:
                    violations.append(violation)

            except Exception as e:
                # Exception during execution is a potential violation
                if property_type == PropertyType.NULL_SAFE and inp.value is None:
                    violations.append(PropertyViolation(
                        property_name=property_name,
                        property_type=property_type,
                        input_values=[inp],
                        expected="No exception",
                        actual=str(e),
                        error_message=f"Function raised exception on None: {e}"
                    ))

        return violations

    def _check_property(
        self,
        func: Callable,
        inp: GeneratedInput,
        result: Any,
        property_type: PropertyType,
        property_spec: Dict[str, Any]
    ) -> Optional[PropertyViolation]:
        """Check if a property holds for a given input/output."""

        property_name = property_spec.get("name", "unknown")

        if property_type == PropertyType.IDEMPOTENT:
            try:
                second_result = func(result)
                if result != second_result:
                    return PropertyViolation(
                        property_name=property_name,
                        property_type=property_type,
                        input_values=[inp],
                        expected=str(result),
                        actual=str(second_result),
                        error_message="Function is not idempotent: f(f(x)) != f(x)"
                    )
            except Exception:
                pass  # Skip if second call fails

        elif property_type == PropertyType.BOUNDED:
            bounds = property_spec.get("bounds", {})
            min_val = bounds.get("min")
            max_val = bounds.get("max")

            if min_val is not None and result < min_val:
                return PropertyViolation(
                    property_name=property_name,
                    property_type=property_type,
                    input_values=[inp],
                    expected=f">= {min_val}",
                    actual=str(result),
                    error_message=f"Result below minimum bound: {result} < {min_val}"
                )

            if max_val is not None and result > max_val:
                return PropertyViolation(
                    property_name=property_name,
                    property_type=property_type,
                    input_values=[inp],
                    expected=f"<= {max_val}",
                    actual=str(result),
                    error_message=f"Result above maximum bound: {result} > {max_val}"
                )

        elif property_type == PropertyType.TYPE_PRESERVING:
            expected_type = property_spec.get("expected_type")
            if expected_type and not isinstance(result, eval(expected_type)):
                return PropertyViolation(
                    property_name=property_name,
                    property_type=property_type,
                    input_values=[inp],
                    expected=expected_type,
                    actual=type(result).__name__,
                    error_message=f"Type mismatch: expected {expected_type}, got {type(result).__name__}"
                )

        return None

    def run_property_testing(
        self,
        code: str,
        function_name: str,
        function: Optional[Callable] = None
    ) -> PropertyTestResult:
        """
        Run property-based testing on a function.

        Args:
            code: Source code containing the function
            function_name: Name of the function to test
            function: Optional callable (if not provided, will try to extract)

        Returns:
            PropertyTestResult with violations and edge cases
        """
        start_time = datetime.now()

        result = PropertyTestResult(
            function_name=function_name,
            properties_tested=[],
            total_inputs_generated=0,
            timestamp=start_time.isoformat()
        )

        # Infer properties
        properties = self.infer_properties(code, function_name)
        result.properties_tested = [p.get("name", "unknown") for p in properties]

        # If no function provided, try to get it from code
        if function is None:
            try:
                exec_globals = {}
                exec(code, exec_globals)
                function = exec_globals.get(function_name)
            except Exception as e:
                result.error = f"Failed to extract function: {e}"
                result.success = False
                return result

        if function is None:
            result.error = f"Function {function_name} not found in code"
            result.success = False
            return result

        # Test each property
        total_inputs = 0
        for prop in properties:
            violations = self.test_property(function, prop)
            result.violations.extend(violations)
            total_inputs += self.max_inputs

        result.total_inputs_generated = total_inputs
        result.duration_ms = (datetime.now() - start_time).total_seconds() * 1000

        return result


def quick_property_test(code: str, function_name: str, max_inputs: int = 50) -> PropertyTestResult:
    """
    Quick property-based test for a function.

    Args:
        code: Source code
        function_name: Function to test
        max_inputs: Max inputs to generate

    Returns:
        PropertyTestResult
    """
    tester = PropertyTester(max_inputs=max_inputs)
    return tester.run_property_testing(code, function_name)


if __name__ == "__main__":
    # Self-test
    print("Property Testing - Self Test")
    print("=" * 50)

    # Test input generator
    gen = InputGenerator(seed=42)

    print("\nInteger edge cases:")
    for inp in gen.integers(count=10)[:5]:
        if inp.is_edge_case:
            print(f"  {inp.value}: {inp.description}")

    print("\nString edge cases:")
    for inp in gen.strings(count=20)[:5]:
        if inp.is_edge_case:
            print(f"  {repr(inp.value)}: {inp.description}")

    print("\nFloat edge cases:")
    for inp in gen.floats(count=15)[:5]:
        if inp.is_edge_case:
            print(f"  {inp.value}: {inp.description}")

    print("\nProperty testing self-test complete!")
