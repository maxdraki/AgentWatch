/**
 * AgentWatch OpenClaw Hook
 *
 * Bridges OpenClaw session events to AgentWatch traces and model usage.
 *
 * Events handled:
 *   - conversation:start  → opens a trace in AgentWatch
 *   - conversation:end    → closes the trace with duration and status
 *   - model:used          → records model invocation, tokens, and cost
 *
 * Correlation key:
 *   Uses conversationId when present (WhatsApp, Discord channels).
 *   Falls back to sessionKey for webchat and terminal sessions —
 *   this ensures webchat conversations produce proper traces with real
 *   durations rather than orphaned start/end records.
 *
 * Setup: see HOOK.md
 */

const AGENTWATCH_URL: string =
  process.env.AGENTWATCH_URL ?? "http://localhost:8470";

// ─── Event types ─────────────────────────────────────────────────────────────

interface ConversationStartEvent {
  type: "conversation:start";
  conversationId?: string;
  sessionKey?: string;
  channel?: string;
  agentName?: string;
  timestamp?: string;
}

interface ConversationEndEvent {
  type: "conversation:end";
  conversationId?: string;
  sessionKey?: string;
  channel?: string;
  agentName?: string;
  timestamp?: string;
  durationMs?: number;
  status?: string;
}

interface ModelUsedEvent {
  type: "model:used";
  model: string;
  promptTokens: number;
  completionTokens: number;
  costUsd?: number;
  latencyMs?: number;
  conversationId?: string;
  sessionKey?: string;
  agentName?: string;
}

type HookEvent =
  | ConversationStartEvent
  | ConversationEndEvent
  | ModelUsedEvent;

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Derive a stable correlation key for the event.
 *
 * Prefers conversationId (set for WhatsApp/Discord channels where a
 * message ID is available). Falls back to sessionKey for webchat and
 * terminal sessions — this is the fix for issue #8, ensuring webchat
 * conversations produce properly correlated start/end trace pairs.
 */
function correlationKey(
  event: ConversationStartEvent | ConversationEndEvent | ModelUsedEvent,
): string {
  return (
    ("conversationId" in event && event.conversationId
      ? event.conversationId
      : undefined) ??
    ("sessionKey" in event && event.sessionKey
      ? event.sessionKey
      : undefined) ??
    "unknown"
  );
}

async function post(path: string, body: unknown): Promise<void> {
  const url = `${AGENTWATCH_URL}${path}`;
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      console.error(
        `[agentwatch-hook] POST ${path} returned ${resp.status}`,
      );
    }
  } catch (err) {
    console.error(`[agentwatch-hook] POST ${path} failed:`, err);
  }
}

// ─── Event handlers ───────────────────────────────────────────────────────────

async function handleConversationStart(
  event: ConversationStartEvent,
): Promise<void> {
  const key = correlationKey(event);
  await post("/api/v1/ingest/traces", {
    id: key,
    agent_name: event.agentName ?? "openclaw",
    name: `conversation:${event.channel ?? "unknown"}`,
    status: "running",
    started_at: event.timestamp ?? new Date().toISOString(),
    metadata: {
      channel: event.channel,
      session_key: event.sessionKey,
      conversation_id: event.conversationId,
    },
  });
}

async function handleConversationEnd(
  event: ConversationEndEvent,
): Promise<void> {
  const key = correlationKey(event);
  await post("/api/v1/ingest/traces", {
    id: key,
    agent_name: event.agentName ?? "openclaw",
    name: `conversation:${event.channel ?? "unknown"}`,
    status: event.status === "error" ? "failed" : "completed",
    ended_at: event.timestamp ?? new Date().toISOString(),
    duration_ms: event.durationMs,
    metadata: {
      channel: event.channel,
      session_key: event.sessionKey,
      conversation_id: event.conversationId,
    },
  });
}

async function handleModelUsed(event: ModelUsedEvent): Promise<void> {
  await post("/api/v1/ingest/model_usage", {
    model: event.model,
    prompt_tokens: event.promptTokens,
    completion_tokens: event.completionTokens,
    cost_usd: event.costUsd ?? 0,
    latency_ms: event.latencyMs,
    agent_name: event.agentName ?? "openclaw",
  });
}

async function handleEvent(event: HookEvent): Promise<void> {
  switch (event.type) {
    case "conversation:start":
      await handleConversationStart(event);
      break;
    case "conversation:end":
      await handleConversationEnd(event);
      break;
    case "model:used":
      await handleModelUsed(event);
      break;
    default:
      // Unknown event type — ignore silently
      break;
  }
}

// ─── Main: read NDJSON from stdin ─────────────────────────────────────────────

async function main(): Promise<void> {
  let buffer = "";

  process.stdin.setEncoding("utf8");

  process.stdin.on("data", (chunk: string) => {
    buffer += chunk;
  });

  process.stdin.on("end", async () => {
    const lines = buffer.split("\n").filter((l) => l.trim().length > 0);
    for (const line of lines) {
      try {
        const event = JSON.parse(line) as HookEvent;
        await handleEvent(event);
      } catch (err) {
        console.error(
          "[agentwatch-hook] Failed to parse event:",
          line.slice(0, 200),
          err,
        );
      }
    }
  });
}

main().catch((err) => {
  console.error("[agentwatch-hook] Fatal error:", err);
  process.exit(1);
});
