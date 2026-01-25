"""
af_engine.stage_registry - Stage Handler Discovery and Registration

This module provides the StageRegistry class for loading, discovering,
and managing stage handlers. Supports both code-based and YAML-based
stage definitions.
"""

import importlib
import logging
from pathlib import Path
from typing import Dict, Optional, Type

from .stages.base import StageHandler, BaseStageHandler, StageRestrictions
from .config import load_stage_definitions, get_stage_config

logger = logging.getLogger(__name__)


class StageRegistry:
    """
    Registry for stage handlers.

    The StageRegistry manages the discovery and instantiation of stage handlers.
    It supports:
    - Loading handlers from the stages module
    - Loading handlers from YAML configuration
    - Custom handler registration
    - Handler caching for reuse
    """

    # Default handler mapping (module-based)
    DEFAULT_HANDLERS = {
        'PLANNING': ('af_engine.stages.planning', 'PlanningStageHandler'),
        'BUILDING': ('af_engine.stages.building', 'BuildingStageHandler'),
        'TESTING': ('af_engine.stages.testing', 'TestingStageHandler'),
        'ANALYZING': ('af_engine.stages.analyzing', 'AnalyzingStageHandler'),
        'CYCLE_END': ('af_engine.stages.cycle_end', 'CycleEndStageHandler'),
        'COMPLETE': ('af_engine.stages.complete', 'CompleteStageHandler'),
    }

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize the stage registry.

        Args:
            config_path: Optional path to YAML config (uses default if not provided)
        """
        self._handlers: Dict[str, StageHandler] = {}
        self._handler_classes: Dict[str, Type[StageHandler]] = {}
        self._config: Dict = {}

        # Load configuration
        if config_path and config_path.exists():
            from .config import load_yaml
            self._config = load_yaml(config_path)
        else:
            self._config = load_stage_definitions()

    def get_handler(self, stage_name: str) -> StageHandler:
        """
        Get a stage handler instance.

        Handlers are cached after first instantiation.

        Args:
            stage_name: Name of the stage (e.g., 'PLANNING')

        Returns:
            StageHandler instance for the stage

        Raises:
            KeyError: If stage_name is not valid
            ImportError: If handler module cannot be loaded
        """
        # Handle None or empty stage names by defaulting to PLANNING
        if not stage_name:
            logger.warning("Null or empty stage_name passed to get_handler, defaulting to PLANNING")
            stage_name = "PLANNING"
        stage_upper = stage_name.upper()

        # Return cached handler if available
        if stage_upper in self._handlers:
            return self._handlers[stage_upper]

        # Instantiate and cache
        handler_class = self.get_handler_class(stage_upper)
        handler = handler_class()
        self._handlers[stage_upper] = handler

        logger.debug(f"Instantiated handler for {stage_upper}: {handler_class.__name__}")
        return handler

    def get_handler_class(self, stage_name: str) -> Type[StageHandler]:
        """
        Get the handler class for a stage.

        Args:
            stage_name: Name of the stage

        Returns:
            Handler class (not instantiated)

        Raises:
            KeyError: If stage not found
            ImportError: If module cannot be loaded
        """
        stage_upper = stage_name.upper()

        # Return cached class if available
        if stage_upper in self._handler_classes:
            return self._handler_classes[stage_upper]

        # Try YAML config first
        stage_config = self._config.get('stages', {}).get(stage_upper, {})
        if stage_config:
            module_name = stage_config.get('handler_module')
            class_name = stage_config.get('handler_class')
            if module_name and class_name:
                handler_class = self._load_handler_class(module_name, class_name)
                self._handler_classes[stage_upper] = handler_class
                return handler_class

        # Fall back to default handlers
        if stage_upper in self.DEFAULT_HANDLERS:
            module_name, class_name = self.DEFAULT_HANDLERS[stage_upper]
            handler_class = self._load_handler_class(module_name, class_name)
            self._handler_classes[stage_upper] = handler_class
            return handler_class

        raise KeyError(f"No handler found for stage: {stage_name}")

    def _load_handler_class(
        self,
        module_name: str,
        class_name: str
    ) -> Type[StageHandler]:
        """
        Load a handler class from a module.

        Args:
            module_name: Full module path (e.g., 'af_engine.stages.planning')
            class_name: Class name within the module

        Returns:
            The handler class

        Raises:
            ImportError: If module or class cannot be loaded
        """
        try:
            module = importlib.import_module(module_name)
            handler_class = getattr(module, class_name)

            # Validate it looks like a handler
            if not hasattr(handler_class, 'stage_name'):
                logger.warning(
                    f"Handler {class_name} missing stage_name attribute"
                )

            return handler_class

        except ImportError as e:
            logger.error(f"Failed to import {module_name}: {e}")
            raise
        except AttributeError as e:
            logger.error(f"Class {class_name} not found in {module_name}: {e}")
            raise ImportError(f"Class {class_name} not found in {module_name}")

    def register_handler(
        self,
        stage_name: str,
        handler_class: Type[StageHandler]
    ) -> None:
        """
        Register a custom handler class.

        Args:
            stage_name: Stage name to register for
            handler_class: Handler class to use
        """
        stage_upper = stage_name.upper()
        self._handler_classes[stage_upper] = handler_class

        # Clear cached instance if exists
        if stage_upper in self._handlers:
            del self._handlers[stage_upper]

        logger.info(f"Registered custom handler for {stage_upper}: {handler_class.__name__}")

    def get_restrictions(self, stage_name: str) -> StageRestrictions:
        """
        Get restrictions for a stage.

        First checks YAML config, then falls back to handler's get_restrictions().

        Args:
            stage_name: Stage name

        Returns:
            StageRestrictions for the stage
        """
        if not stage_name:
            stage_name = "PLANNING"
        stage_upper = stage_name.upper()

        # Check YAML config first
        stage_config = self._config.get('stages', {}).get(stage_upper, {})
        if 'restrictions' in stage_config:
            restrictions_config = stage_config['restrictions']
            return StageRestrictions(
                allowed_tools=restrictions_config.get('allowed_tools', []),
                blocked_tools=restrictions_config.get('blocked_tools', []),
                allowed_write_paths=restrictions_config.get('allowed_write_paths', ['*']),
                forbidden_write_paths=restrictions_config.get('forbidden_write_paths', []),
                allow_bash=restrictions_config.get('allow_bash', True),
                read_only=restrictions_config.get('read_only', False),
            )

        # Fall back to handler
        handler = self.get_handler(stage_upper)
        return handler.get_restrictions()

    def get_transitions(self, stage_name: str) -> Dict[str, str]:
        """
        Get valid transitions for a stage.

        Args:
            stage_name: Stage name

        Returns:
            Dict mapping response status to next stage
        """
        if not stage_name:
            stage_name = "PLANNING"
        stage_upper = stage_name.upper()
        stage_config = self._config.get('stages', {}).get(stage_upper, {})
        return stage_config.get('transitions', {})

    def get_all_stages(self) -> list:
        """Get list of all registered stage names."""
        # Combine config stages and default stages
        config_stages = set(self._config.get('stages', {}).keys())
        default_stages = set(self.DEFAULT_HANDLERS.keys())
        return sorted(config_stages | default_stages)

    def is_valid_stage(self, stage_name: str) -> bool:
        """Check if a stage name is valid."""
        if not stage_name:
            return False
        return stage_name.upper() in self.get_all_stages()

    def reload_config(self) -> None:
        """Reload YAML configuration."""
        self._config = load_stage_definitions()
        logger.info("Reloaded stage configuration")


# Global registry instance
_registry: Optional[StageRegistry] = None


def get_registry() -> StageRegistry:
    """Get the global stage registry instance."""
    global _registry
    if _registry is None:
        _registry = StageRegistry()
    return _registry


def get_handler(stage_name: str) -> StageHandler:
    """Convenience function to get a handler from the global registry."""
    return get_registry().get_handler(stage_name)
