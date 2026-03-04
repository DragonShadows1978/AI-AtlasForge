"""
af_engine.integrations.artifact_categorizer - Artifact Content Categorization

Categorizes artifacts based on content analysis, naming patterns, and directory hints.
Uses a priority-ordered detection strategy: special names -> directory -> extension -> pattern -> content.
"""

import re
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# -- Category constants --------------------------------------------------------

CATEGORY_PLAN = "plan"
CATEGORY_REPORT = "report"
CATEGORY_RESEARCH = "research"
CATEGORY_CYCLE_LOG = "cycle_log"
CATEGORY_CODE = "code"
CATEGORY_DATA = "data"
CATEGORY_LOG = "log"
CATEGORY_INDEX = "index"
CATEGORY_HANDOFF = "handoff"
CATEGORY_CONFIG = "config"
CATEGORY_UNKNOWN = "unknown"

# -- Special filename -> category mapping --------------------------------------

SPECIAL_FILENAMES = {
    "index.md": CATEGORY_INDEX,
    "manifest.md": CATEGORY_INDEX,
    "artifact_manifest.json": CATEGORY_INDEX,
    "handoff.md": CATEGORY_HANDOFF,
    "memory.md": CATEGORY_CONFIG,
    "readme.md": CATEGORY_INDEX,
    "claude.md": CATEGORY_CONFIG,
    "ground_rules.md": CATEGORY_CONFIG,
    "environment.md": CATEGORY_CONFIG,
}

# -- Extension -> category mapping ---------------------------------------------

EXT_TO_CATEGORY = {
    ".py": CATEGORY_CODE,
    ".js": CATEGORY_CODE,
    ".ts": CATEGORY_CODE,
    ".jsx": CATEGORY_CODE,
    ".tsx": CATEGORY_CODE,
    ".sh": CATEGORY_CODE,
    ".bash": CATEGORY_CODE,
    ".c": CATEGORY_CODE,
    ".cpp": CATEGORY_CODE,
    ".h": CATEGORY_CODE,
    ".rs": CATEGORY_CODE,
    ".go": CATEGORY_CODE,
    ".json": CATEGORY_DATA,
    ".yaml": CATEGORY_DATA,
    ".yml": CATEGORY_DATA,
    ".csv": CATEGORY_DATA,
    ".db": CATEGORY_DATA,
    ".sqlite": CATEGORY_DATA,
    ".sqlite3": CATEGORY_DATA,
    ".parquet": CATEGORY_DATA,
    ".pkl": CATEGORY_DATA,
    ".toml": CATEGORY_CONFIG,
    ".ini": CATEGORY_CONFIG,
    ".cfg": CATEGORY_CONFIG,
    ".env": CATEGORY_CONFIG,
    ".log": CATEGORY_LOG,
    ".out": CATEGORY_LOG,
}

# -- Filename pattern -> category (checked in order) --------------------------

FILENAME_PATTERNS = [
    (re.compile(r"handoff", re.I), CATEGORY_HANDOFF),
    (re.compile(r"cycle[_\-\s]*(report|log|summary|end|complete)", re.I), CATEGORY_CYCLE_LOG),
    (re.compile(r"cycle[_\-]?\d+", re.I), CATEGORY_CYCLE_LOG),
    (re.compile(r"(implementation|execution|action|mission|project)[_\-]?plan", re.I), CATEGORY_PLAN),
    (re.compile(r"plan[_\-]?(v\d|final|draft|revision)?$", re.I), CATEGORY_PLAN),
    (re.compile(r"(health|status|progress|final|summary|analysis)[_\-]?report", re.I), CATEGORY_REPORT),
    (re.compile(r"report[_\-]?(v\d|final|draft)?$", re.I), CATEGORY_REPORT),
    (re.compile(r"artifact[_\-]health", re.I), CATEGORY_REPORT),
    (re.compile(r"(summary|analysis|findings)$", re.I), CATEGORY_REPORT),
    (re.compile(r"(settings|config|configuration|options)(s?)$", re.I), CATEGORY_CONFIG),
    (re.compile(r"(log|logs|transcript|trace|debug)", re.I), CATEGORY_LOG),
]

# -- Content sampling keywords -> category (checked in order) -----------------

CONTENT_SIGNATURES = [
    (["cycle_complete", "continuation_prompt", "cycle_number", "cycles_remaining"], CATEGORY_CYCLE_LOG),
    (["## steps", "## approach", "## implementation", "## plan", "success criteria", "## requirements"], CATEGORY_PLAN),
    (["implementation plan", "approach rationale", "## files to create", "## files to modify"], CATEGORY_PLAN),
    (["## summary", "## achievements", "## results", "health report", "orphaned files", "archive candidates"], CATEGORY_REPORT),
    (["## analysis", "## findings", "mission complete", "stage completed"], CATEGORY_REPORT),
    (["## research", "## literature", "## references", "abstract", "methodology"], CATEGORY_RESEARCH),
    (["working on:", "completed:", "in progress:", "next steps:", "handoff #"], CATEGORY_HANDOFF),
]

CONTENT_SAMPLE_BYTES = 600

# Naming convention allowlist (exempt from snake_case validation)
NAMING_ALLOWLIST = {"INDEX.md", "HANDOFF.md", "MEMORY.md", "README.md", "CLAUDE.md",
                    "GROUND_RULES.md", "ENVIRONMENT.md", "DASHBOARD_IMPORT_POLICY.md"}

VALID_NAME_RE = re.compile(r'^[a-z][a-z0-9_]*(\.[a-z0-9]+)?$')


class ArtifactCategorizer:
    """
    Categorizes artifact files using a multi-stage detection strategy.

    Detection priority:
    1. Special filenames (INDEX.md, HANDOFF.md, etc.)
    2. Directory hint (files inside research/ -> research category)
    3. File extension for binary/code/data types
    4. Filename pattern matching
    5. Content sampling (first CONTENT_SAMPLE_BYTES of text files)
    6. Fallback: unknown
    """

    def categorize(self, path: Path, workspace_root: Optional[Path] = None) -> str:
        """
        Categorize a single artifact file.

        Args:
            path: Absolute path to the artifact file.
            workspace_root: Optional workspace root to compute relative paths for
                            directory-hint detection.

        Returns:
            Category string (one of the CATEGORY_* constants).
        """
        if not path.exists():
            return CATEGORY_UNKNOWN

        name_lower = path.name.lower()

        # 1. Special filename exact match
        cat = SPECIAL_FILENAMES.get(name_lower)
        if cat:
            logger.debug("Categorized %r as %r (special filename)", path.name, cat)
            return cat

        # 2. Directory hint
        if workspace_root:
            try:
                rel = path.relative_to(workspace_root)
                parts = rel.parts
                if len(parts) > 1 and parts[0].lower() == "research":
                    logger.debug("Categorized %r as research (directory hint)", path.name)
                    return CATEGORY_RESEARCH
            except ValueError:
                pass

        # 3. Extension
        ext = path.suffix.lower()
        if ext in EXT_TO_CATEGORY:
            cat = EXT_TO_CATEGORY[ext]
            if ext == ".json" and re.search(r"(manifest|index)", name_lower):
                cat = CATEGORY_INDEX
            logger.debug("Categorized %r as %r (extension %r)", path.name, cat, ext)
            return cat

        # 4. Filename pattern matching
        stem_lower = path.stem.lower()
        for pattern, cat in FILENAME_PATTERNS:
            if pattern.search(stem_lower):
                logger.debug("Categorized %r as %r (filename pattern)", path.name, cat)
                return cat

        # 5. Content sampling
        if self._is_text_file(path):
            content = self._sample_content(path)
            if content:
                cat = self._match_content(content)
                if cat:
                    logger.debug("Categorized %r as %r (content analysis)", path.name, cat)
                    return cat

        # 6. Fallback
        logger.debug("Categorized %r as unknown (no match)", path.name)
        return CATEGORY_UNKNOWN

    def validate_naming(self, path: Path) -> Optional[str]:
        """
        Validate artifact naming conventions.

        Returns a suggested correction if the name violates conventions,
        or None if the name is valid (or on the allowlist).

        Rules: lowercase, snake_case, no spaces, valid extension.
        Allowlist: INDEX.md, HANDOFF.md, MEMORY.md, README.md and similar.
        """
        name = path.name
        if name in NAMING_ALLOWLIST:
            return None
        if VALID_NAME_RE.match(name):
            return None
        # Build suggestion: lowercase, spaces/hyphens -> underscores
        stem = path.stem.lower().replace(" ", "_").replace("-", "_")
        # Strip non-alphanumeric/underscore chars (except leading char)
        stem = re.sub(r"[^a-z0-9_]", "", stem)
        if not stem or not stem[0].isalpha():
            stem = "artifact_" + stem.lstrip("_0123456789")
        ext = path.suffix.lower()
        suggestion = stem + ext if ext else stem
        return suggestion if suggestion != name else None

    def _is_text_file(self, path: Path) -> bool:
        """Heuristic check: can the file be read as text?"""
        if path.stat().st_size == 0:
            return False
        binary_exts = {".db", ".sqlite", ".sqlite3", ".parquet", ".pkl",
                       ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip",
                       ".tar", ".gz", ".bz2", ".whl", ".exe", ".so"}
        if path.suffix.lower() in binary_exts:
            return False
        try:
            with open(path, "rb") as f:
                chunk = f.read(512)
            return b"\x00" not in chunk
        except OSError:
            return False

    def _sample_content(self, path: Path) -> str:
        """Read first CONTENT_SAMPLE_BYTES of a text file, lowercased."""
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read(CONTENT_SAMPLE_BYTES).lower()
        except OSError:
            return ""

    def _match_content(self, content: str) -> Optional[str]:
        """Match content against known signatures; return first match or None."""
        for keywords, cat in CONTENT_SIGNATURES:
            for kw in keywords:
                if kw in content:
                    return cat
        return None

    def describe_category(self, category: str) -> str:
        """Return a human-readable description for a category."""
        descriptions = {
            CATEGORY_PLAN: "Implementation/execution plan",
            CATEGORY_REPORT: "Report or summary document",
            CATEGORY_RESEARCH: "Research notes and findings",
            CATEGORY_CYCLE_LOG: "Cycle completion log",
            CATEGORY_CODE: "Source code file",
            CATEGORY_DATA: "Data file (JSON, YAML, CSV, DB)",
            CATEGORY_LOG: "Log or trace output",
            CATEGORY_INDEX: "Index or manifest file",
            CATEGORY_HANDOFF: "Context handoff document",
            CATEGORY_CONFIG: "Configuration file",
            CATEGORY_UNKNOWN: "Uncategorized artifact",
        }
        return descriptions.get(category, "Unknown category")

    def all_categories(self) -> list:
        """Return all known category names."""
        return [
            CATEGORY_PLAN, CATEGORY_REPORT, CATEGORY_RESEARCH, CATEGORY_CYCLE_LOG,
            CATEGORY_CODE, CATEGORY_DATA, CATEGORY_LOG, CATEGORY_INDEX,
            CATEGORY_HANDOFF, CATEGORY_CONFIG, CATEGORY_UNKNOWN,
        ]
