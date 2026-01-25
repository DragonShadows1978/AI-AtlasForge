"""
af_engine.integration_manager - Event Bus for Integration Handlers

This module provides the IntegrationManager class that coordinates event
dispatch to all registered integration handlers.
"""

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Type

from .integrations.base import (
    IntegrationHandler,
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)


class IntegrationManager:
    """
    Manages integration handlers and coordinates event dispatch.

    The IntegrationManager acts as an event bus, routing events to
    subscribed handlers. It provides:
    - Handler registration with event subscriptions
    - Priority-based execution order
    - Graceful error handling (failures don't block other handlers)
    - Handler availability checking
    """

    def __init__(self):
        """Initialize the integration manager."""
        # Handler registry: name -> handler instance
        self._handlers: Dict[str, IntegrationHandler] = {}

        # Subscription index: event_type -> list of handler names
        self._subscriptions: Dict[StageEvent, List[str]] = defaultdict(list)

        # Priority cache for sorting
        self._priorities: Dict[str, int] = {}

        # Statistics
        self._stats = {
            "events_emitted": 0,
            "handlers_invoked": 0,
            "errors_handled": 0,
        }

    def register(self, handler: IntegrationHandler) -> None:
        """
        Register an integration handler.

        Args:
            handler: The integration handler to register
        """
        name = handler.name

        if name in self._handlers:
            logger.warning(f"Replacing existing handler: {name}")
            self.unregister(name)

        self._handlers[name] = handler
        self._priorities[name] = handler.priority.value

        # Index subscriptions
        for event_type in handler.get_subscriptions():
            self._subscriptions[event_type].append(name)
            # Sort by priority after adding
            self._subscriptions[event_type].sort(
                key=lambda n: self._priorities.get(n, 999)
            )

        logger.debug(f"Registered integration: {name} (priority: {handler.priority.name})")

    def unregister(self, name: str) -> None:
        """
        Unregister an integration handler.

        Args:
            name: Name of the handler to unregister
        """
        if name not in self._handlers:
            return

        handler = self._handlers.pop(name)
        self._priorities.pop(name, None)

        # Remove from subscription index
        for event_type, handlers in self._subscriptions.items():
            if name in handlers:
                handlers.remove(name)

        logger.debug(f"Unregistered integration: {name}")

    def emit(self, event: Event) -> None:
        """
        Emit an event to all subscribed handlers.

        Events are dispatched to handlers in priority order. Handler
        errors are caught and logged but don't block other handlers.

        Args:
            event: The event to emit
        """
        self._stats["events_emitted"] += 1

        handler_names = self._subscriptions.get(event.type, [])
        if not handler_names:
            logger.debug(f"No handlers for event: {event.type.value}")
            return

        logger.debug(f"Emitting {event.type.value} to {len(handler_names)} handlers")

        for name in handler_names:
            handler = self._handlers.get(name)
            if not handler:
                continue

            if not handler.is_available():
                logger.debug(f"Handler {name} not available, skipping")
                continue

            try:
                handler.handle_event(event)
                self._stats["handlers_invoked"] += 1
            except Exception as e:
                self._stats["errors_handled"] += 1
                logger.warning(f"Handler {name} failed for {event.type.value}: {e}")

    def emit_stage_started(
        self,
        stage: str,
        mission_id: str,
        data: Optional[Dict] = None
    ) -> None:
        """
        Convenience method to emit a STAGE_STARTED event.

        Args:
            stage: The stage that started
            mission_id: The mission ID
            data: Optional additional event data
        """
        self.emit(Event(
            type=StageEvent.STAGE_STARTED,
            stage=stage,
            mission_id=mission_id,
            data=data or {},
            source="integration_manager"
        ))

    def emit_stage_completed(
        self,
        stage: str,
        mission_id: str,
        data: Optional[Dict] = None
    ) -> None:
        """
        Convenience method to emit a STAGE_COMPLETED event.

        Args:
            stage: The stage that completed
            mission_id: The mission ID
            data: Optional additional event data
        """
        self.emit(Event(
            type=StageEvent.STAGE_COMPLETED,
            stage=stage,
            mission_id=mission_id,
            data=data or {},
            source="integration_manager"
        ))

    def emit_mission_completed(
        self,
        mission_id: str,
        stage: str = "COMPLETE",
        data: Optional[Dict] = None
    ) -> None:
        """
        Convenience method to emit a MISSION_COMPLETED event.

        Args:
            mission_id: The mission ID
            stage: The final stage (usually COMPLETE)
            data: Optional additional event data
        """
        self.emit(Event(
            type=StageEvent.MISSION_COMPLETED,
            stage=stage,
            mission_id=mission_id,
            data=data or {},
            source="integration_manager"
        ))

    def get_handler(self, name: str) -> Optional[IntegrationHandler]:
        """
        Get a specific handler by name.

        Args:
            name: Handler name

        Returns:
            The handler or None if not found
        """
        return self._handlers.get(name)

    def is_available(self, name: str) -> bool:
        """
        Check if a specific handler is available.

        Args:
            name: Handler name

        Returns:
            True if handler exists and is available
        """
        handler = self._handlers.get(name)
        return handler is not None and handler.is_available()

    def get_all_handlers(self) -> Dict[str, IntegrationHandler]:
        """Get all registered handlers."""
        return dict(self._handlers)

    def get_available_handlers(self) -> Dict[str, IntegrationHandler]:
        """Get all available handlers."""
        return {
            name: handler
            for name, handler in self._handlers.items()
            if handler.is_available()
        }

    def get_stats(self) -> Dict[str, int]:
        """Get integration manager statistics."""
        return {
            **self._stats,
            "handlers_registered": len(self._handlers),
            "handlers_available": len(self.get_available_handlers()),
        }

    def load_default_integrations(self) -> None:
        """
        Load all default integration handlers.

        This method attempts to instantiate and register all default
        integrations. Failures are logged but don't stop other
        integrations from loading.
        """
        from .integrations import DEFAULT_INTEGRATIONS

        for handler_class in DEFAULT_INTEGRATIONS:
            try:
                handler = handler_class()
                self.register(handler)
            except Exception as e:
                logger.warning(f"Failed to load integration {handler_class.__name__}: {e}")

    def load_from_config(self, config: Dict) -> None:
        """
        Load integrations from configuration.

        Args:
            config: Integration configuration dictionary
        """
        integrations_config = config.get('integrations', {})

        for name, int_config in integrations_config.items():
            if not int_config.get('enabled', True):
                logger.debug(f"Integration {name} disabled in config")
                continue

            module_name = int_config.get('module')
            class_name = int_config.get('class')

            if not module_name or not class_name:
                logger.warning(f"Invalid config for integration {name}")
                continue

            try:
                import importlib
                module = importlib.import_module(module_name)
                handler_class = getattr(module, class_name)
                handler = handler_class()
                self.register(handler)
            except Exception as e:
                logger.warning(f"Failed to load integration {name}: {e}")

    # === HOT-RELOAD API ===

    def reload_integration(self, name: str) -> bool:
        """
        Hot-reload a specific integration handler.

        This method unregisters the handler, reimports its module,
        and re-registers a fresh instance. Useful for development
        when modifying integration code.

        Args:
            name: Name of the integration to reload

        Returns:
            True if reload succeeded, False otherwise
        """
        if name not in self._handlers:
            logger.warning(f"Cannot reload unknown integration: {name}")
            return False

        handler = self._handlers[name]
        handler_class = type(handler)
        module_name = handler_class.__module__

        try:
            import importlib
            import sys

            # Unregister current handler
            self.unregister(name)

            # Reimport the module
            if module_name in sys.modules:
                module = sys.modules[module_name]
                importlib.reload(module)
            else:
                module = importlib.import_module(module_name)

            # Get the refreshed class
            refreshed_class = getattr(module, handler_class.__name__)

            # Create and register new instance
            new_handler = refreshed_class()
            self.register(new_handler)

            logger.info(f"Hot-reloaded integration: {name}")
            return True

        except Exception as e:
            logger.error(f"Failed to hot-reload integration {name}: {e}")
            # Try to restore the original handler
            try:
                self.register(handler)
            except Exception:
                pass
            return False

    def reload_all_integrations(self) -> Dict[str, bool]:
        """
        Hot-reload all registered integrations.

        Returns:
            Dictionary mapping handler names to reload success status
        """
        results = {}
        handler_names = list(self._handlers.keys())

        for name in handler_names:
            results[name] = self.reload_integration(name)

        successful = sum(1 for v in results.values() if v)
        logger.info(f"Hot-reloaded {successful}/{len(handler_names)} integrations")

        return results

    def add_integration_dynamically(
        self,
        module_name: str,
        class_name: str,
    ) -> bool:
        """
        Dynamically add a new integration at runtime.

        Args:
            module_name: Full module path (e.g., 'af_engine.integrations.my_handler')
            class_name: Handler class name

        Returns:
            True if integration was added successfully
        """
        try:
            import importlib
            module = importlib.import_module(module_name)
            handler_class = getattr(module, class_name)
            handler = handler_class()
            self.register(handler)
            logger.info(f"Dynamically added integration: {handler.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to add integration {class_name}: {e}")
            return False

    def remove_integration(self, name: str) -> bool:
        """
        Remove an integration at runtime.

        Args:
            name: Name of the integration to remove

        Returns:
            True if integration was removed
        """
        if name not in self._handlers:
            return False

        self.unregister(name)
        logger.info(f"Removed integration: {name}")
        return True

    def get_integration_info(self, name: str) -> Optional[Dict]:
        """
        Get detailed information about an integration.

        Args:
            name: Handler name

        Returns:
            Dictionary with integration details or None if not found
        """
        handler = self._handlers.get(name)
        if not handler:
            return None

        return {
            "name": handler.name,
            "priority": handler.priority.name,
            "available": handler.is_available(),
            "subscriptions": [e.value for e in handler.get_subscriptions()],
            "module": type(handler).__module__,
            "class": type(handler).__name__,
        }
