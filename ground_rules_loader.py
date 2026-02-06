#!/usr/bin/env python3
"""
Ground-rules loading helpers with provider-aware overlays.

Resolution order for standard R&D prompts:
1. GROUND_RULES.md (base)
2. GROUND_RULES_<PROVIDER>.md (optional overlay, appended if present)

Resolution order for investigation prompts:
1. investigations/INV_GROUND_RULES.md (base)
2. investigations/INV_GROUND_RULES_<PROVIDER>.md (optional overlay, appended if present)
"""

import os
from pathlib import Path
from typing import Optional, Tuple

import io_utils
from atlasforge_config import BASE_DIR, STATE_DIR, INVESTIGATIONS_DIR

SUPPORTED_LLM_PROVIDERS = {"claude", "codex"}
DEFAULT_LLM_PROVIDER = "claude"
LLM_PROVIDER_PATH = STATE_DIR / "llm_provider.json"


def normalize_llm_provider(provider: Optional[str]) -> str:
    """Normalize provider identifier to supported values."""
    candidate = str(provider or "").strip().lower()
    if candidate in SUPPORTED_LLM_PROVIDERS:
        return candidate
    return DEFAULT_LLM_PROVIDER


def get_active_llm_provider(provider: Optional[str] = None) -> str:
    """
    Resolve active provider.

    Precedence: explicit argument -> env var -> state file -> default.
    """
    if provider:
        return normalize_llm_provider(provider)

    env_provider = os.environ.get("ATLASFORGE_LLM_PROVIDER")
    if env_provider:
        return normalize_llm_provider(env_provider)

    try:
        data = io_utils.atomic_read_json(LLM_PROVIDER_PATH, {})
        if isinstance(data, dict) and data.get("provider"):
            return normalize_llm_provider(data.get("provider"))
    except Exception:
        pass

    return DEFAULT_LLM_PROVIDER


def _rules_base_name(investigation: bool) -> str:
    return "INV_GROUND_RULES" if investigation else "GROUND_RULES"


def _rules_parent_dir(root: Optional[Path], investigation: bool) -> Path:
    if root is not None:
        return root / "investigations" if investigation else root
    return INVESTIGATIONS_DIR if investigation else BASE_DIR


def resolve_ground_rules_files(
    provider: Optional[str] = None,
    investigation: bool = False,
    root: Optional[Path] = None,
) -> Tuple[str, Path, Path]:
    """
    Resolve provider and candidate ground-rules files.

    Returns:
        Tuple of (resolved_provider, base_rules_path, provider_overlay_path)
    """
    resolved_provider = get_active_llm_provider(provider)
    parent = _rules_parent_dir(root, investigation)
    base_name = _rules_base_name(investigation)

    base_rules_path = parent / f"{base_name}.md"
    overlay_path = parent / f"{base_name}_{resolved_provider.upper()}.md"

    return resolved_provider, base_rules_path, overlay_path


def load_ground_rules(
    provider: Optional[str] = None,
    investigation: bool = False,
    root: Optional[Path] = None,
) -> tuple[str, Path, Optional[Path], str]:
    """
    Load base ground rules and append provider overlay when present.

    Returns:
        Tuple of (combined_rules, base_path, overlay_path_or_none, resolved_provider)
    """
    resolved_provider, base_path, overlay_path = resolve_ground_rules_files(
        provider=provider,
        investigation=investigation,
        root=root,
    )

    sections = []

    if base_path.exists():
        sections.append(base_path.read_text())

    selected_overlay: Optional[Path] = None
    if overlay_path.exists():
        sections.append(
            "\n=== PROVIDER-SPECIFIC GROUND RULES "
            f"({resolved_provider.upper()}) ===\n"
            f"{overlay_path.read_text()}\n"
            "=== END PROVIDER-SPECIFIC GROUND RULES ===\n"
        )
        selected_overlay = overlay_path

    combined = "\n\n".join(s.strip() for s in sections if s and s.strip())
    return combined, base_path, selected_overlay, resolved_provider
