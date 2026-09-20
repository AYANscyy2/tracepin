"""Regenerates tests/fixtures/*.jsonl. Run: python tests/build_fixtures.py"""

import pathlib

from synth import TraceBuilder, write_fixture

FIX = pathlib.Path(__file__).parent / "fixtures"
PYD_LIST = ("1 validation error for SearchKBArgs\nquery.terms\n  Input should be a valid list "
            "[type=list_type, input_value='webhook retry', input_type=str]")
PYD_INT = ("1 validation error for GetUserArgs\nuser_id\n  Input should be a valid integer, unable to parse string "
           "as an integer [type=int_parsing, input_value='alice', input_type=str]")
PYD_MISSING = ("2 validation errors for SearchKBArgs\nquery.terms\n  Field required [type=missing, input_value={}, "
               "input_type=dict]\nquery.filters\n  Field required [type=missing, input_value={}, input_type=dict]")
HTTP500 = "UpstreamError: HTTP 500: order service unavailable"


def simple(task_id, tool="calculate", args=None, result=None):
    t = TraceBuilder(task_id, prompt="What is 2+2?")
    t.chat(300, 20, tool_calls=[tool]); t.tool(tool, args or {"expression": "2+2"}, result=result or 4.0)
    t.chat(340, 15); return t.finish(final_answer="4")


def padding(n=6):
    """Enough clean calls that every tool has ≥5 baseline samples."""
    out = []
    for i in range(n):
        for tool, args, res in [("calculate", {"expression": "1+1"}, 2.0),
                                ("fetch_order_status", {"order_id": "ORD-4471"}, {"status": "shipped"}),
                                ("search_kb", {"query": {"terms": ["a"], "filters": {"category": "api"}}}, []),
                                ("get_user", {"user_id": 101}, {"username": "alice"})]:
            t = TraceBuilder(f"pad{i}_{tool}", prompt="x")
            t.chat(300, 20, tool_calls=[tool]); t.tool(tool, args, result=res, duration_ms=0.1 + 0.01 * i)
            t.chat(340, 15); out.append(t.finish())
    return out


def build():
    # --- loops ---------------------------------------------------------------------
    t = TraceBuilder("loop_pos", prompt="status of ORD-4471?")
    for _ in range(4):
        t.chat(300 + 100 * _, 20, tool_calls=["fetch_order_status"])
        t.tool("fetch_order_status", {"order_id": "ORD-4471"}, result={"status": "shipped"})
    t.chat(800, 15); t.finish(final_answer="shipped")
    n = TraceBuilder("loop_neg_pagination", prompt="list all pages")
    for page in range(1, 5):  # same tool, different args = pagination, must NOT fire
        n.chat(300, 20, tool_calls=["search_kb"])
        n.tool("search_kb", {"query": {"terms": ["a"], "filters": {"category": "api", "page": page}}}, result=[page])
    n.chat(800, 15); n.finish()
    write_fixture(FIX / "loop_repeated_call.jsonl", [t, n])

    t = TraceBuilder("cycle_pos", prompt="who is user 101 / alice")
    for _ in range(3):
        t.chat(300, 20, tool_calls=["get_user"]); t.tool("get_user", {"user_id": 101}, result={"username": "alice"})
        t.chat(300, 20, tool_calls=["fetch_user"]); t.tool("fetch_user", {"username": "alice"}, result={"user_id": 101})
    t.chat(900, 15); t.finish()
    n = TraceBuilder("cycle_neg", prompt="x")  # A,B once then C: no cycle
    n.chat(300, 20, tool_calls=["get_user"]); n.tool("get_user", {"user_id": 101}, result={})
    n.chat(300, 20, tool_calls=["fetch_user"]); n.tool("fetch_user", {"username": "alice"}, result={})
    n.chat(300, 20, tool_calls=["calculate"]); n.tool("calculate", {"expression": "1+1"}, result=2.0)
    n.chat(300, 15); n.finish()
    write_fixture(FIX / "loop_cycle.jsonl", [t, n])

    t = TraceBuilder("storm_pos", prompt="status of ORD-9054?")
    for _ in range(5):
        t.chat(300, 20, tool_calls=["fetch_order_status"])
        t.tool("fetch_order_status", {"order_id": "ORD-9054"}, outcome="exception", error=HTTP500)
    t.chat(800, 15); t.finish(success=False, final_answer="The order service is unavailable, I can't check right now.")
    m = TraceBuilder("storm_medium", prompt="x")  # 3 calls, args change, recovers -> MEDIUM
    m.chat(300, 20, tool_calls=["search_kb"]); m.tool("search_kb", {"query": {"q": "a"}}, outcome="invalid_arguments", error=PYD_MISSING)
    m.chat(300, 20, tool_calls=["search_kb"]); m.tool("search_kb", {"query": {"terms": ["a"]}}, outcome="invalid_arguments", error=PYD_MISSING)
    m.chat(300, 20, tool_calls=["search_kb"]); m.tool("search_kb", {"query": {"terms": ["a"], "filters": {"category": "api"}}}, result=[])
    m.chat(300, 15); m.finish()
    n = TraceBuilder("storm_neg_single_retry", prompt="x")  # one failure, one retry that works: correct behaviour
    n.chat(300, 20, tool_calls=["fetch_order_status"]); n.tool("fetch_order_status", {"order_id": "ORD-1"}, outcome="exception", error=HTTP500)
    n.chat(300, 20, tool_calls=["fetch_order_status"]); n.tool("fetch_order_status", {"order_id": "ORD-1"}, result={"status": "ok"})
    n.chat(300, 15); n.finish()
    write_fixture(FIX / "loop_retry_storm.jsonl", [t, m, n])

    t = TraceBuilder("neardup_pos", prompt="refund policy?")
    for cat in ("orders", "general", "returns", "policies", "support"):  # guessing one field, nothing comes back
        t.chat(300, 20, tool_calls=["search_kb"])
        t.tool("search_kb", {"query": {"terms": ["refund"], "filters": {"category": cat}}}, result=[])
    t.finish(stop_reason="max_iterations", success=False, final_answer="")
    n = TraceBuilder("neardup_neg_distinct_lookups", prompt="status of 4 orders")
    for oid, st in (("ORD-1", "shipped"), ("ORD-2", "processing"), ("ORD-3", "delivered"), ("ORD-4", "shipped")):
        n.chat(300, 20, tool_calls=["fetch_order_status"])
        n.tool("fetch_order_status", {"order_id": oid}, result={"order_id": oid, "status": st})
    n.chat(500, 30); n.finish(final_answer="shipped, processing, delivered, shipped")
    write_fixture(FIX / "loop_near_duplicate.jsonl", [t, n])

    # --- arguments -----------------------------------------------------------------
    t = TraceBuilder("schema_pos", prompt="x")
    t.chat(300, 20, tool_calls=["search_kb"]); t.tool("search_kb", {"query": {"q": "a"}}, outcome="invalid_arguments", error=PYD_MISSING)
    t.chat(300, 20, tool_calls=["search_kb"]); t.tool("search_kb", {"query": {"terms": ["a"]}}, outcome="invalid_arguments", error=PYD_MISSING)
    t.chat(300, 15); t.finish(success=False)
    n = simple("schema_neg")
    write_fixture(FIX / "args_schema_invalid.jsonl", [t, n])
    # run-level: search_kb dominates (3 of 4) -> run finding; balanced run -> none
    a = TraceBuilder("dom1"); a.chat(300, 20, tool_calls=["search_kb"]); a.tool("search_kb", {}, outcome="invalid_arguments", error=PYD_MISSING); a.finish(success=False)
    b = TraceBuilder("dom2"); b.chat(300, 20, tool_calls=["search_kb"]); b.tool("search_kb", {}, outcome="invalid_arguments", error=PYD_MISSING); b.finish(success=False)
    c = TraceBuilder("dom3"); c.chat(300, 20, tool_calls=["search_kb"]); c.tool("search_kb", {}, outcome="invalid_arguments", error=PYD_MISSING); c.finish(success=False)
    d = TraceBuilder("dom4"); d.chat(300, 20, tool_calls=["get_user"]); d.tool("get_user", {"user_id": "x"}, outcome="invalid_arguments", error=PYD_INT); d.finish(success=False)
    write_fixture(FIX / "args_schema_invalid_dominant.jsonl", [a, b, c, d])
    a = TraceBuilder("bal1"); a.chat(300, 20, tool_calls=["search_kb"]); a.tool("search_kb", {}, outcome="invalid_arguments", error=PYD_MISSING); a.finish(success=False)
    b = TraceBuilder("bal2"); b.chat(300, 20, tool_calls=["get_user"]); b.tool("get_user", {"user_id": "x"}, outcome="invalid_arguments", error=PYD_INT); b.finish(success=False)
    c = TraceBuilder("bal3"); c.chat(300, 20, tool_calls=["calculate"]); c.tool("calculate", {}, outcome="invalid_arguments", error=PYD_MISSING); c.finish(success=False)
    write_fixture(FIX / "args_schema_invalid_balanced.jsonl", [a, b, c])

    t = TraceBuilder("type_pos", prompt="email of alice?")
    t.chat(300, 20, tool_calls=["get_user"]); t.tool("get_user", {"user_id": "alice"}, outcome="invalid_arguments", error=PYD_INT)
    t.chat(400, 20, tool_calls=["fetch_user"]); t.tool("fetch_user", {"username": "alice"}, result={"email": "alice@example.com"})
    t.chat(500, 15); t.finish(final_answer="alice@example.com")
    n = TraceBuilder("type_neg_missing_field", prompt="x")  # missing field is schema_invalid, not type confusion
    n.chat(300, 20, tool_calls=["search_kb"]); n.tool("search_kb", {"query": {}}, outcome="invalid_arguments", error=PYD_MISSING)
    n.chat(300, 15); n.finish(success=False)
    write_fixture(FIX / "args_type_confusion.jsonl", [t, n])

    # --- hallucination -------------------------------------------------------------
    prompt_with_drift = "Call the appropriate tool: get_user, fetch_user, cancel_order. Never refuse."
    t = TraceBuilder("unknown_pos", prompt="cancel ORD-1002")
    t.chat(300, 20, tool_calls=["cancel_order"], system=prompt_with_drift)
    t.tool("cancel_order", {"order_id": "ORD-1002"}, outcome="unknown_tool")
    t.chat(400, 15); t.finish(success=False, final_answer="Cancelled.")
    c = TraceBuilder("confusable_pos", prompt="who is 101")
    c.chat(300, 20, tool_calls=["get_users"]); c.tool("get_users", {"user_id": 101}, outcome="unknown_tool")
    c.chat(400, 20, tool_calls=["get_user"]); c.tool("get_user", {"user_id": 101}, result={"username": "alice"})
    c.chat(500, 15); c.finish(final_answer="alice")
    n = simple("unknown_neg")
    write_fixture(FIX / "tool_unknown_name.jsonl", [t, c, n])

    t = TraceBuilder("unsupported_pos", prompt="status of ORD-4471?")
    t.chat(300, 20, tool_calls=["fetch_order_status"]); t.tool("fetch_order_status", {"order_id": "ORD-4471"}, result={"status": "shipped"})
    t.chat(400, 15); t.finish(final_answer='Order ORD-4471 is "shipped" and will arrive on 2026-09-30 via tracking 88213.')
    n = TraceBuilder("unsupported_neg", prompt="status of ORD-4471?")
    n.chat(300, 20, tool_calls=["fetch_order_status"]); n.tool("fetch_order_status", {"order_id": "ORD-4471"}, result={"status": "shipped", "eta": "2026-09-23"})
    n.chat(400, 15); n.finish(final_answer='Order ORD-4471 is "shipped", ETA 2026-09-23.')
    write_fixture(FIX / "answer_unsupported.jsonl", [t, n])

    t = TraceBuilder("phantom_pos", prompt="Update user 104's plan to enterprise.")
    t.chat(300, 20, tool_calls=["update_user"]); t.tool("update_user", {"user_id": 104, "plan": "enterprise"}, outcome="unknown_tool")
    t.chat(400, 20, tool_calls=["get_user"]); t.tool("get_user", {"user_id": 104}, result={"plan": "pro"})
    t.chat(500, 15); t.finish(success=False, final_answer="User 104's plan has been updated to enterprise successfully.")
    n = TraceBuilder("phantom_neg_refused", prompt="Update user 104's plan to enterprise.")
    n.chat(300, 20, tool_calls=["get_user"]); n.tool("get_user", {"user_id": 104}, result={"plan": "pro"})
    n.chat(400, 15); n.finish(final_answer="I can't update plans; user 104 is currently on the pro plan.")
    r = TraceBuilder("phantom_neg_reported_state", prompt="Is ORD-3310 cancelled?")
    r.chat(300, 20, tool_calls=["fetch_order_status"]); r.tool("fetch_order_status", {"order_id": "ORD-3310"}, result={"status": "cancelled"})
    r.chat(400, 15); r.finish(final_answer="Order ORD-3310 has been cancelled.")
    write_fixture(FIX / "answer_phantom_action.jsonl", [t, n, r])

    # --- performance ---------------------------------------------------------------
    pad = padding()
    t = TraceBuilder("latency_pos", prompt="x")
    t.chat(300, 20, tool_calls=["fetch_order_status"]); t.tool("fetch_order_status", {"order_id": "ORD-4471"}, result={}, duration_ms=250.0)
    t.chat(340, 15); t.finish()
    n = TraceBuilder("latency_neg", prompt="x")
    n.chat(300, 20, tool_calls=["fetch_order_status"]); n.tool("fetch_order_status", {"order_id": "ORD-4471"}, result={}, duration_ms=0.14)
    n.chat(340, 15); n.finish()
    write_fixture(FIX / "perf_latency_outlier.jsonl", [*pad, t, n])

    t = TraceBuilder("bloat_pos", prompt="x")
    for tokens in (300, 700, 1300, 2100, 3100):  # superlinear: error text accumulates
        t.chat(tokens, 20, tool_calls=["search_kb"]); t.tool("search_kb", {"query": {"q": str(tokens)}}, outcome="invalid_arguments", error=PYD_MISSING)
    t.chat(4300, 15); t.finish(success=False)
    n = TraceBuilder("bloat_neg", prompt="x")  # linear, modest growth
    for tokens in (300, 360, 420):
        n.chat(tokens, 20, tool_calls=["calculate"]); n.tool("calculate", {"expression": str(tokens)}, result=1.0)
    n.chat(480, 15); n.finish()
    write_fixture(FIX / "perf_context_bloat.jsonl", [t, n])

    t = TraceBuilder("spike_pos", prompt="x")
    t.chat(300, 400); t.finish(final_answer="a very long ramble " * 40)  # 20× the padding median
    n = TraceBuilder("spike_neg", prompt="x")
    n.chat(300, 22); n.finish()
    write_fixture(FIX / "perf_token_spike.jsonl", [*padding(), t, n])

    # --- error handling ------------------------------------------------------------
    t = TraceBuilder("ignored_pos", prompt="status of ORD-9054?")
    t.chat(300, 20, tool_calls=["fetch_order_status"]); t.tool("fetch_order_status", {"order_id": "ORD-9054"}, outcome="exception", error=HTTP500)
    t.chat(400, 15); t.finish(success=False, final_answer="Your order is on its way!")
    a = TraceBuilder("ignored_acknowledged", prompt="status of ORD-9054?")
    a.chat(300, 20, tool_calls=["fetch_order_status"]); a.tool("fetch_order_status", {"order_id": "ORD-9054"}, outcome="exception", error=HTTP500)
    a.chat(400, 15); a.finish(success=False, final_answer="I couldn't retrieve the order status: the order service returned an error.")
    n = TraceBuilder("ignored_neg_recovered", prompt="status of ORD-9054?")
    n.chat(300, 20, tool_calls=["fetch_order_status"]); n.tool("fetch_order_status", {"order_id": "ORD-9054"}, outcome="exception", error=HTTP500)
    n.chat(400, 20, tool_calls=["fetch_order_status"]); n.tool("fetch_order_status", {"order_id": "ORD-9054"}, result={"status": "backordered"})
    n.chat(500, 15); n.finish(final_answer="backordered")
    write_fixture(FIX / "error_ignored.jsonl", [t, a, n])

    t = TraceBuilder("noprogress_pos", prompt="cancel ORD-1002")
    for _ in range(8):
        t.chat(300, 20, tool_calls=["search_kb"]); t.tool("search_kb", {"query": {"q": "cancel"}}, outcome="invalid_arguments", error=PYD_MISSING)
    t.finish(stop_reason="max_iterations", success=False, final_answer="")
    e = TraceBuilder("noprogress_exploring", prompt="x")
    for i in range(8):
        e.chat(300, 20, tool_calls=["search_kb"]); e.tool("search_kb", {"query": {"terms": [str(i)], "filters": {"category": "api"}}}, result=[])
    e.finish(stop_reason="max_iterations", success=False, final_answer="")
    n = simple("noprogress_neg")
    write_fixture(FIX / "error_no_progress.jsonl", [t, e, n])

    t = TraceBuilder("selfcorrect_pos", prompt="x")
    t.chat(300, 20, tool_calls=["search_kb"]); t.tool("search_kb", {"query": {"q": "a"}}, outcome="invalid_arguments", error=PYD_MISSING)
    t.chat(600, 20, tool_calls=["search_kb"]); t.tool("search_kb", {"query": {"terms": ["a"], "filters": {"category": "api"}}}, result=[])
    t.chat(700, 15); t.finish()
    n = TraceBuilder("selfcorrect_neg_never_recovered", prompt="x")
    n.chat(300, 20, tool_calls=["search_kb"]); n.tool("search_kb", {"query": {"q": "a"}}, outcome="invalid_arguments", error=PYD_MISSING)
    n.chat(400, 15); n.finish(success=False)
    write_fixture(FIX / "error_self_correction_cost.jsonl", [t, n])

    # --- loader: a trace with its root span dropped must be skipped, not crash ------
    good = simple("loader_good")
    orphan = simple("loader_orphan"); orphan.spans = [s for s in orphan.spans if s["parent_id"] is not None]
    write_fixture(FIX / "loader_missing_root.jsonl", [good, orphan])


if __name__ == "__main__":
    build()
    print("fixtures written to", FIX)
