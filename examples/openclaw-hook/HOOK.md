---
name: agentwatch
description: "Send telemetry to AgentWatch observability dashboard"
homepage: https://github.com/maxdraki/AgentWatch
metadata:
  {
    "openclaw":
      {
        "emoji": "🔭",
        "events":
          [
            "gateway:startup",
            "message:received",
            "message:sent",
            "command:new",
            "command:reset",
          ],
        "export": "default",
      },
  }
---

# AgentWatch Telemetry Hook

Sends OpenClaw gateway events to an [AgentWatch](https://github.com/maxdraki/AgentWatch) server for observability.

Tracks:
- **Traces** — each message received/sent pair becomes a trace with spans
- **Logs** — commands, session lifecycle, errors
- **Health** — gateway startup confirmation
- **Metrics** — message counts by channel

## Installation

Copy this directory into your OpenClaw workspace hooks:

```bash
cp -r examples/openclaw-hook ~/.openclaw/workspace/hooks/agentwatch
```

Then enable in `openclaw.json`:

```json
{
  "hooks": {
    "internal": {
      "entries": {
        "agentwatch": { "enabled": true }
      }
    }
  }
}
```

Restart the gateway to load the hook.

## Handlers

This directory contains two handler implementations:

| File | Status | Description |
|------|--------|-------------|
| `handler.ts` | **Active** | Works with current OpenClaw events (`message:received`, `message:sent`, `command`, `gateway:startup`) |
| `index.ts` | Future | NDJSON stdin handler for `conversation:start/end` and `model:used` events (not yet emitted by OpenClaw) |

Use `handler.ts` — it's the one that works with OpenClaw v2026.3.x.

## How it works

The hook correlates `message:received` and `message:sent` events by conversation ID to compute **real round-trip duration** — the time from when a message arrives to when the reply is delivered. Each conversation turn becomes a trace with nested spans (conversation + delivery), giving you a waterfall view in the dashboard.

Events that can't be correlated (e.g. outbound-only announcements) still produce traces with zero duration.

## Configuration

Set the following environment variables (via `hooks.internal.entries.agentwatch.env` in `openclaw.json` or container env):

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTWATCH_URL` | `http://172.17.0.1:8470` | AgentWatch server URL (see note below) |
| `AGENTWATCH_TOKEN` | *(none)* | Optional auth token |
| `AGENTWATCH_AGENT_NAME` | `openclaw-gateway` | Agent name in dashboard |

## Running AgentWatch

```bash
pip install agentwatch[server]
agentwatch serve --host 0.0.0.0 --port 8470
```

Then open `http://localhost:8470` to see your OpenClaw telemetry.

## Network note

The default `AGENTWATCH_URL` uses `172.17.0.1` — the Docker bridge gateway IP that lets the container reach the host. This works when OpenClaw runs in Docker and AgentWatch runs on the host.

| Setup | URL to use |
|-------|-----------|
| OpenClaw in Docker, AgentWatch on host | `http://172.17.0.1:8470` (default) |
| Both on the same host (no Docker) | `http://localhost:8470` |
| AgentWatch on a different machine | `http://<agentwatch-ip>:8470` |
