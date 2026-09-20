import Link from "next/link";
import { listRuns, pct } from "@/lib/data";

export default function RunsPage() {
  const runs = listRuns();
  return (
    <div>
      <h1 className="mb-1 text-base font-semibold">Runs</h1>
      <p className="mb-4 text-ink-2">
        Each run is 44 seeded tasks against the same tool set. Findings come from{" "}
        <span className="mono">tracepin analyze</span>; nothing here re-runs detectors.
      </p>
      <table className="w-full">
        <thead>
          <tr>
            <th>label</th><th>run</th><th>model</th><th>prompt</th><th>commit</th>
            <th className="text-right">passed</th><th className="text-right">findings</th>
            <th className="text-right">high</th><th className="text-right">traces flagged</th><th className="text-right">input tokens</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.run_id}>
              <td><Link href={`/runs/${r.run_id}/`} className="font-medium">{r.label}</Link></td>
              <td className="mono">{r.run_id}</td>
              <td className="mono">{r.model}</td>
              <td className="mono">{r.prompt_version}</td>
              <td className="mono">{r.git_sha.slice(0, 10)}</td>
              <td className="text-right tabular-nums">{r.pass_count}/{r.task_count} <span className="text-ink-3">({pct(r.pass_count, r.task_count)})</span></td>
              <td className="text-right tabular-nums">{r.summary.total}</td>
              <td className="text-right tabular-nums text-error">{r.summary.by_severity.high ?? 0}</td>
              <td className="text-right tabular-nums">{r.summary.traces_with_findings}</td>
              <td className="text-right tabular-nums">{r.total_input_tokens.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-4 text-ink-3">
        v1 → v2 changed the prompt (real tools only, refusal allowed, one retry). v2 → v3 changed one docstring
        (<span className="mono">tools/docs.py:36</span>). See <Link href="/compare/" className="underline">compare</Link>.
      </p>
    </div>
  );
}
