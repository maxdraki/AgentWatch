"""
AgentWatch Web Dashboard — FastAPI application.

A lightweight web dashboard for viewing traces, logs, health checks,
patterns, and costs. Designed to be started with a single command:

    agentwatch serve

Or programmatically:

    from agentwatch.server.app import create_app
    app = create_app(db_path="/path/to/agentwatch.db")
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from agentwatch.storage import Storage

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _parse_iso(s: str) -> datetime:
    """Parse an ISO timestamp string to datetime."""
    # Handle both 'Z' suffix and '+00:00'
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _compute_waterfall(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Compute waterfall layout data for a trace's spans.

    Returns a list of span dicts augmented with:
      - depth: nesting level (0 = root)
      - offset_pct: percentage offset from trace start
      - width_pct: percentage width of trace duration
    """
    spans = trace.get("spans", [])
    if not spans:
        return []

    trace_start = _parse_iso(trace["started_at"])
    trace_dur = trace.get("duration_ms") or 1.0

    # Build parent->children map for depth calculation
    span_map = {s["id"]: s for s in spans}
    depths: dict[str, int] = {}

    def get_depth(span_id: str) -> int:
        if span_id in depths:
            return depths[span_id]
        span = span_map.get(span_id)
        if not span or not span.get("parent_id"):
            depths[span_id] = 0
            return 0
        parent_depth = get_depth(span["parent_id"])
        depths[span_id] = parent_depth + 1
        return depths[span_id]

    result = []
    for span in spans:
        depth = get_depth(span["id"])
        span_start = _parse_iso(span["started_at"])
        offset_ms = (span_start - trace_start).total_seconds() * 1000
        span_dur = span.get("duration_ms") or 0

        offset_pct = max(0, min(100, (offset_ms / trace_dur) * 100)) if trace_dur > 0 else 0
        width_pct = max(1, min(100 - offset_pct, (span_dur / trace_dur) * 100)) if trace_dur > 0 else 1

        result.append({
            **span,
            "depth": depth,
            "offset_pct": round(offset_pct, 1),
            "width_pct": round(width_pct, 1),
        })

    return result


def create_app(
    db_path: str | None = None,
    auth_token: str | None = None,
) -> FastAPI:
    """
    Create the FastAPI application.

    Args:
        db_path: Path to SQLite database. Defaults to ~/.agentwatch/agentwatch.db
        auth_token: Optional authentication token. When set, all dashboard
            pages and API endpoints require this token. Can also be set via
            AGENTWATCH_AUTH_TOKEN environment variable.
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import RedirectResponse, Response as StarletteResponse
    from agentwatch.auth import AuthConfig, extract_token, verify_token, render_login_page

    app = FastAPI(
        title="AgentWatch",
        description="Lightweight observability for autonomous AI agents",
        version="0.1.0",
    )

    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

    # Shared storage instance
    storage = Storage(db_path=db_path)

    # ─── Authentication ──────────────────────────────────────────────────

    auth_config = AuthConfig.from_env()
    if auth_token:
        auth_config.token = auth_token

    if auth_config.enabled:
        class AuthMiddleware(BaseHTTPMiddleware):
            """Middleware that enforces token authentication."""

            async def dispatch(self, request: Request, call_next):
                path = request.url.path

                # Allow excluded paths (health, metrics)
                for excluded in auth_config.excluded_paths:
                    if path == excluded or path.startswith(excluded + "/"):
                        return await call_next(request)

                # Allow login page and static assets
                if path in ("/login", "/favicon.ico"):
                    return await call_next(request)

                # Extract token from request
                token = extract_token(
                    query_params=dict(request.query_params),
                    headers=dict(request.headers),
                    cookies=request.cookies,
                    cookie_name=auth_config.cookie_name,
                )

                if token and auth_config.token and verify_token(token, auth_config.token):
                    return await call_next(request)

                # Not authenticated
                if path.startswith("/api/"):
                    return StarletteResponse(
                        content='{"error": "Authentication required"}',
                        status_code=401,
                        media_type="application/json",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

                # Redirect HTML pages to login
                return RedirectResponse(
                    url=f"/login?next={path}",
                    status_code=302,
                )

        app.add_middleware(AuthMiddleware)

    # ─── Login endpoints ─────────────────────────────────────────────────

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, next: str = "/", error: str | None = None):
        """Login page for token authentication."""
        if not auth_config.enabled:
            return RedirectResponse(url="/")
        return HTMLResponse(render_login_page(error=error, next_url=next))

    @app.post("/login")
    async def login_submit(request: Request):
        """Handle login form submission."""
        if not auth_config.enabled:
            return RedirectResponse(url="/", status_code=302)

        form = await request.form()
        token = str(form.get("token", ""))
        next_url = str(form.get("next", "/"))

        if auth_config.token and verify_token(token, auth_config.token):
            response = RedirectResponse(url=next_url, status_code=302)
            response.set_cookie(
                key=auth_config.cookie_name,
                value=token,
                max_age=auth_config.cookie_max_age,
                httponly=True,
                samesite="lax",
            )
            return response

        return HTMLResponse(
            render_login_page(error="Invalid token. Please try again.", next_url=next_url),
            status_code=401,
        )

    @app.get("/logout")
    async def logout(request: Request):
        """Clear auth cookie and redirect to login."""
        response = RedirectResponse(url="/login", status_code=302)
        response.delete_cookie(key=auth_config.cookie_name)
        return response

    # ─── Dashboard (HTML) ────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        """Main dashboard page with sparkline charts."""
        from agentwatch.server.charts import sparkline_svg, donut_chart_svg, ChartPoint

        stats = storage.get_stats()
        health = storage.get_health_latest()
        traces = storage.get_traces(limit=10)

        # Trace activity sparkline (last 24 hours of traces)
        all_recent = storage.get_traces(limit=200)
        durations = [t.get("duration_ms", 0) or 0 for t in all_recent[:50] if t.get("duration_ms")]
        duration_sparkline = sparkline_svg(list(reversed(durations)), width=120, height=30) if len(durations) >= 2 else ""

        # Status donut chart
        status_chart = ""
        breakdown = stats.get("trace_status_breakdown", {})
        if breakdown:
            status_colors = {"completed": "#3fb950", "failed": "#f85149", "running": "#d29922"}
            points = [ChartPoint(label=k, value=float(v), color=status_colors.get(k)) for k, v in breakdown.items() if v > 0]
            if points:
                status_chart = donut_chart_svg(points, size=120, thickness=16)

        # Custom metrics for dashboard card
        metric_list = storage.list_metrics()

        return templates.TemplateResponse(request, "dashboard.html", context={
            "stats": stats,
            "health": health,
            "traces": traces,
            "duration_sparkline": duration_sparkline,
            "status_chart": status_chart,
            "metrics": metric_list,
        })

    @app.get("/traces", response_class=HTMLResponse)
    async def traces_page(
        request: Request,
        agent: str | None = None,
        status: str | None = None,
        search: str | None = None,
    ):
        """Traces list page with search and filters."""
        from agentwatch.models import TraceStatus
        status_filter = TraceStatus(status) if status else None
        traces = storage.get_traces(
            agent_name=agent,
            status=status_filter,
            name_contains=search,
            limit=50,
        )
        # Get agent list for filter dropdown
        stats = storage.get_stats()
        agents = stats.get("agents", [])

        return templates.TemplateResponse(request, "traces.html", context={
            "traces": traces,
            "agent_filter": agent,
            "status_filter": status,
            "search_filter": search,
            "agents": agents,
        })

    @app.get("/traces/{trace_id}", response_class=HTMLResponse)
    async def trace_detail_page(request: Request, trace_id: str):
        """Single trace detail page with waterfall visualization."""
        trace = storage.get_trace(trace_id)
        waterfall = _compute_waterfall(trace) if trace else []
        return templates.TemplateResponse(request, "trace_detail.html", context={
            "trace": trace,
            "waterfall": waterfall,
        })

    @app.get("/health", response_class=HTMLResponse)
    async def health_page(request: Request):
        """Health checks page."""
        health = storage.get_health_latest()
        return templates.TemplateResponse(request, "health.html", context={
            "health": health,
        })

    @app.get("/logs", response_class=HTMLResponse)
    async def logs_page(
        request: Request,
        agent: str | None = None,
        level: str | None = None,
        search: str | None = None,
        hours: int | None = None,
    ):
        """Logs page with search, agent, level, and time filters."""
        from agentwatch.models import LogLevel
        level_filter = LogLevel(level.lower()) if level else None
        logs = storage.get_logs(
            agent_name=agent,
            level=level_filter,
            search=search,
            hours=hours,
            limit=100,
        )
        stats = storage.get_stats()
        agents = stats.get("agents", [])
        return templates.TemplateResponse(request, "logs.html", context={
            "logs": logs,
            "agent_filter": agent,
            "level_filter": level,
            "search_filter": search,
            "hours_filter": hours,
            "agents": agents,
        })

    @app.get("/costs", response_class=HTMLResponse)
    async def costs_page(request: Request):
        """Costs page with cost timeline chart."""
        from agentwatch.server.charts import cost_timeline_data, bar_chart_svg, sparkline_svg, ChartPoint

        cost_summary = storage.get_cost_summary()
        usage = storage.get_token_usage(limit=50)

        # Generate cost timeline (last 7 days)
        all_usage = storage.get_token_usage(hours=168, limit=10000)
        timeline = cost_timeline_data(all_usage, days=7)
        timeline_values = [p.value for p in timeline]
        timeline_svg = sparkline_svg(timeline_values, width=600, height=60, color="#3fb950") if any(v > 0 for v in timeline_values) else ""
        timeline_labels = [p.label for p in timeline]

        # Generate model breakdown chart
        model_chart = ""
        if cost_summary.get("by_model"):
            model_points = [
                ChartPoint(label=m["model"].split("/")[-1][:20], value=m["cost_usd"])
                for m in cost_summary["by_model"][:8]
            ]
            model_chart = bar_chart_svg(model_points, width=500, height=len(model_points) * 30 + 20)

        return templates.TemplateResponse(request, "costs.html", context={
            "summary": cost_summary,
            "usage": usage,
            "timeline_svg": timeline_svg,
            "timeline_labels": timeline_labels,
            "model_chart": model_chart,
        })

    @app.get("/metrics-dashboard", response_class=HTMLResponse)
    async def metrics_page(request: Request):
        """Custom metrics dashboard page."""
        from agentwatch.server.charts import sparkline_svg

        metric_list = storage.list_metrics()

        # Build detail data for each metric
        metric_details = []
        for m in metric_list:
            name = m["name"]
            s = storage.get_metric_summary(name, agent_name=m.get("agent_name"))

            # Generate sparkline from series data
            spark = ""
            series = s.get("series", [])
            if len(series) >= 2:
                values = [p["value"] for p in series]
                spark = sparkline_svg(values, width=140, height=28, color="#58a6ff")

            metric_details.append({
                "name": name,
                "agent_name": m.get("agent_name", ""),
                "kind": m.get("kind", "gauge"),
                "count": s.get("count", 0),
                "latest": s.get("latest_value"),
                "min": s.get("min"),
                "max": s.get("max"),
                "avg": s.get("avg"),
                "sparkline": spark,
            })

        return templates.TemplateResponse(request, "metrics.html", context={
            "metrics": metric_details,
        })

    @app.get("/alerts", response_class=HTMLResponse)
    async def alerts_page(request: Request):
        """Alerts configuration and history page."""
        from agentwatch.alerts import get_manager

        manager = get_manager()
        rules = [
            {"name": r.name, "alert_type": r.alert_type.value, "enabled": r.enabled,
             "cooldown_seconds": r.cooldown_seconds}
            for r in manager.rules
        ]
        recent_alerts = [a.to_dict() for a in reversed(manager.history[-20:])]

        return templates.TemplateResponse(request, "alerts.html", context={
            "rules": rules,
            "recent_alerts": recent_alerts,
        })

    @app.get("/agents", response_class=HTMLResponse)
    async def agents_page(request: Request):
        """Multi-agent comparison page."""
        stats = storage.get_stats()
        agents = stats.get("agents", [])

        agent_data = []
        total_traces = 0
        total_cost = 0.0
        all_failed = 0
        all_recent = 0

        for agent_name in agents:
            agent_stats = storage.get_stats(agent_name=agent_name)
            cost_summary = storage.get_cost_summary(agent_name=agent_name)
            health = storage.get_health_latest(agent_name=agent_name)
            recent = storage.get_traces(agent_name=agent_name, limit=5)

            breakdown = agent_stats.get("trace_status_breakdown", {})
            completed = breakdown.get("completed", 0)
            failed = breakdown.get("failed", 0)
            running = breakdown.get("running", 0)
            agent_total = completed + failed + running
            error_rate = (failed / agent_total * 100) if agent_total > 0 else 0

            # Average duration
            all_traces = storage.get_traces(agent_name=agent_name, limit=100)
            durations = [t["duration_ms"] for t in all_traces if t.get("duration_ms")]
            avg_ms = sum(durations) / len(durations) if durations else 0
            if avg_ms < 1000:
                avg_dur = f"{avg_ms:.0f}ms"
            elif avg_ms < 60000:
                avg_dur = f"{avg_ms / 1000:.1f}s"
            else:
                avg_dur = f"{avg_ms / 60000:.1f}m"

            # Model breakdown
            models = []
            for m in cost_summary.get("by_model", []):
                models.append({
                    "name": m["model"].split("/")[-1][:25],
                    "cost": m.get("cost_usd", 0),
                })

            # Recent traces
            recent_traces = []
            for t in recent[:5]:
                dur = t.get("duration_ms")
                if dur is None:
                    d_str = "-"
                elif dur < 1000:
                    d_str = f"{dur:.0f}ms"
                elif dur < 60000:
                    d_str = f"{dur / 1000:.1f}s"
                else:
                    d_str = f"{dur / 60000:.1f}m"
                recent_traces.append({
                    "id": t["id"],
                    "name": t["name"],
                    "status": t["status"],
                    "duration": d_str,
                })

            cost_usd = cost_summary.get("total_cost_usd", 0)
            total_tokens = cost_summary.get("total_tokens", 0)

            agent_data.append({
                "name": agent_name,
                "total_traces": agent_total,
                "completed": completed,
                "failed": failed,
                "error_rate": error_rate,
                "avg_duration": avg_dur,
                "health": health,
                "cost_usd": cost_usd,
                "total_tokens": total_tokens,
                "models": models,
                "recent_traces": recent_traces,
            })

            total_traces += agent_total
            total_cost += cost_usd
            all_failed += failed
            all_recent += agent_total

        total_error_rate = (all_failed / all_recent * 100) if all_recent > 0 else 0

        return templates.TemplateResponse(request, "agents.html", context={
            "agents": agents,
            "agent_data": agent_data,
            "total_traces": total_traces,
            "total_cost": total_cost,
            "total_error_rate": total_error_rate,
        })

    @app.get("/models", response_class=HTMLResponse)
    async def models_page(request: Request, hours: int = 24):
        """Model usage dashboard."""
        model_stats = storage.get_model_stats(hours=hours)
        total_cost = sum(m["total_cost_usd"] for m in model_stats)
        total_requests = sum(m["requests"] for m in model_stats)
        total_tokens = sum(m["total_tokens"] for m in model_stats)
        return templates.TemplateResponse(request, "models.html", context={
            "model_stats": model_stats,
            "total_cost": round(total_cost, 6),
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "hours": hours,
        })

    @app.get("/crons", response_class=HTMLResponse)
    async def crons_page(request: Request):
        """Cron job monitoring dashboard."""
        cron_stats = storage.get_cron_stats()
        return templates.TemplateResponse(request, "crons.html", context={
            "cron_stats": cron_stats,
        })

    @app.get("/patterns", response_class=HTMLResponse)
    async def patterns_page(request: Request):
        """Patterns and trends page."""
        from agentwatch.core import _agent, init, _reset
        from agentwatch.patterns import detect_patterns as _detect, detect_trends as _trends

        was_init = _agent is not None
        if not was_init:
            init("_dashboard", db_path=storage.db_path)

        try:
            patterns = _detect(window_hours=24)
            trends = _trends(window_hours=24)
        finally:
            if not was_init:
                _reset()

        return templates.TemplateResponse(request, "patterns.html", context={
            "patterns": [p.to_dict() for p in patterns],
            "trends": trends.to_dict(),
        })

    # ─── Prometheus Metrics ──────────────────────────────────────────────

    @app.get("/metrics")
    async def prometheus_metrics():
        """
        Prometheus/OpenMetrics compatible metrics endpoint.

        Scrape this with Prometheus, VictoriaMetrics, or any compatible collector.
        """
        from fastapi.responses import Response
        from agentwatch.exporters.prometheus import PrometheusExporter

        exporter = PrometheusExporter(storage)
        metrics_text = exporter.collect()
        return Response(
            content=metrics_text,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    # ─── Server-Sent Events ─────────────────────────────────────────────

    @app.get("/api/health/stream")
    async def health_stream(request: Request):
        """SSE stream of health check updates (polls every 10s)."""
        import asyncio

        async def event_generator():
            while True:
                if await request.is_disconnected():
                    break
                health = storage.get_health_latest()
                yield f"data: {json.dumps(health)}\n\n"
                await asyncio.sleep(10)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    # ─── API Endpoints (JSON) ────────────────────────────────────────────

    @app.get("/api/stats")
    async def api_stats(agent: str | None = None) -> dict:
        """Get aggregate statistics."""
        return storage.get_stats(agent_name=agent)

    @app.get("/api/traces")
    async def api_traces(
        agent: str | None = None,
        status: str | None = None,
        search: str | None = None,
        hours: int | None = None,
        min_duration_ms: float | None = None,
        max_duration_ms: float | None = None,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> list[dict]:
        """List traces with optional filters."""
        from agentwatch.models import TraceStatus
        status_filter = TraceStatus(status) if status else None
        return storage.get_traces(
            agent_name=agent,
            status=status_filter,
            name_contains=search,
            min_duration_ms=min_duration_ms,
            max_duration_ms=max_duration_ms,
            hours=hours,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/traces/{trace_id}")
    async def api_trace_detail(trace_id: str) -> dict | None:
        """Get trace detail with spans and events."""
        return storage.get_trace(trace_id) or {}

    @app.get("/api/logs")
    async def api_logs(
        agent: str | None = None,
        level: str | None = None,
        search: str | None = None,
        hours: int | None = None,
        limit: int = Query(100, ge=1, le=1000),
    ) -> list[dict]:
        """List logs with optional filters."""
        from agentwatch.models import LogLevel
        level_filter = LogLevel(level.lower()) if level else None
        return storage.get_logs(
            agent_name=agent,
            level=level_filter,
            search=search,
            hours=hours,
            limit=limit,
        )

    @app.get("/api/health")
    async def api_health(agent: str | None = None) -> list[dict]:
        """Get latest health check results."""
        return storage.get_health_latest(agent_name=agent)

    @app.get("/api/health/{name}/history")
    async def api_health_history(
        name: str,
        agent: str | None = None,
        limit: int = Query(100, ge=1, le=500),
    ) -> list[dict]:
        """Get health check history for a named check."""
        return storage.get_health_history(name, agent_name=agent, limit=limit)

    @app.get("/api/costs")
    async def api_costs(
        agent: str | None = None,
        hours: int | None = None,
    ) -> dict:
        """Get cost summary."""
        return storage.get_cost_summary(agent_name=agent, hours=hours)

    @app.get("/api/costs/usage")
    async def api_cost_usage(
        agent: str | None = None,
        model: str | None = None,
        hours: int | None = None,
        limit: int = Query(100, ge=1, le=1000),
    ) -> list[dict]:
        """Get token usage records."""
        return storage.get_token_usage(
            agent_name=agent,
            model=model,
            hours=hours,
            limit=limit,
        )

    @app.get("/api/report")
    async def api_report(
        agent: str | None = None,
        hours: int = Query(24, ge=1, le=720),
    ) -> dict:
        """Generate a summary report."""
        from agentwatch.core import _agent, init, _reset
        from agentwatch.reports import summary_data

        was_init = _agent is not None
        if not was_init:
            init("_dashboard", db_path=storage.db_path)
        try:
            return summary_data(hours=hours, agent_name=agent)
        finally:
            if not was_init:
                _reset()

    @app.get("/api/alerts")
    async def api_alerts() -> dict:
        """Get alert configuration and history."""
        from agentwatch.alerts import get_manager
        manager = get_manager()
        return {
            "rules": [
                {"name": r.name, "alert_type": r.alert_type.value,
                 "enabled": r.enabled, "cooldown_seconds": r.cooldown_seconds}
                for r in manager.rules
            ],
            "history": [a.to_dict() for a in manager.history[-50:]],
        }

    @app.get("/api/alerts/check")
    async def api_alerts_check() -> list[dict]:
        """Run all alert checks and return any fired alerts."""
        from agentwatch.alerts import check_all
        alerts = check_all()
        return [a.to_dict() for a in alerts]

    # ─── Ingestion API (v1) ──────────────────────────────────────────────

    @app.post("/api/v1/ingest/traces")
    async def ingest_traces_endpoint(request: Request) -> dict:
        """
        Ingest one or more traces from a remote agent.

        Accepts:
            {"name": "...", "agent_name": "...", ...}  — single trace
            [{"name": "..."}, ...]                      — array of traces
        """
        from agentwatch.ingest import ingest_trace

        body = await request.json()
        items = body if isinstance(body, list) else [body]
        ids = []
        for item in items:
            trace_id = ingest_trace(item, storage)
            ids.append(trace_id)
        return {"status": "ok", "ingested": len(ids), "ids": ids}

    @app.post("/api/v1/ingest/logs")
    async def ingest_logs_endpoint(request: Request) -> dict:
        """Ingest one or more log entries from a remote agent."""
        from agentwatch.ingest import ingest_log

        body = await request.json()
        items = body if isinstance(body, list) else [body]
        ids = []
        for item in items:
            log_id = ingest_log(item, storage)
            ids.append(log_id)
        return {"status": "ok", "ingested": len(ids), "ids": ids}

    @app.post("/api/v1/ingest/health")
    async def ingest_health_endpoint(request: Request) -> dict:
        """Ingest one or more health check results from a remote agent."""
        from agentwatch.ingest import ingest_health

        body = await request.json()
        items = body if isinstance(body, list) else [body]
        names = []
        for item in items:
            name = ingest_health(item, storage)
            names.append(name)
        return {"status": "ok", "ingested": len(names), "names": names}

    @app.post("/api/v1/ingest/costs")
    async def ingest_costs_endpoint(request: Request) -> dict:
        """Ingest one or more token usage / cost records from a remote agent."""
        from agentwatch.ingest import ingest_cost

        body = await request.json()
        items = body if isinstance(body, list) else [body]
        ids = []
        for item in items:
            usage_id = ingest_cost(item, storage)
            ids.append(usage_id)
        return {"status": "ok", "ingested": len(ids), "ids": ids}

    @app.post("/api/v1/ingest/metrics")
    async def ingest_metrics_endpoint(request: Request) -> dict:
        """Ingest one or more metric data points from a remote agent."""
        from agentwatch.ingest import ingest_metric

        body = await request.json()
        items = body if isinstance(body, list) else [body]
        ids = []
        for item in items:
            metric_id = ingest_metric(item, storage)
            ids.append(metric_id)
        return {"status": "ok", "ingested": len(ids), "ids": ids}

    @app.post("/api/v1/ingest/batch")
    async def ingest_batch_endpoint(request: Request) -> dict:
        """
        Ingest a batch of mixed records.

        Accepts: {"traces": [...], "logs": [...], "health": [...], "costs": [...]}
        """
        from agentwatch.ingest import ingest_batch

        body = await request.json()
        counts = ingest_batch(body, storage)
        total = sum(counts.values())
        return {"status": "ok", "ingested": total, "counts": counts}

    @app.post("/api/v1/ingest/model_usage")
    async def ingest_model_usage_endpoint(request: Request) -> dict:
        """
        Ingest model usage records from any LLM integration.

        Accepts single record or array. Each record:
            {"model": "...", "prompt_tokens": N, "completion_tokens": N,
             "cost_usd": N, "latency_ms": N, "agent_name": "..."}
        """
        from agentwatch.ingest import ingest_model_usage

        body = await request.json()
        items = body if isinstance(body, list) else [body]
        ids = [ingest_model_usage(item, storage) for item in items]
        return {"status": "ok", "ingested": len(ids), "ids": ids}

    @app.post("/api/v1/ingest/cron_run")
    async def ingest_cron_run_endpoint(request: Request) -> dict:
        """
        Ingest cron job run results from any scheduler.

        Accepts single record or array. Each record:
            {"job_name": "...", "status": "ok|error|timeout",
             "duration_ms": N, "error": "...", "agent_name": "..."}
        """
        from agentwatch.ingest import ingest_cron_run

        body = await request.json()
        items = body if isinstance(body, list) else [body]
        ids = [ingest_cron_run(item, storage) for item in items]
        return {"status": "ok", "ingested": len(ids), "ids": ids}

    # ─── Metrics API ─────────────────────────────────────────────────────

    @app.get("/api/metrics")
    async def api_metrics(
        name: str | None = None,
        agent: str | None = None,
        hours: int | None = None,
        limit: int = Query(100, ge=1, le=1000),
    ) -> list[dict]:
        """List metric data points."""
        return storage.get_metrics(
            name=name,
            agent_name=agent,
            hours=hours,
            limit=limit,
        )

    @app.get("/api/metrics/list")
    async def api_metrics_list(agent: str | None = None) -> list[dict]:
        """List all known metric names with latest values."""
        return storage.list_metrics(agent_name=agent)

    @app.get("/api/metrics/{metric_name}/summary")
    async def api_metric_summary(
        metric_name: str,
        agent: str | None = None,
        hours: int | None = None,
    ) -> dict:
        """Get aggregate statistics for a metric."""
        return storage.get_metric_summary(
            name=metric_name,
            agent_name=agent,
            hours=hours,
        )

    # ─── Model Usage API ─────────────────────────────────────────────────

    @app.get("/api/model-stats")
    async def api_model_stats(hours: int = Query(24, ge=1, le=720)) -> list[dict]:
        """Get per-model usage statistics."""
        return storage.get_model_stats(hours=hours)

    # ─── Cron Monitoring API ─────────────────────────────────────────────

    @app.get("/api/cron-stats")
    async def api_cron_stats() -> list[dict]:
        """Get cron job run statistics."""
        return storage.get_cron_stats()

    @app.get("/api/cron-history/{job_name}")
    async def api_cron_history(
        job_name: str,
        limit: int = Query(50, ge=1, le=200),
    ) -> list[dict]:
        """Get run history for a specific cron job."""
        return storage.get_cron_history(job_name, limit=limit)

    @app.get("/api/patterns")
    async def api_patterns(
        agent: str | None = None,
        hours: int = Query(24, ge=1, le=168),
    ) -> list[dict]:
        """Detect and return patterns."""
        # Import here to avoid circular import
        from agentwatch.patterns import detect_patterns as _detect

        # Temporarily set up a read-only agent for pattern detection
        from agentwatch.core import _agent, init, shutdown, _reset

        was_init = _agent is not None
        if not was_init:
            init("_dashboard", db_path=storage.db_path)

        try:
            patterns = _detect(agent_name=agent, window_hours=hours)
            return [p.to_dict() for p in patterns]
        finally:
            if not was_init:
                _reset()

    @app.get("/api/trends")
    async def api_trends(
        agent: str | None = None,
        hours: int = Query(24, ge=1, le=168),
    ) -> dict:
        """Get trend analysis."""
        from agentwatch.patterns import detect_trends as _trends
        from agentwatch.core import _agent, init, _reset

        was_init = _agent is not None
        if not was_init:
            init("_dashboard", db_path=storage.db_path)

        try:
            trends = _trends(agent_name=agent, window_hours=hours)
            return trends.to_dict()
        finally:
            if not was_init:
                _reset()

    return app


def run_server(
    db_path: str | None = None,
    host: str = "0.0.0.0",
    port: int = 8470,
    auth_token: str | None = None,
) -> None:
    """
    Start the AgentWatch dashboard server.

    Args:
        db_path: Path to SQLite database.
        host: Host to bind to.
        port: Port to listen on.
        auth_token: Optional authentication token for dashboard access.
    """
    import uvicorn

    app = create_app(db_path=db_path, auth_token=auth_token)
    print(f"\n  🔭 AgentWatch Dashboard: http://{host}:{port}")
    if auth_token:
        from agentwatch.auth import hash_token
        print(f"  🔒 Authentication enabled (token hash: {hash_token(auth_token)})")
    else:
        print("  🔓 No authentication — dashboard is publicly accessible")
    print()
    uvicorn.run(app, host=host, port=port, log_level="info")
