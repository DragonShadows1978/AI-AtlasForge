"""
Red Team Agent - Spawns fresh LLM instances to adversarially test code.

The key insight: The same entity that builds cannot objectively test.
This module spawns FRESH LLM instances with NO memory of implementation
details, giving them a truly adversarial perspective.
"""

import json
import logging
import os
import ssl
import string
import sys
import threading
import time
import urllib.request
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dashboard IPC: emit red team lifecycle events to the Mission Activity window
# ---------------------------------------------------------------------------
try:
    _DASHBOARD_PORT = int(os.environ.get('ATLASFORGE_PORT', '5010'))
except (ValueError, TypeError):
    _DASHBOARD_PORT = 5010
# Bug 5 fix: whitelist-validate IPC scheme to prevent URL scheme injection
# Cycle 2 Fix: wrap in try/except so invalid env var doesn't raise at import time.
# An invalid ATLASFORGE_IPC_SCHEME would previously cause a module-level ValueError,
# making the entire adversarial_testing pipeline unimportable (DoS). Fall back to
# 'https' and emit a warning instead of crashing.
_ALLOWED_IPC_SCHEMES = frozenset({'http', 'https'})
_raw_ipc_scheme = os.environ.get('ATLASFORGE_IPC_SCHEME', 'https').strip().lower()
if _raw_ipc_scheme not in _ALLOWED_IPC_SCHEMES:
    logger.warning(
        "Invalid ATLASFORGE_IPC_SCHEME %r (must be 'http' or 'https'); "
        "falling back to 'https'", _raw_ipc_scheme
    )
    _raw_ipc_scheme = 'https'
_IPC_SCHEME = _raw_ipc_scheme
_IPC_URL = f'{_IPC_SCHEME}://localhost:{_DASHBOARD_PORT}/api/internal/agent-event'
# RT-3: SSL context scoped to dashboard IPC only (self-signed localhost cert)
# Do NOT reuse for external connections -- verification is intentionally disabled.
_DASHBOARD_SSL_CTX = ssl.create_default_context()
_DASHBOARD_SSL_CTX.check_hostname = False
_DASHBOARD_SSL_CTX.verify_mode = ssl.CERT_NONE


def _emit_red_team_event(payload: dict) -> None:
    """Fire-and-forget IPC emission to dashboard Mission Activity window.

    Runs in a daemon thread -- never blocks the red team, never raises.
    Bug 4 fix: validates host is localhost before using no-verify SSL context.
    """
    def _post():
        try:
            # Bug 4 fix: only use no-verify SSL for localhost/loopback targets
            from urllib.parse import urlparse
            _parsed = urlparse(_IPC_URL)
            _host = _parsed.hostname or ''
            _is_local = _host in ('localhost', '127.0.0.1', '::1', '')
            if not _is_local:
                import logging as _log
                _log.getLogger(__name__).warning(
                    "Dashboard IPC target %r is not localhost; skipping no-verify SSL", _host
                )
                return
            body = json.dumps({'room': 'mission_agents', 'payload': payload}).encode()
            req = urllib.request.Request(
                _IPC_URL, data=body,
                headers={'Content-Type': 'application/json'}, method='POST'
            )
            ctx = _DASHBOARD_SSL_CTX if _IPC_SCHEME == 'https' else None
            urllib.request.urlopen(req, timeout=3, context=ctx).close()
        except Exception as e:
            logger.debug("Dashboard IPC failed: %s", e)
    threading.Thread(target=_post, daemon=True).start()

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from experiment_framework import (
    invoke_fresh_llm,
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
    stop_reasons: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        # Cycle 2 7b: asdict() already converts enums to values, so no post-processing needed
        return asdict(self)

    @property
    def critical_findings(self) -> List[RedTeamFinding]:
        return [f for f in self.findings if f.severity == "critical"]

    @property
    def high_findings(self) -> List[RedTeamFinding]:
        return [f for f in self.findings if f.severity == "high"]

    @property
    def total_issues(self) -> int:
        return len(self.findings)


def _find_balanced_json(text: str) -> Optional[str]:
    """O(N) single-pass brace-counting to find the first balanced JSON object.

    Walks the string once, tracking brace depth and respecting string literals
    and escape sequences. Returns the substring from the opening '{' to the
    matching '}', or None if no balanced object is found.

    Ported from atlasforge_conductor.py:_find_balanced_json (Cycle 2).
    """
    if not isinstance(text, str):
        return None
    start = text.find('{')
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False
    i = start
    while i < len(text):
        ch = text[i]
        if escape_next:
            escape_next = False
        elif ch == '\\' and in_string:
            escape_next = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return None


def _find_all_balanced_json(text: str):
    """O(N) scan returning all top-level balanced JSON object substrings.

    Unlike _find_balanced_json which stops at the first candidate, this
    function continues scanning after each balanced block so that callers
    can try all candidates and pick the best-matching one (e.g. the first
    that parses successfully and contains a required key).

    Returns a list of (start, end, substring) tuples in left-to-right order.
    """
    if not isinstance(text, str):
        return []
    candidates = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != '{':
            i += 1
            continue
        # Scan forward for the matching closing brace
        start = i
        depth = 0
        in_string = False
        escape_next = False
        j = i
        while j < n:
            ch = text[j]
            if escape_next:
                escape_next = False
            elif ch == '\\' and in_string:
                escape_next = True
            elif ch == '"':
                in_string = not in_string
            elif not in_string:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        candidates.append((start, j + 1, text[start:j + 1]))
                        break
            j += 1
        # Advance past the whole balanced object so nested objects inside an
        # already-captured candidate are not returned as separate top-level JSON.
        i = j + 1 if depth == 0 else start + 1
    return candidates


class RedTeamAgent:
    """
    Spawns fresh LLM instances to adversarially analyze code.

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

    # Iter 3 Fix M10: use string.Template ($code) instead of str.format({code})
    # to avoid needing brace escaping that garbles user code containing { }
    ANALYSIS_PROMPT_TEMPLATE = string.Template("""Analyze this code for vulnerabilities and bugs.
I need you to BREAK this code. Find edge cases, bugs, and security issues.

## Code to Analyze:
```
$code
```

## Functional Description:
$description

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
{
    "findings": [
        {
            "category": "boundary",
            "severity": "high",
            "title": "Array index out of bounds",
            "description": "The function doesn't check array length before accessing index",
            "reproduction_steps": ["Call function with empty array", "Observe crash"],
            "affected_code": "process_items:15",
            "suggested_fix": "Add length check before access",
            "confidence": 0.95
        }
    ],
    "attack_vectors_tried": ["empty input", "null values", "huge arrays", "negative indices"]
}
""")

    def __init__(
        self,
        model: ModelType = ModelType.BALANCED,
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
        Spawn a fresh LLM instance to adversarially analyze code.

        Args:
            code: The code to analyze
            description: Brief functional description of what the code does
            session_id: Optional session identifier

        Returns:
            RedTeamResult with findings
        """
        # Cycle 2 Fix 4.1: guard against None or empty code
        if code is None or not isinstance(code, str):
            raise TypeError(f"code must be a non-empty string, got {type(code).__name__}")
        if not code.strip():
            raise ValueError("code must not be empty or whitespace-only")

        if session_id is None:
            session_id = f"rt_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        agent_id = f"red_team_{session_id}"
        start_time = time.time()
        line_count = 0

        # Emit: agent spawned — creates the Red Team tab in Mission Activity
        _emit_red_team_event({
            'event': 'agent_spawned',
            'agent_id': agent_id,
            'label': 'Red Team',
            'timestamp': datetime.now().isoformat()
        })

        # Emit: starting analysis
        _emit_red_team_event({
            'event': 'agent_stream_line',
            'agent_id': agent_id,
            'label': 'Red Team',
            'event_type': 'thinking',
            'text': f'Invoking fresh LLM adversarial analysis ({len(code)} chars of code)...',
            'timestamp': datetime.now().isoformat()
        })
        line_count += 1

        # Iter 3 Fix M10: use safe_substitute — no brace escaping needed
        prompt = self.ANALYSIS_PROMPT_TEMPLATE.safe_substitute(
            code=code,
            description=description
        )

        # Invoke fresh LLM instance (blocking — 20-120s)
        response, duration_ms = invoke_fresh_llm(
            prompt=prompt,
            model=self.model,
            system_prompt=self.RED_TEAM_SYSTEM_PROMPT,
            timeout=self.timeout_seconds
        )

        # Emit: response received
        _emit_red_team_event({
            'event': 'agent_stream_line',
            'agent_id': agent_id,
            'label': 'Red Team',
            'event_type': 'thinking',
            'text': f'Response received in {duration_ms:.0f}ms, parsing findings...',
            'timestamp': datetime.now().isoformat()
        })
        line_count += 1

        # RT1: guard None before constructing result
        if response is None:
            response = ""

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
            # Cycle 2 Fix 4.2: sanitize control characters before logging
            import re as _re
            _sanitized = _re.sub(r'[\x00-\x1f\x7f-\x9f]', '', response[:200])
            logger.warning(
                "Red team analysis failed (duration=%.0fms): %s", duration_ms, _sanitized
            )
            result.success = False
            result.error = response
            # Emit: error state
            _emit_red_team_event({
                'event': 'agent_error',
                'agent_id': agent_id,
                'label': 'Red Team',
                'error': response[:200],
                'timestamp': datetime.now().isoformat()
            })
            _emit_red_team_event({
                'event': 'agent_complete',
                'agent_id': agent_id,
                'label': 'Red Team',
                'duration_seconds': round(time.time() - start_time, 1),
                'line_count': line_count,
                'timestamp': datetime.now().isoformat()
            })
            return result

        # Parse findings from JSON response
        try:
            # Try to extract JSON from response
            parsed = self._extract_json(response)
            if parsed:
                for finding_data in parsed.get("findings", []):
                    try:
                        finding = RedTeamFinding(
                            category=AttackCategory(finding_data.get("category", "logic")),
                            severity=finding_data.get("severity") or "medium",
                            title=finding_data.get("title", "Unknown issue"),
                            description=finding_data.get("description", ""),
                            reproduction_steps=(lambda rs: [rs] if isinstance(rs, str) else rs if isinstance(rs, list) else [])(finding_data.get("reproduction_steps", [])),
                            affected_code=finding_data.get("affected_code", "unknown"),
                            suggested_fix=finding_data.get("suggested_fix", ""),
                            confidence=max(0.0, min(1.0, float(finding_data.get("confidence", 0.5))))
                        )
                    except (ValueError, KeyError, TypeError):
                        # Invalid AttackCategory or other field error - fall back to LOGIC_FLAW
                        # Safe confidence read: malformed confidence must not re-raise
                        try:
                            safe_confidence = max(0.0, min(1.0, float(finding_data.get("confidence", 0.5))))
                        except (ValueError, TypeError):
                            safe_confidence = 0.5
                        finding = RedTeamFinding(
                            category=AttackCategory.LOGIC_FLAW,
                            severity=finding_data.get("severity") or "medium",
                            title=finding_data.get("title", "Unknown issue"),
                            description=finding_data.get("description", ""),
                            reproduction_steps=(lambda rs: [rs] if isinstance(rs, str) else rs if isinstance(rs, list) else [])(finding_data.get("reproduction_steps", [])),
                            affected_code=finding_data.get("affected_code", "unknown"),
                            suggested_fix=finding_data.get("suggested_fix", ""),
                            confidence=safe_confidence
                        )
                    result.findings.append(finding)

                result.attack_vectors_tried = parsed.get("attack_vectors_tried", [])
        except Exception as e:
            result.error = f"Failed to parse response: {e}"
            # Preserve any findings already appended inside the loop.
            # Only mark failure if no findings were recovered at all.
            if not result.findings:
                result.success = False

        # Emit: findings summary
        critical_count = len([f for f in result.findings if f.severity == "critical"])
        high_count = len([f for f in result.findings if f.severity == "high"])
        summary_text = (
            f'Analysis complete: {result.total_issues} issue(s) found '
            f'({critical_count} critical, {high_count} high)'
        )
        _emit_red_team_event({
            'event': 'agent_stream_line',
            'agent_id': agent_id,
            'label': 'Red Team',
            'event_type': 'tool_result',
            'text': summary_text,
            'timestamp': datetime.now().isoformat()
        })
        line_count += 1

        # Emit: individual findings sorted by severity (critical first)
        _SEVERITY_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'info': 4}
        for finding in sorted(result.findings, key=lambda f: _SEVERITY_ORDER.get(f.severity or "medium", 5)):
            sev = (finding.severity or "medium").upper()
            cat = finding.category.value if hasattr(finding.category, 'value') else str(finding.category)
            evt_type = 'error' if finding.severity in ('critical', 'high') else 'tool_result'
            _emit_red_team_event({
                'event': 'agent_stream_line',
                'agent_id': agent_id,
                'label': 'Red Team',
                'event_type': evt_type,
                'text': f'[{sev}] {cat}: {finding.title} — {finding.affected_code}',
                'timestamp': datetime.now().isoformat()
            })
            line_count += 1

        # Emit: complete
        _emit_red_team_event({
            'event': 'agent_complete',
            'agent_id': agent_id,
            'label': 'Red Team',
            'duration_seconds': round(time.time() - start_time, 1),
            'line_count': line_count,
            'timestamp': datetime.now().isoformat()
        })

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

        try:
            code = file_path.read_text()
        except UnicodeDecodeError:
            return RedTeamResult(
                session_id=f"rt_error_{datetime.now().strftime('%H%M%S')}",
                code_analyzed="",
                agent_model=self.model.value,
                timestamp=datetime.now().isoformat(),
                duration_ms=0,
                success=False,
                error=f"Binary or non-UTF-8 file cannot be analyzed: {file_path}"
            )

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

    def analyze_workspace(
        self,
        workspace_dir: Path,
        mission_desc: str = "",
        n_agents: int = 3,
        timeout: int = 300,
    ) -> RedTeamResult:
        """
        Launch parallel blind agents against a workspace directory.

        This is the preferred method for workspace-based red teaming.
        Agents are real Claude CLI subprocesses — they explore the codebase
        themselves using file tools instead of receiving pasted code.

        Args:
            workspace_dir: Directory containing the codebase to attack
            mission_desc: Brief description of what the code does
            n_agents: Number of parallel blind agents (1-4)
            timeout: Per-agent timeout in seconds

        Returns:
            RedTeamResult with aggregated findings from all agents
        """
        from .blind_agent_runner import RedTeamOrchestrator

        orchestrator = RedTeamOrchestrator(timeout=timeout, n_agents=n_agents)
        return orchestrator.launch(
            workspace_dir=Path(workspace_dir),
            mission_desc=mission_desc or f"Codebase at {workspace_dir}",
            n_agents=n_agents,
            timeout=timeout,
        )

    def _extract_json(self, text: str) -> Optional[dict]:
        """Extract JSON from response text."""
        # RT-2: guard against None input
        if text is None:
            return None
        # Guard against non-string types (int, list, etc.)
        if not isinstance(text, str):
            return None
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

        # O(N) scan for all balanced brace blocks; try each until one parses
        # and contains the required "findings" key.  This prevents stopping
        # at the first block (which may be a non-JSON fragment like {literal})
        # when a later block contains the actual JSON payload.
        for _start, _end, json_str in _find_all_balanced_json(text):
            try:
                obj = json.loads(json_str)
                if isinstance(obj, dict) and "findings" in obj:
                    return obj
            except json.JSONDecodeError:
                continue

        return None


def analyze_workspace(
    workspace_dir: Path,
    mission_desc: str = "",
    n_agents: int = 3,
    timeout: int = 300,
) -> RedTeamResult:
    """
    Launch a blind agent red team against a workspace directory.

    This is the preferred red team method for workspace-based testing.
    Spawns real Claude CLI subprocesses (not single LLM calls), each with
    full tool access to explore the codebase, run tests, and break things.
    Each agent appears as a tab in the Mission Activity dashboard.

    Args:
        workspace_dir: Path to the workspace/codebase to attack
        mission_desc: Brief description of what the code does
        n_agents: Number of parallel agents (1-4)
        timeout: Per-agent timeout in seconds

    Returns:
        RedTeamResult with aggregated findings from all agents
    """
    from .blind_agent_runner import RedTeamOrchestrator
    orchestrator = RedTeamOrchestrator(timeout=timeout, n_agents=n_agents)
    return orchestrator.launch(
        workspace_dir=Path(workspace_dir),
        mission_desc=mission_desc or "Explore and determine from code",
        n_agents=n_agents,
        timeout=timeout,
    )


def run_red_team_analysis(
    code: str,
    description: str = "",
    model: ModelType = ModelType.BALANCED
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
    import subprocess
    subprocess.run(['echo', user_input], check=False)
'''

    print("Analyzing vulnerable test code...")
    agent = RedTeamAgent(model=ModelType.FAST)  # Use fast tier for quick test
    result = agent.analyze_code(
        code=test_code,
        description="Utility functions for a web application"
    )

    print(f"\nFindings: {result.total_issues}")
    print(f"Critical: {len(result.critical_findings)}")
    print(f"High: {len(result.high_findings)}")
    print(f"Duration: {result.duration_ms:.0f}ms")

    for finding in result.findings:
        print(f"\n[{(finding.severity or 'medium').upper()}] {finding.title}")
        print(f"  Category: {finding.category.value}")
        print(f"  Confidence: {finding.confidence:.0%}")

    print("\nRed team self-test complete!")
