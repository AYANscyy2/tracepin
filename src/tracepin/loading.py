"""JSONL -> Run. Groups spans by trace_id and reconstructs the parent/child tree."""

import json
import logging
import pathlib
from collections import defaultdict

from tracepin.models import ATTR_PREFIX, Run, Span, Trace

log = logging.getLogger(__name__)


def load_spans(path: str | pathlib.Path) -> tuple[list[Span], dict]:
    """Parse every line; return spans plus the resource attributes of the first line."""
    spans: list[Span] = []
    resource: dict = {}
    for lineno, line in enumerate(pathlib.Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            log.warning("%s:%d unparseable line skipped: %s", path, lineno, e)
            continue
        if not resource:
            resource = raw.get("resource", {}).get("attributes", {})
        spans.append(Span.from_json(raw))
    return spans, resource


def build_traces(spans: list[Span]) -> tuple[list[Trace], int]:
    by_trace: dict[str, list[Span]] = defaultdict(list)
    for s in spans:
        by_trace[s.trace_id].append(s)

    traces: list[Trace] = []
    skipped = 0
    for trace_id, group in by_trace.items():
        group.sort(key=lambda s: s.start_ns)
        roots = [s for s in group if s.parent_span_id is None]
        if len(roots) != 1:
            # A dropped root (crash mid-task) leaves orphans; skip rather than crash.
            log.warning("trace %s has %d root spans, skipping (%d spans)", trace_id, len(roots), len(group))
            skipped += 1
            continue
        traces.append(Trace(trace_id=trace_id, root=roots[0], spans=group))
    traces.sort(key=lambda t: t.root.start_ns)
    # A resumed run (--run-id) appends; if a task was re-run, the latest attempt wins.
    latest: dict[str, Trace] = {}
    for t in traces:
        if t.task_id in latest:
            log.warning("task %s appears more than once; keeping the latest trace", t.task_id)
        latest[t.task_id] = t
    return list(latest.values()), skipped


def load_run(path: str | pathlib.Path) -> Run:
    spans, resource = load_spans(path)
    traces, skipped = build_traces(spans)
    return Run(
        run_id=str(resource.get(f"{ATTR_PREFIX}.run.id", pathlib.Path(path).stem)),
        model=str(resource.get("gen_ai.request.model", "unknown")),
        prompt_version=str(resource.get(f"{ATTR_PREFIX}.prompt.version", "unknown")),
        git_sha=str(resource.get("vcs.repository.ref.revision", "unknown")),
        traces=traces,
        skipped_traces=skipped,
    )
