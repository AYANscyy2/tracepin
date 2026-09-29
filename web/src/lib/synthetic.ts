// Seeded stress trace in the exported web shape: the same attributes the real agent loop
// writes, so the Graph view, its status derivation and the layout tests see realistic spans.

import type { Finding, Span, ToolOutcome } from "./types";

/** mulberry32: tiny deterministic PRNG so the same size always yields the same trace. */
function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const TOOLS = ["get_user", "fetch_user", "fetch_order_status", "search_kb", "calculate"];
const UNKNOWN = ["cancel_order", "send_email", "update_user"];
const MODEL = "gemini-3.5-flash-lite";

/**
 * A plausible agent run with exactly `targetSpans` spans: one invoke_agent root, turns of
 * chat + 0-3 tool calls, occasional parallel tool calls and nested sub-agents. Injects the
 * failure modes the detectors find (unknown tools, invalid arguments, slow chats with
 * transport retries, near-duplicate loops) so every node state shows up.
 */
export function generateSyntheticTrace(targetSpans: number, seed = 42): { spans: Span[]; findings: Finding[] } {
  const rand = rng(seed + targetSpans);
  const pick = <T,>(arr: readonly T[]) => arr[Math.floor(rand() * arr.length)];
  const spans: Span[] = [];
  const findings: Finding[] = [];
  const traceId = `0xsynthetic${targetSpans}`;
  let clock = 0;
  let n = 0;
  const nextId = () => `syn-${(n++).toString(36)}`;
  const span = (id: string, parent: string | null, name: string, operation: string, start: number, end: number, attributes: Record<string, unknown>, error = false): Span => ({
    span_id: id, parent_span_id: parent, name, operation, start_ms: start, end_ms: end,
    status_code: error ? "ERROR" : "UNSET", status_message: error ? `${name} failed` : null, attributes, events: [],
  });
  const finding = (detector_id: string, severity: Finding["severity"], span_ids: string[], title: string): Finding => ({
    detector_id, severity, confidence: 0.9, trace_id: traceId, task_id: "synthetic", span_ids, title, detail: title,
    evidence: {}, code_location: null, scope: "trace",
  });

  const rootId = nextId();
  spans.push(span(rootId, null, "invoke_agent tracepin", "invoke_agent", 0, 0, { "gen_ai.agent.name": "tracepin" }));
  // per open agent: id and its iteration counter
  let agents = [{ id: rootId, iteration: 0 }];
  let lastTool = "";
  let repeatRun: string[] = [];

  while (spans.length < targetSpans) {
    if (rand() < 0.03 && agents.length < 4) {
      const id = nextId();
      spans.push(span(id, agents[agents.length - 1].id, `invoke_agent ${pick(["researcher", "planner", "critic"])}`, "invoke_agent", clock, clock, {}));
      agents = [...agents, { id, iteration: 0 }];
      continue;
    }
    if (rand() < 0.04 && agents.length > 1) {
      agents = agents.slice(0, -1);
      continue;
    }

    const agent = agents[agents.length - 1];
    const toolCalls = Math.floor(rand() * 4);
    const requested = Array.from({ length: toolCalls }, () => (rand() < 0.03 ? pick(UNKNOWN) : rand() < 0.2 && lastTool ? lastTool : pick(TOOLS)));
    const retries = rand() < 0.03 ? 1 + Math.floor(rand() * 3) : 0;
    const chatDur = 400 + rand() * 1800 + retries * 20_000;
    const chatId = nextId();
    spans.push(span(chatId, agent.id, `chat ${MODEL}`, "chat", clock, clock + chatDur, {
      "tracepin.chat.iteration": agent.iteration++,
      "tracepin.chat.tools_offered": TOOLS,
      "tracepin.chat.transport_retries": retries,
      "tracepin.chat.tool_calls_requested": requested,
      "gen_ai.usage.input_tokens": 300 + Math.floor(rand() * 4000),
      "gen_ai.usage.output_tokens": 20 + Math.floor(rand() * 600),
    }));
    if (retries) findings.push(finding("perf.latency_outlier", "high", [chatId], `chat took ${Math.round(chatDur)} ms`));
    clock += chatDur + 1;

    // ~20% of multi-call turns run their calls concurrently
    const parallel = requested.length > 1 && rand() < 0.2;
    const turnStart = clock;
    let turnEnd = clock;
    for (const tool of requested) {
      if (spans.length >= targetSpans) break;
      if (parallel) clock = turnStart;
      const outcome: ToolOutcome = UNKNOWN.includes(tool) ? "unknown_tool" : rand() < 0.05 ? "invalid_arguments" : "ok";
      const dur = outcome === "ok" ? 1 + rand() * 300 : 0.1;
      const id = nextId();
      spans.push(span(id, agent.id, `execute_tool ${tool}`, "execute_tool", clock, clock + dur, {
        "gen_ai.tool.name": tool,
        "tracepin.tool.outcome": outcome,
        "tracepin.tool.arguments": JSON.stringify({ q: `${tool} #${n}` }),
      }, outcome !== "ok"));
      clock += dur + 1;
      turnEnd = Math.max(turnEnd, clock);

      if (outcome === "unknown_tool") findings.push(finding("tool.unknown_name", "high", [id], `${tool} is not a registered tool`));
      if (outcome === "invalid_arguments") findings.push(finding("args.schema_invalid", "medium", [id], `${tool} arguments rejected`));
      repeatRun = tool === lastTool ? [...repeatRun, id] : [id];
      lastTool = tool;
      if (repeatRun.length === 3) findings.push(finding("loop.near_duplicate", "high", repeatRun, `${tool} called 3x with near-identical arguments`));
    }
    clock = Math.max(clock, turnEnd);
  }

  // Close agent spans around their children (children always come after parents).
  const byId = new Map(spans.map((s) => [s.span_id, s]));
  for (let i = spans.length - 1; i >= 0; i--) {
    const s = spans[i];
    if (!s.parent_span_id) continue;
    const p = byId.get(s.parent_span_id)!;
    if (s.end_ms > p.end_ms) p.end_ms = s.end_ms;
  }
  return { spans, findings };
}
