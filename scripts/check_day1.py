"""Day 1 acceptance checks against a trace JSONL. Usage: python scripts/check_day1.py traces/run_x.jsonl"""

import json
import pathlib
import sys
from collections import Counter, defaultdict


def main(path: str) -> int:
    spans = [json.loads(l) for l in pathlib.Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    ok = True

    def report(name, passed, detail=""):
        nonlocal ok
        ok &= bool(passed)
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}  {detail}")

    res = [s["resource"]["attributes"] for s in spans]
    report("every line has resource run.id + prompt.version",
           all("tracepin.run.id" in r and "tracepin.prompt.version" in r for r in res), f"{len(spans)} spans")

    tools = [s for s in spans if s["attributes"].get("gen_ai.operation.name") == "execute_tool"]
    ran = [s for s in tools if s["attributes"].get("tracepin.tool.outcome") in ("ok", "exception")]
    missing = [s for s in ran if "code.file.path" not in s["attributes"] or "code.line.number" not in s["attributes"]]
    report("every executed tool span has code.file.path + code.line.number", not missing, f"{len(ran)} executed")
    if ran:
        a = ran[0]["attributes"]
        line = pathlib.Path(a["code.file.path"]).read_text().splitlines()[a["code.line.number"] - 1]
        report("code.line.number points at a real def", "traced_tool" in line or "def " in line, f"{a['code.function.name']} -> {line.strip()[:60]}")

    outcomes = Counter(s["attributes"].get("tracepin.tool.outcome") for s in tools)
    report("unknown_tool >= 3", outcomes["unknown_tool"] >= 3, f"got {outcomes['unknown_tool']}")
    report("invalid_arguments >= 3", outcomes["invalid_arguments"] >= 3, f"got {outcomes['invalid_arguments']}")

    roots = [s for s in spans if s["attributes"].get("gen_ai.operation.name") == "invoke_agent"]
    stops = Counter(s["attributes"].get("tracepin.run.stop_reason") for s in roots)
    report("max_iterations >= 2 tasks", stops["max_iterations"] >= 2, f"stop_reasons={dict(stops)}")

    # same tool + identical args 3+ times within one trace
    per_trace = defaultdict(Counter)
    for s in tools:
        per_trace[s["context"]["trace_id"]][(s["attributes"]["gen_ai.tool.name"], s["attributes"].get("tracepin.tool.arguments"))] += 1
    repeats = [(t, k, n) for t, c in per_trace.items() for k, n in c.items() if n >= 3]
    report("some task repeated identical tool call 3+ times", bool(repeats),
           f"{len(repeats)} cases; e.g. {repeats[0][1][0]} x{repeats[0][2]}" if repeats else "")

    chats = [s for s in spans if s["attributes"].get("gen_ai.operation.name") == "chat"]
    chats_ok = [s for s in chats if s["status"]["status_code"] != "ERROR"]
    report("token counts non-zero on every chat span",
           chats_ok and all(s["attributes"].get("gen_ai.usage.input_tokens", 0) > 0 and s["attributes"].get("gen_ai.usage.output_tokens", 0) > 0 for s in chats_ok),
           f"{len(chats_ok)} chat spans")

    succ = sum(1 for s in roots if s["attributes"].get("tracepin.run.success"))
    print(f"\n  tasks={len(roots)} pass={succ} tool_calls={len(tools)} outcomes={dict(outcomes)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
