"""Regression diff between two findings files, matched by task_id."""

from collections import Counter

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tracepin.analyze import Analysis, TraceSummary


def _signature(ts: TraceSummary) -> tuple:
    """What a failure 'looks like' to the detectors, so STILL FAILING can say 'same bug or new one'."""
    return tuple(sorted(Counter(ts.finding_ids).items()))


def _delta(a: float, b: float, unit: str = "", pct: bool = True, lower_is_better: bool = True) -> Text:
    d = b - a
    arrow = "→" if d == 0 else ("▲" if d > 0 else "▼")
    worse = d > 0 if lower_is_better else d < 0
    style = "dim" if d == 0 else ("red" if worse else "green")
    txt = f"{a:,.0f}{unit} {arrow} {b:,.0f}{unit}"
    if pct and a:
        txt += f" ({d / a:+.0%})"
    return Text(txt, style=style)


def compare(a: Analysis, b: Analysis, console: Console) -> int:
    ta = {t.task_id: t for t in a.traces}
    tb = {t.task_id: t for t in b.traces}
    common = sorted(set(ta) & set(tb))
    only_a, only_b = sorted(set(ta) - set(tb)), sorted(set(tb) - set(ta))

    console.print(Panel(
        f"[bold]A[/] {a.run['run_id']}  prompt={a.run['prompt_version']}  model={a.run['model']}  "
        f"{a.run['pass_count']}/{a.run['task_count']} passed\n"
        f"[bold]B[/] {b.run['run_id']}  prompt={b.run['prompt_version']}  model={b.run['model']}  "
        f"{b.run['pass_count']}/{b.run['task_count']} passed",
        title="[bold]tracepin compare[/]", border_style="blue", box=box.ROUNDED,
    ))
    if only_a or only_b:
        console.print(Panel(
            f"Task sets differ — comparing the {len(common)} tasks in both.\n"
            f"only in A: {only_a or '-'}\nonly in B: {only_b or '-'}",
            title="[bold red]WARNING[/]", border_style="red",
        ))

    regressed = [t for t in common if ta[t].success and not tb[t].success]
    fixed = [t for t in common if not ta[t].success and tb[t].success]
    still = [t for t in common if not ta[t].success and not tb[t].success]
    unchanged = [t for t in common if ta[t].success and tb[t].success]

    def bucket(title, tasks, style, note=None):
        console.rule(Text(f"{title} ({len(tasks)})", style=style))
        if not tasks:
            console.print(Text("  none", style="dim"))
            return
        t = Table(box=box.SIMPLE, show_header=True)
        t.add_column("task"); t.add_column("A"); t.add_column("B"); t.add_column("note")
        for task in tasks:
            x, y = ta[task], tb[task]
            t.add_row(task,
                      f"{x.stop_reason} {x.iterations}it {', '.join(sorted(set(x.finding_ids))) or '-'}",
                      f"{y.stop_reason} {y.iterations}it {', '.join(sorted(set(y.finding_ids))) or '-'}",
                      note(x, y) if note else "")
        console.print(t)

    def still_note(x, y):
        return "[dim]same signature[/]" if _signature(x) == _signature(y) else "[yellow]signature changed — different bug[/]"

    bucket("REGRESSED  passed in A, fails in B", regressed, "bold red")
    bucket("FIXED  failed in A, passes in B", fixed, "bold green")
    bucket("STILL FAILING", still, "yellow", still_note)
    console.rule(Text(f"UNCHANGED PASSES ({len(unchanged)})", style="dim"))

    # detector-level delta
    ca = Counter(f.detector_id for f in a.findings if f.task_id in common or f.scope == "run")
    cb = Counter(f.detector_id for f in b.findings if f.task_id in common or f.scope == "run")
    t = Table(title="Findings per detector", box=box.SIMPLE_HEAVY, title_justify="left")
    t.add_column("detector", style="bold"); t.add_column("A", justify="right"); t.add_column("B", justify="right"); t.add_column("Δ")
    for det in sorted(set(ca) | set(cb), key=lambda d: -(ca[d] + cb[d])):
        d = cb[det] - ca[det]
        arrow = Text("→ 0", style="dim") if d == 0 else Text(f"{'▲' if d > 0 else '▼'} {d:+d}", style="red" if d > 0 else "green")
        t.add_row(det, str(ca[det]), str(cb[det]), arrow)
    console.print(t)

    def agg(an, keys):
        return sum(getattr(an_t, k) for an_t in (an[x] for x in common) for k in keys)

    t = Table(title="Aggregate deltas (common tasks)", box=box.SIMPLE_HEAVY, title_justify="left")
    t.add_column("metric", style="bold"); t.add_column("A → B")
    t.add_row("passed", _delta(sum(ta[x].success for x in common), sum(tb[x].success for x in common), pct=False, lower_is_better=False))
    t.add_row("input tokens", _delta(agg(ta, ["input_tokens"]), agg(tb, ["input_tokens"])))
    t.add_row("output tokens", _delta(agg(ta, ["output_tokens"]), agg(tb, ["output_tokens"])))
    t.add_row("wall clock", _delta(agg(ta, ["duration_ms"]) / 1000, agg(tb, ["duration_ms"]) / 1000, "s"))
    t.add_row("tool calls", _delta(sum(len(ta[x].timeline) for x in common), sum(len(tb[x].timeline) for x in common)))
    t.add_row("iterations", _delta(agg(ta, ["iterations"]), agg(tb, ["iterations"])))
    console.print(t)

    return 1 if regressed else 0
