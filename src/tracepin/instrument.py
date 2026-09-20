"""@traced_tool decorator and the recorder for tool calls that never reach a function."""

import functools
import inspect
import json
import uuid

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

tracer = trace.get_tracer("tracepin")

MAX_ATTR = 4000


def _code_attrs(fn) -> dict:
    """Emit both the current and legacy code.* semconv names.

    The code semconv was renamed (code.filepath -> code.file.path etc). Emitting both
    is three extra lines and means any downstream tool resolves it either way.
    """
    try:
        target = inspect.unwrap(fn)
        path = inspect.getsourcefile(target)
        lineno = inspect.getsourcelines(target)[1]
        qualname = target.__qualname__
    except Exception:
        return {}
    return {
        "code.file.path": path,
        "code.function.name": qualname,
        "code.line.number": lineno,
        "code.filepath": path,
        "code.function": qualname,
        "code.lineno": lineno,
    }


def _clip(value) -> str:
    return json.dumps(value, default=str)[:MAX_ATTR]


def traced_tool(name: str | None = None, description: str | None = None):
    def decorator(fn):
        tool_name = name or fn.__name__
        code = _code_attrs(fn)

        @functools.wraps(fn)
        def wrapper(*args, _call_id: str | None = None, **kwargs):
            with tracer.start_as_current_span(
                f"execute_tool {tool_name}", kind=SpanKind.INTERNAL
            ) as span:
                span.set_attribute("gen_ai.operation.name", "execute_tool")
                span.set_attribute("gen_ai.tool.name", tool_name)
                span.set_attribute("gen_ai.tool.type", "function")
                span.set_attribute("gen_ai.tool.call.id", _call_id or uuid.uuid4().hex)
                if description:
                    span.set_attribute("gen_ai.tool.description", description)
                for k, v in code.items():
                    span.set_attribute(k, v)
                span.set_attribute("tracepin.tool.arguments", _clip(kwargs))

                try:
                    result = fn(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    span.set_attribute("tracepin.tool.outcome", "exception")
                    raise

                span.set_attribute("tracepin.tool.result", _clip(result))
                span.set_attribute("tracepin.tool.outcome", "ok")
                return result

        wrapper.__tracepin_tool_name__ = tool_name
        wrapper.__tracepin_description__ = description or (fn.__doc__ or "").strip()
        return wrapper

    return decorator


def record_rejected_call(tool_name: str, raw_arguments, reason: str, outcome: str):
    """Span for a tool call the agent could not execute.

    outcome is one of: unknown_tool | invalid_arguments | unparseable
    These never reach a real function, so they get no code.* attributes — that absence
    is itself the Day 2 signal for a hallucinated tool name.
    """
    with tracer.start_as_current_span(
        f"execute_tool {tool_name}", kind=SpanKind.INTERNAL
    ) as span:
        span.set_attribute("gen_ai.operation.name", "execute_tool")
        span.set_attribute("gen_ai.tool.name", tool_name)
        span.set_attribute("tracepin.tool.arguments", _clip(raw_arguments))
        span.set_attribute("tracepin.tool.outcome", outcome)
        span.set_attribute("tracepin.tool.rejection_reason", reason)
        span.set_status(Status(StatusCode.ERROR, reason))
