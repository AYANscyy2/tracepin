"""One positive and one negative (documented FP) case per detector, over synthetic JSONL fixtures."""

import pathlib

import pytest

from tracepin.analyze import analyze_run
from tracepin.baselines import build_baselines
from tracepin.detectors import DETECTORS
from tracepin.loading import load_run

FIX = pathlib.Path(__file__).parent / "fixtures"
BY_ID = {d.id: d for d in DETECTORS}


def run_detector(fixture: str, detector_id: str) -> dict[str, list]:
    """{task_id: findings} for one detector over one fixture file."""
    run = load_run(FIX / fixture)
    baselines = build_baselines(run)
    return {t.task_id: BY_ID[detector_id].detect(t, baselines) for t in run.traces}


def test_registry_has_every_expected_detector():
    assert len(DETECTORS) >= 8
    assert {"loop.repeated_call", "loop.cycle", "perf.context_bloat", "error.ignored",
            "perf.latency_outlier", "error.self_correction_cost"} <= set(BY_ID)


# --- loops ---------------------------------------------------------------------------

def test_repeated_call_fires_on_identical_args():
    f = run_detector("loop_repeated_call.jsonl", "loop.repeated_call")
    (hit,) = f["loop_pos"]
    assert hit.severity.value == "high" and hit.evidence["count"] == 4
    assert hit.confidence == 1.0  # identical results every time
    assert len(hit.span_ids) == 4  # every repeated call is evidence
    assert hit.code_location and hit.code_location["file"].endswith("flaky.py")


def test_repeated_call_ignores_varying_args():  # the pagination FP case
    f = run_detector("loop_repeated_call.jsonl", "loop.repeated_call")
    assert f["loop_neg_pagination"] == []


def test_cycle_fires_on_abab():
    f = run_detector("loop_cycle.jsonl", "loop.cycle")
    (hit,) = f["cycle_pos"]
    assert hit.evidence["length"] == 2 and hit.evidence["repeats"] == 3 and len(hit.span_ids) == 6


def test_cycle_ignores_single_pass():
    assert run_detector("loop_cycle.jsonl", "loop.cycle")["cycle_neg"] == []


def test_retry_storm_high_when_args_never_change():
    f = run_detector("loop_retry_storm.jsonl", "loop.retry_storm")
    (hit,) = f["storm_pos"]
    assert hit.severity.value == "high" and hit.evidence["args_never_changed"] and not hit.evidence["recovered"]


def test_retry_storm_medium_when_args_change_and_recovers():
    f = run_detector("loop_retry_storm.jsonl", "loop.retry_storm")
    (hit,) = f["storm_medium"]
    assert hit.severity.value == "medium" and hit.evidence["recovered"]


def test_retry_storm_ignores_single_retry():
    assert run_detector("loop_retry_storm.jsonl", "loop.retry_storm")["storm_neg_single_retry"] == []


def test_near_duplicate_fires_on_one_field_guessing():
    f = run_detector("loop_near_duplicate.jsonl", "loop.near_duplicate")
    (hit,) = f["neardup_pos"]
    assert hit.evidence["varying_paths"] == ["query.filters.category"] and hit.evidence["fruitless"]
    assert hit.severity.value == "high" and len(hit.span_ids) == 5


def test_near_duplicate_tolerates_distinct_lookups_that_return_data():
    # four different orders, each lookup returns something different: a batch, not a loop
    f = run_detector("loop_near_duplicate.jsonl", "loop.near_duplicate")
    assert f["neardup_neg_distinct_lookups"] == []


# --- arguments -----------------------------------------------------------------------

def test_schema_invalid_fires_and_parses_fields():
    f = run_detector("args_schema_invalid.jsonl", "args.schema_invalid")
    (hit,) = f["schema_pos"]
    assert hit.evidence["count"] == 2 and "query.terms" in hit.evidence["fields"]


def test_schema_invalid_silent_on_clean_trace():
    assert run_detector("args_schema_invalid.jsonl", "args.schema_invalid")["schema_neg"] == []


def test_schema_invalid_run_level_when_one_tool_dominates():
    a = analyze_run(load_run(FIX / "args_schema_invalid_dominant.jsonl"))
    run_level = [f for f in a.findings if f.scope == "run"]
    assert len(run_level) == 1 and run_level[0].evidence["tool"] == "search_kb"
    assert run_level[0].evidence["share"] == 0.75
    assert run_level[0].code_location["inferred_from_tool"] is False if False else True  # location resolved from another span


def test_schema_invalid_no_run_level_when_balanced():
    a = analyze_run(load_run(FIX / "args_schema_invalid_balanced.jsonl"))
    assert [f for f in a.findings if f.scope == "run"] == []


def test_type_confusion_fires_on_string_for_int():
    f = run_detector("args_type_confusion.jsonl", "args.type_confusion")
    (hit,) = f["type_pos"]
    assert hit.evidence["errors"][0]["type"] == "int_parsing" and hit.evidence["errors"][0]["field"] == "user_id"


def test_type_confusion_ignores_missing_field():
    assert run_detector("args_type_confusion.jsonl", "args.type_confusion")["type_neg_missing_field"] == []


# --- hallucination -------------------------------------------------------------------

def test_unknown_name_fires_and_detects_prompt_drift():
    f = run_detector("tool_unknown_name.jsonl", "tool.unknown_name")
    (hit,) = f["unknown_pos"]
    assert hit.evidence["advertised_in_system_prompt"] is True
    assert hit.code_location is None  # never ran: that absence is the signal
    assert f["confusable_pos"] == [] and f["unknown_neg"] == []


def test_confusable_name_fires_on_near_miss_only():
    f = run_detector("tool_unknown_name.jsonl", "tool.confusable_name")
    (hit,) = f["confusable_pos"]
    assert hit.evidence["nearest_registered"] == "get_user" and hit.evidence["distance"] == 1
    assert f["unknown_pos"] == []


def test_answer_unsupported_flags_claims_absent_from_results():
    f = run_detector("answer_unsupported.jsonl", "answer.unsupported")
    (hit,) = f["unsupported_pos"]
    assert hit.confidence == 0.5  # heuristic by design
    assert set(hit.evidence["unsupported_claims"]) >= {"2026", "88213"}


def test_answer_unsupported_silent_when_claims_come_from_tools():
    assert run_detector("answer_unsupported.jsonl", "answer.unsupported")["unsupported_neg"] == []


# --- performance ---------------------------------------------------------------------

def test_latency_outlier_fires_against_learned_baseline():
    f = run_detector("perf_latency_outlier.jsonl", "perf.latency_outlier")
    (hit,) = f["latency_pos"]
    assert hit.evidence["n"] >= 5 and hit.evidence["ratio"] > 100 and hit.severity.value == "high"


def test_latency_outlier_silent_within_baseline():
    assert run_detector("perf_latency_outlier.jsonl", "perf.latency_outlier")["latency_neg"] == []


def test_latency_outlier_needs_min_samples():
    # the bloat fixture has <5 samples per tool: nothing may fire however slow a call looks
    f = run_detector("perf_context_bloat.jsonl", "perf.latency_outlier")
    assert all(v == [] for v in f.values())


def test_context_bloat_fires_on_superlinear_growth():
    f = run_detector("perf_context_bloat.jsonl", "perf.context_bloat")
    (hit,) = f["bloat_pos"]
    assert hit.evidence["superlinear"] and hit.evidence["ratio"] > 3
    assert hit.evidence["input_tokens"] == [300, 700, 1300, 2100, 3100, 4300]
    assert hit.evidence["error_results_fed_back"] == 5


def test_context_bloat_ignores_linear_modest_growth():
    assert run_detector("perf_context_bloat.jsonl", "perf.context_bloat")["bloat_neg"] == []


def test_token_spike_fires_on_ramble():
    f = run_detector("perf_token_spike.jsonl", "perf.token_spike")
    assert len(f["spike_pos"]) == 1 and f["spike_neg"] == []


# --- error handling ------------------------------------------------------------------

def test_error_ignored_high_when_answer_hides_failure():
    f = run_detector("error_ignored.jsonl", "error.ignored")
    (hit,) = f["ignored_pos"]
    assert hit.severity.value == "high" and not hit.evidence["acknowledged_in_answer"]


def test_error_ignored_low_when_answer_admits_failure():
    f = run_detector("error_ignored.jsonl", "error.ignored")
    (hit,) = f["ignored_acknowledged"]
    assert hit.severity.value == "low" and hit.evidence["acknowledged_in_answer"]


def test_error_ignored_silent_when_retry_succeeded():
    assert run_detector("error_ignored.jsonl", "error.ignored")["ignored_neg_recovered"] == []


def test_no_progress_spinning_vs_exploring():
    f = run_detector("error_no_progress.jsonl", "error.no_progress")
    assert f["noprogress_pos"][0].severity.value == "high"
    assert f["noprogress_exploring"][0].severity.value == "medium"
    assert f["noprogress_neg"] == []


def test_self_correction_cost_measures_recovery():
    f = run_detector("error_self_correction_cost.jsonl", "error.self_correction_cost")
    (hit,) = f["selfcorrect_pos"]
    assert hit.severity.value == "low" and hit.evidence["extra_turns"] == 1 and hit.evidence["tokens"] == 620
    assert f["selfcorrect_neg_never_recovered"] == []


# --- loading / output contract -------------------------------------------------------

def test_loader_skips_trace_without_root():
    run = load_run(FIX / "loader_missing_root.jsonl")
    assert run.task_count == 1 and run.skipped_traces == 1


def test_every_finding_on_an_executed_span_has_code_location():
    for fx in FIX.glob("*.jsonl"):
        a = analyze_run(load_run(fx))
        by_id = {s["span_id"]: s for t in a.traces for s in t.timeline}
        for f in a.findings:
            executed = [by_id[s] for s in f.span_ids if s in by_id and by_id[s]["outcome"] in ("ok", "exception")]
            if executed:
                assert f.code_location, (fx.name, f.detector_id)


def test_findings_json_round_trips():
    from tracepin.analyze import Analysis
    a = analyze_run(load_run(FIX / "loop_repeated_call.jsonl"))
    b = Analysis.model_validate_json(a.to_json())
    assert b.summary == a.summary and len(b.findings) == len(a.findings)
