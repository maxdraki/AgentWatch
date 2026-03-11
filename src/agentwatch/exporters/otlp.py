"""
OpenTelemetry OTLP exporter for AgentWatch.

Exports AgentWatch traces to any OpenTelemetry-compatible backend via
HTTP/JSON (OTLP). No dependency on the OpenTelemetry SDK — we build
the OTLP JSON payload directly.

Supports:
- Jaeger, Zipkin, Grafana Tempo, Honeycomb, Datadog, etc.
- Any OTLP/HTTP endpoint

Usage:
    from agentwatch.exporters.otlp import OTLPExporter

    exporter = OTLPExporter(
        endpoint="http://localhost:4318/v1/traces",
        service_name="my-agent",
    )

    # Export recent traces
    exporter.export_recent(storage, hours=1)

    # Export a single trace
    exporter.export_trace(trace_dict)

    # Use as a background exporter (auto-export on interval)
    exporter.start_background(storage, interval_seconds=30)
    # ... later ...
    exporter.stop_background()
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError

from agentwatch.storage import Storage


def _iso_to_nanos(iso_str: str | None) -> int:
    """Convert ISO timestamp to nanoseconds since epoch."""
    if not iso_str:
        return int(time.time() * 1_000_000_000)
    try:
        if iso_str.endswith("Z"):
            iso_str = iso_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(iso_str)
        return int(dt.timestamp() * 1_000_000_000)
    except (ValueError, TypeError):
        return int(time.time() * 1_000_000_000)


def _ms_to_nanos(ms: float | None) -> int:
    """Convert milliseconds to nanoseconds."""
    if ms is None:
        return 0
    return int(ms * 1_000_000)


def _hex_id(s: str, length: int = 16) -> str:
    """Convert an AgentWatch ID to a hex string of the right length for OTLP."""
    # OTLP trace IDs are 32 hex chars, span IDs are 16 hex chars
    # Our IDs are 16-char hex already, so we pad or hash as needed
    try:
        # Already hex?
        int(s, 16)
        if len(s) >= length:
            return s[:length]
        return s.ljust(length, "0")
    except ValueError:
        # Not hex — hash it
        import hashlib
        h = hashlib.md5(s.encode()).hexdigest()
        return h[:length]


def _status_code(status: str) -> int:
    """Map AgentWatch trace status to OTLP StatusCode."""
    # OTLP: 0=UNSET, 1=OK, 2=ERROR
    if status == "completed":
        return 1
    if status == "failed":
        return 2
    return 0  # running or unknown


def _span_kind(name: str) -> int:
    """Infer OTLP SpanKind from span name."""
    # OTLP: 0=UNSPECIFIED, 1=INTERNAL, 2=SERVER, 3=CLIENT, 4=PRODUCER, 5=CONSUMER
    lower = name.lower()
    # Check server patterns first (handle/serve are typically server-side)
    if any(w in lower for w in ("serve", "handle", "endpoint")):
        return 2  # SERVER
    if any(w in lower for w in ("http", "api", "request", "fetch")):
        return 3  # CLIENT
    return 1  # INTERNAL


def _build_attributes(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a metadata dict to OTLP attributes."""
    attrs = []
    for key, value in metadata.items():
        attr: dict[str, Any] = {"key": key}
        if isinstance(value, bool):
            attr["value"] = {"boolValue": value}
        elif isinstance(value, int):
            attr["value"] = {"intValue": str(value)}
        elif isinstance(value, float):
            attr["value"] = {"doubleValue": value}
        elif isinstance(value, list):
            # Array of strings
            attr["value"] = {"arrayValue": {
                "values": [{"stringValue": str(v)} for v in value]
            }}
        else:
            attr["value"] = {"stringValue": str(value)}
        attrs.append(attr)
    return attrs


class OTLPExporter:
    """
    Export AgentWatch traces to an OTLP/HTTP endpoint.

    Args:
        endpoint: OTLP HTTP endpoint URL (e.g., "http://localhost:4318/v1/traces").
        service_name: Service name for the resource.
        headers: Additional HTTP headers (e.g., API keys).
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:4318/v1/traces",
        service_name: str = "agentwatch",
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ):
        self.endpoint = endpoint
        self.service_name = service_name
        self.headers = headers or {}
        self.timeout = timeout

        self._exported_count = 0
        self._error_count = 0
        self._bg_thread: threading.Thread | None = None
        self._bg_stop = threading.Event()

    def trace_to_otlp(self, trace: dict[str, Any]) -> dict[str, Any]:
        """
        Convert an AgentWatch trace dict to an OTLP ResourceSpans payload.

        Args:
            trace: A trace dict from storage (with spans and events).

        Returns:
            OTLP-compatible dict ready for JSON serialisation.
        """
        agent_name = trace.get("agent_name", self.service_name)
        trace_id = _hex_id(trace.get("id", uuid.uuid4().hex), 32)

        resource = {
            "attributes": _build_attributes({
                "service.name": agent_name,
                "service.version": "0.1.0",
                "telemetry.sdk.name": "agentwatch",
                "telemetry.sdk.language": "python",
            }),
        }

        otlp_spans = []

        for span_data in trace.get("spans", []):
            span_id = _hex_id(span_data.get("id", uuid.uuid4().hex[:16]), 16)
            parent_id = ""
            if span_data.get("parent_id"):
                parent_id = _hex_id(span_data["parent_id"], 16)

            # Build events from span events
            events = []
            for evt in span_data.get("events", []):
                events.append({
                    "timeUnixNano": str(_iso_to_nanos(evt.get("timestamp"))),
                    "name": evt.get("message", "event"),
                    "attributes": _build_attributes(evt.get("metadata", {})),
                })

            # Build span attributes from metadata
            attrs = _build_attributes(span_data.get("metadata", {}))

            # Add error as attribute if present
            if span_data.get("error"):
                attrs.append({
                    "key": "error.message",
                    "value": {"stringValue": span_data["error"]},
                })

            otlp_span = {
                "traceId": trace_id,
                "spanId": span_id,
                "name": span_data.get("name", "unnamed"),
                "kind": _span_kind(span_data.get("name", "")),
                "startTimeUnixNano": str(_iso_to_nanos(span_data.get("started_at"))),
                "endTimeUnixNano": str(_iso_to_nanos(span_data.get("ended_at"))),
                "attributes": attrs,
                "events": events,
                "status": {
                    "code": _status_code(span_data.get("status", "running")),
                },
            }

            if parent_id:
                otlp_span["parentSpanId"] = parent_id

            otlp_spans.append(otlp_span)

        # If no spans, create one from the trace itself
        if not otlp_spans:
            otlp_spans.append({
                "traceId": trace_id,
                "spanId": _hex_id(uuid.uuid4().hex[:16], 16),
                "name": trace.get("name", "unnamed"),
                "kind": 1,
                "startTimeUnixNano": str(_iso_to_nanos(trace.get("started_at"))),
                "endTimeUnixNano": str(_iso_to_nanos(trace.get("ended_at"))),
                "attributes": _build_attributes(trace.get("metadata", {})),
                "status": {"code": _status_code(trace.get("status", "running"))},
            })

        return {
            "resourceSpans": [{
                "resource": resource,
                "scopeSpans": [{
                    "scope": {
                        "name": "agentwatch",
                        "version": "0.1.0",
                    },
                    "spans": otlp_spans,
                }],
            }],
        }

    def export_trace(self, trace: dict[str, Any]) -> bool:
        """
        Export a single trace to the OTLP endpoint.

        Args:
            trace: A trace dict (from storage.get_trace()).

        Returns:
            True if export succeeded, False otherwise.
        """
        payload = self.trace_to_otlp(trace)
        return self._send(payload)

    def export_recent(
        self,
        storage: Storage,
        hours: int = 1,
        agent_name: str | None = None,
    ) -> int:
        """
        Export recent traces to the OTLP endpoint.

        Args:
            storage: AgentWatch storage instance.
            hours: Export traces from the last N hours.
            agent_name: Optional agent name filter.

        Returns:
            Number of traces exported.
        """
        traces = storage.get_traces(
            agent_name=agent_name,
            hours=hours,
            limit=500,
        )

        exported = 0
        for trace_summary in traces:
            trace_detail = storage.get_trace(trace_summary["id"])
            if trace_detail:
                if self.export_trace(trace_detail):
                    exported += 1

        return exported

    def start_background(
        self,
        storage: Storage,
        interval_seconds: int = 30,
        agent_name: str | None = None,
    ) -> None:
        """
        Start a background thread that periodically exports new traces.

        Args:
            storage: AgentWatch storage instance.
            interval_seconds: Export interval.
            agent_name: Optional agent name filter.
        """
        if self._bg_thread and self._bg_thread.is_alive():
            return

        self._bg_stop.clear()
        seen_ids: set[str] = set()

        def _worker():
            # Pre-populate seen IDs with existing traces
            existing = storage.get_traces(limit=1000)
            for t in existing:
                seen_ids.add(t["id"])

            while not self._bg_stop.wait(interval_seconds):
                try:
                    recent = storage.get_traces(
                        agent_name=agent_name,
                        hours=1,
                        limit=100,
                    )
                    for trace_summary in recent:
                        if trace_summary["id"] not in seen_ids:
                            seen_ids.add(trace_summary["id"])
                            if trace_summary["status"] != "running":
                                trace_detail = storage.get_trace(trace_summary["id"])
                                if trace_detail:
                                    self.export_trace(trace_detail)
                except Exception:
                    self._error_count += 1

        self._bg_thread = threading.Thread(target=_worker, daemon=True, name="otlp-exporter")
        self._bg_thread.start()

    def stop_background(self) -> None:
        """Stop the background export thread."""
        self._bg_stop.set()
        if self._bg_thread:
            self._bg_thread.join(timeout=5)
            self._bg_thread = None

    @property
    def stats(self) -> dict[str, Any]:
        """Get exporter statistics."""
        return {
            "exported": self._exported_count,
            "errors": self._error_count,
            "endpoint": self.endpoint,
            "service_name": self.service_name,
        }

    def _send(self, payload: dict[str, Any]) -> bool:
        """Send an OTLP payload to the endpoint."""
        body = json.dumps(payload).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "agentwatch-otlp/0.1.0",
            **self.headers,
        }

        req = Request(
            self.endpoint,
            data=body,
            headers=headers,
            method="POST",
        )

        try:
            with urlopen(req, timeout=self.timeout) as resp:
                self._exported_count += 1
                return True
        except URLError:
            self._error_count += 1
            return False
        except Exception:
            self._error_count += 1
            return False
