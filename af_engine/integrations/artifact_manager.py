"""
af_engine.integrations.artifact_manager - Automated Artifact Management

This integration manages artifacts created during mission execution,
including categorization, INDEX.md maintenance, health reporting,
naming validation, and archival.

Extends the base integration to subscribe to CYCLE_COMPLETED events and
run a full artifact pipeline on each cycle end.
"""

import fnmatch
import hashlib
import json
import logging
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

# Default config path (relative to ATLASFORGE_ROOT)
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "workspace" / "AtlasForge" / "config" / "artifact_management.yaml"

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)
from .artifact_categorizer import ArtifactCategorizer
from .artifact_health import ArtifactHealthAnalyzer

logger = logging.getLogger(__name__)

# Directories (relative to workspace root) that contain artifacts to manage
ARTIFACT_DIRS = ["artifacts", "research"]

# Naming convention: valid artifact name pattern
VALID_NAME_RE = re.compile(r'^[a-z][a-z0-9_]*(\.[a-z0-9]+)?$')

# Allowlist: names exempt from naming validation
NAMING_ALLOWLIST = {
    "INDEX.md", "HANDOFF.md", "MEMORY.md", "README.md", "CLAUDE.md",
    "GROUND_RULES.md", "ENVIRONMENT.md", "DASHBOARD_IMPORT_POLICY.md",
}

# Files to exclude from artifact scanning (internal management files)
EXCLUDED_FILES = {"artifact_manifest.json", "INDEX.md"}

# Glob patterns for system-generated files to exclude from scanning
SYSTEM_FILE_PATTERNS = [
    "artifact_health_report_*.md",   # health analysis output files
    "artifact_health_report_final.md",  # covered by above, but explicit
]

# Subdirectories to exclude from artifact scanning
EXCLUDED_SUBDIRS = {"transcripts"}

# File extensions to exclude from artifact scanning
EXCLUDED_EXTENSIONS = {".jsonl"}


class ArtifactManagerIntegration(BaseIntegrationHandler):
    """
    Manages artifacts created during mission execution.

    On CYCLE_COMPLETED:
    1. Scans artifacts/ and research/ directories
    2. Categorizes each file (content + pattern + extension)
    3. Validates naming conventions, logs warnings
    4. Updates artifact_manifest.json (machine-readable inventory)
    5. Regenerates INDEX.md (human-readable catalog)
    6. Runs health analysis (orphans, duplicates, stale, archive candidates)
    7. Writes artifact_health_report_cycle_N.md

    On MISSION_COMPLETED:
    - Runs the full pipeline (same as cycle end)
    - Archives artifacts to .af_archives/

    On MISSION_STARTED:
    - Stores workspace_dir for later use
    - Ensures artifact directories exist
    """

    name = "artifact_manager"
    priority = IntegrationPriority.LOW
    subscriptions = [
        StageEvent.MISSION_STARTED,
        StageEvent.STAGE_COMPLETED,
        StageEvent.CYCLE_COMPLETED,
        StageEvent.MISSION_COMPLETED,
    ]

    def __init__(self, archive_dir: Optional[Path] = None):
        """Initialize artifact manager. Loads config from artifact_management.yaml if present."""
        super().__init__()
        self.archive_dir = archive_dir
        self._workspace_dir: Optional[Path] = None
        self._mission_id: str = "unknown"
        self._cycle_files_created: List[str] = []
        self._cycle_files_modified: List[str] = []
        self._categorizer = ArtifactCategorizer()
        # Load configurable thresholds (with type coercion to defend against bad YAML values)
        cfg = self._load_config()
        try:
            self._stale_days: int = int(cfg.get("stale_days", 30))
        except (TypeError, ValueError):
            self._stale_days = 30
        try:
            self._archive_score_threshold: float = float(cfg.get("archive_score_threshold", 0.7))
        except (TypeError, ValueError):
            self._archive_score_threshold = 0.7
        self._health_analyzer = ArtifactHealthAnalyzer(
            stale_days=self._stale_days,
            archive_score_threshold=self._archive_score_threshold,
        )

    def _load_config(self) -> dict:
        """Load artifact_management.yaml config. Returns empty dict on any error."""
        if not _YAML_AVAILABLE:
            return {}
        try:
            if _CONFIG_PATH.exists():
                with open(_CONFIG_PATH, "r") as f:
                    data = _yaml.safe_load(f) or {}
                logger.info("ArtifactManager: loaded config from %s", _CONFIG_PATH)
                return data.get("artifact_management", data)
        except Exception as exc:
            logger.debug("ArtifactManager: config load failed (%s), using defaults", exc)
        return {}

    # -- Event handlers -------------------------------------------------------

    def on_mission_started(self, event: Event) -> None:
        """Initialize artifact tracking for new mission."""
        self._cycle_files_created = []
        self._cycle_files_modified = []
        self._mission_id = event.mission_id

        workspace = (
            event.data.get("mission_workspace")
            or event.data.get("workspace_dir")
        )
        if workspace:
            self._workspace_dir = Path(workspace)
            for d in ARTIFACT_DIRS:
                (self._workspace_dir / d).mkdir(parents=True, exist_ok=True)
            logger.info("ArtifactManager: initialized for mission %s at %s",
                        self._mission_id, self._workspace_dir)

    def on_stage_completed(self, event: Event) -> None:
        """Accumulate files created/modified during each stage."""
        files_created = event.data.get("files_created", [])
        files_modified = event.data.get("files_modified", [])
        self._cycle_files_created.extend(files_created)
        self._cycle_files_modified.extend(files_modified)

        # Also capture workspace_dir if we missed it from MISSION_STARTED
        if not self._workspace_dir:
            ws = (
                event.data.get("mission_workspace")
                or event.data.get("workspace_dir")
            )
            if ws:
                self._workspace_dir = Path(ws)

    def on_cycle_completed(self, event: Event) -> None:
        """Process artifacts at the end of each cycle."""
        # Update workspace_dir from event if available
        ws = (
            event.data.get("mission_workspace")
            or event.data.get("workspace_dir")
        )
        if ws:
            self._workspace_dir = Path(ws)

        if not self._workspace_dir:
            logger.warning("ArtifactManager: no workspace_dir available, skipping cycle processing")
            return

        cycle_number = int(event.data.get("cycle_number", 0))

        # Merge event-level file lists with accumulated stage files
        event_created = event.data.get("files_created", [])
        event_modified = event.data.get("files_modified", [])
        all_created = list(set(self._cycle_files_created + event_created))
        all_modified = list(set(self._cycle_files_modified + event_modified))

        try:
            self._run_artifact_pipeline(cycle_number=cycle_number)
        except Exception as exc:
            logger.warning("ArtifactManager: pipeline error on cycle %d: %s", cycle_number, exc)

        # Reset cycle-level accumulation
        self._cycle_files_created = []
        self._cycle_files_modified = []

    def on_mission_completed(self, event: Event) -> None:
        """Run full pipeline and archive artifacts on mission completion."""
        ws = event.data.get("workspace_dir")
        if ws:
            self._workspace_dir = Path(ws)

        if self._workspace_dir:
            try:
                self._run_artifact_pipeline(cycle_number=None)
            except Exception as exc:
                logger.warning("ArtifactManager: pipeline error on mission complete: %s", exc)

        # Archive to .af_archives/
        try:
            self._archive_artifacts(event.mission_id)
        except Exception as exc:
            logger.warning("ArtifactManager: archival failed: %s", exc)

    # -- Core pipeline --------------------------------------------------------

    def _run_artifact_pipeline(self, cycle_number: Optional[int]) -> None:
        """
        Full artifact management pipeline:
        1. Scan artifact directories
        2. Categorize each file
        3. Validate naming conventions
        4. Update artifact_manifest.json
        5. Regenerate INDEX.md
        6. Run health analysis
        7. Write health report
        """
        workspace = self._workspace_dir
        if not workspace or not workspace.exists():
            logger.warning("ArtifactManager: workspace %s does not exist", workspace)
            return

        # 1. Scan
        all_paths = self._scan_artifacts(workspace)
        if not all_paths:
            logger.info("ArtifactManager: no artifacts found in %s", workspace)
            return

        logger.info("ArtifactManager: processing %d artifacts", len(all_paths))

        # 2. Categorize
        categories: Dict[Path, str] = {}
        for p in all_paths:
            categories[p] = self._categorizer.categorize(p, workspace_root=workspace)

        # 3. Validate naming
        naming_warnings: List[Tuple[Path, str]] = []
        for p in all_paths:
            suggestion = self._categorizer.validate_naming(p)
            if suggestion:
                naming_warnings.append((p, suggestion))
                logger.warning(
                    "ArtifactManager: naming violation %r → suggest %r", p.name, suggestion
                )

        # 4. Build manifest data
        manifest = self._build_manifest(all_paths, categories, workspace)

        # 5. Write manifest JSON
        manifest_path = workspace / "artifacts" / "artifact_manifest.json"
        self._write_manifest(manifest, manifest_path)

        # 6. Regenerate INDEX.md
        index_path = workspace / "artifacts" / "INDEX.md"
        self._write_index_md(all_paths, categories, manifest, index_path, naming_warnings)

        # 7. Health analysis
        report = self._health_analyzer.generate_health_report(
            artifact_paths=all_paths,
            workspace_dir=workspace,
            categories=categories,
            stale_days=self._stale_days,
        )

        # 8. Write health report
        if cycle_number is not None:
            report_name = f"artifact_health_report_cycle_{cycle_number}.md"
        else:
            report_name = "artifact_health_report_final.md"

        health_path = workspace / "artifacts" / report_name
        health_md = self._health_analyzer.render_markdown(report, cycle_number=cycle_number)
        health_path.write_text(health_md, encoding="utf-8")

        logger.info(
            "ArtifactManager: pipeline complete — %s",
            report.summary_line(),
        )
        if naming_warnings:
            logger.info(
                "ArtifactManager: %d naming violation(s) detected", len(naming_warnings)
            )

    # -- Scanning -------------------------------------------------------------

    def _scan_artifacts(self, workspace: Path) -> List[Path]:
        """Recursively scan ARTIFACT_DIRS for all files, skipping system files."""
        found: List[Path] = []
        for dir_name in ARTIFACT_DIRS:
            target = workspace / dir_name
            if not target.exists():
                continue
            for p in target.rglob("*"):
                if not p.is_file():
                    continue
                # Skip excluded filenames
                if p.name in EXCLUDED_FILES:
                    continue
                # Skip excluded subdirectory trees
                if any(part in EXCLUDED_SUBDIRS for part in p.parts):
                    continue
                # Skip excluded extensions
                if p.suffix.lower() in EXCLUDED_EXTENSIONS:
                    continue
                # Skip system-generated file patterns
                if any(fnmatch.fnmatch(p.name, pat) for pat in SYSTEM_FILE_PATTERNS):
                    continue
                found.append(p)
        return sorted(found)

    # -- Manifest JSON --------------------------------------------------------

    def _build_manifest(
        self,
        paths: List[Path],
        categories: Dict[Path, str],
        workspace: Path,
    ) -> dict:
        """Build the artifact_manifest.json data structure."""
        artifacts = []
        for p in paths:
            try:
                stat = p.stat()
                rel = str(p.relative_to(workspace))
                md5 = self._md5(p)
                ctime = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat()
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
                suggestion = self._categorizer.validate_naming(p)
                artifacts.append({
                    "path": rel,
                    "filename": p.name,
                    "category": categories.get(p, "unknown"),
                    "size_bytes": stat.st_size,
                    "md5": md5,
                    "created_at": ctime,
                    "modified_at": mtime,
                    "naming_valid": suggestion is None,
                    "naming_suggestion": suggestion,
                })
            except OSError as exc:
                logger.debug("ArtifactManager: skipping %s: %s", p, exc)

        return {
            "version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mission_id": self._mission_id,
            "workspace_dir": str(workspace),
            "artifact_count": len(artifacts),
            "artifacts": artifacts,
        }

    def _write_manifest(self, manifest: dict, path: Path) -> None:
        """Write manifest JSON atomically."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        tmp.replace(path)
        logger.debug("ArtifactManager: wrote manifest to %s", path)

    # -- INDEX.md generation --------------------------------------------------

    def _write_index_md(
        self,
        paths: List[Path],
        categories: Dict[Path, str],
        manifest: dict,
        index_path: Path,
        naming_warnings: List[Tuple[Path, str]],
    ) -> None:
        """Generate and write INDEX.md."""
        # Group by category
        by_cat: Dict[str, List[dict]] = defaultdict(list)
        for entry in manifest["artifacts"]:
            by_cat[entry["category"]].append(entry)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = [
            "# Artifact Index",
            "",
            f"**Mission**: {self._mission_id}",
            f"**Generated**: {now}",
            f"**Total Artifacts**: {len(paths)}",
            "",
            "---",
            "",
        ]

        # Category display order and labels
        category_order = [
            ("plan", "Plans"),
            ("report", "Reports"),
            ("research", "Research"),
            ("cycle_log", "Cycle Logs"),
            ("code", "Code"),
            ("data", "Data Files"),
            ("config", "Config Files"),
            ("log", "Logs"),
            ("index", "Indexes & Manifests"),
            ("handoff", "Handoff Documents"),
            ("unknown", "Uncategorized"),
        ]

        for cat_key, cat_label in category_order:
            entries = by_cat.get(cat_key, [])
            if not entries:
                continue
            lines.append(f"## {cat_label} ({len(entries)})")
            lines.append("")
            lines.append("| File | Size | Modified | Valid Name |")
            lines.append("|------|------|----------|------------|")
            for e in sorted(entries, key=lambda x: x["filename"]):
                size_kb = f"{e['size_bytes'] / 1024:.1f} KB"
                modified = e["modified_at"][:10] if e["modified_at"] else "—"
                name_ok = "✅" if e["naming_valid"] else f"⚠️ → `{e['naming_suggestion']}`"
                fname = e["filename"]
                rel_path = e["path"]
                lines.append(f"| [{fname}]({rel_path}) | {size_kb} | {modified} | {name_ok} |")
            lines.append("")

        # Health summary footer
        lines += [
            "---",
            "",
            "## Health Summary",
            "",
        ]
        # Quick counts from naming warnings
        lines.append(f"- Naming violations: {len(naming_warnings)}")
        if naming_warnings:
            for p, suggestion in naming_warnings:
                lines.append(f"  - `{p.name}` → suggest `{suggestion}`")

        lines.append("")
        lines.append("_Last updated by ArtifactManagerIntegration v2.0_")

        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text("\n".join(lines), encoding="utf-8")
        logger.debug("ArtifactManager: wrote INDEX.md to %s", index_path)

    # -- Archival -------------------------------------------------------------

    def _archive_artifacts(self, mission_id: str) -> Optional[Path]:
        """Copy artifacts to .af_archives/ for long-term storage."""
        if self.archive_dir is None:
            self.archive_dir = Path(".af_archives")

        self.archive_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_path = self.archive_dir / f"{mission_id}_{timestamp}"
        archive_path.mkdir(parents=True, exist_ok=True)

        if not self._workspace_dir:
            return None

        count = 0
        for dir_name in ARTIFACT_DIRS:
            src_dir = self._workspace_dir / dir_name
            if not src_dir.exists():
                continue
            for p in src_dir.rglob("*"):
                if p.is_file():
                    try:
                        rel = p.relative_to(self._workspace_dir)
                        dst = archive_path / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(p, dst)
                        count += 1
                    except Exception as exc:
                        logger.debug("ArtifactManager: archive copy failed for %s: %s", p, exc)

        logger.info("ArtifactManager: archived %d artifacts to %s", count, archive_path)
        return archive_path

    # -- Utility --------------------------------------------------------------

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

    def get_artifacts(self) -> List[str]:
        """Get list of tracked artifact file paths (cycle-accumulated)."""
        return list(set(self._cycle_files_created + self._cycle_files_modified))
