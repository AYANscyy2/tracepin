"""Task loading and answer checkers."""

import json
import pathlib
import re
from dataclasses import dataclass, field

REFUSAL_PATTERNS = re.compile(
    r"(can(?:'|no)?t|cannot|unable to|not able to|don't have|do not have|no (?:tool|way|access)|"
    r"not possible|outside (?:of )?my|beyond my|isn't something i can|not something i can)",
    re.IGNORECASE,
)


@dataclass
class Task:
    id: str
    prompt: str
    expected: str
    checker: str
    tags: list[str] = field(default_factory=list)


def load_tasks(path: str) -> list[Task]:
    tasks = []
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            tasks.append(Task(**json.loads(line)))
    return tasks


def _numbers(text: str) -> list[float]:
    return [float(n.replace(",", "")) for n in re.findall(r"-?\d[\d,]*\.?\d*", text)]


def check(task: Task, answer: str) -> bool:
    a = (answer or "").strip()
    if task.checker == "exact":
        return a.lower() == task.expected.lower()
    if task.checker == "contains":
        return task.expected.lower() in a.lower()
    if task.checker == "numeric":
        target = float(task.expected)
        return any(abs(n - target) < 1e-6 * max(1.0, abs(target)) for n in _numbers(a))
    if task.checker == "refusal":
        return bool(REFUSAL_PATTERNS.search(a))
    raise ValueError(f"unknown checker {task.checker!r}")
