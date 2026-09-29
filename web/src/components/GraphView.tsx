"use client";

import "@xyflow/react/dist/style.css";
import { Background, BackgroundVariant, Controls, MiniMap, ReactFlow, useReactFlow, useStore, type Node, type NodeMouseHandler } from "@xyflow/react";
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { buildGraph, type GraphNodeData, type TraceGraph } from "@/lib/graph";
import { createGraphStore, GraphStoreContext, useGraph, type GraphStore } from "@/lib/graphStore";
import type { Finding, Span } from "@/lib/types";
import { fmtMs } from "@/lib/fmt";
import { GraphNode, PinIcon, type SpanFlowNode } from "./GraphNode";
import { PerfHud } from "./PerfHud";
import { SpanAttributes } from "./SpanAttributes";

/** Module scope: a new object each render would make React Flow re-mount every node. */
const NODE_TYPES = { span: GraphNode };
/** Above this, only nodes in the viewport are mounted. */
const VIRTUALIZE_ABOVE = 200;
/** The initial view frames the first N nodes, top-aligned, so a long agent loop opens readable instead of fit-to-tiny. */
const INITIAL_FRAME = 6;
const FRAME_PAD = 24;
const MINIMAP_ABOVE = 40;

const MINIMAP_COLOR = { ok: "var(--ink-3)", slow: "var(--warn)", failed: "var(--error)" } as const;
function minimapColor(node: Node) {
  const d = node.data as GraphNodeData;
  return d.status === "ok" && d.flagged ? "var(--accent)" : MINIMAP_COLOR[d.status];
}

export function GraphView({ spans, findings, highlighted, onSelectSpan, onHover, hud = false }: {
  spans: Span[]; findings: Finding[]; highlighted: ReadonlySet<string>;
  onSelectSpan: (id: string) => void; onHover: (id: string | null) => void; hud?: boolean;
}) {
  const graph = useMemo(() => buildGraph(spans, findings), [spans, findings]);
  const [store] = useState(() => createGraphStore(highlighted, onSelectSpan));
  // Mirror the explorer's selection into the store; nodes pick out their own booleans.
  useLayoutEffect(() => store.setState({ highlighted }), [store, highlighted]);
  useLayoutEffect(() => store.setState({ onSelectSpan }), [store, onSelectSpan]);

  return (
    <GraphStoreContext.Provider value={store}>
      <div className="border hairline bg-panel">
        <div className="flex items-baseline justify-between border-b hairline px-2 py-1 text-ink-2">
          <span className="text-xs">{spans.length} spans · each chat turn is a row, its tool calls below it · click a node to select its finding and inspect it</span>
          <span className="mono text-xs"><span className="inline-block h-2 w-1 bg-accent align-middle" /> ok <span className="ml-2 inline-block h-2 w-1 bg-warn align-middle" /> slow <span className="ml-2 inline-block h-2 w-1 bg-error align-middle" /> failed <span className="ml-2 text-warn"><PinIcon /></span> flagged</span>
        </div>
        <Frame graph={graph} store={store} onHover={onHover} hud={hud} />
        <Inspector graph={graph} />
      </div>
    </GraphStoreContext.Provider>
  );
}

/** Owns the has-selection class: it re-renders on selection, the memoised canvas under it doesn't. */
function Frame({ graph, store, onHover, hud }: { graph: TraceGraph; store: GraphStore; onHover: (id: string | null) => void; hud: boolean }) {
  const hasSelection = useGraph((s) => s.highlighted.size > 0);
  return (
    <div data-testid="graph-frame" className={`tp-graph relative h-[70vh] min-h-[420px] ${hasSelection ? "has-selection" : ""}`}>
      <Canvas graph={graph} store={store} onHover={onHover} />
      {hud && <PerfHud nodes={graph.nodes.length} layoutMs={graph.layoutMs} />}
    </div>
  );
}

const Canvas = memo(function Canvas({ graph, store, onHover }: { graph: TraceGraph; store: GraphStore; onHover: (id: string | null) => void }) {
  const onNodeClick = useCallback<NodeMouseHandler>((_, n) => store.getState().activate(n.id), [store]);
  const onPaneClick = useCallback(() => store.setState({ selectedSpan: null }), [store]);
  const onNodeMouseEnter = useCallback<NodeMouseHandler>((_, n) => onHover(n.id), [onHover]);
  const onNodeMouseLeave = useCallback(() => onHover(null), [onHover]);
  const big = graph.nodes.length > VIRTUALIZE_ABOVE;

  return (
    <ReactFlow<SpanFlowNode>
      nodes={graph.nodes}
      edges={graph.edges}
      nodeTypes={NODE_TYPES}
      onNodeClick={onNodeClick}
      onPaneClick={onPaneClick}
      onNodeMouseEnter={onNodeMouseEnter}
      onNodeMouseLeave={onNodeMouseLeave}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      nodesFocusable={false}
      edgesFocusable={false}
      onlyRenderVisibleElements={big}
      minZoom={0.03}
      maxZoom={2}
      panOnScroll
      zoomOnDoubleClick={false}
      attributionPosition="top-right"
    >
      <InitialView graph={graph} />
      <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="var(--rule)" />
      <Controls showInteractive={false} position="bottom-left" />
      {graph.nodes.length > MINIMAP_ABOVE && (
        <MiniMap pannable zoomable position="bottom-right" nodeColor={minimapColor} nodeStrokeWidth={0} ariaLabel="Trace overview" />
      )}
    </ReactFlow>
  );
});

/** Once the pane has a size: frame the first nodes at zoom <= 1, centred horizontally, top-aligned. */
function InitialView({ graph }: { graph: TraceGraph }) {
  const { setViewport } = useReactFlow();
  const w = useStore((s) => s.width);
  const h = useStore((s) => s.height);
  const done = useRef(false);
  useEffect(() => {
    if (done.current || !w || !h || !graph.nodes.length) return;
    done.current = true;
    const first = graph.nodes.slice(0, INITIAL_FRAME);
    const minX = Math.min(...first.map((n) => n.position.x));
    const maxX = Math.max(...first.map((n) => n.position.x + n.width));
    const maxY = Math.max(...first.map((n) => n.position.y + n.height));
    const zoom = Math.min(1, (w - 2 * FRAME_PAD) / (maxX - minX), (h - 2 * FRAME_PAD) / maxY);
    void setViewport({ x: (w - (maxX - minX) * zoom) / 2 - minX * zoom, y: FRAME_PAD, zoom });
  }, [w, h, graph, setViewport]);
  return null;
}

function Inspector({ graph }: { graph: TraceGraph }) {
  const id = useGraph((s) => s.selectedSpan);
  const span = id === null ? undefined : graph.spanById.get(id);
  if (!span) return <div className="border-t hairline px-3 py-1.5 text-xs text-ink-3">Click a node to see its attributes and events.</div>;
  return (
    <div className="border-t hairline bg-bg px-3 py-2">
      <div className="mono mb-1 text-[12px]">{span.name} <span className="text-ink-3">· {fmtMs(span.end_ms - span.start_ms)} @ {fmtMs(span.start_ms)}</span></div>
      <SpanAttributes span={span} />
    </div>
  );
}
