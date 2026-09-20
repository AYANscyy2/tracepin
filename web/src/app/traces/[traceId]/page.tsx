import Link from "next/link";
import { TraceExplorer } from "@/components/TraceExplorer";
import { allTraceIds, findRunForTrace, fmtMs, listRuns, loadAnalysis, loadCode, loadTraces, shortId } from "@/lib/data";

export function generateStaticParams() {
  return allTraceIds().map(({ traceId }) => ({ traceId }));
}

export default async function TracePage({ params }: { params: Promise<{ traceId: string }> }) {
  const { traceId } = await params;
  const runId = findRunForTrace(traceId);
  if (!runId) return <p>Unknown trace.</p>;
  const a = loadAnalysis(runId);
  const t = a.traces.find((x) => x.trace_id === traceId)!;
  const spans = loadTraces(runId)[traceId].spans;
  const code = loadCode(runId);
  const label = listRuns().find((r) => r.run_id === runId)?.label ?? runId;
  const order = { high: 0, medium: 1, low: 2 } as const;
  const findings = a.findings.filter((f) => f.trace_id === traceId).sort((x, y) => order[x.severity] - order[y.severity] || y.confidence - x.confidence);

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <Link href={`/runs/${runId}/`} className="text-ink-2">← {label}</Link>
        <h1 className="text-base font-semibold">{t.task_id} <span className="mono font-normal text-ink-3">{shortId(traceId)}</span></h1>
        <span className={t.success ? "text-accent" : "font-medium text-error"}>{t.success ? "passed" : "failed"}</span>
        <span className="mono text-ink-2">{t.stop_reason} · {t.iterations} iterations · {t.timeline.length} tool calls · {t.input_tokens.toLocaleString()}/{t.output_tokens.toLocaleString()} tokens · {fmtMs(t.duration_ms)}</span>
        <span className="mono text-ink-3">{t.tags.join(" ")}</span>
      </div>
      <p className="mb-1"><span className="text-ink-3">prompt · </span>{t.task_prompt}</p>
      <p className="mb-3 text-ink-2"><span className="text-ink-3">answer · </span>{t.final_answer || <span className="italic text-ink-3">(none)</span>}</p>
      <TraceExplorer summary={t} spans={spans} findings={findings} code={code} sha={a.run.git_sha} />
    </div>
  );
}
