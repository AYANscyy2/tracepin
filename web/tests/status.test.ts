import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test } from "vitest";
import { buildGraph } from "@/lib/graph";
import type { Analysis, TraceSpans } from "@/lib/types";

// t037 on the v1 run: unknown tool, two rejected search_kb calls, a 72 s chat with two
// transport retries, then four near-duplicate search_kb calls until max_iterations.
const RUN = "9d7035e7b694";
const TRACE = "0x7274597ef22d87765c60774b1055839e";
const data = (rel: string) => JSON.parse(readFileSync(path.join(__dirname, "..", "data", rel), "utf-8"));

test("status and flags on the t037 retry storm come from spans + findings", () => {
  const spans = (data(`traces/${RUN}.json`) as Record<string, TraceSpans>)[TRACE].spans;
  const findings = (data(`findings/${RUN}.json`) as Analysis).findings.filter((f) => f.trace_id === TRACE);
  const g = buildGraph(spans, findings);
  expect(g.nodes).toHaveLength(spans.length);
  const node = (pred: (d: (typeof g.nodes)[number]["data"]) => boolean) => g.nodes.filter((n) => pred(n.data)).map((n) => n.data);

  const failed = node((d) => d.status === "failed");
  expect(failed.map((d) => `${d.label}:${d.outcome}`)).toEqual(["cancel_order:unknown_tool", "search_kb:invalid_arguments", "search_kb:invalid_arguments"]);
  expect(failed.every((d) => d.flagged)).toBe(true);

  const slow = node((d) => d.status === "slow");
  expect(slow.map((d) => d.label)).toEqual(["chat #2"]);
  expect(slow[0].retries).toBe(2);
  expect(slow[0].durationMs).toBeGreaterThan(70_000);
  expect(slow[0].detectors).toContain("perf.latency_outlier");

  // the near-duplicate loop: flagged, but each call itself succeeded
  const loop = node((d) => d.detectors.includes("loop.near_duplicate"));
  expect(loop).toHaveLength(4);
  expect(loop.every((d) => d.status === "ok" && d.flagged && d.outcome === "ok")).toBe(true);

  const root = node((d) => d.kind === "agent")[0];
  expect(root).toMatchObject({ status: "ok", flagged: true });
  expect(root.detectors).toEqual(["error.no_progress"]);

  const first = node((d) => d.label === "chat #0")[0];
  expect(first).toMatchObject({ status: "ok", flagged: false, inputTokens: 347, outputTokens: 23 });
});
