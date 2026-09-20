import type { Finding, LocationRank } from "@/lib/types";

export function CodePanel({ finding, location, sha }: { finding: Finding | null; location: LocationRank | null; sha: string }) {
  if (!finding) return null;
  if (!finding.code_location || !location?.context) {
    return (
      <div className="border hairline bg-panel px-3 py-2 text-ink-2">
        <span className="mono text-ink-3">code</span> · no code location: the implicated call never reached a function — that absence is the finding.
      </div>
    );
  }
  const c = location.context;
  const lines = c.snippet.split("\n");
  return (
    <div className="border hairline bg-panel">
      <div className="flex flex-wrap items-baseline gap-x-4 border-b hairline px-3 py-1">
        <span className="mono font-medium">{c.file}:{c.line}</span>
        <span className="mono text-ink-2">{c.function}</span>
        <span className="text-ink-3">{location.findings} findings / {location.tasks.length} tasks at this line in this run</span>
        <span className="mono ml-auto text-[11px] text-ink-3">source @ {c.source_ref.slice(0, 10)}{c.source_ref === sha ? " (the commit that ran)" : ""}</span>
      </div>
      {c.blame && (
        <div className="mono border-b hairline px-3 py-1 text-[11px] text-ink-2">
          blame <span className="font-medium text-ink">{c.blame.commit.slice(0, 8)}</span> {c.blame.author} {c.blame.authored_at.slice(0, 10)} <span className="italic">“{c.blame.subject}”</span>
          {c.permalink && <a className="ml-3 underline" href={c.permalink} target="_blank" rel="noreferrer">github ↗</a>}
        </div>
      )}
      <pre className="mono overflow-x-auto px-3 py-2 text-[11.5px] leading-[1.5]">
        {lines.map((ln, i) => {
          const hit = ln.startsWith(">");
          return <div key={i} className={hit ? "-mx-3 bg-error-soft px-3 font-medium" : ""}>{ln}</div>;
        })}
      </pre>
    </div>
  );
}
