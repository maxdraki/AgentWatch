"""Tests for the authentication module."""

import os
from unittest.mock import patch

from agentwatch.auth import (
    AuthConfig,
    extract_token,
    generate_token,
    hash_token,
    render_login_page,
    verify_token,
)


class TestAuthConfig:
    """Tests for AuthConfig."""

    def test_default_config(self):
        config = AuthConfig()
        assert config.token is None
        assert config.cookie_name == "agentwatch_token"
        assert config.cookie_max_age == 86400
        assert not config.enabled

    def test_enabled_with_token(self):
        config = AuthConfig(token="my-secret")
        assert config.enabled

    def test_disabled_with_empty_token(self):
        config = AuthConfig(token="")
        assert not config.enabled

    def test_from_env(self):
        with patch.dict(os.environ, {"AGENTWATCH_AUTH_TOKEN": "test-token"}):
            config = AuthConfig.from_env()
            assert config.token == "test-token"
            assert config.enabled

    def test_from_env_no_token(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove AGENTWATCH_AUTH_TOKEN if present
            env = {k: v for k, v in os.environ.items() if k != "AGENTWATCH_AUTH_TOKEN"}
            with patch.dict(os.environ, env, clear=True):
                config = AuthConfig.from_env()
                assert not config.enabled

    def test_from_env_custom_cookie(self):
        with patch.dict(os.environ, {
            "AGENTWATCH_AUTH_TOKEN": "tok",
            "AGENTWATCH_AUTH_COOKIE": "my_cookie",
        }):
            config = AuthConfig.from_env()
            assert config.cookie_name == "my_cookie"

    def test_excluded_paths(self):
        config = AuthConfig()
        assert "/health" in config.excluded_paths
        assert "/metrics" in config.excluded_paths


class TestVerifyToken:
    """Tests for token verification."""

    def test_valid_token(self):
        assert verify_token("my-secret", "my-secret")

    def test_invalid_token(self):
        assert not verify_token("wrong", "my-secret")

    def test_empty_token(self):
        assert not verify_token("", "my-secret")

    def test_constant_time(self):
        """Verify we're using constant-time comparison."""
        # This is a functional test — timing attacks need specialized tools
        assert verify_token("abcdef", "abcdef")
        assert not verify_token("abcdef", "abcdeg")


class TestGenerateToken:
    """Tests for token generation."""

    def test_generates_hex_string(self):
        token = generate_token()
        assert len(token) == 64  # 32 bytes = 64 hex chars
        int(token, 16)  # Should be valid hex

    def test_custom_length(self):
        token = generate_token(length=16)
        assert len(token) == 32

    def test_unique_tokens(self):
        tokens = {generate_token() for _ in range(10)}
        assert len(tokens) == 10  # All unique


class TestHashToken:
    """Tests for token hashing."""

    def test_returns_prefix(self):
        h = hash_token("my-secret")
        assert len(h) == 16
        assert isinstance(h, str)

    def test_deterministic(self):
        h1 = hash_token("test")
        h2 = hash_token("test")
        assert h1 == h2

    def test_different_tokens_different_hashes(self):
        h1 = hash_token("token-a")
        h2 = hash_token("token-b")
        assert h1 != h2


class TestExtractToken:
    """Tests for token extraction from requests."""

    def test_query_param(self):
        token = extract_token(query_params={"token": "from-query"})
        assert token == "from-query"

    def test_bearer_header(self):
        token = extract_token(headers={"Authorization": "Bearer my-bearer-token"})
        assert token == "my-bearer-token"

    def test_custom_header(self):
        token = extract_token(headers={"X-AgentWatch-Token": "custom-header-token"})
        assert token == "custom-header-token"

    def test_cookie(self):
        token = extract_token(cookies={"agentwatch_token": "cookie-token"})
        assert token == "cookie-token"

    def test_custom_cookie_name(self):
        token = extract_token(
            cookies={"my_cookie": "custom-cookie"},
            cookie_name="my_cookie",
        )
        assert token == "custom-cookie"

    def test_priority_order(self):
        """Query param > header > cookie."""
        token = extract_token(
            query_params={"token": "query"},
            headers={"Authorization": "Bearer header"},
            cookies={"agentwatch_token": "cookie"},
        )
        assert token == "query"

    def test_header_over_cookie(self):
        token = extract_token(
            headers={"Authorization": "Bearer header"},
            cookies={"agentwatch_token": "cookie"},
        )
        assert token == "header"

    def test_no_token(self):
        token = extract_token(
            query_params={},
            headers={},
            cookies={},
        )
        assert token is None

    def test_none_inputs(self):
        assert extract_token() is None

    def test_case_insensitive_headers(self):
        token = extract_token(headers={"authorization": "Bearer lower-case"})
        assert token == "lower-case"

    def test_bearer_with_extra_whitespace(self):
        token = extract_token(headers={"Authorization": "Bearer   spaced  "})
        assert token == "spaced"


class TestRenderLoginPage:
    """Tests for login page rendering."""

    def test_renders_html(self):
        html = render_login_page()
        assert "<!DOCTYPE html>" in html
        assert "AgentWatch" in html
        assert "Access Token" in html

    def test_no_error_by_default(self):
        html = render_login_page()
        assert "display: none" in html

    def test_shows_error(self):
        html = render_login_page(error="Bad token")
        assert "display: block" in html
        assert "Bad token" in html

    def test_next_url(self):
        html = render_login_page(next_url="/traces")
        assert "/traces" in html
