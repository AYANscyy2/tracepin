"""The agent loop. Hand-rolled so every span boundary is explicit."""

import json
from dataclasses import dataclass

from google.genai import types
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from pydantic import ValidationError

from tracepin.instrument import record_rejected_call
from tracepin.llm import LLM
from tracepin.tasks import Task, check
from tracepin.tools import MODELS, TOOLS
from tracepin.tools import flaky

tracer = trace.get_tracer("tracepin")

MAX_ITERATIONS = 8


@dataclass
class RunResult:
    task_id: str
    success: bool
    stop_reason: str
    iterations: int
    final_answer: str


def _tool_result_part(name: str, payload: dict) -> types.Part:
    return types.Part.from_function_response(name=name, response=payload)


def run_task(llm: LLM, task: Task) -> RunResult:
    flaky.current_task_id.set(task.id)
    with tracer.start_as_current_span("invoke_agent tracepin", kind=SpanKind.INTERNAL) as root:
        root.set_attribute("gen_ai.operation.name", "invoke_agent")
        root.set_attribute("gen_ai.agent.name", "tracepin")
        root.set_attribute("tracepin.task.id", task.id)
        root.set_attribute("tracepin.task.prompt", task.prompt)
        root.set_attribute("tracepin.task.expected", task.expected)
        root.set_attribute("tracepin.task.checker", task.checker)
        root.set_attribute("tracepin.task.tags", task.tags)

        history: list[types.Content] = [
            types.Content(role="user", parts=[types.Part.from_text(text=task.prompt)])
        ]
        final_answer = ""
        stop_reason = "max_iterations"
        iterations = 0

        try:
            for i in range(MAX_ITERATIONS):
                iterations = i + 1
                response = llm.chat(history, iteration=i)
                if response.content is not None:
                    history.append(response.content)
                final_answer = response.text or final_answer

                if not response.tool_calls:
                    stop_reason = "answered"
                    break

                result_parts: list[types.Part] = []
                for call in response.tool_calls:
                    if call.name not in TOOLS:
                        reason = f"tool '{call.name}' not in registry"
                        record_rejected_call(call.name, call.args, reason, "unknown_tool")
                        result_parts.append(_tool_result_part(call.name, {"error": reason}))
                        continue
                    try:
                        args = MODELS[call.name].model_validate(call.args)
                    except ValidationError as e:
                        record_rejected_call(call.name, call.args, str(e), "invalid_arguments")
                        result_parts.append(_tool_result_part(call.name, {"error": str(e)}))
                        continue
                    try:
                        result = TOOLS[call.name](**args.model_dump(mode="json"), _call_id=call.id)
                    except Exception as e:
                        result = {"error": f"{type(e).__name__}: {e}"}  # span already recorded it
                    if not isinstance(result, dict):
                        result = {"result": result}
                    result_parts.append(_tool_result_part(call.name, result))

                history.append(types.Content(role="user", parts=result_parts))
        except Exception as exc:
            stop_reason = "fatal_error"
            root.record_exception(exc)
            root.set_status(Status(StatusCode.ERROR, str(exc)))

        success = stop_reason != "fatal_error" and check(task, final_answer)
        root.set_attribute("tracepin.run.iterations", iterations)
        root.set_attribute("tracepin.run.stop_reason", stop_reason)
        root.set_attribute("tracepin.run.success", success)
        root.set_attribute("tracepin.run.final_answer", final_answer[:4000])
        return RunResult(task.id, success, stop_reason, iterations, final_answer)
