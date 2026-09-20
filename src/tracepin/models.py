"""Typed trace model. Detectors work on these, never on raw span dicts."""

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ATTR_PREFIX = "tracepin"


def _iso_to_ns(ts: str) -> int:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1_000_000_000)


class Span(BaseModel):
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    start_ns: int
    end_ns: int
    status_code: Literal["UNSET", "OK", "ERROR"]
    status_message: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    events: list[dict] = Field(default_factory=list)

    @classmethod
    def from_json(cls, raw: dict) -> "Span":
        """Build from one `ReadableSpan.to_json()` object (one JSONL line)."""
        ctx = raw["context"]
        status = raw.get("status", {})
        return cls(
            trace_id=ctx["trace_id"],
            span_id=ctx["span_id"],
            parent_span_id=raw.get("parent_id"),
            name=raw["name"],
            start_ns=_iso_to_ns(raw["start_time"]),
            end_ns=_iso_to_ns(raw["end_time"]),
            status_code=status.get("status_code", "UNSET"),
            status_message=status.get("description"),
            attributes=raw.get("attributes", {}),
            events=raw.get("events", []),
        )

    @property
    def duration_ms(self) -> float:
        return (self.end_ns - self.start_ns) / 1_000_000

    @property
    def operation(self) -> str | None:
        return self.attributes.get("gen_ai.operation.name")

    @property
    def is_error(self) -> bool:
        return self.status_code == "ERROR"

    # -- execute_tool accessors ------------------------------------------------
    @property
    def tool_name(self) -> str | None:
        return self.attributes.get("gen_ai.tool.name")

    @property
    def tool_outcome(self) -> str | None:
        """ok | exception | invalid_arguments | unknown_tool | None (not a tool span)."""
        return self.attributes.get(f"{ATTR_PREFIX}.tool.outcome")

    @property
    def tool_arguments(self) -> Any:
        raw = self.attributes.get(f"{ATTR_PREFIX}.tool.arguments")
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return raw

    @property
    def tool_result(self) -> str | None:
        """Serialized result on success, the error text on failure."""
        if self.tool_outcome == "ok":
            return self.attributes.get(f"{ATTR_PREFIX}.tool.result")
        return self.attributes.get(f"{ATTR_PREFIX}.tool.rejection_reason") or self.status_message

    @property
    def rejection_reason(self) -> str | None:
        return self.attributes.get(f"{ATTR_PREFIX}.tool.rejection_reason")

    @property
    def code_location(self) -> dict | None:
        """{file, function, line} from code.* attrs (current or legacy semconv names)."""
        a = self.attributes
        path = a.get("code.file.path") or a.get("code.filepath")
        if not path:
            return None
        return {
            "file": path,
            "function": a.get("code.function.name") or a.get("code.function"),
            "line": a.get("code.line.number") or a.get("code.lineno"),
        }

    # -- chat accessors --------------------------------------------------------
    @property
    def input_tokens(self) -> int | None:
        return self.attributes.get("gen_ai.usage.input_tokens")

    @property
    def output_tokens(self) -> int | None:
        return self.attributes.get("gen_ai.usage.output_tokens")

    @property
    def model(self) -> str | None:
        return self.attributes.get("gen_ai.request.model")

    @property
    def tools_offered(self) -> list[str]:
        return list(self.attributes.get(f"{ATTR_PREFIX}.chat.tools_offered") or [])

    @property
    def system_instructions(self) -> str | None:
        """Only present when the run captured content (TRACEPIN_CAPTURE_CONTENT=1)."""
        for e in self.events:
            v = e.get("attributes", {}).get("gen_ai.system_instructions")
            if v:
                return v
        return None


class Trace(BaseModel):
    """One task execution: the invoke_agent root plus everything under it."""

    trace_id: str
    root: Span
    spans: list[Span]  # sorted by start_ns, root included

    @property
    def task_id(self) -> str:
        return str(self.root.attributes.get(f"{ATTR_PREFIX}.task.id", ""))

    @property
    def task_prompt(self) -> str:
        return str(self.root.attributes.get(f"{ATTR_PREFIX}.task.prompt", ""))

    @property
    def task_checker(self) -> str:
        return str(self.root.attributes.get(f"{ATTR_PREFIX}.task.checker", ""))

    @property
    def tags(self) -> list[str]:
        return list(self.root.attributes.get(f"{ATTR_PREFIX}.task.tags") or [])

    @property
    def success(self) -> bool:
        return bool(self.root.attributes.get(f"{ATTR_PREFIX}.run.success", False))

    @property
    def stop_reason(self) -> str:
        return str(self.root.attributes.get(f"{ATTR_PREFIX}.run.stop_reason", "unknown"))

    @property
    def iterations(self) -> int:
        return int(self.root.attributes.get(f"{ATTR_PREFIX}.run.iterations", 0))

    @property
    def final_answer(self) -> str:
        return str(self.root.attributes.get(f"{ATTR_PREFIX}.run.final_answer", ""))

    @property
    def duration_ms(self) -> float:
        return self.root.duration_ms

    def by_operation(self, op: str) -> list[Span]:
        """Spans of one operation type, sorted by start_ns."""
        return [s for s in self.spans if s.operation == op]

    @property
    def tool_calls(self) -> list[Span]:
        return self.by_operation("execute_tool")

    @property
    def chats(self) -> list[Span]:
        return self.by_operation("chat")

    def children(self, span_id: str) -> list[Span]:
        return [s for s in self.spans if s.parent_span_id == span_id]

    def get(self, span_id: str) -> Span | None:
        return next((s for s in self.spans if s.span_id == span_id), None)

    @property
    def total_input_tokens(self) -> int:
        return sum(c.input_tokens or 0 for c in self.chats)

    @property
    def total_output_tokens(self) -> int:
        return sum(c.output_tokens or 0 for c in self.chats)

    @property
    def tools_offered(self) -> list[str]:
        """The tool registry as the model saw it (from the first chat span)."""
        for c in self.chats:
            if c.tools_offered:
                return c.tools_offered
        return []


class Run(BaseModel):
    run_id: str
    model: str
    prompt_version: str
    git_sha: str
    traces: list[Trace]
    skipped_traces: int = 0  # traces dropped at load time (no root span)

    @property
    def task_count(self) -> int:
        return len(self.traces)

    @property
    def pass_count(self) -> int:
        return sum(1 for t in self.traces if t.success)

    def trace_by_task(self, task_id: str) -> Trace | None:
        return next((t for t in self.traces if t.task_id == task_id), None)
