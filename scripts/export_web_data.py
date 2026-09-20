"""Write web/data/ for the Next.js viewer: runs index, findings, slim spans, code contexts.

Usage: python scripts/export_web_data.py v1=findings/sample_v1.json:traces/sample.jsonl ...
Default: the four committed sample runs.
"""

import json
import pathlib
import sys

from tracepin.analyze import Analysis
from tracepin.loading import load_run
from tracepin.rootcause import code_context, rank_locations, relpath, suggested_fix

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "data"
CLIP = 600

DEFAULT = {
    "v1": ("findings/sample_v1.json", "traces/sample.jsonl"),
    "v1_35lite": ("findings/sample_v1_35lite.json", "traces/run_v1_35lite_c506b6cf636b.jsonl"),
    "v2_35lite": ("findings/sample_v2_35lite.json", "traces/run_v2_35lite_3a519473661c.jsonl"),
    "v3_35lite": ("findings/sample_v3_35lite.json", "traces/run_v3_35lite_5ac209f1fdfd.jsonl"),
}


def _clip(v):
    if isinstance(v, str) and len(v) > CLIP:
        return v[:CLIP] + "…"
    if isinstance(v, list):
        return [_clip(x) for x in v]
    return v


def slim_span(s, t0: int) -> dict:
    return {
        "span_id": s.span_id, "parent_span_id": s.parent_span_id, "name": s.name,
        "operation": s.operation, "start_ms": (s.start_ns - t0) / 1e6, "end_ms": (s.end_ns - t0) / 1e6,
        "status_code": s.status_code, "status_message": _clip(s.status_message),
        "attributes": {k: _clip(v) for k, v in s.attributes.items()},
        "events": [{"name": e.get("name"), "at_ms": None, "attributes": {k: _clip(v) for k, v in e.get("attributes", {}).items()
                                                                          if not k.startswith("exception.stacktrace") and k != "gen_ai.input.messages"}}
                   for e in s.events],
    }


def export(label: str, findings_path: str, trace_path: str, index: list) -> None:
    a = Analysis.load(ROOT / findings_path)
    run = load_run(ROOT / trace_path)
    run_id = a.run["run_id"]
    (OUT / "findings").mkdir(parents=True, exist_ok=True)
    (OUT / "traces").mkdir(exist_ok=True)
    (OUT / "code").mkdir(exist_ok=True)

    root_start = {t.trace_id: t.root.start_ns for t in run.traces}
    doc = a.model_dump(mode="json")
    for f in doc["findings"]:
        # unaccounted-time holes are absolute ns; the viewer works in ms offsets from the root
        if f["detector_id"] == "perf.unaccounted_time" and f["trace_id"] in root_start:
            t0 = root_start[f["trace_id"]]
            f["evidence"]["holes_ms"] = [{"start_ms": (h["start_ns"] - t0) / 1e6, "end_ms": (h["end_ns"] - t0) / 1e6,
                                          "kind": h["kind"], "span_id": h["span_id"]} for h in f["evidence"].get("holes", [])]
        f["suggested_fix"] = suggested_fix(next(x for x in a.findings if x.span_ids == f["span_ids"] and x.detector_id == f["detector_id"]))
        if f["code_location"]:
            f["code_location"]["file"] = relpath(f["code_location"]["file"])
    (OUT / "findings" / f"{run_id}.json").write_text(json.dumps(doc), encoding="utf-8")

    traces = {}
    from tracepin.models import _iso_to_ns  # noqa: F401  (timestamps already ns)
    for t in run.traces:
        t0 = t.root.start_ns
        spans = [slim_span(s, t0) for s in t.spans]
        for s, raw in zip(spans, t.spans):
            for ev, raw_ev in zip(s["events"], raw.events):
                ts = raw_ev.get("timestamp")
                if ts:
                    from tracepin.models import _iso_to_ns
                    ev["at_ms"] = (_iso_to_ns(ts) - t0) / 1e6
        traces[t.trace_id] = {"trace_id": t.trace_id, "task_id": t.task_id, "spans": spans}
    (OUT / "traces" / f"{run_id}.json").write_text(json.dumps(traces), encoding="utf-8")

    code = {}
    for r in rank_locations(a):
        ctx = r.context.model_dump(mode="json") if r.context else None
        code[f"{r.file}:{r.line}"] = {"file": r.file, "line": r.line, "function": r.function, "findings": r.findings,
                                      "tasks": r.tasks, "detectors": r.detectors, "context": ctx}
    (OUT / "code" / f"{run_id}.json").write_text(json.dumps(code), encoding="utf-8")

    index.append({"run_id": run_id, "label": label, **{k: a.run[k] for k in ("model", "prompt_version", "git_sha", "task_count", "pass_count")},
                  "summary": a.summary, "total_input_tokens": a.run["total_input_tokens"], "total_wall_ms": a.run["total_wall_ms"]})
    print(f"{label:10} {run_id}  {len(run.traces)} traces  {len(a.findings)} findings  {len(code)} locations")


def main(argv: list[str]) -> None:
    specs = dict(DEFAULT)
    for arg in argv:
        label, _, rest = arg.partition("=")
        fp, _, tp = rest.partition(":")
        specs[label] = (fp, tp)
    index: list = []
    for label, (fp, tp) in specs.items():
        export(label, fp, tp, index)
    (OUT / "runs.json").write_text(json.dumps(index, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
