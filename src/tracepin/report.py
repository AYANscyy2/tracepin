"""Rich terminal report over a findings file. Exit code 1 if any HIGH finding exists."""

import pathlib
from collections import Counter, defaultdict

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from tracepin.analyze import Analysis, TraceSummary
from tracepin.detectors import Finding

SEV_STYLE = {"high": "bold red", "medium": "yellow", "low": "dim cyan"}
OUTCOME_STYLE = {"ok": "green", "exception": "red", "invalid_arguments": "magenta", "unknown_tool": "bold red"}
TOP_N = 5


def _sev(s: str) -> Text:
    return Text(s.upper(), style=SEV_STYLE.get(s, ""))


def _loc(f: Finding) -> str:
    loc = f.code_location
    if not loc:
        return "-"
    path = pathlib.Path(loc["file"])
    try:
        path = path.relative_to(pathlib.Path.cwd())
    except ValueError:
        pass
    s = f"{path}:{loc['line']}"
    return s + (" (tool)" if loc.get("inferred_from_tool") else "")


def _short(trace_id: str) -> str:
    return trace_id.replace("0x", "")[:10]


def filter_findings(a: Analysis, detector: str | None, severity: str | None, trace: str | None) -> list[Finding]:
    out = a.findings
    if detector:
        out = [f for f in out if f.detector_id == detector]
    if severity:
        out = [f for f in out if f.severity.value == severity]
    if trace:
        out = [f for f in out if f.trace_id.replace("0x", "").startswith(trace.replace("0x", "")) or f.task_id == trace]
    return out


def header(a: Analysis, findings: list[Finding]) -> Panel:
    r = a.run
    rate = r["pass_count"] / r["task_count"] if r["task_count"] else 0
    sev = Counter(f.severity.value for f in findings)
    t = Table.grid(padding=(0, 2))
    t.add_column(style="bold"); t.add_column()
    t.add_row("run", f"{r['run_id']}   model={r['model']}   prompt={r['prompt_version']}   sha={r['git_sha'][:10]}")
    t.add_row("tasks", f"{r['pass_count']}/{r['task_count']} passed ({rate:.0%})"
              + (f"   [red]{r['skipped_traces']} traces skipped at load[/]" if r.get("skipped_traces") else ""))
    t.add_row("tokens", f"{r['total_input_tokens']:,} in / {r['total_output_tokens']:,} out   wall {r['total_wall_ms'] / 1000:.0f}s")
    t.add_row("findings", Text.assemble(
        (f"{len(findings)} total   ", "bold"),
        (f"{sev['high']} high", SEV_STYLE["high"]), "   ",
        (f"{sev['medium']} medium", SEV_STYLE["medium"]), "   ",
        (f"{sev['low']} low", SEV_STYLE["low"]),
        f"   across {len({f.trace_id for f in findings if f.scope == 'trace'})} traces",
    ))
    return Panel(t, title="[bold]tracepin report[/]", border_style="blue", box=box.ROUNDED)


def by_detector(findings: list[Finding]) -> Table:
    t = Table(title="Findings by detector", box=box.SIMPLE_HEAVY, title_justify="left")
    t.add_column("detector", style="bold"); t.add_column("count", justify="right")
    t.add_column("severity"); t.add_column("conf", justify="right"); t.add_column("affected tasks")
    groups: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        groups[f.detector_id].append(f)
    order = {"high": 0, "medium": 1, "low": 2}
    for det, fs in sorted(groups.items(), key=lambda kv: (min(order[f.severity.value] for f in kv[1]), -len(kv[1]))):
        worst = min(fs, key=lambda f: order[f.severity.value]).severity.value
        tasks = sorted({f.task_id for f in fs})
        shown = ", ".join(tasks[:8]) + (f" +{len(tasks) - 8}" if len(tasks) > 8 else "")
        conf = sum(f.confidence for f in fs) / len(fs)
        t.add_row(det, str(len(fs)), _sev(worst), f"{conf:.2f}", shown)
    return t


def run_level(findings: list[Finding]) -> Group | None:
    fs = [f for f in findings if f.scope == "run"]
    if not fs:
        return None
    panels = [
        Panel(Text.assemble((f.title + "\n", "bold"), f.detail, ("\n" + _loc(f), "dim")),
              title=Text.assemble(_sev(f.severity.value), f"  {f.detector_id}"), border_style=SEV_STYLE[f.severity.value])
        for f in fs
    ]
    return Group(*panels)


def trace_tree(ts: TraceSummary, findings: list[Finding], verbose: bool) -> Tree:
    flagged: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        for sid in f.span_ids:
            flagged[sid].append(f)
    mark = "[green]✓[/]" if ts.success else "[red]✗[/]"
    root = Tree(
        f"{mark} [bold]{ts.task_id}[/] [dim]{_short(ts.trace_id)}[/]  {ts.stop_reason}  "
        f"{ts.iterations} iters  {ts.input_tokens:,}/{ts.output_tokens:,} tok  {ts.duration_ms / 1000:.1f}s  "
        f"[bold]{len(findings)} finding(s)[/]  [dim]{ts.tags}[/]"
    )
    tl = root.add("[dim]timeline[/]")
    for i, c in enumerate(ts.timeline, 1):
        style = OUTCOME_STYLE.get(c["outcome"] or "", "")
        line = Text.assemble(
            f"{i:>2}. ", (f"{c['tool']}", "bold"), f"  {c['duration_ms']:.2f}ms  ", (c["outcome"] or "?", style)
        )
        if c.get("error"):
            line.append(f"  {c['error'][:70]}", style="dim")
        elif verbose:
            line.append(f"  {c['arguments'][:70]}", style="dim")
        hits = flagged.get(c["span_id"], [])
        if hits:
            line.append("  ◀ " + ", ".join(sorted({h.detector_id for h in hits})), style="bold yellow")
        tl.add(line)
    fl = root.add("[dim]findings[/]")
    for f in findings:
        node = fl.add(Text.assemble(_sev(f.severity.value), f" {f.detector_id}  ", (f.title, "bold"), (f"  conf={f.confidence:.2f}", "dim")))
        node.add(Text(f.detail[:300], style="dim"))
        if f.code_location:
            node.add(Text(_loc(f), style="cyan underline"))
    if verbose:
        root.add(Text(f"answer: {ts.final_answer[:200]!r}", style="italic"))
    return root


def render(a: Analysis, console: Console, *, detector=None, severity=None, trace=None, top: int = TOP_N) -> int:
    findings = filter_findings(a, detector, severity, trace)
    console.print(header(a, findings))
    rl = run_level(findings)
    if rl:
        console.print(rl)
    console.print(by_detector(findings))

    per_trace: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        if f.scope == "trace":
            per_trace[f.trace_id].append(f)
    summaries = {t.trace_id: t for t in a.traces}
    if trace:
        chosen = [tid for tid in per_trace]
        if not chosen:  # a trace with no findings still deserves a timeline
            chosen = [t.trace_id for t in a.traces
                      if t.trace_id.replace("0x", "").startswith(trace.replace("0x", "")) or t.task_id == trace]
        title = "Trace deep dive"
    else:
        chosen = sorted(per_trace, key=lambda tid: (-len(per_trace[tid]), tid))[:top]
        title = f"Top {len(chosen)} offending traces"
    if chosen:
        console.rule(f"[bold]{title}[/]")
        for tid in chosen:
            console.print(trace_tree(summaries[tid], per_trace.get(tid, []), verbose=bool(trace)))
            console.print()
    return 1 if any(f.severity.value == "high" for f in findings) else 0
