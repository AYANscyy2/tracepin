"use client";

import { useCallback, useMemo, useState, useSyncExternalStore } from "react";
import { Waterfall } from "./Waterfall";
import { GraphView } from "./GraphView";
import { FindingsList } from "./FindingsList";
import { CodePanel } from "./CodePanel";
import type { Finding, LocationRank, Span, TraceSummary } from "@/lib/types";

export interface Gap { start_ms: number; end_ms: number; kind: string; span_id: string }

type View = "waterfall" | "graph";

// The view lives in the URL hash (#graph), so a link or a reload keeps it. Read through
// useSyncExternalStore: the server renders the waterfall, the client picks up the hash.
const subscribeHash = (cb: () => void) => {
  window.addEventListener("hashchange", cb);
  return () => window.removeEventListener("hashchange", cb);
};
const hashView = (): View => (window.location.hash === "#graph" ? "graph" : "waterfall");
function setHashView(v: View) {
  const url = new URL(window.location.href);
  url.hash = v === "graph" ? "graph" : "";
  history.replaceState(history.state, "", url);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

export function TraceExplorer({ summary, spans, findings, code, sha }: {
  summary: TraceSummary; spans: Span[]; findings: Finding[]; code: Record<string, LocationRank>; sha: string;
}) {
  const [selected, setSelected] = useState<number | null>(findings.length ? 0 : null);
  const [hoverSpan, setHoverSpan] = useState<string | null>(null);
  const view = useSyncExternalStore(subscribeHash, hashView, () => "waterfall" as const);

  const highlighted = useMemo(() => new Set(selected === null ? [] : findings[selected].span_ids), [selected, findings]);
  const flagged = useMemo(() => {
    const m = new Map<string, Finding[]>();
    findings.forEach((f) => f.span_ids.forEach((id) => m.set(id, [...(m.get(id) ?? []), f])));
    return m;
  }, [findings]);

  // unaccounted-time holes come straight out of the finding's evidence
  const gaps = useMemo<Gap[]>(
    () => findings.filter((f) => f.detector_id === "perf.unaccounted_time").flatMap((f) => (f.evidence.holes_ms as Gap[] | undefined) ?? []),
    [findings],
  );

  // stable, so hover re-renders don't reach the graph canvas
  const onSelectSpan = useCallback((id: string) => {
    const i = findings.findIndex((f) => f.span_ids.includes(id));
    if (i >= 0) setSelected(i);
  }, [findings]);

  const sel = selected === null ? null : findings[selected];
  const loc = sel?.code_location ? code[`${sel.code_location.file}:${sel.code_location.line}`] ?? null : null;

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
      <div className="min-w-0">
        <div role="tablist" aria-label="trace view" className="mb-[-1px] flex text-xs">
          {(["waterfall", "graph"] as const).map((v) => (
            <button key={v} type="button" role="tab" aria-selected={view === v} onClick={() => setHashView(v)}
              className={`border hairline px-3 py-1 capitalize ${view === v ? "border-b-panel bg-panel font-medium" : "bg-bg text-ink-2 hover:text-ink"} ${v === "graph" ? "ml-[-1px]" : ""}`}>
              {v}
            </button>
          ))}
        </div>
        {view === "graph" ? (
          <GraphView spans={spans} findings={findings} highlighted={highlighted} onSelectSpan={onSelectSpan} onHover={setHoverSpan} />
        ) : (
          <Waterfall spans={spans} highlighted={highlighted} flagged={flagged} gaps={gaps} onHover={setHoverSpan} onSelectSpan={onSelectSpan} />
        )}
        <div className="mt-3">
          <CodePanel finding={sel} location={loc} sha={sha} />
        </div>
      </div>
      <FindingsList findings={findings} selected={selected} onSelect={setSelected} hoverSpan={hoverSpan} />
    </div>
  );
}
