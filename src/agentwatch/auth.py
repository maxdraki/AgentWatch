"""
AgentWatch — Dashboard Authentication.

Provides token-based authentication for the web dashboard and API.
When enabled, all dashboard pages and API endpoints require a valid
token via query parameter, header, or cookie.

Configuration::

    # Via environment variable
    export AGENTWATCH_AUTH_TOKEN="my-secret-token"

    # Via config file (agentwatch.toml)
    [auth]
    token = "my-secret-token"
    cookie_name = "agentwatch_token"
    cookie_max_age = 86400  # 24 hours

    # Via init()
    agentwatch.init("my-agent", auth_token="my-secret-token")

When no token is configured, authentication is disabled and the
dashboard is accessible without credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import string
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AuthConfig:
    """Authentication configuration."""

    token: str | None = None
    cookie_name: str = "agentwatch_token"
    cookie_max_age: int = 86400  # 24 hours
    excluded_paths: list[str] = field(default_factory=lambda: [
        "/health",
        "/healthz",
        "/metrics",
    ])

    @classmethod
    def from_env(cls) -> "AuthConfig":
        """Create config from environment variables."""
        return cls(
            token=os.environ.get("AGENTWATCH_AUTH_TOKEN"),
            cookie_name=os.environ.get("AGENTWATCH_AUTH_COOKIE", "agentwatch_token"),
        )

    @property
    def enabled(self) -> bool:
        """Whether authentication is enabled."""
        return self.token is not None and len(self.token) > 0


def verify_token(provided: str, expected: str) -> bool:
    """
    Constant-time token comparison to prevent timing attacks.

    Args:
        provided: The token provided by the user.
        expected: The expected valid token.

    Returns:
        True if tokens match.
    """
    return hmac.compare_digest(provided.encode(), expected.encode())


def generate_token(length: int = 32) -> str:
    """
    Generate a cryptographically secure random token.

    Args:
        length: Number of random bytes (token will be hex-encoded, so 2x length).

    Returns:
        A hex-encoded random token string.
    """
    return secrets.token_hex(length)


def hash_token(token: str) -> str:
    """
    Hash a token for safe logging/storage (never log raw tokens).

    Args:
        token: The raw token.

    Returns:
        SHA-256 hash prefix (first 16 chars) for identification.
    """
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def extract_token(
    query_params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    cookie_name: str = "agentwatch_token",
) -> str | None:
    """
    Extract authentication token from request sources.

    Checks in order:
    1. Query parameter: ?token=xxx
    2. Authorization header: Bearer xxx
    3. X-AgentWatch-Token header
    4. Cookie

    Args:
        query_params: URL query parameters.
        headers: HTTP headers (case-insensitive keys).
        cookies: HTTP cookies.
        cookie_name: Name of the auth cookie.

    Returns:
        The token string if found, None otherwise.
    """
    # 1. Query parameter
    if query_params and query_params.get("token"):
        return str(query_params["token"])

    # 2. Authorization header (Bearer token)
    if headers:
        # Normalize header keys to lowercase
        norm_headers = {k.lower(): v for k, v in headers.items()}
        auth_header = norm_headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:].strip()

        # 3. Custom header
        custom = norm_headers.get("x-agentwatch-token")
        if custom:
            return custom.strip()

    # 4. Cookie
    if cookies and cookies.get(cookie_name):
        return cookies[cookie_name]

    return None


# ─── Login page HTML ─────────────────────────────────────────────────────

_LOGIN_PAGE_TEMPLATE = string.Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>AgentWatch — Login</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            background: #0d1117;
            color: #e6edf3;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
        }
        .login-card {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 12px;
            padding: 40px;
            width: 100%;
            max-width: 400px;
            margin: 20px;
        }
        .login-card h1 {
            font-size: 24px;
            margin-bottom: 8px;
            color: #e6edf3;
        }
        .login-card p {
            color: #8b949e;
            margin-bottom: 24px;
            font-size: 14px;
        }
        .login-card label {
            display: block;
            color: #8b949e;
            font-size: 13px;
            margin-bottom: 6px;
        }
        .login-card input[type="password"] {
            width: 100%;
            padding: 10px 12px;
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            color: #e6edf3;
            font-size: 14px;
            margin-bottom: 16px;
            outline: none;
        }
        .login-card input[type="password"]:focus {
            border-color: #58a6ff;
            box-shadow: 0 0 0 2px rgba(88, 166, 255, 0.15);
        }
        .login-card button {
            width: 100%;
            padding: 10px;
            background: #238636;
            border: 1px solid #2ea043;
            border-radius: 6px;
            color: #ffffff;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
        }
        .login-card button:hover { background: #2ea043; }
        .error {
            background: #3d1f28;
            border: 1px solid #f85149;
            border-radius: 6px;
            padding: 10px 12px;
            margin-bottom: 16px;
            color: #f85149;
            font-size: 13px;
            display: $error_display;
        }
        .logo {
            text-align: center;
            margin-bottom: 24px;
            font-size: 32px;
        }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="logo">🔭</div>
        <h1>AgentWatch</h1>
        <p>Enter your access token to view the dashboard.</p>
        <div class="error">$error_message</div>
        <form method="POST" action="/login">
            <label for="token">Access Token</label>
            <input type="password" id="token" name="token" placeholder="Enter token" autofocus required>
            <input type="hidden" name="next" value="$next_url">
            <button type="submit">Sign In</button>
        </form>
    </div>
</body>
</html>""")


def render_login_page(
    error: str | None = None,
    next_url: str = "/",
) -> str:
    """
    Render the login page HTML.

    Args:
        error: Error message to display (None for no error).
        next_url: URL to redirect to after successful login.

    Returns:
        HTML string for the login page.
    """
    return _LOGIN_PAGE_TEMPLATE.substitute(
        error_display="block" if error else "none",
        error_message=error or "",
        next_url=next_url,
    )
