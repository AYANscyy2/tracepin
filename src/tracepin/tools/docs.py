"""search_kb: nested, badly documented schema on purpose. Produces malformed arguments."""

from datetime import date

from pydantic import BaseModel, Field

from tracepin.instrument import traced_tool


class KBFilters(BaseModel):
    category: str
    after: date | None = None


class KBQuery(BaseModel):
    terms: list[str] = Field(min_length=1)
    filters: KBFilters


class SearchKBArgs(BaseModel):
    query: KBQuery


_ARTICLES = [
    {"id": "kb-1", "title": "How to reset your password", "category": "account", "published": "2026-03-02", "terms": ["password", "reset", "login"]},
    {"id": "kb-2", "title": "Refund policy for annual plans", "category": "billing", "published": "2026-05-14", "terms": ["refund", "annual", "plan", "billing"]},
    {"id": "kb-3", "title": "Exporting data as CSV", "category": "data", "published": "2025-11-20", "terms": ["export", "csv", "data"]},
    {"id": "kb-4", "title": "Two-factor authentication setup", "category": "account", "published": "2026-07-01", "terms": ["2fa", "two-factor", "authentication", "security"]},
    {"id": "kb-5", "title": "Invoice download and tax IDs", "category": "billing", "published": "2026-01-09", "terms": ["invoice", "tax", "download", "billing"]},
    {"id": "kb-6", "title": "API rate limits explained", "category": "api", "published": "2026-06-30", "terms": ["api", "rate", "limit", "429"]},
    {"id": "kb-7", "title": "Webhook retry behaviour", "category": "api", "published": "2026-08-12", "terms": ["webhook", "retry", "api"]},
    {"id": "kb-8", "title": "Deleting your account", "category": "account", "published": "2025-09-05", "terms": ["delete", "account", "gdpr"]},
]


@traced_tool(
    description=(
        "Search the knowledge base. `query` must be an object of the form "
        '{"terms": ["<keyword>", ...], "filters": {"category": "<account|billing|data|api>", '
        '"after": "YYYY-MM-DD" or null}}. `terms` is a list of single keywords (not a sentence); '
        "`filters.category` is required; `after` restricts to articles published after that date."
    )
)
def search_kb(query: dict) -> list:
    # Day 1 shipped this with description="Search the knowledge base." — the Day 2 run-level
    # args.schema_invalid finding (100% of rejections on this tool) pointed here.
    q = KBQuery.model_validate(query)
    terms = {t.lower() for t in q.terms}
    out = []
    for a in _ARTICLES:
        if a["category"] != q.filters.category:
            continue
        if q.filters.after and date.fromisoformat(a["published"]) <= q.filters.after:
            continue
        if terms & set(a["terms"]):
            out.append({"id": a["id"], "title": a["title"], "published": a["published"]})
    return out
