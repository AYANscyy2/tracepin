"""Per-tool and per-model latency/token baselines learned from the corpus.

Nothing in the detectors hardcodes a millisecond threshold: "slow" means slow relative
to what this tool normally does in this run.
"""

import statistics
from collections import defaultdict

from pydantic import BaseModel, Field

from tracepin.models import Run, Span

MIN_SAMPLES = 5  # below this, outlier detection is noise


class Stats(BaseModel):
    median: float
    mad: float  # median absolute deviation
    n: int

    @classmethod
    def of(cls, values: list[float]) -> "Stats":
        med = statistics.median(values)
        mad = statistics.median(abs(v - med) for v in values)
        return cls(median=med, mad=mad, n=len(values))

    def is_outlier(self, value: float) -> bool:
        """Robust rule: beyond median + 3*MAD *and* more than double the median.

        The second clause stops a tool with near-zero MAD (e.g. an in-memory dict lookup
        that takes 0.08ms ± 0.01) from flagging a 0.2ms call.
        """
        if self.n < MIN_SAMPLES:
            return False
        return value > self.median + 3 * self.mad and value > 2 * self.median


class Baselines(BaseModel):
    tool_latency_ms: dict[str, Stats] = Field(default_factory=dict)
    chat_latency_ms: dict[str, Stats] = Field(default_factory=dict)  # keyed by model
    chat_output_tokens: Stats | None = None
    chat_input_tokens: Stats | None = None

    def to_json(self) -> dict:
        return {
            "tools": {k: {"median_ms": v.median, "mad_ms": v.mad, "n": v.n} for k, v in self.tool_latency_ms.items()},
            "chat": {k: {"median_ms": v.median, "mad_ms": v.mad, "n": v.n} for k, v in self.chat_latency_ms.items()},
            "chat_output_tokens": self.chat_output_tokens.model_dump() if self.chat_output_tokens else None,
            "chat_input_tokens": self.chat_input_tokens.model_dump() if self.chat_input_tokens else None,
        }


def _executed(span: Span) -> bool:
    # Rejected calls (unknown_tool / invalid_arguments) never ran a function; their
    # ~0.1ms duration would drag the baseline down and make every real call an outlier.
    return span.tool_outcome in ("ok", "exception")


def build_baselines(run: Run) -> Baselines:
    tool_samples: dict[str, list[float]] = defaultdict(list)
    chat_samples: dict[str, list[float]] = defaultdict(list)
    out_tokens: list[float] = []
    in_tokens: list[float] = []
    seen_tool: set[str] = set()

    for trace in run.traces:
        for s in trace.tool_calls:
            if not _executed(s) or not s.tool_name:
                continue
            # First call of each tool in the run is a cold start; exclude it.
            if s.tool_name not in seen_tool:
                seen_tool.add(s.tool_name)
                continue
            tool_samples[s.tool_name].append(s.duration_ms)
        for c in trace.chats:
            if c.is_error:
                continue
            chat_samples[c.model or run.model].append(c.duration_ms)
            if c.output_tokens is not None:
                out_tokens.append(float(c.output_tokens))
            if c.input_tokens is not None:
                in_tokens.append(float(c.input_tokens))

    return Baselines(
        tool_latency_ms={k: Stats.of(v) for k, v in tool_samples.items() if v},
        chat_latency_ms={k: Stats.of(v) for k, v in chat_samples.items() if v},
        chat_output_tokens=Stats.of(out_tokens) if out_tokens else None,
        chat_input_tokens=Stats.of(in_tokens) if in_tokens else None,
    )
