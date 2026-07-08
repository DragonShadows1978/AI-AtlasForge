#!/usr/bin/env python3
"""
MUTATION TEST 2: COMBAT DAMAGE FORMULA

Mutant: Return 0 damage always (enemy never takes damage)
Changed: `compute_physical_damage` returns (0, crit) instead of (max(...), crit)
Expected result: Tests KILL the mutant because battles never end.
"""

import sys
import shutil
from pathlib import Path
import subprocess

def test_damage_mutant():
    """Test if combat tests catch zero-damage mutation."""

    combat_types_path = Path("/mnt/ForgeRealm/AI-AtlasForge/workspace/Project-Labrinth_Game/mission_8db9afbf/labyrinth/engine/combat_types.py")

    # Backup original
    backup_path = combat_types_path.with_suffix('.py.bak')
    shutil.copy(combat_types_path, backup_path)

    try:
        # Read and mutate
        content = combat_types_path.read_text()

        # Mutate the return statement in compute_physical_damage to always return 0
        original_return = "    return max(B.MIN_DAMAGE, int(dmg)), crit"
        mutated_return = "    return 0, crit  # MUTANT: always zero damage"

        if original_return not in content:
            print(f"ERROR: Could not find the target return statement")
            return False

        mutated_content = content.replace(original_return, mutated_return)
        combat_types_path.write_text(mutated_content)
        print("[MUTANT APPLIED] compute_physical_damage now returns 0 damage always")

        # Run combat tests
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "labyrinth/tests/test_combat.py::test_defend_halves_incoming_damage_in_battle",
             "labyrinth/tests/test_combat.py::test_victory_economy_and_survivor_carryback",
             "labyrinth/tests/test_combat.py::test_defeat_when_player_dies",
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
            print("The combat tests SURVIVED the mutant!")
            print("Tests did not detect that damage was set to 0.")
            return False
        else:
            print("\n[FINDING: STRONG TESTS]")
            print("The combat tests KILLED the mutant!")
            print("Tests caught the zero-damage bug.")
            print("\nTest output (last 600 chars):")
            output = result.stdout + result.stderr
            print(output[-600:])
            return True

    finally:
        # Restore original
        shutil.copy(backup_path, combat_types_path)
        backup_path.unlink()
        print("\n[RESTORED] Original combat_types.py restored from backup")

if __name__ == "__main__":
    print("=" * 80)
    print("MUTATION TEST 2: COMBAT DAMAGE FORMULA")
    print("=" * 80)
    result = test_damage_mutant()
    print("=" * 80)
    print(f"VERDICT: {'KILLED' if result else 'SURVIVED'}")
    print("=" * 80)
