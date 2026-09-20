"""Repeated calls, A→B→A→B cycles, and retry storms. Nothing in Day 1 labels these;
they are inferred from the order of execute_tool spans within a trace."""

from collections import defaultdict

from tracepin.baselines import Baselines
from tracepin.detectors.base import Finding, Severity, finding, fingerprint, register
from tracepin.models import Trace

REPEAT_MIN = 3
STORM_MIN = 3
STORM_HIGH_RETRIES = 4


@register
class RepeatedCall:
    id = "loop.repeated_call"
    description = "Identical tool call (same tool, same canonical args) ≥3 times in one trace."

    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]:
        groups: dict[str, list] = defaultdict(list)
        for s in trace.tool_calls:
            groups[fingerprint(s)].append(s)

        out = []
        for fp, spans in groups.items():
            if len(spans) < REPEAT_MIN:
                continue
            results = {s.tool_result for s in spans}
            same_result = len(results) == 1
            # If the tool answered identically every time the agent learned nothing.
            confidence = 1.0 if same_result else min(1.0, 0.6 + 0.1 * (len(spans) - REPEAT_MIN))
            out.append(
                finding(
                    self.id, trace, spans,
                    severity=Severity.HIGH,
                    confidence=confidence,
                    title=f"{spans[0].tool_name} called {len(spans)}× with identical arguments",
                    detail=(
                        f"{fp} repeated {len(spans)} times; the tool returned "
                        f"{'the same result every time' if same_result else f'{len(results)} distinct results'}."
                    ),
                    evidence={
                        "fingerprint": fp,
                        "count": len(spans),
                        "identical_results": same_result,
                        "outcomes": [s.tool_outcome for s in spans],
                    },
                )
            )
        return out


def _is_periodic(pattern: tuple) -> bool:
    n = len(pattern)
    return any(n % p == 0 and pattern == pattern[:p] * (n // p) for p in range(1, n))


def _rotation_key(pattern: tuple) -> tuple:
    return min(pattern[i:] + pattern[:i] for i in range(len(pattern)))


@register
class Cycle:
    id = "loop.cycle"
    description = "A repeating subsequence of 2–4 distinct calls (A,B,A,B) occurring ≥2 times."

    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]:
        calls = trace.tool_calls
        seq = [fingerprint(s) for s in calls]
        best: dict[tuple, tuple[int, int, tuple]] = {}  # canonical -> (start, repeats, pattern as seen)

        for length in range(2, 5):
            i = 0
            while i + 2 * length <= len(seq):
                pattern = tuple(seq[i : i + length])
                # A,A,A is repeated_call's job; A,B,A,B as a length-4 pattern is just the
                # length-2 one counted twice.
                if len(set(pattern)) == 1 or _is_periodic(pattern):
                    i += 1
                    continue
                repeats = 1
                while seq[i + repeats * length : i + (repeats + 1) * length] == list(pattern):
                    repeats += 1
                key = _rotation_key(pattern)  # B,A,B,A is the same cycle as A,B,A,B
                if repeats >= 2 and (key not in best or repeats > best[key][1]):
                    best[key] = (i, repeats, pattern)
                i += 1

        out = []
        for start, repeats, pattern in best.values():
            spans = calls[start : start + repeats * len(pattern)]
            out.append(
                finding(
                    self.id, trace, spans,
                    severity=Severity.HIGH,
                    confidence=min(1.0, 0.7 + 0.15 * (repeats - 2)),
                    title=f"{len(pattern)}-call cycle repeated {repeats}×: "
                    + " → ".join(s.split("(")[0] for s in pattern),
                    detail=f"The sequence {list(pattern)} recurs {repeats} times back-to-back over {len(spans)} calls.",
                    evidence={"pattern": list(pattern), "length": len(pattern), "repeats": repeats},
                )
            )
        return out


@register
class RetryStorm:
    id = "loop.retry_storm"
    description = "Same tool called ≥3 times with the earlier calls failing. Retrying is sometimes right; storms are not."

    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]:
        by_tool: dict[str, list] = defaultdict(list)
        for s in trace.tool_calls:
            if s.tool_name:
                by_tool[s.tool_name].append(s)

        out = []
        for tool, spans in by_tool.items():
            if len(spans) < STORM_MIN:
                continue
            # A storm is a run of failures; the last call may or may not have succeeded.
            if not all(s.is_error for s in spans[:-1]):
                continue
            retries = len(spans) - 1
            fps = {fingerprint(s) for s in spans}
            args_never_changed = len(fps) == 1
            recovered = not spans[-1].is_error
            severity = Severity.HIGH if (retries > STORM_HIGH_RETRIES or args_never_changed) else Severity.MEDIUM
            out.append(
                finding(
                    self.id, trace, spans,
                    severity=severity,
                    confidence=0.9 if args_never_changed else 0.7,
                    title=f"{tool} retried {retries}× after failures"
                    + (" without changing arguments" if args_never_changed else "")
                    + (", eventually succeeded" if recovered else ", never succeeded"),
                    detail=(
                        f"{len(spans)} calls to {tool}; first {len(spans) - 1} failed "
                        f"({', '.join(s.tool_outcome or '?' for s in spans[:-1])}); "
                        f"{len(fps)} distinct argument set(s)."
                    ),
                    evidence={
                        "tool": tool,
                        "retries": retries,
                        "args_never_changed": args_never_changed,
                        "recovered": recovered,
                        "outcomes": [s.tool_outcome for s in spans],
                    },
                )
            )
        return out
