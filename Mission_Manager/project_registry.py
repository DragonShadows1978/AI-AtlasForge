#!/usr/bin/env python3
"""Project identity helpers for mission suggestions.

Mission suggestions are often generated long after the originating mission has
completed, so project identity has to be recoverable from text, source metadata,
and known project names. This module keeps that logic out of the dashboard
routes and storage CRUD code.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

BASE_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = BASE_DIR / "workspace"

_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")

_CANONICAL_ALIASES = {
    "ai atlasforge": "AI-AtlasForge",
    "ai-atlasforge": "AI-AtlasForge",
    "ai_atlasforge": "AI-AtlasForge",
    "atlasforge": "AI-AtlasForge",
    "af engine": "AI-AtlasForge",
    "af_engine": "AI-AtlasForge",
    "mission suggestions": "AI-AtlasForge",
    "mission control": "AI-AtlasForge",
    "mcp agent streams": "MCP Agent Streams",
    "agent streams": "MCP Agent Streams",
    "agent stream manager": "MCP Agent Streams",
    "ai afterimage": "AI-AfterImage",
    "ai-afterimage": "AI-AfterImage",
    "afterimage": "AI-AfterImage",
    "ai storyforge": "AI-StoryForge",
    "ai-storyforge": "AI-StoryForge",
    "storyforge": "AI-StoryForge",
    "stick figure fighter": "Stick Figure Fighter",
    "stick_figure_fighter": "Stick Figure Fighter",
    "lexibank": "Lexibank Experiments",
    "lexibank experiments": "Lexibank Experiments",
    "rpg generation pipeline": "RPG Generation Pipeline",
    "rpg pipeline": "RPG Generation Pipeline",
}

_PROJECT_PHRASES = (
    "Stick Figure Fighter",
    "Stick_Figure_Fighter",
    "Lexibank Experiments",
    "Lexibank",
    "RPG Generation Pipeline",
    "MCP Agent Streams",
    "AI-AfterImage",
    "AI-StoryForge",
    "AI-AtlasForge",
)

_ATLASFORGE_MARKERS = (
    "suggestion_storage.py",
    "dashboard_modules/",
    "dashboard_static/",
    "atlasforge_conductor",
    "mission suggestion",
    "mission control",
    "web_proxy",
    "webproxy",
    "research_agent",
    "agent_stream_manager",
    "af_engine",
)


def project_slug(name: str) -> str:
    """Return a stable, comparable slug for a project display name."""
    raw = str(name or "").strip().lower()
    raw = raw.replace("&", " and ")
    slug = _SEPARATOR_RE.sub("-", raw).strip("-")
    return slug[:80]


def canonicalize_project_name(name: Any, known_projects: Optional[Iterable[str]] = None) -> str:
    """Normalize user/agent project text to a canonical display name."""
    raw = str(name or "").strip()
    if not raw:
        return ""

    raw = re.sub(r"\s+", " ", raw)
    raw = raw.strip(" \"'")
    slug = project_slug(raw)

    for known in known_projects or ():
        known_name = str(known or "").strip()
        if known_name and project_slug(known_name) == slug:
            return known_name

    alias_key = raw.lower().replace("_", " ").replace("-", " ")
    alias_key = re.sub(r"\s+", " ", alias_key).strip()
    if alias_key in _CANONICAL_ALIASES:
        return _CANONICAL_ALIASES[alias_key]
    if slug in _CANONICAL_ALIASES:
        return _CANONICAL_ALIASES[slug]

    if "_" in raw and " " not in raw:
        raw = raw.replace("_", " ")
    if raw.isupper() and len(raw) <= 6:
        return raw
    if "-" in raw:
        parts = [p for p in raw.split("-") if p]
        return "-".join(_title_part(p) for p in parts)[:100]
    return " ".join(_title_part(p) for p in raw.split())[:100]


def _title_part(value: str) -> str:
    if value.upper() == "AI":
        return "AI"
    if value.upper() == "RPG":
        return "RPG"
    if value.upper() == "MCP":
        return "MCP"
    if value.lower() in {"api", "ui", "db"}:
        return value.upper()
    if any(ch.isupper() for ch in value[1:]):
        return value
    return value[:1].upper() + value[1:]


def workspace_projects() -> List[str]:
    """Return plausible project names from workspace directories."""
    if not WORKSPACE_DIR.exists():
        return []
    ignored = {"artifacts", "research", "tests", ".junk"}
    projects = []
    for item in WORKSPACE_DIR.iterdir():
        if not item.is_dir() or item.name.startswith(".") or item.name in ignored:
            continue
        canonical = canonicalize_project_name(item.name)
        if canonical and not canonical.lower().startswith(("project-", "project_")):
            projects.append(canonical)
    return sorted(set(projects), key=str.lower)


def infer_project_name(
    suggestion: Dict[str, Any],
    known_projects: Optional[Iterable[str]] = None,
    default: str = "AI-AtlasForge",
) -> str:
    """Infer a project for a suggestion using known projects and content cues."""
    known = [canonicalize_project_name(p) for p in (known_projects or []) if str(p or "").strip()]
    known = [p for p in known if p]
    text = _suggestion_text(suggestion)
    text_lower = text.lower()

    source = str(suggestion.get("project_source") or "").strip()
    explicit = suggestion.get("project_name") or suggestion.get("project")
    if source in {"explicit", "merged"} and isinstance(explicit, str) and explicit.strip():
        return canonicalize_project_name(explicit, known)

    for project in known:
        if _project_mentioned(project, text_lower):
            return project

    for phrase in _PROJECT_PHRASES:
        if _project_mentioned(phrase, text_lower):
            return canonicalize_project_name(phrase, known)

    for alias, canonical in _CANONICAL_ALIASES.items():
        if alias in text_lower:
            return canonicalize_project_name(canonical, known)

    if any(marker in text_lower for marker in _ATLASFORGE_MARKERS):
        return canonicalize_project_name("AI-AtlasForge", known)

    try:
        from project_name_resolver import resolve_project_name
        resolved = resolve_project_name(text, str(suggestion.get("id") or "mission_00000000"))
        if resolved and not resolved.startswith("project_"):
            return canonicalize_project_name(resolved, known)
    except Exception:
        pass

    return canonicalize_project_name(default, known)


def _project_mentioned(project: str, text_lower: str) -> bool:
    project_slug_value = project_slug(project)
    project_words = project_slug_value.replace("-", " ")
    normalized_text = _SEPARATOR_RE.sub(" ", text_lower).strip()
    return project.lower() in text_lower or project_words in normalized_text


def _suggestion_text(suggestion: Dict[str, Any]) -> str:
    parts = []
    for key in (
        "mission_title",
        "mission_description",
        "rationale",
        "source_mission_summary",
        "scope_context",
        "original_mission_title",
        "original_mission_description",
    ):
        value = suggestion.get(key)
        if value:
            parts.append(str(value))
    tags = suggestion.get("auto_tags")
    if isinstance(tags, list):
        parts.extend(str(tag) for tag in tags)
    drift = suggestion.get("drift_context")
    if isinstance(drift, dict):
        parts.append(" ".join(str(v) for v in drift.values() if isinstance(v, (str, int, float))))
    return "\n".join(parts)


def project_options_from_suggestions(suggestions: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Summarize canonical projects currently represented in suggestions."""
    counts: Dict[str, int] = {}
    slugs: Dict[str, str] = {}
    for suggestion in suggestions:
        name = canonicalize_project_name(suggestion.get("project_name"))
        if not name:
            name = infer_project_name(suggestion, counts.keys())
        slug = project_slug(name)
        if not slug:
            continue
        counts[name] = counts.get(name, 0) + 1
        slugs[name] = slug
    return [
        {"project_name": name, "project_slug": slugs[name], "count": counts[name]}
        for name in sorted(counts, key=str.lower)
    ]
