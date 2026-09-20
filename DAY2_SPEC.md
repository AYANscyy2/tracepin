# Day 2 Spec — Detection, Reporting, Regression (`tracepin`)

Input: the JSONL trace files from Day 1. Output: a detector engine, a CLI that produces a
readable failure report, and a regression harness that diffs two runs.

No UI today. Day 3 reads the findings file this produces, so the findings schema is the
contract — get it right before writing detector number two.

> Swap `tracepin` for whatever you actually named it.

---

## 0. The thing that decides whether this half is impressive

Half your detectors can be written by filtering a field Day 1 already wrote
(`arkit.tool.outcome == "unknown_tool"`). Those are table stakes — keep them, they're
cheap and they demonstrate the instrumentation works end to end.

But a reviewer at Patched reads those as "he filtered a column." The detectors that carry
the project are the ones inferring a pattern **nothing labelled**:

- tool-call loops and A→B→A→B cycles
- context bloat (input tokens compounding across iterations)
- errors the agent acknowledged and then ignored
- per-tool latency outliers against a learned baseline
- self-correction cost (wrong tool → right tool, how many turns)

Build at least three from that list. If you're short on time, cut a trivial detector, not
one of these.

---

## 1. New files

```
src/tracepin/
  models.py          # Span, Trace, Run, Finding  (pydantic)
  loading.py         # JSONL -> Run, parent/child tree reconstruction
  detectors/
    __init__.py      # DETECTORS registry
    base.py          # Detector protocol, Finding, Severity
    loops.py
    arguments.py
    hallucination.py
    performance.py
    error_handling.py
  baselines.py       # per-tool median/MAD stats over a corpus
  analyze.py         # run all detectors over a Run
  report.py          # rich terminal report
  compare.py         # regression diff between two Runs
  cli.py             # typer app
tests/
  fixtures/*.jsonl   # hand-built synthetic traces, one per detector
  test_detectors.py
findings/            # output, gitignored except one sample
```

---

## 2. `models.py` — the trace model

Don't poke at raw dicts in the detectors. Parse once, then detectors work on typed objects.

```python
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


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

    @property
    def duration_ms(self) -> float:
        return (self.end_ns - self.start_ns) / 1_000_000

    @property
    def operation(self) -> str | None:
        return self.attributes.get("gen_ai.operation.name")

    # convenience accessors used constantly by detectors
    @property
    def tool_name(self) -> str | None:
        return self.attributes.get("gen_ai.tool.name")

    @property
    def tool_outcome(self) -> str | None:
        return self.attributes.get("arkit.tool.outcome")

    @property
    def tool_arguments(self) -> Any:
        raw = self.attributes.get("arkit.tool.arguments")
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return raw


class Trace(BaseModel):
    """One task execution: the invoke_agent root plus everything under it."""
    trace_id: str
    root: Span
    spans: list[Span]

    @property
    def task_id(self) -> str: ...
    @property
    def success(self) -> bool: ...
    @property
    def stop_reason(self) -> str: ...
    def by_operation(self, op: str) -> list[Span]:
        """Spans of one operation type, sorted by start_ns."""
    def children(self, span_id: str) -> list[Span]: ...


class Run(BaseModel):
    run_id: str
    model: str
    prompt_version: str
    git_sha: str
    traces: list[Trace]
```

`loading.py`: read JSONL, parse each line, group by `trace_id`, find the span with no
parent as root, sort children by `start_ns`. Guard the case where a root is missing
(dropped span) — log and skip that trace rather than crashing the run.

---

## 3. `detectors/base.py` — the contract

```python
class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Finding(BaseModel):
    detector_id: str            # "loop.repeated_call"
    severity: Severity
    confidence: float           # 0..1 — how sure, before a human looks
    trace_id: str
    task_id: str
    span_ids: list[str]         # every span that constitutes the evidence
    title: str                  # one line, human readable
    detail: str                 # what happened, with numbers
    evidence: dict              # structured, machine-readable
    code_location: dict | None  # {file, function, line} lifted from code.* attrs


class Detector(Protocol):
    id: str
    description: str
    def detect(self, trace: Trace, baselines: Baselines) -> list[Finding]: ...
```

Two rules, both load-bearing for Day 3:

1. **Always populate `code_location`** when the implicated span has `code.file.path`.
   Day 3's root-cause step hydrates straight from this — no re-derivation.
2. **`span_ids` lists every span in the evidence**, not just the last one. A loop finding
   points at all seven repeated calls, so the UI can highlight the whole band.

Registry in `detectors/__init__.py`: `DETECTORS: list[Detector]`, each registered by
decorator. Adding a detector should be one file and one import — say so in the README,
it's the extensibility question an interviewer will ask.

---

## 4. The detectors

### `loops.py` — repeated calls and cycles

The highest-value detector. Work over `trace.by_operation("execute_tool")` in start order.

Fingerprint a call as `(tool_name, canonical(args))` where canonical is
`json.dumps(args, sort_keys=True, separators=(",",":"))` on the parsed args, with string
values `.strip()`ed. Don't lowercase — you'll merge genuinely different calls.

Three patterns:

- **`loop.repeated_call`** — identical fingerprint ≥3 times in one trace.
  `severity=HIGH`, confidence scales with count. Evidence: fingerprint, count, span ids,
  whether the results were also identical (if the tool returned the same thing every time,
  the agent learned nothing — confidence 1.0).
- **`loop.cycle`** — a repeating subsequence of length 2–4 in the fingerprint sequence,
  occurring ≥2 times (A,B,A,B). `severity=HIGH`.
- **`loop.retry_storm`** — same tool called ≥3 times where the earlier calls have
  `status_code == "ERROR"`. This is distinct from `repeated_call`: retrying a failing tool
  is *sometimes correct*. `severity=MEDIUM`, and flag it HIGH only if retries exceed 4 or
  the arguments never changed between retries.

FP mode to document: legitimate pagination or batch lookups repeat a tool with varying
args. Your fingerprint includes args, so those don't trigger — say that in the README.

### `arguments.py`

- **`args.schema_invalid`** — spans with `outcome == "invalid_arguments"`. Group by tool;
  if one tool accounts for >40% of all invalid-arg events across the run, emit a
  run-level finding: the tool description is the bug, not the model. That inversion — the
  fix being in your own tool docstring — is the single best story for the README.
- **`args.type_confusion`** — a tool called with an argument whose JSON type is wrong for
  the schema (string where int expected). Distinguishable from missing-field errors, and
  it's the classic `get_user(user_id="alice")` symptom.

### `hallucination.py`

- **`tool.unknown_name`** — `outcome == "unknown_tool"`. Include the nearest real tool
  name by Levenshtein against the registry; if the distance is ≤3, that's a near-miss on
  a confusable pair (your `get_user`/`fetch_user` seed) rather than a pure invention.
  Report the two cases separately — they have different fixes.
- **`answer.unsupported`** — the final answer contains a concrete claim (a number, an ID,
  a status string) that appears in no tool result in the trace. Keep this one simple:
  extract quoted strings and digit-runs from `arkit.run.final_answer`, check membership
  against the concatenated tool results. Mark `confidence=0.5` and document it as
  heuristic. Being explicit about a detector's weakness reads better than pretending.

### `performance.py`

Never hardcode millisecond thresholds. Build baselines from the corpus in `baselines.py`:
per-tool median and MAD of `duration_ms`, plus the same for `chat` spans per model.

- **`perf.latency_outlier`** — a span where `duration > median + 3*MAD` **and**
  `duration > 2 * median`. Require ≥5 samples for that tool or skip — sparse tools produce
  garbage outliers. Exclude the first call of a run from the baseline (cold start).
- **`perf.context_bloat`** — within a trace, `gen_ai.usage.input_tokens` across successive
  `chat` spans. Flag when the last call's input is >3× the first, or growth is
  superlinear. This is your best performance detector: it catches the agent stuffing
  accumulated error messages back into context, which is exactly what your seeded flaky
  tool causes. Evidence should be the token series so the UI can chart it.
- **`perf.token_spike`** — output tokens on one `chat` span far above the run median,
  usually the model rambling instead of calling a tool.

### `error_handling.py`

- **`error.ignored`** — a tool span with `status_code == "ERROR"`, where the trace ends
  `stop_reason == "answered"` and there was no subsequent successful call to the same
  tool. The agent hit an error, gave up silently, and answered anyway. `severity=HIGH`.
  This one is the most damning in a real system and nothing in Day 1 labelled it.
- **`error.no_progress`** — `stop_reason == "max_iterations"`, plus the count of distinct
  tool fingerprints tried. Low distinct count with high iteration count means it was
  spinning, not exploring.
- **`error.self_correction_cost`** — a wrong-tool or invalid-args event followed later by
  a successful call achieving the same goal. Measures turns and tokens burned recovering.
  `severity=LOW` — it's not a failure, it's an efficiency finding, and having a
  non-failure detector shows you understand the difference.

---

## 5. `analyze.py` + findings output

```
tracepin analyze traces/run_abc123.jsonl --out findings/run_abc123.json
```

Baselines are computed over the whole run before detectors execute. Every detector runs
over every trace; collect all findings; write one JSON:

```json
{
  "run": { "run_id": "...", "model": "...", "prompt_version": "v1", "git_sha": "...",
           "task_count": 40, "pass_count": 23 },
  "baselines": { "tools": { "fetch_order_status": { "median_ms": 82.1, "mad_ms": 9.4, "n": 61 } } },
  "findings": [ ... ],
  "summary": { "by_detector": {...}, "by_severity": {...}, "traces_with_findings": 21 }
}
```

Day 3 reads this file directly. Don't make the UI re-run detectors.

---

## 6. `report.py` — the CLI report

`rich`, and make it genuinely readable — this is what you screenshot for the README.

```
tracepin report findings/run_abc123.json
tracepin report findings/run_abc123.json --detector loop.repeated_call --severity high
tracepin report findings/run_abc123.json --trace 4f2a...    # single trace deep dive
```

Three sections:

1. **Run header** — model, prompt version, git SHA, pass rate, total findings.
2. **Findings by detector** — table: detector, count, severity, affected tasks.
3. **Top offending traces** — the 5 traces with the most findings, each expandable to a
   timeline: tool calls in order, duration, outcome, flagged ones marked. For each finding
   print the `code_location` as `path:line` so it's clickable in a terminal.

Exit code 1 if any HIGH finding exists. That makes it CI-usable, and "wire it into CI as a
gate" is a strong line in the README.

---

## 7. `compare.py` — the regression layer

This is the half Patched cares about most. Run the same 40 tasks under two configs and
diff.

Generate the second run first:

```
python -m tracepin.runner run --prompt-version v2 --model gemini-2.0-flash-lite ...
python -m tracepin.runner run --prompt-version v1 --model gemini-2.0-flash     ...
```

v2 = the system prompt fixed based on what Day 2's detectors told you. That causality —
detector found it, prompt fix, regression proves it — is the whole README narrative.

```
tracepin compare findings/run_v1.json findings/run_v2.json
```

Match traces by `task_id`. Output four buckets, in this order:

- **REGRESSED** — passed in A, fails in B. Headline. Print first, always.
- **FIXED** — failed in A, passes in B.
- **STILL FAILING** — with a note on whether the detector signature changed (same bug or
  a new one wearing the old bug's clothes).
- **UNCHANGED PASSES** — count only, don't list.

Plus a detector-level delta table (finding counts per detector, A vs B, arrow) and
aggregate deltas on tokens and wall-clock. Exit code 1 if anything regressed.

Handle the case where the two runs have different task sets — intersect and warn loudly
rather than silently comparing partial overlaps.

---

## 8. Tests — don't skip these

Detectors are pure functions over data, so they're trivially testable, and their presence
is a strong signal for both applications.

`tests/fixtures/` — hand-write small JSONL traces, one per detector, by editing real spans
from your Day 1 output. For each detector: one fixture that must trigger, one that must
**not** (the FP case you documented). `pytest`, that's it.

```python
def test_repeated_call_fires_on_identical_args(): ...
def test_repeated_call_ignores_varying_args(): ...   # the pagination FP case
```

---

## 9. Day 2 acceptance

- [ ] `tracepin analyze` runs over the Day 1 trace file with no crashes and no traces skipped.
- [ ] ≥8 detectors registered, ≥3 from the pattern-inference list in §0.
- [ ] Every finding on a real tool span has a populated `code_location`.
- [ ] `tracepin report` output is screenshot-worthy. Take the screenshot.
- [ ] Every detector has ≥2 tests, one positive one negative. `pytest` green.
- [ ] Second run exists under v2 prompt, and `tracepin compare` produces a non-empty
      FIXED bucket — you fixed something real, with evidence.
- [ ] You can name, out loud, the three most interesting bugs the detectors caught. Those
      three become the README case study.

---

## 10. Write these down as you go

Keep a scratch file of every real bug the detectors surface, with the trace id. By
tomorrow evening you'll have forgotten which ones were interesting, and the README case
study is the single highest-leverage artifact in this whole project — it's what a founder
actually reads before deciding whether to interview you.
