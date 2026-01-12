#!/usr/bin/env python3
"""
Phase-Aware Drift Detection Module

This module implements phase-aware drift validation to prevent false positives
when agents correctly progress through sequential mission phases.

The key insight: Multi-phase missions (e.g., "Phase 2A, then 2B, then 2C")
should only compare agent work against the ACTIVE phase, not the entire
mission specification. As phases are completed, similarity should be computed
against the current phase objectives only.

Components:
1. PhaseExtractor - Extracts phases from mission text (LLM or heuristics)
2. PhaseCompletionDetector - Detects when phases are complete
3. PhaseAwareComparator - Computes similarity against active phase only
4. AccompanyingDocsDiscovery - Finds related mission documents
"""

import re
import json
import hashlib
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from enum import Enum
import sys

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


class PhaseStatus(Enum):
    """Status of a mission phase."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


@dataclass
class MissionPhase:
    """
    Represents a discrete phase within a mission.

    Attributes:
        phase_id: Unique identifier for the phase
        name: Human-readable phase name (e.g., "Phase 2A", "Step 1")
        objectives: List of objectives for this phase
        success_criteria: Criteria to determine phase completion
        depends_on: List of phase_ids that must complete before this phase
        status: Current status of the phase
        started_at: When the phase was started
        completed_at: When the phase was completed
        completion_evidence: Evidence/markers of completion
        raw_text: Original text describing this phase
    """
    phase_id: str
    name: str
    objectives: List[str]
    success_criteria: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    status: PhaseStatus = PhaseStatus.PENDING
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    completion_evidence: List[str] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d['status'] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'MissionPhase':
        if isinstance(data.get('status'), str):
            data['status'] = PhaseStatus(data['status'])
        return cls(**data)

    def mark_started(self):
        """Mark this phase as in progress."""
        if self.status == PhaseStatus.PENDING:
            self.status = PhaseStatus.IN_PROGRESS
            self.started_at = datetime.now().isoformat()

    def mark_completed(self, evidence: List[str] = None):
        """Mark this phase as completed with optional evidence."""
        self.status = PhaseStatus.COMPLETED
        self.completed_at = datetime.now().isoformat()
        if evidence:
            self.completion_evidence.extend(evidence)

    def get_combined_text(self) -> str:
        """Get combined text of objectives and criteria for comparison."""
        parts = [self.name, self.raw_text]
        parts.extend(self.objectives)
        parts.extend(self.success_criteria)
        return '\n'.join(filter(None, parts))


@dataclass
class PhaseTrackingState:
    """
    Persistent state for tracking phase progression in a mission.

    This state persists across cycles and mission restarts.
    """
    mission_id: str
    phases: List[MissionPhase] = field(default_factory=list)
    active_phase_id: Optional[str] = None
    completed_phase_ids: List[str] = field(default_factory=list)
    phase_transitions: List[Dict] = field(default_factory=list)
    extraction_method: str = "heuristic"  # "heuristic", "llm", "manual"
    extracted_at: Optional[str] = None
    last_updated: Optional[str] = None
    accompanying_docs: List[str] = field(default_factory=list)
    accompanying_docs_content: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'mission_id': self.mission_id,
            'phases': [p.to_dict() for p in self.phases],
            'active_phase_id': self.active_phase_id,
            'completed_phase_ids': self.completed_phase_ids,
            'phase_transitions': self.phase_transitions,
            'extraction_method': self.extraction_method,
            'extracted_at': self.extracted_at,
            'last_updated': self.last_updated,
            'accompanying_docs': self.accompanying_docs,
            'accompanying_docs_content': self.accompanying_docs_content
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'PhaseTrackingState':
        phases = [MissionPhase.from_dict(p) for p in data.get('phases', [])]
        return cls(
            mission_id=data.get('mission_id', ''),
            phases=phases,
            active_phase_id=data.get('active_phase_id'),
            completed_phase_ids=data.get('completed_phase_ids', []),
            phase_transitions=data.get('phase_transitions', []),
            extraction_method=data.get('extraction_method', 'heuristic'),
            extracted_at=data.get('extracted_at'),
            last_updated=data.get('last_updated'),
            accompanying_docs=data.get('accompanying_docs', []),
            accompanying_docs_content=data.get('accompanying_docs_content', {})
        )

    def get_active_phase(self) -> Optional[MissionPhase]:
        """Get the currently active phase."""
        if not self.active_phase_id:
            return None
        for phase in self.phases:
            if phase.phase_id == self.active_phase_id:
                return phase
        return None

    def get_phase_by_id(self, phase_id: str) -> Optional[MissionPhase]:
        """Get a phase by its ID."""
        for phase in self.phases:
            if phase.phase_id == phase_id:
                return phase
        return None

    def get_incomplete_phases(self) -> List[MissionPhase]:
        """Get all incomplete phases in order."""
        return [p for p in self.phases if p.status not in (PhaseStatus.COMPLETED, PhaseStatus.SKIPPED)]

    def get_next_eligible_phase(self) -> Optional[MissionPhase]:
        """Get the next phase that has all dependencies met."""
        for phase in self.phases:
            if phase.status in (PhaseStatus.COMPLETED, PhaseStatus.SKIPPED):
                continue
            # Check dependencies
            deps_met = all(
                dep_id in self.completed_phase_ids
                for dep_id in phase.depends_on
            )
            if deps_met:
                return phase
        return None

    def record_transition(self, from_phase: str, to_phase: str, reason: str):
        """Record a phase transition for history."""
        self.phase_transitions.append({
            'from_phase': from_phase,
            'to_phase': to_phase,
            'reason': reason,
            'timestamp': datetime.now().isoformat()
        })
        self.last_updated = datetime.now().isoformat()


class PhaseExtractor:
    """
    Extracts mission phases from mission text.

    Uses a combination of heuristic parsing and optional LLM assistance
    to identify discrete phases within a mission specification.
    """

    # Common phase patterns - ordered by specificity (most specific first)
    PHASE_PATTERNS = [
        # "=== PHASE 2A: TITLE ===" or "=== PHASE 2A - TITLE ===" (markdown/doc headers)
        r'={3,}\s*PHASE\s+(\d+[A-Za-z]):?\s*[-:]?\s*([^=\n]+)',
        # "PHASE 2A:", "Phase 2A -", "PHASE 2A - TITLE" (standard format with letters)
        r'(?:PHASE|Phase)\s+(\d+[A-Za-z])[\s:]+[-:]?\s*(.+?)(?=(?:PHASE|Phase)\s+\d+[A-Za-z]|$)',
        # "PHASE 1:", "Phase A:", "PHASE 2:" (without letters, less specific)
        r'(?:PHASE|Phase)\s+([A-Z0-9]+)[\s:]+(.+?)(?=(?:PHASE|Phase)\s+[A-Z0-9]|$)',
        # "Step 1:", "STEP 2:"
        r'(?:STEP|Step)\s+(\d+)[\s:]+(.+?)(?=(?:STEP|Step)\s+\d|$)',
        # "1.", "2.", "3." numbered sections
        r'^(\d+)\.\s*([A-Z][A-Z\s]+?)$\n(.+?)(?=^\d+\.|$)',
        # "(1) objective", "(2) objective"
        r'\((\d+)\)\s+(.+?)(?=\(\d+\)|$)',
        # "Part 1:", "PART A:"
        r'(?:PART|Part)\s+([A-Z0-9]+)[\s:]+(.+?)(?=(?:PART|Part)\s+[A-Z0-9]|$)',
        # "Milestone 1:", "Milestone: xyz"
        r'(?:MILESTONE|Milestone)\s*(\d+)?[\s:]+(.+?)(?=(?:MILESTONE|Milestone)|$)',
    ]

    # Phrases indicating phase sequencing
    SEQUENCE_INDICATORS = [
        r'then\s+(?:do|proceed|move|start)\s+',
        r'after\s+(?:completing|finishing|that|this)',
        r'once\s+(?:complete|done|finished)',
        r'next,?\s+(?:implement|create|build|add)',
        r'first,?\s+',
        r'finally,?\s+',
        r'before\s+(?:starting|moving)',
    ]

    def __init__(self):
        """Initialize the phase extractor."""
        pass

    def extract_phases(
        self,
        mission_text: str,
        use_llm: bool = False
    ) -> Tuple[List[MissionPhase], str]:
        """
        Extract phases from mission text.

        Args:
            mission_text: The full mission specification text
            use_llm: Whether to use LLM for extraction (if heuristics fail)

        Returns:
            Tuple of (list of phases, extraction method used)
        """
        # First try heuristic extraction
        phases = self._heuristic_extract(mission_text)

        if phases:
            return phases, "heuristic"

        # If heuristics fail and LLM is enabled, try LLM extraction
        if use_llm:
            phases = self._llm_extract(mission_text)
            if phases:
                return phases, "llm"

        # Fall back to treating the whole mission as a single phase
        single_phase = MissionPhase(
            phase_id="phase_main",
            name="Main Objectives",
            objectives=self._extract_objectives(mission_text),
            raw_text=mission_text[:1000]
        )
        return [single_phase], "fallback"

    def _heuristic_extract(self, text: str) -> List[MissionPhase]:
        """
        Extract phases using pattern matching heuristics.
        """
        phases = []

        # First, try the specific "=== PHASE XY: TITLE ===" format used in OPAL-style missions
        opal_pattern = r'={3,}\s*PHASE\s+(\d+[A-Za-z])\s*[-:]+\s*([^=\n]+)'
        opal_matches = list(re.finditer(opal_pattern, text, re.IGNORECASE))

        if len(opal_matches) >= 2:
            # Find content for each phase by looking until the next phase header
            for i, match in enumerate(opal_matches):
                phase_id_num = match.group(1).strip()
                phase_title = match.group(2).strip()
                phase_name = f"Phase {phase_id_num}"

                # Get content until next phase or end
                start_pos = match.end()
                if i + 1 < len(opal_matches):
                    end_pos = opal_matches[i + 1].start()
                else:
                    end_pos = len(text)

                phase_content = text[start_pos:end_pos].strip()

                phase_id = f"phase_{phase_id_num.lower()}"

                # Extract objectives from content
                objectives = self._extract_objectives(phase_content)

                phase = MissionPhase(
                    phase_id=phase_id,
                    name=f"{phase_name}: {phase_title}",
                    objectives=objectives,
                    raw_text=phase_content[:500],
                    depends_on=[phases[-1].phase_id] if phases else []
                )
                phases.append(phase)

            if phases:
                return phases

        # Try each pattern (fallback)
        for pattern in self.PHASE_PATTERNS:
            matches = list(re.finditer(pattern, text, re.MULTILINE | re.DOTALL | re.IGNORECASE))

            if len(matches) >= 2:  # At least 2 phases found
                seen_names = set()
                for i, match in enumerate(matches):
                    groups = match.groups()

                    if len(groups) >= 2:
                        phase_name = f"Phase {groups[0].strip()}"

                        # Skip duplicate phase names
                        if phase_name in seen_names:
                            continue
                        seen_names.add(phase_name)

                        phase_content = groups[1].strip() if len(groups) > 1 else ""
                        if len(groups) > 2:
                            phase_content = (groups[1].strip() + '\n' + groups[2].strip()).strip()

                        phase_id = f"phase_{hashlib.md5(phase_name.encode()).hexdigest()[:8]}"

                        # Extract objectives from content
                        objectives = self._extract_objectives(phase_content)

                        phase = MissionPhase(
                            phase_id=phase_id,
                            name=phase_name,
                            objectives=objectives,
                            raw_text=phase_content[:500],
                            # Dependencies: each phase depends on previous
                            depends_on=[phases[-1].phase_id] if phases else []
                        )
                        phases.append(phase)

                if phases:
                    return phases

        # Try to find numbered sections
        section_pattern = r'(?:^|\n)(\d+)[.):]\s*\*?\*?([A-Z][^.\n]+)'
        section_matches = re.findall(section_pattern, text, re.MULTILINE)

        if len(section_matches) >= 2:
            for i, (num, title) in enumerate(section_matches):
                phase_id = f"phase_{num}"
                phase = MissionPhase(
                    phase_id=phase_id,
                    name=f"Step {num}: {title.strip()[:50]}",
                    objectives=[title.strip()],
                    depends_on=[phases[-1].phase_id] if phases else []
                )
                phases.append(phase)
            return phases

        # Check for explicit "THEN" sequencing
        if self._has_explicit_sequencing(text):
            phases = self._extract_sequential_phases(text)
            if phases:
                return phases

        return []

    def _has_explicit_sequencing(self, text: str) -> bool:
        """Check if text contains explicit sequencing indicators."""
        for pattern in self.SEQUENCE_INDICATORS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def _extract_sequential_phases(self, text: str) -> List[MissionPhase]:
        """Extract phases from sequentially described text."""
        phases = []

        # Split on common sequence markers
        markers = r'(?:then|after that|next|finally|first)'
        parts = re.split(markers, text, flags=re.IGNORECASE)

        if len(parts) >= 2:
            for i, part in enumerate(parts):
                part = part.strip()
                if len(part) < 20:  # Too short to be meaningful
                    continue

                phase_id = f"phase_seq_{i}"
                objectives = self._extract_objectives(part)

                phase = MissionPhase(
                    phase_id=phase_id,
                    name=f"Stage {i + 1}",
                    objectives=objectives or [part[:100]],
                    raw_text=part[:500],
                    depends_on=[phases[-1].phase_id] if phases else []
                )
                phases.append(phase)

        return phases

    def _extract_objectives(self, text: str) -> List[str]:
        """Extract objective statements from text."""
        objectives = []

        # Look for bullet points
        bullet_pattern = r'[-*•]\s*(.+?)(?=[-*•]|\n\n|$)'
        bullets = re.findall(bullet_pattern, text, re.DOTALL)

        for bullet in bullets:
            obj = bullet.strip()
            if 10 < len(obj) < 200:
                objectives.append(obj)

        # Look for numbered items
        numbered_pattern = r'(?:^|\n)\s*\d+[.)]\s*(.+?)(?=\n\s*\d+[.)]|$)'
        numbered = re.findall(numbered_pattern, text, re.DOTALL)

        for item in numbered:
            obj = item.strip()
            if 10 < len(obj) < 200 and obj not in objectives:
                objectives.append(obj)

        # If nothing found, extract key sentences
        if not objectives:
            sentences = re.split(r'[.!?]+', text)
            for sent in sentences[:5]:
                sent = sent.strip()
                if 20 < len(sent) < 150:
                    objectives.append(sent)

        return objectives[:10]  # Limit to 10 objectives

    def _llm_extract(self, mission_text: str) -> List[MissionPhase]:
        """
        Use LLM to extract phases from mission text.

        This is called when heuristic extraction fails.
        """
        try:
            from experiment_framework import invoke_fresh_claude, ModelType

            prompt = f"""Analyze this mission specification and extract the distinct phases/stages.

MISSION TEXT:
{mission_text[:2000]}

List each phase in this format (JSON array):
[
  {{
    "name": "Phase name",
    "objectives": ["objective 1", "objective 2"],
    "depends_on_name": "Previous phase name or null",
    "criteria": ["success criterion 1"]
  }}
]

Only output the JSON array, no other text. If there are no clear phases, return an empty array []."""

            response, _ = invoke_fresh_claude(
                prompt=prompt,
                model=ModelType.CLAUDE_HAIKU,
                timeout=30
            )

            # Parse response
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())

                phases = []
                name_to_id = {}

                for i, item in enumerate(data):
                    phase_id = f"phase_llm_{i}"
                    name = item.get('name', f'Phase {i+1}')
                    name_to_id[name] = phase_id

                    depends_on = []
                    dep_name = item.get('depends_on_name')
                    if dep_name and dep_name in name_to_id:
                        depends_on.append(name_to_id[dep_name])
                    elif phases:
                        depends_on.append(phases[-1].phase_id)

                    phase = MissionPhase(
                        phase_id=phase_id,
                        name=name,
                        objectives=item.get('objectives', []),
                        success_criteria=item.get('criteria', []),
                        depends_on=depends_on
                    )
                    phases.append(phase)

                return phases

        except Exception as e:
            print(f"LLM phase extraction failed: {e}")

        return []


class PhaseCompletionDetector:
    """
    Detects when mission phases are completed.

    Uses multiple signals:
    1. Explicit agent markers ("Phase X complete")
    2. File existence checks (specific files created)
    3. Test passage (pytest tests pass)
    4. Keyword analysis (objectives discussed in past tense)
    5. Success criteria matching
    """

    # Patterns indicating phase completion
    COMPLETION_PATTERNS = [
        r'(?:phase|step|stage)\s+[A-Z0-9]+\s+(?:is\s+)?(?:complete|done|finished)',
        r'completed?\s+(?:phase|step|stage)\s+[A-Z0-9]+',
        r'(?:successfully\s+)?(?:implemented|built|created|added)\s+.{5,50}',
        r'all\s+(?:\d+\s+)?tests?\s+pass(?:ing)?',
        r'✓|✅|☑|done',
        r'moving\s+(?:on\s+)?to\s+(?:phase|step|stage)\s+[A-Z0-9]+',
    ]

    def __init__(self, mission_dir: Optional[Path] = None):
        """
        Initialize the completion detector.

        Args:
            mission_dir: Path to mission directory for file checks
        """
        self.mission_dir = mission_dir

    def check_phase_completion(
        self,
        phase: MissionPhase,
        agent_output: str,
        cycle_reports: List[Dict] = None
    ) -> Tuple[bool, List[str]]:
        """
        Check if a phase appears to be completed.

        Args:
            phase: The phase to check
            agent_output: Recent agent output/continuation prompt
            cycle_reports: Previous cycle reports to analyze

        Returns:
            Tuple of (is_complete, evidence_list)
        """
        evidence = []
        completion_signals = 0

        # Check for explicit completion markers
        marker_evidence = self._check_completion_markers(phase, agent_output)
        if marker_evidence:
            evidence.extend(marker_evidence)
            completion_signals += 2  # Strong signal

        # Check if objectives appear in past tense
        objective_evidence = self._check_objectives_completed(phase, agent_output)
        if objective_evidence:
            evidence.extend(objective_evidence)
            completion_signals += 1

        # Check file existence (if mission_dir available)
        if self.mission_dir:
            file_evidence = self._check_file_markers(phase)
            if file_evidence:
                evidence.extend(file_evidence)
                completion_signals += 1

        # Check success criteria
        criteria_evidence = self._check_success_criteria(phase, agent_output, cycle_reports)
        if criteria_evidence:
            evidence.extend(criteria_evidence)
            completion_signals += 1

        # Require at least 2 signals for completion
        is_complete = completion_signals >= 2 or len(evidence) >= 2

        return is_complete, evidence

    def _check_completion_markers(
        self,
        phase: MissionPhase,
        text: str
    ) -> List[str]:
        """Check for explicit completion markers in text."""
        evidence = []

        text_lower = text.lower()
        phase_name_lower = phase.name.lower()

        # Check for phase-specific completion
        for pattern in self.COMPLETION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                # Verify it's about this phase
                if any(part in text_lower for part in phase_name_lower.split()):
                    evidence.append(f"Explicit completion marker found for {phase.name}")
                    break

        # Check for checkmarks or done markers
        if any(marker in text for marker in ['✓', '✅', '☑', '[x]', '[X]']):
            # Look for nearby phase reference
            lines = text.split('\n')
            for line in lines:
                if any(marker in line for marker in ['✓', '✅', '☑', '[x]', '[X]']):
                    if any(part in line.lower() for part in phase_name_lower.split()):
                        evidence.append(f"Checkmark completion marker for {phase.name}")

        return evidence

    def _check_objectives_completed(
        self,
        phase: MissionPhase,
        text: str
    ) -> List[str]:
        """Check if objectives are discussed as completed."""
        evidence = []
        text_lower = text.lower()

        completed_indicators = [
            'implemented', 'created', 'built', 'added', 'completed',
            'finished', 'done', 'working', 'passing', 'functional'
        ]

        for objective in phase.objectives:
            obj_words = list(re.findall(r'\b\w{4,}\b', objective.lower()))

            # Check if objective keywords appear near completion words
            if obj_words:
                for indicator in completed_indicators:
                    pattern = rf'\b{indicator}\b.{{0,50}}({'|'.join(obj_words[:3])})'
                    if re.search(pattern, text_lower):
                        evidence.append(f"Objective appears completed: {objective[:50]}")
                        break

        return evidence

    def _check_file_markers(self, phase: MissionPhase) -> List[str]:
        """Check for file-based completion evidence."""
        evidence = []

        if not self.mission_dir or not self.mission_dir.exists():
            return evidence

        # Look for phase-specific test files
        phase_num = re.search(r'(\d+[A-Za-z]?)', phase.name)
        if phase_num:
            test_patterns = [
                f"test_phase{phase_num.group(1)}*.py",
                f"test_*{phase_num.group(1)}*.py",
                f"*phase{phase_num.group(1)}*.py"
            ]

            workspace = self.mission_dir / "workspace"
            if workspace.exists():
                for pattern in test_patterns:
                    matches = list(workspace.rglob(pattern))
                    if matches:
                        evidence.append(f"Found file: {matches[0].name}")

        return evidence

    def _check_success_criteria(
        self,
        phase: MissionPhase,
        text: str,
        cycle_reports: List[Dict] = None
    ) -> List[str]:
        """Check if success criteria are met."""
        evidence = []

        for criterion in phase.success_criteria:
            criterion_lower = criterion.lower()

            # Extract key metric if present
            metric_match = re.search(r'(\d+(?:\.\d+)?)\s*(%|percent|tests?|passing)', criterion_lower)

            if metric_match:
                target = float(metric_match.group(1))

                # Look for this metric in text
                metric_pattern = rf'(\d+(?:\.\d+)?)\s*(?:%|percent|tests?|passing)'
                found = re.findall(metric_pattern, text.lower())

                for value in found:
                    if float(value) >= target:
                        evidence.append(f"Success criterion met: {criterion[:50]}")
                        break
            else:
                # Check for keyword presence
                keywords = set(re.findall(r'\b\w{5,}\b', criterion_lower))
                if keywords:
                    keyword_matches = sum(1 for kw in keywords if kw in text.lower())
                    if keyword_matches >= len(keywords) * 0.5:
                        evidence.append(f"Success criterion keywords matched: {criterion[:50]}")

        return evidence


class AccompanyingDocsDiscovery:
    """
    Discovers and extracts content from accompanying mission documents.

    Many missions reference external documents that define scope:
    - MISSION.txt - Technical specification
    - SPEC.md - API/feature specification
    - game_idea.md - Game design document
    - README.md - Project context
    """

    # Common accompanying document patterns
    DOC_PATTERNS = [
        'MISSION.txt', 'MISSION.md', 'mission.txt', 'mission.md',
        'SPEC.txt', 'SPEC.md', 'spec.txt', 'spec.md',
        'README.md', 'README.txt',
        'game_idea.md', 'game_design.md', 'design.md',
        'requirements.txt', 'requirements.md',
        'PLAN.md', 'plan.md',
        'WISHLIST.md', 'wishlist.md', 'WISHLIST.txt'
    ]

    def __init__(self, mission_dir: Optional[Path] = None):
        """
        Initialize the document discovery.

        Args:
            mission_dir: Path to mission directory
        """
        self.mission_dir = mission_dir

    def discover_docs(self, mission_text: str = "") -> List[Path]:
        """
        Discover accompanying documents.

        Args:
            mission_text: Mission text to search for document references

        Returns:
            List of paths to discovered documents
        """
        discovered = []

        # Search mission workspace
        if self.mission_dir:
            workspace = self.mission_dir / "workspace"
            search_dirs = [self.mission_dir, workspace] if workspace.exists() else [self.mission_dir]

            for search_dir in search_dirs:
                if not search_dir.exists():
                    continue

                # Check for known patterns
                for pattern in self.DOC_PATTERNS:
                    matches = list(search_dir.glob(pattern))
                    discovered.extend(matches)

                # Recursive search for .md and .txt files
                for ext in ['.md', '.txt']:
                    for doc in search_dir.rglob(f'*{ext}'):
                        # Skip common non-relevant files
                        if any(skip in str(doc) for skip in ['.git', '__pycache__', 'node_modules', 'venv']):
                            continue
                        # Only include if name suggests it's a spec/design doc
                        name_lower = doc.name.lower()
                        if any(kw in name_lower for kw in ['mission', 'spec', 'readme', 'design', 'plan', 'wish', 'req']):
                            if doc not in discovered:
                                discovered.append(doc)

        # Search for document references in mission text
        if mission_text:
            referenced = self._find_doc_references(mission_text)
            for ref in referenced:
                if self.mission_dir:
                    potential_path = self.mission_dir / "workspace" / ref
                    if potential_path.exists() and potential_path not in discovered:
                        discovered.append(potential_path)

        return discovered

    def _find_doc_references(self, text: str) -> List[str]:
        """Find document references in text."""
        references = []

        # Look for file path references
        path_pattern = r'(?:see|read|refer(?:ence)?|in|from)\s+[`\'"]?([A-Za-z_][A-Za-z0-9_./]+\.(?:md|txt))[`\'"]?'
        matches = re.findall(path_pattern, text, re.IGNORECASE)
        references.extend(matches)

        # Look for backtick-quoted filenames
        backtick_pattern = r'`([A-Za-z_][A-Za-z0-9_./]+\.(?:md|txt))`'
        matches = re.findall(backtick_pattern, text)
        references.extend(matches)

        return list(set(references))

    def extract_doc_content(
        self,
        doc_path: Path,
        max_chars: int = 5000
    ) -> str:
        """
        Extract content from a document.

        Args:
            doc_path: Path to the document
            max_chars: Maximum characters to extract

        Returns:
            Document content (truncated if necessary)
        """
        try:
            content = doc_path.read_text(encoding='utf-8')
            if len(content) > max_chars:
                # Try to truncate at a paragraph boundary
                truncated = content[:max_chars]
                last_para = truncated.rfind('\n\n')
                if last_para > max_chars * 0.7:
                    truncated = truncated[:last_para]
                return truncated + '\n...[truncated]'
            return content
        except Exception as e:
            return f"[Error reading {doc_path.name}: {e}]"

    def get_combined_mission_context(
        self,
        original_mission: str,
        discovered_docs: List[Path] = None
    ) -> str:
        """
        Combine original mission with accompanying document content.

        Args:
            original_mission: Original mission text from config
            discovered_docs: List of document paths to include

        Returns:
            Combined context string
        """
        parts = [original_mission]

        if discovered_docs is None:
            discovered_docs = self.discover_docs(original_mission)

        for doc_path in discovered_docs[:5]:  # Limit to 5 docs
            content = self.extract_doc_content(doc_path, max_chars=3000)
            if content and not content.startswith('[Error'):
                parts.append(f"\n\n--- {doc_path.name} ---\n{content}")

        return '\n'.join(parts)


class PhaseAwareComparator:
    """
    Computes drift similarity against active phase only.

    This is the core component that prevents false positives by:
    1. Comparing agent output against active phase, not full mission
    2. Including completed phases as context but not as comparison targets
    3. Incorporating accompanying document context
    """

    def __init__(self):
        """Initialize the comparator."""
        # Import prescreener for similarity computation
        try:
            from drift_prescreen import DriftPrescreener
            self.prescreener = DriftPrescreener()
        except ImportError:
            self.prescreener = None

    def compute_phase_aware_similarity(
        self,
        continuation: str,
        phase_state: PhaseTrackingState,
        original_mission: str = ""
    ) -> Dict[str, Any]:
        """
        Compute similarity with phase awareness.

        Args:
            continuation: Agent continuation prompt
            phase_state: Current phase tracking state
            original_mission: Original full mission (fallback)

        Returns:
            Dict with similarity scores and analysis
        """
        result = {
            'active_phase_similarity': 0.0,
            'full_mission_similarity': 0.0,
            'recommended_similarity': 0.0,
            'phase_context': {},
            'method': 'phase_aware',
            'comparison_target': None
        }

        active_phase = phase_state.get_active_phase()

        if active_phase:
            # Build comparison target from active phase
            comparison_text = self._build_phase_comparison_text(
                active_phase,
                phase_state.accompanying_docs_content
            )

            result['comparison_target'] = active_phase.name
            result['phase_context'] = {
                'active_phase': active_phase.name,
                'completed_phases': [p.name for p in phase_state.phases
                                     if p.phase_id in phase_state.completed_phase_ids],
                'objectives': active_phase.objectives[:5]
            }

            # Compute similarity against active phase
            if self.prescreener:
                phase_result = self.prescreener.check(comparison_text, continuation)
                result['active_phase_similarity'] = phase_result['similarity']

                # Also compute full mission similarity for reference
                if original_mission:
                    full_result = self.prescreener.check(original_mission, continuation)
                    result['full_mission_similarity'] = full_result['similarity']
            else:
                # Fallback: simple keyword overlap
                result['active_phase_similarity'] = self._simple_similarity(comparison_text, continuation)
                if original_mission:
                    result['full_mission_similarity'] = self._simple_similarity(original_mission, continuation)

            # Recommended similarity is phase-aware
            result['recommended_similarity'] = result['active_phase_similarity']

        else:
            # No active phase - use full mission
            result['method'] = 'full_mission'
            result['comparison_target'] = 'full_mission'

            if self.prescreener and original_mission:
                full_result = self.prescreener.check(original_mission, continuation)
                result['full_mission_similarity'] = full_result['similarity']
                result['recommended_similarity'] = full_result['similarity']
            elif original_mission:
                result['full_mission_similarity'] = self._simple_similarity(original_mission, continuation)
                result['recommended_similarity'] = result['full_mission_similarity']

        return result

    def _build_phase_comparison_text(
        self,
        phase: MissionPhase,
        accompanying_docs: Dict[str, str]
    ) -> str:
        """Build comparison text from phase and relevant docs."""
        parts = [phase.get_combined_text()]

        # Add relevant portions of accompanying docs
        for doc_name, content in accompanying_docs.items():
            # Search for phase references in doc content
            phase_keywords = re.findall(r'\b\w{4,}\b', phase.name.lower())
            doc_lower = content.lower()

            if any(kw in doc_lower for kw in phase_keywords):
                # Extract relevant section
                relevant = self._extract_relevant_section(content, phase.name)
                if relevant:
                    parts.append(f"[From {doc_name}]: {relevant}")

        return '\n'.join(parts)

    def _extract_relevant_section(self, content: str, phase_name: str) -> str:
        """Extract section of document relevant to the phase."""
        lines = content.split('\n')
        relevant_lines = []
        in_relevant = False

        phase_keywords = set(re.findall(r'\b\w{3,}\b', phase_name.lower()))

        for i, line in enumerate(lines):
            line_lower = line.lower()

            # Check if line is relevant to phase
            keyword_match = sum(1 for kw in phase_keywords if kw in line_lower)

            if keyword_match >= len(phase_keywords) * 0.3:
                in_relevant = True
                relevant_lines.append(line)
            elif in_relevant:
                # Include following lines until next section
                if line.startswith('#') or line.startswith('---'):
                    in_relevant = False
                else:
                    relevant_lines.append(line)
                    if len(relevant_lines) > 20:  # Limit extraction
                        break

        return '\n'.join(relevant_lines[:20])

    def _simple_similarity(self, text1: str, text2: str) -> float:
        """Simple word overlap similarity."""
        words1 = set(re.findall(r'\b\w{4,}\b', text1.lower()))
        words2 = set(re.findall(r'\b\w{4,}\b', text2.lower()))

        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        union = words1 | words2

        return len(intersection) / len(union)


# =============================================================================
# STATE PERSISTENCE
# =============================================================================

def load_phase_state(mission_dir: Path) -> Optional[PhaseTrackingState]:
    """Load phase tracking state from mission directory."""
    state_path = mission_dir / "phase_tracking_state.json"

    if state_path.exists():
        try:
            with open(state_path, 'r') as f:
                data = json.load(f)
            return PhaseTrackingState.from_dict(data)
        except Exception as e:
            print(f"Warning: Could not load phase state: {e}")

    return None


def save_phase_state(state: PhaseTrackingState, mission_dir: Path):
    """Save phase tracking state to mission directory."""
    state_path = mission_dir / "phase_tracking_state.json"
    mission_dir.mkdir(parents=True, exist_ok=True)

    state.last_updated = datetime.now().isoformat()

    try:
        with open(state_path, 'w') as f:
            json.dump(state.to_dict(), f, indent=2)
    except Exception as e:
        print(f"Warning: Could not save phase state: {e}")


def initialize_phase_tracking(
    mission_id: str,
    mission_text: str,
    mission_dir: Path,
    use_llm: bool = False
) -> PhaseTrackingState:
    """
    Initialize phase tracking for a new mission.

    This extracts phases from the mission text and discovers
    accompanying documents.
    """
    # Check for existing state
    existing = load_phase_state(mission_dir)
    if existing and existing.mission_id == mission_id and existing.phases:
        return existing

    # Extract phases
    extractor = PhaseExtractor()
    phases, method = extractor.extract_phases(mission_text, use_llm=use_llm)

    # Discover accompanying documents
    doc_discovery = AccompanyingDocsDiscovery(mission_dir)
    docs = doc_discovery.discover_docs(mission_text)

    # Extract document content
    doc_content = {}
    for doc in docs:
        content = doc_discovery.extract_doc_content(doc)
        if content and not content.startswith('[Error'):
            doc_content[doc.name] = content

    # Create state
    state = PhaseTrackingState(
        mission_id=mission_id,
        phases=phases,
        active_phase_id=phases[0].phase_id if phases else None,
        extraction_method=method,
        extracted_at=datetime.now().isoformat(),
        accompanying_docs=[str(d) for d in docs],
        accompanying_docs_content=doc_content
    )

    # Mark first phase as in progress
    if phases:
        phases[0].mark_started()

    # Save state
    save_phase_state(state, mission_dir)

    return state


# =============================================================================
# SELF TEST
# =============================================================================

if __name__ == "__main__":
    print("Phase-Aware Drift Detection - Self Test")
    print("=" * 50)

    # Test mission with clear phases
    test_mission = """
    MISSION: OPAL Phase 2 - Pixel Art Pipeline

    Complete the following phases IN ORDER:

    PHASE 2A - TILING AND DENOISING
    - Implement tiled VQ-VAE processing
    - Add edge-aware denoising
    - Create pixelize post-processor
    Success: 95% edge sharpness metric

    PHASE 2B - PRIOR TRAINING
    - Train autoregressive prior model
    - Implement temperature scheduling
    - Add conditional generation
    Success: val_loss < 2.0

    PHASE 2C - BATCH GENERATION
    - Create batch generation script
    - Add diversity metrics
    - Implement metadata logging
    Success: 100+ samples generated

    After 2A is complete, move to 2B.
    After 2B is complete, move to 2C.
    """

    print("\nTest 1: Phase extraction")
    extractor = PhaseExtractor()
    phases, method = extractor.extract_phases(test_mission)
    print(f"  Extraction method: {method}")
    print(f"  Phases found: {len(phases)}")
    for p in phases:
        print(f"    - {p.name}: {len(p.objectives)} objectives")

    print("\nTest 2: Phase completion detection")
    detector = PhaseCompletionDetector()

    if phases:
        # Simulate completion text
        completion_text = """
        Phase 2A is complete! All tests passing.
        Implemented tiled processing with edge-aware denoising.
        Edge sharpness metric: 98.3%
        ✓ Moving on to Phase 2B
        """

        is_complete, evidence = detector.check_phase_completion(phases[0], completion_text)
        print(f"  Phase {phases[0].name} complete: {is_complete}")
        print(f"  Evidence: {evidence}")

    print("\nTest 3: Phase-aware similarity")
    comparator = PhaseAwareComparator()

    # Create mock phase state
    state = PhaseTrackingState(
        mission_id="test_mission",
        phases=phases,
        active_phase_id=phases[1].phase_id if len(phases) > 1 else phases[0].phase_id,
        completed_phase_ids=[phases[0].phase_id] if phases else []
    )

    # Continuation working on Phase 2B (should be high similarity)
    phase2b_continuation = """
    Continue with Phase 2B - Prior Training:
    - Complete the autoregressive prior model
    - Implement linear temperature scheduling
    - Test conditional generation with reference images
    Target: val_loss below 2.0
    """

    result = comparator.compute_phase_aware_similarity(phase2b_continuation, state, test_mission)
    print(f"  Active phase: {result['comparison_target']}")
    print(f"  Phase similarity: {result['active_phase_similarity']:.3f}")
    print(f"  Full mission similarity: {result['full_mission_similarity']:.3f}")
    print(f"  Recommended: {result['recommended_similarity']:.3f}")

    print("\nSelf test complete!")
