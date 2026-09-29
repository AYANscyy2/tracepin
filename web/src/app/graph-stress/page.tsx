"use client";

// Not linked from the site; the README points here. /graph-stress/?n=5000

import { Suspense, useCallback, useMemo, useState, useSyncExternalStore } from "react";
import { useSearchParams } from "next/navigation";
import { GraphView } from "@/components/GraphView";
import { generateSyntheticTrace } from "@/lib/synthetic";

const noHover = () => {};
const noSubscribe = () => () => {};

export default function GraphStressPage() {
  // client-only: the layout time and React Flow's MiniMap differ between server and browser
  const mounted = useSyncExternalStore(noSubscribe, () => true, () => false);
  const fallback = <p className="text-ink-3">Generating…</p>;
  return mounted ? <Suspense fallback={fallback}><Stress /></Suspense> : fallback;
}

function Stress() {
  const raw = Number(useSearchParams().get("n"));
  const n = Number.isFinite(raw) && raw > 0 ? Math.min(Math.floor(raw), 50_000) : 1000;
  const { spans, findings } = useMemo(() => generateSyntheticTrace(n), [n]);
  const [selected, setSelected] = useState<number | null>(null);
  const highlighted = useMemo(() => new Set(selected === null ? [] : findings[selected].span_ids), [selected, findings]);
  const onSelectSpan = useCallback((id: string) => {
    const i = findings.findIndex((f) => f.span_ids.includes(id));
    setSelected(i >= 0 ? i : null);
  }, [findings]);

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h1 className="text-base font-semibold">Graph stress test</h1>
        <span className="mono text-ink-2">{spans.length} synthetic spans · {findings.length} findings · seeded, same n gives the same trace</span>
        <span className="mono text-ink-3">?n= {[250, 1000, 2000, 5000].map((k) => <a key={k} href={`?n=${k}`} className="ml-1 underline">{k}</a>)}</span>
        {selected !== null && (
          <button type="button" className="text-ink-2 underline" onClick={() => setSelected(null)}>clear selection ({findings[selected].detector_id})</button>
        )}
      </div>
      <GraphView spans={spans} findings={findings} highlighted={highlighted} onSelectSpan={onSelectSpan} onHover={noHover} hud />
    </div>
  );
}
