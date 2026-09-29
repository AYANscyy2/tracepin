// Flow layout for the Graph view. Pure data, no React: the tests run it in node.
//
// Reads like a workflow canvas: a span's children run top to bottom in execution order,
// and a child with its own children (a sub-agent) branches off to the right. Under
// invoke_agent the rows follow the agent loop: each chat is a row, and the tool calls that
// turn requested sit side by side in the row below it. Generic layered layout (ELK) took
// ~36 s at 1k spans and produced 100k+ px-wide rows, because agent runs are long sequences,
// not balanced trees; this is one O(n log n) pass (the sort dominates).

import type { Finding, Span } from "./types";

export type NodeStatus = "ok" | "slow" | "failed";
export type NodeKind = "agent" | "chat" | "tool" | "other";

export const NODE_WIDTH = 220;
export const NODE_HEIGHT = 58;
/** Vertical gap between consecutive rows. */
export const GAP_Y = 32;
/** Horizontal gap between spans in the same row. */
export const GAP_X = 20;
/** Children start this far right of their parent, so the parent's sequence edge never crosses them. */
export const INDENT_X = NODE_WIDTH / 2 + 40;

export interface GraphNodeData extends Record<string, unknown> {
  span: Span;
  kind: NodeKind;
  label: string;
  status: NodeStatus;
  flagged: boolean;
  detectors: string[];
  durationMs: number;
  inputTokens: number | null;
  outputTokens: number | null;
  retries: number;
  outcome: string | null;
}

export interface GraphNode {
  id: string;
  type: "span";
  position: { x: number; y: number };
  width: number;
  height: number;
  data: GraphNodeData;
}

export type EdgeKind = "seq" | "branch";

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle: "b" | "r";
  targetHandle: "t";
  /** Bezier for step-to-step flow (fan-out/in curves cleanly); smoothstep into a child's first row. */
  type: "default" | "smoothstep";
  className: string;
}

export interface TraceGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  spanById: Map<string, Span>;
  layoutMs: number;
}

const attr = <T,>(s: Span, k: string) => s.attributes[k] as T | undefined;

export function toolOutcome(s: Span): string | null {
  return attr<string>(s, "tracepin.tool.outcome") ?? null;
}

/** failed: ERROR status or a tool outcome other than ok. slow: a perf.* finding points at it. */
export function nodeStatus(s: Span, findings: Finding[] | undefined): NodeStatus {
  const oc = toolOutcome(s);
  if (s.status_code === "ERROR" || (oc !== null && oc !== "ok")) return "failed";
  if (findings?.some((f) => f.detector_id.startsWith("perf."))) return "slow";
  return "ok";
}

function kindOf(s: Span): NodeKind {
  if (s.operation === "invoke_agent") return "agent";
  if (s.operation === "chat") return "chat";
  if (s.operation === "execute_tool") return "tool";
  return "other";
}

function labelOf(s: Span, kind: NodeKind): string {
  if (kind === "tool") return attr<string>(s, "gen_ai.tool.name") ?? s.name.replace(/^execute_tool /, "");
  if (kind === "chat") {
    const it = attr<number>(s, "tracepin.chat.iteration");
    return it === undefined ? s.name : `chat #${it}`;
  }
  if (kind === "agent") return s.name.replace(/^invoke_agent /, "");
  return s.name;
}

/**
 * Group time-ordered siblings into rows. A sibling that starts before the current row has
 * finished ran in parallel with it, so it joins that row; otherwise it opens the next row.
 */
export function groupRows(children: Span[]): Span[][] {
  const rows: Span[][] = [];
  let row: Span[] = [];
  let rowEnd = Number.NEGATIVE_INFINITY;
  for (const c of children) {
    if (row.length > 0 && c.start_ms < rowEnd) {
      row.push(c);
      rowEnd = Math.max(rowEnd, c.end_ms);
    } else {
      if (row.length) rows.push(row);
      row = [c];
      rowEnd = c.end_ms;
    }
  }
  if (row.length) rows.push(row);
  return rows;
}

/**
 * Rows for an agent's children: each chat is a row, and the execute_tool spans between it and
 * the next chat (the calls that turn requested) form the row below it. Anything else, including
 * tools before the first chat, falls back to time-overlap grouping.
 */
export function agentRows(children: Span[]): Span[][] {
  const rows: Span[][] = [];
  let other: Span[] = [];
  let tools: Span[] | null = null;
  const flushTools = () => {
    if (tools?.length) rows.push(tools);
    tools = null;
  };
  const flushOther = () => {
    if (other.length) rows.push(...groupRows(other));
    other = [];
  };
  for (const c of children) {
    if (c.operation === "chat") {
      flushTools();
      flushOther();
      rows.push([c]);
      tools = [];
    } else if (c.operation === "execute_tool" && tools) {
      tools.push(c);
    } else {
      flushTools();
      other.push(c);
    }
  }
  flushTools();
  flushOther();
  return rows;
}

export function buildGraph(input: Span[], findings: Finding[]): TraceGraph {
  const t0 = performance.now();
  // Ties break longest-first so a parent sorts ahead of a child that starts on the same ms.
  const spans = [...input].sort((a, b) => a.start_ms - b.start_ms || b.end_ms - a.end_ms);
  const spanById = new Map<string, Span>();
  for (const s of spans) spanById.set(s.span_id, s);
  const children = new Map<string, Span[]>();
  const roots: Span[] = [];
  for (const s of spans) {
    // a span whose parent wasn't exported is drawn as a root rather than dropped
    if (s.parent_span_id && spanById.has(s.parent_span_id)) {
      const list = children.get(s.parent_span_id);
      if (list) list.push(s);
      else children.set(s.parent_span_id, [s]);
    } else {
      roots.push(s);
    }
  }

  const findingsBySpan = new Map<string, Finding[]>();
  for (const f of findings) {
    for (const id of f.span_ids) {
      const list = findingsBySpan.get(id);
      if (list) list.push(f);
      else findingsBySpan.set(id, [f]);
    }
  }

  const rowsOf = new Map<string, Span[][]>();
  const rows = (s: Span) => {
    let r = rowsOf.get(s.span_id);
    if (!r) {
      const cs = children.get(s.span_id) ?? [];
      r = s.operation === "invoke_agent" ? agentRows(cs) : groupRows(cs);
      rowsOf.set(s.span_id, r);
    }
    return r;
  };

  // Pass 1: subtree widths. Reversed pre-order visits children before parents.
  const preOrder: Span[] = [];
  const dfs: Span[] = [...roots];
  while (dfs.length) {
    const s = dfs.pop()!;
    preOrder.push(s);
    for (const c of children.get(s.span_id) ?? []) dfs.push(c);
  }
  const width = new Map<string, number>();
  for (let i = preOrder.length - 1; i >= 0; i--) {
    const s = preOrder[i];
    let w = NODE_WIDTH;
    for (const row of rows(s)) {
      let rw = 0;
      for (const c of row) rw += width.get(c.span_id)! + GAP_X;
      w = Math.max(w, INDENT_X + rw - GAP_X);
    }
    width.set(s.span_id, w);
  }

  const status = new Map<string, NodeStatus>();
  for (const s of spans) status.set(s.span_id, nodeStatus(s, findingsBySpan.get(s.span_id)));

  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  const addEdge = (source: string, target: string, kind: EdgeKind) => {
    const failed = status.get(target) === "failed";
    edges.push({
      id: `${kind}:${source}->${target}`,
      source,
      target,
      sourceHandle: kind === "seq" ? "b" : "r",
      targetHandle: "t",
      type: kind === "seq" ? "default" : "smoothstep",
      className: `edge-${kind}${failed ? " edge-failed" : ""}`,
    });
  };

  const place = (s: Span, x: number, y: number) => {
    const kind = kindOf(s);
    const fs = findingsBySpan.get(s.span_id);
    nodes.push({
      id: s.span_id,
      type: "span",
      position: { x, y },
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      data: {
        span: s,
        kind,
        label: labelOf(s, kind),
        status: status.get(s.span_id)!,
        flagged: !!fs,
        detectors: fs ? Array.from(new Set(fs.map((f) => f.detector_id))) : [],
        durationMs: s.end_ms - s.start_ms,
        inputTokens: attr<number>(s, "gen_ai.usage.input_tokens") ?? null,
        outputTokens: attr<number>(s, "gen_ai.usage.output_tokens") ?? null,
        retries: attr<number>(s, "tracepin.chat.transport_retries") ?? 0,
        outcome: toolOutcome(s),
      },
    });
  };

  // Pass 2: place top-down with an explicit stack (deep traces would blow the call stack).
  type Frame = { span: Span; x: number; rowIdx: number; cursorY: number; prevRow: Span[] | null; bottom: number; colX: number; itemIdx: number; rowBottom: number };
  const layoutSubtree = (root: Span, x: number, y: number): number => {
    place(root, x, y);
    const stack: Frame[] = [{ span: root, x, rowIdx: 0, cursorY: y + NODE_HEIGHT + GAP_Y, prevRow: null, bottom: y + NODE_HEIGHT, colX: 0, itemIdx: 0, rowBottom: 0 }];
    let lastBottom = y + NODE_HEIGHT;

    while (stack.length) {
      const f = stack[stack.length - 1];
      const r = rows(f.span);
      if (f.rowIdx >= r.length) {
        stack.pop();
        lastBottom = f.bottom;
        const parent = stack[stack.length - 1];
        if (parent) parent.rowBottom = Math.max(parent.rowBottom, f.bottom);
        continue;
      }
      const row = r[f.rowIdx];
      if (f.itemIdx === 0 && f.rowBottom === 0) {
        f.colX = f.x + INDENT_X;
        f.rowBottom = f.cursorY + NODE_HEIGHT;
        // Edges into this row. One-to-many and many-to-one connect every pair, which is
        // exactly chat -> each tool call -> next chat under an agent.
        if (!f.prevRow) {
          for (const c of row) addEdge(f.span.span_id, c.span_id, "branch");
        } else if (f.prevRow.length === 1 || row.length === 1) {
          for (const a of f.prevRow) for (const b of row) addEdge(a.span_id, b.span_id, "seq");
        } else {
          for (let i = 0; i < row.length; i++) addEdge(f.prevRow[Math.min(i, f.prevRow.length - 1)].span_id, row[i].span_id, "seq");
        }
      }
      if (f.itemIdx < row.length) {
        const c = row[f.itemIdx];
        const cx = f.colX;
        f.colX += width.get(c.span_id)! + GAP_X;
        f.itemIdx++;
        place(c, cx, f.cursorY);
        if (children.has(c.span_id)) {
          stack.push({ span: c, x: cx, rowIdx: 0, cursorY: f.cursorY + NODE_HEIGHT + GAP_Y, prevRow: null, bottom: f.cursorY + NODE_HEIGHT, colX: 0, itemIdx: 0, rowBottom: 0 });
        }
        continue;
      }
      // Row finished.
      f.bottom = Math.max(f.bottom, f.rowBottom);
      f.cursorY = f.rowBottom + GAP_Y;
      f.prevRow = row;
      f.rowIdx++;
      f.itemIdx = 0;
      f.rowBottom = 0;
    }
    return lastBottom;
  };

  let y = 0;
  for (const root of roots) y = layoutSubtree(root, 0, y) + GAP_Y * 2;

  return { nodes, edges, spanById, layoutMs: performance.now() - t0 };
}
