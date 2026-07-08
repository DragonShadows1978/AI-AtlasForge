#!/usr/bin/env python3
"""
MUTATION TEST 3: HUNGER CLOCK DECREMENT

Mutant: Remove hunger decrement (comment out `self.player.hunger -= B.HUNGER_PER_STEP`)
Expected result: Tests KILL the mutant because hunger never decreases.
"""

import sys
import shutil
from pathlib import Path
import subprocess

def test_hunger_mutant():
    """Test if explore tests catch disabled hunger decrement."""

    explore_path = Path("/mnt/ForgeRealm/AI-AtlasForge/workspace/Project-Labrinth_Game/mission_8db9afbf/labyrinth/engine/explore.py")

    # Backup original
    backup_path = explore_path.with_suffix('.py.bak')
    shutil.copy(explore_path, backup_path)

    try:
        # Read and mutate
        content = explore_path.read_text()

        # Find and mutate the hunger decrement
        original_line = "        self.player.hunger -= B.HUNGER_PER_STEP"
        mutated_line = "        # MUTANT: disabled hunger decrement\n        # self.player.hunger -= B.HUNGER_PER_STEP"

        if original_line not in content:
            print(f"ERROR: Could not find the target hunger decrement line")
            return False

        mutated_content = content.replace(original_line, mutated_line)
        explore_path.write_text(mutated_content)
        print("[MUTANT APPLIED] Hunger decrement disabled in _tick_hunger()")

        # Run hunger tests
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "labyrinth/tests/test_explore.py::test_successful_step_decrements_hunger_by_exactly_one_step",
             "labyrinth/tests/test_explore.py::test_hunger_is_monotonic_non_increasing_over_many_steps_and_reaches_zero",
             "labyrinth/tests/test_explore.py::test_step_at_zero_hunger_drains_hp_by_starve_damage",
             "-x", "-v", "--tb=line"],
            capture_output=True,
            text=True,
            cwd="/mnt/ForgeRealm/AI-AtlasForge/workspace/Project-Labrinth_Game/mission_8db9afbf",
            timeout=60
        )

        passed = result.returncode == 0

        print(f"\n[TEST RESULTS]")
        print(f"Exit code: {result.returncode}")
        print(f"Tests passed: {passed}")

        if passed:
            print("\n[FINDING: WEAK TESTS]")
            print("The hunger tests SURVIVED the mutant!")
            print("Tests did not detect that hunger decrement was disabled.")
            return False
        else:
            print("\n[FINDING: STRONG TESTS]")
            print("The hunger tests KILLED the mutant!")
            print("Tests caught the missing hunger decrement.")
            print("\nTest output (last 600 chars):")
            output = result.stdout + result.stderr
            print(output[-600:])
            return True

    finally:
        # Restore original
        shutil.copy(backup_path, explore_path)
        backup_path.unlink()
        print("\n[RESTORED] Original explore.py restored from backup")

if __name__ == "__main__":
    print("=" * 80)
    print("MUTATION TEST 3: HUNGER CLOCK DECREMENT")
    print("=" * 80)
    result = test_hunger_mutant()
    print("=" * 80)
    print(f"VERDICT: {'KILLED' if result else 'SURVIVED'}")
    print("=" * 80)
