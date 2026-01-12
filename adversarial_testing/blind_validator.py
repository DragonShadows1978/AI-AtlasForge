"""
Blind Validator - Independent validation against original specification.

The key epistemic principle: A separate agent validates against the ORIGINAL spec
without any knowledge of implementation details. This catches "spec drift" -
when implementation diverges from what was originally requested.

The validator only knows:
1. The original specification/requirements
2. How to interact with the implementation (API/interface)

The validator does NOT know:
1. How the implementation works internally
2. What design decisions were made
3. What compromises or shortcuts were taken
"""

import sys
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from experiment_framework import invoke_fresh_claude, ModelType


class ValidationStatus(Enum):
    """Validation result status."""
    PASS = "pass"
    FAIL = "fail"
    PARTIAL = "partial"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


class RequirementType(Enum):
    """Types of requirements."""
    FUNCTIONAL = "functional"       # What the system should DO
    BEHAVIORAL = "behavioral"       # How the system should BEHAVE
    INTERFACE = "interface"         # API/interface requirements
    CONSTRAINT = "constraint"       # Limitations and boundaries
    QUALITY = "quality"            # Non-functional requirements
    SECURITY = "security"          # Security requirements


@dataclass
class RequirementCheck:
    """A single requirement validation check."""
    requirement_id: str
    requirement_text: str
    requirement_type: RequirementType
    status: ValidationStatus
    evidence: str  # What evidence supports the verdict
    gaps: List[str] = field(default_factory=list)  # What's missing
    confidence: float = 0.0  # 0.0 - 1.0


@dataclass
class ValidationResult:
    """Complete blind validation results."""
    spec_hash: str  # Hash of original spec for traceability
    implementation_hash: str  # Hash of implementation checked
    validator_model: str
    timestamp: str
    duration_ms: float
    requirements_checked: List[RequirementCheck] = field(default_factory=list)
    overall_status: ValidationStatus = ValidationStatus.INCONCLUSIVE
    spec_drift_detected: bool = False
    drift_severity: str = "none"  # none, low, medium, high, critical
    recommendations: List[str] = field(default_factory=list)
    raw_response: str = ""
    success: bool = True
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "spec_hash": self.spec_hash,
            "implementation_hash": self.implementation_hash,
            "validator_model": self.validator_model,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "requirements_checked": [
                {
                    "id": r.requirement_id,
                    "text": r.requirement_text,
                    "type": r.requirement_type.value,
                    "status": r.status.value,
                    "evidence": r.evidence,
                    "gaps": r.gaps,
                    "confidence": r.confidence
                }
                for r in self.requirements_checked
            ],
            "overall_status": self.overall_status.value,
            "spec_drift_detected": self.spec_drift_detected,
            "drift_severity": self.drift_severity,
            "recommendations": self.recommendations,
            "success": self.success,
            "error": self.error
        }

    @property
    def passed_requirements(self) -> List[RequirementCheck]:
        return [r for r in self.requirements_checked if r.status == ValidationStatus.PASS]

    @property
    def failed_requirements(self) -> List[RequirementCheck]:
        return [r for r in self.requirements_checked if r.status == ValidationStatus.FAIL]

    @property
    def pass_rate(self) -> float:
        if not self.requirements_checked:
            return 0.0
        return len(self.passed_requirements) / len(self.requirements_checked)


class BlindValidator:
    """
    Validates implementation against specification without implementation knowledge.

    The validator operates in complete isolation from the implementation details.
    It only sees:
    - The original specification
    - The implementation's external behavior (API, outputs, interface)

    This ensures validation is against what was REQUESTED, not what was BUILT.
    """

    # System prompt for blind validator - explicitly isolated from implementation
    VALIDATOR_SYSTEM_PROMPT = """You are a blind validator. Your job is to validate an implementation
against its ORIGINAL specification WITHOUT knowing how it was implemented.

You must be SKEPTICAL. Assume nothing. Verify everything against the spec.

Your validation must be:
1. SPECIFICATION-DRIVEN: Only validate what the spec actually requires
2. EVIDENCE-BASED: Cite specific behaviors that support your verdict
3. OBJECTIVE: No assumptions about "good intentions" or "reasonable interpretations"
4. THOROUGH: Check every stated requirement

When validating:
- PASS: Requirement is clearly met with evidence
- FAIL: Requirement is clearly not met
- PARTIAL: Requirement is partially met (note what's missing)
- INCONCLUSIVE: Cannot determine from available evidence

Be harsh. Be skeptical. The implementation must PROVE it meets the spec.
"""

    VALIDATION_PROMPT_TEMPLATE = """Validate this implementation against the original specification.

## ORIGINAL SPECIFICATION:
{specification}

## IMPLEMENTATION TO VALIDATE:
```
{implementation}
```

## INTERFACE/API DESCRIPTION:
{interface}

## Your Task:
For EACH requirement in the specification:
1. Extract the specific requirement
2. Check if the implementation meets it
3. Cite evidence for your verdict
4. Note any gaps or missing functionality

BE SKEPTICAL. The implementation must PROVE it meets the spec.

Respond in JSON:
{{
    "requirements": [
        {{
            "id": "REQ-1",
            "text": "The original requirement text",
            "type": "functional|behavioral|interface|constraint|quality|security",
            "status": "pass|fail|partial|inconclusive",
            "evidence": "What in the implementation supports this verdict",
            "gaps": ["What's missing or incomplete"],
            "confidence": 0.0-1.0
        }}
    ],
    "overall_status": "pass|fail|partial|inconclusive",
    "spec_drift_detected": true|false,
    "drift_severity": "none|low|medium|high|critical",
    "drift_description": "How the implementation differs from spec",
    "recommendations": ["Suggestions to align with spec"]
}}
"""

    def __init__(
        self,
        model: ModelType = ModelType.CLAUDE_SONNET,
        timeout_seconds: int = 180
    ):
        """
        Initialize blind validator.

        Args:
            model: Model to use for validation
            timeout_seconds: Timeout for validation
        """
        self.model = model
        self.timeout_seconds = timeout_seconds

    def validate(
        self,
        specification: str,
        implementation: str,
        interface_description: str = ""
    ) -> ValidationResult:
        """
        Validate implementation against specification.

        Args:
            specification: The original specification/requirements
            implementation: The implementation code or behavior description
            interface_description: Description of how to interact with implementation

        Returns:
            ValidationResult with detailed findings
        """
        import hashlib

        start_time = datetime.now()

        # Create hashes for traceability
        spec_hash = hashlib.md5(specification.encode()).hexdigest()[:12]
        impl_hash = hashlib.md5(implementation.encode()).hexdigest()[:12]

        result = ValidationResult(
            spec_hash=spec_hash,
            implementation_hash=impl_hash,
            validator_model=self.model.value,
            timestamp=start_time.isoformat(),
            duration_ms=0
        )

        # Build validation prompt
        prompt = self.VALIDATION_PROMPT_TEMPLATE.format(
            specification=specification,
            implementation=implementation,
            interface=interface_description or "Standard API/function interface"
        )

        # Invoke fresh Claude instance for blind validation
        response, duration_ms = invoke_fresh_claude(
            prompt=prompt,
            model=self.model,
            system_prompt=self.VALIDATOR_SYSTEM_PROMPT,
            timeout=self.timeout_seconds
        )

        result.duration_ms = duration_ms
        result.raw_response = response

        if response.startswith("ERROR:"):
            result.success = False
            result.error = response
            result.overall_status = ValidationStatus.ERROR
            return result

        # Parse response
        try:
            parsed = self._extract_json(response)
            if parsed:
                # Extract requirements
                for req_data in parsed.get("requirements", []):
                    req = RequirementCheck(
                        requirement_id=req_data.get("id", "unknown"),
                        requirement_text=req_data.get("text", ""),
                        requirement_type=RequirementType(req_data.get("type", "functional")),
                        status=ValidationStatus(req_data.get("status", "inconclusive")),
                        evidence=req_data.get("evidence", ""),
                        gaps=req_data.get("gaps", []),
                        confidence=float(req_data.get("confidence", 0.5))
                    )
                    result.requirements_checked.append(req)

                # Overall status
                result.overall_status = ValidationStatus(
                    parsed.get("overall_status", "inconclusive")
                )

                # Spec drift
                result.spec_drift_detected = parsed.get("spec_drift_detected", False)
                result.drift_severity = parsed.get("drift_severity", "none")

                # Recommendations
                result.recommendations = parsed.get("recommendations", [])

        except Exception as e:
            result.error = f"Failed to parse response: {e}"
            result.overall_status = ValidationStatus.INCONCLUSIVE

        return result

    def validate_against_file(
        self,
        spec_path: Path,
        implementation_path: Path
    ) -> ValidationResult:
        """
        Validate implementation file against specification file.

        Args:
            spec_path: Path to specification file
            implementation_path: Path to implementation file

        Returns:
            ValidationResult
        """
        spec_path = Path(spec_path)
        implementation_path = Path(implementation_path)

        if not spec_path.exists():
            return ValidationResult(
                spec_hash="",
                implementation_hash="",
                validator_model=self.model.value,
                timestamp=datetime.now().isoformat(),
                duration_ms=0,
                success=False,
                error=f"Specification file not found: {spec_path}",
                overall_status=ValidationStatus.ERROR
            )

        if not implementation_path.exists():
            return ValidationResult(
                spec_hash="",
                implementation_hash="",
                validator_model=self.model.value,
                timestamp=datetime.now().isoformat(),
                duration_ms=0,
                success=False,
                error=f"Implementation file not found: {implementation_path}",
                overall_status=ValidationStatus.ERROR
            )

        spec = spec_path.read_text()
        implementation = implementation_path.read_text()

        return self.validate(spec, implementation)

    def extract_requirements(self, specification: str) -> List[Dict[str, str]]:
        """
        Extract individual requirements from a specification.

        Args:
            specification: The specification text

        Returns:
            List of requirement dicts
        """
        extraction_prompt = f"""Extract all individual requirements from this specification.

## Specification:
{specification}

For each requirement, identify:
1. A unique ID (REQ-1, REQ-2, etc.)
2. The exact requirement text
3. The type (functional, behavioral, interface, constraint, quality, security)

Respond in JSON:
{{
    "requirements": [
        {{
            "id": "REQ-1",
            "text": "The exact requirement",
            "type": "functional"
        }}
    ]
}}
"""

        response, _ = invoke_fresh_claude(
            prompt=extraction_prompt,
            model=self.model,
            timeout=60
        )

        try:
            parsed = self._extract_json(response)
            if parsed:
                return parsed.get("requirements", [])
        except Exception:
            pass

        return []

    def _extract_json(self, text: str) -> Optional[dict]:
        """Extract JSON from response text."""
        import re

        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON in code blocks
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find raw JSON object
        json_match = re.search(r'\{[^{}]*"requirements".*?\}', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        return None


def validate_implementation(
    specification: str,
    implementation: str,
    model: ModelType = ModelType.CLAUDE_SONNET
) -> ValidationResult:
    """
    Convenience function to validate implementation against specification.

    Args:
        specification: Original requirements
        implementation: Implementation to validate
        model: Model to use

    Returns:
        ValidationResult
    """
    validator = BlindValidator(model=model)
    return validator.validate(specification, implementation)


if __name__ == "__main__":
    # Self-test
    print("Blind Validator - Self Test")
    print("=" * 50)

    test_spec = """
## Requirements for Calculator Module

1. MUST provide an `add(a, b)` function that returns the sum of two numbers
2. MUST provide a `divide(a, b)` function that returns a/b
3. MUST handle division by zero gracefully (return None or raise ValueError)
4. MUST support both integers and floats
5. All functions MUST be pure (no side effects)
"""

    test_impl = '''
def add(a, b):
    """Add two numbers."""
    return a + b

def divide(a, b):
    """Divide a by b."""
    return a / b  # NOTE: Does NOT handle division by zero!

def multiply(a, b):
    """Multiply two numbers."""
    return a * b
'''

    print("Specification:")
    print(test_spec[:200] + "...")
    print("\nImplementation has a deliberate bug (no div-by-zero handling)")
    print("\nRunning blind validation...")

    validator = BlindValidator(model=ModelType.CLAUDE_HAIKU)  # Use Haiku for speed
    result = validator.validate(test_spec, test_impl)

    print(f"\nOverall Status: {result.overall_status.value}")
    print(f"Spec Drift Detected: {result.spec_drift_detected}")
    print(f"Pass Rate: {result.pass_rate:.1%}")

    print("\nRequirement Checks:")
    for req in result.requirements_checked:
        status_icon = "✓" if req.status == ValidationStatus.PASS else "✗"
        print(f"  {status_icon} [{req.status.value}] {req.requirement_id}: {req.requirement_text[:50]}...")

    print("\nBlind validator self-test complete!")
