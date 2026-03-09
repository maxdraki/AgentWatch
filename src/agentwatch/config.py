"""
Configuration file support for AgentWatch.

Loads settings from agentwatch.toml (or agentwatch.json) in the
current directory or ~/.agentwatch/. Configuration is layered:

    1. Built-in defaults
    2. ~/.agentwatch/agentwatch.toml (user-level)
    3. ./agentwatch.toml (project-level)
    4. Environment variables (AGENTWATCH_*)
    5. Explicit arguments to init()

Usage:

    # Auto-detected from file:
    agentwatch.init()  # reads config from agentwatch.toml

    # Explicit config:
    from agentwatch.config import load_config
    config = load_config()
    agentwatch.init(**config.to_init_kwargs())

Config file format (TOML):

    [agent]
    name = "my-agent"
    db_path = "/path/to/agentwatch.db"

    [server]
    host = "0.0.0.0"
    port = 8470
    metrics = true
    metrics_port = 9090

    [retention]
    trace_days = 30
    log_days = 7
    health_days = 14
    cost_days = 90
    auto_prune = true

    [alerts]
    error_rate_threshold = 10.0
    cost_threshold_usd = 5.0
    cost_threshold_hours = 24

    [costs]
    # Custom model pricing (USD per 1M tokens)
    [costs.pricing]
    "my-custom-model" = [1.0, 3.0]  # [input, output]
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AgentSettings:
    """Agent configuration."""
    name: str = "default"
    db_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ServerSettings:
    """Dashboard server configuration."""
    host: str = "0.0.0.0"
    port: int = 8470
    metrics: bool = False
    metrics_port: int | None = None


@dataclass
class RetentionSettings:
    """Data retention configuration."""
    trace_days: int = 30
    log_days: int = 7
    health_days: int = 14
    cost_days: int = 90
    auto_prune: bool = False


@dataclass
class AlertSettings:
    """Alert defaults."""
    error_rate_threshold: float | None = None
    cost_threshold_usd: float | None = None
    cost_threshold_hours: int = 24


@dataclass
class CostSettings:
    """Cost tracking configuration."""
    pricing: dict[str, tuple[float, float]] = field(default_factory=dict)


@dataclass
class Config:
    """Complete AgentWatch configuration."""
    agent: AgentSettings = field(default_factory=AgentSettings)
    server: ServerSettings = field(default_factory=ServerSettings)
    retention: RetentionSettings = field(default_factory=RetentionSettings)
    alerts: AlertSettings = field(default_factory=AlertSettings)
    costs: CostSettings = field(default_factory=CostSettings)

    def to_init_kwargs(self) -> dict[str, Any]:
        """Convert to kwargs for agentwatch.init()."""
        kwargs: dict[str, Any] = {"agent_name": self.agent.name}
        if self.agent.db_path:
            kwargs["db_path"] = self.agent.db_path
        if self.agent.metadata:
            kwargs["metadata"] = self.agent.metadata
        return kwargs

    def to_dict(self) -> dict[str, Any]:
        """Serialize config to a dictionary."""
        return {
            "agent": {
                "name": self.agent.name,
                "db_path": self.agent.db_path,
                "metadata": self.agent.metadata,
            },
            "server": {
                "host": self.server.host,
                "port": self.server.port,
                "metrics": self.server.metrics,
                "metrics_port": self.server.metrics_port,
            },
            "retention": {
                "trace_days": self.retention.trace_days,
                "log_days": self.retention.log_days,
                "health_days": self.retention.health_days,
                "cost_days": self.retention.cost_days,
                "auto_prune": self.retention.auto_prune,
            },
            "alerts": {
                "error_rate_threshold": self.alerts.error_rate_threshold,
                "cost_threshold_usd": self.alerts.cost_threshold_usd,
                "cost_threshold_hours": self.alerts.cost_threshold_hours,
            },
            "costs": {
                "pricing": {k: list(v) for k, v in self.costs.pricing.items()},
            },
        }


def load_config(
    config_path: str | None = None,
    use_env: bool = True,
) -> Config:
    """
    Load configuration from files and environment.

    Search order:
        1. Explicit config_path (if provided)
        2. ./agentwatch.toml or ./agentwatch.json
        3. ~/.agentwatch/agentwatch.toml or ~/.agentwatch/agentwatch.json
        4. Environment variables override file settings

    Args:
        config_path: Explicit path to config file.
        use_env: Whether to apply environment variable overrides.

    Returns:
        Config instance with merged settings.
    """
    config = Config()
    raw: dict[str, Any] = {}

    # Find config file
    if config_path:
        raw = _load_file(config_path)
    else:
        # Check project directory first, then user directory
        for dir_path in [Path.cwd(), Path.home() / ".agentwatch"]:
            for filename in ["agentwatch.toml", "agentwatch.json"]:
                path = dir_path / filename
                if path.exists():
                    raw = _load_file(str(path))
                    if raw:
                        break
            if raw:
                break

    # Apply file settings
    if raw:
        _apply_raw(config, raw)

    # Apply environment overrides
    if use_env:
        _apply_env(config)

    return config


def _load_file(path: str) -> dict[str, Any]:
    """Load a config file (TOML or JSON)."""
    p = Path(path)
    if not p.exists():
        return {}

    content = p.read_text()

    if p.suffix == ".json":
        return json.loads(content)

    if p.suffix == ".toml":
        # Use tomllib (Python 3.11+) or fall back to basic parsing
        try:
            import tomllib
            return tomllib.loads(content)
        except ImportError:
            try:
                import tomli
                return tomli.loads(content)
            except ImportError:
                # Basic TOML-like parsing for simple flat configs
                return _basic_toml_parse(content)

    return {}


def _basic_toml_parse(content: str) -> dict[str, Any]:
    """
    Very basic TOML parser for simple key=value configs.
    Handles sections, strings, numbers, and booleans.
    Not a full TOML parser — just enough for our config format.
    """
    result: dict[str, Any] = {}
    current_section: dict[str, Any] = result
    current_key: list[str] = []

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Section header
        if line.startswith("["):
            section = line.strip("[]").strip()
            parts = section.split(".")
            current_section = result
            current_key = parts
            for part in parts:
                if part not in current_section:
                    current_section[part] = {}
                current_section = current_section[part]
            continue

        # Key = value
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()

            # Parse value
            parsed: Any
            if value.startswith('"') and value.endswith('"'):
                parsed = value[1:-1]
            elif value.lower() == "true":
                parsed = True
            elif value.lower() == "false":
                parsed = False
            elif value.replace(".", "").replace("-", "").isdigit():
                parsed = float(value) if "." in value else int(value)
            else:
                parsed = value

            current_section[key] = parsed

    return result


def _apply_raw(config: Config, raw: dict[str, Any]) -> None:
    """Apply raw dict settings to config."""
    # Agent settings
    agent = raw.get("agent", {})
    if "name" in agent:
        config.agent.name = agent["name"]
    if "db_path" in agent:
        config.agent.db_path = agent["db_path"]
    if "metadata" in agent:
        config.agent.metadata = agent["metadata"]

    # Server settings
    server = raw.get("server", {})
    if "host" in server:
        config.server.host = server["host"]
    if "port" in server:
        config.server.port = int(server["port"])
    if "metrics" in server:
        config.server.metrics = bool(server["metrics"])
    if "metrics_port" in server:
        config.server.metrics_port = int(server["metrics_port"])

    # Retention settings
    retention = raw.get("retention", {})
    if "trace_days" in retention:
        config.retention.trace_days = int(retention["trace_days"])
    if "log_days" in retention:
        config.retention.log_days = int(retention["log_days"])
    if "health_days" in retention:
        config.retention.health_days = int(retention["health_days"])
    if "cost_days" in retention:
        config.retention.cost_days = int(retention["cost_days"])
    if "auto_prune" in retention:
        config.retention.auto_prune = bool(retention["auto_prune"])

    # Alert settings
    alerts = raw.get("alerts", {})
    if "error_rate_threshold" in alerts:
        config.alerts.error_rate_threshold = float(alerts["error_rate_threshold"])
    if "cost_threshold_usd" in alerts:
        config.alerts.cost_threshold_usd = float(alerts["cost_threshold_usd"])
    if "cost_threshold_hours" in alerts:
        config.alerts.cost_threshold_hours = int(alerts["cost_threshold_hours"])

    # Cost settings
    costs = raw.get("costs", {})
    pricing = costs.get("pricing", {})
    for model, prices in pricing.items():
        if isinstance(prices, (list, tuple)) and len(prices) == 2:
            config.costs.pricing[model] = (float(prices[0]), float(prices[1]))


def _apply_env(config: Config) -> None:
    """Apply environment variable overrides."""
    env_map = {
        "AGENTWATCH_NAME": ("agent", "name"),
        "AGENTWATCH_DB_PATH": ("agent", "db_path"),
        "AGENTWATCH_HOST": ("server", "host"),
        "AGENTWATCH_PORT": ("server", "port"),
        "AGENTWATCH_METRICS": ("server", "metrics"),
        "AGENTWATCH_METRICS_PORT": ("server", "metrics_port"),
    }

    for env_var, (section, key) in env_map.items():
        value = os.environ.get(env_var)
        if value is None:
            continue

        obj = getattr(config, section)

        # Type coerce
        current = getattr(obj, key)
        if isinstance(current, bool):
            setattr(obj, key, value.lower() in ("true", "1", "yes"))
        elif isinstance(current, int):
            setattr(obj, key, int(value))
        else:
            setattr(obj, key, value)
