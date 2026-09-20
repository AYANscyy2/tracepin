"""Finding -> source line -> blame -> permalink. Plus the run-level ranking of locations."""

import pathlib
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone

from pydantic import BaseModel

from tracepin.analyze import Analysis
from tracepin.detectors import Finding

SNIPPET_RADIUS = 8


class BlameInfo(BaseModel):
    commit: str
    author: str
    authored_at: datetime
    subject: str  # commit message first line


class CodeContext(BaseModel):
    file: str  # repo-relative
    line: int
    function: str | None
    snippet: str  # ±8 lines around the hit, with line numbers, hit marked with '>'
    blame: BlameInfo | None
    permalink: str | None  # github.com/<repo>/blob/<sha>/<path>#L<line>
    source_ref: str  # the commit the snippet was read from


class LocationRank(BaseModel):
    file: str
    line: int
    function: str | None
    findings: int
    tasks: list[str]
    detectors: dict[str, int]
    context: CodeContext | None = None


def _git(*args: str, cwd: str | pathlib.Path | None = None) -> str | None:
    try:
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL, cwd=cwd).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def repo_root() -> pathlib.Path | None:
    top = _git("rev-parse", "--show-toplevel")
    return pathlib.Path(top) if top else None


def relpath(file: str) -> str:
    """Absolute path from code.file.path -> repo-relative, so blame and permalinks work."""
    root = repo_root()
    p = pathlib.Path(file)
    if root and p.is_absolute():
        try:
            return str(p.relative_to(root))
        except ValueError:
            pass
    return str(p)


def blame_line(path: str, line: int, sha: str = "HEAD") -> BlameInfo | None:
    """Blame at the commit that produced the trace, not HEAD: today's code may not be what ran."""
    out = _git("blame", "-L", f"{line},{line}", "--porcelain", sha, "--", path)
    if not out:
        return None
    lines = out.splitlines()
    fields = {"commit": lines[0].split()[0]}
    for ln in lines[1:]:
        if ln.startswith("\t"):
            break
        key, _, value = ln.partition(" ")
        fields[key] = value
    try:
        ts = datetime.fromtimestamp(int(fields["author-time"]), tz=timezone.utc)
    except (KeyError, ValueError):
        return None
    return BlameInfo(commit=fields["commit"], author=fields.get("author", "?"), authored_at=ts,
                     subject=fields.get("summary", ""))


def source_at(path: str, sha: str) -> tuple[list[str], str]:
    """Source lines at `sha` (what actually ran); fall back to the working tree."""
    text = _git("show", f"{sha}:{path}") if sha and sha != "unknown" else None
    if text is not None:
        return text.splitlines(), sha
    root = repo_root() or pathlib.Path.cwd()
    try:
        return (root / path).read_text(encoding="utf-8").splitlines(), "worktree"
    except OSError:
        return [], "missing"


def permalink(path: str, line: int, sha: str) -> str | None:
    url = _git("remote", "get-url", "origin")
    if not url:
        return None
    m = re.match(r"(?:git@github\.com:|https://github\.com/)([^/]+/[^/]+?)(?:\.git)?/?$", url)
    if not m:
        return None
    return f"https://github.com/{m.group(1)}/blob/{sha}/{path}#L{line}"


def code_context(location: dict, sha: str) -> CodeContext:
    path, line = relpath(location["file"]), int(location["line"])
    lines, ref = source_at(path, sha)
    lo, hi = max(1, line - SNIPPET_RADIUS), min(len(lines), line + SNIPPET_RADIUS)
    snippet = "\n".join(
        f"{'>' if n == line else ' '} {n:>4}  {lines[n - 1]}" for n in range(lo, hi + 1)
    )
    return CodeContext(
        file=path, line=line, function=location.get("function"), snippet=snippet,
        blame=blame_line(path, line, sha) if ref == sha else blame_line(path, line),
        permalink=permalink(path, line, sha), source_ref=ref,
    )


def rank_locations(analysis: Analysis, *, with_context: bool = True) -> list[LocationRank]:
    """Group findings by code_location, rank by distinct tasks broken."""
    groups: dict[tuple[str, int], list[Finding]] = defaultdict(list)
    for f in analysis.findings:
        if f.code_location:
            groups[(relpath(f.code_location["file"]), int(f.code_location["line"]))].append(f)
    ranked = []
    for (file, line), fs in groups.items():
        tasks = sorted({f.task_id for f in fs if f.scope == "trace"})
        detectors: dict[str, int] = defaultdict(int)
        for f in fs:
            detectors[f.detector_id] += 1
        ranked.append(LocationRank(
            file=file, line=line, function=fs[0].code_location.get("function"),
            findings=len(fs), tasks=tasks, detectors=dict(sorted(detectors.items(), key=lambda kv: -kv[1])),
            context=code_context(fs[0].code_location, analysis.run["git_sha"]) if with_context else None,
        ))
    ranked.sort(key=lambda r: (-len(r.tasks), -r.findings, r.file, r.line))
    return ranked


# --- suggested fix: rule-based on purpose ---------------------------------------------------
# An LLM-written suggestion would be unverifiable and would undercut a project whose
# premise is deterministic detection. These are the categories a human would reach for.

FIX_CATEGORIES: dict[str, str] = {
    "args.schema_invalid": "tool description / schema docs",
    "args.type_confusion": "tool description / schema docs (state the type)",
    "loop.repeated_call": "prompt: handle error feedback instead of resending",
    "loop.near_duplicate": "prompt: stop after an empty result; or tool: return a hint on empty",
    "loop.cycle": "prompt: handle error feedback; check the two tools are not confusable",
    "loop.retry_storm": "prompt: cap retries; tool: make the error message actionable",
    "tool.unknown_name": "prompt drift: remove the stale tool from the system prompt",
    "tool.confusable_name": "rename confusable tools",
    "answer.unsupported": "prompt: cite tool results; verify manually (heuristic finding)",
    "answer.phantom_action": "prompt: surface tool failures; never claim an action a tool did not perform",
    "perf.latency_outlier": "tool/model latency: investigate the slow dependency",
    "perf.context_bloat": "trim error feedback fed back into context",
    "perf.token_spike": "prompt: answer concisely / call the tool",
    "perf.unaccounted_time": "instrumentation gap: add spans or a working client timeout",
    "error.ignored": "prompt: surface tool failures",
    "error.no_progress": "prompt: allow refusal; lower max_iterations",
    "error.self_correction_cost": "tool description / schema docs (avoid the first rejection)",
}


PERF = "perf."


def top_finding(findings: list[Finding]) -> Finding | None:
    """The finding worth explaining first: highest severity non-latency finding. Latency
    outliers only headline a trace that has nothing else on it."""
    ordered = sorted(findings, key=lambda f: ({"high": 0, "medium": 1, "low": 2}[f.severity.value], -f.confidence))
    return next((f for f in ordered if not f.detector_id.startswith(PERF)), ordered[0] if ordered else None)


def suggested_fix(f: Finding) -> str:
    if f.detector_id == "tool.unknown_name" and f.evidence.get("advertised_in_system_prompt") is False:
        return "model invented a tool: prompt: list the tools explicitly"
    return FIX_CATEGORIES.get(f.detector_id, "unclassified")
