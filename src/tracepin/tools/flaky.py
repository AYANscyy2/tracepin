"""fetch_order_status raises a 500 on ~20% of calls.

The RNG is seeded per (task_id, call_index) so reruns reproduce the same failures.
"""

import hashlib
from contextvars import ContextVar

from pydantic import BaseModel

from tracepin.instrument import traced_tool

# Set by the agent loop before each task so flakiness is keyed to the task.
current_task_id: ContextVar[str] = ContextVar("current_task_id", default="none")
_call_counts: dict[str, int] = {}

FAILURE_RATE = 0.20

_ORDERS = {
    "ORD-4471": {"order_id": "ORD-4471", "status": "shipped", "eta": "2026-09-23"},
    "ORD-1002": {"order_id": "ORD-1002", "status": "processing", "eta": "2026-09-25"},
    "ORD-7788": {"order_id": "ORD-7788", "status": "delivered", "eta": "2026-09-18"},
    "ORD-3310": {"order_id": "ORD-3310", "status": "cancelled", "eta": None},
    "ORD-9054": {"order_id": "ORD-9054", "status": "backordered", "eta": "2026-10-04"},
    "ORD-2201": {"order_id": "ORD-2201", "status": "shipped", "eta": "2026-09-22"},
    "ORD-6120": {"order_id": "ORD-6120", "status": "processing", "eta": "2026-09-27"},
    "ORD-8833": {"order_id": "ORD-8833", "status": "delivered", "eta": "2026-09-15"},
}


class UpstreamError(RuntimeError):
    pass


class FetchOrderStatusArgs(BaseModel):
    order_id: str


def _should_fail(task_id: str, call_index: int) -> bool:
    digest = hashlib.sha256(f"{task_id}:{call_index}".encode()).digest()
    return int.from_bytes(digest[:4], "big") / 2**32 < FAILURE_RATE


@traced_tool(description="Get the current status of an order by order_id.")
def fetch_order_status(order_id: str) -> dict:
    task_id = current_task_id.get()
    idx = _call_counts.get(task_id, 0)
    _call_counts[task_id] = idx + 1
    if _should_fail(task_id, idx):
        raise UpstreamError("HTTP 500: order service unavailable")
    order = _ORDERS.get(order_id)
    if order is None:
        raise KeyError(f"no order with order_id={order_id!r}")
    return order
