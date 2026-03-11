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

## Configuration

Set the following environment variables (via `hooks.internal.entries.agentwatch.env` in `openclaw.json` or container env):

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTWATCH_URL` | `http://172.17.0.1:8470` | AgentWatch server URL (use Docker gateway IP from container) |
| `AGENTWATCH_TOKEN` | *(none)* | Optional auth token |
| `AGENTWATCH_AGENT_NAME` | `openclaw-gateway` | Agent name in dashboard |

## Running AgentWatch

```bash
pip install agentwatch[server]
agentwatch serve --host 0.0.0.0 --port 8470
```

Then open `http://localhost:8470` to see your OpenClaw telemetry.
