"""
Lightweight SVG chart generation for the AgentWatch dashboard.

Generates inline SVG charts without any JavaScript dependencies.
Charts are server-rendered and embedded directly in HTML templates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass
class ChartPoint:
    """A single data point for a chart."""
    label: str
    value: float
    color: str | None = None


def sparkline_svg(
    values: list[float],
    width: int = 200,
    height: int = 40,
    color: str = "#58a6ff",
    fill_opacity: float = 0.1,
) -> str:
    """
    Generate an SVG sparkline chart.

    Args:
        values: List of numeric values.
        width: SVG width in pixels.
        height: SVG height in pixels.
        color: Line/fill color.
        fill_opacity: Opacity of the area fill.

    Returns:
        SVG string.
    """
    if not values or len(values) < 2:
        return ""

    min_val = min(values)
    max_val = max(values)
    val_range = max_val - min_val or 1
    padding = 2

    points = []
    for i, v in enumerate(values):
        x = padding + (i / (len(values) - 1)) * (width - 2 * padding)
        y = height - padding - ((v - min_val) / val_range) * (height - 2 * padding)
        points.append(f"{x:.1f},{y:.1f}")

    polyline = " ".join(points)
    fill_points = f"{padding},{height - padding} {polyline} {width - padding},{height - padding}"

    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
  <polygon points="{fill_points}" fill="{color}" fill-opacity="{fill_opacity}"/>
  <polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""


def bar_chart_svg(
    data: list[ChartPoint],
    width: int = 400,
    height: int = 200,
    bar_color: str = "#58a6ff",
    bg_color: str = "transparent",
    label_color: str = "#8b949e",
    value_color: str = "#e6edf3",
) -> str:
    """
    Generate a horizontal bar chart SVG.

    Args:
        data: List of ChartPoint with label and value.
        width: SVG width.
        height: SVG height.
        bar_color: Default bar color.

    Returns:
        SVG string.
    """
    if not data:
        return ""

    max_val = max(d.value for d in data) or 1
    bar_height = min(24, (height - 20) / len(data) - 6)
    label_width = 120
    value_width = 60

    lines = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">']

    for i, point in enumerate(data):
        y = 10 + i * (bar_height + 6)
        bar_width = ((point.value / max_val) * (width - label_width - value_width - 20))
        color = point.color or bar_color

        # Label
        lines.append(
            f'  <text x="{label_width - 8}" y="{y + bar_height / 2 + 4}" '
            f'text-anchor="end" fill="{label_color}" font-size="12" '
            f'font-family="-apple-system, sans-serif">{_escape(point.label)}</text>'
        )

        # Bar
        lines.append(
            f'  <rect x="{label_width}" y="{y}" width="{max(2, bar_width):.1f}" '
            f'height="{bar_height}" rx="3" fill="{color}" opacity="0.8"/>'
        )

        # Value
        lines.append(
            f'  <text x="{label_width + bar_width + 8:.1f}" y="{y + bar_height / 2 + 4}" '
            f'fill="{value_color}" font-size="12" '
            f'font-family="-apple-system, sans-serif">{_format_value(point.value)}</text>'
        )

    lines.append("</svg>")
    return "\n".join(lines)


def donut_chart_svg(
    data: list[ChartPoint],
    size: int = 160,
    thickness: int = 20,
    colors: list[str] | None = None,
    label_color: str = "#e6edf3",
) -> str:
    """
    Generate a donut chart SVG.

    Args:
        data: List of ChartPoint with value (labels shown in legend).
        size: SVG size (square).
        thickness: Ring thickness.
        colors: Color palette. Defaults to a built-in palette.

    Returns:
        SVG string.
    """
    if not data:
        return ""

    default_colors = ["#58a6ff", "#3fb950", "#d29922", "#f85149", "#bc8cff", "#f78166", "#79c0ff"]
    palette = colors or default_colors
    total = sum(d.value for d in data) or 1

    cx = cy = size / 2
    r = (size - thickness) / 2
    circumference = 2 * 3.14159 * r

    lines = [f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg">']

    # Background ring
    lines.append(
        f'  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
        f'stroke="#30363d" stroke-width="{thickness}"/>'
    )

    offset: float = 0
    for i, point in enumerate(data):
        pct = point.value / total
        dash = pct * circumference
        gap = circumference - dash
        color = point.color or palette[i % len(palette)]
        rotation = -90 + (offset / total) * 360

        lines.append(
            f'  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
            f'stroke="{color}" stroke-width="{thickness}" '
            f'stroke-dasharray="{dash:.1f} {gap:.1f}" '
            f'transform="rotate({rotation:.1f} {cx} {cy})"/>'
        )
        offset += point.value

    # Center label
    lines.append(
        f'  <text x="{cx}" y="{cy + 5}" text-anchor="middle" '
        f'fill="{label_color}" font-size="18" font-weight="600" '
        f'font-family="-apple-system, sans-serif">{_format_value(total)}</text>'
    )
    lines.append(
        f'  <text x="{cx}" y="{cy + 20}" text-anchor="middle" '
        f'fill="#8b949e" font-size="11" '
        f'font-family="-apple-system, sans-serif">total</text>'
    )

    lines.append("</svg>")
    return "\n".join(lines)


def cost_timeline_data(
    usage_records: list[dict[str, Any]],
    days: int = 7,
) -> list[ChartPoint]:
    """
    Aggregate cost records into daily totals for charting.

    Args:
        usage_records: Raw token_usage records from storage.
        days: Number of days to show.

    Returns:
        List of ChartPoint with daily cost totals.
    """
    now = datetime.now(timezone.utc)
    daily: dict[str, float] = {}

    for i in range(days):
        date = (now - timedelta(days=days - 1 - i)).strftime("%m/%d")
        daily[date] = 0.0

    for record in usage_records:
        try:
            ts = record.get("timestamp", "")
            if isinstance(ts, str):
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            else:
                dt = ts
            date_key = dt.strftime("%m/%d")
            if date_key in daily:
                daily[date_key] += record.get("estimated_cost_usd", 0)
        except (ValueError, TypeError, AttributeError):
            continue

    return [ChartPoint(label=k, value=v) for k, v in daily.items()]


def trace_timeline_data(
    traces: list[dict[str, Any]],
    hours: int = 24,
) -> tuple[list[ChartPoint], list[ChartPoint]]:
    """
    Aggregate traces into hourly buckets for charting.

    Returns:
        Tuple of (success_points, failure_points).
    """
    now = datetime.now(timezone.utc)
    success: dict[int, int] = {}
    failure: dict[int, int] = {}

    for h in range(hours):
        success[h] = 0
        failure[h] = 0

    for trace in traces:
        try:
            ts = trace.get("started_at", "")
            if isinstance(ts, str):
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            else:
                dt = ts
            hours_ago = int((now - dt).total_seconds() / 3600)
            if 0 <= hours_ago < hours:
                bucket = hours - 1 - hours_ago
                if trace.get("status") == "failed":
                    failure[bucket] = failure.get(bucket, 0) + 1
                else:
                    success[bucket] = success.get(bucket, 0) + 1
        except (ValueError, TypeError, AttributeError):
            continue

    success_points = [ChartPoint(label=f"{h}h", value=float(success[h])) for h in range(hours)]
    failure_points = [ChartPoint(label=f"{h}h", value=float(failure[h])) for h in range(hours)]

    return success_points, failure_points


# ─── Helpers ─────────────────────────────────────────────────────────────


def _escape(s: str) -> str:
    """Escape text for SVG."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _format_value(v: float) -> str:
    """Format a numeric value for display."""
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.1f}k"
    if v < 1 and v > 0:
        return f"${v:.4f}"
    return f"{v:.0f}"
