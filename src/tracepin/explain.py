"""Rich rendering for `tracepin rootcause` and `tracepin explain`."""

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from tracepin.analyze import Analysis, TraceSummary
from tracepin.report import OUTCOME_STYLE, SEV_STYLE, _sev, trace_tree
from tracepin.rootcause import CodeContext, code_context, rank_locations, suggested_fix, top_finding


def _blame_line(ctx: CodeContext) -> Text:
    if not ctx.blame:
        return Text("blame unavailable", style="dim")
    b = ctx.blame
    return Text.assemble(("blame  ", "dim"), (b.commit[:8], "bold"), f"  {b.author}  {b.authored_at:%Y-%m-%d}  ", (f'"{b.subject}"', "italic"))


def _snippet(ctx: CodeContext, radius: int | None = None) -> Syntax:
    lines = ctx.snippet.splitlines()
    if radius is not None:
        hit = next((i for i, ln in enumerate(lines) if ln.startswith(">")), 0)
        lines = lines[max(0, hit - radius): hit + radius + 1]
    code = "\n".join(ln[8:] if len(ln) > 8 else "" for ln in lines)  # strip marker + number gutter
    first = int(lines[0][2:6]) if lines else 1
    return Syntax(code, "python", line_numbers=True, start_line=first, highlight_lines={ctx.line}, theme="ansi_dark")


def render_rootcause(a: Analysis, console: Console, top: int = 5) -> None:
    ranked = rank_locations(a)
    console.print(Panel(
        f"[bold]{a.run['run_id']}[/]  prompt={a.run['prompt_version']}  model={a.run['model']}  "
        f"commit={a.run['git_sha'][:10]}  {a.run['pass_count']}/{a.run['task_count']} passed",
        title="[bold]Locations ranked by tasks affected[/]", border_style="blue", box=box.ROUNDED))
    if not ranked:
        console.print("  no findings carry a code_location")
        return
    for i, r in enumerate(ranked[:top], 1):
        head = Text.assemble((f"{i}. ", "bold"), (f"{r.file}:{r.line}", "bold cyan"), f"  {r.function or ''}",
                             (f"    {r.findings} findings / {len(r.tasks)} tasks", "bold"))
        console.print(head)
        console.print(Text("   " + "  ".join(f"{d}×{n}" for d, n in r.detectors.items()), style="dim"))
        if r.context:
            console.print(Text("   ") + _blame_line(r.context))
            console.print(Text(f"   source @ {r.context.source_ref[:10]}", style="dim"))
            console.print(_snippet(r.context, radius=2))
            if r.context.permalink:
                console.print(Text("   " + r.context.permalink, style="underline"))
        console.print()
    if len(ranked) > top:
        console.print(Text(f"  … {len(ranked) - top} more location(s)", style="dim"))


def find_trace(a: Analysis, key: str) -> TraceSummary | None:
    k = key.replace("0x", "")
    return next((t for t in a.traces if t.task_id == key or t.trace_id.replace("0x", "").startswith(k)), None)


def render_explain(a: Analysis, ts: TraceSummary, console: Console) -> None:
    findings = [f for f in a.findings if f.trace_id == ts.trace_id]
    mark = "[green]passed[/]" if ts.success else "[red]failed[/]"
    console.print(Panel(
        Text.assemble((ts.task_prompt or "(prompt not recorded)", "bold"), "\n",
                      (f"{ts.task_id}  {ts.trace_id}  {mark}  {ts.stop_reason}  {ts.iterations} iterations  "
                       f"{ts.input_tokens:,}/{ts.output_tokens:,} tokens  {ts.duration_ms / 1000:.1f}s", "")),
        title=f"[bold]tracepin explain[/]  run {a.run['run_id']}  prompt={a.run['prompt_version']}  model={a.run['model']}",
        border_style="blue", box=box.ROUNDED))
    console.print(Text(f"answer: {ts.final_answer[:300]!r}", style="italic"))
    console.print()
    console.print(trace_tree(ts, findings, verbose=True))
    console.print()
    top = top_finding(findings)
    if top:
        console.rule(Text(f"root cause of the top finding: {top.detector_id}", style="bold"))
        if top.code_location:
            ctx = code_context(top.code_location, a.run["git_sha"])
            console.print(Text.assemble((f"{ctx.file}:{ctx.line}", "bold cyan"), f"  {ctx.function or ''}",
                                        ("  (inferred from the tool the rejected call targeted)" if top.code_location.get("inferred_from_tool") else "", "dim")))
            console.print(_blame_line(ctx))
            console.print(Text(f"source @ {ctx.source_ref[:10]}", style="dim"))
            console.print(_snippet(ctx))
            if ctx.permalink:
                console.print(Text(ctx.permalink, style="underline"))
        else:
            console.print(Text("no code_location: the implicated call never reached a function (that is the finding)", style="dim"))
        t = Table(box=box.SIMPLE, show_header=True, title="suggested fix (rule-based, see rootcause.FIX_CATEGORIES)", title_justify="left")
        t.add_column("finding"); t.add_column("fix category")
        for f in findings:
            t.add_row(Text.assemble(_sev(f.severity.value), f" {f.detector_id}"), suggested_fix(f))
        console.print(t)
