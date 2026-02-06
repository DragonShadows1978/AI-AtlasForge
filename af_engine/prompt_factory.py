"""
af_engine.prompt_factory - Template-Based Prompt Generation

This module provides the PromptFactory class for generating stage prompts
with context injection from various sources (KB, AfterImage, Recovery).
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

from .stages.base import StageContext
from .state_manager import StateManager
from ground_rules_loader import load_ground_rules, get_active_llm_provider

logger = logging.getLogger(__name__)


class PromptFactory:
    """
    Factory for generating stage prompts with context injection.

    The PromptFactory provides:
    - Template-based prompt generation
    - Ground rules loading and caching
    - Context injection from KB, AfterImage, Recovery
    - Preference and criteria formatting
    """

    # Ground rules file path (relative to AtlasForge root)
    GROUND_RULES_FILE = "GROUND_RULES.md"

    def __init__(self, atlasforge_root: Optional[Path] = None):
        """
        Initialize the prompt factory.

        Args:
            atlasforge_root: Path to AtlasForge root directory
        """
        self.root = atlasforge_root or Path(__file__).parent.parent
        self._ground_rules_cache: Optional[str] = None
        self._ground_rules_cache_provider: Optional[str] = None
        self._ground_rules_cache_by_provider: Dict[str, str] = {}

    def build_context(self, state: StateManager) -> StageContext:
        """
        Build a StageContext from StateManager.

        Args:
            state: StateManager with mission data

        Returns:
            StageContext for use with stage handlers
        """
        mission = state.mission

        return StageContext(
            mission=mission,
            mission_id=state.mission_id,
            original_mission=mission.get("original_problem_statement", ""),
            problem_statement=mission.get("problem_statement", "No mission defined"),
            workspace_dir=str(state.get_workspace_dir()),
            artifacts_dir=str(state.get_artifacts_dir()),
            research_dir=str(state.get_research_dir()),
            tests_dir=str(state.get_tests_dir()),
            cycle_number=state.cycle_number,
            cycle_budget=state.cycle_budget,
            iteration=state.iteration,
            max_iterations=mission.get("max_iterations", 10),
            history=state.history,
            cycle_history=state.cycle_history,
            preferences=mission.get("preferences", {}),
            success_criteria=mission.get("success_criteria", []),
        )

    def get_ground_rules(self, provider: Optional[str] = None) -> str:
        """
        Load and cache ground rules from file.

        Returns:
            Ground rules content or empty string if not found
        """
        # Preserve legacy behavior for tests/customized file names.
        if self.GROUND_RULES_FILE != "GROUND_RULES.md":
            if self._ground_rules_cache is not None:
                return self._ground_rules_cache

            ground_rules_path = self.root / self.GROUND_RULES_FILE
            try:
                if ground_rules_path.exists():
                    self._ground_rules_cache = ground_rules_path.read_text()
                    logger.debug(f"Loaded ground rules from {ground_rules_path}")
                else:
                    logger.warning(f"Ground rules not found at {ground_rules_path}")
                    self._ground_rules_cache = ""
            except Exception as e:
                logger.error(f"Failed to load ground rules: {e}")
                self._ground_rules_cache = ""
            return self._ground_rules_cache

        resolved_provider = get_active_llm_provider(provider)
        cached = self._ground_rules_cache_by_provider.get(resolved_provider)
        if cached is not None:
            self._ground_rules_cache = cached
            self._ground_rules_cache_provider = resolved_provider
            return cached

        try:
            rules, base_path, overlay_path, _ = load_ground_rules(
                provider=resolved_provider,
                investigation=False,
                root=self.root,
            )
            if not rules:
                logger.warning(
                    f"Ground rules not found for provider '{resolved_provider}' "
                    f"(base path: {base_path})"
                )
            else:
                overlay_msg = f" + overlay {overlay_path}" if overlay_path else ""
                logger.debug(
                    f"Loaded ground rules for provider '{resolved_provider}' "
                    f"from {base_path}{overlay_msg}"
                )
        except Exception as e:
            logger.error(f"Failed to load ground rules: {e}")
            rules = ""

        self._ground_rules_cache_by_provider[resolved_provider] = rules
        self._ground_rules_cache = rules
        self._ground_rules_cache_provider = resolved_provider
        return rules

    def inject_kb_context(
        self,
        prompt: str,
        mission_context: str,
        kb_provider: Optional[Any] = None,
        use_cache: bool = True,
    ) -> str:
        """
        Inject Knowledge Base context into prompt.

        Uses the kb_cache module for lazy loading and caching to reduce
        latency from ~750ms to <10ms for cached queries.

        Args:
            prompt: Original prompt
            mission_context: Mission context for KB search
            kb_provider: Optional KB provider instance (deprecated, ignored)
            use_cache: Whether to use cached results (default True)

        Returns:
            Prompt with KB context injected
        """
        try:
            # Use the cached KB query for performance
            from .kb_cache import query_relevant_learnings

            learnings = query_relevant_learnings(
                mission_context,
                top_k=5,
                use_cache=use_cache,
            )

            if not learnings:
                return prompt

            kb_section = self._format_kb_learnings(learnings)

            # Insert after ground rules but before mission details
            if "=== CURRENT MISSION ===" in prompt:
                parts = prompt.split("=== CURRENT MISSION ===")
                return f"{parts[0]}\n{kb_section}\n=== CURRENT MISSION ==={parts[1]}"
            else:
                return f"{prompt}\n\n{kb_section}"

        except Exception as e:
            logger.warning(f"Failed to inject KB context: {e}")
            return prompt

    def _format_kb_learnings(self, learnings: List[Dict]) -> str:
        """Format KB learnings for prompt injection."""
        lines = [
            "=== LEARNINGS FROM PAST MISSIONS ===",
            "",
            "The following learnings from previous missions may be relevant:",
            "",
        ]

        for learning in learnings:
            title = learning.get("title", "Untitled")
            content = learning.get("content", "")
            mission_id = learning.get("mission_id", "unknown")
            category = learning.get("category", "general")

            lines.append(f"**{title}** [{category}] (from {mission_id})")
            lines.append(content[:500])  # Truncate long content
            lines.append("")

        lines.append("Consider these learnings when planning your approach.")
        lines.append("")

        return "\n".join(lines)

    def inject_afterimage_context(
        self,
        prompt: str,
        query: str,
        afterimage_provider: Optional[Any] = None,
    ) -> str:
        """
        Inject AfterImage (episodic code memory) context into prompt.

        Args:
            prompt: Original prompt
            query: Query string for memory search
            afterimage_provider: Optional AfterImage provider instance

        Returns:
            Prompt with AfterImage context injected
        """
        if afterimage_provider is None:
            try:
                from atlasforge_enhancements.afterimage import AfterImage
                afterimage_provider = AfterImage()
            except ImportError:
                # Fallback to local AI-AfterImage package when enhancement wrapper
                # is not present.
                try:
                    import sys
                    from atlasforge_config import BASE_DIR, WORKSPACE_DIR

                    candidates = [
                        WORKSPACE_DIR / "AI-AfterImage",
                        WORKSPACE_DIR / "AfterImage",
                        BASE_DIR.parent / "AI-AfterImage",
                        Path.home() / "AI-AfterImage",
                    ]
                    for candidate in candidates:
                        if candidate.exists():
                            candidate_str = str(candidate)
                            if candidate_str not in sys.path:
                                sys.path.insert(0, candidate_str)

                    from afterimage.search import HybridSearch
                    afterimage_provider = HybridSearch()
                except ImportError:
                    logger.debug("AfterImage not available")
                    return prompt

        try:
            try:
                # Wrapper interface
                memories = afterimage_provider.search(query, max_results=3)
            except TypeError:
                # HybridSearch interface
                memories = afterimage_provider.search(query, limit=3, threshold=0.01)

            if not memories:
                return prompt

            ai_section = self._format_afterimage_memories(memories)

            # Append to prompt
            return f"{prompt}\n\n{ai_section}"

        except Exception as e:
            logger.warning(f"Failed to inject AfterImage context: {e}")
            return prompt

    def _format_afterimage_memories(self, memories: List[Dict]) -> str:
        """Format AfterImage memories for prompt injection."""
        lines = [
            "=== CODE MEMORY (AfterImage) ===",
            "",
            "Relevant code patterns from recent work:",
            "",
        ]

        for memory in memories:
            if isinstance(memory, dict):
                file_path = memory.get("file_path", "unknown")
                snippet = memory.get("snippet", memory.get("new_code", ""))
                context = memory.get("context", "")
            else:
                # SearchResult dataclass/object path.
                file_path = getattr(memory, "file_path", "unknown")
                snippet = getattr(memory, "new_code", "") or getattr(memory, "snippet", "")
                context = getattr(memory, "context", "")

            lines.append(f"**{file_path}**")
            if context:
                lines.append(f"Context: {context}")
            lines.append("```")
            lines.append(snippet[:1000])  # Truncate long snippets
            lines.append("```")
            lines.append("")

        return "\n".join(lines)

    def inject_recovery_context(
        self,
        prompt: str,
        recovery_info: Optional[Dict] = None,
    ) -> str:
        """
        Inject crash recovery context into prompt.

        Args:
            prompt: Original prompt
            recovery_info: Recovery information dict

        Returns:
            Prompt with recovery context injected
        """
        if not recovery_info:
            return prompt

        recovery_section = self._format_recovery_context(recovery_info)

        # Insert at beginning of mission section
        if "=== CURRENT MISSION ===" in prompt:
            parts = prompt.split("=== CURRENT MISSION ===")
            return f"{parts[0]}\n{recovery_section}\n=== CURRENT MISSION ==={parts[1]}"
        else:
            return f"{recovery_section}\n\n{prompt}"

    def _format_recovery_context(self, recovery_info: Dict) -> str:
        """Format recovery info for prompt injection."""
        lines = [
            "=== CRASH RECOVERY ===",
            f"Your previous session crashed during the **{recovery_info.get('stage', 'UNKNOWN')}** stage.",
            "",
            f"**Mission:** {recovery_info.get('mission_id', 'unknown')}",
            f"**Iteration:** {recovery_info.get('iteration', 0)}",
            f"**Cycle:** {recovery_info.get('cycle', 1)}",
            "",
        ]

        if recovery_info.get("progress"):
            lines.append("**Progress at crash:**")
            lines.append(str(recovery_info["progress"]))
            lines.append("")

        if recovery_info.get("hint"):
            lines.append(f"**Recovery hint:** {recovery_info['hint']}")
            lines.append("")

        lines.append("IMPORTANT: Resume from where you left off. Do NOT restart from scratch.")
        lines.append("=== END CRASH RECOVERY ===")
        lines.append("")

        return "\n".join(lines)

    def format_preferences(self, preferences: Dict[str, Any]) -> str:
        """
        Format user preferences for prompt inclusion.

        Args:
            preferences: Dictionary of preferences

        Returns:
            Formatted preferences string
        """
        if not preferences:
            return ""

        # Handle case where preferences is a string instead of dict
        if isinstance(preferences, str):
            return f"User Preferences: {preferences}"

        lines = ["User Preferences:"]

        for key, value in preferences.items():
            # Format preference name nicely
            name = key.replace("_", " ").title()
            lines.append(f"  - {name}: {value}")

        return "\n".join(lines)

    def format_success_criteria(self, criteria: List[str]) -> str:
        """
        Format success criteria for prompt inclusion.

        Args:
            criteria: List of success criteria strings

        Returns:
            Formatted criteria string
        """
        if not criteria:
            return ""

        lines = ["Success Criteria:"]
        for i, criterion in enumerate(criteria, 1):
            lines.append(f"  {i}. {criterion}")

        return "\n".join(lines)

    def format_history(self, history: List[Dict], max_entries: int = 10) -> str:
        """
        Format mission history for prompt inclusion.

        Args:
            history: List of history entries
            max_entries: Maximum entries to include

        Returns:
            Formatted history string
        """
        if not history:
            return "No history yet."

        recent = history[-max_entries:]
        lines = ["Recent History:"]

        for entry in recent:
            timestamp = entry.get("timestamp", "unknown")
            stage = entry.get("stage", "unknown")
            event = entry.get("event", "unknown")

            # Truncate long events
            if len(event) > 100:
                event = event[:100] + "..."

            lines.append(f"  [{timestamp[:19]}] {stage}: {event}")

        return "\n".join(lines)

    def assemble_prompt(
        self,
        stage_prompt: str,
        context: StageContext,
        include_ground_rules: bool = True,
        include_mission_header: bool = True,
    ) -> str:
        """
        Assemble a complete prompt with all components.

        Args:
            stage_prompt: Stage-specific prompt content
            context: StageContext with mission data
            include_ground_rules: Whether to include ground rules
            include_mission_header: Whether to include mission header

        Returns:
            Complete assembled prompt
        """
        parts = []

        # Ground rules (if requested and available)
        if include_ground_rules:
            ground_rules = self.get_ground_rules(context.mission.get("llm_provider"))
            if ground_rules:
                parts.append("=== GROUND RULES (READ CAREFULLY) ===")
                parts.append(ground_rules)
                parts.append("=== END GROUND RULES ===")
                parts.append("")

        # Mission header
        if include_mission_header:
            parts.append(f"CURRENT MISSION: {context.problem_statement}")
            parts.append(f"CURRENT STAGE: {context.mission.get('current_stage', 'UNKNOWN')}")
            parts.append(f"ITERATION: {context.iteration}")
            parts.append(f"WORKSPACE: {context.workspace_dir}")
            parts.append("")

        # History summary
        if context.history:
            history_summary = self.format_history(context.history)
            parts.append("=== RECENT HISTORY ===")
            parts.append(history_summary)
            parts.append("")

        # Stage-specific prompt
        parts.append(stage_prompt)

        # Preferences and criteria
        if context.preferences:
            parts.append("")
            parts.append(self.format_preferences(context.preferences))

        if context.success_criteria:
            parts.append("")
            parts.append(self.format_success_criteria(context.success_criteria))

        return "\n".join(parts)
