# Day 1 Spec — Agent Reliability Toolkit (`arkit`)

Goal for today: a tool-calling agent, fully instrumented with real OpenTelemetry spans,
run against 40 seeded tasks, producing a trace file that contains **real, unforced failures**.

No detectors today. No UI today. Today is: emit correct spans, capture real bugs.

> Rename `arkit` to the final project name before starting — it appears in the package
> name, the tracer name, and every custom attribute prefix.

---

## 0. Non-negotiables

These three decisions make Day 2 and Day 3 easy. Skipping any of them costs hours later.

1. **Every tool span carries `code.*` attributes** pointing at the function that ran.
   Day 3's root-cause step is then "read attribute, open file, print lines" instead of a
   whole mapping layer.
2. **Failed tool calls are emitted as spans, not swallowed.** A hallucinated tool name, a
   schema-invalid argument blob, a tool that raises — all produce a span with an error
   status. If the agent loop silently rejects a bad tool name, Day 2 has nothing to detect.
3. **Resource attributes identify the run**: model, prompt version, git SHA, run id.
   That's the join key for Day 2's regression layer.

---

## 1. Repo layout

```
arkit/
  pyproject.toml
  docker-compose.yml          # jaeger all-in-one, for screenshots
  .env.example
  src/arkit/
    __init__.py
    tracing.py                # provider, resource, exporters
    instrument.py             # @traced_tool, failure recorders
    llm.py                    # thin model client + chat span
    agent.py                  # the loop
    tools/
      __init__.py             # TOOLS registry
      users.py                # get_user / fetch_user  (confusable pair)
      flaky.py                # fetch_order_status     (20% 500s)
      docs.py                 # search_kb              (nasty nested schema)
      calc.py                 # calculate
    tasks.py                  # task loading + checkers
    runner.py                 # CLI: run all tasks, write traces
  tasks/tasks.jsonl
  traces/                     # gitignored, except traces/sample.jsonl
```

Python 3.11+, `uv` or plain venv. Deps:

```
opentelemetry-api
opentelemetry-sdk
opentelemetry-exporter-otlp-proto-http
pydantic>=2
google-genai          # or openai, pick one and stay
python-dotenv
typer
rich
```

Do **not** add LangGraph. Hand-rolled loop is ~120 lines and you control exactly what gets
spanned. Framework auto-instrumentation would hide the thing you're demonstrating.

---

## 2. `tracing.py`

```python
import pathlib
import subprocess
import threading
import uuid

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unknown"


class JSONLSpanExporter(SpanExporter):
    """One JSON object per span per line. This is what the Day 2 detectors read."""

    def __init__(self, path: str):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def export(self, spans):
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            for span in spans:
                f.write(span.to_json(indent=None) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self):
        return None


def setup_tracing(
    *,
    model: str,
    prompt_version: str,
    jsonl_path: str,
    run_id: str | None = None,
    otlp: bool = True,
) -> str:
    run_id = run_id or uuid.uuid4().hex[:12]

    resource = Resource.create(
        {
            "service.name": "arkit-agent",
            "service.version": "0.1.0",
            "vcs.repository.ref.revision": _git_sha(),
            "arkit.run.id": run_id,
            "arkit.prompt.version": prompt_version,
            "gen_ai.request.model": model,
        }
    )

    provider = TracerProvider(resource=resource)
    # Simple (not Batch) for the JSONL exporter: ordering matters for the detectors
    # and run volume is tiny.
    provider.add_span_processor(SimpleSpanProcessor(JSONLSpanExporter(jsonl_path)))
    if otlp:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:4318/v1/traces"))
        )
    trace.set_tracer_provider(provider)
    return run_id
```

`ReadableSpan.to_json()` gives you trace/span/parent ids, name, kind, start & end times in
ns, attributes, events, status, and the full resource block. Everything Day 2 needs.

---

## 3. `instrument.py`

```python
import functools
import inspect
import json
import uuid

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

tracer = trace.get_tracer("arkit")

MAX_ATTR = 4000


def _code_attrs(fn) -> dict:
    """Emit both the current and legacy code.* semconv names.

    The code semconv was renamed (code.filepath -> code.file.path etc). Emitting both
    is three extra lines and means any downstream tool resolves it either way. Note the
    duplication in the README so it reads as deliberate.
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
                span.set_attribute("arkit.tool.arguments", _clip(kwargs))

                try:
                    result = fn(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    span.set_attribute("arkit.tool.outcome", "exception")
                    raise

                span.set_attribute("arkit.tool.result", _clip(result))
                span.set_attribute("arkit.tool.outcome", "ok")
                return result

        wrapper.__arkit_tool_name__ = tool_name
        wrapper.__arkit_description__ = description or (fn.__doc__ or "").strip()
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
        span.set_attribute("arkit.tool.arguments", _clip(raw_arguments))
        span.set_attribute("arkit.tool.outcome", outcome)
        span.set_attribute("arkit.tool.rejection_reason", reason)
        span.set_status(Status(StatusCode.ERROR, reason))
```

---

## 4. Span hierarchy and attributes

Three span types, nested. Names follow the GenAI convention of `{operation} {target}`.

**`invoke_agent arkit` — root, one per task**

| attribute | value |
|---|---|
| `gen_ai.operation.name` | `invoke_agent` |
| `gen_ai.agent.name` | `arkit` |
| `arkit.task.id` | task id from tasks.jsonl |
| `arkit.task.prompt` | the user prompt |
| `arkit.task.expected` | expected answer |
| `arkit.run.iterations` | loop count at exit |
| `arkit.run.stop_reason` | `answered` \| `max_iterations` \| `fatal_error` |
| `arkit.run.success` | bool, from the checker |
| `arkit.run.final_answer` | model's last text |

**`chat {model}` — one per LLM call**

| attribute | value |
|---|---|
| `gen_ai.operation.name` | `chat` |
| `gen_ai.system` | `gemini` / `openai` |
| `gen_ai.request.model` | model id |
| `gen_ai.request.temperature` | `0` |
| `gen_ai.response.model` | as returned |
| `gen_ai.response.finish_reasons` | list |
| `gen_ai.usage.input_tokens` | int |
| `gen_ai.usage.output_tokens` | int |
| `arkit.chat.iteration` | 0-indexed loop number |
| `arkit.chat.tool_calls_requested` | list of names the model asked for |

Put message content in span **events** (`gen_ai.client.inference.operation.details` style)
or in `arkit.chat.messages`, gated behind an env flag `ARKIT_CAPTURE_CONTENT=1`. Off by
default, on for your runs. Mention the flag in the README — content-capture opt-in is
standard practice and reviewers notice it.

**`execute_tool {name}` — one per tool call, including rejected ones.** Covered above.

> Pin your semconv version in the README (`Follows OTel GenAI semconv v1.3x`). These
> attribute names have been moving between releases; check the current spec page and
> adjust before you publish. Stating the version you targeted is the credible move.

---

## 5. The tools — seeded so failures happen on their own

Don't fabricate bugs. Make the environment mildly hostile and run a weak model at
temperature 0. The failures will arrive.

```python
# tools/users.py
@traced_tool(description="Look up a user by their numeric user_id.")
def get_user(user_id: int) -> dict: ...

@traced_tool(description="Retrieve the profile record for a username string.")
def fetch_user(username: str) -> dict: ...
```
Two near-identical names with incompatible signatures. Produces wrong-tool selection and
argument-type errors.

```python
# tools/flaky.py — raises a 500 on ~20% of calls, seeded RNG per (task_id, call_index)
@traced_tool(description="Get the current status of an order by order_id.")
def fetch_order_status(order_id: str) -> dict: ...
```
Deterministic flakiness, so reruns reproduce. Produces retry loops.

```python
# tools/docs.py — schema is nested and badly documented on purpose
@traced_tool(description="Search the knowledge base.")
def search_kb(query: dict) -> list: ...
    # expects {"terms": [...], "filters": {"category": str, "after": "YYYY-MM-DD"}}
    # description above says none of this
```
Produces malformed arguments.

```python
# tools/calc.py — the one honest tool
@traced_tool(description="Evaluate an arithmetic expression.")
def calculate(expression: str) -> float: ...
```

Registry in `tools/__init__.py`: `TOOLS: dict[str, Callable]`, plus a JSON-schema list
built from Pydantic models for the model's tool declarations.

---

## 6. `tasks/tasks.jsonl` — 40 tasks

```json
{"id":"t001","prompt":"What is the order status for ORD-4471?","expected":"shipped","checker":"contains","tags":["flaky"]}
```

Fields: `id`, `prompt`, `expected`, `checker` (`exact` | `contains` | `numeric` | `refusal`),
`tags`.

Composition:
- 16 solvable-and-clean (baseline — you need passes, not just failures)
- 8 hitting `fetch_order_status` (retry loops)
- 6 forcing the `get_user` / `fetch_user` choice
- 6 requiring `search_kb` with filters (malformed args)
- **8 unsolvable with the given tools** — checker `refusal`, expected behaviour is to say
  it can't. This is where hallucinated tool names come from, and it's the highest-value
  bucket. Don't cut it.

---

## 7. `agent.py` — the loop

```
messages = [system_prompt, user_prompt]
for i in range(MAX_ITERATIONS):          # MAX_ITERATIONS = 8
    response = llm.chat(messages, tools=TOOL_SCHEMAS)   # -> chat span
    if no tool calls:
        stop_reason = "answered"; break
    for call in response.tool_calls:
        if call.name not in TOOLS:
            record_rejected_call(call.name, call.args, "tool not in registry", "unknown_tool")
            append tool-result message: error text
            continue
        try:
            args = MODELS[call.name].model_validate(call.args)   # pydantic
        except ValidationError as e:
            record_rejected_call(call.name, call.args, str(e), "invalid_arguments")
            append tool-result message: error text
            continue
        try:
            result = TOOLS[call.name](**args.model_dump(), _call_id=call.id)
        except Exception as e:
            result = {"error": str(e)}      # span already recorded the exception
        append tool-result message
else:
    stop_reason = "max_iterations"
```

Rules, in order of how badly breaking them hurts Day 2:

1. On any rejection, **feed the error back to the model as a tool result** and keep
   looping. The model's recovery attempt (or failure to recover) is the interesting part.
2. Never raise out of the loop on a tool failure. Only a genuine crash sets
   `stop_reason = "fatal_error"`.
3. `MAX_ITERATIONS = 8`. Loops must be allowed to actually loop, then terminate visibly.
4. System prompt lives in `prompts/v1.md`, version string passed into `setup_tracing`.
   Make v1 deliberately mediocre — terse, no guidance on error handling or on refusing.
   v2 on Day 2 is your regression comparison.

Model: `gemini-2.0-flash-lite` or `gpt-4o-mini`, temperature 0.

---

## 8. `runner.py`

```
python -m arkit.runner run \
  --tasks tasks/tasks.jsonl \
  --model gemini-2.0-flash-lite \
  --prompt-version v1 \
  --out traces/run_{run_id}.jsonl
```

Sequential, not concurrent — concurrency muddies the latency signal you want on Day 2.
Rich progress bar. On finish print: tasks run, pass/fail, total tool calls, count by
`arkit.tool.outcome`. That last line is your first look at whether the seeding worked.

---

## 9. Day 1 acceptance — do not move to Day 2 until all of these hold

- [ ] `docker compose up` gives Jaeger at :16686, and one task shows a correct waterfall:
      `invoke_agent` → `chat` → `execute_tool` nested, not flat. **Screenshot it now**, it
      goes in the README.
- [ ] `traces/run_*.jsonl` parses line by line as JSON, every line has `resource` with
      `arkit.run.id` and `arkit.prompt.version`.
- [ ] Every `execute_tool` span that actually ran a function has `code.file.path` and
      `code.line.number` pointing at a real line — verify by opening one.
- [ ] `grep` the trace file for `"unknown_tool"` → at least 3 hits.
- [ ] `grep` for `"invalid_arguments"` → at least 3 hits.
- [ ] At least 2 tasks ended with `stop_reason = "max_iterations"`.
- [ ] At least one task called the same tool with identical arguments 3+ times.
- [ ] Token counts are present and non-zero on every `chat` span.

If the failure buckets are empty, weaken the system prompt before you weaken the tools —
it's the faster lever and it's more honest as a case study.

---

## 10. Commit discipline

Small commits with real messages from hour one. Both companies will read the git history
as a work sample. `git log --oneline` should tell the story of the build, not show three
commits named "wip".
