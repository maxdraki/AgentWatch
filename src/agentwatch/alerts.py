"""
Alert system for AgentWatch.

Configurable alerting based on health check status, error rates,
cost thresholds, and custom conditions. Alerts can be delivered
via webhooks, log messages, or custom handlers.

Usage:
    import agentwatch

    # Alert on health check failures
    agentwatch.alerts.on_health_change(
        check_name="database",
        handler=lambda alert: print(f"DB alert: {alert.message}"),
    )

    # Alert on error rate threshold
    agentwatch.alerts.on_error_rate(
        threshold_pct=10.0,
        window_minutes=30,
        handler=my_handler,
    )

    # Alert on cost threshold
    agentwatch.alerts.on_cost_threshold(
        threshold_usd=1.0,
        period_hours=24,
        handler=my_handler,
    )

    # Webhook delivery
    agentwatch.alerts.webhook(
        url="https://hooks.slack.com/...",
        events=["health_change", "error_spike", "cost_threshold"],
    )
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger("agentwatch.alerts")


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, Enum):
    HEALTH_CHANGE = "health_change"
    ERROR_SPIKE = "error_spike"
    COST_THRESHOLD = "cost_threshold"
    PATTERN_DETECTED = "pattern_detected"
    CUSTOM = "custom"


@dataclass
class Alert:
    """A triggered alert."""

    type: AlertType
    level: AlertLevel
    title: str
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)
    agent_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "level": self.level.value,
            "title": self.title,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
            "agent_name": self.agent_name,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class AlertRule:
    """A configured alert rule."""

    name: str
    alert_type: AlertType
    handler: Callable[[Alert], None]
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    cooldown_seconds: int = 300  # Don't re-fire same alert within 5 min
    _last_fired: float = 0.0


class AlertManager:
    """
    Manages alert rules and delivery.

    Typically accessed via the module-level functions, which use
    a global AlertManager instance.
    """

    def __init__(self):
        self._rules: list[AlertRule] = []
        self._lock = threading.Lock()
        self._history: list[Alert] = []
        self._max_history = 100

    def add_rule(self, rule: AlertRule) -> None:
        """Add an alert rule."""
        with self._lock:
            self._rules.append(rule)

    def remove_rule(self, name: str) -> bool:
        """Remove a rule by name. Returns True if found."""
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r.name != name]
            return len(self._rules) < before

    def fire(self, alert: Alert) -> int:
        """
        Fire an alert to all matching handlers.

        Returns the number of handlers that were called.
        """
        fired = 0
        now = time.monotonic()

        with self._lock:
            rules = list(self._rules)

        for rule in rules:
            if not rule.enabled:
                continue

            # Check type match
            if rule.alert_type != AlertType.CUSTOM and rule.alert_type != alert.type:
                continue

            # Check cooldown
            if now - rule._last_fired < rule.cooldown_seconds:
                logger.debug(f"Alert rule '{rule.name}' in cooldown, skipping")
                continue

            try:
                rule.handler(alert)
                rule._last_fired = now
                fired += 1
            except Exception as e:
                logger.error(f"Alert handler '{rule.name}' failed: {e}")

        # Store in history
        with self._lock:
            self._history.append(alert)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

        return fired

    def check_health(self) -> list[Alert]:
        """
        Check health status and fire alerts for any issues.

        Returns list of fired alerts.
        """
        try:
            from agentwatch.core import get_agent
            agent = get_agent()
        except RuntimeError:
            return []

        health_results = agent.storage.get_health_latest()
        alerts = []

        for h in health_results:
            if h["status"] in ("critical", "warn"):
                level = AlertLevel.CRITICAL if h["status"] == "critical" else AlertLevel.WARNING
                alert = Alert(
                    type=AlertType.HEALTH_CHANGE,
                    level=level,
                    title=f"Health check '{h['name']}' is {h['status']}",
                    message=h.get("message", ""),
                    agent_name=h.get("agent_name", ""),
                    metadata={"check_name": h["name"], "status": h["status"]},
                )
                self.fire(alert)
                alerts.append(alert)

        return alerts

    def check_error_rate(
        self,
        threshold_pct: float = 10.0,
        window_traces: int = 100,
    ) -> Alert | None:
        """
        Check recent error rate and fire alert if above threshold.
        """
        try:
            from agentwatch.core import get_agent
            agent = get_agent()
        except RuntimeError:
            return None

        stats = agent.storage.get_stats()
        rate = stats.get("recent_error_rate_pct", 0)

        if rate >= threshold_pct:
            alert = Alert(
                type=AlertType.ERROR_SPIKE,
                level=AlertLevel.CRITICAL if rate > threshold_pct * 2 else AlertLevel.WARNING,
                title=f"Error rate at {rate:.1f}%",
                message=f"Error rate ({rate:.1f}%) exceeds threshold ({threshold_pct}%) over last {window_traces} traces",
                metadata={"error_rate": rate, "threshold": threshold_pct},
            )
            self.fire(alert)
            return alert

        return None

    def check_costs(
        self,
        threshold_usd: float = 1.0,
        period_hours: int = 24,
    ) -> Alert | None:
        """
        Check cost spending and fire alert if above threshold.
        """
        try:
            from agentwatch.core import get_agent
            agent = get_agent()
        except RuntimeError:
            return None

        summary = agent.storage.get_cost_summary(hours=period_hours)
        total = summary.get("total_cost_usd", 0)

        if total >= threshold_usd:
            alert = Alert(
                type=AlertType.COST_THRESHOLD,
                level=AlertLevel.WARNING if total < threshold_usd * 2 else AlertLevel.CRITICAL,
                title=f"Cost threshold exceeded: ${total:.4f}",
                message=f"Spent ${total:.4f} in the last {period_hours}h (threshold: ${threshold_usd:.2f})",
                metadata={"total_cost": total, "threshold": threshold_usd, "period_hours": period_hours},
            )
            self.fire(alert)
            return alert

        return None

    def run_all_checks(self) -> list[Alert]:
        """Run all configured alert checks."""
        alerts = []
        alerts.extend(self.check_health())

        # Check rules for configured thresholds
        for rule in self._rules:
            if rule.alert_type == AlertType.ERROR_SPIKE:
                threshold = rule.config.get("threshold_pct", 10.0)
                result = self.check_error_rate(threshold_pct=threshold)
                if result:
                    alerts.append(result)
            elif rule.alert_type == AlertType.COST_THRESHOLD:
                threshold = rule.config.get("threshold_usd", 1.0)
                hours = rule.config.get("period_hours", 24)
                result = self.check_costs(threshold_usd=threshold, period_hours=hours)
                if result:
                    alerts.append(result)

        return alerts

    @property
    def history(self) -> list[Alert]:
        """Get alert history."""
        with self._lock:
            return list(self._history)

    @property
    def rules(self) -> list[AlertRule]:
        """Get configured rules."""
        with self._lock:
            return list(self._rules)


# ─── Global instance ────────────────────────────────────────────────────

_manager = AlertManager()


def get_manager() -> AlertManager:
    """Get the global AlertManager."""
    return _manager


# ─── Convenience functions ──────────────────────────────────────────────


def on_health_change(
    handler: Callable[[Alert], None],
    check_name: str | None = None,
    cooldown_seconds: int = 300,
) -> None:
    """
    Register a handler for health check status changes.

    Args:
        handler: Callable that receives an Alert.
        check_name: Optional filter for specific check name.
        cooldown_seconds: Minimum seconds between re-firing.
    """
    name = f"health_{check_name or 'all'}"
    rule_handler: Callable[[Alert], None]
    if check_name:
        def filtered_handler(alert: Alert) -> None:
            if alert.metadata.get("check_name") == check_name:
                handler(alert)
        rule_handler = filtered_handler
    else:
        rule_handler = handler

    _manager.add_rule(AlertRule(
        name=name,
        alert_type=AlertType.HEALTH_CHANGE,
        handler=rule_handler,
        cooldown_seconds=cooldown_seconds,
    ))


def on_error_rate(
    threshold_pct: float = 10.0,
    handler: Callable[[Alert], None] | None = None,
    cooldown_seconds: int = 600,
) -> None:
    """
    Register a handler for error rate threshold alerts.

    Args:
        threshold_pct: Error rate percentage that triggers the alert.
        handler: Alert handler. Defaults to logging.
        cooldown_seconds: Minimum seconds between re-firing.
    """
    _manager.add_rule(AlertRule(
        name=f"error_rate_{threshold_pct}",
        alert_type=AlertType.ERROR_SPIKE,
        handler=handler or _default_handler,
        config={"threshold_pct": threshold_pct},
        cooldown_seconds=cooldown_seconds,
    ))


def on_cost_threshold(
    threshold_usd: float = 1.0,
    period_hours: int = 24,
    handler: Callable[[Alert], None] | None = None,
    cooldown_seconds: int = 3600,
) -> None:
    """
    Register a handler for cost threshold alerts.

    Args:
        threshold_usd: Cost in USD that triggers the alert.
        period_hours: Time window for cost aggregation.
        handler: Alert handler. Defaults to logging.
        cooldown_seconds: Minimum seconds between re-firing.
    """
    _manager.add_rule(AlertRule(
        name=f"cost_{threshold_usd}",
        alert_type=AlertType.COST_THRESHOLD,
        handler=handler or _default_handler,
        config={"threshold_usd": threshold_usd, "period_hours": period_hours},
        cooldown_seconds=cooldown_seconds,
    ))


def webhook(
    url: str,
    events: list[str] | None = None,
    headers: dict[str, str] | None = None,
    cooldown_seconds: int = 300,
) -> None:
    """
    Register a webhook for alert delivery.

    Sends a POST request with the alert JSON payload to the URL.

    Args:
        url: Webhook URL.
        events: List of alert type strings to subscribe to. None = all.
        headers: Additional HTTP headers.
        cooldown_seconds: Minimum seconds between re-firing.
    """
    event_types = set(events or [t.value for t in AlertType])

    def webhook_handler(alert: Alert):
        if alert.type.value not in event_types:
            return
        try:
            data = alert.to_json().encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "AgentWatch/0.1",
                    **(headers or {}),
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.debug(f"Webhook delivered to {url}: {resp.status}")
        except urllib.error.URLError as e:
            logger.error(f"Webhook delivery failed for {url}: {e}")

    # Register for each event type
    for event_type in event_types:
        try:
            at = AlertType(event_type)
        except ValueError:
            continue
        _manager.add_rule(AlertRule(
            name=f"webhook_{url[:30]}_{event_type}",
            alert_type=at,
            handler=webhook_handler,
            cooldown_seconds=cooldown_seconds,
        ))


def fire(
    title: str,
    message: str,
    level: str = "warning",
    metadata: dict[str, Any] | None = None,
) -> Alert:
    """
    Fire a custom alert manually.

    Args:
        title: Alert title.
        message: Alert message.
        level: Alert level (info/warning/critical).
        metadata: Additional metadata.

    Returns:
        The fired Alert.
    """
    try:
        alert_level = AlertLevel(level)
    except ValueError:
        alert_level = AlertLevel.WARNING

    agent_name = ""
    try:
        from agentwatch.core import get_agent
        agent_name = get_agent().name
    except RuntimeError:
        pass

    alert = Alert(
        type=AlertType.CUSTOM,
        level=alert_level,
        title=title,
        message=message,
        agent_name=agent_name,
        metadata=metadata or {},
    )
    _manager.fire(alert)
    return alert


def check_all() -> list[Alert]:
    """Run all configured alert checks and return fired alerts."""
    return _manager.run_all_checks()


def _default_handler(alert: Alert) -> None:
    """Default alert handler — logs the alert."""
    try:
        from agentwatch.logging import log
        log(
            "error" if alert.level == AlertLevel.CRITICAL else "warn",
            f"[ALERT] {alert.title}: {alert.message}",
            alert.metadata,
        )
    except Exception:
        logger.warning(f"Alert: {alert.title}: {alert.message}")
