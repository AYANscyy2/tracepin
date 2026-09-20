"""How the agent behaves after something goes wrong."""

import re

from tracepin.baselines import Baselines
from tracepin.detectors.base import Finding, Severity, finding, fingerprint, register
from tracepin.models import Trace

# Same vocabulary tasks.py uses to grade refusals: if the answer says "I can't", the
# failure was surfaced to the user rather than hidden.
ACKNOWLEDGED = re.compile(
    r"(can(?:'|no)?t|cannot|unable to|not able to|don't have|do not have|no (?:tool|way|access)|"
    r"not possible|error|fail|unavailable|try again)",
    re.IGNORECASE,
)


@register
class ErrorIgnored:
    id = "error.ignored"
    description = ("A tool call failed, the agent never got a later success from that tool, and answered anyway. "
                   "LOW if the answer admits the failure, HIGH if it hides it.")

    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]:
        if trace.stop_reason != "answered":
            return []
        calls = trace.tool_calls
        out = []
        seen_tools: set[str] = set()
        for i, s in enumerate(calls):
            if not s.is_error or s.tool_outcome == "unknown_tool" or not s.tool_name:
                continue  # unknown_tool is tool.unknown_name's finding
            if s.tool_name in seen_tools:
                continue  # one finding per tool per trace
            later_ok = any(t.tool_name == s.tool_name and t.tool_outcome == "ok" for t in calls[i + 1 :])
            if later_ok:
                continue
            seen_tools.add(s.tool_name)
            failures = [t for t in calls[i:] if t.tool_name == s.tool_name and t.is_error]
            acknowledged = bool(ACKNOWLEDGED.search(trace.final_answer))
            hidden = not acknowledged
            out.append(
                finding(
                    self.id, trace, failures,
                    severity=Severity.HIGH if hidden else Severity.LOW,
                    confidence=0.9 if hidden else 0.5,
                    title=f"{s.tool_name} failed ({s.tool_outcome}) and the agent answered "
                    + ("without mentioning it" if hidden else "admitting it"),
                    detail=f"{len(failures)} failed call(s), no later success. Error: {(s.tool_result or '')[:120]!r}. "
                    f"Final answer: {trace.final_answer[:140]!r}",
                    evidence={"tool": s.tool_name, "outcome": s.tool_outcome, "failures": len(failures),
                              "acknowledged_in_answer": acknowledged, "task_passed": trace.success},
                )
            )
        return out


@register
class NoProgress:
    id = "error.no_progress"
    description = "Hit max_iterations. Few distinct calls over many iterations = spinning, not exploring."

    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]:
        if trace.stop_reason != "max_iterations":
            return []
        calls = trace.tool_calls
        distinct = len({fingerprint(s) for s in calls})
        ratio = distinct / len(calls) if calls else 0.0
        tools = {s.tool_name for s in calls}
        ok_calls = sum(1 for s in calls if s.tool_outcome == "ok")
        # Spinning: same call over and over, or hammering one tool that never once worked.
        spinning = bool(calls) and (ratio <= 0.5 or (len(tools) == 1 and ok_calls == 0))
        return [
            finding(
                self.id, trace, [trace.root, *calls],
                severity=Severity.HIGH if spinning else Severity.MEDIUM,
                confidence=0.95,
                title=f"exhausted {trace.iterations} iterations: {distinct} distinct call(s) in {len(calls)} attempts"
                + (" — spinning" if spinning else " — exploring"),
                detail=f"stop_reason=max_iterations; {ok_calls} successful tool calls; "
                f"distinct/total = {ratio:.2f}; {trace.total_input_tokens} input tokens burned.",
                evidence={"iterations": trace.iterations, "calls": len(calls), "distinct_fingerprints": distinct,
                          "distinct_ratio": round(ratio, 2), "distinct_tools": sorted(t or "" for t in tools),
                          "successful_calls": ok_calls,
                          "input_tokens": trace.total_input_tokens},
            )
        ]


@register
class SelfCorrectionCost:
    id = "error.self_correction_cost"
    description = ("A rejected call (unknown tool / invalid args) later followed by a successful call: "
                   "the agent recovered. Measures turns and tokens spent recovering. Not a failure.")

    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]:
        calls = trace.tool_calls
        chats = trace.chats
        out = []
        handled: set[str] = set()
        for i, s in enumerate(calls):
            if s.tool_outcome not in ("invalid_arguments", "unknown_tool") or not s.tool_name:
                continue
            if s.tool_name in handled:
                continue
            # invalid args: recovery is the same tool succeeding; unknown tool: any tool succeeding.
            rec = next(
                (t for t in calls[i + 1 :]
                 if t.tool_outcome == "ok" and (s.tool_outcome == "unknown_tool" or t.tool_name == s.tool_name)),
                None,
            )
            if rec is None:
                continue
            handled.add(s.tool_name)
            wasted = [t for t in calls[i:] if t.start_ns < rec.start_ns]
            between = [c for c in chats if s.start_ns < c.start_ns <= rec.start_ns]
            tokens = sum((c.input_tokens or 0) + (c.output_tokens or 0) for c in between)
            wall_ms = (rec.end_ns - s.start_ns) / 1e6
            out.append(
                finding(
                    self.id, trace, [*wasted, rec],
                    severity=Severity.LOW,
                    confidence=0.85,
                    title=f"recovered from {s.tool_outcome} on {s.tool_name} after {len(between)} extra turn(s)",
                    detail=f"{len(wasted)} rejected call(s) before {rec.tool_name} succeeded; "
                    f"{tokens} tokens and {wall_ms:.0f}ms spent recovering.",
                    evidence={"failed_tool": s.tool_name, "failure": s.tool_outcome, "recovered_with": rec.tool_name,
                              "wasted_calls": len(wasted), "extra_turns": len(between), "tokens": tokens,
                              "wall_ms": round(wall_ms, 1)},
                )
            )
        return out
