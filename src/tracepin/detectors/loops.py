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


NEAR_DUP_MIN = 4
NEAR_DUP_MAX_DIFF = 1  # leaf paths that may differ between consecutive calls


def _flatten(value, prefix: str = "") -> dict[str, str]:
    """{'query.filters.category': 'api', 'query.terms[0]': 'x'} — leaf paths of the args."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    if isinstance(value, list):
        out = {}
        for i, v in enumerate(value):
            out.update(_flatten(v, f"{prefix}[{i}]"))
        return out
    return {prefix: str(value).strip()}


@register
class NearDuplicate:
    id = "loop.near_duplicate"
    description = ("Same tool ≥4 times with arguments that differ in at most one leaf value per step: "
                   "the agent is guessing one field (a category, a page) rather than learning from results.")

    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]:
        by_tool: dict[str, list] = defaultdict(list)
        for s in trace.tool_calls:
            if s.tool_name:
                by_tool[s.tool_name].append(s)

        out = []
        for tool, all_spans in by_tool.items():
            if len(all_spans) < NEAR_DUP_MIN:
                continue
            # Longest run of consecutive calls where each step changes at most one leaf.
            # The run usually starts after a couple of shape-guessing rejections.
            all_flats = [_flatten(s.tool_arguments) for s in all_spans]
            best_start, best_len, start = 0, 1, 0
            for i in range(1, len(all_spans)):
                a, b = all_flats[i - 1], all_flats[i]
                if len({p for p in set(a) | set(b) if a.get(p) != b.get(p)}) > NEAR_DUP_MAX_DIFF:
                    start = i
                if i - start + 1 > best_len:
                    best_start, best_len = start, i - start + 1
            spans = all_spans[best_start : best_start + best_len]
            flats = all_flats[best_start : best_start + best_len]
            if len(spans) < NEAR_DUP_MIN or len({fingerprint(s) for s in spans}) == 1:
                continue  # too short, or byte-identical (repeated_call owns that)
            changed = [{p for p in set(a) | set(b) if a.get(p) != b.get(p)} for a, b in zip(flats, flats[1:])]
            varying = sorted({p for c in changed for p in c})
            tried = {p: [f.get(p) for f in flats] for p in varying}
            empty = sum(1 for s in spans if s.tool_outcome == "ok" and s.tool_result in ("[]", "{}", "null", ""))
            failed = sum(1 for s in spans if s.is_error)
            fruitless = empty + failed == len(spans)
            ok_results = [s.tool_result for s in spans if s.tool_outcome == "ok"]
            # A batch of distinct lookups that each returned something new is legitimate
            # (four different order ids, four different statuses). Only flag when the
            # calls were fruitless or kept returning the same thing.
            if not fruitless and len(set(ok_results)) == len(spans) and empty == 0:
                continue
            out.append(
                finding(
                    self.id, trace, spans,
                    severity=Severity.HIGH if fruitless else Severity.MEDIUM,
                    confidence=min(1.0, 0.6 + 0.1 * (len(spans) - NEAR_DUP_MIN)) + (0.1 if fruitless else 0),
                    title=f"{tool} called {len(spans)}× varying only {', '.join(varying) or 'nothing'}"
                    + (" — every call came back empty or failed" if fruitless else ""),
                    detail=f"Consecutive calls differ in ≤{NEAR_DUP_MAX_DIFF} argument; values tried: "
                    + "; ".join(f"{p}={v}" for p, v in tried.items())[:300]
                    + f". {empty} empty results, {failed} errors.",
                    evidence={"tool": tool, "count": len(spans), "varying_paths": varying, "values_tried": tried,
                              "empty_results": empty, "errors": failed, "fruitless": fruitless},
                )
            )
        return out
