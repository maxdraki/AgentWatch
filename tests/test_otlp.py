"""Tests for the OpenTelemetry OTLP exporter."""

from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock

from agentwatch.exporters.otlp import (
    OTLPExporter,
    _hex_id,
    _iso_to_nanos,
    _ms_to_nanos,
    _status_code,
    _span_kind,
    _build_attributes,
)


class TestHelpers:
    def test_iso_to_nanos(self):
        # Specific timestamp
        nanos = _iso_to_nanos("2026-03-10T01:00:00+00:00")
        assert nanos > 0
        # Should be roughly right (March 2026)
        assert nanos > 1_700_000_000_000_000_000

    def test_iso_to_nanos_z_suffix(self):
        nanos = _iso_to_nanos("2026-03-10T01:00:00Z")
        assert nanos > 0

    def test_iso_to_nanos_none(self):
        nanos = _iso_to_nanos(None)
        assert nanos > 0  # Defaults to now

    def test_ms_to_nanos(self):
        assert _ms_to_nanos(1000) == 1_000_000_000
        assert _ms_to_nanos(0.5) == 500_000
        assert _ms_to_nanos(None) == 0

    def test_hex_id_already_hex(self):
        result = _hex_id("abcdef1234567890", 16)
        assert result == "abcdef1234567890"

    def test_hex_id_short(self):
        result = _hex_id("abcd", 16)
        assert len(result) == 16
        assert result.startswith("abcd")

    def test_hex_id_long(self):
        result = _hex_id("abcdef1234567890extra", 16)
        assert len(result) == 16

    def test_hex_id_non_hex(self):
        result = _hex_id("not-a-hex-string", 16)
        assert len(result) == 16

    def test_hex_id_32_for_trace(self):
        result = _hex_id("abcdef12345678", 32)
        assert len(result) == 32

    def test_status_code(self):
        assert _status_code("completed") == 1
        assert _status_code("failed") == 2
        assert _status_code("running") == 0
        assert _status_code("unknown") == 0

    def test_span_kind(self):
        assert _span_kind("http-request") == 3  # CLIENT
        assert _span_kind("fetch-data") == 3
        assert _span_kind("api-call") == 3
        assert _span_kind("handle-request") == 2  # SERVER
        assert _span_kind("serve-page") == 2
        assert _span_kind("process-batch") == 1  # INTERNAL

    def test_build_attributes(self):
        attrs = _build_attributes({
            "string_val": "hello",
            "int_val": 42,
            "float_val": 3.14,
            "bool_val": True,
            "list_val": ["a", "b"],
        })

        assert len(attrs) == 5

        # Check types
        by_key = {a["key"]: a["value"] for a in attrs}
        assert by_key["string_val"] == {"stringValue": "hello"}
        assert by_key["int_val"] == {"intValue": "42"}
        assert by_key["float_val"] == {"doubleValue": 3.14}
        assert by_key["bool_val"] == {"boolValue": True}
        assert "arrayValue" in by_key["list_val"]

    def test_build_attributes_empty(self):
        assert _build_attributes({}) == []


class TestOTLPExporter:
    def test_trace_to_otlp_basic(self):
        exporter = OTLPExporter(service_name="test-agent")
        trace = {
            "id": "abc123def456",
            "agent_name": "test-agent",
            "name": "my-task",
            "status": "completed",
            "started_at": "2026-03-10T01:00:00+00:00",
            "ended_at": "2026-03-10T01:00:05+00:00",
            "duration_ms": 5000,
            "metadata": {},
            "spans": [
                {
                    "id": "span001",
                    "name": "main",
                    "status": "completed",
                    "started_at": "2026-03-10T01:00:00+00:00",
                    "ended_at": "2026-03-10T01:00:05+00:00",
                    "duration_ms": 5000,
                    "metadata": {"key": "value"},
                    "events": [],
                },
            ],
        }

        payload = exporter.trace_to_otlp(trace)

        assert "resourceSpans" in payload
        rs = payload["resourceSpans"][0]

        # Check resource
        resource_attrs = {a["key"]: a for a in rs["resource"]["attributes"]}
        assert "service.name" in resource_attrs

        # Check spans
        scope_spans = rs["scopeSpans"][0]
        assert scope_spans["scope"]["name"] == "agentwatch"
        assert len(scope_spans["spans"]) == 1

        otlp_span = scope_spans["spans"][0]
        assert otlp_span["name"] == "main"
        assert otlp_span["status"]["code"] == 1  # OK
        assert len(otlp_span["traceId"]) == 32

    def test_trace_to_otlp_with_events(self):
        exporter = OTLPExporter()
        trace = {
            "id": "trace1",
            "name": "task",
            "agent_name": "agent",
            "status": "completed",
            "metadata": {},
            "spans": [{
                "id": "span1",
                "name": "main",
                "status": "completed",
                "started_at": "2026-03-10T01:00:00+00:00",
                "metadata": {},
                "events": [
                    {
                        "message": "started processing",
                        "timestamp": "2026-03-10T01:00:01+00:00",
                        "metadata": {"count": 5},
                    },
                ],
            }],
        }

        payload = exporter.trace_to_otlp(trace)
        otlp_span = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        assert len(otlp_span["events"]) == 1
        assert otlp_span["events"][0]["name"] == "started processing"

    def test_trace_to_otlp_nested_spans(self):
        exporter = OTLPExporter()
        trace = {
            "id": "trace1",
            "name": "pipeline",
            "agent_name": "agent",
            "status": "completed",
            "metadata": {},
            "spans": [
                {
                    "id": "root_span",
                    "name": "pipeline",
                    "status": "completed",
                    "metadata": {},
                    "events": [],
                },
                {
                    "id": "child_span",
                    "parent_id": "root_span",
                    "name": "fetch",
                    "status": "completed",
                    "metadata": {},
                    "events": [],
                },
            ],
        }

        payload = exporter.trace_to_otlp(trace)
        spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
        assert len(spans) == 2

        child = [s for s in spans if s["name"] == "fetch"][0]
        assert "parentSpanId" in child

    def test_trace_to_otlp_failed(self):
        exporter = OTLPExporter()
        trace = {
            "id": "trace1",
            "name": "failing",
            "agent_name": "agent",
            "status": "failed",
            "metadata": {},
            "spans": [{
                "id": "span1",
                "name": "main",
                "status": "failed",
                "error": "Connection timeout",
                "metadata": {},
                "events": [],
            }],
        }

        payload = exporter.trace_to_otlp(trace)
        otlp_span = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        assert otlp_span["status"]["code"] == 2  # ERROR

        # Error should be in attributes
        attr_keys = [a["key"] for a in otlp_span["attributes"]]
        assert "error.message" in attr_keys

    def test_trace_to_otlp_no_spans(self):
        """A trace with no spans should create a synthetic span."""
        exporter = OTLPExporter()
        trace = {
            "id": "trace1",
            "name": "bare-trace",
            "agent_name": "agent",
            "status": "completed",
            "started_at": "2026-03-10T01:00:00+00:00",
            "metadata": {"key": "val"},
            "spans": [],
        }

        payload = exporter.trace_to_otlp(trace)
        spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
        assert len(spans) == 1
        assert spans[0]["name"] == "bare-trace"

    def test_stats(self):
        exporter = OTLPExporter(endpoint="http://fake:4318/v1/traces", service_name="test")
        stats = exporter.stats
        assert stats["exported"] == 0
        assert stats["errors"] == 0
        assert stats["endpoint"] == "http://fake:4318/v1/traces"
        assert stats["service_name"] == "test"

    def test_export_trace_success(self):
        exporter = OTLPExporter()
        sent = []

        def mock_send(payload):
            sent.append(payload)
            return True

        exporter._send = mock_send

        trace = {
            "id": "t1", "name": "task", "agent_name": "a",
            "status": "completed", "metadata": {},
            "spans": [{"id": "s1", "name": "main", "status": "completed",
                       "metadata": {}, "events": []}],
        }

        result = exporter.export_trace(trace)
        assert result is True
        assert len(sent) == 1

    def test_export_recent(self, tmp_path):
        from agentwatch.storage import Storage
        from agentwatch.ingest import ingest_trace

        storage = Storage(db_path=str(tmp_path / "test.db"))
        sent = []

        # Ingest some traces
        ingest_trace({"name": "task-1", "agent_name": "a", "status": "completed"}, storage)
        ingest_trace({"name": "task-2", "agent_name": "a", "status": "completed"}, storage)

        exporter = OTLPExporter()
        exporter._send = lambda payload: (sent.append(payload), True)[-1]

        exported = exporter.export_recent(storage, hours=1)
        assert exported == 2
        assert len(sent) == 2

    def test_otlp_payload_is_valid_json(self):
        """Ensure the payload serialises cleanly to JSON."""
        exporter = OTLPExporter()
        trace = {
            "id": "trace1",
            "name": "serialisation-test",
            "agent_name": "agent",
            "status": "completed",
            "started_at": "2026-03-10T01:00:00+00:00",
            "ended_at": "2026-03-10T01:00:05+00:00",
            "metadata": {"key": "value", "nested": {"a": 1}},
            "spans": [{
                "id": "span1",
                "name": "main",
                "status": "completed",
                "started_at": "2026-03-10T01:00:00+00:00",
                "ended_at": "2026-03-10T01:00:05+00:00",
                "metadata": {"float": 3.14, "list": [1, 2, 3]},
                "events": [{"message": "test", "timestamp": "2026-03-10T01:00:01+00:00", "metadata": {}}],
            }],
        }

        payload = exporter.trace_to_otlp(trace)
        # Should serialise without error
        json_str = json.dumps(payload)
        # Should parse back
        parsed = json.loads(json_str)
        assert "resourceSpans" in parsed
