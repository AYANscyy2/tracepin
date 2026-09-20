"use client";

import { useState } from "react";
import type { Finding, Span } from "@/lib/types";
import type { Gap } from "./TraceExplorer";
import { fmtMs } from "@/lib/fmt";

const ROW = 22;
const LABEL_W = 300;

function depthOf(s: Span, byId: Map<string, Span>): number {
  let d = 0, cur = s;
  while (cur.parent_span_id && byId.has(cur.parent_span_id)) { cur = byId.get(cur.parent_span_id)!; d++; }
  return d;
}

function outcome(s: Span): string | null {
  return (s.attributes["tracepin.tool.outcome"] as string | undefined) ?? null;
}

const HIDE_ATTRS = new Set(["tracepin.task.prompt", "tracepin.task.expected"]);

export function Waterfall({ spans, highlighted, flagged, gaps, onHover, onSelectSpan }: {
  spans: Span[]; highlighted: Set<string>; flagged: Map<string, Finding[]>; gaps: Gap[];
  onHover: (id: string | null) => void; onSelectSpan: (id: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const byId = new Map(spans.map((s) => [s.span_id, s]));
  const total = Math.max(...spans.map((s) => s.end_ms), 1);
  const rows = [...spans].sort((a, b) => a.start_ms - b.start_ms || b.end_ms - a.end_ms);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * total);

  return (
    <div className="border hairline bg-panel">
      <div className="flex items-baseline justify-between border-b hairline px-2 py-1 text-ink-2">
        <span className="text-xs">{spans.length} spans · {fmtMs(total)} · click a bar to select its finding, hover for attributes</span>
        <span className="mono text-xs"><span className="inline-block h-2 w-3 bg-accent align-middle" /> span <span className="ml-2 inline-block h-2 w-3 bg-error align-middle" /> error <span className="ml-2 inline-block h-2 w-3 hatch align-middle" /> unaccounted</span>
      </div>
      <div className="relative overflow-x-auto">
        {/* time axis */}
        <div className="relative h-5 border-b hairline" style={{ marginLeft: LABEL_W }}>
          {ticks.map((t) => (
            <span key={t} className="mono absolute top-0 text-[10px] text-ink-3" style={{ left: `${(100 * t) / total}%`, transform: "translateX(-50%)" }}>{fmtMs(t)}</span>
          ))}
        </div>
        {rows.map((s) => {
          const depth = depthOf(s, byId);
          const left = (100 * s.start_ms) / total;
          const width = Math.max((100 * (s.end_ms - s.start_ms)) / total, 0.15);
          const isErr = s.status_code === "ERROR";
          const hi = highlighted.has(s.span_id);
          const dim = highlighted.size > 0 && !hi;
          const fs = flagged.get(s.span_id) ?? [];
          const oc = outcome(s);
          const isOpen = open === s.span_id;
          const rowGaps = gaps.filter((g) => g.span_id === s.span_id);
          return (
            <div key={s.span_id}>
              <div className={`group flex items-center border-b hairline ${hi ? "bg-accent-soft/60" : ""}`} style={{ height: ROW }}
                onMouseEnter={() => onHover(s.span_id)} onMouseLeave={() => onHover(null)}>
                <button type="button" onClick={() => setOpen(isOpen ? null : s.span_id)}
                  className="mono flex shrink-0 items-center gap-1 truncate text-left text-[12px]" style={{ width: LABEL_W, paddingLeft: 8 + depth * 14 }}
                  aria-expanded={isOpen} title={s.name}>
                  <span className="text-ink-3">{isOpen ? "▾" : "▸"}</span>
                  <span className={`truncate ${isErr ? "text-error" : ""}`}>{s.name.replace(/^execute_tool /, "⚙ ").replace(/^chat /, "✉ ").replace(/^invoke_agent /, "▶ ")}</span>
                  {oc && oc !== "ok" && <span className="ml-1 shrink-0 text-[10px] text-error">{oc}</span>}
                </button>
                <div className="relative h-full flex-1 overflow-hidden">
                  <button type="button" onClick={() => onSelectSpan(s.span_id)} aria-label={`${s.name}, ${fmtMs(s.end_ms - s.start_ms)}${fs.length ? `, ${fs.length} finding(s)` : ""}`}
                    className={`absolute top-[6px] h-[10px] transition-opacity ${isErr ? "bg-error" : "bg-accent"} ${dim ? "opacity-30" : ""} ${hi ? "ring-2 ring-ink ring-offset-1" : ""}`}
                    style={{ left: `${left}%`, width: `${width}%`, minWidth: 2 }} />
                  {rowGaps.map((g, i) => (
                    <div key={i} className="hatch pointer-events-none absolute top-[2px] h-[18px] bg-panel/60" title={`${g.kind}: ${fmtMs(g.end_ms - g.start_ms)} unaccounted`}
                      style={{ left: `${(100 * g.start_ms) / total}%`, width: `${Math.max((100 * (g.end_ms - g.start_ms)) / total, 0.15)}%` }} />
                  ))}
                  {/* annotation goes after the bar unless there is more room before it */}
                  <span className={`mono pointer-events-none absolute top-[3px] whitespace-nowrap text-[10px] text-ink-2 ${dim ? "opacity-40" : ""}`}
                    style={left > 100 - (left + width) ? { right: `calc(${100 - left}% + 6px)` } : { left: `calc(${left + width}% + 6px)` }}>
                    {fmtMs(s.end_ms - s.start_ms)}{fs.length ? <span className="ml-1 text-warn">◀ {Array.from(new Set(fs.map((f) => f.detector_id))).join(", ")}</span> : null}
                  </span>
                </div>
              </div>
              {isOpen && (
                <div className="border-b hairline bg-bg px-3 py-2">
                  <dl className="mono grid grid-cols-[max-content_1fr] gap-x-4 gap-y-0.5 text-[11px]">
                    <dt className="text-ink-3">span_id</dt><dd>{s.span_id}</dd>
                    <dt className="text-ink-3">status</dt><dd className={isErr ? "text-error" : ""}>{s.status_code}{s.status_message ? ` — ${s.status_message}` : ""}</dd>
                    {Object.entries(s.attributes).filter(([k]) => !HIDE_ATTRS.has(k)).map(([k, v]) => (
                      <><dt key={k + "k"} className="text-ink-3">{k}</dt><dd key={k + "v"} className="break-all whitespace-pre-wrap">{typeof v === "string" ? v : JSON.stringify(v)}</dd></>
                    ))}
                    {s.events.map((e, i) => (
                      <><dt key={i + "ek"} className="text-warn">event {e.name}</dt><dd key={i + "ev"} className="break-all">{e.at_ms !== null ? `@${fmtMs(e.at_ms)} ` : ""}{JSON.stringify(e.attributes)}</dd></>
                    ))}
                  </dl>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
