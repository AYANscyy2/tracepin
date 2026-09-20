"""CLI: run every task sequentially, write spans to JSONL, print a failure-bucket summary."""

import json
import pathlib
import time
import uuid
from collections import Counter

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

app = typer.Typer(add_completion=False)
console = Console()


@app.command()
def run(
    tasks: str = typer.Option("tasks/tasks.jsonl", help="Path to tasks JSONL."),
    model: str = typer.Option("gemini-3.1-flash-lite"),
    prompt_version: str = typer.Option("v1", help="Name of prompts/<version>.md"),
    out: str = typer.Option("traces/run_{run_id}.jsonl", help="Trace output; {run_id} is substituted."),
    otlp: bool = typer.Option(True, help="Also export to Jaeger at localhost:4318."),
    only: str = typer.Option("", help="Comma-separated task ids to run (default: all)."),
    pause: float = typer.Option(0.0, help="Seconds to sleep between tasks (free-tier pacing)."),
):
    load_dotenv(".env"); load_dotenv(".env.local")
    run_id = uuid.uuid4().hex[:12]
    out_path = out.format(run_id=run_id)

    # Import after dotenv so TRACEPIN_CAPTURE_CONTENT from .env is honoured.
    from tracepin.agent import run_task
    from tracepin.llm import LLM
    from tracepin.tasks import load_tasks
    from tracepin.tools import TOOL_SCHEMAS
    from tracepin.tracing import setup_tracing, shutdown_tracing

    setup_tracing(model=model, prompt_version=prompt_version, jsonl_path=out_path, run_id=run_id, otlp=otlp)
    system_prompt = pathlib.Path("prompts", f"{prompt_version}.md").read_text(encoding="utf-8").strip()
    llm = LLM(model=model, system_prompt=system_prompt, tool_schemas=TOOL_SCHEMAS)

    task_list = load_tasks(tasks)
    if only:
        wanted = set(only.split(","))
        task_list = [t for t in task_list if t.id in wanted]

    console.print(f"[bold]run_id[/] {run_id}  [bold]model[/] {model}  [bold]prompt[/] {prompt_version}  → {out_path}")
    results = []
    with Progress(TextColumn("{task.description}"), BarColumn(), TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(), console=console) as progress:
        bar = progress.add_task("running", total=len(task_list))
        for task in task_list:
            progress.update(bar, description=f"{task.id}")
            r = run_task(llm, task)
            results.append(r)
            mark = "[green]✓[/]" if r.success else "[red]✗[/]"
            progress.console.print(f"  {mark} {task.id} {r.stop_reason:14} iters={r.iterations} {r.final_answer[:70]!r}")
            progress.advance(bar)
            if pause:
                time.sleep(pause)
    shutdown_tracing()

    _summarize(out_path, results)


def _summarize(out_path: str, results) -> None:
    outcomes: Counter = Counter()
    tool_calls = 0
    with open(out_path, encoding="utf-8") as f:
        for line in f:
            span = json.loads(line)
            attrs = span.get("attributes", {})
            if attrs.get("gen_ai.operation.name") == "execute_tool":
                tool_calls += 1
                outcomes[attrs.get("tracepin.tool.outcome", "?")] += 1

    passed = sum(r.success for r in results)
    stops = Counter(r.stop_reason for r in results)

    table = Table(title="Run summary")
    table.add_column("metric"); table.add_column("value")
    table.add_row("tasks run", str(len(results)))
    table.add_row("pass / fail", f"{passed} / {len(results) - passed}")
    table.add_row("stop reasons", ", ".join(f"{k}={v}" for k, v in stops.most_common()))
    table.add_row("total tool calls", str(tool_calls))
    table.add_row("by tool outcome", ", ".join(f"{k}={v}" for k, v in outcomes.most_common()) or "-")
    console.print(table)


if __name__ == "__main__":
    app()
