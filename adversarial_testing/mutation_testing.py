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
import subprocess
import tempfile
import hashlib
import random
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from enum import Enum


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
        self.visit(tree)
        return self.mutations_found

    def apply_mutation(self, tree: ast.AST, mutation_index: int) -> ast.AST:
        """Apply a specific mutation by index."""
        tree_copy = copy.deepcopy(tree)
        self.current_mutation_index = mutation_index
        self.mutation_counter = 0
        return self.visit(tree_copy)

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        """Mutate binary operators."""
        op_type = type(node.op)
        if op_type in self.ARITHMETIC_OPS:
            mutation_desc = f"Replace {op_type.__name__} with {self.ARITHMETIC_OPS[op_type].__name__}"

            if self.current_mutation_index == -1:
                # Collection mode
                self.mutations_found.append((
                    MutantOperator.ARITHMETIC_REPLACE.value,
                    getattr(node, 'lineno', 0),
                    mutation_desc
                ))
            elif hasattr(self, 'mutation_counter'):
                if self.mutation_counter == self.current_mutation_index:
                    node.op = self.ARITHMETIC_OPS[op_type]()
                self.mutation_counter += 1

        self.generic_visit(node)
        return node

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        """Mutate comparison operators."""
        for i, op in enumerate(node.ops):
            op_type = type(op)
            if op_type in self.COMPARISON_OPS:
                mutation_desc = f"Replace {op_type.__name__} with {self.COMPARISON_OPS[op_type].__name__}"

                if self.current_mutation_index == -1:
                    self.mutations_found.append((
                        MutantOperator.COMPARISON_REPLACE.value,
                        getattr(node, 'lineno', 0),
                        mutation_desc
                    ))
                elif hasattr(self, 'mutation_counter'):
                    if self.mutation_counter == self.current_mutation_index:
                        node.ops[i] = self.COMPARISON_OPS[op_type]()
                    self.mutation_counter += 1

        self.generic_visit(node)
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        """Mutate boolean operators."""
        op_type = type(node.op)
        if op_type in self.LOGICAL_OPS:
            mutation_desc = f"Replace {op_type.__name__} with {self.LOGICAL_OPS[op_type].__name__}"

            if self.current_mutation_index == -1:
                self.mutations_found.append((
                    MutantOperator.LOGICAL_REPLACE.value,
                    getattr(node, 'lineno', 0),
                    mutation_desc
                ))
            elif hasattr(self, 'mutation_counter'):
                if self.mutation_counter == self.current_mutation_index:
                    node.op = self.LOGICAL_OPS[op_type]()
                self.mutation_counter += 1

        self.generic_visit(node)
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        """Mutate constants."""
        if isinstance(node.value, bool):
            mutation_desc = f"Replace {node.value} with {not node.value}"
            if self.current_mutation_index == -1:
                self.mutations_found.append((
                    MutantOperator.CONSTANT_REPLACE.value,
                    getattr(node, 'lineno', 0),
                    mutation_desc
                ))
            elif hasattr(self, 'mutation_counter'):
                if self.mutation_counter == self.current_mutation_index:
                    node.value = not node.value
                self.mutation_counter += 1

        elif isinstance(node.value, int) and not isinstance(node.value, bool):
            # Boundary mutation: n -> n+1
            mutation_desc = f"Replace {node.value} with {node.value + 1}"
            if self.current_mutation_index == -1:
                self.mutations_found.append((
                    MutantOperator.CONSTANT_BOUNDARY.value,
                    getattr(node, 'lineno', 0),
                    mutation_desc
                ))
            elif hasattr(self, 'mutation_counter'):
                if self.mutation_counter == self.current_mutation_index:
                    node.value = node.value + 1
                self.mutation_counter += 1

        return node

    def visit_Return(self, node: ast.Return) -> ast.AST:
        """Mutate return statements."""
        if node.value is not None:
            mutation_desc = "Replace return value with None"
            if self.current_mutation_index == -1:
                self.mutations_found.append((
                    MutantOperator.RETURN_VALUE.value,
                    getattr(node, 'lineno', 0),
                    mutation_desc
                ))
            elif hasattr(self, 'mutation_counter'):
                if self.mutation_counter == self.current_mutation_index:
                    node.value = ast.Constant(value=None)
                self.mutation_counter += 1

        self.generic_visit(node)
        return node

    def visit_If(self, node: ast.If) -> ast.AST:
        """Mutate if conditions."""
        mutation_desc = "Negate if condition"
        if self.current_mutation_index == -1:
            self.mutations_found.append((
                MutantOperator.CONDITION_NEGATE.value,
                getattr(node, 'lineno', 0),
                mutation_desc
            ))
        elif hasattr(self, 'mutation_counter'):
            if self.mutation_counter == self.current_mutation_index:
                # Wrap condition in Not
                node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
            self.mutation_counter += 1

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
        self.max_mutants = max_mutants
        self.timeout_per_mutant = timeout_per_mutant
        self.sample_ratio = sample_ratio
        self.mutator = PythonMutator()

    def generate_mutants(self, code: str) -> List[Mutant]:
        """
        Generate mutants from source code.

        Args:
            code: Python source code

        Returns:
            List of Mutant objects
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return []  # Can't mutate invalid code

        # Collect all possible mutations
        mutations = self.mutator.collect_mutations(tree)

        # Sample if needed
        if len(mutations) > self.max_mutants:
            mutations = random.sample(mutations, self.max_mutants)

        # Generate actual mutants
        mutants = []
        for i, (op_type, line, desc) in enumerate(mutations):
            try:
                # Re-parse for each mutation (to get fresh tree)
                tree = ast.parse(code)
                mutated_tree = self.mutator.apply_mutation(tree, i)
                ast.fix_missing_locations(mutated_tree)
                mutated_code = ast.unparse(mutated_tree)

                mutant_id = hashlib.md5(f"{op_type}:{line}:{desc}".encode()).hexdigest()[:8]

                mutant = Mutant(
                    id=f"m_{mutant_id}",
                    operator=MutantOperator(op_type),
                    original_code=code,
                    mutated_code=mutated_code,
                    location=f"line:{line}",
                    description=desc
                )
                mutants.append(mutant)

            except Exception as e:
                # Some mutations may produce invalid code, skip them
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
            dir=original_file.parent
        ) as tmp:
            tmp.write(mutant.mutated_code)
            tmp_path = Path(tmp.name)

        try:
            # Backup original file
            original_code = original_file.read_text()

            # Replace with mutant
            original_file.write_text(mutant.mutated_code)

            # Run tests
            result = subprocess.run(
                test_command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_per_mutant,
                cwd=original_file.parent
            )

            mutant.test_output = result.stdout + result.stderr

            # If tests fail, mutant is killed
            if result.returncode != 0:
                mutant.killed = True
            else:
                mutant.killed = False  # Mutant survived - tests didn't catch it!

        except subprocess.TimeoutExpired:
            mutant.killed = True  # Timeout counts as killed (infinite loop mutation)
            mutant.error = "Timeout"
        except Exception as e:
            mutant.error = str(e)
        finally:
            # Restore original file
            original_file.write_text(original_code)
            # Clean up temp file
            tmp_path.unlink(missing_ok=True)

        return mutant

    def run_mutation_testing(
        self,
        code_path: Path,
        test_command: str
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
        code_path = Path(code_path)

        result = MutationResult(
            code_path=str(code_path),
            test_command=test_command,
            timestamp=start_time.isoformat(),
            duration_ms=0
        )

        if not code_path.exists():
            result.success = False
            result.error = f"File not found: {code_path}"
            return result

        code = code_path.read_text()

        # Generate mutants
        mutants = self.generate_mutants(code)

        if not mutants:
            result.error = "No mutants generated (possibly invalid code or no mutable constructs)"
            result.score = MutationScore(
                total_mutants=0,
                killed_mutants=0,
                survived_mutants=0,
                error_mutants=0,
                score=1.0  # No mutants = perfect score (vacuously true)
            )
            return result

        # Sample mutants if needed
        if self.sample_ratio < 1.0:
            sample_size = max(1, int(len(mutants) * self.sample_ratio))
            mutants = random.sample(mutants, sample_size)

        # Test each mutant
        for mutant in mutants:
            self.test_mutant(mutant, test_command, code_path)

        result.mutants = mutants

        # Calculate score
        killed = sum(1 for m in mutants if m.killed and not m.error)
        errors = sum(1 for m in mutants if m.error)
        total = len(mutants)
        survived = total - killed - errors
        testable = total - errors

        score = killed / testable if testable > 0 else 1.0

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
