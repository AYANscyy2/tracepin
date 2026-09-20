"""Run every detector over every trace of a Run and produce the findings document Day 3 reads."""

import json
import pathlib
from collections import Counter

from pydantic import BaseModel, Field

from tracepin.baselines import Baselines, build_baselines
from tracepin.detectors import DETECTORS, Finding, RunDetector
from tracepin.loading import load_run
from tracepin.models import Run, Trace

SCHEMA_VERSION = 1


class TraceSummary(BaseModel):
    """Enough about each trace for report/compare without re-reading the JSONL."""

    trace_id: str
    task_id: str
    task_prompt: str = ""
    tags: list[str]
    success: bool
    stop_reason: str
    iterations: int
    duration_ms: float
    input_tokens: int
    output_tokens: int
    final_answer: str
    timeline: list[dict]  # tool calls in order: span_id, tool, outcome, duration_ms, error
    finding_ids: list[str] = Field(default_factory=list)


class Analysis(BaseModel):
    schema_version: int = SCHEMA_VERSION
    run: dict
    baselines: dict
    traces: list[TraceSummary]
    findings: list[Finding]
    summary: dict

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=1)

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "Analysis":
        return cls.model_validate_json(pathlib.Path(path).read_text(encoding="utf-8"))


def _timeline(trace: Trace) -> list[dict]:
    return [
        {
            "span_id": s.span_id,
            "tool": s.tool_name,
            "outcome": s.tool_outcome,
            "duration_ms": round(s.duration_ms, 2),
            "error": " ".join((s.tool_result or "").split())[:160] if s.is_error else None,
            "arguments": str(s.tool_arguments)[:160],
        }
        for s in trace.tool_calls
    ]


def _fill_code_locations(run: Run, findings: list[Finding]) -> None:
    """Rejected calls carry no code.* attrs, but the *tool* they targeted usually ran
    elsewhere in the run. Point the finding at that function: it is where the fix goes
    (e.g. the docstring of a tool whose description keeps producing invalid args)."""
    locations: dict[str, dict] = {}
    for t in run.traces:
        for s in t.tool_calls:
            if s.tool_name and s.tool_name not in locations and s.code_location:
                locations[s.tool_name] = s.code_location
    for f in findings:
        if f.code_location is None:
            tool = f.evidence.get("tool") or f.evidence.get("failed_tool")
            if tool in locations:
                f.code_location = {**locations[tool], "inferred_from_tool": True}


def detect_trace(trace: Trace, baselines: Baselines) -> list[Finding]:
    out: list[Finding] = []
    for d in DETECTORS:
        out.extend(d.detect(trace, baselines))
    return out


def analyze_run(run: Run) -> Analysis:
    baselines = build_baselines(run)
    findings: list[Finding] = []
    summaries: list[TraceSummary] = []

    for trace in run.traces:
        fs = detect_trace(trace, baselines)
        findings.extend(fs)
        summaries.append(
            TraceSummary(
                trace_id=trace.trace_id,
                task_id=trace.task_id,
                task_prompt=trace.task_prompt,
                tags=trace.tags,
                success=trace.success,
                stop_reason=trace.stop_reason,
                iterations=trace.iterations,
                duration_ms=round(trace.duration_ms, 1),
                input_tokens=trace.total_input_tokens,
                output_tokens=trace.total_output_tokens,
                final_answer=trace.final_answer[:300],
                timeline=_timeline(trace),
                finding_ids=[f.detector_id for f in fs],
            )
        )
    for d in DETECTORS:
        if isinstance(d, RunDetector):
            findings.extend(d.detect_run(run, baselines))
    _fill_code_locations(run, findings)

    sev_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (sev_order[f.severity.value], -f.confidence, f.task_id))

    trace_findings = [f for f in findings if f.scope == "trace"]
    summary = {
        "total": len(findings),
        "by_detector": dict(Counter(f.detector_id for f in findings).most_common()),
        "by_severity": dict(Counter(f.severity.value for f in findings)),
        "traces_with_findings": len({f.trace_id for f in trace_findings}),
        "traces_with_high": len({f.trace_id for f in trace_findings if f.severity.value == "high"}),
        "detectors_run": [d.id for d in DETECTORS],
    }
    return Analysis(
        run={
            "run_id": run.run_id,
            "model": run.model,
            "prompt_version": run.prompt_version,
            "git_sha": run.git_sha,
            "task_count": run.task_count,
            "pass_count": run.pass_count,
            "skipped_traces": run.skipped_traces,
            "total_input_tokens": sum(t.total_input_tokens for t in run.traces),
            "total_output_tokens": sum(t.total_output_tokens for t in run.traces),
            "total_wall_ms": round(sum(t.duration_ms for t in run.traces), 1),
        },
        baselines=baselines.to_json(),
        traces=summaries,
        findings=findings,
        summary=summary,
    )


def analyze_file(trace_path: str, out_path: str | None = None) -> Analysis:
    run = load_run(trace_path)
    analysis = analyze_run(run)
    if out_path:
        p = pathlib.Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(analysis.to_json(), encoding="utf-8")
    return analysis
