import Link from "next/link";
import { listRuns, loadAnalysis } from "@/lib/data";
import type { Analysis, TraceSummary } from "@/lib/types";

function sig(t: TraceSummary): string {
  const c: Record<string, number> = {};
  t.finding_ids.forEach((d) => (c[d] = (c[d] ?? 0) + 1));
  return Object.entries(c).sort().map(([k, v]) => `${k}×${v}`).join(",");
}

function diff(a: Analysis, b: Analysis) {
  const ta = new Map(a.traces.map((t) => [t.task_id, t]));
  const tb = new Map(b.traces.map((t) => [t.task_id, t]));
  const common = [...ta.keys()].filter((k) => tb.has(k)).sort();
  const pick = (p: (x: TraceSummary, y: TraceSummary) => boolean) => common.filter((k) => p(ta.get(k)!, tb.get(k)!)).map((k) => ({ task: k, a: ta.get(k)!, b: tb.get(k)! }));
  return {
    common,
    regressed: pick((x, y) => x.success && !y.success),
    fixed: pick((x, y) => !x.success && y.success),
    still: pick((x, y) => !x.success && !y.success),
    unchanged: pick((x, y) => x.success && y.success).length,
    detectors: Array.from(new Set([...a.findings, ...b.findings].map((f) => f.detector_id))).map((d) => ({
      d, a: a.findings.filter((f) => f.detector_id === d).length, b: b.findings.filter((f) => f.detector_id === d).length,
    })).sort((x, y) => y.a + y.b - (x.a + x.b)),
    tokens: [common.reduce((s, k) => s + ta.get(k)!.input_tokens, 0), common.reduce((s, k) => s + tb.get(k)!.input_tokens, 0)],
    calls: [common.reduce((s, k) => s + ta.get(k)!.timeline.length, 0), common.reduce((s, k) => s + tb.get(k)!.timeline.length, 0)],
    iters: [common.reduce((s, k) => s + ta.get(k)!.iterations, 0), common.reduce((s, k) => s + tb.get(k)!.iterations, 0)],
  };
}

function Bucket({ title, rows, tone, note }: { title: string; rows: { task: string; a: TraceSummary; b: TraceSummary }[]; tone: string; note?: (a: TraceSummary, b: TraceSummary) => string }) {
  return (
    <section className="mb-4">
      <h3 className={`mb-1 text-sm font-semibold ${tone}`}>{title} <span className="mono font-normal">({rows.length})</span></h3>
      {rows.length === 0 ? <p className="text-ink-3">none</p> : (
        <table className="w-full">
          <thead><tr><th>task</th><th>A</th><th>B</th>{note && <th>note</th>}</tr></thead>
          <tbody>
            {rows.map(({ task, a, b }) => (
              <tr key={task}>
                <td className="mono font-medium">{task}</td>
                <td className="mono text-ink-2"><Link href={`/traces/${a.trace_id}/`} className="underline">{a.stop_reason} {a.iterations}it</Link> {Array.from(new Set(a.finding_ids)).sort().join(", ") || "–"}</td>
                <td className="mono text-ink-2"><Link href={`/traces/${b.trace_id}/`} className="underline">{b.stop_reason} {b.iterations}it</Link> {Array.from(new Set(b.finding_ids)).sort().join(", ") || "–"}</td>
                {note && <td className="text-ink-2">{note(a, b)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function Delta({ a, b, lowerIsBetter = true }: { a: number; b: number; lowerIsBetter?: boolean }) {
  const d = b - a;
  const worse = lowerIsBetter ? d > 0 : d < 0;
  return <span className={`mono tabular-nums ${d === 0 ? "text-ink-3" : worse ? "text-error" : "text-accent"}`}>{a.toLocaleString()} {d === 0 ? "→" : d > 0 ? "▲" : "▼"} {b.toLocaleString()}{a ? ` (${d > 0 ? "+" : ""}${Math.round((100 * d) / a)}%)` : ""}</span>;
}

export default function ComparePage() {
  const runs = listRuns().filter((r) => r.label.endsWith("35lite"));
  const pairs = runs.slice(1).map((r, i) => [runs[i], r] as const);
  return (
    <div>
      <h1 className="mb-1 text-base font-semibold">Compare</h1>
      <p className="mb-4 text-ink-2">Same 44 tasks, same model (<span className="mono">gemini-3.5-flash-lite</span>), one change per step. REGRESSED first, always.</p>
      {pairs.map(([ra, rb]) => {
        const a = loadAnalysis(ra.run_id), b = loadAnalysis(rb.run_id);
        const d = diff(a, b);
        return (
          <div key={rb.run_id} className="mb-8 border-t hairline pt-3">
            <h2 className="mb-2 text-sm font-semibold">
              {ra.label} → {rb.label}
              <span className="mono ml-3 font-normal text-ink-2">A {ra.run_id} prompt={ra.prompt_version} {ra.pass_count}/{ra.task_count} · B {rb.run_id} prompt={rb.prompt_version} {rb.pass_count}/{rb.task_count} · commit {ra.git_sha.slice(0, 8)} → {rb.git_sha.slice(0, 8)}</span>
            </h2>
            <Bucket title="REGRESSED · passed in A, fails in B" rows={d.regressed} tone="text-error" />
            <Bucket title="FIXED · failed in A, passes in B" rows={d.fixed} tone="text-accent" />
            <Bucket title="STILL FAILING" rows={d.still} tone="text-warn" note={(x, y) => (sig(x) === sig(y) ? "same signature" : "signature changed — different bug")} />
            <p className="mb-3 text-ink-3">UNCHANGED PASSES ({d.unchanged})</p>
            <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
              <table>
                <thead><tr><th>detector</th><th className="text-right">A</th><th className="text-right">B</th><th className="text-right">Δ</th></tr></thead>
                <tbody>{d.detectors.map((x) => (
                  <tr key={x.d}><td className="mono">{x.d}</td><td className="text-right tabular-nums">{x.a}</td><td className="text-right tabular-nums">{x.b}</td>
                    <td className={`mono text-right tabular-nums ${x.b - x.a === 0 ? "text-ink-3" : x.b > x.a ? "text-error" : "text-accent"}`}>{x.b - x.a === 0 ? "→ 0" : `${x.b > x.a ? "▲" : "▼"} ${x.b - x.a > 0 ? "+" : ""}${x.b - x.a}`}</td></tr>
                ))}</tbody>
              </table>
              <table>
                <thead><tr><th>metric</th><th>A → B</th></tr></thead>
                <tbody>
                  <tr><td>passed</td><td><Delta a={d.common.filter((k) => a.traces.find((t) => t.task_id === k)!.success).length} b={d.common.filter((k) => b.traces.find((t) => t.task_id === k)!.success).length} lowerIsBetter={false} /></td></tr>
                  <tr><td>input tokens</td><td><Delta a={d.tokens[0]} b={d.tokens[1]} /></td></tr>
                  <tr><td>tool calls</td><td><Delta a={d.calls[0]} b={d.calls[1]} /></td></tr>
                  <tr><td>iterations</td><td><Delta a={d.iters[0]} b={d.iters[1]} /></td></tr>
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
    </div>
  );
}
