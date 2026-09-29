import { describe, expect, test } from "vitest";
import { agentRows, buildGraph, groupRows, NODE_HEIGHT, NODE_WIDTH, type GraphNode } from "@/lib/graph";
import { generateSyntheticTrace } from "@/lib/synthetic";
import type { Span } from "@/lib/types";

const span = (id: string, operation: string, start: number, end: number, parent: string | null = "agent"): Span => ({
  span_id: id, parent_span_id: parent, name: `${operation} ${id}`, operation, start_ms: start, end_ms: end,
  status_code: "UNSET", status_message: null, attributes: {}, events: [],
});

function overlaps(nodes: GraphNode[]): string[] {
  const sorted = [...nodes].sort((a, b) => a.position.y - b.position.y || a.position.x - b.position.x);
  const bad: string[] = [];
  for (let i = 0; i < sorted.length; i++) {
    for (let j = i + 1; j < sorted.length && sorted[j].position.y < sorted[i].position.y + NODE_HEIGHT; j++) {
      const a = sorted[i].position, b = sorted[j].position;
      if (Math.abs(a.x - b.x) < NODE_WIDTH && Math.abs(a.y - b.y) < NODE_HEIGHT) bad.push(`${sorted[i].id}/${sorted[j].id}`);
    }
  }
  return bad;
}

describe("layout", () => {
  test.each([50, 1000, 5000])("%i synthetic spans: every span placed, no overlaps, under 250 ms", (size) => {
    const { spans, findings } = generateSyntheticTrace(size);
    expect(spans).toHaveLength(size);
    buildGraph(spans, findings); // warm the JIT so the timing measures the layout, not compilation
    const g = buildGraph(spans, findings);
    console.log(`layout ${size} spans: ${g.layoutMs.toFixed(1)} ms`);
    expect(g.nodes).toHaveLength(size);
    expect(new Set(g.nodes.map((n) => n.id)).size).toBe(size);
    expect(overlaps(g.nodes)).toEqual([]);
    expect(g.layoutMs).toBeLessThan(250);
  });

  test("the synthetic trace exercises sub-agents, parallel calls and every status", () => {
    const { spans, findings } = generateSyntheticTrace(1000);
    const g = buildGraph(spans, findings);
    expect(spans.filter((s) => s.operation === "invoke_agent").length).toBeGreaterThan(1);
    expect(new Set(g.nodes.map((n) => n.data.status))).toEqual(new Set(["ok", "slow", "failed"]));
    const ys = new Map<number, number>();
    for (const n of g.nodes.filter((n) => n.data.kind === "tool")) ys.set(n.position.y, (ys.get(n.position.y) ?? 0) + 1);
    expect(Math.max(...ys.values())).toBeGreaterThan(1);
  });

  test("a span whose parent is missing is placed as a root, not dropped", () => {
    const g = buildGraph([span("a", "chat", 0, 5, "gone")], []);
    expect(g.nodes.map((n) => n.id)).toEqual(["a"]);
  });
});

describe("agent-aware rows", () => {
  // one agent, three turns: two tool calls, none, one
  const spans = [
    span("agent", "invoke_agent", 0, 100, null),
    span("c0", "chat", 1, 10),
    span("t0a", "execute_tool", 11, 12),
    span("t0b", "execute_tool", 13, 14),
    span("c1", "chat", 15, 20),
    span("c2", "chat", 21, 30),
    span("t2a", "execute_tool", 31, 32),
  ];

  test("each chat is a row and the tool calls of its turn share the next row, even when sequential in time", () => {
    const children = spans.slice(1);
    expect(agentRows(children).map((r) => r.map((s) => s.span_id))).toEqual([["c0"], ["t0a", "t0b"], ["c1"], ["c2"], ["t2a"]]);
    // time-overlap grouping alone would have put t0a and t0b on separate rows
    expect(groupRows(children.slice(1, 3))).toHaveLength(2);

    const pos = new Map(buildGraph(spans, []).nodes.map((n) => [n.id, n.position]));
    expect(pos.get("t0a")!.y).toBe(pos.get("t0b")!.y);
    expect(pos.get("t0b")!.x).toBeGreaterThanOrEqual(pos.get("t0a")!.x + NODE_WIDTH);
    expect(pos.get("t0a")!.y).toBeGreaterThan(pos.get("c0")!.y);
    expect(pos.get("c1")!.y).toBeGreaterThan(pos.get("t0a")!.y);
    // the agent's rows sit right of it so its branch edge never crosses them
    expect(pos.get("c0")!.x).toBeGreaterThan(pos.get("agent")!.x + NODE_WIDTH / 2);
  });

  test("edges run agent -> first chat, chat -> each tool call -> next chat", () => {
    const edges = buildGraph(spans, []).edges.map((e) => `${e.source}->${e.target}`).sort();
    expect(edges).toEqual([
      "agent->c0",
      "c0->t0a", "c0->t0b",
      "c1->c2",
      "c2->t2a",
      "t0a->c1", "t0b->c1",
    ]);
  });

  test("spans that aren't chat/tool fall back to time-overlap grouping", () => {
    const rows = agentRows([span("x", "retrieve", 0, 10), span("y", "retrieve", 5, 12), span("c", "chat", 13, 20), span("z", "retrieve", 21, 22)]);
    expect(rows.map((r) => r.map((s) => s.span_id))).toEqual([["x", "y"], ["c"], ["z"]]);
  });

  test("non-agent parents group overlapping children into one row", () => {
    const rows = groupRows([span("a", "x", 0, 10), span("b", "x", 12, 50), span("c", "x", 13, 20), span("d", "x", 60, 70)]);
    expect(rows.map((r) => r.map((s) => s.span_id))).toEqual([["a"], ["b", "c"], ["d"]]);
  });
});
