#!/usr/bin/env python3
"""
MUTATION TEST 1: CONNECTIVITY RECONCILIATION

Mutant: Comment out _reconcile_connectivity call at line 612 of procgen.py
Expected result: Tests KILL the mutant by detecting orphaned/unreachable cells.
"""

import sys
import shutil
from pathlib import Path
import subprocess
import tempfile

def test_connectivity_mutant():
    """Test if connectivity tests catch a disabled _reconcile_connectivity."""

    procgen_path = Path("/mnt/ForgeRealm/AI-AtlasForge/workspace/Project-Labrinth_Game/mission_8db9afbf/labyrinth/engine/procgen.py")

    # Backup the original
    backup_path = procgen_path.with_suffix('.py.bak')
    shutil.copy(procgen_path, backup_path)

    try:
        # Read and mutate
        content = procgen_path.read_text()
        original_line = "    keep = _reconcile_connectivity(grid, start_pos, protected)"

        if original_line not in content:
            print(f"ERROR: Could not find the target line in procgen.py")
            return False

        mutated_content = content.replace(
            original_line,
            "    # MUTANT: disabled _reconcile_connectivity\n    # keep = _reconcile_connectivity(grid, start_pos, protected)\n    keep = set()  # BUG: never seals orphans"
        )

        procgen_path.write_text(mutated_content)
        print("[MUTANT APPLIED] _reconcile_connectivity disabled - orphan sealing is now broken")

        # Run tests
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "labyrinth/tests/test_procgen.py::test_floor_is_solvable",
             "-x", "-q", "--tb=line"],
            capture_output=True,
            text=True,
            cwd="/mnt/ForgeRealm/AI-AtlasForge/workspace/Project-Labrinth_Game/mission_8db9afbf",
            timeout=120
        )

        passed = result.returncode == 0

        print(f"\n[TEST RESULTS]")
        print(f"Exit code: {result.returncode}")
        print(f"Tests passed: {passed}")

        if passed:
            print("\n[FINDING: WEAK TESTS]")
            print("The connectivity tests SURVIVED the mutant!")
            print("Tests did not detect that _reconcile_connectivity was disabled.")
            return False
        else:
            print("\n[FINDING: STRONG TESTS]")
            print("The connectivity tests KILLED the mutant!")
            print("Tests caught the missing connectivity reconciliation.")
            print("\nTest output (last 500 chars):")
            print((result.stdout + result.stderr)[-500:])
            return True

    finally:
        # Restore original
        shutil.copy(backup_path, procgen_path)
        backup_path.unlink()
        print("\n[RESTORED] Original procgen.py restored from backup")

if __name__ == "__main__":
    print("=" * 80)
    print("MUTATION TEST 1: CONNECTIVITY RECONCILIATION")
    print("=" * 80)
    result = test_connectivity_mutant()
    print("=" * 80)
    print(f"VERDICT: {'KILLED' if result else 'SURVIVED'}")
    print("=" * 80)
