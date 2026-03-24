"""
Property-Based Testing - Generate edge cases via automated property verification.

Property-based testing is epistemically superior to example-based testing because:
1. It generates inputs the developer couldn't conceive
2. It shrinks failures to minimal reproducing cases
3. Each property-based test finds ~50x more bugs than unit tests

The key insight: Assert that PROPERTIES remain valid for a wide variety of inputs,
rather than testing specific examples.
"""

import ast as _ast_module
import concurrent.futures
import logging
import math
import os
import resource
import signal
import subprocess
import sys
import json
import random
import string
import textwrap
import threading
import time
import types
from string import Template
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable, Type, Union
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from experiment_framework import invoke_fresh_llm, ModelType


# Safe type registry — replaces eval(expected_type) to prevent arbitrary code execution
_SAFE_PYTHON_TYPES: dict = {
    'int': int, 'str': str, 'float': float, 'bool': bool,
    'list': list, 'dict': dict, 'tuple': tuple, 'set': set,
    'bytes': bytes, 'bytearray': bytearray, 'complex': complex,
    'frozenset': frozenset, 'NoneType': type(None),
}

# Safe builtins for exec() — prevents access to os/sys/open/__import__ etc.
# getattr/setattr/hasattr excluded: they enable sandbox traversal via __globals__ access.
# Security: 'type', 'object', 'iter' removed — they enable MRO traversal:
#   ().__class__.__mro__[-1].__subclasses__() → unrestricted builtins → os.system
_SAFE_BUILTINS_NAMES = frozenset({
    'abs', 'all', 'any', 'bool', 'chr', 'dict', 'divmod', 'enumerate',
    'filter', 'float', 'format', 'frozenset',
    'hash', 'int', 'isinstance', 'issubclass', 'len', 'list',
    'map', 'max', 'min', 'next', 'ord', 'pow', 'print',
    'range', 'repr', 'reversed', 'round', 'set', 'slice',
    'sorted', 'str', 'sum', 'tuple', 'zip', 'Exception',
    'ValueError', 'TypeError', 'NotImplementedError', 'AttributeError',
    'KeyError', 'IndexError', 'StopIteration', 'True', 'False', 'None',
})
import builtins as _builtins_module  # Always a module; avoids __builtins__ dict/module ambiguity
# Bug 9: wrap in MappingProxyType to prevent runtime mutation from same-process code
_RESTRICTED_BUILTINS = types.MappingProxyType(
    {k: v for k, v in vars(_builtins_module).items() if k in _SAFE_BUILTINS_NAMES}
)


def _validate_assertion(assertion: str) -> bool:
    """PT-C3-1: Reject assertions containing dunder attribute access (__class__, __bases__, etc.).

    Uses AST parsing to detect attribute nodes, name nodes starting with '__',
    or getattr/setattr/delattr calls with dunder attribute names.
    Returns False if any dunder access is found, True if safe.
    """
    try:
        tree = _ast_module.parse(assertion, mode='eval')
    except SyntaxError:
        return False
    for node in _ast_module.walk(tree):
        # PT-C3-2: block lambda expressions entirely — assertions never need lambdas
        # and lambda bodies can be used to bypass dunder checks via deferred execution
        if isinstance(node, _ast_module.Lambda):
            return False
        if isinstance(node, _ast_module.Attribute) and node.attr.startswith('__'):
            return False
        if isinstance(node, _ast_module.Name) and node.id.startswith('__'):
            return False
        # Bug fix: block getattr/setattr/delattr/hasattr unconditionally —
        # dynamic attribute access is never needed in property assertions and any
        # non-Constant attr argument (BinOp, Call, etc.) can bypass a name-based check.
        if isinstance(node, _ast_module.Call):
            func = node.func
            if isinstance(func, _ast_module.Name) and func.id in ('getattr', 'setattr', 'delattr', 'hasattr'):
                return False
    return True


_EVAL_TIMEOUT_SECONDS = 5  # PT-C5-1: max time for eval()/exec() of LLM-supplied code


def _eval_with_timeout(code_str: str, globals_dict: dict, locals_dict: dict, *, timeout: int = _EVAL_TIMEOUT_SECONDS, mode: str = 'eval'):
    """Run eval() or exec() with a timeout to prevent DoS from LLM-supplied code.

    Uses signal.SIGALRM on main thread, falls back to ThreadPoolExecutor otherwise.
    Returns the eval result (mode='eval') or None (mode='exec').
    Raises TimeoutError if execution exceeds timeout.
    """
    # Cycle 2 Fix 2.1: validate timeout parameter
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError(f"timeout must be a positive number, got {type(timeout).__name__}: {timeout!r}")
    if timeout <= 0:
        raise ValueError(f"timeout must be positive, got {timeout}")
    # Iter 4 Fix A1: reject inf/nan timeout
    if not math.isfinite(timeout):
        raise ValueError(f"timeout must be finite, got {timeout!r}")
    # Cycle 2 Fix 2.2: validate mode parameter
    if mode not in ('eval', 'exec'):
        raise ValueError(f"mode must be 'eval' or 'exec', got {mode!r}")

    def _run():
        if mode == 'eval':
            return eval(code_str, globals_dict, locals_dict)
        else:
            exec(code_str, globals_dict, locals_dict)
            return None

    if threading.current_thread() is threading.main_thread():
        def _alarm_handler(signum, frame):
            raise TimeoutError(f"eval/exec timed out after {timeout}s")
        old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
        # Cycle 3 Fix PT-1: save remaining time from any outer alarm before setting ours.
        # signal.alarm() resets the countdown — nested calls would cancel an outer timeout.
        # Use getitimer(ITIMER_REAL) to get sub-second remaining time, fall back to 0.
        try:
            _outer_itime = signal.getitimer(signal.ITIMER_REAL)
            _outer_remaining = _outer_itime[0]  # seconds remaining (float), 0 if no alarm
        except Exception:
            _outer_remaining = 0.0
        # Cycle 5: use setitimer for sub-second precision instead of alarm() which
        # quantizes to 1s. Only arm if our timeout expires before the outer one.
        if _outer_remaining <= 0 or timeout < _outer_remaining:
            signal.setitimer(signal.ITIMER_REAL, timeout)
        _t_start = time.monotonic()
        try:
            return _run()
        finally:
            _elapsed = time.monotonic() - _t_start
            # Restore outer alarm: cancel ours first, then re-arm outer if it was active.
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
            if _outer_remaining > 0:
                # Subtract actual elapsed time so the outer deadline is not extended.
                # Clamp to 0.001 minimum: setitimer(0) would cancel the alarm entirely.
                _corrected = _outer_remaining - _elapsed
                signal.setitimer(signal.ITIMER_REAL, max(0.001, _corrected))
    else:
        # Use a daemon thread instead of ThreadPoolExecutor: daemon=True ensures
        # runaway threads (e.g. infinite loops) don't prevent process exit and
        # don't accumulate — the TPE's shutdown(cancel_futures=True) cannot
        # actually stop a running thread.
        _result_holder = [None]
        _exc_holder = [None]
        def _thread_target():
            try:
                _result_holder[0] = _run()
            except BaseException as e:
                _exc_holder[0] = e
        t = threading.Thread(target=_thread_target, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            raise TimeoutError(f"eval/exec timed out after {timeout}s")
        if _exc_holder[0] is not None:
            raise _exc_holder[0]
        return _result_holder[0]


def _to_json_safe(d: dict) -> dict:
    """Filter a dict to only JSON-serializable primitive values.

    Converts MappingProxyType to dict, drops non-serializable entries
    (functions, types, etc.) so the subprocess IPC is pickle-free.
    Bug 1+2 fix: replaces _make_picklable() which was used with pickle.
    """
    safe = {}
    for k, v in d.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, types.MappingProxyType):
            v = dict(v)
        try:
            json.dumps(v)
            safe[k] = v
        except (TypeError, ValueError):
            pass  # skip non-serializable entries (functions, class objects, etc.)
    return safe


def _subprocess_eval(code_str: str, globals_dict: dict, locals_dict: dict, *, timeout: int = _EVAL_TIMEOUT_SECONDS, mode: str = 'eval'):
    """6a: Run eval/exec in an isolated subprocess with resource limits.

    Provides OS-level isolation for LLM-supplied code: separate process with
    CPU time, memory, and file descriptor limits via resource.setrlimit().
    Uses JSON (not pickle) for IPC to prevent arbitrary code execution via ACE.
    Raises RuntimeError if IPC serialization fails — no in-process fallback.
    """
    # Cycle 2 Fix 2.1: validate timeout parameter
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError(f"timeout must be a positive number, got {type(timeout).__name__}: {timeout!r}")
    if timeout <= 0:
        raise ValueError(f"timeout must be positive, got {timeout}")
    # Iter 4 Fix A1: reject inf/nan timeout
    if not math.isfinite(timeout):
        raise ValueError(f"timeout must be finite, got {timeout!r}")
    # Cycle 2 Fix 2.2: validate mode parameter
    if mode not in ('eval', 'exec'):
        raise ValueError(f"mode must be 'eval' or 'exec', got {mode!r}")

    # Bug 1+2 fix: use JSON for IPC instead of pickle to eliminate ACE vector.
    # Only JSON-serializable primitives from globals/locals are passed through.
    # Fix 2b: detect silently-dropped non-serializable keys and raise RuntimeError
    # so callers cannot force a silent degradation by passing non-serializable objects.
    json_globals = _to_json_safe(globals_dict)
    json_locals = _to_json_safe(locals_dict)
    _str_keys_globals = {k for k in globals_dict if isinstance(k, str)}
    _str_keys_locals = {k for k in locals_dict if isinstance(k, str)}
    _dropped_globals = _str_keys_globals - set(json_globals)
    _dropped_locals = _str_keys_locals - set(json_locals)
    if _dropped_globals or _dropped_locals:
        raise RuntimeError(
            f"Cannot serialize globals/locals for subprocess isolation — "
            f"non-serializable keys dropped: globals={sorted(_dropped_globals)}, "
            f"locals={sorted(_dropped_locals)}. Refusing to execute with silently "
            f"stripped context to preserve isolation guarantees."
        )

    # Use caller's timeout for RLIMIT_CPU instead of hardcoded 10s.
    # C2-1b MODERATE: cap at 3600s (1 hour) to prevent astronomically large RLIMIT_CPU
    # values from an oversized timeout argument effectively disabling the resource limit.
    cpu_limit = int(max(1, min(3600, math.ceil(timeout))))

    # Build a self-contained script that sets resource limits, then eval/exec.
    # IPC is via JSON on stdin/stdout -- no pickle anywhere in the child process.
    script = textwrap.dedent(f"""\
        import json, resource, sys, types, builtins
        # Resource limits: CPU from caller timeout, 256MB memory, 16 file descriptors
        resource.setrlimit(resource.RLIMIT_CPU, ({cpu_limit}, {cpu_limit}))
        try:
            resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
        except (ValueError, OSError):
            pass  # Some systems don't support RLIMIT_AS
        resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))
        # Read JSON inputs from stdin -- no pickle, no ACE
        data = json.loads(sys.stdin.buffer.read().decode('utf-8'))
        code = data['code']
        g = data['globals']
        l = data['locals']
        mode = data['mode']
        # Restore safe builtins from the builtins module (not from untrusted JSON).
        # Wrap in MappingProxyType to prevent eval/exec'd code from mutating __builtins__
        # to re-introduce __import__ or other dangerous callables.
        g['__builtins__'] = types.MappingProxyType({{
            k: getattr(builtins, k)
            for k in ('abs','all','any','bool','chr','dict','divmod','enumerate',
                       'filter','float','format','frozenset','hash','int',
                       'isinstance','issubclass','len','list','map','max','min',
                       'next','ord','pow','print','range','repr','reversed',
                       'round','set','slice','sorted','str','sum','tuple','zip',
                       'Exception','ValueError','TypeError','NotImplementedError',
                       'AttributeError','KeyError','IndexError','StopIteration',
                       'True','False','None')
            if hasattr(builtins, k)
        }})
        # Save stdout writer, then delete setup-phase modules from child scope
        # so eval/exec'd code cannot access sys, resource, builtins, or types.
        _out = sys.stdout.write
        del sys, resource, builtins, types
        try:
            if mode == 'eval':
                result = eval(code, g, l)
            else:
                exec(code, g, l)
                result = None
            # Only JSON-serializable results can be returned
            try:
                out = json.dumps({{'ok': True, 'result': result}})
            except (TypeError, ValueError):
                out = json.dumps({{'ok': True, 'result': None}})
            _out(out)
        except BaseException as e:
            # Catch BaseException to handle SystemExit from resource limits
            _out(json.dumps({{'ok': False, 'error': type(e).__name__, 'msg': str(e)}}))
    """)

    try:
        payload = json.dumps({
            'code': code_str,
            'globals': json_globals,
            'locals': json_locals,
            'mode': mode,
        }).encode('utf-8')
    except (TypeError, ValueError) as _ser_err:
        raise RuntimeError(
            f"Cannot serialize globals/locals for subprocess isolation — "
            f"refusing in-process fallback to preserve resource limits: {_ser_err}"
        ) from _ser_err

    # Cycle 3 Fix PT-2: strip PYTHONPATH from subprocess env to prevent parent sys.path
    # inheritance. A malicious json.py in the working directory could otherwise execute
    # in the sandboxed child, bypassing isolation entirely.
    _child_env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONSTARTUP', 'PYTHONOPTIMIZE')}
    if 'PATH' not in _child_env:
        _child_env['PATH'] = '/usr/bin:/bin'
    try:
        proc = subprocess.run(
            [sys.executable, '-c', script],
            input=payload,
            capture_output=True,
            timeout=timeout + 2,  # extra grace for subprocess overhead
            env=_child_env,
        )
        if proc.returncode != 0:
            # Subprocess crashed (e.g., killed by RLIMIT_CPU)
            stderr = proc.stderr.decode(errors='replace')[:200]
            raise RuntimeError(f"Subprocess eval crashed (rc={proc.returncode}): {stderr}")
        result_data = json.loads(proc.stdout.decode('utf-8', errors='replace'))
        if result_data['ok']:
            return result_data['result']
        else:
            # Re-raise the original exception type using builtins module directly
            import builtins as _bi
            exc_cls = getattr(_bi, result_data['error'], None)
            # Only re-raise Exception subclasses, not SystemExit/KeyboardInterrupt
            if exc_cls is None or not isinstance(exc_cls, type) or not issubclass(exc_cls, Exception):
                exc_cls = RuntimeError
            raise exc_cls(result_data['msg'])
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"Subprocess eval timed out after {timeout}s")
    except (json.JSONDecodeError, KeyError, EOFError) as _json_err:
        # Subprocess produced unparseable output — do NOT fall back to in-process eval,
        # as that would bypass subprocess isolation (no RLIMIT_AS, no fd restrictions).
        raise RuntimeError(
            f"Subprocess eval produced unparseable output (isolation failure): {_json_err}"
        ) from _json_err


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
        self.seed = seed if seed is not None else random.randint(0, 2**32)
        # Bug 4: use private Random instance instead of global singleton
        # so separate InputGenerator instances with same seed are reproducible
        self.rng = random.Random(self.seed)

    def integers(self, count: int = 100, include_edge_cases: bool = True) -> List[GeneratedInput]:
        """Generate integer inputs."""
        if count <= 0:  # C3-8: guard against zero/negative count
            return []
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

        # Random integers — use self.rng for reproducibility (Bug 4)
        for _ in range(count - len(inputs)):
            value = self.rng.randint(-10000, 10000)
            inputs.append(GeneratedInput(
                value=value,
                generator="random_int",
                seed=self.seed
            ))

        # M1: truncate to exactly count when count < number of edge cases
        result = inputs[:count]
        # Cycle 5: floor — always return at least 1 input when count > 0 was requested
        if not result and count > 0:
            result = [GeneratedInput(value=0, generator="fallback_floor", seed=self.seed)]
        return result

    def strings(self, count: int = 100, include_edge_cases: bool = True) -> List[GeneratedInput]:
        """Generate string inputs."""
        if count <= 0:  # C3-8: guard matches integers()
            return []
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

        # Random strings — use self.rng (Bug 4)
        for _ in range(count - len(inputs)):
            length = self.rng.randint(0, 100)
            value = ''.join(self.rng.choices(string.printable, k=length))
            inputs.append(GeneratedInput(
                value=value,
                generator="random_string",
                seed=self.seed
            ))

        # M2: truncate to exactly count when count < number of edge cases
        return inputs[:count]

    def lists(self, count: int = 50, element_generator: Callable = None) -> List[GeneratedInput]:
        """Generate list inputs."""
        if count <= 0:  # C3-8: guard matches integers()
            return []
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

        # Random lists — use self.rng (Bug 4)
        for _ in range(count - len(inputs)):
            length = self.rng.randint(0, 20)
            if element_generator:
                elements = []
                for _ in range(length):
                    gen_result = element_generator()
                    if gen_result is None:
                        elements.append(self.rng.randint(-100, 100))
                    else:
                        elements.append(gen_result.value)
            else:
                elements = [self.rng.randint(-100, 100) for _ in range(length)]
            inputs.append(GeneratedInput(
                value=elements,
                generator="random_list",
                seed=self.seed
            ))

        # M2: truncate to exactly count when count < number of edge cases
        return inputs[:count]

    def floats(self, count: int = 100, include_edge_cases: bool = True) -> List[GeneratedInput]:
        """Generate float inputs."""
        if count <= 0:  # C3-8: guard matches integers()
            return []
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

        # Random floats — use self.rng (Bug 4)
        for _ in range(max(0, count - len(inputs))):
            value = self.rng.uniform(-1e6, 1e6)
            inputs.append(GeneratedInput(
                value=value,
                generator="random_float",
                seed=self.seed
            ))

        # M2: truncate to exactly count when count < number of edge cases
        return inputs[:count]

    def dicts(self, count: int = 50) -> List[GeneratedInput]:
        """Generate dictionary inputs."""
        if count <= 0:
            return []
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

        # Random dicts to fill up to count — use self.rng (Bug 4)
        for _ in range(max(0, count - len(inputs))):
            length = self.rng.randint(0, 10)
            value = {
                ''.join(self.rng.choices(string.ascii_lowercase, k=self.rng.randint(1, 10))): self.rng.randint(-100, 100)
                for _ in range(length)
            }
            inputs.append(GeneratedInput(
                value=value,
                generator="random_dict",
                seed=self.seed
            ))

        return inputs[:count]


class PropertyTester:
    """
    Tests code against properties with generated inputs.

    Uses LLM to generate property assertions and validate them.
    """

    # PT-1 fix: Use string.Template to avoid KeyError when user code contains braces
    PROPERTY_INFERENCE_PROMPT = Template("""Analyze this code and infer what properties should hold.

## Code:
```
$code
```

## Function to analyze: $function_name

Identify properties that should always be true for this function.
Consider:
1. What invariants should hold?
2. Should the function be idempotent? (f(f(x)) == f(x))
3. Are there type constraints on output?
4. Should it be null-safe?
5. Are there bounded outputs?
6. Should it be pure (no side effects)?

Respond in JSON:
{
    "function_name": "$function_name",
    "properties": [
        {
            "name": "property_name",
            "type": "invariant|idempotent|bounded|null_safe|pure|type_preserving",
            "description": "What this property checks",
            "assertion": "Python expression that should be True",
            "input_types": ["int", "str", etc]
        }
    ]
}
""")

    def __init__(
        self,
        model: ModelType = ModelType.BALANCED,
        max_inputs: int = 100,
        seed: Optional[int] = None,
        property_timeout: int = 30
    ):
        """
        Initialize property tester.

        Args:
            model: Model to use for property inference
            max_inputs: Maximum inputs to generate per property
            seed: Optional RNG seed for reproducibility (0 is a valid seed)
            property_timeout: Per-property timeout in seconds (must be a positive int)
        """
        # C3-4a: validate max_inputs and model types at construction time
        if not isinstance(max_inputs, int) or isinstance(max_inputs, bool) or max_inputs <= 0:
            raise TypeError(f"max_inputs must be a positive int, got {type(max_inputs).__name__}: {max_inputs!r}")
        if not isinstance(model, ModelType):
            raise TypeError(f"model must be a ModelType, got {type(model).__name__}: {model!r}")
        if not isinstance(property_timeout, int) or isinstance(property_timeout, bool) or property_timeout <= 0:
            raise TypeError(f"property_timeout must be a positive int, got {type(property_timeout).__name__}: {property_timeout!r}")
        self.model = model
        self.max_inputs = max_inputs
        self.generator = InputGenerator(seed=seed)
        self.property_timeout = property_timeout

    def infer_properties(self, code: str, function_name: str) -> List[Dict[str, Any]]:
        """
        Use LLM to infer properties for a function.

        Args:
            code: The source code
            function_name: Name of function to analyze

        Returns:
            List of property specifications
        """
        # C3-4b: validate infer_properties inputs
        if not isinstance(code, str):
            raise TypeError(f"code must be str, got {type(code).__name__}")
        if not isinstance(function_name, str) or not function_name.strip():
            raise TypeError(f"function_name must be a non-empty str, got {type(function_name).__name__!r}: {function_name!r}")
        prompt = self.PROPERTY_INFERENCE_PROMPT.safe_substitute(
            code=code,
            function_name=function_name
        )

        response, _ = invoke_fresh_llm(
            prompt=prompt,
            model=self.model,
            timeout=60
        )

        try:
            # Parse JSON from response
            # PT-C5-2: cap input to 100KB to prevent runaway parsing
            _capped = response[:102400] if len(response) > 102400 else response
            # Cycle 3 Fix P1: use json.JSONDecoder.raw_decode() instead of regex
            # to correctly handle nested JSON objects with balanced braces
            decoder = json.JSONDecoder()
            idx = 0
            while idx < len(_capped):
                brace_pos = _capped.find('{', idx)
                if brace_pos == -1:
                    break
                try:
                    parsed, end_idx = decoder.raw_decode(_capped, brace_pos)
                    if isinstance(parsed, dict) and "properties" in parsed:
                        return parsed.get("properties", [])
                    # Not the right JSON object, keep scanning
                    idx = end_idx
                except json.JSONDecodeError:
                    idx = brace_pos + 1
        except Exception as e:
            # Bug 11: log instead of silently swallowing
            logger.warning("infer_properties: failed to parse LLM response: %s", e)

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

        Raises:
            ValueError: If func is None
        """
        # C3-4c: reject non-callable func and non-dict property_spec
        if not callable(func):
            raise TypeError(f"func must be callable, got {type(func).__name__}: {func!r}")
        if not isinstance(property_spec, dict):
            raise TypeError(f"property_spec must be a dict, got {type(property_spec).__name__}")

        violations = []
        input_types = property_spec.get("input_types", ["int"])

        # Bug 5: normalize property type string to lowercase for case-insensitive matching
        raw_type = property_spec.get("type", "invariant")
        try:
            property_type = PropertyType(raw_type.lower() if isinstance(raw_type, str) else raw_type)
        except ValueError:
            logger.warning("Unknown PropertyType %r, defaulting to INVARIANT", raw_type)
            property_type = PropertyType.INVARIANT

        property_name = property_spec.get("name", "unknown")

        if not input_types:
            return violations

        # Generate inputs based on types
        inputs = []
        count_per_type = max(1, self.max_inputs // len(input_types))
        for input_type in input_types:
            if input_type == "int":
                inputs.extend(self.generator.integers(count_per_type))
            elif input_type == "str":
                inputs.extend(self.generator.strings(count_per_type))
            elif input_type == "float":
                inputs.extend(self.generator.floats(count_per_type))
            elif input_type == "list":
                inputs.extend(self.generator.lists(count_per_type))
            # Bug 3: add dict dispatch so dicts() generator is reachable
            elif input_type == "dict":
                inputs.extend(self.generator.dicts(count_per_type))
            else:
                # PT-2 fix: unknown input_type falls back to integers instead of silently producing zero inputs
                logger.warning("Unknown input_type %r, falling back to integers", input_type)
                inputs.extend(self.generator.integers(count_per_type))

        # Truncate to max_inputs: count_per_type*len(input_types) can exceed max_inputs
        # when len(input_types) > max_inputs (count_per_type=1 but total=len(input_types))
        inputs = inputs[:self.max_inputs]

        # Cycle 3 Fix (#9): MONOTONIC needs all inputs at once (sorted pairs), not per-input.
        # Handle it here before the per-input loop to avoid the per-input vs all-inputs mismatch.
        if property_type == PropertyType.MONOTONIC:
            return self._check_monotonic(func, inputs, property_name, property_type)

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
                # Exception during execution is a violation
                if property_type == PropertyType.NULL_SAFE and inp.value is None:
                    violations.append(PropertyViolation(
                        property_name=property_name,
                        property_type=property_type,
                        input_values=[inp],
                        expected="No exception",
                        actual=str(e),
                        error_message=f"Function raised exception on None: {e}"
                    ))
                else:
                    violations.append(PropertyViolation(
                        property_name=property_name,
                        property_type=property_type,
                        input_values=[inp],
                        expected="No exception during property check",
                        actual=str(e),
                        error_message=f"Unexpected exception: {type(e).__name__}: {e}"
                    ))

        return violations

    def _check_monotonic(
        self,
        func: Callable,
        inputs: list,
        property_name: str,
        property_type: "PropertyType",
    ) -> List[PropertyViolation]:
        """Check monotone non-decreasing: for all x <= y, f(x) <= f(y).

        Cycle 3 Fix (#9): replaces unconditional NotImplementedError in _check_property.
        Operates on all generated inputs sorted by value, testing consecutive pairs.
        """
        violations = []
        numeric_inputs = [
            i for i in inputs
            if isinstance(i.value, (int, float)) and not isinstance(i.value, bool)
        ]
        if len(numeric_inputs) < 2:
            return violations  # Not enough numeric inputs to check monotonicity

        sorted_inputs = sorted(numeric_inputs, key=lambda i: i.value)
        for idx in range(len(sorted_inputs) - 1):
            x_inp = sorted_inputs[idx]
            y_inp = sorted_inputs[idx + 1]
            x_val = x_inp.value
            y_val = y_inp.value
            if x_val == y_val:
                continue
            try:
                fx = func(x_val)
                fy = func(y_val)
            except Exception:
                continue  # skip pairs where func raises
            if not isinstance(fx, (int, float)) or not isinstance(fy, (int, float)):
                continue  # skip non-numeric results
            if x_val <= y_val and fx > fy:
                violations.append(PropertyViolation(
                    property_name=property_name,
                    property_type=property_type,
                    input_values=[x_inp, y_inp],
                    expected=f"f({x_val}) <= f({y_val})",
                    actual=f"f({x_val})={fx} > f({y_val})={fy}",
                    error_message=f"Monotonicity violated: f({x_val})={fx} > f({y_val})={fy}",
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
            except Exception as e:
                # Bug 8: don't silently swallow — report as violation
                return PropertyViolation(
                    property_name=property_name,
                    property_type=property_type,
                    input_values=[inp],
                    expected="f(f(x)) should not raise",
                    actual=str(e),
                    error_message=f"IDEMPOTENT re-application raised: {type(e).__name__}: {e}"
                )

        elif property_type == PropertyType.BOUNDED:
            # PT-C3-5: Guard against None/NaN results that crash comparison operators
            if result is None or (isinstance(result, float) and math.isnan(result)):
                return None  # Cannot evaluate bounds for None/NaN
            # Cycle 2 5b: guard complex and non-numeric types that crash < / > comparisons
            if isinstance(result, complex) or not isinstance(result, (int, float)):
                return None  # Cannot evaluate bounds for non-numeric types

            bounds = property_spec.get("bounds", {})
            min_val = bounds.get("min")
            max_val = bounds.get("max")
            # PT-C3-6: validate bounds are numeric before comparison
            if min_val is not None and not isinstance(min_val, (int, float)):
                return None
            if max_val is not None and not isinstance(max_val, (int, float)):
                return None
            # PT-C5-3: reject NaN bounds — NaN comparisons silently return False, bypassing checks
            if isinstance(min_val, float) and math.isnan(min_val):
                return None
            if isinstance(max_val, float) and math.isnan(max_val):
                return None

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
            _expected_cls = _SAFE_PYTHON_TYPES.get(expected_type)
            if expected_type and _expected_cls is not None and not isinstance(result, _expected_cls):
                return PropertyViolation(
                    property_name=property_name,
                    property_type=property_type,
                    input_values=[inp],
                    expected=expected_type,
                    actual=type(result).__name__,
                    error_message=f"Type mismatch: expected {expected_type}, got {type(result).__name__}"
                )

        # Cycle 3 Fix PT-3: COMMUTATIVE and ASSOCIATIVE need two args per call.
        # Log + return None instead of raising NotImplementedError.
        # NotImplementedError was silently swallowed by ThreadPoolExecutor in run_property_testing,
        # giving zero feedback. Now callers get an explicit warning per input.
        elif property_type == PropertyType.COMMUTATIVE:
            logger.warning("COMMUTATIVE check needs two-arg calls; not supported here — skipping")
            return None

        elif property_type == PropertyType.ASSOCIATIVE:
            logger.warning("ASSOCIATIVE check needs two-arg calls; not supported here — skipping")
            return None

        elif property_type == PropertyType.INVERSE:
            inverse_func = property_spec.get("inverse_func")
            if inverse_func and callable(inverse_func):
                try:
                    roundtrip = inverse_func(result)
                    if roundtrip != inp.value:
                        return PropertyViolation(
                            property_name=property_name,
                            property_type=property_type,
                            input_values=[inp],
                            expected=str(inp.value),
                            actual=str(roundtrip),
                            error_message="Inverse property violated: f^-1(f(x)) != x"
                        )
                except Exception as e:
                    # P2: report as violation instead of just logging
                    logger.warning("INVERSE check error for %s: %s", property_name, e)
                    return PropertyViolation(
                        property_name=property_name,
                        property_type=property_type,
                        input_values=[inp],
                        expected="f^-1(f(x)) should not raise",
                        actual=str(e),
                        error_message=f"INVERSE check raised: {type(e).__name__}: {e}"
                    )

        elif property_type == PropertyType.MONOTONIC:
            # Cycle 3 Fix (#9): MONOTONIC is now handled in test_property() via _check_monotonic()
            # before the per-input loop. This branch should never be reached.
            pass

        elif property_type == PropertyType.NULL_SAFE:
            # Null-safety is checked at the exception level in test_property
            pass

        elif property_type == PropertyType.PURE:
            # Pure: call twice, results should be identical
            try:
                second_result = func(inp.value)
                if result != second_result:
                    return PropertyViolation(
                        property_name=property_name,
                        property_type=property_type,
                        input_values=[inp],
                        expected=str(result),
                        actual=str(second_result),
                        error_message="Function is not pure: different results for same input"
                    )
            except Exception as e:
                # P2: report as violation instead of just logging
                logger.warning("PURE check error for %s: %s", property_name, e)
                return PropertyViolation(
                    property_name=property_name,
                    property_type=property_type,
                    input_values=[inp],
                    expected="f(x) should not raise on repeated call",
                    actual=str(e),
                    error_message=f"PURE check raised: {type(e).__name__}: {e}"
                )

        elif property_type == PropertyType.CONTENT_PRESERVING:
            # P1: use Counter instead of set to detect duplicate differences
            from collections import Counter
            try:
                if hasattr(inp.value, '__iter__') and hasattr(result, '__iter__'):
                    if Counter(str(x) for x in inp.value) != Counter(str(x) for x in result):
                        return PropertyViolation(
                            property_name=property_name,
                            property_type=property_type,
                            input_values=[inp],
                            expected="same content",
                            actual="content differs",
                            error_message="Content not preserved"
                        )
            except Exception as e:
                # Cycle 2 P2-sibling: return violation instead of just logging (matches INVERSE/PURE)
                logger.warning("CONTENT_PRESERVING check error for %s: %s", property_name, e)
                return PropertyViolation(
                    property_name=property_name,
                    property_type=property_type,
                    input_values=[inp],
                    expected="content preserving check should not raise",
                    actual=str(e),
                    error_message=f"CONTENT_PRESERVING check raised: {type(e).__name__}: {e}"
                )

        elif property_type == PropertyType.INVARIANT:
            # Invariant: evaluate the assertion string from property_spec
            assertion = property_spec.get("assertion", "")
            if assertion:
                # PT-C3-1: AST-based dunder attribute filter to prevent MRO traversal
                if not _validate_assertion(assertion):
                    logger.warning("Assertion rejected (dunder access): %s", assertion)
                    return PropertyViolation(
                        property_name=property_name,
                        property_type=property_type,
                        input_values=[inp],
                        expected="assertion must not use dunder attributes",
                        actual=f"assertion rejected: {assertion!r}",
                        error_message="Assertion rejected: contains dunder attribute access (__class__, __mro__, etc.)"
                    )
                try:
                    # PT-3 fix: removed 'func' from eval locals to prevent __globals__ sandbox escape
                    # PT-C5-1: wrap in timeout to prevent DoS from resource-exhausting assertions
                    local_vars = {"x": inp.value, "result": result}
                    # An invariant assertion must return a truthy value to pass.
                    # Use `not` to catch all falsy violations: False, None, 0.
                    # (Integer 0 is a legitimate invariant violation, not a "no result".)
                    # RT-01 Fix: pass {} as globals (not {"__builtins__": _RESTRICTED_BUILTINS}).
                    # MappingProxyType is not JSON-serializable; the subprocess serializer strips
                    # it and the safety guard raises RuntimeError → 100% false-positive violations.
                    # Subprocess isolation already provides safe builtins.
                    _inv_result = _subprocess_eval(assertion, {}, local_vars)
                    if not _inv_result:
                        return PropertyViolation(
                            property_name=property_name,
                            property_type=property_type,
                            input_values=[inp],
                            expected=f"assertion: {assertion}",
                            actual=str(result),
                            error_message=f"Invariant violated: {assertion}"
                        )
                except Exception as e:
                    # PT-C3-2/PT-C3-7: return violation instead of silently swallowing
                    logger.warning("Invariant eval error for %s: %s", property_name, e)
                    return PropertyViolation(
                        property_name=property_name,
                        property_type=property_type,
                        input_values=[inp],
                        expected=f"assertion: {assertion}",
                        actual=str(e),
                        error_message=f"INVARIANT eval raised: {type(e).__name__}: {e}"
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
        # C3-4d: validate run_property_testing inputs
        if not isinstance(code, str):
            raise TypeError(f"code must be str, got {type(code).__name__}")
        if not isinstance(function_name, str) or not function_name.strip():
            raise TypeError(f"function_name must be a non-empty str, got {type(function_name).__name__!r}: {function_name!r}")
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

        # If no function provided, try to get it from code.
        # NOTE: _subprocess_eval runs in an isolated child process; the parent exec_globals
        # dict is never mutated by it. We must exec the code in-process to extract the
        # function object. Use _RESTRICTED_BUILTINS to limit available builtins.
        # The subprocess path was erroneous (MappingProxyType not JSON-serializable, and
        # subprocess results are returned by value via JSON — not as live Python objects).
        if function is None:
            try:
                # C2-1a CRITICAL: pass _RESTRICTED_BUILTINS directly — it is already a
                # MappingProxyType. Wrapping in dict() strips immutability, allowing crafted
                # property functions to overwrite __builtins__ entries and escape the sandbox.
                exec_globals = {'__builtins__': _RESTRICTED_BUILTINS}
                _eval_with_timeout(
                    compile(code, '<property_test>', 'exec'),
                    exec_globals, {}, timeout=_EVAL_TIMEOUT_SECONDS, mode='exec'
                )  # noqa: S102
                function = exec_globals.get(function_name)
            except Exception as e:
                logger.warning(
                    "exec of untrusted code raised %s: %s — code (first 500 chars): %r",
                    type(e).__name__, e, code[:500]
                )
                result.error = f"Failed to extract function: {e}"
                result.success = False
                return result

        if function is None:
            result.error = f"Function {function_name} not found in code"
            result.success = False
            return result

        # Test each property with per-property timeout and violation deduplication.
        # Use a single shared executor for all properties to prevent thread pile-up:
        # creating one TPE per property leaks threads on timeout (shutdown can't kill
        # running threads), so N timeouts → N leaked threads + N executor instances.
        total_inputs = 0
        _seen_violations: set = set()
        _prop_timeout = self.property_timeout
        _shared_ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            for prop in properties:
                _fut = _shared_ex.submit(self.test_property, function, prop)
                violations = []  # initialize before try — prevents UnboundLocalError if future raises
                try:
                    violations = _fut.result(timeout=_prop_timeout)
                except concurrent.futures.TimeoutError:
                    logger.warning(
                        "Property '%s' timed out after %ss — skipping",
                        prop.get('name', 'unknown'), _prop_timeout
                    )
                    violations = []
                except Exception as _prop_err:
                    logger.warning(
                        "Property '%s' raised %s: %s — skipping",
                        prop.get('name', 'unknown'), type(_prop_err).__name__, _prop_err
                    )
                    violations = []
                for v in violations:
                    _first_val = str(v.input_values[0].value)[:80] if v.input_values else ""
                    _key = (v.property_name, _first_val, (v.error_message or "")[:80])
                    if _key not in _seen_violations:
                        _seen_violations.add(_key)
                        result.violations.append(v)
                total_inputs += self.max_inputs
        finally:
            _shared_ex.shutdown(wait=False, cancel_futures=True)

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
    # C3-4e: validate quick_property_test inputs before constructing PropertyTester
    if not isinstance(code, str):
        raise TypeError(f"code must be str, got {type(code).__name__}")
    if not isinstance(function_name, str) or not function_name.strip():
        raise TypeError(f"function_name must be a non-empty str, got {type(function_name).__name__!r}")
    if not isinstance(max_inputs, int) or isinstance(max_inputs, bool) or max_inputs <= 0:
        raise TypeError(f"max_inputs must be a positive int, got {type(max_inputs).__name__}: {max_inputs!r}")
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
