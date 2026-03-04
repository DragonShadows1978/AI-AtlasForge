"""
af_engine.integrations.artifact_health - Artifact Health Analysis

Analyzes artifact directories for health issues:
- Orphaned files (not referenced by any other artifact)
- Duplicate files (identical content, detected via MD5)
- Stale files (not modified in > N days)
- Archive candidates (scored by age + reference frequency)
- Naming convention violations

Produces a structured HealthReport and a human-readable markdown section.
"""

import fnmatch
import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# System-generated files that should be exempt from all health checks
HEALTH_SYSTEM_PATTERNS = [
    "artifact_health_report_*.md",
    "artifact_health_report_final.md",
]

# Extensions to skip in health checks
HEALTH_EXCLUDED_EXTENSIONS = {".jsonl"}

# Default staleness threshold in days
DEFAULT_STALE_DAYS = 30

# Minimum archive score to be listed as a candidate
ARCHIVE_SCORE_THRESHOLD = 0.7


@dataclass
class OrphanInfo:
    path: Path
    reason: str


@dataclass
class DuplicateGroup:
    md5_hash: str
    paths: List[Path]
    size_bytes: int


@dataclass
class StaleInfo:
    path: Path
    days_old: float
    last_modified: str  # ISO date string


@dataclass
class ArchiveCandidate:
    path: Path
    score: float          # 0.0 – 1.0 (higher = stronger archive candidate)
    age_score: float
    ref_score: float
    days_old: float
    reference_count: int
    reason: str


@dataclass
class NamingViolation:
    path: Path
    suggestion: str


@dataclass
class HealthReport:
    """Aggregated artifact health analysis results."""
    generated_at: str = ""
    workspace_dir: str = ""
    total_artifacts: int = 0
    orphans: List[OrphanInfo] = field(default_factory=list)
    duplicate_groups: List[DuplicateGroup] = field(default_factory=list)
    stale_files: List[StaleInfo] = field(default_factory=list)
    archive_candidates: List[ArchiveCandidate] = field(default_factory=list)
    naming_violations: List[NamingViolation] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        """True if no critical health issues detected."""
        return (
            len(self.orphans) == 0
            and len(self.duplicate_groups) == 0
            and len(self.naming_violations) == 0
        )

    def summary_line(self) -> str:
        return (
            f"{self.total_artifacts} artifacts | "
            f"{len(self.orphans)} orphans | "
            f"{len(self.duplicate_groups)} dup groups | "
            f"{len(self.stale_files)} stale | "
            f"{len(self.archive_candidates)} archive candidates | "
            f"{len(self.naming_violations)} naming violations"
        )


# Categories that are exempt from orphan detection (they are structural)
ORPHAN_EXEMPT_CATEGORIES = {"index", "config", "handoff"}

# Filenames that are never considered orphans
ORPHAN_EXEMPT_NAMES = {
    "INDEX.md", "HANDOFF.md", "MEMORY.md", "README.md", "CLAUDE.md",
    "artifact_manifest.json", "GROUND_RULES.md", "ENVIRONMENT.md",
}


class ArtifactHealthAnalyzer:
    """
    Performs health analysis on a set of artifact files.

    Usage:
        analyzer = ArtifactHealthAnalyzer()
        report = analyzer.generate_health_report(artifact_paths, workspace_root)
        markdown = analyzer.render_markdown(report)
    """

    def __init__(
        self,
        stale_days: int = DEFAULT_STALE_DAYS,
        archive_score_threshold: float = ARCHIVE_SCORE_THRESHOLD,
    ) -> None:
        """
        Initialize with optional threshold overrides.

        Args:
            stale_days: Days before a file is considered stale.
            archive_score_threshold: Minimum score to list as archive candidate.
        """
        self._stale_days = stale_days
        self._archive_score_threshold = archive_score_threshold

    @staticmethod
    def _is_system_file(path: Path) -> bool:
        """Return True if this file is a system-generated AtlasForge management file."""
        if path.suffix.lower() in HEALTH_EXCLUDED_EXTENSIONS:
            return True
        return any(fnmatch.fnmatch(path.name, pat) for pat in HEALTH_SYSTEM_PATTERNS)

    def generate_health_report(
        self,
        artifact_paths: List[Path],
        workspace_dir: Path,
        categories: Optional[Dict[Path, str]] = None,
        stale_days: Optional[int] = None,
    ) -> HealthReport:
        """
        Generate a full health report for the given artifact paths.

        Args:
            artifact_paths: List of artifact file paths to analyze.
            workspace_dir: Root workspace directory (for building cross-references).
            categories: Optional dict mapping path -> category string.
            stale_days: Number of days before a file is considered stale.

        Returns:
            A populated HealthReport dataclass.
        """
        categories = categories or {}
        if stale_days is None:
            stale_days = self._stale_days
        existing = [p for p in artifact_paths if p.exists()]

        report = HealthReport(
            generated_at=datetime.now(timezone.utc).isoformat(),
            workspace_dir=str(workspace_dir),
            total_artifacts=len(existing),
        )

        # Build reference map: which filenames appear in which files
        ref_map = self._build_reference_map(existing)

        report.orphans = self.find_orphans(existing, ref_map, categories)
        report.duplicate_groups = self.find_duplicates(existing)
        report.stale_files = self.find_stale(existing, days=stale_days)
        report.archive_candidates = self.score_archive_candidates(existing, ref_map)
        report.naming_violations = self._check_naming(existing)

        return report

    # -- Core analysis methods ------------------------------------------------

    def find_orphans(
        self,
        artifact_paths: List[Path],
        ref_map: Dict[str, List[str]],
        categories: Dict[Path, str],
    ) -> List[OrphanInfo]:
        """
        Find artifacts that are not referenced by any other artifact.

        A file is an orphan if:
        - Its filename does not appear in any other artifact's content
        - It is not in the ORPHAN_EXEMPT_NAMES set
        - Its category is not in ORPHAN_EXEMPT_CATEGORIES

        Returns a list of OrphanInfo objects.
        """
        orphans = []
        for path in artifact_paths:
            if self._is_system_file(path):
                continue
            if path.name in ORPHAN_EXEMPT_NAMES:
                continue
            cat = categories.get(path, "")
            if cat in ORPHAN_EXEMPT_CATEGORIES:
                continue
            refs = ref_map.get(path.name, [])
            if not refs:
                orphans.append(OrphanInfo(
                    path=path,
                    reason=f"Not referenced by any other artifact (0 cross-references)",
                ))
        return orphans

    def find_duplicates(self, artifact_paths: List[Path]) -> List[DuplicateGroup]:
        """
        Find files with identical content using size-bucket + MD5.

        Returns a list of DuplicateGroup objects (groups with 2+ files).
        """
        # Group by size first (cheap filter)
        size_buckets: Dict[int, List[Path]] = {}
        for path in artifact_paths:
            if not path.is_file():
                continue
            size = path.stat().st_size
            size_buckets.setdefault(size, []).append(path)

        # Hash files in same-size buckets
        hash_groups: Dict[str, List[Path]] = {}
        for size, paths in size_buckets.items():
            if len(paths) < 2:
                continue
            for path in paths:
                md5 = self._md5(path)
                if md5:
                    hash_groups.setdefault(md5, []).append(path)

        duplicates = []
        for md5, paths in hash_groups.items():
            if len(paths) >= 2:
                size = paths[0].stat().st_size if paths[0].exists() else 0
                duplicates.append(DuplicateGroup(md5_hash=md5, paths=paths, size_bytes=size))

        return duplicates

    def find_stale(
        self, artifact_paths: List[Path], days: int = DEFAULT_STALE_DAYS
    ) -> List[StaleInfo]:
        """
        Find files not modified in the last `days` days.

        Returns a list of StaleInfo objects, sorted by staleness (oldest first).
        """
        now = datetime.now(timezone.utc).timestamp()
        threshold_seconds = days * 86400
        stale = []

        for path in artifact_paths:
            if not path.is_file():
                continue
            if self._is_system_file(path):
                continue
            try:
                mtime = os.path.getmtime(path)
                age_seconds = now - mtime
                if age_seconds > threshold_seconds:
                    days_old = age_seconds / 86400
                    last_modified = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime(
                        "%Y-%m-%d"
                    )
                    stale.append(StaleInfo(path=path, days_old=days_old, last_modified=last_modified))
            except OSError:
                continue

        stale.sort(key=lambda s: s.days_old, reverse=True)
        return stale

    def score_archive_candidates(
        self,
        artifact_paths: List[Path],
        ref_map: Dict[str, List[str]],
    ) -> List[ArchiveCandidate]:
        """
        Score each artifact as an archive candidate.

        Score formula:
            age_score  = min(days_old / 30, 1.0)          (1.0 if >= 30 days old)
            ref_score  = 1.0 - min(ref_count / 5, 1.0)   (1.0 if never referenced)
            score      = 0.6 * age_score + 0.4 * ref_score

        Returns candidates with score >= ARCHIVE_SCORE_THRESHOLD, sorted by score desc.
        """
        now = datetime.now(timezone.utc).timestamp()
        candidates = []

        for path in artifact_paths:
            if not path.is_file():
                continue
            if self._is_system_file(path):
                continue
            if path.name in ORPHAN_EXEMPT_NAMES:
                continue
            try:
                mtime = os.path.getmtime(path)
                days_old = (now - mtime) / 86400
            except OSError:
                days_old = 0.0

            ref_count = len(ref_map.get(path.name, []))
            age_score = min(days_old / 30.0, 1.0)
            ref_score = 1.0 - min(ref_count / 5.0, 1.0)
            score = 0.6 * age_score + 0.4 * ref_score

            if score >= self._archive_score_threshold:
                reason_parts = []
                if age_score >= 0.8:
                    reason_parts.append(f"{days_old:.0f} days old")
                if ref_score >= 0.8:
                    reason_parts.append(f"only {ref_count} references")
                reason = "; ".join(reason_parts) if reason_parts else "low activity"

                candidates.append(ArchiveCandidate(
                    path=path,
                    score=round(score, 3),
                    age_score=round(age_score, 3),
                    ref_score=round(ref_score, 3),
                    days_old=round(days_old, 1),
                    reference_count=ref_count,
                    reason=reason,
                ))

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    # -- Reference map building -----------------------------------------------

    def _build_reference_map(self, paths: List[Path]) -> Dict[str, List[str]]:
        """
        Build a map: filename -> list of filenames that reference it.

        Reads each text file and checks which other filenames appear in its content.
        Also matches file stems (without extension) and partial matches so that
        e.g. a reference to "implementation_plan" counts as a reference to
        "implementation_plan.md". This reduces false-positive orphan detection.
        """
        file_paths = [p for p in paths if p.is_file()]
        all_names = {p.name for p in file_paths}
        # Build stem -> canonical names mapping (a stem can map to multiple files)
        stem_to_names: Dict[str, List[str]] = {}
        for p in file_paths:
            stem = p.stem
            stem_to_names.setdefault(stem, []).append(p.name)

        ref_map: Dict[str, List[str]] = {name: [] for name in all_names}

        for path in file_paths:
            content = self._read_text(path)
            if not content:
                continue
            # Check full filename matches
            for other_name in all_names:
                if other_name == path.name:
                    continue
                if other_name in content:
                    if path.name not in ref_map[other_name]:
                        ref_map[other_name].append(path.name)
            # Check stem-only matches (e.g. "implementation_plan" in content)
            for stem, names in stem_to_names.items():
                # Skip trivial stems that could cause false positives (1-3 chars)
                if len(stem) < 4:
                    continue
                if stem in content:
                    for other_name in names:
                        if other_name == path.name:
                            continue
                        if path.name not in ref_map[other_name]:
                            ref_map[other_name].append(path.name)

        return ref_map

    # -- Naming validation ----------------------------------------------------

    _VALID_NAME_RE = re.compile(r'^[a-z][a-z0-9_]*(\.[a-z0-9]+)?$')
    _ALLOWLIST = {
        "INDEX.md", "HANDOFF.md", "MEMORY.md", "README.md", "CLAUDE.md",
        "GROUND_RULES.md", "ENVIRONMENT.md", "DASHBOARD_IMPORT_POLICY.md",
    }

    def _check_naming(self, artifact_paths: List[Path]) -> List[NamingViolation]:
        """Check naming conventions for all artifact paths."""
        violations = []
        for path in artifact_paths:
            if self._is_system_file(path):
                continue
            name = path.name
            if name in self._ALLOWLIST:
                continue
            if not self._VALID_NAME_RE.match(name):
                suggestion = self._suggest_name(path)
                violations.append(NamingViolation(path=path, suggestion=suggestion))
        return violations

    def _suggest_name(self, path: Path) -> str:
        """Suggest a valid snake_case name for a file."""
        stem = path.stem.lower().replace(" ", "_").replace("-", "_")
        stem = re.sub(r"[^a-z0-9_]", "", stem)
        if not stem or not stem[0].isalpha():
            stem = "artifact_" + stem.lstrip("_0123456789")
        ext = path.suffix.lower()
        return stem + ext if ext else stem

    # -- Utilities ------------------------------------------------------------

    def _md5(self, path: Path) -> Optional[str]:
        """Compute MD5 hash of a file. Returns None on error."""
        try:
            h = hashlib.md5()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()
        except OSError:
            return None

    def _read_text(self, path: Path, max_bytes: int = 32768) -> str:
        """Read a file as text (up to max_bytes). Returns empty string on failure."""
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read(max_bytes)
        except OSError:
            return ""

    # -- Markdown rendering ---------------------------------------------------

    def render_markdown(self, report: HealthReport, cycle_number: Optional[int] = None) -> str:
        """Render a HealthReport as a markdown document."""
        title = f"Artifact Health Report"
        if cycle_number is not None:
            title += f" — Cycle {cycle_number}"

        lines = [
            f"# {title}",
            "",
            f"**Generated**: {report.generated_at}",
            f"**Workspace**: `{report.workspace_dir}`",
            f"**Total Artifacts**: {report.total_artifacts}",
            "",
            "---",
            "",
        ]

        # Summary table
        lines += [
            "## Health Summary",
            "",
            f"| Check | Count | Status |",
            f"|-------|-------|--------|",
            f"| Orphaned files | {len(report.orphans)} | {'✅ Clean' if not report.orphans else '⚠️ Review'} |",
            f"| Duplicate groups | {len(report.duplicate_groups)} | {'✅ Clean' if not report.duplicate_groups else '⚠️ Review'} |",
            f"| Stale files (>30d) | {len(report.stale_files)} | {'✅ Clean' if not report.stale_files else '⚠️ Review'} |",
            f"| Archive candidates | {len(report.archive_candidates)} | {'✅ None' if not report.archive_candidates else 'ℹ️ Suggested'} |",
            f"| Naming violations | {len(report.naming_violations)} | {'✅ Clean' if not report.naming_violations else '⚠️ Fix'} |",
            "",
        ]

        # Orphans
        if report.orphans:
            lines += ["## Orphaned Files", ""]
            lines.append("Files not referenced by any other artifact:")
            lines.append("")
            for o in report.orphans:
                lines.append(f"- `{o.path.name}` — {o.reason}")
            lines.append("")

        # Duplicates
        if report.duplicate_groups:
            lines += ["## Duplicate Groups", ""]
            for i, g in enumerate(report.duplicate_groups, 1):
                lines.append(f"**Group {i}** (MD5: `{g.md5_hash[:8]}...`, {g.size_bytes} bytes):")
                for p in g.paths:
                    lines.append(f"  - `{p.name}`")
            lines.append("")

        # Stale
        if report.stale_files:
            lines += ["## Stale Files", "", "| File | Last Modified | Days Old |",
                      "|------|--------------|----------|"]
            for s in report.stale_files:
                lines.append(f"| `{s.path.name}` | {s.last_modified} | {s.days_old:.0f} |")
            lines.append("")

        # Archive candidates
        if report.archive_candidates:
            lines += ["## Archive Candidates", "", "| File | Score | Days Old | Refs | Reason |",
                      "|------|-------|----------|------|--------|"]
            for c in report.archive_candidates:
                lines.append(
                    f"| `{c.path.name}` | {c.score:.2f} | {c.days_old:.0f} | {c.reference_count} | {c.reason} |"
                )
            lines.append("")

        # Naming violations
        if report.naming_violations:
            lines += ["## Naming Violations", "", "| File | Suggested Name |",
                      "|------|---------------|"]
            for v in report.naming_violations:
                lines.append(f"| `{v.path.name}` | `{v.suggestion}` |")
            lines.append("")

        if report.is_healthy:
            lines += ["## Overall Status", "", "_All checks passed. Artifact directory is healthy._", ""]

        lines.append("_Generated by ArtifactHealthAnalyzer v1.0_")
        return "\n".join(lines)
