"use client";

import { useEffect, useState } from "react";
import { fmtMs } from "@/lib/fmt";

/**
 * Main-thread frames per second, sampled every 500 ms. Pan or zoom the canvas and watch it.
 * A quick proxy; confirm numbers for the README with the Chrome Performance panel.
 */
export function PerfHud({ nodes, layoutMs }: { nodes: number; layoutMs: number }) {
  const [fps, setFps] = useState<number | null>(null);

  useEffect(() => {
    let frames = 0;
    let windowStart = performance.now();
    let raf = requestAnimationFrame(function tick(now) {
      frames++;
      if (now - windowStart >= 500) {
        setFps(Math.round((frames * 1000) / (now - windowStart)));
        frames = 0;
        windowStart = now;
      }
      raf = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div className="mono pointer-events-none absolute left-2 top-2 z-10 flex gap-3 border hairline bg-panel/90 px-2 py-1 text-[11px] text-ink-2" aria-live="off">
      <span><b className="text-ink">{fps ?? "–"}</b> fps</span>
      <span><b className="text-ink">{nodes}</b> spans</span>
      <span>layout <b className="text-ink">{fmtMs(layoutMs)}</b></span>
    </div>
  );
}
