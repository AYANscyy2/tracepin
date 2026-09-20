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
