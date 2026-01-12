#!/usr/bin/env python3
"""
Edge Detector - Automatic relationship detection between explored files.

Detects edges by analyzing tool invocation patterns:
- grep_to_read: Grep results leading to Read calls
- explored_next: Sequential file reads within time window
- import: Python import statements
- reference: File path references in content
- test_of: Test file to source file relationships

Part of the Interactive Exploration Graph Enhancement mission.
"""

import ast
import re
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set
from collections import deque


# =============================================================================
# EDGE DETECTION CONTEXT
# =============================================================================

@dataclass
class ToolEvent:
    """Record of a tool invocation for edge detection."""
    tool_name: str
    file_path: Optional[str]
    timestamp: datetime
    input_summary: Dict
    output_summary: Dict
    files_matched: List[str] = field(default_factory=list)  # For Grep results


class EdgeDetectionContext:
    """
    Tracks recent tool invocations for edge detection.

    Maintains a sliding window of recent events to detect
    relationships between file explorations.
    """

    def __init__(self, max_age_seconds: int = 120, max_events: int = 100):
        self.max_age_seconds = max_age_seconds
        self.max_events = max_events
        self.events: deque = deque(maxlen=max_events)
        self._lock = threading.Lock()

    def add_event(self, event: ToolEvent):
        """Add a new tool event."""
        with self._lock:
            self.events.append(event)
            self._cleanup_old()

    def _cleanup_old(self):
        """Remove events older than max_age_seconds."""
        cutoff = datetime.now() - timedelta(seconds=self.max_age_seconds)
        while self.events and self.events[0].timestamp < cutoff:
            self.events.popleft()

    def get_recent_reads(self, within_seconds: int = 30) -> List[ToolEvent]:
        """Get recent Read events within time window."""
        with self._lock:
            cutoff = datetime.now() - timedelta(seconds=within_seconds)
            return [e for e in self.events
                    if e.tool_name == "Read" and e.timestamp >= cutoff]

    def get_recent_greps(self, within_seconds: int = 60) -> List[ToolEvent]:
        """Get recent Grep events within time window."""
        with self._lock:
            cutoff = datetime.now() - timedelta(seconds=within_seconds)
            return [e for e in self.events
                    if e.tool_name == "Grep" and e.timestamp >= cutoff]

    def get_previous_read(self) -> Optional[ToolEvent]:
        """Get the most recent Read event."""
        with self._lock:
            for event in reversed(self.events):
                if event.tool_name == "Read" and event.file_path:
                    return event
        return None

    def clear(self):
        """Clear all events."""
        with self._lock:
            self.events.clear()


# Global context for edge detection
_edge_context = EdgeDetectionContext()


def get_edge_context() -> EdgeDetectionContext:
    """Get the global edge detection context."""
    return _edge_context


# =============================================================================
# EDGE DETECTION LOGIC
# =============================================================================

@dataclass
class DetectedEdge:
    """A detected edge between two files."""
    source_path: str
    target_path: str
    relationship: str  # grep_to_read, explored_next, import, reference, test_of
    strength: float  # 0.0 to 1.0
    context: str  # Description of how the edge was detected
    timestamp: datetime = field(default_factory=datetime.now)


class EdgeDetector:
    """
    Detects relationships between explored files.

    Edge types:
    - grep_to_read: Grep results leading to Read calls
    - explored_next: Sequential file reads
    - import: Python import statements
    - reference: File path references in content
    - test_of: Test file to source file relationships
    """

    # Patterns for test file detection
    TEST_PATTERNS = [
        (r'^test_(.+)\.py$', r'\1.py'),  # test_foo.py -> foo.py
        (r'^(.+)_test\.py$', r'\1.py'),  # foo_test.py -> foo.py
        (r'^tests/test_(.+)\.py$', r'\1.py'),  # tests/test_foo.py -> foo.py
        (r'^tests/(.+)_test\.py$', r'\1.py'),  # tests/foo_test.py -> foo.py
    ]

    def __init__(self, context: EdgeDetectionContext = None):
        self.context = context or get_edge_context()
        self.explored_files: Set[str] = set()  # Track known files

    def record_tool_event(
        self,
        tool_name: str,
        input_summary: Dict,
        output_summary: Dict = None
    ) -> List[DetectedEdge]:
        """
        Record a tool invocation and detect any edges.

        Returns list of detected edges.
        """
        output_summary = output_summary or {}
        file_path = None
        files_matched = []

        # Extract file path based on tool type
        if tool_name == "Read":
            file_path = input_summary.get("file_path")
        elif tool_name in ("Write", "Edit"):
            file_path = input_summary.get("file_path")
        elif tool_name == "Grep":
            # Grep results may include matched files in output
            files_matched = output_summary.get("files_matched", [])

        # Create event
        event = ToolEvent(
            tool_name=tool_name,
            file_path=file_path,
            timestamp=datetime.now(),
            input_summary=input_summary,
            output_summary=output_summary,
            files_matched=files_matched
        )

        # Detect edges before adding event
        edges = self._detect_edges(event)

        # Add event to context
        self.context.add_event(event)

        # Track explored files
        if file_path:
            self.explored_files.add(file_path)

        return edges

    def _detect_edges(self, event: ToolEvent) -> List[DetectedEdge]:
        """Detect edges based on the new event."""
        edges = []

        if event.tool_name == "Read" and event.file_path:
            # Pattern 1: grep_to_read
            edges.extend(self._detect_grep_to_read(event))

            # Pattern 2: explored_next
            edge = self._detect_explored_next(event)
            if edge:
                edges.append(edge)

        return edges

    def _detect_grep_to_read(self, read_event: ToolEvent) -> List[DetectedEdge]:
        """Detect if a Read was triggered by previous Grep results."""
        edges = []
        recent_greps = self.context.get_recent_greps(within_seconds=60)

        for grep_event in recent_greps:
            # Check if the read file was in grep results
            if read_event.file_path in grep_event.files_matched:
                pattern = grep_event.input_summary.get("pattern", "")
                edge = DetectedEdge(
                    source_path=read_event.file_path,  # The file we're reading
                    target_path=read_event.file_path,  # Self-reference (grep found it)
                    relationship="grep_to_read",
                    strength=0.8,
                    context=f"Found via grep pattern: {pattern[:50]}"
                )
                edges.append(edge)

                # Also create edge from any previously read file that led to this grep
                prev_read = self._get_read_before_grep(grep_event)
                if prev_read and prev_read.file_path != read_event.file_path:
                    edge = DetectedEdge(
                        source_path=prev_read.file_path,
                        target_path=read_event.file_path,
                        relationship="grep_to_read",
                        strength=0.9,
                        context=f"Grep '{pattern[:30]}' led from {Path(prev_read.file_path).name}"
                    )
                    edges.append(edge)

        return edges

    def _get_read_before_grep(self, grep_event: ToolEvent) -> Optional[ToolEvent]:
        """Get the Read event that occurred just before a Grep."""
        events = list(self.context.events)
        try:
            grep_idx = events.index(grep_event)
            # Look backward for a Read
            for i in range(grep_idx - 1, -1, -1):
                if events[i].tool_name == "Read":
                    return events[i]
        except (ValueError, IndexError):
            pass
        return None

    def _detect_explored_next(self, read_event: ToolEvent) -> Optional[DetectedEdge]:
        """Detect sequential file exploration."""
        recent_reads = self.context.get_recent_reads(within_seconds=30)

        # Get the previous read (not the current one)
        for event in reversed(recent_reads):
            if event.file_path and event.file_path != read_event.file_path:
                return DetectedEdge(
                    source_path=event.file_path,
                    target_path=read_event.file_path,
                    relationship="explored_next",
                    strength=0.5,
                    context=f"Explored after {Path(event.file_path).name}"
                )

        return None

    def analyze_file_content(
        self,
        file_path: str,
        content: str
    ) -> List[DetectedEdge]:
        """
        Analyze file content for import and reference edges.

        Call this after reading a file to detect static relationships.
        """
        edges = []

        # Python imports
        if file_path.endswith('.py'):
            edges.extend(self._detect_imports(file_path, content))

        # File path references
        edges.extend(self._detect_references(file_path, content))

        # Test file relationships
        edge = self._detect_test_relationship(file_path)
        if edge:
            edges.append(edge)

        return edges

    def _detect_imports(self, file_path: str, content: str) -> List[DetectedEdge]:
        """Detect Python import edges using AST."""
        edges = []

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return edges

        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)

        # Try to resolve imports to file paths
        base_dir = Path(file_path).parent
        for imp in imports:
            resolved = self._resolve_import(imp, base_dir)
            if resolved and resolved in self.explored_files:
                edges.append(DetectedEdge(
                    source_path=file_path,
                    target_path=resolved,
                    relationship="import",
                    strength=1.0,
                    context=f"imports {imp}"
                ))

        return edges

    def _resolve_import(self, import_name: str, base_dir: Path) -> Optional[str]:
        """Try to resolve an import name to a file path."""
        # Convert module.submodule to path
        parts = import_name.split('.')

        # Try relative paths
        for i in range(len(parts)):
            rel_path = '/'.join(parts[:i+1]) + '.py'
            full_path = base_dir / rel_path
            if full_path.exists():
                return str(full_path)

            # Try as package
            package_path = base_dir / '/'.join(parts[:i+1]) / '__init__.py'
            if package_path.exists():
                return str(package_path)

        return None

    def _detect_references(self, file_path: str, content: str) -> List[DetectedEdge]:
        """Detect file path references in content."""
        edges = []

        # Pattern for file paths
        path_patterns = [
            r'["\']([/\w.-]+\.(?:py|js|ts|json|yaml|yml|md|txt|sh))["\']',
            r'open\(["\']([^"\']+)["\']',
            r'Path\(["\']([^"\']+)["\']',
        ]

        found_paths = set()
        for pattern in path_patterns:
            matches = re.findall(pattern, content)
            found_paths.update(matches)

        for ref_path in found_paths:
            # Normalize path
            if not ref_path.startswith('/'):
                ref_path = str(Path(file_path).parent / ref_path)

            if ref_path in self.explored_files and ref_path != file_path:
                edges.append(DetectedEdge(
                    source_path=file_path,
                    target_path=ref_path,
                    relationship="reference",
                    strength=0.7,
                    context=f"References path in content"
                ))

        return edges

    def _detect_test_relationship(self, file_path: str) -> Optional[DetectedEdge]:
        """Detect test file to source file relationships."""
        filename = Path(file_path).name
        parent_dir = Path(file_path).parent

        for test_pattern, source_pattern in self.TEST_PATTERNS:
            match = re.match(test_pattern, filename)
            if match:
                # Try to find the source file
                source_name = match.expand(source_pattern.replace('\\1', r'\1'))

                # Look in same directory and parent
                for search_dir in [parent_dir, parent_dir.parent, parent_dir.parent / 'src']:
                    source_path = search_dir / source_name
                    source_str = str(source_path)
                    if source_str in self.explored_files:
                        return DetectedEdge(
                            source_path=file_path,
                            target_path=source_str,
                            relationship="test_of",
                            strength=0.95,
                            context=f"Test file for {source_name}"
                        )

        return None

    def set_explored_files(self, files: Set[str]):
        """Set the known explored files."""
        self.explored_files = files


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

_detector = None

def get_edge_detector() -> EdgeDetector:
    """Get or create the global edge detector."""
    global _detector
    if _detector is None:
        _detector = EdgeDetector()
    return _detector


def detect_edges_for_read(
    file_path: str,
    content: Optional[str] = None
) -> List[Dict]:
    """
    Detect edges when a file is read.

    Returns list of edge dicts ready for ExplorationGraph.add_edge().
    """
    detector = get_edge_detector()

    # Record the read event
    event_edges = detector.record_tool_event(
        tool_name="Read",
        input_summary={"file_path": file_path}
    )

    # Analyze content if provided
    content_edges = []
    if content:
        content_edges = detector.analyze_file_content(file_path, content)

    # Combine and convert to dicts
    all_edges = event_edges + content_edges
    return [
        {
            "source_path": e.source_path,
            "target_path": e.target_path,
            "relationship": e.relationship,
            "strength": e.strength,
            "context": e.context
        }
        for e in all_edges
    ]


def detect_edges_for_grep(
    pattern: str,
    files_matched: List[str]
) -> List[Dict]:
    """
    Record a grep operation for edge detection.

    Call this when grep finds matching files.
    """
    detector = get_edge_detector()

    edges = detector.record_tool_event(
        tool_name="Grep",
        input_summary={"pattern": pattern},
        output_summary={"files_matched": files_matched}
    )

    return [
        {
            "source_path": e.source_path,
            "target_path": e.target_path,
            "relationship": e.relationship,
            "strength": e.strength,
            "context": e.context
        }
        for e in edges
    ]


def sync_explored_files(graph) -> None:
    """Sync explored files from an ExplorationGraph."""
    detector = get_edge_detector()
    explored = set()
    for node in graph.nodes.values():
        if node.path:
            explored.add(node.path)
    detector.set_explored_files(explored)


# =============================================================================
# DEMO
# =============================================================================

if __name__ == "__main__":
    print("Edge Detector - Demo")
    print("=" * 50)

    detector = EdgeDetector()

    # Simulate exploration sequence
    detector.explored_files = {
        "/src/api.py",
        "/src/models.py",
        "/src/utils.py",
        "/tests/test_api.py"
    }

    # Read first file
    print("\n1. Reading /src/api.py")
    edges = detector.record_tool_event(
        tool_name="Read",
        input_summary={"file_path": "/src/api.py"}
    )
    print(f"   Edges detected: {len(edges)}")

    # Grep for something
    print("\n2. Grep for 'def create'")
    edges = detector.record_tool_event(
        tool_name="Grep",
        input_summary={"pattern": "def create"},
        output_summary={"files_matched": ["/src/models.py", "/src/utils.py"]}
    )
    print(f"   Edges detected: {len(edges)}")

    # Read file from grep results
    print("\n3. Reading /src/models.py (from grep results)")
    edges = detector.record_tool_event(
        tool_name="Read",
        input_summary={"file_path": "/src/models.py"}
    )
    print(f"   Edges detected: {len(edges)}")
    for e in edges:
        print(f"   - {e.relationship}: {e.source_path} -> {e.target_path}")

    # Analyze content
    print("\n4. Analyzing Python imports in /src/api.py")
    content = """
import os
from src.models import User
from src.utils import helper
"""
    edges = detector.analyze_file_content("/src/api.py", content)
    print(f"   Edges detected: {len(edges)}")
    for e in edges:
        print(f"   - {e.relationship}: {e.source_path} -> {e.target_path}")

    # Test file detection
    print("\n5. Detecting test relationship for /tests/test_api.py")
    edges = detector.analyze_file_content("/tests/test_api.py", "# test file")
    print(f"   Edges detected: {len(edges)}")
    for e in edges:
        print(f"   - {e.relationship}: {e.source_path} -> {e.target_path}")

    print("\nDemo complete!")
