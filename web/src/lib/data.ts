import fs from "node:fs";
import path from "node:path";
import type { Analysis, LocationRank, RunIndexEntry, TraceSpans } from "./types";

const DATA = path.join(process.cwd(), "data");

function readJson<T>(rel: string): T {
  return JSON.parse(fs.readFileSync(path.join(DATA, rel), "utf-8")) as T;
}

export function listRuns(): RunIndexEntry[] {
  return readJson<RunIndexEntry[]>("runs.json");
}

export function loadAnalysis(runId: string): Analysis {
  return readJson<Analysis>(`findings/${runId}.json`);
}

export function loadTraces(runId: string): Record<string, TraceSpans> {
  return readJson<Record<string, TraceSpans>>(`traces/${runId}.json`);
}

export function loadCode(runId: string): Record<string, LocationRank> {
  return readJson<Record<string, LocationRank>>(`code/${runId}.json`);
}

/** Every (run, trace) pair, for static params and for resolving a trace id to its run. */
export function allTraceIds(): { runId: string; traceId: string }[] {
  return listRuns().flatMap((r) => loadAnalysis(r.run_id).traces.map((t) => ({ runId: r.run_id, traceId: t.trace_id })));
}

export function findRunForTrace(traceId: string): string | null {
  return allTraceIds().find((x) => x.traceId === traceId)?.runId ?? null;
}
export { shortId, fmtMs, pct } from "./fmt";
