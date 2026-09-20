"""Latency and token behaviour relative to learned baselines. No hardcoded milliseconds."""

from tracepin.baselines import Baselines
from tracepin.detectors.base import Finding, Severity, finding, register
from tracepin.models import Trace

BLOAT_RATIO = 3.0
BLOAT_MIN_CHATS = 3  # two chat spans always grow (a tool result got appended); need a trend
SPIKE_RATIO = 3.0
# Model API latency jitters 2-4× on its own (shared free-tier quota); that is not the
# agent's behaviour. Only stalls well beyond jitter are worth a finding on a chat span.
CHAT_MIN_RATIO = 5.0


@register
class LatencyOutlier:
    id = "perf.latency_outlier"
    description = "Span slower than median + 3·MAD and >2× median for its tool (or model), n≥5."

    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]:
        out = []
        for s in trace.tool_calls:
            stats = baselines.tool_latency_ms.get(s.tool_name or "")
            if not stats or s.tool_outcome not in ("ok", "exception") or not stats.is_outlier(s.duration_ms):
                continue
            out.append(self._finding(trace, s, stats, s.tool_name))
        for c in trace.chats:
            stats = baselines.chat_latency_ms.get(c.model or "")
            if not stats or c.is_error or not stats.is_outlier(c.duration_ms):
                continue
            if c.duration_ms < CHAT_MIN_RATIO * stats.median:
                continue
            out.append(self._finding(trace, c, stats, f"chat {c.model}"))
        return out

    def _finding(self, trace, span, stats, label):
        ratio = span.duration_ms / stats.median if stats.median else float("inf")
        retries = span.attributes.get("tracepin.chat.transport_retries", 0)
        return finding(
            self.id, trace, [span],
            # Ratio-based, never absolute: a 3× blip on a sub-ms tool is LOW, a 30× stall is HIGH.
            severity=Severity.HIGH if ratio > 10 else Severity.MEDIUM if ratio > 5 else Severity.LOW,
            confidence=min(1.0, 0.5 + 0.05 * ratio),
            title=f"{label} took {span.duration_ms:.0f}ms ({ratio:.1f}× its median)",
            detail=f"baseline median {stats.median:.1f}ms, MAD {stats.mad:.1f}ms over n={stats.n}"
            + (f"; {retries} transport retries inside the span" if retries else ""),
            evidence={"duration_ms": span.duration_ms, "median_ms": stats.median, "mad_ms": stats.mad,
                      "n": stats.n, "ratio": round(ratio, 2), "transport_retries": retries},
        )


@register
class ContextBloat:
    id = "perf.context_bloat"
    description = "Input tokens compounding across chat turns: last >3× first, or superlinear growth."

    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]:
        chats = [c for c in trace.chats if c.input_tokens is not None]
        if len(chats) < BLOAT_MIN_CHATS:
            return []
        series = [c.input_tokens for c in chats]
        first, last = series[0], series[-1]
        ratio = last / first if first else float("inf")
        deltas = [b - a for a, b in zip(series, series[1:])]
        # superlinear: each step adds more than the step before (context is feeding on itself)
        superlinear = len(deltas) >= 3 and all(d2 > d1 for d1, d2 in zip(deltas, deltas[1:]))
        if ratio <= BLOAT_RATIO and not superlinear:
            return []
        errors_fed_back = sum(1 for s in trace.tool_calls if s.is_error)
        return [
            finding(
                self.id, trace, chats,
                severity=Severity.HIGH if ratio > 2 * BLOAT_RATIO else Severity.MEDIUM,
                confidence=min(1.0, 0.7 + 0.1 * (ratio - BLOAT_RATIO)) if ratio > BLOAT_RATIO else 0.7,
                title=f"context grew {ratio:.1f}× over {len(series)} turns ({first}→{last} input tokens)",
                detail=f"input token series {series}; deltas {deltas}; "
                f"{errors_fed_back} error results were fed back into context along the way."
                + (" Growth is superlinear." if superlinear else ""),
                evidence={"input_tokens": series, "deltas": deltas, "ratio": round(ratio, 2),
                          "superlinear": superlinear, "error_results_fed_back": errors_fed_back,
                          "total_input_tokens": sum(series)},
            )
        ]


@register
class TokenSpike:
    id = "perf.token_spike"
    description = "One chat span's output tokens far above the run median — the model rambling instead of calling a tool."

    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]:
        stats = baselines.chat_output_tokens
        if not stats:
            return []
        out = []
        for c in trace.chats:
            if c.output_tokens is None or not stats.is_outlier(c.output_tokens) or c.output_tokens < SPIKE_RATIO * stats.median:
                continue
            called = c.attributes.get("tracepin.chat.tool_calls_requested") or []
            out.append(
                finding(
                    self.id, trace, [c],
                    severity=Severity.LOW,
                    confidence=0.7,
                    title=f"chat emitted {c.output_tokens} output tokens (run median {stats.median:.0f})",
                    detail=f"iteration {c.attributes.get('tracepin.chat.iteration')}; tool calls requested: {list(called) or 'none'}",
                    evidence={"output_tokens": c.output_tokens, "median": stats.median, "mad": stats.mad,
                              "tool_calls_requested": list(called)},
                )
            )
        return out


# --- unaccounted time --------------------------------------------------------------
RETRY_EVENT = "tracepin.transport.retry"
HOLE_RATIO = 5.0  # an attempt that ran > 5× the model's median call and returned nothing is a hole
MIN_SHARE = 0.2  # ...and holes must add up to a fifth of the trace before it is worth a finding


def _ns(ts: str) -> int:
    from tracepin.models import _iso_to_ns
    return _iso_to_ns(ts)


def _root_gaps(trace: Trace) -> list[dict]:
    """Wall time under the root that no child span covers (lead-in, tail, or between children)."""
    kids = sorted(trace.children(trace.root.span_id), key=lambda s: s.start_ns)
    if not kids:
        return []
    edges = [(trace.root.start_ns, kids[0].start_ns)]
    edges += [(a.end_ns, b.start_ns) for a, b in zip(kids, kids[1:])]
    edges.append((kids[-1].end_ns, trace.root.end_ns))
    return [{"kind": "root_gap", "span_id": trace.root.span_id, "start_ns": a, "end_ns": b, "ms": (b - a) / 1e6}
            for a, b in edges if b > a]


def _attempts(chat) -> list[dict]:
    """Split a chat span into HTTP attempts using its retry events.

    Each retry event marks the end of a failed attempt followed by a recorded sleep of
    `delay_s`. Everything between the sleeps is time spent waiting on a request.
    """
    retries = sorted((e for e in chat.events if e.get("name") == RETRY_EVENT), key=lambda e: e["timestamp"])
    out, cursor = [], chat.start_ns
    for i, e in enumerate(retries, 1):
        end = _ns(e["timestamp"])
        delay_ns = int(float(e.get("attributes", {}).get("delay_s", 0)) * 1e9)
        out.append({"attempt": i, "start_ns": cursor, "end_ns": end, "returned": False})
        cursor = end + delay_ns
    out.append({"attempt": len(retries) + 1, "start_ns": cursor, "end_ns": chat.end_ns, "returned": not chat.is_error})
    return out


@register
class UnaccountedTime:
    id = "perf.unaccounted_time"
    description = ("Wall time nothing accounts for: gaps under the root no child span covers, and time inside "
                   "a chat span spent on requests that returned nothing, beyond the recorded backoff sleeps.")

    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]:
        median = min((s.median for s in baselines.chat_latency_ms.values()), default=0.0)
        if not median:
            return []  # no model baseline yet: nothing to call "too long"
        holes = [g for g in _root_gaps(trace) if g["ms"] >= HOLE_RATIO * median]
        spans = []
        for c in trace.chats:
            stats = baselines.chat_latency_ms.get(c.model or "")
            if not stats:
                continue
            for a in _attempts(c):
                ms = (a["end_ns"] - a["start_ns"]) / 1e6
                # A slow attempt that *answered* is latency (perf.latency_outlier's job); a slow
                # attempt that produced nothing is time the trace cannot explain.
                if a["returned"] or ms < HOLE_RATIO * stats.median:
                    continue
                holes.append({"kind": "request_no_response", "span_id": c.span_id, "attempt": a["attempt"],
                              "start_ns": a["start_ns"], "end_ns": a["end_ns"], "ms": ms,
                              "recorded_backoff_s": sum(float(e.get("attributes", {}).get("delay_s", 0))
                                                        for e in c.events if e.get("name") == RETRY_EVENT)})
                spans.append(c)
        total = sum(h["ms"] for h in holes)
        wall = trace.duration_ms
        if not holes or total < max(HOLE_RATIO * median, MIN_SHARE * wall):
            return []
        share = total / wall if wall else 0.0
        worst = max(holes, key=lambda h: h["ms"])
        holes.sort(key=lambda h: h["start_ns"])
        return [finding(
            self.id, trace, spans or [trace.root],
            severity=Severity.HIGH if share > 0.5 else Severity.MEDIUM if share > 0.25 else Severity.LOW,
            confidence=0.9 if spans else 0.7,
            title=f"{total / 1000:.0f}s of {wall / 1000:.0f}s wall clock ({share:.0%}) is unaccounted for",
            detail=f"{len(holes)} hole(s); largest {worst['ms'] / 1000:.0f}s "
                   + (f"in chat attempt {worst['attempt']} that never returned (recorded backoff "
                      f"{worst['recorded_backoff_s']:.0f}s does not cover it)" if worst["kind"] == "request_no_response"
                      else "between spans under the root"),
            evidence={"wall_ms": round(wall, 1), "unaccounted_ms": round(total, 1), "share": round(share, 3),
                      "holes": [{**h, "ms": round(h["ms"], 1)} for h in holes]},
        )]
