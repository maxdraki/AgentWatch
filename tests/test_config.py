"""Tests for configuration file support."""

import json
import os
import pytest
from pathlib import Path

from agentwatch.config import (
    Config, AgentSettings, ServerSettings, RetentionSettings,
    load_config, _basic_toml_parse, _apply_env,
)


class TestConfig:
    """Test Config dataclass."""

    def test_defaults(self):
        """Config should have sensible defaults."""
        config = Config()
        assert config.agent.name == "default"
        assert config.agent.db_path is None
        assert config.server.port == 8470
        assert config.server.metrics is False
        assert config.retention.trace_days == 30
        assert config.retention.log_days == 7

    def test_to_init_kwargs(self):
        """Should produce valid init() kwargs."""
        config = Config()
        config.agent.name = "my-agent"
        config.agent.db_path = "/tmp/test.db"

        kwargs = config.to_init_kwargs()
        assert kwargs["agent_name"] == "my-agent"
        assert kwargs["db_path"] == "/tmp/test.db"

    def test_to_dict(self):
        """Should serialize to dict."""
        config = Config()
        d = config.to_dict()
        assert d["agent"]["name"] == "default"
        assert d["server"]["port"] == 8470
        assert d["retention"]["trace_days"] == 30


class TestBasicTomlParse:
    """Test the basic TOML parser."""

    def test_simple_values(self):
        content = '''
[agent]
name = "my-agent"
port = 8080
enabled = true
rate = 3.14
'''
        result = _basic_toml_parse(content)
        assert result["agent"]["name"] == "my-agent"
        assert result["agent"]["port"] == 8080
        assert result["agent"]["enabled"] is True
        assert result["agent"]["rate"] == 3.14

    def test_nested_sections(self):
        content = '''
[costs.pricing]
"custom-model" = "test"
'''
        result = _basic_toml_parse(content)
        assert "costs" in result
        assert "pricing" in result["costs"]

    def test_comments(self):
        content = '''
# This is a comment
[agent]
name = "test"
# Another comment
'''
        result = _basic_toml_parse(content)
        assert result["agent"]["name"] == "test"

    def test_empty(self):
        result = _basic_toml_parse("")
        assert result == {}

    def test_false_value(self):
        content = '''
[server]
metrics = false
'''
        result = _basic_toml_parse(content)
        assert result["server"]["metrics"] is False


class TestLoadConfig:
    """Test config file loading."""

    def test_load_json(self, tmp_path, monkeypatch):
        """Should load from JSON."""
        config_file = tmp_path / "agentwatch.json"
        config_file.write_text(json.dumps({
            "agent": {"name": "json-agent"},
            "server": {"port": 9999},
        }))

        config = load_config(str(config_file))
        assert config.agent.name == "json-agent"
        assert config.server.port == 9999

    def test_load_toml(self, tmp_path):
        """Should load from TOML."""
        config_file = tmp_path / "agentwatch.toml"
        config_file.write_text('''
[agent]
name = "toml-agent"

[server]
port = 7777
metrics = true

[retention]
trace_days = 60
log_days = 14
''')
        config = load_config(str(config_file))
        assert config.agent.name == "toml-agent"
        assert config.server.port == 7777
        assert config.server.metrics is True
        assert config.retention.trace_days == 60
        assert config.retention.log_days == 14

    def test_load_nonexistent(self, tmp_path, monkeypatch):
        """Missing config should return defaults."""
        monkeypatch.chdir(tmp_path)
        config = load_config()
        assert config.agent.name == "default"

    def test_env_overrides(self, monkeypatch):
        """Environment variables should override file settings."""
        monkeypatch.setenv("AGENTWATCH_NAME", "env-agent")
        monkeypatch.setenv("AGENTWATCH_PORT", "5555")
        monkeypatch.setenv("AGENTWATCH_METRICS", "true")

        config = Config()
        _apply_env(config)

        assert config.agent.name == "env-agent"
        assert config.server.port == 5555
        assert config.server.metrics is True

    def test_env_false(self, monkeypatch):
        """Env var 'false' should parse as False."""
        monkeypatch.setenv("AGENTWATCH_METRICS", "false")

        config = Config()
        _apply_env(config)
        assert config.server.metrics is False

    def test_retention_settings(self, tmp_path):
        """Retention settings should load from config."""
        config_file = tmp_path / "agentwatch.json"
        config_file.write_text(json.dumps({
            "retention": {
                "trace_days": 90,
                "log_days": 30,
                "health_days": 45,
                "cost_days": 180,
                "auto_prune": True,
            }
        }))

        config = load_config(str(config_file))
        assert config.retention.trace_days == 90
        assert config.retention.log_days == 30
        assert config.retention.auto_prune is True

    def test_custom_pricing(self, tmp_path):
        """Custom model pricing should load."""
        config_file = tmp_path / "agentwatch.json"
        config_file.write_text(json.dumps({
            "costs": {
                "pricing": {
                    "my-model": [2.0, 6.0],
                }
            }
        }))

        config = load_config(str(config_file))
        assert "my-model" in config.costs.pricing
        assert config.costs.pricing["my-model"] == (2.0, 6.0)

    def test_alert_settings(self, tmp_path):
        """Alert settings should load."""
        config_file = tmp_path / "agentwatch.json"
        config_file.write_text(json.dumps({
            "alerts": {
                "error_rate_threshold": 15.0,
                "cost_threshold_usd": 10.0,
            }
        }))

        config = load_config(str(config_file))
        assert config.alerts.error_rate_threshold == 15.0
        assert config.alerts.cost_threshold_usd == 10.0
