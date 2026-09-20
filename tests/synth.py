"""Builds JSONL spans in the exact shape the Day 1 exporter writes, for fixtures.

Fixtures are synthetic on purpose: each one isolates one detector's trigger and its
documented false-positive case, with numbers lifted from real Day 1 spans.
"""

import json
import pathlib
from datetime import datetime, timedelta, timezone

TOOL_CODE = {
    "get_user": ("src/tracepin/tools/users.py", "get_user", 29),
    "fetch_user": ("src/tracepin/tools/users.py", "fetch_user", 37),
    "fetch_order_status": ("src/tracepin/tools/flaky.py", "fetch_order_status", 44),
    "search_kb": ("src/tracepin/tools/docs.py", "search_kb", 36),
    "calculate": ("src/tracepin/tools/calc.py", "calculate", 35),
}
REGISTRY = list(TOOL_CODE)
T0 = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class TraceBuilder:
    _counter = 0

    def __init__(self, task_id: str, *, prompt: str = "", checker: str = "contains", tags=(), start: datetime = T0):
        TraceBuilder._counter += 1
        self.trace_id = f"0x{TraceBuilder._counter:032x}"
        self.root_id = self._sid()
        self.task_id, self.prompt, self.checker, self.tags = task_id, prompt, checker, list(tags)
        self.clock = start
        self.start = start
        self.spans: list[dict] = []
        self.iteration = 0

    def _sid(self) -> str:
        TraceBuilder._counter += 1
        return f"0x{TraceBuilder._counter:016x}"

    def _span(self, name, kind, attrs, duration_ms, status="UNSET", description=None, events=None, parent=None):
        start = self.clock
        end = start + timedelta(milliseconds=duration_ms)
        self.clock = end + timedelta(milliseconds=1)
        sid = self._sid()
        status_block = {"status_code": status}
        if description:
            status_block["description"] = description
        self.spans.append({
            "name": name,
            "context": {"trace_id": self.trace_id, "span_id": sid, "trace_state": "[]"},
            "kind": kind,
            "parent_id": parent if parent is not None else self.root_id,
            "start_time": _ts(start), "end_time": _ts(end),
            "status": status_block,
            "attributes": attrs, "events": events or [], "links": [],
        })
        return sid

    def chat(self, input_tokens=300, output_tokens=20, duration_ms=1500.0, tool_calls=(), system=None,
             transport_retries=0):
        attrs = {
            "gen_ai.operation.name": "chat", "gen_ai.system": "gemini",
            "gen_ai.request.model": "gemini-3.1-flash-lite", "gen_ai.request.temperature": 0.0,
            "tracepin.chat.iteration": self.iteration, "tracepin.chat.tools_offered": REGISTRY,
            "tracepin.chat.transport_retries": transport_retries,
            "gen_ai.usage.input_tokens": input_tokens, "gen_ai.usage.output_tokens": output_tokens,
            "gen_ai.response.finish_reasons": ["STOP"],
            "tracepin.chat.tool_calls_requested": list(tool_calls),
        }
        events = []
        if system:
            events.append({"name": "gen_ai.client.inference.operation.details", "timestamp": _ts(self.clock),
                           "attributes": {"gen_ai.system_instructions": system, "gen_ai.input.messages": "[]"}})
        self.iteration += 1
        return self._span("chat gemini-3.1-flash-lite", "SpanKind.CLIENT", attrs, duration_ms, events=events)

    def tool(self, name, args, outcome="ok", result=None, error=None, duration_ms=0.1):
        attrs = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name,
                 "tracepin.tool.arguments": json.dumps(args), "tracepin.tool.outcome": outcome}
        status, desc = "UNSET", None
        if outcome in ("ok", "exception"):
            f, fn, line = TOOL_CODE[name]
            attrs.update({"gen_ai.tool.type": "function", "gen_ai.tool.call.id": self._sid(),
                          "code.file.path": f, "code.function.name": fn, "code.line.number": line,
                          "code.filepath": f, "code.function": fn, "code.lineno": line})
        if outcome == "ok":
            attrs["tracepin.tool.result"] = json.dumps(result if result is not None else {"ok": True})
        elif outcome == "exception":
            status, desc = "ERROR", error or "UpstreamError: HTTP 500: order service unavailable"
        else:  # invalid_arguments | unknown_tool
            status = "ERROR"
            desc = error or (f"tool '{name}' not in registry" if outcome == "unknown_tool"
                             else "1 validation error for Args\nfield\n  Field required [type=missing, input_value={}, input_type=dict]")
            attrs["tracepin.tool.rejection_reason"] = desc
        return self._span(f"execute_tool {name}", "SpanKind.INTERNAL", attrs, duration_ms, status, desc)

    def finish(self, stop_reason="answered", success=True, final_answer="done"):
        attrs = {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "tracepin",
                 "tracepin.task.id": self.task_id, "tracepin.task.prompt": self.prompt,
                 "tracepin.task.expected": "", "tracepin.task.checker": self.checker, "tracepin.task.tags": self.tags,
                 "tracepin.run.iterations": self.iteration, "tracepin.run.stop_reason": stop_reason,
                 "tracepin.run.success": success, "tracepin.run.final_answer": final_answer}
        end = self.clock
        sid = self.root_id
        self.spans.append({
            "name": "invoke_agent tracepin",
            "context": {"trace_id": self.trace_id, "span_id": sid, "trace_state": "[]"},
            "kind": "SpanKind.INTERNAL", "parent_id": None,
            "start_time": _ts(self.start), "end_time": _ts(end),
            "status": {"status_code": "UNSET"}, "attributes": attrs, "events": [], "links": [],
        })
        return self


RESOURCE = {"attributes": {"service.name": "tracepin-agent", "vcs.repository.ref.revision": "fixture",
                           "tracepin.run.id": "fixture", "tracepin.prompt.version": "fixture",
                           "gen_ai.request.model": "gemini-3.1-flash-lite"}, "schema_url": ""}


def write_fixture(path: str | pathlib.Path, traces: list[TraceBuilder]) -> None:
    lines = [json.dumps({**s, "resource": RESOURCE}) for t in traces for s in t.spans]
    pathlib.Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
