"""Thin Gemini client. One `chat {model}` span per call, token counts always recorded."""

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field

from google import genai
from google.genai import errors, types
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

tracer = trace.get_tracer("tracepin")

# Free-tier quota is 15 req/min. 429s are transport noise, not agent behaviour, so they
# are retried inside the chat span and counted in tracepin.chat.transport_retries.
MAX_TRANSPORT_RETRIES = 4

CAPTURE_CONTENT = os.environ.get("TRACEPIN_CAPTURE_CONTENT", "0") == "1"
MAX_CONTENT_ATTR = 8000


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class ChatResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    content: types.Content | None = None  # model turn, appended to history verbatim


def _content_to_dict(c: types.Content) -> dict:
    parts = []
    for p in c.parts or []:
        if p.text is not None:
            parts.append({"text": p.text})
        elif p.function_call is not None:
            parts.append({"function_call": {"name": p.function_call.name, "args": p.function_call.args}})
        elif p.function_response is not None:
            parts.append({"function_response": {"name": p.function_response.name, "response": p.function_response.response}})
    return {"role": c.role, "parts": parts}


def _retry_delay(message: str) -> float:
    m = re.search(r"retryDelay['\"]?: ['\"]?(\d+)s", message)
    return float(m.group(1)) + 1 if m else 20.0


class LLM:
    def __init__(self, model: str, system_prompt: str, tool_schemas: list[dict], temperature: float = 0.0):
        self.model = model
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.client = genai.Client(
            api_key=os.environ["GEMINI_API_KEY"],
            # A hung request must fail loudly, not block the run forever.
            http_options=types.HttpOptions(timeout=60_000),
        )
        self.tools = [
            types.Tool(function_declarations=[types.FunctionDeclaration(**s) for s in tool_schemas])
        ]
        self.tool_names = [s["name"] for s in tool_schemas]

    def chat(self, history: list[types.Content], iteration: int) -> ChatResponse:
        with tracer.start_as_current_span(f"chat {self.model}", kind=SpanKind.CLIENT) as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.system", "gemini")
            span.set_attribute("gen_ai.request.model", self.model)
            span.set_attribute("gen_ai.request.temperature", self.temperature)
            span.set_attribute("tracepin.chat.iteration", iteration)
            span.set_attribute("tracepin.chat.tools_offered", self.tool_names)
            if CAPTURE_CONTENT:
                span.add_event(
                    "gen_ai.client.inference.operation.details",
                    {
                        "gen_ai.system_instructions": self.system_prompt[:MAX_CONTENT_ATTR],
                        "gen_ai.input.messages": json.dumps(
                            [_content_to_dict(c) for c in history], default=str
                        )[:MAX_CONTENT_ATTR],
                    },
                )

            config = types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                temperature=self.temperature,
                tools=self.tools,
                # No automatic function calling: the agent loop owns dispatch.
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )
            retries = 0
            try:
                while True:
                    try:
                        resp = self.client.models.generate_content(
                            model=self.model, contents=history, config=config
                        )
                        break
                    except errors.ClientError as exc:
                        if exc.code != 429 or retries >= MAX_TRANSPORT_RETRIES:
                            raise
                        retries += 1
                        delay = _retry_delay(str(exc))
                        span.add_event("tracepin.transport.retry", {"attempt": retries, "delay_s": delay})
                        time.sleep(delay)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
            finally:
                span.set_attribute("tracepin.chat.transport_retries", retries)

            usage = resp.usage_metadata
            if usage is not None:
                span.set_attribute("gen_ai.usage.input_tokens", usage.prompt_token_count or 0)
                span.set_attribute("gen_ai.usage.output_tokens", usage.candidates_token_count or 0)
            if resp.model_version:
                span.set_attribute("gen_ai.response.model", resp.model_version)
            if resp.response_id:
                span.set_attribute("gen_ai.response.id", resp.response_id)

            cand = resp.candidates[0] if resp.candidates else None
            finish = [str(cand.finish_reason.name if cand and cand.finish_reason else "unknown")]
            span.set_attribute("gen_ai.response.finish_reasons", finish)

            text_parts: list[str] = []
            calls: list[ToolCall] = []
            content = cand.content if cand else None
            for p in (content.parts if content and content.parts else []):
                if p.function_call is not None:
                    calls.append(
                        ToolCall(
                            id=p.function_call.id or uuid.uuid4().hex[:12],
                            name=p.function_call.name or "",
                            args=dict(p.function_call.args or {}),
                        )
                    )
                elif p.text:
                    text_parts.append(p.text)

            text = "".join(text_parts)
            span.set_attribute("tracepin.chat.tool_calls_requested", [c.name for c in calls])
            if CAPTURE_CONTENT:
                span.add_event(
                    "gen_ai.choice",
                    {
                        "gen_ai.output.messages": json.dumps(
                            _content_to_dict(content) if content else {}, default=str
                        )[:MAX_CONTENT_ATTR]
                    },
                )
            return ChatResponse(text=text, tool_calls=calls, content=content)
