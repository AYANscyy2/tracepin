"use client";

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { memo } from "react";
import type { GraphNodeData, NodeKind } from "@/lib/graph";
import { useGraph } from "@/lib/graphStore";
import { fmtMs } from "@/lib/fmt";

export type SpanFlowNode = Node<GraphNodeData, "span">;

// same glyphs as the Waterfall labels
const GLYPH: Record<NodeKind, string> = { agent: "▶", chat: "✉", tool: "⚙", other: "·" };
const BAR = { ok: "bg-accent", slow: "bg-warn", failed: "bg-error" } as const;

export function PinIcon() {
  return <svg className="inline-block" width="12" height="12" viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M8 1a4.5 4.5 0 0 0-1 8.9V15l1 1 1-1V9.9A4.5 4.5 0 0 0 8 1Z" /></svg>;
}

/** Test hook: called on every node render so the re-render test can count them. */
export const nodeRenderProbe: { onRender: ((id: string) => void) | null } = { onRender: null };

/**
 * Subscribes to two derived booleans, never to the raw selection. Dimming the rest when a
 * finding is selected is a CSS rule on the graph wrapper (`.has-selection .gnode:not(.is-hl)`),
 * so selecting a finding re-renders only the nodes entering or leaving it.
 */
function GraphNodeImpl({ id, data }: NodeProps<SpanFlowNode>) {
  const hl = useGraph((s) => s.highlighted.has(id));
  const selected = useGraph((s) => s.selectedSpan === id);
  const activate = useGraph((s) => s.activate);
  nodeRenderProbe.onRender?.(id);

  const { kind, label, status, flagged, detectors, durationMs, inputTokens, outputTokens, retries, outcome } = data;
  const badOutcome = outcome !== null && outcome !== "ok";
  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={`${kind} ${label}, ${fmtMs(durationMs)}, ${status}${flagged ? `, flagged by ${detectors.join(", ")}` : ""}`}
      title={data.span.name}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          activate(id);
        }
      }}
      className={`gnode relative flex h-full w-full cursor-pointer overflow-hidden border transition-opacity ${hl ? "is-hl bg-accent-soft" : "bg-panel"} ${status === "failed" ? "border-error/60" : "hairline"} ${selected ? "ring-2 ring-ink ring-offset-1" : ""}`}
    >
      <Handle type="target" position={Position.Top} id="t" isConnectable={false} />
      <div className={`w-1 shrink-0 ${BAR[status]}`} />
      <div className="mono min-w-0 flex-1 px-2 py-1.5 text-[12px] leading-tight">
        <div className="flex items-baseline gap-1 pr-4">
          <span className="text-ink-3">{GLYPH[kind]}</span>
          <span className={`truncate ${status === "failed" ? "text-error" : ""}`}>{label}</span>
        </div>
        <div className="mt-1.5 flex items-baseline gap-2 whitespace-nowrap text-[11px] text-ink-2">
          <span className={status === "slow" ? "text-warn" : ""}>{fmtMs(durationMs)}</span>
          {kind === "chat" && inputTokens !== null && <span className="text-ink-3">{inputTokens}/{outputTokens ?? 0} tok</span>}
          {retries > 0 && <span className="text-warn">{retries} retr{retries === 1 ? "y" : "ies"}</span>}
          {badOutcome && <span className="truncate bg-error-soft px-1 text-[10px] text-error">{outcome}</span>}
        </div>
      </div>
      {flagged && (
        <span className="absolute right-1 top-1 text-warn" title={`flagged: ${detectors.join(", ")}`}>
          <PinIcon />
        </span>
      )}
      <Handle type="source" position={Position.Bottom} id="b" isConnectable={false} />
      <Handle type="source" position={Position.Right} id="r" isConnectable={false} />
    </div>
  );
}

export const GraphNode = memo(GraphNodeImpl);
