"""Tracer provider, run-identifying resource, and the JSONL exporter Day 2 reads."""

import pathlib
import subprocess
import threading
import uuid

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)


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
            "service.name": "tracepin-agent",
            "service.version": "0.1.0",
            "vcs.repository.ref.revision": _git_sha(),
            "tracepin.run.id": run_id,
            "tracepin.prompt.version": prompt_version,
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


def shutdown_tracing() -> None:
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()
