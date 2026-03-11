/**
 * AgentWatch telemetry hook for OpenClaw.
 *
 * Sends traces, logs, health checks, and metrics to an AgentWatch server
 * via its HTTP ingestion API. All sends are fire-and-forget to avoid
 * blocking message processing.
 *
 * Installation:
 *   Copy this directory to ~/.openclaw/workspace/hooks/agentwatch/
 *   Enable in openclaw.json: hooks.internal.entries.agentwatch.enabled = true
 *   Restart the gateway.
 *
 * Environment variables:
 *   AGENTWATCH_URL        - Server URL (default: http://172.17.0.1:8470)
 *   AGENTWATCH_TOKEN      - Optional auth token
 *   AGENTWATCH_AGENT_NAME - Agent name (default: openclaw-gateway)
 */

const AGENTWATCH_URL =
  process.env.AGENTWATCH_URL || "http://172.17.0.1:8470";
const AGENTWATCH_TOKEN = process.env.AGENTWATCH_TOKEN || "";
const AGENT_NAME = process.env.AGENTWATCH_AGENT_NAME || "openclaw-gateway";

const MAX_CONTENT = 200;

// ── Helpers ────────────────────────────────────────────────────────────

function uuid(): string {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

function now(): string {
  return new Date().toISOString();
}

function truncate(s: string, max: number): string {
  if (!s) return "";
  const clean = s.replace(/\n/g, " ").trim();
  return clean.length <= max ? clean : clean.slice(0, max) + "...";
}

// ── HTTP client (fire-and-forget) ──────────────────────────────────────

async function post(
  path: string,
  body: Record<string, unknown>
): Promise<void> {
  const url = `${AGENTWATCH_URL}/api/v1/ingest/${path}`;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "User-Agent": "openclaw-agentwatch-hook/1.0",
  };
  if (AGENTWATCH_TOKEN) {
    headers["Authorization"] = `Bearer ${AGENTWATCH_TOKEN}`;
  }

  try {
    const resp = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
    if (!resp.ok) {
      console.error(
        `[agentwatch] POST ${path} failed: ${resp.status} ${resp.statusText}`
      );
    }
  } catch (err) {
    console.error(
      `[agentwatch] POST ${path} error:`,
      err instanceof Error ? err.message : String(err)
    );
  }
}

function sendTrace(
  name: string,
  status: "completed" | "failed",
  durationMs: number,
  metadata: Record<string, unknown> = {},
  spans: Record<string, unknown>[] = []
): void {
  const traceId = uuid();
  const startedAt = new Date(Date.now() - durationMs).toISOString();
  const endedAt = now();

  void post("traces", {
    id: traceId,
    agent_name: AGENT_NAME,
    name,
    status,
    started_at: startedAt,
    ended_at: endedAt,
    duration_ms: durationMs,
    metadata,
    spans:
      spans.length > 0
        ? spans
        : [
            {
              id: uuid(),
              trace_id: traceId,
              name,
              status,
              started_at: startedAt,
              ended_at: endedAt,
              duration_ms: durationMs,
              metadata,
              events: [],
            },
          ],
  });
}

function sendLog(
  level: string,
  message: string,
  metadata: Record<string, unknown> = {}
): void {
  void post("logs", {
    agent_name: AGENT_NAME,
    level,
    message,
    timestamp: now(),
    metadata,
  });
}

function sendHealth(
  name: string,
  status: string,
  message: string,
  metadata: Record<string, unknown> = {}
): void {
  void post("health", {
    name,
    agent_name: AGENT_NAME,
    status,
    message,
    timestamp: now(),
    metadata,
  });
}

function sendMetric(
  name: string,
  value: number,
  tags: Record<string, string> = {}
): void {
  void post("metrics", {
    agent_name: AGENT_NAME,
    name,
    value,
    kind: "counter",
    tags,
    timestamp: now(),
  });
}

// ── Skip patterns (heartbeats, noise) ──────────────────────────────────

const SKIP = [
  /^HEARTBEAT_OK$/i,
  /^NO_REPLY$/i,
  /^Read HEARTBEAT\.md/i,
  /^\s*$/,
];

function shouldSkip(content: string): boolean {
  return SKIP.some((p) => p.test((content || "").trim()));
}

// ── Main handler ───────────────────────────────────────────────────────

const handler = async (event: any) => {
  try {
    const { type, action, context, sessionKey } = event;

    // ── Gateway startup ──────────────────────────────────────────────
    if (type === "gateway" && action === "startup") {
      sendHealth("gateway", "ok", "Gateway started", {
        sessionKey,
        timestamp: now(),
      });
      sendLog("info", "OpenClaw gateway started", { event: "startup" });
      return;
    }

    // ── Message received ─────────────────────────────────────────────
    if (type === "message" && action === "received") {
      const content = context?.content || context?.body || "";
      if (shouldSkip(content)) return;

      const from =
        context?.metadata?.senderName || context?.from || "unknown";
      const channel = context?.channelId || "unknown";
      const isGroup = !!context?.isGroup;

      sendTrace("msg:received:" + channel, "completed", 1, {
        direction: "inbound",
        channel,
        from,
        isGroup,
        content: truncate(content, MAX_CONTENT),
        conversationId: context?.conversationId,
      });

      sendMetric("messages_received", 1, { channel });
      return;
    }

    // ── Message sent ─────────────────────────────────────────────────
    if (type === "message" && action === "sent") {
      const content = context?.content || context?.body || "";
      if (shouldSkip(content)) return;

      const to = context?.to || "unknown";
      const channel = context?.channelId || "unknown";
      const success = context?.success !== false;

      sendTrace(
        "msg:sent:" + channel,
        success ? "completed" : "failed",
        1,
        {
          direction: "outbound",
          channel,
          to,
          success,
          error: context?.error,
          isGroup: !!context?.isGroup,
          content: truncate(content, MAX_CONTENT),
          conversationId: context?.conversationId,
        }
      );

      sendMetric("messages_sent", 1, {
        channel,
        success: String(success),
      });

      if (!success && context?.error) {
        sendLog("error", "Message delivery failed: " + context.error, {
          channel,
          to,
        });
      }
      return;
    }

    // ── Commands ─────────────────────────────────────────────────────
    if (type === "command") {
      sendLog("info", "Command: /" + action, {
        event: "command:" + action,
        sessionKey,
        source: context?.commandSource,
        senderId: context?.senderId,
      });

      sendTrace("command:" + action, "completed", 1, {
        command: action,
        sessionKey,
        source: context?.commandSource,
      });
      return;
    }
  } catch (err) {
    // Never throw — don't break message processing
    console.error(
      "[agentwatch]",
      err instanceof Error ? err.message : String(err)
    );
  }
};

export default handler;
