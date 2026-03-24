"""
Mutation Testing - Verify test quality by introducing controlled mutations.

Based on Karl Popper's principle of falsification: tests gain strength not by
being 'proven,' but by surviving rigorous attempts to disprove them.

The key insight: "If a mutant is introduced, this normally causes a bug in
the program's functionality which the tests should find. This way, the tests
are tested."
"""

import ast
import copy
import logging
import math
import subprocess
import tempfile
import hashlib
import random
import fcntl
import threading as _threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

# Security: restrict mutation targets to files within the project root.
# Resolved at import time to prevent symlink bypass at call time.
_ALLOWED_ROOT: Path = Path(__file__).resolve().parent.parent

# Safe environment variable allowlist for subprocess.run in test_mutant().
# Prevents environment variable injection when inherited os.environ contains
# attacker-controlled variables (e.g. PYTHONPATH, LD_PRELOAD).
_ALLOWED_ENV_VARS = frozenset({
    'PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'TERM',
    'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ',
    'PYTHONDONTWRITEBYTECODE', 'PYTHONIOENCODING',
    'VIRTUAL_ENV', 'CONDA_PREFIX',
})


class MutantOperator(Enum):
    """Types of mutation operators."""
    # Arithmetic operators
    ARITHMETIC_REPLACE = "arithmetic_replace"  # + -> -, * -> /, etc.
    ARITHMETIC_DELETE = "arithmetic_delete"    # Remove operation

    # Comparison operators
    COMPARISON_REPLACE = "comparison_replace"  # == -> !=, < -> <=, etc.
    COMPARISON_BOUNDARY = "comparison_boundary"  # < -> <=, > -> >=

    # Logical operators
    LOGICAL_REPLACE = "logical_replace"  # and -> or, not removal

    # Constant mutations
    CONSTANT_REPLACE = "constant_replace"  # 0 -> 1, True -> False
    CONSTANT_BOUNDARY = "constant_boundary"  # n -> n+1, n -> n-1

    # Statement mutations
    STATEMENT_DELETE = "statement_delete"  # Remove a statement
    RETURN_VALUE = "return_value"          # Change return value

    # Branch mutations
    CONDITION_NEGATE = "condition_negate"  # Negate if condition
    BRANCH_SWAP = "branch_swap"            # Swap if/else bodies


@dataclass
class Mutant:
    """A single code mutation."""
    id: str
    operator: MutantOperator
    original_code: str
    mutated_code: str
    location: str  # line number or AST node description
    description: str
    killed: bool = False
    error: Optional[str] = None
    test_output: str = ""


@dataclass
class MutationScore:
    """Mutation testing score and analysis."""
    total_mutants: int
    killed_mutants: int
    survived_mutants: int
    error_mutants: int
    score: float  # killed / (total - errors)
    survived_details: List[Mutant] = field(default_factory=list)

    def __post_init__(self):
        if not (0.0 <= self.score <= 1.0):
            raise ValueError(f"MutationScore.score must be in [0.0, 1.0], got {self.score}")
        for _field_name in ('total_mutants', 'killed_mutants', 'survived_mutants', 'error_mutants'):
            _val = getattr(self, _field_name)
            if not isinstance(_val, int) or _val < 0:
                raise ValueError(f"MutationScore.{_field_name} must be a non-negative int, got {_val!r}")

    @property
    def is_passing(self) -> bool:
        """Is the mutation score acceptable? (>= 80%)"""
        return self.score >= 0.8


@dataclass
class MutationResult:
    """Complete results from mutation testing."""
    code_path: str
    test_command: str
    timestamp: str
    duration_ms: float
    mutants: List[Mutant] = field(default_factory=list)
    score: Optional[MutationScore] = None
    success: bool = True
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "code_path": self.code_path,
            "test_command": self.test_command,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "mutants": [
                {
                    "id": m.id,
                    "operator": m.operator.value,
                    "location": m.location,
                    "description": m.description,
                    "killed": m.killed,
                    "error": m.error
                }
                for m in self.mutants
            ],
            "score": {
                "total": self.score.total_mutants,
                "killed": self.score.killed_mutants,
                "survived": self.score.survived_mutants,
                "errors": self.score.error_mutants,
                "score": self.score.score
            } if self.score else None,
            "success": self.success,
            "error": self.error
        }


class PythonMutator(ast.NodeTransformer):
    """AST transformer that applies mutations to Python code."""

    # Thread-local storage so concurrent calls on the same instance don't corrupt each other
    _tl = _threading.local()

    @property
    def _current_mutation_index(self) -> int:
        """Read current_mutation_index from thread-local (apply mode) or instance (collect mode)."""
        return getattr(PythonMutator._tl, 'current_mutation_index', self.current_mutation_index)

    # Operator replacements
    ARITHMETIC_OPS = {
        ast.Add: ast.Sub,
        ast.Sub: ast.Add,
        ast.Mult: ast.FloorDiv,
        ast.FloorDiv: ast.Mult,
        ast.Div: ast.Mult,
        ast.Mod: ast.Div
    }

    COMPARISON_OPS = {
        ast.Eq: ast.NotEq,
        ast.NotEq: ast.Eq,
        ast.Lt: ast.LtE,
        ast.LtE: ast.Lt,
        ast.Gt: ast.GtE,
        ast.GtE: ast.Gt,
        ast.Is: ast.IsNot,
        ast.IsNot: ast.Is
    }

    LOGICAL_OPS = {
        ast.And: ast.Or,
        ast.Or: ast.And
    }

    def __init__(self, target_mutation: Optional[str] = None):
        """
        Initialize mutator.

        Args:
            target_mutation: If specified, only apply this mutation type
        """
        self.target_mutation = target_mutation
        self.mutations_found: List[Tuple[str, int, str]] = []  # (type, line, desc)
        self.current_mutation_index = -1  # -1 means collect only, >= 0 means apply that mutation

    def collect_mutations(self, tree: ast.AST) -> List[Tuple[str, int, str]]:
        """Collect all possible mutations without applying them."""
        self.mutations_found = []
        self.current_mutation_index = -1
        # Also clear thread-local to avoid stale apply_mutation values from same thread
        PythonMutator._tl.current_mutation_index = -1
        self.visit(tree)
        return self.mutations_found

    def apply_mutation(self, tree: ast.AST, mutation_index: int) -> ast.AST:
        """Apply a specific mutation by index."""
        tree_copy = copy.deepcopy(tree)
        # Both current_mutation_index and mutation_counter are thread-local to prevent
        # concurrent calls on the same PythonMutator instance from corrupting each other.
        PythonMutator._tl.current_mutation_index = mutation_index
        PythonMutator._tl.mutation_counter = 0
        try:
            return self.visit(tree_copy)
        finally:
            # Clear thread-local after apply to prevent stale _tl from leaking into
            # subsequent collect_mutations() calls on the same thread.
            PythonMutator._tl.current_mutation_index = -1

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        """Mutate binary operators."""
        op_type = type(node.op)
        if op_type in self.ARITHMETIC_OPS:
            mutation_desc = f"Replace {op_type.__name__} with {self.ARITHMETIC_OPS[op_type].__name__}"

            if self._current_mutation_index == -1:
                # Collection mode
                self.mutations_found.append((
                    MutantOperator.ARITHMETIC_REPLACE.value,
                    getattr(node, 'lineno', 0),
                    mutation_desc
                ))
            elif hasattr(PythonMutator._tl, 'mutation_counter'):
                if PythonMutator._tl.mutation_counter == self._current_mutation_index:
                    node.op = self.ARITHMETIC_OPS[op_type]()
                PythonMutator._tl.mutation_counter += 1

        self.generic_visit(node)
        return node

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        """Mutate comparison operators."""
        for i, op in enumerate(node.ops):
            op_type = type(op)
            if op_type in self.COMPARISON_OPS:
                mutation_desc = f"Replace {op_type.__name__} with {self.COMPARISON_OPS[op_type].__name__}"

                if self._current_mutation_index == -1:
                    self.mutations_found.append((
                        MutantOperator.COMPARISON_REPLACE.value,
                        getattr(node, 'lineno', 0),
                        mutation_desc
                    ))
                elif hasattr(PythonMutator._tl, 'mutation_counter'):
                    if PythonMutator._tl.mutation_counter == self._current_mutation_index:
                        node.ops[i] = self.COMPARISON_OPS[op_type]()
                    PythonMutator._tl.mutation_counter += 1

        self.generic_visit(node)
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        """Mutate boolean operators."""
        op_type = type(node.op)
        if op_type in self.LOGICAL_OPS:
            mutation_desc = f"Replace {op_type.__name__} with {self.LOGICAL_OPS[op_type].__name__}"

            if self._current_mutation_index == -1:
                self.mutations_found.append((
                    MutantOperator.LOGICAL_REPLACE.value,
                    getattr(node, 'lineno', 0),
                    mutation_desc
                ))
            elif hasattr(PythonMutator._tl, 'mutation_counter'):
                if PythonMutator._tl.mutation_counter == self._current_mutation_index:
                    node.op = self.LOGICAL_OPS[op_type]()
                PythonMutator._tl.mutation_counter += 1

        self.generic_visit(node)
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        """Mutate constants."""
        if isinstance(node.value, bool):
            mutation_desc = f"Replace {node.value} with {not node.value}"
            if self._current_mutation_index == -1:
                self.mutations_found.append((
                    MutantOperator.CONSTANT_REPLACE.value,
                    getattr(node, 'lineno', 0),
                    mutation_desc
                ))
            elif hasattr(PythonMutator._tl, 'mutation_counter'):
                if PythonMutator._tl.mutation_counter == self._current_mutation_index:
                    node.value = not node.value
                PythonMutator._tl.mutation_counter += 1

        elif isinstance(node.value, int) and not isinstance(node.value, bool):
            # Cycle 3 Fix P5a: guard against extremely large integers that could cause memory issues
            if abs(node.value) >= 2**63:
                return node
            # Boundary mutation: n -> n+1
            mutation_desc = f"Replace {node.value} with {node.value + 1}"
            if self._current_mutation_index == -1:
                self.mutations_found.append((
                    MutantOperator.CONSTANT_BOUNDARY.value,
                    getattr(node, 'lineno', 0),
                    mutation_desc
                ))
            elif hasattr(PythonMutator._tl, 'mutation_counter'):
                if PythonMutator._tl.mutation_counter == self._current_mutation_index:
                    node.value = node.value + 1
                PythonMutator._tl.mutation_counter += 1

        return node

    def visit_Return(self, node: ast.Return) -> ast.AST:
        """Mutate return statements."""
        if node.value is not None:
            mutation_desc = "Replace return value with None"
            if self._current_mutation_index == -1:
                self.mutations_found.append((
                    MutantOperator.RETURN_VALUE.value,
                    getattr(node, 'lineno', 0),
                    mutation_desc
                ))
            elif hasattr(PythonMutator._tl, 'mutation_counter'):
                if PythonMutator._tl.mutation_counter == self._current_mutation_index:
                    node.value = ast.Constant(value=None)
                PythonMutator._tl.mutation_counter += 1

        self.generic_visit(node)
        return node

    def visit_If(self, node: ast.If) -> ast.AST:
        """Mutate if conditions."""
        mutation_desc = "Negate if condition"
        if self._current_mutation_index == -1:
            self.mutations_found.append((
                MutantOperator.CONDITION_NEGATE.value,
                getattr(node, 'lineno', 0),
                mutation_desc
            ))
        elif hasattr(PythonMutator._tl, 'mutation_counter'):
            if PythonMutator._tl.mutation_counter == self._current_mutation_index:
                # Wrap condition in Not
                node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
            PythonMutator._tl.mutation_counter += 1

        self.generic_visit(node)
        return node


class MutationTester:
    """
    Runs mutation testing on Python code.

    Process:
    1. Parse the source code into AST
    2. Generate mutants by applying mutation operators
    3. Run tests against each mutant
    4. Calculate mutation score (killed / total)
    """

    def __init__(
        self,
        max_mutants: int = 50,
        timeout_per_mutant: int = 30,
        sample_ratio: float = 1.0
    ):
        """
        Initialize mutation tester.

        Args:
            max_mutants: Maximum number of mutants to generate
            timeout_per_mutant: Timeout for each test run in seconds
            sample_ratio: Ratio of mutants to actually test (for sampling)
        """
        # MT-5: validate max_mutants type
        if isinstance(max_mutants, bool) or not isinstance(max_mutants, int):
            raise ValueError(f"max_mutants must be int, got {type(max_mutants).__name__}")
        if max_mutants < 0:
            raise ValueError(f"max_mutants must be non-negative, got {max_mutants}")
        # MT-5: validate sample_ratio type and range
        if isinstance(sample_ratio, bool) or not isinstance(sample_ratio, (int, float)):
            raise ValueError(f"sample_ratio must be a number, got {type(sample_ratio).__name__}")
        if not (0.0 < sample_ratio <= 1.0):
            raise ValueError(f"sample_ratio must be in (0.0, 1.0], got {sample_ratio}")
        self.max_mutants = max_mutants
        # Cycle 2 6a: reject non-positive/NaN/inf/non-numeric timeout
        if not isinstance(timeout_per_mutant, (int, float)) or isinstance(timeout_per_mutant, bool):
            raise ValueError(f"timeout_per_mutant must be a number, got {type(timeout_per_mutant).__name__}")
        if not math.isfinite(timeout_per_mutant) or timeout_per_mutant <= 0:
            raise ValueError(f"timeout_per_mutant must be a positive finite number, got {timeout_per_mutant!r}")
        self.timeout_per_mutant = timeout_per_mutant
        self.sample_ratio = sample_ratio
        # Shared PythonMutator removed: mutable state (mutation_counter) is unsafe
        # for concurrent generate_mutants() calls. Local instance created per call.

    def generate_mutants(self, code: str) -> List[Mutant]:
        """
        Generate mutants from source code.

        Args:
            code: Python source code

        Returns:
            List of Mutant objects
        """
        # M2: reject any non-str upfront — ast.parse gives confusing error for non-str types
        if not isinstance(code, str):
            raise TypeError(f"code must be str, got {type(code).__name__}")
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            logger.debug("generate_mutants: skipping invalid code (SyntaxError: %s)", e)
            return []  # Can't mutate invalid code

        # Fresh PythonMutator per call — avoids shared mutation_counter state
        # that would cause races if generate_mutants() is called concurrently.
        _mutator = PythonMutator()

        # Collect all possible mutations
        mutations = _mutator.collect_mutations(tree)

        # Sample if needed — preserve original indices so apply_mutation
        # receives the correct position in the full (pre-sample) list.
        indexed_mutations = list(enumerate(mutations))
        # MT-1 fix: clamp max_mutants to 0 minimum to prevent negative k in random.sample()
        effective_max = max(0, self.max_mutants)
        if effective_max == 0:
            return []
        if len(indexed_mutations) > effective_max:
            k = effective_max
            indexed_mutations = random.sample(indexed_mutations, k)

        # Apply sample_ratio in generate_mutants() so callers get sampled results
        if self.sample_ratio < 1.0:
            sample_size = max(1, int(len(indexed_mutations) * self.sample_ratio))
            indexed_mutations = random.sample(indexed_mutations, sample_size)

        # Generate actual mutants
        mutants = []
        seen_ids = set()  # Cycle 2 6b: track IDs to detect collisions
        for orig_idx, (op_type, line, desc) in indexed_mutations:
            try:
                # Cycle 3 Fix MT-HIGH: bounds-check orig_idx against the actual node count
                # before applying. apply_mutation iterates AST nodes counting up to orig_idx;
                # if orig_idx >= len(mutations) in the fresh tree, no node is mutated and
                # the "mutant" is identical to the original (phantom mutant — always survives).
                _fresh_mutations = _mutator.collect_mutations(ast.parse(code))
                if orig_idx >= len(_fresh_mutations):
                    continue  # Skip phantom — index out of range for this source
                tree = ast.parse(code)
                mutated_tree = _mutator.apply_mutation(tree, orig_idx)
                ast.fix_missing_locations(mutated_tree)
                mutated_code = ast.unparse(mutated_tree)

                # Cycle 5: secondary phantom-mutant check via AST round-trip.
                # String comparison after unparse can miss whitespace-equivalent
                # mutations. Compare against canonical unparse of original.
                _canonical_orig = ast.unparse(ast.parse(code))
                if mutated_code == _canonical_orig:
                    logger.debug(
                        "Phantom mutant (AST equal after unparse): orig_idx=%d op=%s",
                        orig_idx, op_type
                    )
                    continue

                # Cycle 2 6b: include orig_idx in hash to prevent collision for same-line identical mutations
                mutant_id = hashlib.md5(f"{op_type}:{line}:{desc}:{orig_idx}".encode()).hexdigest()[:8]
                # Belt-and-suspenders: append counter if hash still collides
                base_id = mutant_id
                collision_counter = 0
                while mutant_id in seen_ids:
                    collision_counter += 1
                    mutant_id = hashlib.md5(f"{base_id}:{collision_counter}".encode()).hexdigest()[:8]
                seen_ids.add(mutant_id)

                mutant = Mutant(
                    id=f"m_{mutant_id}",
                    operator=MutantOperator(op_type),
                    original_code=code,
                    mutated_code=mutated_code,
                    location=f"line:{line}",
                    description=desc
                )
                mutants.append(mutant)

            except (SyntaxError, ValueError, TypeError, RecursionError) as e:
                # MT-6: narrow to specific AST/unparse errors, skip invalid mutations
                # RecursionError added: ast.unparse() on deeply recursive ASTs raises RecursionError
                logger.debug("Skipping mutation orig_idx=%d op=%s: %s", orig_idx, op_type, e)
                continue

        return mutants

    def test_mutant(
        self,
        mutant: Mutant,
        test_command: str,
        original_file: Path
    ) -> Mutant:
        """
        Test a single mutant by running tests against it.

        Args:
            mutant: The mutant to test
            test_command: Command to run tests (e.g., "pytest tests/")
            original_file: Path to the original file being mutated

        Returns:
            Updated Mutant with killed status
        """
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.py',
            delete=False,
            dir=tempfile.gettempdir()
        ) as tmp:
            tmp.write(mutant.mutated_code)
            tmp_path = Path(tmp.name)

        # P1: validate original_file is a regular file (rejects dirs, symlinks-to-dirs, missing paths)
        if not original_file.is_file():
            raise FileNotFoundError(f"original_file is not a regular file: {original_file}")

        # C1: initialize before try so finally can guard against NameError
        original_code = None
        # C2: use a .bak file for atomic backup — survives SIGKILL between write and finally
        # Cycle 3 Fix (#8): use full filename (including extension) as stem for derived paths
        # so foo.js.bak and foo.js.mutant_lock don't collide with foo.bak / foo.mutant_lock.
        # The old guard for .bak-suffixed files is no longer needed with this scheme.
        bak_path = original_file.with_name(original_file.name + '.bak')
        # 5a: advisory file lock to prevent concurrent test_mutant calls from clobbering .bak
        lock_path = original_file.with_name(original_file.name + '.mutant_lock')
        # Fix 6: validate test_command BEFORE acquiring lock / overwriting disk state,
        # so bad inputs raise immediately without side effects.
        if not isinstance(test_command, (str, list)) or not test_command:
            raise ValueError("test_command must be a non-empty string or list")
        if isinstance(test_command, list) and not all(isinstance(c, str) for c in test_command):
            raise TypeError("test_command list elements must all be strings")
        if isinstance(test_command, str) and not test_command.strip():
            raise ValueError("test_command must be a non-empty string")

        try:
            lock_fd = open(lock_path, 'w')
        except PermissionError as e:
            raise PermissionError(f"Cannot acquire lock file {lock_path}: {e}") from e
        # C3-2b: guarantee lock_fd is closed even if flock raises (e.g. interrupted system call)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        except Exception:
            lock_fd.close()
            raise

        try:
            # Backup original file (C1 fix: assigned before overwrite)
            original_code = original_file.read_text()
            # C2: write atomic backup so finally can restore even if process is killed mid-mutant
            bak_path.write_text(original_code)

            # Cycle 3 Fix MT-CRIT: replace source atomically via os.replace() so that
            # a crash between the write and the finally-block restore cannot leave the
            # original file in a partially-written state.  Write to a second temp file
            # in the same directory (guaranteeing same filesystem for atomic rename),
            # then rename into place.
            import os as _os
            _atomic_fd, _atomic_path_str = tempfile.mkstemp(
                dir=original_file.parent, suffix='.mutant_atomic'
            )
            try:
                with _os.fdopen(_atomic_fd, 'w') as _af:
                    _af.write(mutant.mutated_code)
                # Preserve original file permissions before atomic rename
                import shutil as _shutil_perms
                try:
                    _shutil_perms.copymode(str(original_file), _atomic_path_str)
                except OSError:
                    pass  # best-effort; continue with rename even if copymode fails
                _os.replace(_atomic_path_str, str(original_file))
            except Exception:
                try:
                    _os.unlink(_atomic_path_str)
                except OSError:
                    pass
                raise

            # Run tests — shell=False prevents OS command injection via LLM-supplied test_command
            import shlex as _shlex
            _cmd_list = _shlex.split(test_command) if isinstance(test_command, str) else list(test_command)
            # Cycle 3 Fix P5c: validate executable element exists and is not empty
            if not _cmd_list or not _cmd_list[0].strip():
                raise ValueError("test_command resolved to empty command list")
            import shutil as _shutil
            _exe = _cmd_list[0]
            # Bare names (no path separator) are looked up on PATH — always allowed if found.
            # Any string containing "/" is treated as a path and must resolve within _ALLOWED_ROOT.
            # Fix 5: include backslash so Windows-style paths are classified as path-style,
            # not bare names. On Linux \\ paths fail which() and are blocked — semantically correct.
            _is_bare_name = "/" not in _exe and "\\" not in _exe
            if _is_bare_name:
                _found = _shutil.which(_exe)
            else:
                # Relative/absolute paths: resolve to canonical form and enforce workspace containment
                _found = None
                for _candidate in (Path(_exe),):
                    try:
                        _resolved = _candidate.resolve()
                    except (OSError, ValueError):
                        # OSError: permission denied / invalid path; ValueError: embedded NUL bytes
                        continue
                    if _resolved.is_file() and _resolved.is_relative_to(_ALLOWED_ROOT):
                        _found = str(_resolved)
                        break
            if not _found:
                raise FileNotFoundError(f"security: test_command executable not found or outside workspace: {_exe!r}")
            # Fix 1 (TOCTOU): use _found (resolved canonical path) as argv[0] so subprocess
            # executes exactly the binary that passed containment check, not the original string.
            _exec_cmd = [_found] + _cmd_list[1:]
            import os as _os_env
            _safe_env = {k: v for k, v in _os_env.environ.items() if k in _ALLOWED_ENV_VARS}
            result = subprocess.run(
                _exec_cmd,
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_per_mutant,
                cwd=original_file.parent,
                env=_safe_env,
            )

            mutant.test_output = result.stdout + result.stderr

            # If tests fail, mutant is killed
            if result.returncode != 0:
                mutant.killed = True
            else:
                mutant.killed = False  # Mutant survived - tests didn't catch it!

        except subprocess.TimeoutExpired as _te:
            mutant.killed = True  # Timeout counts as killed (infinite loop = detected) C3-5: no error flag
            mutant.test_output = ((_te.stdout or b'').decode('utf-8', errors='replace') +
                                  (_te.stderr or b'').decode('utf-8', errors='replace'))
        except ValueError:
            # Fix 2: programming errors (bad test_command) must propagate, not be silently stored
            raise
        except (ImportError, ModuleNotFoundError) as e:
            # MT-7: infrastructure failures — not a real test result
            mutant.error = f"infrastructure: {e}"
            mutant.killed = False
        except Exception as e:
            # C3-2a: re-raise infrastructure failures so they aren't masked as mutation results
            if isinstance(e, OSError):
                raise
            logger.debug("test_mutant caught unexpected error for mutant %s: %s", mutant.id, e)
            mutant.error = str(e)
            mutant.killed = False  # Explicit: error state != survived, but also != killed
        finally:
            # MT-C3-2: hash-based backup integrity check (replaces length-only check)
            # Nested try/finally guarantees lock release even if write_text() raises OSError.
            _restore_error = None
            try:
                if bak_path.exists():
                    backup_content = bak_path.read_text()
                    if original_code is not None:
                        original_hash = hashlib.sha256(original_code.encode()).hexdigest()
                        backup_hash = hashlib.sha256(backup_content.encode()).hexdigest()
                        if original_hash == backup_hash:
                            # Backup matches original — safe to restore from .bak
                            original_file.write_text(backup_content)
                        else:
                            # Backup may be corrupt (truncated) — use in-memory copy
                            original_file.write_text(original_code)
                    else:
                        # No in-memory copy available — best effort from .bak
                        original_file.write_text(backup_content)
                    bak_path.unlink(missing_ok=True)
                elif original_code is not None:
                    original_file.write_text(original_code)
                else:
                    # Neither bak nor in-memory copy — file left in mutant state (corruption)
                    _restore_error = RuntimeError(
                        f"Cannot restore {original_file}: no backup file and no in-memory copy"
                    )
            except OSError as _e:
                _restore_error = _e
            finally:
                # Lock release is unconditional — runs even if write_text() raised above.
                # tmp_path.unlink() is wrapped so a PermissionError on tmpfs cannot
                # prevent flock release and lock_fd.close() from executing.
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass  # best-effort cleanup; do not block lock release
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
                try:
                    lock_path.unlink(missing_ok=True)
                except OSError:
                    pass  # best-effort; stale lock file is not a blocker
            if _restore_error is not None:
                raise _restore_error

        # Bug 24: enforce exclusive states — killed XOR error XOR survived
        if mutant.error:
            mutant.killed = False  # error state takes precedence

        return mutant

    def run_mutation_testing(
        self,
        code_path: Path,
        test_command: str = ""
    ) -> MutationResult:
        """
        Run full mutation testing on a file.

        Args:
            code_path: Path to the Python file to mutate
            test_command: Command to run tests

        Returns:
            MutationResult with score and details
        """
        start_time = datetime.now()

        # Cycle 3 Fix P5b: validate code_path is not None
        if code_path is None:
            raise TypeError("code_path must be a Path, got None")
        # MT-C3-1: validate test_command early (before any file I/O)
        if not test_command or (isinstance(test_command, str) and not test_command.strip()):
            raise ValueError("test_command must be a non-empty string")

        code_path = Path(code_path).resolve()

        result = MutationResult(
            code_path=str(code_path),
            test_command=test_command,
            timestamp=start_time.isoformat(),
            duration_ms=0
        )

        if not code_path.is_relative_to(_ALLOWED_ROOT):
            result.success = False
            result.error = f"Security: path traversal rejected — {code_path} is outside {_ALLOWED_ROOT}"
            return result

        if not code_path.exists():
            result.success = False
            result.error = f"File not found: {code_path}"
            return result

        if not code_path.is_file():
            result.success = False
            result.error = f"Security: path is not a regular file: {code_path}"
            return result

        code = code_path.read_text()

        # Generate mutants
        mutants = self.generate_mutants(code)

        if not mutants:
            empty_score = MutationScore(
                total_mutants=0,
                killed_mutants=0,
                survived_mutants=0,
                error_mutants=0,
                score=0.0
            )
            if self.max_mutants == 0:
                # Caller explicitly requested zero mutants — not an error
                result.success = True
                result.score = empty_score
                return result
            result.success = False
            result.error = "No mutants generated (possibly invalid code or no mutable constructs)"
            result.score = empty_score
            return result

        # sample_ratio already applied inside generate_mutants(); no double-sampling here.

        # Test each mutant
        for mutant in mutants:
            self.test_mutant(mutant, test_command, code_path)

        result.mutants = mutants

        # Calculate score
        killed = sum(1 for m in mutants if m.killed and not m.error)
        errors = sum(1 for m in mutants if m.error)
        total = len(mutants)
        # C2-3a: log a warning when mutation accounting is inconsistent so the problem
        # is visible rather than silently swallowed by the clamp to 0.
        _raw_survived = total - killed - errors
        if _raw_survived < 0:
            logger.warning(
                f"Mutation accounting inconsistency: total={total} killed={killed} "
                f"errors={errors} raw_survived={_raw_survived}; clamping to 0"
            )
        survived = max(0, _raw_survived)
        testable = total - errors

        # M1: score=0.0 when no testable mutants (all errors), not 1.0
        score = killed / testable if testable > 0 else 0.0

        result.score = MutationScore(
            total_mutants=total,
            killed_mutants=killed,
            survived_mutants=survived,
            error_mutants=errors,
            score=score,
            survived_details=[m for m in mutants if not m.killed and not m.error]
        )

        result.duration_ms = (datetime.now() - start_time).total_seconds() * 1000

        return result


def quick_mutation_test(
    code_path: Path,
    test_command: str,
    max_mutants: int = 20
) -> MutationScore:
    """
    Quick mutation test for a single file.

    Args:
        code_path: Path to Python file
        test_command: Command to run tests
        max_mutants: Maximum mutants to test

    Returns:
        MutationScore
    """
    tester = MutationTester(max_mutants=max_mutants)
    result = tester.run_mutation_testing(code_path, test_command)
    return result.score if result.score else MutationScore(0, 0, 0, 0, 0.0)


if __name__ == "__main__":
    # Self-test with example code
    print("Mutation Testing - Self Test")
    print("=" * 50)

    test_code = '''
def add(a, b):
    return a + b

def is_positive(n):
    return n > 0

def classify(x):
    if x < 0:
        return "negative"
    elif x == 0:
        return "zero"
    else:
        return "positive"
'''

    print("Original code:")
    print(test_code)
    print("\nGenerating mutants...")

    tester = MutationTester(max_mutants=10)
    mutants = tester.generate_mutants(test_code)

    print(f"\nGenerated {len(mutants)} mutants:")
    for mutant in mutants[:5]:  # Show first 5
        print(f"  [{mutant.operator.value}] {mutant.description} @ {mutant.location}")

    print("\nMutation testing self-test complete!")
    print("Note: Full testing requires a test file to run against the mutants.")
