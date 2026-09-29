// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, expect, test, vi } from "vitest";
import * as graphLib from "@/lib/graph";
import { GraphView } from "@/components/GraphView";
import { nodeRenderProbe } from "@/components/GraphNode";
import { generateSyntheticTrace } from "@/lib/synthetic";

// count layouts: selection must never rebuild the nodes array
vi.mock("@/lib/graph", async (orig) => {
  const mod = await orig<typeof graphLib>();
  return { ...mod, buildGraph: vi.fn(mod.buildGraph) };
});

// React Flow needs these in jsdom (per its testing guide)
beforeAll(() => {
  globalThis.ResizeObserver = class {
    constructor(private cb: ResizeObserverCallback) {}
    observe(target: Element) {
      const contentRect = { x: 0, y: 0, width: 1000, height: 800, top: 0, left: 0, right: 1000, bottom: 800 } as DOMRectReadOnly;
      this.cb([{ target, contentRect } as ResizeObserverEntry], this as unknown as ResizeObserver);
    }
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  globalThis.DOMMatrixReadOnly = class {
    m22: number;
    constructor(t?: string) { this.m22 = Number(t?.match(/scale\(([\d.]+)\)/)?.[1] ?? 1); }
  } as unknown as typeof DOMMatrixReadOnly;
  Object.defineProperties(HTMLElement.prototype, {
    offsetHeight: { configurable: true, get() { return parseFloat(this.style.height) || 1; } },
    offsetWidth: { configurable: true, get() { return parseFloat(this.style.width) || 1; } },
  });
  (SVGElement.prototype as unknown as { getBBox: () => DOMRect }).getBBox = () => ({ x: 0, y: 0, width: 0, height: 0 }) as DOMRect;
});
afterEach(() => {
  cleanup();
  nodeRenderProbe.onRender = null;
});

// under the virtualisation threshold, so every node is mounted and counted
const { spans, findings } = generateSyntheticTrace(150);
const noop = () => {};

test("selecting a finding re-renders only the nodes whose highlight flips", () => {
  const renders = new Map<string, number>();
  nodeRenderProbe.onRender = (id) => renders.set(id, (renders.get(id) ?? 0) + 1);
  const onSelectSpan = vi.fn();
  const view = (hl: ReadonlySet<string>) => <GraphView spans={spans} findings={findings} highlighted={hl} onSelectSpan={onSelectSpan} onHover={noop} />;

  const { rerender, container } = render(view(new Set()));
  expect(new Set(renders.keys()).size).toBe(spans.length);
  const frame = screen.getByTestId("graph-frame");
  expect(frame.classList.contains("has-selection")).toBe(false);

  const rerendered = (hl: ReadonlySet<string>) => {
    renders.clear();
    act(() => rerender(view(hl)));
    return new Set(renders.keys());
  };

  const a = new Set(findings.find((f) => f.detector_id === "loop.near_duplicate")!.span_ids);
  const b = new Set(findings.find((f) => f.detector_id === "perf.latency_outlier")!.span_ids);
  expect(a.size).toBeGreaterThan(1);

  expect(rerendered(a)).toEqual(a);
  expect(frame.classList.contains("has-selection")).toBe(true);
  expect(container.querySelectorAll(".gnode.is-hl")).toHaveLength(a.size);

  // switching findings touches only the symmetric difference
  expect(rerendered(b)).toEqual(new Set([...a, ...b].filter((id) => a.has(id) !== b.has(id))));
  expect(container.querySelectorAll(".gnode.is-hl")).toHaveLength(b.size);

  // same selection, new Set instance (a parent re-render): nothing re-renders
  expect(rerendered(new Set(b)).size).toBe(0);

  expect(rerendered(new Set())).toEqual(b);
  expect(frame.classList.contains("has-selection")).toBe(false);

  // clicking a node: only that node re-renders (its selected ring), the explorer's handler runs,
  // and the inspector shows its attributes
  const target = spans[5];
  renders.clear();
  act(() => { fireEvent.click(container.querySelector(`.react-flow__node[data-id="${target.span_id}"]`)!); });
  expect(new Set(renders.keys())).toEqual(new Set([target.span_id]));
  expect(onSelectSpan).toHaveBeenCalledWith(target.span_id);
  expect(screen.getAllByText(target.span_id).length).toBeGreaterThan(0);

  // the layout ran once; selection never rebuilt the nodes array
  expect(graphLib.buildGraph).toHaveBeenCalledTimes(1);
});
