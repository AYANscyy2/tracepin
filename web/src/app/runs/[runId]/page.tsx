import Link from "next/link";
import { Sev } from "@/components/Sev";
import { fmtMs, listRuns, loadAnalysis, loadCode, pct, shortId } from "@/lib/data";
import type { Severity } from "@/lib/types";

export function generateStaticParams() {
  return listRuns().map((r) => ({ runId: r.run_id }));
}

const ORDER: Record<Severity, number> = { high: 0, medium: 1, low: 2 };

export default async function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  const a = loadAnalysis(runId);
  const code = Object.values(loadCode(runId)).sort((x, y) => y.tasks.length - x.tasks.length);
  const label = listRuns().find((r) => r.run_id === runId)?.label ?? runId;

  const byDetector = Object.entries(a.summary.by_detector).map(([id, n]) => {
    const fs = a.findings.filter((f) => f.detector_id === id);
    const worst = fs.reduce<Severity>((w, f) => (ORDER[f.severity] < ORDER[w] ? f.severity : w), "low");
    const tasks = Array.from(new Set(fs.map((f) => f.task_id))).sort();
    return { id, n, worst, tasks };
  }).sort((x, y) => ORDER[x.worst] - ORDER[y.worst] || y.n - x.n);

  const runLevel = a.findings.filter((f) => f.scope === "run");

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <h1 className="text-base font-semibold">{label} <span className="mono font-normal text-ink-3">{a.run.run_id}</span></h1>
        <span className="mono text-ink-2">model={a.run.model} prompt={a.run.prompt_version} commit={a.run.git_sha.slice(0, 10)}</span>
        <span className="tabular-nums">{a.run.pass_count}/{a.run.task_count} passed ({pct(a.run.pass_count, a.run.task_count)})</span>
        <span className="tabular-nums text-ink-2">{a.summary.total} findings · {a.summary.by_severity.high ?? 0} high</span>
        <span className="tabular-nums text-ink-2">{a.run.total_input_tokens.toLocaleString()} input tokens · {fmtMs(a.run.total_wall_ms)} wall</span>
      </div>

      {runLevel.map((f) => (
        <div key={f.detector_id + f.title} className="mb-4 border-l-2 border-error bg-panel px-3 py-2">
          <div className="flex items-baseline gap-2"><Sev s={f.severity} /><span className="mono text-ink-2">{f.detector_id}</span><span className="font-medium">{f.title}</span></div>
          <p className="mt-1 text-ink-2">{f.detail}</p>
          {f.code_location && <p className="mono mt-1 text-ink-3">{f.code_location.file}:{f.code_location.line}</p>}
        </div>
      ))}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_1fr]">
        <section>
          <h2 className="mb-1 text-sm font-semibold">Findings by detector</h2>
          <table className="w-full">
            <thead><tr><th>detector</th><th className="text-right">count</th><th>worst</th><th>tasks</th></tr></thead>
            <tbody>
              {byDetector.map((d) => (
                <tr key={d.id}>
                  <td className="mono">{d.id}</td>
                  <td className="text-right tabular-nums">{d.n}</td>
                  <td><Sev s={d.worst} /></td>
                  <td className="mono text-ink-2">{d.tasks.slice(0, 8).join(" ")}{d.tasks.length > 8 ? ` +${d.tasks.length - 8}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section>
          <h2 className="mb-1 text-sm font-semibold">Locations ranked by tasks affected</h2>
          <table className="w-full">
            <thead><tr><th>location</th><th className="text-right">findings</th><th className="text-right">tasks</th><th>blame</th></tr></thead>
            <tbody>
              {code.map((c) => (
                <tr key={c.file + c.line}>
                  <td className="mono">{c.file}:{c.line} <span className="text-ink-3">{c.function}</span></td>
                  <td className="text-right tabular-nums">{c.findings}</td>
                  <td className="text-right tabular-nums font-medium">{c.tasks.length}</td>
                  <td className="mono text-ink-2">{c.context?.blame ? `${c.context.blame.commit.slice(0, 8)} ${c.context.blame.authored_at.slice(0, 10)} “${c.context.blame.subject.slice(0, 40)}”` : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

      <section className="mt-6">
        <h2 className="mb-1 text-sm font-semibold">Tasks</h2>
        <table className="w-full">
          <thead><tr><th>task</th><th>prompt</th><th>result</th><th>stop</th><th className="text-right">iters</th><th className="text-right">calls</th><th className="text-right">tokens</th><th className="text-right">wall</th><th>findings</th></tr></thead>
          <tbody>
            {a.traces.map((t) => {
              const fs = a.findings.filter((f) => f.trace_id === t.trace_id);
              const high = fs.filter((f) => f.severity === "high").length;
              return (
                <tr key={t.trace_id}>
                  <td className="mono"><Link href={`/traces/${t.trace_id}/`} className="font-medium">{t.task_id}</Link> <span className="text-ink-3">{shortId(t.trace_id)}</span></td>
                  <td className="max-w-[420px] truncate text-ink-2" title={t.task_prompt}>{t.task_prompt}</td>
                  <td className={t.success ? "text-accent" : "font-medium text-error"}>{t.success ? "pass" : "fail"}</td>
                  <td className="mono text-ink-2">{t.stop_reason}</td>
                  <td className="text-right tabular-nums">{t.iterations}</td>
                  <td className="text-right tabular-nums">{t.timeline.length}</td>
                  <td className="text-right tabular-nums">{t.input_tokens.toLocaleString()}</td>
                  <td className="text-right tabular-nums">{fmtMs(t.duration_ms)}</td>
                  <td className="mono text-ink-2">{fs.length ? <>{high ? <span className="text-error">{high} high</span> : null}{high && fs.length > high ? " · " : ""}{fs.length > high ? `${fs.length - high} other` : ""}</> : <span className="text-ink-3">–</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </div>
  );
}
