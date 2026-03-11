"""
Tests for cron monitoring: storage, ingestion, stats, and public API.
"""

from __future__ import annotations

import pytest

from agentwatch.storage import Storage
from agentwatch.ingest import ingest_cron_run


@pytest.fixture
def storage(tmp_path):
    return Storage(db_path=str(tmp_path / "test.db"))


# ─── Storage ─────────────────────────────────────────────────────────────────


def test_record_cron_run_ok(storage):
    """record_cron_run stores a successful run and returns a 16-char hex ID."""
    rid = storage.record_cron_run(
        job_name="daily-report",
        status="ok",
        duration_ms=1234.5,
        agent_name="scheduler",
    )
    assert rid and len(rid) == 16


def test_record_cron_run_error(storage):
    """record_cron_run stores a failed run with an error message."""
    rid = storage.record_cron_run(
        job_name="fetch-prices",
        status="error",
        duration_ms=500.0,
        error="Connection timed out",
        agent_name="scheduler",
    )
    assert rid


def test_get_cron_stats_empty(storage):
    """get_cron_stats returns an empty list when no runs are recorded."""
    assert storage.get_cron_stats() == []


def test_get_cron_stats_success_rate_and_consecutive_errors(storage):
    """get_cron_stats computes success rate and consecutive error count."""
    # Insert 4 successes then 2 errors (most recent = error)
    for _ in range(4):
        storage.record_cron_run("my-job", "ok", duration_ms=100.0)
    storage.record_cron_run("my-job", "error", error="boom1")
    storage.record_cron_run("my-job", "error", error="boom2")

    stats = storage.get_cron_stats()
    assert len(stats) == 1
    job = stats[0]
    assert job["job_name"] == "my-job"
    assert job["total_runs"] == 6
    # 4/6 = 66.7%
    assert abs(job["success_rate"] - 66.7) < 0.2
    assert job["consecutive_errors"] == 2
    assert job["last_status"] == "error"


def test_get_cron_stats_all_ok(storage):
    """get_cron_stats shows 0 consecutive errors when all runs are ok."""
    for _ in range(3):
        storage.record_cron_run("clean-job", "ok", duration_ms=50.0)
    stats = storage.get_cron_stats()
    assert stats[0]["consecutive_errors"] == 0
    assert stats[0]["success_rate"] == 100.0


def test_get_cron_stats_multiple_jobs(storage):
    """get_cron_stats returns one row per distinct job name."""
    storage.record_cron_run("job-a", "ok")
    storage.record_cron_run("job-b", "ok")
    storage.record_cron_run("job-a", "error", error="oops")

    stats = storage.get_cron_stats()
    assert len(stats) == 2
    names = {s["job_name"] for s in stats}
    assert names == {"job-a", "job-b"}


def test_get_cron_stats_avg_duration(storage):
    """get_cron_stats calculates average duration from available runs."""
    storage.record_cron_run("timed-job", "ok", duration_ms=100.0)
    storage.record_cron_run("timed-job", "ok", duration_ms=300.0)

    stats = storage.get_cron_stats()
    assert stats[0]["avg_duration_ms"] == 200.0


def test_get_cron_stats_no_duration(storage):
    """get_cron_stats returns None for avg_duration when no durations set."""
    storage.record_cron_run("no-dur-job", "ok", duration_ms=None)
    stats = storage.get_cron_stats()
    assert stats[0]["avg_duration_ms"] is None


def test_get_cron_history_filters_by_job(storage):
    """get_cron_history returns only runs for the specified job."""
    storage.record_cron_run("target-job", "ok", duration_ms=100)
    storage.record_cron_run("other-job", "ok", duration_ms=200)
    storage.record_cron_run("target-job", "error", error="x")

    history = storage.get_cron_history("target-job")
    assert len(history) == 2
    assert all(h["job_name"] == "target-job" for h in history)


def test_get_cron_history_most_recent_first(storage):
    """get_cron_history returns runs in reverse chronological order."""
    storage.record_cron_run("ordered-job", "ok", duration_ms=1.0)
    storage.record_cron_run("ordered-job", "error", error="later")

    history = storage.get_cron_history("ordered-job")
    assert history[0]["status"] == "error"
    assert history[1]["status"] == "ok"


# ─── Ingestion ────────────────────────────────────────────────────────────────


def test_ingest_cron_run_full(storage):
    """ingest_cron_run parses a full dict and stores the record."""
    rid = ingest_cron_run(
        {
            "job_name": "sync-data",
            "status": "ok",
            "duration_ms": 750,
            "agent_name": "remote",
        },
        storage,
    )
    assert rid

    stats = storage.get_cron_stats()
    assert len(stats) == 1
    assert stats[0]["job_name"] == "sync-data"


def test_ingest_cron_run_minimal(storage):
    """ingest_cron_run handles a minimal payload (job_name + status only)."""
    rid = ingest_cron_run({"job_name": "minimal-job", "status": "ok"}, storage)
    assert rid


def test_ingest_cron_run_with_error(storage):
    """ingest_cron_run stores error field when provided."""
    rid = ingest_cron_run(
        {"job_name": "failing-job", "status": "error", "error": "Timeout"},
        storage,
    )
    assert rid
    stats = storage.get_cron_stats()
    assert stats[0]["last_error"] == "Timeout"


# ─── Public API ──────────────────────────────────────────────────────────────


def test_public_api_record_cron_run(tmp_path):
    """agentwatch.record_cron_run() works via the top-level public API."""
    import agentwatch

    agentwatch.init("test-cron", db_path=str(tmp_path / "test.db"))
    try:
        rid = agentwatch.record_cron_run("public-api-job", "ok", duration_ms=42.0)
        assert rid is not None

        from agentwatch.core import _agent

        assert _agent is not None
        stats = _agent.storage.get_cron_stats()
        assert len(stats) == 1
        assert stats[0]["job_name"] == "public-api-job"
    finally:
        agentwatch.shutdown()


def test_cron_run_context_manager_success(tmp_path):
    """agentwatch.cron_run() context manager records timing and 'ok' status."""
    import agentwatch

    agentwatch.init("test-cron-ctx", db_path=str(tmp_path / "test.db"))
    try:
        with agentwatch.cron_run("ctx-job"):
            pass  # successful, no exception

        from agentwatch.core import _agent

        assert _agent is not None
        stats = _agent.storage.get_cron_stats()
        assert len(stats) == 1
        job = stats[0]
        assert job["job_name"] == "ctx-job"
        assert job["last_status"] == "ok"
        assert job["avg_duration_ms"] is not None
        assert job["avg_duration_ms"] >= 0
    finally:
        agentwatch.shutdown()


def test_cron_run_context_manager_captures_error(tmp_path):
    """agentwatch.cron_run() captures exceptions and records 'error' status."""
    import agentwatch

    agentwatch.init("test-cron-err", db_path=str(tmp_path / "test.db"))
    try:
        with pytest.raises(RuntimeError, match="test failure"):
            with agentwatch.cron_run("failing-job"):
                raise RuntimeError("test failure")

        from agentwatch.core import _agent

        assert _agent is not None
        stats = _agent.storage.get_cron_stats()
        assert len(stats) == 1
        job = stats[0]
        assert job["last_status"] == "error"
        assert job["last_error"] == "test failure"
        assert job["consecutive_errors"] == 1
    finally:
        agentwatch.shutdown()


def test_record_cron_run_returns_none_without_init():
    """record_cron_run returns None gracefully when agentwatch is not initialised."""
    import agentwatch.core as _core
    from agentwatch.cron_monitoring import record_cron_run
    original = _core._agent
    _core._agent = None
    try:
        result = record_cron_run("orphan-job", "ok")
        assert result is None
    finally:
        _core._agent = original
