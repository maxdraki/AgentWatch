"""
FastAPI middleware for automatic request tracing.

Instruments every FastAPI request as a trace, capturing method, path,
status code, duration, and request metadata. Ideal for agents that
expose HTTP APIs or webhooks.

Usage:

    from fastapi import FastAPI
    from agentwatch.integrations.fastapi import AgentWatchMiddleware

    app = FastAPI()
    app.add_middleware(AgentWatchMiddleware, agent_name="my-api-agent")

    @app.get("/webhook")
    async def handle_webhook():
        # This request is automatically traced
        return {"status": "ok"}

Options:
    agent_name:       Agent name for traces (default: auto-detect)
    exclude_paths:    Set of paths to skip (default: /health, /metrics)
    capture_headers:  Whether to capture request headers as metadata
    capture_body:     Whether to capture request body (careful with large payloads)
"""

from __future__ import annotations

import time
from typing import Any, Callable, cast

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

import agentwatch
from agentwatch.models import TraceStatus


# Default paths to exclude from tracing
DEFAULT_EXCLUDE = {"/health", "/healthz", "/metrics", "/favicon.ico", "/robots.txt"}


class AgentWatchMiddleware(BaseHTTPMiddleware):
    """
    Automatic request tracing middleware for FastAPI/Starlette.

    Creates a trace for each incoming request with:
    - Trace name: "{METHOD} {path}"
    - Status: completed (2xx/3xx) or failed (4xx/5xx)
    - Metadata: method, path, status_code, duration_ms, query params
    - Optional: headers, user agent, content type
    """

    def __init__(
        self,
        app: Any,
        agent_name: str | None = None,
        exclude_paths: set[str] | None = None,
        capture_headers: bool = False,
        capture_body: bool = False,
    ):
        super().__init__(app)
        self.exclude_paths = exclude_paths or DEFAULT_EXCLUDE
        self.capture_headers = capture_headers
        self.capture_body = capture_body

        # Ensure AgentWatch is initialised
        try:
            agentwatch.get_agent()
        except RuntimeError:
            agentwatch.init(agent_name or "fastapi-agent")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process each request with tracing."""
        path = request.url.path

        # Skip excluded paths
        if path in self.exclude_paths:
            return cast(Response, await call_next(request))

        method = request.method
        trace_name = f"{method} {path}"

        # Build metadata
        metadata: dict[str, Any] = {
            "http.method": method,
            "http.path": path,
            "http.scheme": request.url.scheme,
        }

        if request.url.query:
            metadata["http.query"] = str(request.url.query)

        if request.client:
            metadata["http.client_host"] = request.client.host

        if self.capture_headers:
            # Filter out sensitive headers
            sensitive = {"authorization", "cookie", "x-api-key", "x-auth-token"}
            headers = {
                k: v for k, v in request.headers.items()
                if k.lower() not in sensitive
            }
            metadata["http.headers"] = headers

        user_agent = request.headers.get("user-agent")
        if user_agent:
            metadata["http.user_agent"] = user_agent[:200]  # Truncate

        content_type = request.headers.get("content-type")
        if content_type:
            metadata["http.content_type"] = content_type

        # Execute with tracing
        start = time.monotonic()

        with agentwatch.trace(trace_name) as span:
            span.metadata = metadata

            try:
                response = cast(Response, await call_next(request))
                duration_ms = (time.monotonic() - start) * 1000

                span.event(f"Response {response.status_code}", {
                    "http.status_code": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                })

                metadata["http.status_code"] = response.status_code
                metadata["http.duration_ms"] = round(duration_ms, 2)

                # Mark as failed for server errors
                if response.status_code >= 500:
                    span.error = f"HTTP {response.status_code}"

                return response

            except Exception as exc:
                duration_ms = (time.monotonic() - start) * 1000
                span.event(f"Exception: {type(exc).__name__}", {
                    "error": str(exc)[:500],
                    "duration_ms": round(duration_ms, 2),
                })
                raise
