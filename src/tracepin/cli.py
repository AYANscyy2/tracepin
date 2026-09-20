"""tracepin: run | analyze | report | compare"""

import pathlib

import typer
from rich.console import Console

from tracepin.runner import run as _run

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Agent reliability toolkit.")
app.command("run", help="Run the tasks against the agent and write a trace JSONL.")(_run)


@app.command()
def analyze(
    trace_file: str = typer.Argument(..., help="traces/run_<id>.jsonl"),
    out: str = typer.Option("", help="Findings JSON path (default findings/<stem>.json)."),
):
    """Run every detector over a trace file and write findings JSON."""
    from tracepin.analyze import analyze_file

    out = out or str(pathlib.Path("findings") / (pathlib.Path(trace_file).stem + ".json"))
    a = analyze_file(trace_file, out)
    s = a.summary
    Console().print(
        f"[bold]{a.run['run_id']}[/] {a.run['pass_count']}/{a.run['task_count']} passed · "
        f"{s['total']} findings ({s['by_severity']}) over {s['traces_with_findings']} traces → {out}"
        + (f"  [red]{a.run['skipped_traces']} traces skipped[/]" if a.run["skipped_traces"] else "")
    )


@app.command()
def report(
    findings_file: str = typer.Argument(...),
    detector: str = typer.Option(None, help="Only this detector id."),
    severity: str = typer.Option(None, help="high | medium | low"),
    trace: str = typer.Option(None, help="Trace id prefix or task id: single-trace deep dive."),
    top: int = typer.Option(5, help="How many offending traces to expand."),
    svg: str = typer.Option("", help="Also save the rendered report as SVG."),
    width: int = typer.Option(0, help="Console width (0 = auto)."),
):
    """Readable report. Exit code 1 if any HIGH finding exists (CI gate)."""
    from tracepin.analyze import Analysis
    from tracepin.report import render

    console = Console(record=bool(svg), width=width or None)
    code = render(Analysis.load(findings_file), console, detector=detector, severity=severity, trace=trace, top=top)
    if svg:
        console.save_svg(svg, title="tracepin report")
    raise typer.Exit(code)


@app.command()
def compare(
    findings_a: str = typer.Argument(..., help="Baseline findings JSON."),
    findings_b: str = typer.Argument(..., help="Candidate findings JSON."),
    svg: str = typer.Option("", help="Also save the rendered diff as SVG."),
    width: int = typer.Option(0),
):
    """Regression diff of two runs. Exit code 1 if any task regressed."""
    from tracepin.analyze import Analysis
    from tracepin.compare import compare as _compare

    console = Console(record=bool(svg), width=width or None)
    code = _compare(Analysis.load(findings_a), Analysis.load(findings_b), console)
    if svg:
        console.save_svg(svg, title="tracepin compare")
    raise typer.Exit(code)


if __name__ == "__main__":
    app()


@app.command()
def rootcause(
    findings_file: str = typer.Argument(...),
    top: int = typer.Option(5, help="How many locations to show."),
    svg: str = typer.Option(""),
    width: int = typer.Option(0),
):
    """Rank source locations by the number of tasks their findings break, with git blame."""
    from tracepin.analyze import Analysis
    from tracepin.explain import render_rootcause

    console = Console(record=bool(svg), width=width or None)
    render_rootcause(Analysis.load(findings_file), console, top=top)
    if svg:
        console.save_svg(svg, title="tracepin rootcause")


@app.command()
def explain(
    findings_file: str = typer.Argument(...),
    trace: str = typer.Argument(..., help="Trace id (prefix ok) or task id."),
    svg: str = typer.Option(""),
    width: int = typer.Option(0),
):
    """One trace: prompt, findings, timeline, code context of the top finding, suggested fix."""
    from tracepin.analyze import Analysis
    from tracepin.explain import find_trace, render_explain

    a = Analysis.load(findings_file)
    ts = find_trace(a, trace)
    if ts is None:
        raise typer.BadParameter(f"no trace matching {trace!r} in {findings_file}")
    console = Console(record=bool(svg), width=width or None)
    render_explain(a, ts, console)
    if svg:
        console.save_svg(svg, title="tracepin explain")


@app.command()
def repro(
    findings_file: str = typer.Argument(...),
    trace: str = typer.Argument(..., help="Trace id (prefix ok) or task id."),
    out: str = typer.Option("", help="Script path (default repro/<task>_repro.py)."),
    trace_file: str = typer.Option("", help="Run JSONL to extract the recorded spans from (default traces/run_<run_id>*.jsonl)."),
):
    """Emit a standalone script that re-runs the task and exits 0 only if the failure reproduces."""
    import glob

    from tracepin.analyze import Analysis
    from tracepin.explain import find_trace
    from tracepin.repro import generate, signature

    a = Analysis.load(findings_file)
    ts = find_trace(a, trace)
    if ts is None:
        raise typer.BadParameter(f"no trace matching {trace!r} in {findings_file}")
    if not trace_file:
        hits = glob.glob(f"traces/run_*{a.run['run_id']}*.jsonl")
        trace_file = hits[0] if hits else ""
    path = generate(a, ts, trace_file or None, pathlib.Path(out or f"repro/{ts.task_id}_repro.py"))
    Console().print(f"wrote [bold]{path}[/]  signature={signature(a, ts.trace_id)}"
                    + (f"  spans → {path.with_name(ts.task_id + '_trace.jsonl')}" if trace_file else "  [yellow](no trace file found; spans not extracted)[/]"))
