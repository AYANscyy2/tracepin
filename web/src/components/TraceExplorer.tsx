"use client";

import { useMemo, useState } from "react";
import { Waterfall } from "./Waterfall";
import { FindingsList } from "./FindingsList";
import { CodePanel } from "./CodePanel";
import type { Finding, LocationRank, Span, TraceSummary } from "@/lib/types";

export interface Gap { start_ms: number; end_ms: number; kind: string; span_id: string }

export function TraceExplorer({ summary, spans, findings, code, sha }: {
  summary: TraceSummary; spans: Span[]; findings: Finding[]; code: Record<string, LocationRank>; sha: string;
}) {
  const [selected, setSelected] = useState<number | null>(findings.length ? 0 : null);
  const [hoverSpan, setHoverSpan] = useState<string | null>(null);

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

  const sel = selected === null ? null : findings[selected];
  const loc = sel?.code_location ? code[`${sel.code_location.file}:${sel.code_location.line}`] ?? null : null;

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
      <div className="min-w-0">
        <Waterfall spans={spans} highlighted={highlighted} flagged={flagged} gaps={gaps}
          onHover={setHoverSpan} onSelectSpan={(id) => {
            const i = findings.findIndex((f) => f.span_ids.includes(id));
            if (i >= 0) setSelected(i);
          }} />
        <div className="mt-3">
          <CodePanel finding={sel} location={loc} sha={sha} />
        </div>
      </div>
      <FindingsList findings={findings} selected={selected} onSelect={setSelected} hoverSpan={hoverSpan} />
    </div>
  );
}
