"""
Red Team Agent - Spawns fresh Claude instances to adversarially test code.

The key insight: The same entity that builds cannot objectively test.
This module spawns FRESH Claude instances with NO memory of implementation
details, giving them a truly adversarial perspective.
"""

import json
import sys
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from experiment_framework import (
    invoke_fresh_claude,
    ModelType,
    ExperimentConfig,
    Experiment
)


class AttackCategory(Enum):
    """Categories of adversarial attacks."""
    BOUNDARY_TESTING = "boundary"       # Edge cases, limits, boundaries
    TYPE_CONFUSION = "type_confusion"   # Wrong types, coercion failures
    STATE_CORRUPTION = "state_corruption"  # Invalid states, race conditions
    RESOURCE_EXHAUSTION = "resource"    # Memory, CPU, file handles
    INJECTION = "injection"             # SQL, command, path injection
    LOGIC_FLAW = "logic"                # Business logic errors
    CONCURRENCY = "concurrency"         # Threading, async issues
    ERROR_HANDLING = "error_handling"   # Exception handling gaps
    CONTENT_LOSS = "content_loss"       # Data destruction through transformation


@dataclass
class RedTeamFinding:
    """A single finding from red team analysis."""
    category: AttackCategory
    severity: str  # "critical", "high", "medium", "low", "info"
    title: str
    description: str
    reproduction_steps: List[str]
    affected_code: str  # File:line or function name
    suggested_fix: str
    confidence: float  # 0.0 - 1.0


@dataclass
class RedTeamResult:
    """Complete results from a red team session."""
    session_id: str
    code_analyzed: str
    agent_model: str
    timestamp: str
    duration_ms: float
    findings: List[RedTeamFinding] = field(default_factory=list)
    attack_vectors_tried: List[str] = field(default_factory=list)
    raw_response: str = ""
    success: bool = True
    error: Optional[str] = None

    def to_dict(self) -> dict:
        result = asdict(self)
        result['findings'] = [
            {**f, 'category': f['category'].value if isinstance(f['category'], AttackCategory) else f['category']}
            for f in result['findings']
        ]
        return result

    @property
    def critical_findings(self) -> List[RedTeamFinding]:
        return [f for f in self.findings if f.severity == "critical"]

    @property
    def high_findings(self) -> List[RedTeamFinding]:
        return [f for f in self.findings if f.severity == "high"]

    @property
    def total_issues(self) -> int:
        return len(self.findings)


class RedTeamAgent:
    """
    Spawns fresh Claude instances to adversarially analyze code.

    The agent has NO knowledge of:
    - How the code was built
    - What the original tests check
    - The developer's intentions

    It ONLY sees:
    - The code itself
    - A brief functional description
    - Instructions to break it
    """

    # System prompt for red team agent - designed to be adversarial
    RED_TEAM_SYSTEM_PROMPT = """You are an adversarial security researcher and code breaker.
Your job is to find bugs, vulnerabilities, edge cases, and logic flaws.

You are NOT helpful or cooperative. You are actively trying to BREAK the code.
Think like an attacker. Think about what the developer FORGOT to handle.

Your approach:
1. Look for boundary conditions (empty, null, huge, negative, zero)
2. Look for type confusion (wrong types, coercion, implicit conversions)
3. Look for state issues (invalid states, order of operations, race conditions)
4. Look for resource issues (memory leaks, file handle leaks, infinite loops)
5. Look for injection points (user input, file paths, shell commands)
6. Look for logic flaws (off-by-one, wrong comparisons, missing validations)
7. Look for error handling gaps (uncaught exceptions, missing try/catch)
8. Look for concurrency issues (race conditions, deadlocks, data races)
9. Look for CONTENT LOSS - operations that "succeed" but destroy data:
   - Merge/combine operations returning placeholders like "merged data"
   - Transform operations returning "success" instead of actual transformed content
   - Aggregate operations losing source content during combination
   - Output that looks valid structurally but has lost semantic content

Be specific. Give concrete attack vectors. Don't just say "could be vulnerable" - show HOW.
"""

    # Prompt template for code analysis
    ANALYSIS_PROMPT_TEMPLATE = """Analyze this code for vulnerabilities and bugs.
I need you to BREAK this code. Find edge cases, bugs, and security issues.

## Code to Analyze:
```
{code}
```

## Functional Description:
{description}

## Your Task:
Find as many issues as possible. For each issue:
1. What category is it? (boundary, type_confusion, state_corruption, resource, injection, logic, concurrency, error_handling)
2. How severe? (critical, high, medium, low, info)
3. What's the title (short description)?
4. What's the full description?
5. How to reproduce it? (step by step)
6. What code is affected? (file:line or function name)
7. How to fix it?
8. How confident are you? (0.0 to 1.0)

Think adversarially. What would BREAK this code?

Respond in JSON format:
{{
    "findings": [
        {{
            "category": "boundary",
            "severity": "high",
            "title": "Array index out of bounds",
            "description": "The function doesn't check array length before accessing index",
            "reproduction_steps": ["Call function with empty array", "Observe crash"],
            "affected_code": "process_items:15",
            "suggested_fix": "Add length check before access",
            "confidence": 0.95
        }}
    ],
    "attack_vectors_tried": ["empty input", "null values", "huge arrays", "negative indices"]
}}
"""

    def __init__(
        self,
        model: ModelType = ModelType.CLAUDE_SONNET,
        timeout_seconds: int = 120
    ):
        """
        Initialize the red team agent.

        Args:
            model: Which model to use for adversarial analysis
            timeout_seconds: Timeout for each analysis
        """
        self.model = model
        self.timeout_seconds = timeout_seconds

    def analyze_code(
        self,
        code: str,
        description: str = "No description provided",
        session_id: Optional[str] = None
    ) -> RedTeamResult:
        """
        Spawn a fresh Claude instance to adversarially analyze code.

        Args:
            code: The code to analyze
            description: Brief functional description of what the code does
            session_id: Optional session identifier

        Returns:
            RedTeamResult with findings
        """
        if session_id is None:
            session_id = f"rt_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Build the analysis prompt
        prompt = self.ANALYSIS_PROMPT_TEMPLATE.format(
            code=code,
            description=description
        )

        # Invoke fresh Claude instance
        response, duration_ms = invoke_fresh_claude(
            prompt=prompt,
            model=self.model,
            system_prompt=self.RED_TEAM_SYSTEM_PROMPT,
            timeout=self.timeout_seconds
        )

        # Parse the response
        result = RedTeamResult(
            session_id=session_id,
            code_analyzed=code[:500] + "..." if len(code) > 500 else code,
            agent_model=self.model.value,
            timestamp=datetime.now().isoformat(),
            duration_ms=duration_ms,
            raw_response=response
        )

        if response.startswith("ERROR:"):
            result.success = False
            result.error = response
            return result

        # Parse findings from JSON response
        try:
            # Try to extract JSON from response
            parsed = self._extract_json(response)
            if parsed:
                for finding_data in parsed.get("findings", []):
                    finding = RedTeamFinding(
                        category=AttackCategory(finding_data.get("category", "logic")),
                        severity=finding_data.get("severity", "medium"),
                        title=finding_data.get("title", "Unknown issue"),
                        description=finding_data.get("description", ""),
                        reproduction_steps=finding_data.get("reproduction_steps", []),
                        affected_code=finding_data.get("affected_code", "unknown"),
                        suggested_fix=finding_data.get("suggested_fix", ""),
                        confidence=float(finding_data.get("confidence", 0.5))
                    )
                    result.findings.append(finding)

                result.attack_vectors_tried = parsed.get("attack_vectors_tried", [])
        except Exception as e:
            result.error = f"Failed to parse response: {e}"
            # Still mark as success if we got a response, just with parsing issues
            result.success = True

        return result

    def analyze_file(
        self,
        file_path: Path,
        description: str = ""
    ) -> RedTeamResult:
        """
        Analyze a file by reading it and spawning red team analysis.

        Args:
            file_path: Path to the file to analyze
            description: Optional description of the file's purpose

        Returns:
            RedTeamResult with findings
        """
        file_path = Path(file_path)
        if not file_path.exists():
            return RedTeamResult(
                session_id=f"rt_error_{datetime.now().strftime('%H%M%S')}",
                code_analyzed="",
                agent_model=self.model.value,
                timestamp=datetime.now().isoformat(),
                duration_ms=0,
                success=False,
                error=f"File not found: {file_path}"
            )

        code = file_path.read_text()

        if not description:
            description = f"Code from file: {file_path.name}"

        return self.analyze_code(
            code=code,
            description=description,
            session_id=f"rt_{file_path.stem}"
        )

    def run_targeted_attacks(
        self,
        code: str,
        attack_categories: List[AttackCategory],
        description: str = ""
    ) -> RedTeamResult:
        """
        Run targeted attacks in specific categories.

        Args:
            code: The code to analyze
            attack_categories: List of attack categories to focus on
            description: Description of the code

        Returns:
            RedTeamResult focused on specified categories
        """
        category_names = [c.value for c in attack_categories]
        focused_description = f"{description}\n\nFocus on these attack vectors: {', '.join(category_names)}"

        return self.analyze_code(
            code=code,
            description=focused_description
        )

    def _extract_json(self, text: str) -> Optional[dict]:
        """Extract JSON from response text."""
        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON in code blocks
        import re
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find raw JSON object
        json_match = re.search(r'\{[^{}]*"findings"[^{}]*\[.*?\][^{}]*\}', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        return None


def run_red_team_analysis(
    code: str,
    description: str = "",
    model: ModelType = ModelType.CLAUDE_SONNET
) -> RedTeamResult:
    """
    Convenience function to run red team analysis on code.

    Args:
        code: The code to analyze
        description: Description of what the code does
        model: Model to use for analysis

    Returns:
        RedTeamResult with findings
    """
    agent = RedTeamAgent(model=model)
    return agent.analyze_code(code, description)


if __name__ == "__main__":
    # Self-test
    print("Red Team Agent - Self Test")
    print("=" * 50)

    test_code = '''
def divide(a, b):
    """Divide a by b."""
    return a / b

def get_item(items, index):
    """Get item at index."""
    return items[index]

def process_user_input(user_input):
    """Process user input and execute."""
    import os
    os.system(f"echo {user_input}")
'''

    print("Analyzing vulnerable test code...")
    agent = RedTeamAgent(model=ModelType.CLAUDE_HAIKU)  # Use Haiku for fast test
    result = agent.analyze_code(
        code=test_code,
        description="Utility functions for a web application"
    )

    print(f"\nFindings: {result.total_issues}")
    print(f"Critical: {len(result.critical_findings)}")
    print(f"High: {len(result.high_findings)}")
    print(f"Duration: {result.duration_ms:.0f}ms")

    for finding in result.findings:
        print(f"\n[{finding.severity.upper()}] {finding.title}")
        print(f"  Category: {finding.category.value}")
        print(f"  Confidence: {finding.confidence:.0%}")

    print("\nRed team self-test complete!")
