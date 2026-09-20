"""The detector contract. Day 3 reads Finding straight from the findings JSON."""

import json
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from tracepin.baselines import Baselines
from tracepin.models import Run, Span, Trace


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Finding(BaseModel):
    detector_id: str  # "loop.repeated_call"
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)  # how sure, before a human looks
    trace_id: str  # "" for run-level findings
    task_id: str  # "*" for run-level findings
    span_ids: list[str]  # every span that constitutes the evidence
    title: str  # one line, human readable
    detail: str  # what happened, with numbers
    evidence: dict[str, Any] = Field(default_factory=dict)  # structured, machine-readable
    code_location: dict | None = None  # {file, function, line} lifted from code.* attrs
    scope: Literal["trace", "run"] = "trace"


@runtime_checkable
class Detector(Protocol):
    id: str
    description: str

    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]: ...


@runtime_checkable
class RunDetector(Protocol):
    """Optional second pass: findings that only make sense across the whole run."""

    def detect_run(self, run: Run, baselines: Baselines) -> list[Finding]: ...


DETECTORS: list[Detector] = []


def register(cls):
    """Class decorator. Adding a detector = one class with this decorator + one import."""
    inst = cls()
    if any(d.id == inst.id for d in DETECTORS):
        raise ValueError(f"duplicate detector id {inst.id!r}")
    DETECTORS.append(inst)
    return cls


# --- helpers shared by detectors --------------------------------------------------


def _strip(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def canonical_args(args) -> str:
    """Stable string for a call's arguments. Strips whitespace but never lowercases:
    lowercasing would merge genuinely different calls."""
    return json.dumps(_strip(args), sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(span: Span) -> str:
    return f"{span.tool_name}({canonical_args(span.tool_arguments)})"


def code_location_of(*spans: Span) -> dict | None:
    """First populated code.* location among the spans (rejected calls have none)."""
    for s in spans:
        loc = s.code_location
        if loc:
            return loc
    return None


def finding(
    detector_id: str,
    trace: Trace,
    spans: list[Span],
    *,
    severity: Severity,
    confidence: float,
    title: str,
    detail: str,
    evidence: dict | None = None,
) -> Finding:
    return Finding(
        detector_id=detector_id,
        severity=severity,
        confidence=round(min(1.0, max(0.0, confidence)), 3),
        trace_id=trace.trace_id,
        task_id=trace.task_id,
        span_ids=[s.span_id for s in spans],
        title=title,
        detail=detail,
        evidence=evidence or {},
        code_location=code_location_of(*spans),
    )
