"""Things the model made up: tool names that don't exist, and answer claims no tool result supports."""

import re

from tracepin.baselines import Baselines
from tracepin.detectors.base import Finding, Severity, finding, register
from tracepin.models import Trace

NEAR_MISS_DISTANCE = 3


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def nearest_tool(name: str, registry: list[str]) -> tuple[str | None, int]:
    best, dist = None, 10**9
    for r in registry:
        d = levenshtein(name, r)
        if d < dist:
            best, dist = r, d
    return best, dist


class _UnknownToolBase:
    near_miss: bool

    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]:
        registry = trace.tools_offered
        system_prompt = next((c.system_instructions for c in trace.chats if c.system_instructions), None)
        out = []
        for s in trace.tool_calls:
            if s.tool_outcome != "unknown_tool" or not s.tool_name:
                continue
            nearest, dist = nearest_tool(s.tool_name, registry)
            if (dist <= NEAR_MISS_DISTANCE) != self.near_miss:
                continue
            # Prompt drift: the system prompt still advertises a tool that was removed.
            in_prompt = bool(system_prompt) and re.search(rf"\b{re.escape(s.tool_name)}\b", system_prompt) is not None
            out.append(
                finding(
                    self.id, trace, [s],
                    severity=Severity.HIGH,
                    confidence=1.0,
                    title=self.title(s.tool_name, nearest, dist, in_prompt),
                    detail=self.detail(s.tool_name, nearest, dist, in_prompt, registry),
                    evidence={
                        "tool": s.tool_name,
                        "nearest_registered": nearest,
                        "distance": dist,
                        "advertised_in_system_prompt": in_prompt if system_prompt else None,
                        "arguments": s.tool_arguments,
                    },
                )
            )
        return out


@register
class UnknownToolName(_UnknownToolBase):
    id = "tool.unknown_name"
    description = "Call to a tool that is not in the registry and is not close to any real name (invented)."
    near_miss = False

    def title(self, name, nearest, dist, in_prompt):
        return f"called non-existent tool {name}" + (" — still advertised in the system prompt" if in_prompt else "")

    def detail(self, name, nearest, dist, in_prompt, registry):
        src = "The system prompt lists it, so this is prompt drift, not model invention." if in_prompt else \
              "Not mentioned anywhere the model could see; the model invented it."
        return f"{name!r} is not registered (registry: {registry}); nearest is {nearest!r} at distance {dist}. {src}"


@register
class ConfusableToolName(_UnknownToolBase):
    id = "tool.confusable_name"
    description = "Call to an unregistered tool whose name is within edit distance 3 of a real one (near-miss)."
    near_miss = True

    def title(self, name, nearest, dist, in_prompt):
        return f"called {name}, probably meant {nearest} (distance {dist})"

    def detail(self, name, nearest, dist, in_prompt, registry):
        return (f"{name!r} is not registered but is {dist} edit(s) from {nearest!r}. "
                f"A confusable pair: rename one of them or merge, don't fix the prompt.")


# --- answer.unsupported --------------------------------------------------------------

# Double/curly quotes only: apostrophes ("I'm sorry, I couldn't") are not quotations.
_QUOTED = re.compile(r"[\"“”]([^\"“”]{2,80})[\"“”]")
_DIGITS = re.compile(r"\d[\d,]*\.?\d*")


def extract_claims(text: str) -> list[str]:
    claims = [m.group(1).strip() for m in _QUOTED.finditer(text)]
    claims += [m.group(0).rstrip(".").replace(",", "") for m in _DIGITS.finditer(text)]
    return [c for c in dict.fromkeys(claims) if len(c) >= 2]  # dedupe, keep order


def _norm(s: str) -> str:
    return s.replace(",", "").lower()


@register
class AnswerUnsupported:
    id = "answer.unsupported"
    description = ("Heuristic: quoted strings / numbers in the final answer that appear in no tool result "
                   "and not in the user's prompt. confidence=0.5 by design.")

    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]:
        if trace.stop_reason != "answered" or not trace.final_answer:
            return []
        results = [s for s in trace.tool_calls if s.tool_outcome == "ok" and s.tool_result]
        if not results:
            return []  # nothing to compare against; refusals and pure-chat answers are out of scope
        corpus = _norm(" ".join(s.tool_result or "" for s in results) + " " + trace.task_prompt)
        unsupported = [c for c in extract_claims(trace.final_answer) if _norm(c) not in corpus]
        if not unsupported:
            return []
        return [
            finding(
                self.id, trace, [trace.root, *results],
                severity=Severity.MEDIUM,
                confidence=0.5,
                title=f"final answer contains {len(unsupported)} claim(s) not found in any tool result",
                detail=f"Unsupported: {unsupported[:5]}. Answer: {trace.final_answer[:160]!r}",
                evidence={"unsupported_claims": unsupported, "tool_results_checked": len(results)},
            )
        ]


# --- answer.phantom_action -------------------------------------------------------------

_MUTATING = r"(updat|cancel|sen[dt]|creat|delet|remov|process|refund|issu|submit|chang|schedul|reset|escalat|open|clos)"
_CLAIMS_ACTION = re.compile(
    rf"\b(?:has been|have been|was|were|is now|are now|successfully|I(?:'ve| have)|I(?:'ve| have) (?:just |now )?)\s*"
    rf"(?:been )?(?:successfully )?{_MUTATING}\w*",
    re.IGNORECASE,
)
_TOOL_MUTATES = re.compile(_MUTATING, re.IGNORECASE)


@register
class PhantomAction:
    id = "answer.phantom_action"
    description = ("Final answer claims a state-changing action was performed (updated, cancelled, sent, created…) "
                   "but no successful call to a tool with a mutating name exists in the trace. Heuristic.")

    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]:
        if trace.stop_reason != "answered":
            return []
        m = _CLAIMS_ACTION.search(trace.final_answer)
        if not m:
            return []
        mutating_ok = [s for s in trace.tool_calls if s.tool_outcome == "ok" and _TOOL_MUTATES.search(s.tool_name or "")]
        if mutating_ok:
            return []
        # "Order X has been cancelled" when a tool returned status=cancelled is reported state, not a claimed action.
        stem = _TOOL_MUTATES.search(m.group(0)).group(0).lower()
        results = " ".join(s.tool_result or "" for s in trace.tool_calls if s.tool_outcome == "ok").lower()
        if stem in results:
            return []
        rejected = [s for s in trace.tool_calls if s.tool_outcome in ("unknown_tool", "invalid_arguments", "exception")]
        return [
            finding(
                self.id, trace, [trace.root, *rejected],
                severity=Severity.HIGH,
                confidence=0.75 if rejected else 0.6,
                title=f"answer claims an action happened ({m.group(0).strip()!r}) but no tool performed one",
                detail=f"Tools that ran OK: {sorted({s.tool_name for s in trace.tool_calls if s.tool_outcome == 'ok'}) or 'none'}; "
                f"{len(rejected)} rejected/failed call(s). Answer: {trace.final_answer[:160]!r}",
                evidence={"claim": m.group(0).strip(), "ok_tools": sorted({s.tool_name or '' for s in trace.tool_calls if s.tool_outcome == 'ok'}),
                          "rejected_calls": [s.tool_name for s in rejected], "task_passed": trace.success},
            )
        ]
