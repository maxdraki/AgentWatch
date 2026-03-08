"""Tests for the SVG chart generation."""

import pytest

from agentwatch.server.charts import (
    sparkline_svg,
    bar_chart_svg,
    donut_chart_svg,
    cost_timeline_data,
    trace_timeline_data,
    ChartPoint,
)


class TestSparkline:
    def test_basic(self):
        svg = sparkline_svg([1, 3, 2, 5, 4])
        assert "<svg" in svg
        assert "polyline" in svg
        assert "polygon" in svg

    def test_empty(self):
        assert sparkline_svg([]) == ""

    def test_single_value(self):
        assert sparkline_svg([5]) == ""

    def test_two_values(self):
        svg = sparkline_svg([1, 2])
        assert "<svg" in svg

    def test_custom_dimensions(self):
        svg = sparkline_svg([1, 2, 3], width=300, height=80)
        assert 'width="300"' in svg
        assert 'height="80"' in svg

    def test_custom_color(self):
        svg = sparkline_svg([1, 2, 3], color="#ff0000")
        assert "#ff0000" in svg


class TestBarChart:
    def test_basic(self):
        data = [
            ChartPoint(label="A", value=10),
            ChartPoint(label="B", value=20),
        ]
        svg = bar_chart_svg(data)
        assert "<svg" in svg
        assert "A" in svg
        assert "B" in svg
        assert "<rect" in svg

    def test_empty(self):
        assert bar_chart_svg([]) == ""

    def test_custom_colors(self):
        data = [ChartPoint(label="X", value=5, color="#ff0000")]
        svg = bar_chart_svg(data)
        assert "#ff0000" in svg

    def test_large_values(self):
        data = [ChartPoint(label="Big", value=1500000)]
        svg = bar_chart_svg(data)
        assert "1.5M" in svg


class TestDonutChart:
    def test_basic(self):
        data = [
            ChartPoint(label="A", value=60),
            ChartPoint(label="B", value=40),
        ]
        svg = donut_chart_svg(data)
        assert "<svg" in svg
        assert "<circle" in svg
        assert "100" in svg  # total

    def test_empty(self):
        assert donut_chart_svg([]) == ""

    def test_single_segment(self):
        data = [ChartPoint(label="All", value=100)]
        svg = donut_chart_svg(data)
        assert "<svg" in svg


class TestTimelineData:
    def test_cost_timeline(self):
        records = [
            {"timestamp": "2026-03-07T00:00:00+00:00", "estimated_cost_usd": 0.05},
            {"timestamp": "2026-03-07T01:00:00+00:00", "estimated_cost_usd": 0.03},
        ]
        points = cost_timeline_data(records, days=7)
        assert len(points) == 7
        assert all(isinstance(p, ChartPoint) for p in points)

    def test_trace_timeline(self):
        traces = [
            {"started_at": "2026-03-07T00:00:00+00:00", "status": "completed"},
            {"started_at": "2026-03-07T00:00:00+00:00", "status": "failed"},
        ]
        success, failure = trace_timeline_data(traces, hours=24)
        assert len(success) == 24
        assert len(failure) == 24

    def test_empty_records(self):
        points = cost_timeline_data([], days=7)
        assert len(points) == 7
        assert all(p.value == 0 for p in points)
