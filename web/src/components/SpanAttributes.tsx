import { Fragment } from "react";
import type { Span } from "@/lib/types";
import { fmtMs } from "@/lib/fmt";

const HIDE_ATTRS = new Set(["tracepin.task.prompt", "tracepin.task.expected"]);

/** A span's status, attributes and events; shared by the Waterfall row drawer and the Graph inspector. */
export function SpanAttributes({ span: s }: { span: Span }) {
  return (
    <dl className="mono grid grid-cols-[max-content_1fr] gap-x-4 gap-y-0.5 text-[11px]">
      <dt className="text-ink-3">span_id</dt><dd>{s.span_id}</dd>
      <dt className="text-ink-3">status</dt><dd className={s.status_code === "ERROR" ? "text-error" : ""}>{s.status_code}{s.status_message ? ` — ${s.status_message}` : ""}</dd>
      {Object.entries(s.attributes).filter(([k]) => !HIDE_ATTRS.has(k)).map(([k, v]) => (
        <Fragment key={k}><dt className="text-ink-3">{k}</dt><dd className="break-all whitespace-pre-wrap">{typeof v === "string" ? v : JSON.stringify(v)}</dd></Fragment>
      ))}
      {s.events.map((e, i) => (
        <Fragment key={`event-${i}`}><dt className="text-warn">event {e.name}</dt><dd className="break-all">{e.at_ms !== null ? `@${fmtMs(e.at_ms)} ` : ""}{JSON.stringify(e.attributes)}</dd></Fragment>
      ))}
    </dl>
  );
}
