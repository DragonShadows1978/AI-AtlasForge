#!/usr/bin/env python3
"""
Mission Splitter: Decomposes missions into parallelizable work units

When a complex mission can be broken into independent pieces, this module
provides strategies for splitting the work so multiple Claude agents can
work in parallel.

Splitting Strategies:
1. Task-based: Multiple explicit tasks → one agent per task
2. File-based: Multiple files to modify → one agent per file (or file group)
3. Approach-based: Try different approaches in parallel, pick best
4. Section-based: Large feature → frontend/backend/tests splits
5. Phase-based: Research → Design → Implement in parallel tracks
"""

import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
import json
import hashlib


class SplitStrategy(Enum):
    """Strategies for splitting missions."""
    TASK_BASED = "task_based"         # Split by explicit tasks
    FILE_BASED = "file_based"         # Split by files to modify
    APPROACH_BASED = "approach_based" # Try different approaches
    SECTION_BASED = "section_based"   # Frontend/backend/tests
    PHASE_BASED = "phase_based"       # Research/design/implement
    AUTO = "auto"                     # Automatic detection


@dataclass
class WorkUnit:
    """
    Represents a unit of work that can be assigned to an agent.

    Attributes:
        id: Unique identifier
        description: What this work unit should accomplish
        prompt: The full prompt to give the agent
        dependencies: IDs of work units that must complete first
        estimated_complexity: 1-10 scale, affects timeout allocation
        files: Expected files to be created/modified
        strategy: Which splitting strategy produced this unit
        metadata: Additional context
    """
    id: str
    description: str
    prompt: str
    dependencies: List[str] = field(default_factory=list)
    estimated_complexity: int = 5
    files: List[str] = field(default_factory=list)
    strategy: SplitStrategy = SplitStrategy.AUTO
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "description": self.description,
            "prompt": self.prompt,
            "dependencies": self.dependencies,
            "estimated_complexity": self.estimated_complexity,
            "files": self.files,
            "strategy": self.strategy.value,
            "metadata": self.metadata
        }
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'WorkUnit':
        data['strategy'] = SplitStrategy(data.get('strategy', 'auto'))
        return cls(**data)


class MissionSplitter:
    """
    Splits missions into parallelizable work units.

    Usage:
        splitter = MissionSplitter()

        # Auto-detect best strategy
        work_units = splitter.split(mission_text, max_units=5)

        # Or use specific strategy
        work_units = splitter.split(
            mission_text,
            strategy=SplitStrategy.TASK_BASED,
            max_units=5
        )
    """

    def __init__(self):
        self.task_patterns = [
            r'^\d+\.\s*(.+)$',           # "1. Do something"
            r'^[-*]\s*(.+)$',             # "- Do something"
            r'^\s*Task\s*\d*:\s*(.+)$',   # "Task 1: Do something"
            r'^(?:First|Second|Third|Then|Next|Finally)[,:]?\s*(.+)$',  # Sequential words
        ]

        self.file_patterns = [
            r'`([^`]+\.(py|js|ts|tsx|jsx|md|json|yaml|yml))`',  # Backtick files
            r'\b([a-zA-Z_][a-zA-Z0-9_]*\.(py|js|ts|tsx|jsx))\b',  # Plain file mentions
        ]

        self.section_keywords = {
            'frontend': ['frontend', 'ui', 'component', 'react', 'vue', 'css', 'html'],
            'backend': ['backend', 'api', 'server', 'database', 'endpoint', 'rest'],
            'tests': ['test', 'spec', 'testing', 'unit test', 'integration'],
            'docs': ['documentation', 'readme', 'doc', 'docs'],
            'infra': ['deploy', 'ci', 'cd', 'docker', 'kubernetes', 'infrastructure'],
        }

    def split(
        self,
        mission: str,
        strategy: SplitStrategy = SplitStrategy.AUTO,
        max_units: int = 5,
        context: Optional[Dict[str, Any]] = None
    ) -> List[WorkUnit]:
        """
        Split a mission into work units.

        Args:
            mission: The mission text to split
            strategy: Which splitting strategy to use
            max_units: Maximum number of work units to create
            context: Optional context (codebase info, preferences, etc.)

        Returns:
            List of WorkUnit objects
        """
        if strategy == SplitStrategy.AUTO:
            strategy = self._detect_strategy(mission)

        if strategy == SplitStrategy.TASK_BASED:
            return self._split_by_tasks(mission, max_units, context)
        elif strategy == SplitStrategy.FILE_BASED:
            return self._split_by_files(mission, max_units, context)
        elif strategy == SplitStrategy.APPROACH_BASED:
            return self._split_by_approaches(mission, max_units, context)
        elif strategy == SplitStrategy.SECTION_BASED:
            return self._split_by_sections(mission, max_units, context)
        elif strategy == SplitStrategy.PHASE_BASED:
            return self._split_by_phases(mission, max_units, context)
        else:
            # Fallback to single unit
            return [self._create_single_unit(mission, context)]

    def _detect_strategy(self, mission: str) -> SplitStrategy:
        """Auto-detect the best splitting strategy for a mission."""
        mission_lower = mission.lower()

        # Check for explicit task lists
        tasks = self._extract_tasks(mission)
        if len(tasks) >= 2:
            return SplitStrategy.TASK_BASED

        # Check for file-heavy missions
        files = self._extract_files(mission)
        if len(files) >= 3:
            return SplitStrategy.FILE_BASED

        # Check for section keywords
        sections = self._detect_sections(mission)
        if len(sections) >= 2:
            return SplitStrategy.SECTION_BASED

        # Check for comparison/approach language
        approach_words = ['compare', 'alternative', 'approach', 'option', 'try', 'versus', 'vs']
        if any(word in mission_lower for word in approach_words):
            return SplitStrategy.APPROACH_BASED

        # Default to phase-based for complex missions
        if len(mission.split()) > 100:
            return SplitStrategy.PHASE_BASED

        # Simple mission - single unit
        return SplitStrategy.AUTO

    def _extract_tasks(self, mission: str) -> List[str]:
        """Extract explicit tasks from mission text."""
        tasks = []
        for line in mission.split('\n'):
            line = line.strip()
            for pattern in self.task_patterns:
                match = re.match(pattern, line, re.IGNORECASE)
                if match:
                    tasks.append(match.group(1).strip())
                    break
        return tasks

    def _extract_files(self, mission: str) -> List[str]:
        """Extract file references from mission text."""
        files = set()
        for pattern in self.file_patterns:
            matches = re.findall(pattern, mission)
            for match in matches:
                if isinstance(match, tuple):
                    files.add(match[0])
                else:
                    files.add(match)
        return list(files)

    def _detect_sections(self, mission: str) -> List[str]:
        """Detect which sections (frontend, backend, etc.) are mentioned."""
        mission_lower = mission.lower()
        detected = []
        for section, keywords in self.section_keywords.items():
            if any(kw in mission_lower for kw in keywords):
                detected.append(section)
        return detected

    def _generate_id(self, description: str) -> str:
        """Generate a unique ID for a work unit."""
        hash_input = description.encode()
        return f"wu_{hashlib.md5(hash_input).hexdigest()[:8]}"

    def _split_by_tasks(
        self,
        mission: str,
        max_units: int,
        context: Optional[Dict[str, Any]]
    ) -> List[WorkUnit]:
        """Split mission by explicit task list."""
        tasks = self._extract_tasks(mission)

        if not tasks:
            return [self._create_single_unit(mission, context)]

        # Limit to max_units
        if len(tasks) > max_units:
            # Group tasks
            tasks = self._group_items(tasks, max_units)

        work_units = []
        for i, task in enumerate(tasks):
            task_desc = task if isinstance(task, str) else "; ".join(task)

            prompt = self._create_task_prompt(mission, task_desc, i, len(tasks))

            wu = WorkUnit(
                id=self._generate_id(f"task_{i}_{task_desc}"),
                description=task_desc[:100],
                prompt=prompt,
                estimated_complexity=self._estimate_complexity(task_desc),
                strategy=SplitStrategy.TASK_BASED,
                metadata={"task_index": i, "original_task": task_desc}
            )
            work_units.append(wu)

        return work_units

    def _split_by_files(
        self,
        mission: str,
        max_units: int,
        context: Optional[Dict[str, Any]]
    ) -> List[WorkUnit]:
        """Split mission by files to modify."""
        files = self._extract_files(mission)

        if not files:
            return [self._create_single_unit(mission, context)]

        # Group files if too many
        if len(files) > max_units:
            files = self._group_items(files, max_units)

        work_units = []
        for i, file_group in enumerate(files):
            if isinstance(file_group, str):
                file_group = [file_group]

            files_str = ", ".join(file_group)
            prompt = self._create_file_prompt(mission, file_group, i, len(files))

            wu = WorkUnit(
                id=self._generate_id(f"files_{i}_{files_str}"),
                description=f"Modify: {files_str[:80]}",
                prompt=prompt,
                files=file_group,
                estimated_complexity=len(file_group) * 2,
                strategy=SplitStrategy.FILE_BASED,
                metadata={"file_index": i, "files": file_group}
            )
            work_units.append(wu)

        return work_units

    def _split_by_approaches(
        self,
        mission: str,
        max_units: int,
        context: Optional[Dict[str, Any]]
    ) -> List[WorkUnit]:
        """Split mission to try different approaches in parallel."""
        # Define different approaches to try
        approaches = [
            {
                "name": "conservative",
                "description": "Minimal changes, safest approach",
                "instructions": "Use the most straightforward, minimal-change approach. Prioritize safety and backward compatibility."
            },
            {
                "name": "optimized",
                "description": "Performance-focused approach",
                "instructions": "Focus on performance and efficiency. Use optimized algorithms and data structures."
            },
            {
                "name": "modern",
                "description": "Use latest patterns and practices",
                "instructions": "Use modern patterns, latest best practices, and up-to-date libraries."
            },
        ]

        # Limit to max_units
        approaches = approaches[:max_units]

        work_units = []
        for i, approach in enumerate(approaches):
            prompt = self._create_approach_prompt(mission, approach, i, len(approaches))

            wu = WorkUnit(
                id=self._generate_id(f"approach_{approach['name']}"),
                description=f"Approach: {approach['name']} - {approach['description']}",
                prompt=prompt,
                estimated_complexity=7,  # Approaches typically need full context
                strategy=SplitStrategy.APPROACH_BASED,
                metadata={"approach": approach['name']}
            )
            work_units.append(wu)

        return work_units

    def _split_by_sections(
        self,
        mission: str,
        max_units: int,
        context: Optional[Dict[str, Any]]
    ) -> List[WorkUnit]:
        """Split mission by frontend/backend/tests/etc."""
        sections = self._detect_sections(mission)

        if len(sections) < 2:
            return [self._create_single_unit(mission, context)]

        # Limit to max_units
        sections = sections[:max_units]

        work_units = []
        for i, section in enumerate(sections):
            prompt = self._create_section_prompt(mission, section, i, len(sections))

            wu = WorkUnit(
                id=self._generate_id(f"section_{section}"),
                description=f"{section.capitalize()} implementation",
                prompt=prompt,
                estimated_complexity=6,
                strategy=SplitStrategy.SECTION_BASED,
                metadata={"section": section}
            )
            work_units.append(wu)

        return work_units

    def _split_by_phases(
        self,
        mission: str,
        max_units: int,
        context: Optional[Dict[str, Any]]
    ) -> List[WorkUnit]:
        """Split mission into research/design/implement phases."""
        phases = [
            {
                "name": "research",
                "description": "Research and understand requirements",
                "dependencies": [],
            },
            {
                "name": "design",
                "description": "Design the solution architecture",
                "dependencies": ["research"],
            },
            {
                "name": "implement",
                "description": "Implement the solution",
                "dependencies": ["design"],
            },
        ]

        # Limit to max_units
        phases = phases[:max_units]

        work_units = []
        phase_ids = {}

        for i, phase in enumerate(phases):
            phase_id = self._generate_id(f"phase_{phase['name']}")
            phase_ids[phase['name']] = phase_id

            # Resolve dependencies
            deps = [phase_ids.get(d) for d in phase.get('dependencies', []) if d in phase_ids]

            prompt = self._create_phase_prompt(mission, phase, i, len(phases))

            wu = WorkUnit(
                id=phase_id,
                description=f"Phase: {phase['description']}",
                prompt=prompt,
                dependencies=deps,
                estimated_complexity=5,
                strategy=SplitStrategy.PHASE_BASED,
                metadata={"phase": phase['name']}
            )
            work_units.append(wu)

        return work_units

    def _create_single_unit(
        self,
        mission: str,
        context: Optional[Dict[str, Any]]
    ) -> WorkUnit:
        """Create a single work unit for the entire mission."""
        return WorkUnit(
            id=self._generate_id(mission[:100]),
            description="Complete mission",
            prompt=self._create_full_prompt(mission),
            estimated_complexity=self._estimate_complexity(mission),
            strategy=SplitStrategy.AUTO,
            metadata={"full_mission": True}
        )

    def _group_items(self, items: List[str], n: int) -> List[List[str]]:
        """Group items into n groups."""
        groups = [[] for _ in range(n)]
        for i, item in enumerate(items):
            groups[i % n].append(item)
        return [g for g in groups if g]  # Remove empty groups

    def _estimate_complexity(self, text: str) -> int:
        """Estimate complexity on 1-10 scale."""
        word_count = len(text.split())
        if word_count < 20:
            return 2
        elif word_count < 50:
            return 4
        elif word_count < 100:
            return 6
        elif word_count < 200:
            return 8
        else:
            return 10

    # Prompt creation methods
    def _create_task_prompt(
        self,
        mission: str,
        task: str,
        index: int,
        total: int
    ) -> str:
        return f"""# Mission Context
{mission}

# Your Specific Task
You are responsible for task {index + 1} of {total}:
**{task}**

Focus ONLY on this specific task. Do not implement other tasks.

# Instructions
1. Complete the task described above
2. Document what you did
3. Return results in the expected format

# Response Format
Return JSON with:
{{
    "status": "completed" | "failed",
    "task": "{task[:50]}...",
    "files_modified": [],
    "files_created": [],
    "summary": "What was accomplished",
    "issues": []
}}
"""

    def _create_file_prompt(
        self,
        mission: str,
        files: List[str],
        index: int,
        total: int
    ) -> str:
        files_str = "\n".join(f"- {f}" for f in files)
        return f"""# Mission Context
{mission}

# Your Specific Files
You are responsible for file group {index + 1} of {total}:
{files_str}

Focus ONLY on these files. Do not modify other files.

# Instructions
1. Implement the required changes for your assigned files
2. Ensure changes are complete and tested
3. Document what you did

# Response Format
Return JSON with:
{{
    "status": "completed" | "failed",
    "files": {json.dumps(files)},
    "changes": ["list of changes made"],
    "summary": "What was accomplished",
    "issues": []
}}
"""

    def _create_approach_prompt(
        self,
        mission: str,
        approach: Dict[str, str],
        index: int,
        total: int
    ) -> str:
        return f"""# Mission Context
{mission}

# Your Approach: {approach['name'].upper()}
You are testing approach {index + 1} of {total}: **{approach['description']}**

{approach['instructions']}

# Instructions
1. Implement the solution using this approach
2. Document trade-offs and decisions
3. Note any concerns or limitations

# Response Format
Return JSON with:
{{
    "status": "completed" | "failed",
    "approach": "{approach['name']}",
    "implementation": "Description of implementation",
    "files_modified": [],
    "files_created": [],
    "pros": ["advantages of this approach"],
    "cons": ["disadvantages of this approach"],
    "recommendation": "Should this approach be used? Why?"
}}
"""

    def _create_section_prompt(
        self,
        mission: str,
        section: str,
        index: int,
        total: int
    ) -> str:
        return f"""# Mission Context
{mission}

# Your Section: {section.upper()}
You are responsible for the {section} portion of this mission (section {index + 1} of {total}).

Focus ONLY on {section} concerns. Other sections will be handled by other agents.

# Instructions
1. Implement the {section} portion of the feature
2. Define clear interfaces for other sections to integrate with
3. Document your API/interface decisions

# Response Format
Return JSON with:
{{
    "status": "completed" | "failed",
    "section": "{section}",
    "files_modified": [],
    "files_created": [],
    "interfaces": ["APIs or interfaces exposed for other sections"],
    "dependencies": ["What this section needs from other sections"],
    "summary": "What was implemented"
}}
"""

    def _create_phase_prompt(
        self,
        mission: str,
        phase: Dict[str, Any],
        index: int,
        total: int
    ) -> str:
        return f"""# Mission Context
{mission}

# Your Phase: {phase['name'].upper()}
You are responsible for the {phase['name']} phase (phase {index + 1} of {total}).
{phase['description']}

# Instructions
1. Complete the {phase['name']} phase
2. Produce artifacts for the next phase
3. Document decisions and rationale

# Response Format
Return JSON with:
{{
    "status": "completed" | "failed",
    "phase": "{phase['name']}",
    "artifacts": ["files or outputs produced"],
    "findings": ["key findings or decisions"],
    "next_steps": ["recommendations for next phase"],
    "summary": "What was accomplished"
}}
"""

    def _create_full_prompt(self, mission: str) -> str:
        return f"""# Mission
{mission}

# Instructions
Complete the entire mission as described above.

# Response Format
Return JSON with:
{{
    "status": "completed" | "failed",
    "files_modified": [],
    "files_created": [],
    "summary": "What was accomplished",
    "issues": []
}}
"""


# Utility function for quick splitting
def split_mission(
    mission: str,
    max_units: int = 5,
    strategy: str = "auto"
) -> List[WorkUnit]:
    """
    Quick utility to split a mission.

    Args:
        mission: Mission text
        max_units: Maximum work units
        strategy: Strategy name or "auto"

    Returns:
        List of WorkUnit objects
    """
    splitter = MissionSplitter()
    strat = SplitStrategy(strategy) if strategy != "auto" else SplitStrategy.AUTO
    return splitter.split(mission, strategy=strat, max_units=max_units)


if __name__ == "__main__":
    # Self-test
    print("Mission Splitter - Self Test")
    print("=" * 50)

    # Test mission with explicit tasks
    task_mission = """
    Implement user authentication:
    1. Create login form component
    2. Add API endpoint for authentication
    3. Implement session management
    4. Write unit tests
    5. Update documentation
    """

    splitter = MissionSplitter()
    units = splitter.split(task_mission, max_units=3)

    print(f"\nTask-based mission split into {len(units)} units:")
    for wu in units:
        print(f"  - {wu.id}: {wu.description}")

    # Test file-based mission
    file_mission = """
    Refactor the following files to use TypeScript:
    - `utils.js`
    - `api.js`
    - `components/Header.js`
    - `components/Footer.js`
    """

    units = splitter.split(file_mission, max_units=2)
    print(f"\nFile-based mission split into {len(units)} units:")
    for wu in units:
        print(f"  - {wu.id}: {wu.description}")
        print(f"    Files: {wu.files}")

    # Test approach-based
    approach_mission = """
    Compare different caching approaches for the API.
    Try in-memory, Redis, and file-based caching.
    """

    units = splitter.split(approach_mission, strategy=SplitStrategy.APPROACH_BASED, max_units=3)
    print(f"\nApproach-based mission split into {len(units)} units:")
    for wu in units:
        print(f"  - {wu.id}: {wu.description}")

    print("\nMission Splitter self-test complete!")
