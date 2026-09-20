"""Root cause chain: location -> blame -> snippet -> ranking; repro script generation."""

import pathlib
import subprocess

import pytest

from tracepin.analyze import Analysis, analyze_run
from tracepin.detectors import Finding, Severity
from tracepin.loading import load_run
from tracepin.repro import generate, signature
from tracepin.rootcause import blame_line, code_context, rank_locations, relpath, suggested_fix, top_finding

FIX = pathlib.Path(__file__).parent / "fixtures"
SAMPLE = pathlib.Path(__file__).parent.parent / "findings" / "sample_v1.json"
IN_REPO = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True).stdout.strip() == "true"


def test_relpath_strips_repo_root():
    here = pathlib.Path(__file__).resolve()
    assert relpath(str(here)) == "tests/test_rootcause.py"
    assert relpath("tools/x.py") == "tools/x.py"  # already relative: untouched


@pytest.mark.skipif(not IN_REPO, reason="needs the git repo")
def test_blame_line_parses_porcelain():
    b = blame_line("src/tracepin/tools/docs.py", 36, "HEAD")
    assert b and len(b.commit) == 40 and b.author and b.subject


@pytest.mark.skipif(not IN_REPO, reason="needs the git repo")
def test_code_context_reads_source_at_recorded_commit_not_head():
    # 5a9512d is the v1 commit: docstring was still "Search the knowledge base."
    ctx = code_context({"file": "src/tracepin/tools/docs.py", "line": 36, "function": "search_kb"}, "5a9512dea4298e29b78f650d5ec81c2de79e5d28")
    assert ctx.source_ref.startswith("5a9512d")
    assert '>   36  @traced_tool(description="Search the knowledge base.")' in ctx.snippet
    assert ctx.blame and ctx.blame.subject.startswith("Add seeded tools")


def test_code_context_falls_back_to_worktree_on_unknown_sha():
    ctx = code_context({"file": "src/tracepin/tools/docs.py", "line": 36, "function": "search_kb"}, "unknown")
    assert ctx.source_ref == "worktree" and "36" in ctx.snippet


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample findings not present")
def test_rank_locations_puts_search_kb_docstring_first():
    ranked = rank_locations(Analysis.load(SAMPLE), with_context=False)
    assert ranked[0].file.endswith("tools/docs.py") and ranked[0].line == 36
    assert len(ranked[0].tasks) >= 10 and ranked[0].detectors["args.schema_invalid"] >= 10


def _f(det, sev, loc=None, conf=1.0):
    return Finding(detector_id=det, severity=sev, confidence=conf, trace_id="t", task_id="x", span_ids=[],
                   title="", detail="", code_location=loc)


def test_top_finding_skips_latency_outliers_when_a_real_bug_exists():
    fs = [_f("perf.latency_outlier", Severity.HIGH), _f("error.ignored", Severity.HIGH, conf=0.9)]
    assert top_finding(fs).detector_id == "error.ignored"
    assert top_finding([_f("perf.latency_outlier", Severity.HIGH)]).detector_id == "perf.latency_outlier"


def test_suggested_fix_distinguishes_prompt_drift_from_invention():
    drift = _f("tool.unknown_name", Severity.HIGH); drift.evidence = {"advertised_in_system_prompt": True}
    invented = _f("tool.unknown_name", Severity.HIGH); invented.evidence = {"advertised_in_system_prompt": False}
    assert "prompt drift" in suggested_fix(drift) and "invented" in suggested_fix(invented)


def test_signature_excludes_latency_and_low_findings():
    a = analyze_run(load_run(FIX / "error_ignored.jsonl"))
    pos = next(t for t in a.traces if t.task_id == "ignored_pos")
    assert signature(a, pos.trace_id) == ["error.ignored"]
    ack = next(t for t in a.traces if t.task_id == "ignored_acknowledged")
    assert signature(a, ack.trace_id) == []  # LOW-only trace: nothing to assert on


def test_generate_writes_runnable_script_and_extracts_spans(tmp_path):
    a = analyze_run(load_run(FIX / "error_ignored.jsonl"))
    pos = next(t for t in a.traces if t.task_id == "ignored_pos")
    path = generate(a, pos, str(FIX / "error_ignored.jsonl"), tmp_path / "ignored_pos_repro.py")
    src = path.read_text()
    assert "EXPECTED = ['error.ignored']" in src and "sys.exit(replay(" in src
    compile(src, str(path), "exec")
    assert (tmp_path / "ignored_pos_trace.jsonl").read_text().count("\n") == 4  # root + 2 chats + 1 tool
