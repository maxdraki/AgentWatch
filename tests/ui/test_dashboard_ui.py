"""
Playwright UI tests for the AgentWatch dashboard.

Verifies that all dashboard pages render correctly, new tabs (Models, Crons)
are accessible, nav links are present, and auto-refresh indicators appear.

Run with:
    pytest tests/ui/ --browser chromium -v

Requirements:
    pip install pytest-playwright
    playwright install chromium
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    """Temporary database path shared across the test session."""
    return str(tmp_path_factory.mktemp("aw_ui_test") / "test.db")


@pytest.fixture(scope="session", autouse=True)
def seed_demo_data(db_path):
    """Seed demo data into the test database before running UI tests."""
    src_root = Path(__file__).parent.parent.parent / "src"
    demo_script = Path(__file__).parent.parent.parent / "examples" / "demo_seed.py"

    result = subprocess.run(
        [sys.executable, str(demo_script)],
        env={**os.environ, "PYTHONPATH": str(src_root), "AGENTWATCH_DB": db_path},
        capture_output=True,
        text=True,
        timeout=120,
    )
    # Non-fatal: tests can still run with an empty database
    if result.returncode != 0:
        print(f"\nDemo seed warning (exit {result.returncode}):", result.stderr[:500])


@pytest.fixture(scope="session")
def live_server(db_path):
    """Start a live AgentWatch server on a free port for the session."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    src_root = Path(__file__).parent.parent.parent / "src"
    env = {**os.environ, "PYTHONPATH": str(src_root), "AGENTWATCH_DB": db_path}

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "agentwatch.server.app:create_app",
            "--factory",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-level", "error",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    base_url = f"http://127.0.0.1:{port}"

    # Wait for the server to become ready (up to 10s)
    for _ in range(20):
        try:
            urllib.request.urlopen(base_url + "/", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    else:
        proc.terminate()
        pytest.fail("AgentWatch server did not start within 10 seconds")

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ─── Tests ───────────────────────────────────────────────────────────────────


def test_dashboard_loads(page, live_server):
    """Dashboard renders with the correct title and at least 5 nav links."""
    page.goto(live_server + "/")
    assert "AgentWatch" in page.title()

    nav_links = page.locator(".nav-item")
    assert nav_links.count() >= 5, f"Expected ≥5 nav links, got {nav_links.count()}"


def test_models_tab_loads(page, live_server):
    """Models tab renders without error — shows table or empty state."""
    page.goto(live_server + "/models")
    # No 5xx in title
    assert "Error" not in page.title(), f"Error page: {page.title()}"

    has_table = page.locator("#models-table").count() > 0
    has_empty = page.locator(".empty").count() > 0
    assert has_table or has_empty, "Neither #models-table nor .empty found on /models"


def test_crons_tab_loads(page, live_server):
    """Crons tab renders without error — shows table or empty state."""
    page.goto(live_server + "/crons")
    assert "Error" not in page.title()

    has_table = page.locator("#crons-table").count() > 0
    has_empty = page.locator(".empty").count() > 0
    assert has_table or has_empty, "Neither #crons-table nor .empty found on /crons"


def test_auto_refresh_indicator(page, live_server):
    """Auto-refresh indicator on /models appears and shows non-empty text."""
    page.goto(live_server + "/models")

    # The indicator element must exist
    indicator = page.locator("#last-updated-models")
    indicator.wait_for(state="attached", timeout=5000)

    # After a short wait the JS should have populated it
    time.sleep(6)
    text = indicator.inner_text().strip()
    assert text != "" and text != "—", f"Expected indicator text, got: {repr(text)}"


def test_nav_links(page, live_server):
    """All nav paths respond with HTTP status < 400."""
    nav_paths = [
        "/",
        "/traces",
        "/logs",
        "/health",
        "/costs",
        "/metrics-dashboard",
        "/patterns",
        "/alerts",
        "/agents",
        "/models",
        "/crons",
    ]

    for path in nav_paths:
        response = page.goto(live_server + path)
        assert response is not None, f"No response for {path}"
        assert response.status < 400, \
            f"{path} returned HTTP {response.status}"


def test_models_nav_link_present(page, live_server):
    """Models link is present in the sidebar nav."""
    page.goto(live_server + "/")
    models_link = page.locator('a.nav-item[href="/models"]')
    assert models_link.count() > 0, "Models nav link not found in sidebar"


def test_crons_nav_link_present(page, live_server):
    """Crons link is present in the sidebar nav."""
    page.goto(live_server + "/")
    crons_link = page.locator('a.nav-item[href="/crons"]')
    assert crons_link.count() > 0, "Crons nav link not found in sidebar"
