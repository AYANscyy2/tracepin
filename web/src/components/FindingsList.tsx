"use client";

import { Sev } from "./Sev";
import type { Finding } from "@/lib/types";

export function FindingsList({ findings, selected, onSelect, hoverSpan }: {
  findings: Finding[]; selected: number | null; onSelect: (i: number) => void; hoverSpan: string | null;
}) {
  if (!findings.length) return <aside className="border hairline bg-panel p-3 text-ink-3">No findings on this trace.</aside>;
  return (
    <aside className="border hairline bg-panel">
      <div className="border-b hairline px-3 py-1 text-xs text-ink-2">{findings.length} finding(s) · select one to highlight its spans</div>
      <ul role="listbox" aria-label="findings">
        {findings.map((f, i) => {
          const active = i === selected;
          const touched = hoverSpan !== null && f.span_ids.includes(hoverSpan);
          return (
            <li key={i} role="option" aria-selected={active}>
              <button type="button" onClick={() => onSelect(i)}
                className={`block w-full border-b hairline px-3 py-2 text-left transition-colors ${active ? "bg-accent-soft/60" : touched ? "bg-bg" : "hover:bg-bg"}`}>
                <div className="flex items-baseline gap-2">
                  <Sev s={f.severity} />
                  <span className="mono text-ink-2">{f.detector_id}</span>
                  <span className="mono ml-auto text-[11px] text-ink-3">conf {f.confidence.toFixed(2)} · {f.span_ids.length} span{f.span_ids.length === 1 ? "" : "s"}</span>
                </div>
                <div className="mt-0.5 font-medium">{f.title}</div>
                {active && (
                  <>
                    <p className="mt-1 text-ink-2">{f.detail}</p>
                    {f.suggested_fix && <p className="mt-1 text-ink-2"><span className="text-ink-3">fix category · </span>{f.suggested_fix}</p>}
                    {f.code_location && <p className="mono mt-1 text-[11px] text-ink-3">{f.code_location.file}:{f.code_location.line}{f.code_location.inferred_from_tool ? " (inferred from tool)" : ""}</p>}
                  </>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
