"""Tool registry: callables, their Pydantic argument models, and JSON schemas for the model."""

from typing import Callable

from pydantic import BaseModel

from tracepin.tools.calc import CalculateArgs, calculate
from tracepin.tools.docs import SearchKBArgs, search_kb
from tracepin.tools.flaky import FetchOrderStatusArgs, fetch_order_status
from tracepin.tools.users import FetchUserArgs, GetUserArgs, fetch_user, get_user

TOOLS: dict[str, Callable] = {
    "get_user": get_user,
    "fetch_user": fetch_user,
    "fetch_order_status": fetch_order_status,
    "search_kb": search_kb,
    "calculate": calculate,
}

MODELS: dict[str, type[BaseModel]] = {
    "get_user": GetUserArgs,
    "fetch_user": FetchUserArgs,
    "fetch_order_status": FetchOrderStatusArgs,
    "search_kb": SearchKBArgs,
    "calculate": CalculateArgs,
}


def _schema(model: type[BaseModel]) -> dict:
    """Flat JSON schema for the model's tool declaration.

    search_kb's nested schema is intentionally collapsed to `{"query": object}` so the
    model only sees the terse description — the whole point of that tool.
    """
    if model is SearchKBArgs:
        return {"type": "object", "properties": {"query": {"type": "object"}}, "required": ["query"]}
    s = model.model_json_schema()
    return {"type": "object", "properties": s["properties"], "required": s.get("required", [])}


TOOL_SCHEMAS: list[dict] = [
    {
        "name": name,
        "description": fn.__tracepin_description__,
        "parameters": _schema(MODELS[name]),
    }
    for name, fn in TOOLS.items()
]
