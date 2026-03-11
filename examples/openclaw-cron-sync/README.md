# OpenClaw Cron Sync

An integration example that polls OpenClaw's cron job list and syncs
recent run statuses to AgentWatch for monitoring on the Crons dashboard.

## What it does

1. Calls `openclaw cron list --json` to get all configured jobs
2. For each job, calls `openclaw cron runs <id> --json` for recent runs
3. POSTs each run outcome to `POST /api/v1/ingest/cron_run`

## Usage

Run manually:

```bash
python3 examples/openclaw-cron-sync/sync.py
```

Or schedule it as an OpenClaw cron job (runs every 5 minutes):

```json
{
  "schedule": { "kind": "every", "everyMs": 300000 },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "message": "Run python3 /path/to/agentwatch/examples/openclaw-cron-sync/sync.py to sync cron run statuses to AgentWatch"
  }
}
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTWATCH_URL` | `http://localhost:8470` | AgentWatch server URL |
| `AGENT_NAME` | `openclaw` | Agent name for records |
| `LOOKBACK_HOURS` | `1` | How far back to sync (in hours) |

## Direct ingestion (no OpenClaw)

Any scheduler can POST cron run outcomes directly without this script:

```bash
curl -X POST http://localhost:8470/api/v1/ingest/cron_run \
  -H "Content-Type: application/json" \
  -d '{
    "job_name": "my-daily-job",
    "status": "ok",
    "duration_ms": 1234,
    "agent_name": "my-agent"
  }'
```

Or from Python:

```python
import agentwatch

agentwatch.init("my-agent")

# Simple one-liner
agentwatch.record_cron_run("my-daily-job", status="ok", duration_ms=1234)

# Context manager — auto-times and captures exceptions
with agentwatch.cron_run("my-daily-job"):
    do_work()
```
