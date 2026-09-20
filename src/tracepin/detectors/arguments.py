"""Argument-shape failures. The run-level inversion — one tool owning most invalid-arg
events means the *tool description* is the bug — is the most actionable finding here."""

import re
from collections import Counter, defaultdict

from tracepin.baselines import Baselines
from tracepin.detectors.base import Finding, Severity, finding, register
from tracepin.models import Run, Trace

DOMINANT_SHARE = 0.40

# pydantic v2 error codes: "[type=missing, ..." / "[type=int_parsing, ..." / "[type=list_type, ..."
_ERR_TYPE = re.compile(r"\[type=([a-z_]+)")
# Error block starts with the dotted field path on its own line.
_ERR_FIELD = re.compile(r"^([A-Za-z_][\w.]*)\n\s+(.+?) \[type=", re.MULTILINE)

TYPE_ERROR_SUFFIXES = ("_type", "_parsing", "_from_float")


def parse_validation_errors(reason: str | None) -> list[dict]:
    """[{field, message, type}] from a pydantic ValidationError string."""
    if not reason:
        return []
    out = []
    for m in _ERR_FIELD.finditer(reason):
        tail = reason[m.end() - len("[type=") :]
        t = _ERR_TYPE.match(tail)
        out.append({"field": m.group(1), "message": m.group(2), "type": t.group(1) if t else "unknown"})
    return out


def is_type_error(err_type: str) -> bool:
    return err_type != "missing" and err_type.endswith(TYPE_ERROR_SUFFIXES)


@register
class SchemaInvalid:
    id = "args.schema_invalid"
    description = "Tool call rejected by argument validation. Run-level: one tool dominating → fix its description."

    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]:
        by_tool: dict[str, list] = defaultdict(list)
        for s in trace.tool_calls:
            if s.tool_outcome == "invalid_arguments" and s.tool_name:
                by_tool[s.tool_name].append(s)

        out = []
        for tool, spans in by_tool.items():
            errs = [parse_validation_errors(s.rejection_reason) for s in spans]
            fields = Counter(e["field"] for es in errs for e in es)
            out.append(
                finding(
                    self.id, trace, spans,
                    severity=Severity.MEDIUM if len(spans) < 3 else Severity.HIGH,
                    confidence=1.0,
                    title=f"{tool} rejected {len(spans)}× by schema validation",
                    detail=f"Failing fields: {dict(fields)}. Arguments tried: "
                    + "; ".join(str(s.tool_arguments)[:120] for s in spans[:3]),
                    evidence={"tool": tool, "count": len(spans), "fields": dict(fields), "errors": errs},
                )
            )
        return out

    def detect_run(self, run: Run, baselines: Baselines) -> list[Finding]:
        per_tool: Counter = Counter()
        spans_by_tool: dict[str, list] = defaultdict(list)
        traces_by_tool: dict[str, set] = defaultdict(set)
        for t in run.traces:
            for s in t.tool_calls:
                if s.tool_outcome == "invalid_arguments" and s.tool_name:
                    per_tool[s.tool_name] += 1
                    spans_by_tool[s.tool_name].append(s)
                    traces_by_tool[s.tool_name].add(t.task_id)
        total = sum(per_tool.values())
        if not total:
            return []
        tool, n = per_tool.most_common(1)[0]
        share = n / total
        if share <= DOMINANT_SHARE:
            return []
        fields = Counter(
            e["field"] for s in spans_by_tool[tool] for e in parse_validation_errors(s.rejection_reason)
        )
        return [
            Finding(
                detector_id=self.id,
                scope="run",
                severity=Severity.HIGH,
                confidence=min(1.0, share),
                trace_id="",
                task_id="*",
                span_ids=[s.span_id for s in spans_by_tool[tool]],
                title=f"{tool} accounts for {share:.0%} of all invalid-argument events — the tool description is the bug",
                detail=(
                    f"{n} of {total} schema rejections across the run hit {tool}, in {len(traces_by_tool[tool])} tasks. "
                    f"The model keeps guessing the same wrong shapes (missing: {dict(fields)}). "
                    f"When one tool dominates, the fix is its docstring/schema, not the model."
                ),
                evidence={"tool": tool, "count": n, "total": total, "share": round(share, 3),
                          "fields": dict(fields), "tasks": sorted(traces_by_tool[tool])},
                code_location=None,
            )
        ]


@register
class TypeConfusion:
    id = "args.type_confusion"
    description = "Argument present but of the wrong JSON type (string where int/list expected)."

    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]:
        out = []
        for s in trace.tool_calls:
            if s.tool_outcome != "invalid_arguments":
                continue
            type_errs = [e for e in parse_validation_errors(s.rejection_reason) if is_type_error(e["type"])]
            if not type_errs:
                continue
            desc = "; ".join(f"{e['field']}: {e['message']}" for e in type_errs)
            out.append(
                finding(
                    self.id, trace, [s],
                    severity=Severity.MEDIUM,
                    confidence=0.95,
                    title=f"{s.tool_name} called with wrong-typed argument ({type_errs[0]['field']})",
                    detail=f"{desc}. Arguments: {str(s.tool_arguments)[:200]}",
                    evidence={"tool": s.tool_name, "errors": type_errs, "arguments": s.tool_arguments},
                )
            )
        return out
