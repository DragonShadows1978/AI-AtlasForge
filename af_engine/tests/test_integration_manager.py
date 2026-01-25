"""
Tests for IntegrationManager - Event Bus and Handler Management

These tests validate:
- Handler registration and unregistration
- Event emission and subscription
- Priority-based execution order
- Error handling and graceful degradation
- Hot-reload functionality
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from af_engine.integration_manager import IntegrationManager
from af_engine.integrations.base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)


class MockIntegration(BaseIntegrationHandler):
    """Mock integration for testing."""
    name = "mock_integration"
    priority = IntegrationPriority.NORMAL
    subscriptions = [StageEvent.STAGE_STARTED, StageEvent.STAGE_COMPLETED]

    def __init__(self):
        super().__init__()
        self.events_received = []

    def _check_availability(self) -> bool:
        return True

    def handle_event(self, event: Event) -> None:
        self.events_received.append(event)


class HighPriorityIntegration(BaseIntegrationHandler):
    """High priority mock integration."""
    name = "high_priority"
    priority = IntegrationPriority.CRITICAL
    subscriptions = [StageEvent.STAGE_STARTED]

    def _check_availability(self) -> bool:
        return True


class LowPriorityIntegration(BaseIntegrationHandler):
    """Low priority mock integration."""
    name = "low_priority"
    priority = IntegrationPriority.LOW
    subscriptions = [StageEvent.STAGE_STARTED]

    def _check_availability(self) -> bool:
        return True


class UnavailableIntegration(BaseIntegrationHandler):
    """Integration that reports as unavailable."""
    name = "unavailable"
    priority = IntegrationPriority.NORMAL
    subscriptions = [StageEvent.STAGE_STARTED]

    def _check_availability(self) -> bool:
        return False


class FailingIntegration(BaseIntegrationHandler):
    """Integration that raises exceptions."""
    name = "failing"
    priority = IntegrationPriority.NORMAL
    subscriptions = [StageEvent.STAGE_STARTED]

    def _check_availability(self) -> bool:
        return True

    def handle_event(self, event: Event) -> None:
        raise RuntimeError("Intentional failure for testing")


class TestIntegrationManagerBasics:
    """Basic IntegrationManager functionality tests."""

    def test_register_handler(self):
        """Test registering a handler."""
        mgr = IntegrationManager()
        handler = MockIntegration()

        mgr.register(handler)

        assert "mock_integration" in mgr._handlers
        assert mgr.get_handler("mock_integration") is handler

    def test_unregister_handler(self):
        """Test unregistering a handler."""
        mgr = IntegrationManager()
        handler = MockIntegration()
        mgr.register(handler)

        mgr.unregister("mock_integration")

        assert "mock_integration" not in mgr._handlers

    def test_get_all_handlers(self):
        """Test getting all registered handlers."""
        mgr = IntegrationManager()
        handler1 = MockIntegration()
        handler2 = HighPriorityIntegration()

        mgr.register(handler1)
        mgr.register(handler2)

        all_handlers = mgr.get_all_handlers()
        assert len(all_handlers) == 2
        assert "mock_integration" in all_handlers
        assert "high_priority" in all_handlers

    def test_get_available_handlers(self):
        """Test getting only available handlers."""
        mgr = IntegrationManager()
        mgr.register(MockIntegration())
        mgr.register(UnavailableIntegration())

        available = mgr.get_available_handlers()
        assert len(available) == 1
        assert "mock_integration" in available
        assert "unavailable" not in available


class TestEventEmission:
    """Event emission and subscription tests."""

    def test_emit_event(self):
        """Test emitting an event to subscribed handlers."""
        mgr = IntegrationManager()
        handler = MockIntegration()
        mgr.register(handler)

        event = Event(
            type=StageEvent.STAGE_STARTED,
            stage="PLANNING",
            mission_id="test_mission",
            data={},
            source="test"
        )
        mgr.emit(event)

        assert len(handler.events_received) == 1
        assert handler.events_received[0] is event

    def test_emit_to_unsubscribed_event(self):
        """Test that handlers don't receive events they didn't subscribe to."""
        mgr = IntegrationManager()
        handler = MockIntegration()  # Subscribes to STAGE_STARTED, STAGE_COMPLETED
        mgr.register(handler)

        event = Event(
            type=StageEvent.MISSION_COMPLETED,  # Not subscribed
            stage="COMPLETE",
            mission_id="test",
            data={},
            source="test"
        )
        mgr.emit(event)

        assert len(handler.events_received) == 0

    def test_emit_stage_started_convenience(self):
        """Test emit_stage_started convenience method."""
        mgr = IntegrationManager()
        handler = MockIntegration()
        mgr.register(handler)

        mgr.emit_stage_started("PLANNING", "mission_123", {"key": "value"})

        assert len(handler.events_received) == 1
        event = handler.events_received[0]
        assert event.type == StageEvent.STAGE_STARTED
        assert event.stage == "PLANNING"
        assert event.mission_id == "mission_123"

    def test_emit_stage_completed_convenience(self):
        """Test emit_stage_completed convenience method."""
        mgr = IntegrationManager()
        handler = MockIntegration()
        mgr.register(handler)

        mgr.emit_stage_completed("BUILDING", "mission_456")

        assert len(handler.events_received) == 1
        assert handler.events_received[0].type == StageEvent.STAGE_COMPLETED


class TestPriorityOrdering:
    """Priority-based execution order tests."""

    def test_priority_execution_order(self):
        """Test that handlers execute in priority order."""
        mgr = IntegrationManager()
        execution_order = []

        class TrackedHigh(HighPriorityIntegration):
            def handle_event(self, event):
                execution_order.append("high")

        class TrackedNormal(MockIntegration):
            def handle_event(self, event):
                execution_order.append("normal")

        class TrackedLow(LowPriorityIntegration):
            def handle_event(self, event):
                execution_order.append("low")

        # Register in wrong order
        mgr.register(TrackedLow())
        mgr.register(TrackedHigh())
        mgr.register(TrackedNormal())

        mgr.emit_stage_started("TEST", "mission")

        # Should execute in priority order: CRITICAL, NORMAL, LOW
        assert execution_order == ["high", "normal", "low"]


class TestErrorHandling:
    """Error handling and graceful degradation tests."""

    def test_failing_handler_doesnt_block_others(self):
        """Test that a failing handler doesn't block other handlers."""
        mgr = IntegrationManager()
        good_handler = MockIntegration()

        mgr.register(FailingIntegration())
        mgr.register(good_handler)

        # Should not raise
        mgr.emit_stage_started("TEST", "mission")

        # Good handler should still receive the event
        assert len(good_handler.events_received) == 1

    def test_error_stats_tracked(self):
        """Test that errors are tracked in stats."""
        mgr = IntegrationManager()
        mgr.register(FailingIntegration())

        mgr.emit_stage_started("TEST", "mission")

        stats = mgr.get_stats()
        assert stats["errors_handled"] == 1

    def test_unavailable_handler_skipped(self):
        """Test that unavailable handlers are skipped."""
        mgr = IntegrationManager()
        mgr.register(UnavailableIntegration())

        # Should not raise
        mgr.emit_stage_started("TEST", "mission")

        stats = mgr.get_stats()
        assert stats["handlers_invoked"] == 0


class TestHotReload:
    """Hot-reload API tests."""

    def test_reload_integration(self):
        """Test reloading a single integration."""
        mgr = IntegrationManager()
        handler = MockIntegration()
        mgr.register(handler)

        # Should succeed
        result = mgr.reload_integration("mock_integration")
        assert result is True
        assert mgr.is_available("mock_integration")

    def test_reload_unknown_integration(self):
        """Test reloading an unknown integration fails gracefully."""
        mgr = IntegrationManager()

        result = mgr.reload_integration("nonexistent")
        assert result is False

    def test_get_integration_info(self):
        """Test getting integration info."""
        mgr = IntegrationManager()
        mgr.register(MockIntegration())

        info = mgr.get_integration_info("mock_integration")

        assert info is not None
        assert info["name"] == "mock_integration"
        assert info["priority"] == "NORMAL"
        assert info["available"] is True
        assert "stage_started" in info["subscriptions"]

    def test_remove_integration(self):
        """Test removing an integration at runtime."""
        mgr = IntegrationManager()
        mgr.register(MockIntegration())

        result = mgr.remove_integration("mock_integration")

        assert result is True
        assert "mock_integration" not in mgr._handlers


class TestLoadDefaultIntegrations:
    """Default integration loading tests."""

    def test_load_default_integrations(self):
        """Test loading default integrations."""
        mgr = IntegrationManager()

        mgr.load_default_integrations()

        # Should have loaded some integrations
        assert len(mgr._handlers) > 0

    def test_default_integrations_available(self):
        """Test that at least some default integrations are available."""
        mgr = IntegrationManager()
        mgr.load_default_integrations()

        available = mgr.get_available_handlers()

        # At minimum, analytics should be available
        assert "analytics" in available or len(available) > 0


class TestStats:
    """Statistics tracking tests."""

    def test_stats_tracking(self):
        """Test that stats are properly tracked."""
        mgr = IntegrationManager()
        handler = MockIntegration()
        mgr.register(handler)

        mgr.emit_stage_started("TEST", "mission")
        mgr.emit_stage_completed("TEST", "mission")

        stats = mgr.get_stats()

        assert stats["events_emitted"] == 2
        assert stats["handlers_invoked"] == 2
        assert stats["handlers_registered"] == 1
        assert stats["handlers_available"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
