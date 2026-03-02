#!/usr/bin/env python3
"""
scripts/release_pipeline.py - Automated Release Pipeline for AtlasForge

Produces a semantic version bump, human-readable CHANGELOG.md entry,
and an annotated git tag after a clean squash push.

Usage:
    python3 scripts/release_pipeline.py [--push-tags] [--dry-run] [--bump=minor|patch]

Integrated into conductor:
    python3 atlasforge_conductor.py --release [--push-tags] [--dry-run]

Flow:
    1. Detect previous release tag (v*)
    2. Get changed files + commit messages since that tag
    3. Determine bump type: core files -> minor, everything else -> patch
    4. Bump version in pyproject.toml
    5. Generate CHANGELOG.md entry (Keep a Changelog format)
    6. Create version commit (pyproject.toml + CHANGELOG.md)
    7. Create annotated git tag with changelog as message body
    8. Optionally push tag to remote
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Files/directories that constitute "core" AtlasForge systems.
# Any changed file matching one of these prefixes -> minor version bump.
CORE_FILE_PREFIXES: List[str] = [
    "atlasforge_conductor.py",
    "atlasforge_conductor_errors.py",
    "atlasforge_config.py",
    "atlasforge_tray.py",
    "dashboard_v2.py",
    "io_utils.py",
    "context_watcher.py",
    "exploration_hooks.py",
    "decision_graph.py",
    "stage_checkpoint_recovery.py",
    "init_guard.py",
    "af_engine/",
    "atlasforge_enhancements/",
    "scripts/",
    "workspace/ConductorTakeover/",
    "glassbox/",
]

# File extensions to include in "Code Changes" section.
CODE_EXTENSIONS: frozenset = frozenset([
    ".py", ".js", ".ts", ".jsx", ".tsx", ".css", ".html",
])

# File extensions to EXCLUDE from "Code Changes" (docs / config / generated).
IGNORE_EXTENSIONS: frozenset = frozenset([
    ".md", ".json", ".lock", ".txt", ".yaml", ".yml",
    ".cfg", ".toml", ".ini", ".env", ".gitignore",
    ".rst", ".xml", ".csv",
])

# Keyword -> changelog section mapping (order matters: first match wins).
KEYWORD_MAP: List[Tuple[str, List[str]]] = [
    ("Added",   ["add", "new", "feature", "implement", "creat", "introduc", "support"]),
    ("Fixed",   ["fix", "bug", "patch", "repair", "resolv", "correct", "address"]),
    ("Changed", ["updat", "chang", "refactor", "improv", "enhanc", "optim", "rewrite", "migrat",
                 "bump", "replac"]),
    ("Removed", ["remov", "delet", "deprecat", "drop", "strip"]),
]


# ---------------------------------------------------------------------------
# Helper: subprocess wrapper
# ---------------------------------------------------------------------------

def _run(args: List[str], cwd: Path, timeout: int = 30) -> Tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


# ---------------------------------------------------------------------------
# Core logic functions
# ---------------------------------------------------------------------------

def get_repo_root() -> Path:
    """Find the git repository root starting from this script's location."""
    script_dir = Path(__file__).resolve().parent
    # Walk upward to find .git
    candidate = script_dir
    while candidate != candidate.parent:
        if (candidate / ".git").exists():
            return candidate
        candidate = candidate.parent
    # Fallback: ask git directly
    rc, stdout, _ = _run(["git", "rev-parse", "--show-toplevel"], cwd=script_dir)
    if rc == 0:
        return Path(stdout)
    raise RuntimeError("Could not find git repository root")


def get_current_version(repo_root: Path) -> str:
    """Read version from pyproject.toml."""
    toml_path = repo_root / "pyproject.toml"
    if not toml_path.exists():
        raise FileNotFoundError(f"pyproject.toml not found at {toml_path}")
    content = toml_path.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not match:
        print("WARNING: Could not parse version from pyproject.toml, defaulting to 0.1.0")
        return "0.1.0"
    return match.group(1)


def get_previous_release_tag(repo_root: Path) -> Optional[str]:
    """
    Find the most recent release tag matching v*.
    Returns tag name or None if no release tags exist.
    """
    rc, stdout, _ = _run(
        ["git", "describe", "--tags", "--abbrev=0", "--match", "v*"],
        cwd=repo_root,
    )
    if rc == 0 and stdout:
        return stdout
    # If describe failed, try listing tags sorted by version
    rc2, stdout2, _ = _run(["git", "tag", "--list", "v*", "--sort=-version:refname"], cwd=repo_root)
    if rc2 == 0 and stdout2:
        first_tag = stdout2.splitlines()[0].strip()
        if first_tag:
            return first_tag
    return None


def get_changed_files(repo_root: Path, since_ref: Optional[str]) -> List[str]:
    """
    Get list of files changed since `since_ref`.
    Falls back to last 50 commits if no ref given.
    """
    if since_ref:
        rc, stdout, _ = _run(
            ["git", "diff", "--name-only", f"{since_ref}..HEAD"],
            cwd=repo_root,
        )
        if rc == 0:
            return [f for f in stdout.splitlines() if f]
        # Try alternate form
        rc, stdout, _ = _run(
            ["git", "diff", "--name-only", since_ref, "HEAD"],
            cwd=repo_root,
        )
        if rc == 0:
            return [f for f in stdout.splitlines() if f]

    # Fallback: files changed in the last 50 commits
    rc, stdout, _ = _run(
        ["git", "diff", "--name-only", "HEAD~50", "HEAD"],
        cwd=repo_root,
    )
    if rc == 0:
        return [f for f in stdout.splitlines() if f]
    return []


def determine_bump_type(changed_files: List[str], override: Optional[str] = None) -> str:
    """
    Determine whether to bump minor or patch version.

    Logic:
    - If override is 'minor' or 'patch', use that.
    - If any changed file matches CORE_FILE_PREFIXES -> minor.
    - Otherwise -> patch.
    """
    if override in ("minor", "patch"):
        return override

    for f in changed_files:
        for prefix in CORE_FILE_PREFIXES:
            if f == prefix or f.startswith(prefix):
                return "minor"
    return "patch"


def bump_version(version: str, bump_type: str) -> str:
    """
    Increment a semantic version string.

    'minor' -> X.(Y+1).0
    'patch' -> X.Y.(Z+1)
    """
    parts = version.split(".")
    if len(parts) < 3:
        parts += ["0"] * (3 - len(parts))

    try:
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        print(f"WARNING: Could not parse version '{version}', starting from 0.1.0")
        major, minor, patch = 0, 1, 0

    if bump_type == "minor":
        minor += 1
        patch = 0
    else:
        patch += 1

    return f"{major}.{minor}.{patch}"


def get_commit_messages(repo_root: Path, since_ref: Optional[str]) -> List[str]:
    """
    Get one-line commit messages since `since_ref`.
    Returns list of message strings (without SHA prefix).
    """
    if since_ref:
        rc, stdout, _ = _run(
            ["git", "log", "--oneline", f"{since_ref}..HEAD"],
            cwd=repo_root,
        )
        if rc == 0 and stdout:
            lines = stdout.splitlines()
            return [" ".join(line.split()[1:]) for line in lines if line.strip()]

    # Fallback: last 50 commits
    rc, stdout, _ = _run(
        ["git", "log", "--oneline", "-50"],
        cwd=repo_root,
    )
    if rc == 0 and stdout:
        lines = stdout.splitlines()
        return [" ".join(line.split()[1:]) for line in lines if line.strip()]
    return []


def get_diff_stat(repo_root: Path, since_ref: Optional[str]) -> str:
    """Get abbreviated diff stat summary since `since_ref`."""
    if since_ref:
        rc, stdout, _ = _run(
            ["git", "diff", "--stat", f"{since_ref}..HEAD"],
            cwd=repo_root,
            timeout=60,
        )
        if rc == 0 and stdout:
            return stdout
    # Fallback
    rc, stdout, _ = _run(
        ["git", "diff", "--stat", "HEAD~50", "HEAD"],
        cwd=repo_root,
        timeout=60,
    )
    return stdout if rc == 0 else ""


def classify_commits(messages: List[str]) -> Dict[str, List[str]]:
    """
    Bucket commit messages into changelog sections using keyword matching.

    Sections: Added, Fixed, Changed, Removed, Other.
    Filters out [AF] artifact commits (they're noise).
    """
    categories: Dict[str, List[str]] = {
        "Added": [],
        "Changed": [],
        "Fixed": [],
        "Removed": [],
        "Other": [],
    }

    # Filter out [AF] artifact commits
    messages = [m for m in messages if "[AF]" not in m]

    for msg in messages:
        msg_lower = msg.lower()
        matched = False
        for section, keywords in KEYWORD_MAP:
            if any(kw in msg_lower for kw in keywords):
                categories[section].append(msg)
                matched = True
                break
        if not matched:
            categories["Other"].append(msg)

    return categories



def _extract_functions_from_diff(diff_text: str) -> List[str]:
    """
    Parse a unified diff and extract function/class names touched in changed hunks.

    Looks at context lines (starting with ' ', '+', '-') for Python and JS/TS
    function/class definitions. De-duplicates and caps at 5 names.
    """
    func_names: List[str] = []
    seen: set = set()

    # Patterns for Python and JS/TS definitions
    py_pattern = re.compile(r"^[\s+\-\s]*(?:async\s+)?def\s+(\w+)|^[\s+\-\s]*class\s+(\w+)")
    js_pattern = re.compile(
        r"^[\s+\-\s]*(?:export\s+)?(?:async\s+)?function\s+(\w+)"
        r"|^[\s+\-\s]*class\s+(\w+)"
        r"|^[\s+\-\s]*(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|\w+)\s*=>"
    )

    for line in diff_text.splitlines():
        # Only look at changed lines and context (not diff metadata)
        if line.startswith("@@") or line.startswith("---") or line.startswith("+++"):
            continue
        # Strip the leading +/- marker for matching
        stripped = line[1:] if line and line[0] in ("+", "-") else line

        for pattern in (py_pattern, js_pattern):
            m = pattern.match(stripped)
            if m:
                # First non-None group is the name
                name = next((g for g in m.groups() if g), None)
                if name and name not in seen:
                    seen.add(name)
                    func_names.append(name)
                break

        if len(func_names) >= 5:
            break

    return func_names[:5]


def get_changed_file_details(repo_root: Path, since_ref: Optional[str]) -> List[Dict]:
    """
    For each code file changed since `since_ref`, extract status (Added/Modified)
    and which function/class names were touched.

    Returns list of dicts: {file, status, functions}
    Capped at 20 files.
    """
    # Get name-status (A=added, M=modified, D=deleted, R=renamed, etc.)
    if since_ref:
        rc, stdout, _ = _run(
            ["git", "diff", "--name-status", f"{since_ref}..HEAD"],
            cwd=repo_root,
        )
    else:
        rc, stdout, _ = _run(
            ["git", "diff", "--name-status", "HEAD~50", "HEAD"],
            cwd=repo_root,
        )

    if rc != 0 or not stdout:
        return []

    details: List[Dict] = []
    for raw_line in stdout.splitlines():
        parts = raw_line.split("\t")
        if len(parts) < 2:
            continue
        status_code = parts[0].strip()[0]  # A, M, D, R, ...
        filepath = parts[-1].strip()       # last field handles renamed files

        suffix = Path(filepath).suffix.lower()
        if suffix not in CODE_EXTENSIONS:
            continue
        if suffix in IGNORE_EXTENSIONS:
            continue

        label = "Added" if status_code == "A" else "Modified"

        # Get per-file diff to extract function names
        if since_ref:
            rc2, diff_text, _ = _run(
                ["git", "diff", "--unified=0", f"{since_ref}..HEAD", "--", filepath],
                cwd=repo_root,
                timeout=20,
            )
        else:
            rc2, diff_text, _ = _run(
                ["git", "diff", "--unified=0", "HEAD~50", "HEAD", "--", filepath],
                cwd=repo_root,
                timeout=20,
            )

        functions: List[str] = []
        if rc2 == 0 and diff_text:
            functions = _extract_functions_from_diff(diff_text)

        details.append({"file": filepath, "status": label, "functions": functions})

        if len(details) >= 20:
            break

    return details

def generate_changelog_entry(
    version: str,
    categories: Dict[str, List[str]],
    diff_stat: str,
    prev_tag: Optional[str],
    file_details: Optional[List[Dict]] = None,
) -> str:
    """
    Build a Keep-a-Changelog formatted entry for the new version.

    Format:
        ## [X.Y.Z] - YYYY-MM-DD

        ### Added
        - ...

        ### Code Changes
        - **Added** `path/to/file.py` — `func_a`, `func_b`
        - **Modified** `path/to/other.js` — `foo`, `bar`

        ### Stats
        ```
        N files changed, X insertions(+), Y deletions(-)
        ```
    """
    date = datetime.now().strftime("%Y-%m-%d")
    lines: List[str] = [f"## [{version}] - {date}", ""]

    section_order = ["Added", "Changed", "Fixed", "Removed", "Other"]

    any_items = False
    for section in section_order:
        items = categories.get(section, [])
        if not items:
            continue
        any_items = True
        lines.append(f"### {section}")
        lines.append("")
        for item in items[:20]:  # cap at 20 items per section
            clean_item = item.strip()
            if clean_item:
                lines.append(f"- {clean_item}")
        lines.append("")

    if not any_items:
        lines.append("### Changed")
        lines.append("")
        lines.append("- General improvements and maintenance")
        lines.append("")

    # Code Changes section — enriched from actual git diff
    if file_details:
        lines.append("### Code Changes")
        lines.append("")
        total = len(file_details)
        cap = 20
        for detail in file_details[:cap]:
            label = detail["status"]          # "Added" or "Modified"
            filepath = detail["file"]
            funcs = detail.get("functions", [])
            if funcs:
                func_str = ", ".join(f"`{f}`" for f in funcs)
                lines.append(f"- **{label}** `{filepath}` — {func_str}")
            else:
                lines.append(f"- **{label}** `{filepath}`")
        if total > cap:
            lines.append(f"- _...and {total - cap} more files_")
        lines.append("")

    # Diff stat summary (last few lines contain the totals)
    if diff_stat:
        stat_lines = [l for l in diff_stat.strip().splitlines() if l.strip()]
        summary_lines = stat_lines[-5:] if len(stat_lines) >= 5 else stat_lines
        lines.append("### Stats")
        lines.append("")
        lines.append("```")
        lines.extend(summary_lines)
        lines.append("```")
        lines.append("")

    if prev_tag:
        lines.append(f"_Full diff: `git diff {prev_tag}..v{version}`_")
        lines.append("")

    return "\n".join(lines)


def update_changelog_file(repo_root: Path, entry: str) -> None:
    """
    Prepend `entry` into CHANGELOG.md after the top-level header.
    Creates CHANGELOG.md from scratch if it doesn't exist.
    """
    changelog_path = repo_root / "CHANGELOG.md"

    if not changelog_path.exists():
        content = "# Changelog\n\nAll notable changes to this project will be documented here.\n\n"
        changelog_path.write_text(content + entry + "\n", encoding="utf-8")
        print("  Created CHANGELOG.md")
        return

    content = changelog_path.read_text(encoding="utf-8")
    header = "# Changelog\n"
    idx = content.find(header)

    if idx == -1:
        new_content = "# Changelog\n\n" + entry + "\n" + content
    else:
        insert_pos = idx + len(header)
        new_content = content[:insert_pos] + "\n" + entry + "\n" + content[insert_pos:]

    changelog_path.write_text(new_content, encoding="utf-8")
    print("  Updated CHANGELOG.md")


def update_pyproject_version(repo_root: Path, new_version: str) -> None:
    """Update the version field in pyproject.toml (project section only)."""
    toml_path = repo_root / "pyproject.toml"
    content = toml_path.read_text(encoding="utf-8")

    # Locate [project] section boundaries to avoid touching [tool.*] version fields.
    project_match = re.search(r'^\[project\]', content, re.MULTILINE)
    if project_match:
        after_header = content[project_match.end():]
        next_section = re.search(r'^\[', after_header, re.MULTILINE)
        project_end = project_match.end() + (next_section.start() if next_section else len(after_header))
        block = content[project_match.start():project_end]
        new_block = re.sub(
            r'^(version\s*=\s*)"[^"]+"',
            f'\\1"{new_version}"',
            block,
            flags=re.MULTILINE,
        )
        if new_block == block:
            raise ValueError(f"Could not find version field in [project] section of {toml_path}")
        new_content = content[:project_match.start()] + new_block + content[project_end:]
    else:
        # No [project] header: replace the first occurrence only
        new_content = re.sub(
            r'^(version\s*=\s*)"[^"]+"',
            f'\\1"{new_version}"',
            content,
            count=1,
            flags=re.MULTILINE,
        )
        if new_content == content:
            raise ValueError(f"Could not find/replace version field in {toml_path}")

    toml_path.write_text(new_content, encoding="utf-8")
    print(f'  Updated pyproject.toml -> version = "{new_version}"')


def create_version_commit(repo_root: Path, new_version: str) -> bool:
    """
    Stage pyproject.toml and CHANGELOG.md and create a release commit.
    Returns True on success.
    """
    files_to_stage = [
        str(repo_root / "pyproject.toml"),
        str(repo_root / "CHANGELOG.md"),
    ]

    staged_any = False
    for fp in files_to_stage:
        if Path(fp).exists():
            rc, _, stderr = _run(["git", "add", fp], cwd=repo_root)
            if rc == 0:
                staged_any = True
            else:
                print(f"  WARNING: Could not stage {fp}: {stderr}")

    if not staged_any:
        print("  WARNING: Nothing staged for version commit")
        return False

    commit_msg = f"Release v{new_version} - automated release pipeline"
    rc, _, stderr = _run(["git", "commit", "-m", commit_msg], cwd=repo_root)
    if rc != 0:
        if "nothing to commit" in stderr.lower():
            print("  No changes to commit (files already up to date)")
            return True
        print(f"  WARNING: Version commit failed: {stderr}")
        return False

    print(f"  Created commit: '{commit_msg}'")
    return True


def create_annotated_tag(repo_root: Path, version: str, changelog_text: str) -> bool:
    """
    Create an annotated git tag vX.Y.Z with the changelog entry as message.
    Returns True on success, False if tag already exists or creation fails.
    """
    tag_name = f"v{version}"

    # Check if tag already exists
    rc, stdout, _ = _run(["git", "tag", "-l", tag_name], cwd=repo_root)
    if rc == 0 and stdout:
        print(f"  WARNING: Tag {tag_name} already exists -- skipping tag creation")
        return False

    rc, _, stderr = _run(
        ["git", "tag", "-a", tag_name, "-m", changelog_text],
        cwd=repo_root,
    )
    if rc != 0:
        print(f"  ERROR: Failed to create tag {tag_name}: {stderr}")
        return False

    print(f"  Created annotated tag: {tag_name}")
    return True


def push_tag_to_remote(repo_root: Path, version: str, remote: str = "origin") -> bool:
    """
    Push the release tag to the remote separately from code.
    Returns True on success.
    """
    tag_name = f"v{version}"
    rc, stdout, stderr = _run(
        ["git", "push", remote, tag_name],
        cwd=repo_root,
        timeout=60,
    )
    if rc != 0:
        print(f"  WARNING: Failed to push tag {tag_name} to {remote}: {stderr}")
        return False
    print(f"  Pushed tag {tag_name} to {remote}")
    return True


# ---------------------------------------------------------------------------
# Main pipeline class
# ---------------------------------------------------------------------------

class ReleasePipeline:
    """
    Orchestrates the full automated release pipeline.

    Usage:
        pipeline = ReleasePipeline(repo_root, push_tags=False, dry_run=False)
        success = pipeline.run()
    """

    def __init__(
        self,
        repo_root: Path,
        push_tags: bool = False,
        dry_run: bool = False,
        bump_override: Optional[str] = None,
        publish_pypi: bool = False,
    ) -> None:
        self.repo_root = repo_root
        self.push_tags = push_tags
        self.dry_run = dry_run
        self.bump_override = bump_override
        self.publish_pypi = publish_pypi

    def publish_to_pypi(self) -> bool:
        """
        Build the package and upload to PyPI.

        Requires:
          - python3 -m build  (pip install build)
          - python3 -m twine  (pip install twine)
          - ~/.pypirc with credentials, or TWINE_PASSWORD env var

        Gated by --publish-pypi flag; never runs in --dry-run mode.
        """
        print("\n[PyPI] Building package...")
        rc, out, err = _run(
            ["python3", "-m", "build"],
            cwd=self.repo_root,
            timeout=120,
        )
        if rc != 0:
            print(f"  ERROR: Build failed:\n{err or out}")
            return False
        print("  Build complete.")

        print("\n[PyPI] Uploading to PyPI via twine...")
        rc, out, err = _run(
            ["python3", "-m", "twine", "upload", "dist/*"],
            cwd=self.repo_root,
            timeout=120,
        )
        if rc != 0:
            print(f"  ERROR: Upload failed:\n{err or out}")
            return False
        print("  Upload complete.")
        return True

    def run(self) -> bool:
        """Execute the full release pipeline. Returns True on success."""
        print("\n" + "=" * 60)
        print("  AtlasForge Automated Release Pipeline")
        print("=" * 60)

        if self.dry_run:
            print("  DRY RUN -- no files will be modified\n")

        try:
            return self._run_pipeline()
        except KeyboardInterrupt:
            print("\nRelease pipeline cancelled by user.")
            return False
        except Exception as e:
            print(f"\nERROR: Release pipeline failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _run_pipeline(self) -> bool:
        """Internal pipeline execution."""

        # Step 1: Current version
        print("\n[1/8] Reading current version...")
        try:
            current_ver = get_current_version(self.repo_root)
        except FileNotFoundError as e:
            print(f"  ERROR: {e}")
            return False
        print(f"  Current version: {current_ver}")

        # Step 2: Previous release tag
        print("\n[2/8] Finding previous release tag...")
        prev_tag = get_previous_release_tag(self.repo_root)
        if prev_tag:
            print(f"  Previous tag: {prev_tag}")
        else:
            print("  No previous v* tag found -- using last 50 commits")

        # Step 3: Changed files
        print("\n[3/8] Analyzing changed files...")
        changed_files = get_changed_files(self.repo_root, prev_tag)
        print(f"  {len(changed_files)} files changed since {prev_tag or 'start'}")

        # Step 4: Determine bump type
        print("\n[4/8] Determining version bump...")
        bump_type = determine_bump_type(changed_files, self.bump_override)
        new_ver = bump_version(current_ver, bump_type)
        print(f"  Bump type: {bump_type} ({current_ver} -> {new_ver})")

        # Step 5: Commit messages
        print("\n[5/8] Collecting commit messages...")
        messages = get_commit_messages(self.repo_root, prev_tag)
        categories = classify_commits(messages)
        total_items = sum(len(v) for v in categories.values())
        print(f"  {len(messages)} commits -> {total_items} changelog items")
        for section, items in categories.items():
            if items:
                print(f"    {section}: {len(items)}")

        # Step 6: Diff stat
        print("\n[6/8] Getting diff statistics...")
        diff_stat = get_diff_stat(self.repo_root, prev_tag)
        if diff_stat:
            stat_lines = [l for l in diff_stat.strip().splitlines() if l.strip()]
            print(f"  {stat_lines[-1] if stat_lines else 'no stats'}")

        # Step 6b: Collect per-file code change details
        print("\n[6b] Extracting code change details...")
        file_details = get_changed_file_details(self.repo_root, prev_tag)
        if file_details:
            print(f"  {len(file_details)} code file(s) with change details")
        else:
            print("  No code file changes detected (config/docs only)")

        # Step 7: Generate changelog entry
        print("\n[7/8] Generating changelog entry...")
        changelog_entry = generate_changelog_entry(new_ver, categories, diff_stat, prev_tag, file_details)
        preview_lines = changelog_entry.splitlines()[:8]
        for line in preview_lines:
            print(f"    {line}")
        if len(changelog_entry.splitlines()) > 8:
            print(f"    ... ({len(changelog_entry.splitlines()) - 8} more lines)")

        # Dry run stops here
        if self.dry_run:
            print("\n[DRY RUN] Would perform these actions:")
            print(f'  - Update pyproject.toml: version = "{new_ver}"')
            print(f"  - Prepend to CHANGELOG.md: ## [{new_ver}] - {datetime.now().strftime('%Y-%m-%d')}")
            print(f"  - Create commit: 'Release v{new_ver} - automated release pipeline'")
            print(f"  - Create annotated tag: v{new_ver}")
            if self.push_tags:
                print(f"  - Push tag: git push origin v{new_ver}")
            if self.publish_pypi:
                print("  - Run: python3 -m build && twine upload dist/*")
            print("\n[DRY RUN] No changes made.")
            return True

        # Step 8: Apply changes
        print("\n[8/8] Applying release changes...")

        update_pyproject_version(self.repo_root, new_ver)
        update_changelog_file(self.repo_root, changelog_entry)
        create_version_commit(self.repo_root, new_ver)
        tag_ok = create_annotated_tag(self.repo_root, new_ver, changelog_entry)

        if self.push_tags and tag_ok:
            push_tag_to_remote(self.repo_root, new_ver)

        if self.publish_pypi:
            self.publish_to_pypi()

        print("\n" + "=" * 60)
        if tag_ok:
            print(f"  Release v{new_ver} complete!")
            print(f"  Tag: v{new_ver}")
            if not self.push_tags:
                print(f"  To push tag:  git push origin v{new_ver}")
        else:
            print(f"  Version files updated to v{new_ver}, tag skipped (see warnings).")
        print(f"  CHANGELOG.md updated.")
        print("=" * 60 + "\n")

        return True


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AtlasForge automated release pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/release_pipeline.py --dry-run
  python3 scripts/release_pipeline.py --push-tags
  python3 scripts/release_pipeline.py --bump=minor --push-tags
        """,
    )
    parser.add_argument(
        "--push-tags",
        action="store_true",
        help="Push the created release tag to origin",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making any changes",
    )
    parser.add_argument(
        "--bump",
        choices=["minor", "patch"],
        default=None,
        help="Override automatic bump type detection",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Path to git repository root (auto-detected if not set)",
    )
    parser.add_argument(
        "--publish-pypi",
        action="store_true",
        help="After tagging, build and upload package to PyPI (requires build + twine)",
    )

    args = parser.parse_args()

    repo_root = args.repo_root or get_repo_root()
    pipeline = ReleasePipeline(
        repo_root=repo_root,
        push_tags=args.push_tags,
        dry_run=args.dry_run,
        bump_override=args.bump,
        publish_pypi=args.publish_pypi,
    )
    success = pipeline.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
