#!/usr/bin/env python3
"""
Load Sample Mission Script
https://github.com/DragonShadows1978/AI-AtlasForge

Loads the hello_world sample mission to help new users get started.

Usage:
    python3 scripts/load_sample_mission.py
    # or
    make sample-mission
"""

import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

# Colors for terminal output
class Colors:
    BLUE = '\033[0;34m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    RED = '\033[0;31m'
    NC = '\033[0m'  # No Color

def log_info(msg):
    print(f"{Colors.BLUE}[INFO]{Colors.NC} {msg}")

def log_success(msg):
    print(f"{Colors.GREEN}[OK]{Colors.NC} {msg}")

def log_warning(msg):
    print(f"{Colors.YELLOW}[WARN]{Colors.NC} {msg}")

def log_error(msg):
    print(f"{Colors.RED}[ERROR]{Colors.NC} {msg}")

def main():
    # Determine paths
    script_dir = Path(__file__).resolve().parent
    atlasforge_root = script_dir.parent

    sample_mission_path = atlasforge_root / "examples" / "hello_world_mission.json"
    state_dir = atlasforge_root / "state"
    mission_json_path = state_dir / "mission.json"

    # Check if sample mission exists
    if not sample_mission_path.exists():
        log_error(f"Sample mission not found at: {sample_mission_path}")
        sys.exit(1)

    # Create state directory if needed
    state_dir.mkdir(parents=True, exist_ok=True)

    # Check if a mission already exists
    if mission_json_path.exists():
        log_warning("A mission already exists at state/mission.json")
        response = input("Do you want to replace it with the sample mission? [y/N] ").strip().lower()
        if response != 'y':
            log_info("Keeping existing mission. Exiting.")
            sys.exit(0)

        # Backup existing mission
        backup_path = state_dir / f"mission_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(mission_json_path, 'r') as f:
            existing_mission = json.load(f)
        with open(backup_path, 'w') as f:
            json.dump(existing_mission, f, indent=2)
        log_info(f"Backed up existing mission to: {backup_path}")

    # Load sample mission template
    log_info("Loading sample mission template...")
    with open(sample_mission_path, 'r') as f:
        sample_data = json.load(f)

    # Generate mission ID
    mission_id = f"mission_{uuid.uuid4().hex[:8]}"

    # Create full mission structure
    mission = {
        "mission_id": mission_id,
        "problem_statement": sample_data["problem_statement"],
        "original_problem_statement": sample_data["problem_statement"],
        "preferences": sample_data.get("preferences", {}),
        "success_criteria": sample_data.get("success_criteria", []),
        "current_stage": "PLANNING",
        "iteration": 0,
        "max_iterations": 10,
        "artifacts": {
            "plan": None,
            "code": [],
            "tests": []
        },
        "history": [],
        "created_at": datetime.now().isoformat(),
        "last_updated": datetime.now().isoformat(),
        "cycle_started_at": datetime.now().isoformat(),
        "cycle_budget": sample_data.get("cycle_budget", 1),
        "current_cycle": 1,
        "cycle_history": [],
        "mission_workspace": str(atlasforge_root / "missions" / mission_id / "workspace"),
        "mission_dir": str(atlasforge_root / "missions" / mission_id),
        "metadata": sample_data.get("metadata", {})
    }

    # Create mission workspace directory
    mission_workspace = Path(mission["mission_workspace"])
    mission_workspace.mkdir(parents=True, exist_ok=True)
    (mission_workspace / "artifacts").mkdir(exist_ok=True)
    (mission_workspace / "research").mkdir(exist_ok=True)
    (mission_workspace / "tests").mkdir(exist_ok=True)

    # Write mission file
    with open(mission_json_path, 'w') as f:
        json.dump(mission, f, indent=2)

    log_success(f"Sample mission loaded successfully!")
    print()
    print(f"  Mission ID:     {Colors.BLUE}{mission_id}{Colors.NC}")
    print(f"  Mission file:   {Colors.BLUE}{mission_json_path}{Colors.NC}")
    print(f"  Workspace:      {Colors.BLUE}{mission_workspace}{Colors.NC}")
    print()
    print(f"{Colors.YELLOW}Mission objective:{Colors.NC}")
    print(f"  {sample_data['problem_statement'][:100]}...")
    print()
    print(f"{Colors.GREEN}Next steps:{Colors.NC}")
    print(f"  1. Start the dashboard:  {Colors.YELLOW}make dashboard{Colors.NC}")
    print(f"  2. In another terminal:  {Colors.YELLOW}make run{Colors.NC}")
    print()
    print("  The agent will create a simple hello_atlasforge.py script.")
    print("  Watch the dashboard at http://localhost:5050 to see progress.")
    print()

if __name__ == "__main__":
    main()
