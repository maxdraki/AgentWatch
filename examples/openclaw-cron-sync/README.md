# OpenClaw Cron Sync

An integration example that polls OpenClaw's cron job list and syncs
recent run statuses to AgentWatch for monitoring on the Crons dashboard.

## What it does

1. Calls `openclaw cron list --json` to get all configured jobs
2. For each job, calls `openclaw cron runs --id <id> --limit 20` for recent runs
3. Filters to finished runs within the lookback window
4. POSTs each run outcome to `POST /api/v1/ingest/cron_run`

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

## Running from the host (Docker)

If OpenClaw runs in Docker, run the sync script on the **host** and point
it at the container:

```bash
OPENCLAW_CONTAINER=openclaw-openclaw-gateway-1 python3 sync.py
```

The script calls `openclaw` directly by default. To run it against a
Docker container, wrap it with `docker exec` or set `OPENCLAW_CONTAINER`.

When both OpenClaw and AgentWatch run on the same host (AgentWatch outside
Docker), the default `AGENTWATCH_URL=http://localhost:8470` works. If
AgentWatch also runs inside Docker, use the Docker bridge gateway IP
(`http://172.17.0.1:8470`).

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTWATCH_URL` | `http://localhost:8470` | AgentWatch server URL |
| `AGENT_NAME` | `openclaw` | Agent name for records |
| `LOOKBACK_HOURS` | `1` | How far back to sync (in hours) |

## OpenClaw CLI notes

A few quirks discovered during real-world testing:

- `cron list` supports `--json` but wraps output in `{"jobs": [...]}`
- `cron runs` requires `--id <id>` (not a positional arg) and does **not**
  support `--json` — output is still JSON but via a different code path
- Config version warnings may appear on stdout before the JSON payload;
  the script handles this by scanning for the first `{` or `[`
- Run timestamps are epoch milliseconds (`ts` field), not ISO strings

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
