"""Tests for the alert system."""

import os
import tempfile

import pytest

from agentwatch.core import init, _reset, get_agent
from agentwatch.alerts import (
    Alert,
    AlertLevel,
    AlertManager,
    AlertRule,
    AlertType,
    fire,
    on_health_change,
    on_error_rate,
    on_cost_threshold,
    get_manager,
)


@pytest.fixture(autouse=True)
def clean_state():
    """Reset agent and alert manager state between tests."""
    _reset()
    # Reset global alert manager
    import agentwatch.alerts as am
    am._manager = AlertManager()
    yield
    _reset()


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


class TestAlert:
    def test_to_dict(self):
        alert = Alert(
            type=AlertType.HEALTH_CHANGE,
            level=AlertLevel.CRITICAL,
            title="DB down",
            message="Database unreachable",
            agent_name="test",
            metadata={"check": "db"},
        )
        d = alert.to_dict()
        assert d["type"] == "health_change"
        assert d["level"] == "critical"
        assert d["title"] == "DB down"
        assert d["metadata"]["check"] == "db"

    def test_to_json(self):
        alert = Alert(type=AlertType.CUSTOM, level=AlertLevel.INFO, title="Test", message="msg")
        j = alert.to_json()
        import json
        parsed = json.loads(j)
        assert parsed["title"] == "Test"


class TestAlertManager:
    def test_add_and_fire(self):
        manager = AlertManager()
        fired_alerts = []
        manager.add_rule(AlertRule(
            name="test",
            alert_type=AlertType.CUSTOM,
            handler=lambda a: fired_alerts.append(a),
        ))

        alert = Alert(type=AlertType.CUSTOM, level=AlertLevel.INFO, title="Test", message="Hello")
        count = manager.fire(alert)

        assert count == 1
        assert len(fired_alerts) == 1
        assert fired_alerts[0].title == "Test"

    def test_type_filtering(self):
        manager = AlertManager()
        fired = []
        manager.add_rule(AlertRule(
            name="health-only",
            alert_type=AlertType.HEALTH_CHANGE,
            handler=lambda a: fired.append(a),
        ))

        # This should NOT fire (wrong type)
        alert = Alert(type=AlertType.CUSTOM, level=AlertLevel.INFO, title="Custom", message="x")
        count = manager.fire(alert)
        assert count == 0
        assert len(fired) == 0

        # This SHOULD fire
        alert = Alert(type=AlertType.HEALTH_CHANGE, level=AlertLevel.WARNING, title="Health", message="x")
        count = manager.fire(alert)
        assert count == 1
        assert len(fired) == 1

    def test_cooldown(self):
        manager = AlertManager()
        fired = []
        manager.add_rule(AlertRule(
            name="cooldown-test",
            alert_type=AlertType.CUSTOM,
            handler=lambda a: fired.append(a),
            cooldown_seconds=9999,
        ))

        alert = Alert(type=AlertType.CUSTOM, level=AlertLevel.INFO, title="First", message="x")
        manager.fire(alert)
        assert len(fired) == 1

        # Second fire should be in cooldown
        alert2 = Alert(type=AlertType.CUSTOM, level=AlertLevel.INFO, title="Second", message="x")
        manager.fire(alert2)
        assert len(fired) == 1  # Still 1, cooldown prevented second

    def test_remove_rule(self):
        manager = AlertManager()
        manager.add_rule(AlertRule(name="r1", alert_type=AlertType.CUSTOM, handler=lambda a: None))
        assert len(manager.rules) == 1
        assert manager.remove_rule("r1") is True
        assert len(manager.rules) == 0
        assert manager.remove_rule("nonexistent") is False

    def test_disabled_rule(self):
        manager = AlertManager()
        fired = []
        manager.add_rule(AlertRule(
            name="disabled",
            alert_type=AlertType.CUSTOM,
            handler=lambda a: fired.append(a),
            enabled=False,
        ))
        alert = Alert(type=AlertType.CUSTOM, level=AlertLevel.INFO, title="Test", message="x")
        manager.fire(alert)
        assert len(fired) == 0

    def test_handler_error_doesnt_crash(self):
        manager = AlertManager()
        def bad_handler(a):
            raise RuntimeError("handler broke")

        manager.add_rule(AlertRule(name="bad", alert_type=AlertType.CUSTOM, handler=bad_handler))
        alert = Alert(type=AlertType.CUSTOM, level=AlertLevel.INFO, title="Test", message="x")
        # Should not raise
        count = manager.fire(alert)
        assert count == 0  # Handler failed, doesn't count

    def test_history(self):
        manager = AlertManager()
        for i in range(5):
            alert = Alert(type=AlertType.CUSTOM, level=AlertLevel.INFO, title=f"Alert {i}", message="x")
            manager.fire(alert)
        assert len(manager.history) == 5

    def test_check_health(self, db_path):
        init("alert-test", db_path=db_path)
        from agentwatch.models import HealthCheck, HealthStatus
        agent = get_agent()

        # Seed a critical health check
        check = HealthCheck(
            name="database",
            agent_name="alert-test",
            status=HealthStatus.CRITICAL,
            message="Connection refused",
        )
        agent.storage.save_health_check(check)

        manager = AlertManager()
        fired = []
        manager.add_rule(AlertRule(
            name="health-watch",
            alert_type=AlertType.HEALTH_CHANGE,
            handler=lambda a: fired.append(a),
        ))

        alerts = manager.check_health()
        assert len(alerts) >= 1
        assert any("database" in a.title for a in alerts)

    def test_check_error_rate(self, db_path):
        init("error-rate-test", db_path=db_path)
        from agentwatch.models import Trace, TraceStatus
        agent = get_agent()

        # Seed 20 traces, 15 failed
        for i in range(20):
            t = Trace(
                id=f"t-{i}",
                agent_name="error-rate-test",
                name="task",
                status=TraceStatus.FAILED if i < 15 else TraceStatus.COMPLETED,
            )
            t.finish(status=t.status)
            agent.storage.save_trace(t)

        manager = AlertManager()
        fired = []
        manager.add_rule(AlertRule(
            name="error-watch",
            alert_type=AlertType.ERROR_SPIKE,
            handler=lambda a: fired.append(a),
            config={"threshold_pct": 10.0},
        ))

        alert = manager.check_error_rate(threshold_pct=10.0)
        assert alert is not None
        assert "75.0%" in alert.message

    def test_check_costs(self, db_path):
        init("cost-test", db_path=db_path)
        from agentwatch.costs import TokenUsage
        agent = get_agent()

        usage = TokenUsage(
            agent_name="cost-test",
            model="test-model",
            input_tokens=100000,
            output_tokens=50000,
            total_tokens=150000,
            estimated_cost_usd=5.0,
        )
        agent.storage.save_token_usage(usage)

        manager = AlertManager()
        alert = manager.check_costs(threshold_usd=1.0, period_hours=24)
        assert alert is not None
        assert "$5.0000" in alert.message


class TestConvenienceFunctions:
    def test_fire_custom(self, db_path):
        init("fire-test", db_path=db_path)

        # Register a handler
        fired = []
        manager = get_manager()
        manager.add_rule(AlertRule(
            name="catch-all",
            alert_type=AlertType.CUSTOM,
            handler=lambda a: fired.append(a),
        ))

        alert = fire("Test Alert", "Something happened", level="warning")
        assert alert.title == "Test Alert"
        assert len(fired) == 1

    def test_on_health_change(self, db_path):
        init("health-conv", db_path=db_path)
        fired = []
        on_health_change(handler=lambda a: fired.append(a))

        manager = get_manager()
        assert len(manager.rules) >= 1

    def test_on_error_rate(self, db_path):
        init("error-conv", db_path=db_path)
        on_error_rate(threshold_pct=5.0)

        manager = get_manager()
        assert any(r.name.startswith("error_rate") for r in manager.rules)

    def test_on_cost_threshold(self, db_path):
        init("cost-conv", db_path=db_path)
        on_cost_threshold(threshold_usd=10.0, period_hours=48)

        manager = get_manager()
        assert any(r.name.startswith("cost_") for r in manager.rules)
