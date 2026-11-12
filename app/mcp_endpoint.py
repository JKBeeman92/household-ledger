"""MCP server (#33): the month-close figures for Claude Code, Claude Desktop and any other MCP client, at `/mcp`.

Streamable-HTTP transport from the official `mcp` SDK, mounted in this process as one plain ASGI route. It is stateless and answers
with plain JSON (no SSE), so there is no session to hijack and nothing long-lived to proxy.

Auth is an API token (app/auth.py, #34) and nothing else: no cookie session, not even with AUTH_DISABLED, so the app-level CSRF and
session dependencies have nothing to protect here (they do not run on this route) and nothing to break it. Every tool needs the
`read` scope; `save_review` also needs `read+review`.

Only derived datasets leave through here (DATASETS below is an allow-list). Transactions, merchants and notes are never exposed, and
there is no `transactions` tool: the `verify` dataset, which carries merchant names, is deliberately not on the list."""
from __future__ import annotations
import json
import re
from contextlib import asynccontextmanager

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import auth, db, review, review_providers as providers

PATH = "/mcp"
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
# Derived datasets only. A dataset added by the pipeline later stays private until it is listed here.
DATASETS = ("monthly", "yearly", "cat_month", "cat_year", "item_year", "budget_items", "goals", "people", "employer", "debts",
            "recurring", "events", "exclusions", "slush", "reconciliation", "cpi", "review",
            "balance_totals",   # balances by kind and type (#143); `balances` itself names accounts and stays private
            "debt_to_income",   # #145
            "net_worth",   # #146: totals and the split, no account names
            "emergency_fund",   # #150
            "coming_up",   # #144: served with public labels only (PUBLIC below); `payoff` names accounts and stays private
            "decisions", "decision_summary", "plan_suggestions",
            "yearly_bills")   # #151: served without an account name where a bill is saved for in an account (PUBLIC)   # #155: read only; a decision's private note is never in the dataset
MAX_SECTIONS, MAX_HEADING, MAX_TEXT = 12, 200, 5000
INSTRUCTIONS = ("Read-only access to a household's month-close figures (budget months, not calendar months): per-month and per-category "
                "totals, envelopes planned vs spent, goals and the written month-close review. There are no transactions, merchants or notes. "
                "`save_review` writes a month's review and needs a token with the review scope.")
# datasets whose rows name an account: served with the name taken out (coming_up's label names a debt by its type instead)
PUBLIC = {"coming_up": lambda r: {"m": r["m"], "kind": r["kind"], "label": r["public_label"], "amount": r["amount"], "source": r["source"]},
          "yearly_bills": lambda r: {**r, "funded_by": r.get("funded_by") if r.get("funded_kind") != "account" else None}}


def _rows(conn, name: str) -> list[dict]:
    r = conn.execute("SELECT rows FROM datasets WHERE name=%s", (name,)).fetchone()
    return r["rows"] if r else []


def _month(m: str) -> str:
    if not isinstance(m, str) or not MONTH.match(m):
        raise ValueError("a month looks like 2025-03 (YYYY-MM)")
    return m


def _built_month(conn, m: str) -> str:
    if not any(r["m"] == _month(m) for r in _rows(conn, "monthly")):
        raise ValueError(f"{m} is not a built month; list_months shows the ones that are")
    return m


def _caller(ctx: Context) -> dict:
    return ctx.request_context.request.scope["state"]["api_token_user"]


def _sections(sections) -> list[dict]:
    if not isinstance(sections, list) or not 1 <= len(sections) <= MAX_SECTIONS:
        raise ValueError(f"sections must be a list of 1 to {MAX_SECTIONS} objects with heading and text")
    out = providers.parse_sections(json.dumps({"sections": sections}))   # one rule for what a stored section looks like
    for s in out:
        s["heading"], s["text"] = s["heading"].strip(), s["text"].strip()
        if not s["heading"] or not s["text"] or len(s["heading"]) > MAX_HEADING or len(s["text"]) > MAX_TEXT:
            raise ValueError(f"every section needs a heading (up to {MAX_HEADING} characters) and text (up to {MAX_TEXT})")
    return out


def build_server() -> FastMCP:
    mcp = FastMCP("household-ledger", instructions=INSTRUCTIONS, stateless_http=True, json_response=True, streamable_http_path=PATH,
                  transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))   # bearer tokens only, no ambient cookies to rebind

    @mcp.tool()
    def list_months() -> list[dict]:
        """The built budget months, newest first, with their income, spend, debt and net (nominal dollars) and whether a review is stored."""
        with db.pool.connection() as conn:
            reviewed = {r["month"] for r in conn.execute("SELECT month FROM reviews").fetchall()}
            rows = sorted(_rows(conn, "monthly"), key=lambda r: r["m"], reverse=True)
        return [{"month": r["m"], **{k: r[k] for k in ("income", "spend", "debt", "net")}, "has_review": r["m"] in reviewed} for r in rows]

    @mcp.tool()
    def get_dataset(name: str) -> list[dict]:
        """All rows of one derived dataset (the ones the dashboard draws from). Names: %s.""" % ", ".join(DATASETS)
        if name not in DATASETS:
            raise ValueError(f"unknown dataset {name!r}; available: {', '.join(DATASETS)}")
        with db.pool.connection() as conn:
            rows = _rows(conn, name)
        return [PUBLIC[name](r) for r in rows] if name in PUBLIC else rows

    @mcp.tool()
    def month_summary(m: str) -> dict:
        """One budget month (YYYY-MM): ledger totals, spend by category, each envelope's planned vs actual, and any life events
        (events: their labels; event_details: label with kind and person when set)."""
        with db.pool.connection() as conn:
            _built_month(conn, m)
            monthly = next(r for r in _rows(conn, "monthly") if r["m"] == m)
            cats = [{k: r[k] for k in ("category", "amount", "amount_real")} for r in _rows(conn, "cat_month") if r["m"] == m]
            items = [{k: r[k] for k in ("group", "item", "category", "planned", "actual")} for r in _rows(conn, "budget_items") if r["m"] == m]
            evs = [r for r in _rows(conn, "events") if r["m"] == m]
            events = [r["label"] for r in evs]
            details = [{k: r[k] for k in ("label", "kind", "person") if r.get(k)} for r in evs]
            has_review = conn.execute("SELECT 1 FROM reviews WHERE month=%s", (m,)).fetchone() is not None
        return {"month": m, "totals": monthly, "categories": cats, "envelopes": items, "events": events, "event_details": details, "has_review": has_review}

    @mcp.tool()
    def category_trend(category: str, months: int = 12) -> list[dict]:
        """A category's monthly spend (nominal and inflation-adjusted) over the latest `months` built months (1 to 120), oldest first; 0 where it had none."""
        if not isinstance(months, int) or not 1 <= months <= 120:
            raise ValueError("months must be a whole number from 1 to 120")
        with db.pool.connection() as conn:
            rows = _rows(conn, "cat_month")
            span = sorted(r["m"] for r in _rows(conn, "monthly"))[-months:]
        names = {r["category"].lower(): r["category"] for r in rows}
        if category.strip().lower() not in names:
            raise ValueError(f"unknown category {category!r}; available: {', '.join(sorted(names.values()))}")
        want = names[category.strip().lower()]
        by_m = {r["m"]: r for r in rows if r["category"] == want}
        return [{"month": m, "category": want, "amount": by_m[m]["amount"] if m in by_m else 0, "amount_real": by_m[m]["amount_real"] if m in by_m else 0} for m in span]

    @mcp.tool()
    def goals() -> list[dict]:
        """The household's standing targets (giving floor, savings targets, housing cap...) with their units and notes."""
        with db.pool.connection() as conn:
            return _rows(conn, "goals")

    @mcp.tool(name="review")
    def get_review(m: str) -> dict:
        """The stored month-close review for a budget month: its sections, who or what wrote it, and when."""
        with db.pool.connection() as conn:
            row = conn.execute("SELECT month, model, generated_at, hand_edited_at, sections FROM reviews WHERE month=%s", (_month(m),)).fetchone()
        if not row:
            raise ValueError(f"no review is stored for {m}")
        return {"month": row["month"], "written_by": row["model"], "generated_at": row["generated_at"].isoformat(),
                "hand_edited_at": row["hand_edited_at"].isoformat() if row["hand_edited_at"] else None, "sections": row["sections"]}

    @mcp.tool()
    def review_instructions() -> str:
        """The house instructions a month-close review is written to (the built-in text or the household's own): read them before save_review."""
        with db.pool.connection() as conn:
            return review.instructions(conn)

    @mcp.tool()
    def save_review(m: str, sections: list[dict], ctx: Context) -> dict:
        """Write (or replace) a budget month's review. `sections` is 1 to 12 objects {heading, text} (plain prose, no markdown), in order.
        Needs a token with the review scope. The review is marked as written by an agent through this token and replaces a hand-edited one."""
        tok = _caller(ctx)["token"]
        if "review" not in auth.SCOPES[tok["scope"]]:
            raise ValueError(f"this token's scope ({tok['scope']}) does not include review; create a read+review token to save reviews")
        secs = _sections(sections)
        with db.pool.connection() as conn:
            _built_month(conn, m)
            review.save_agent(conn, m, secs, f"agent via MCP ({tok['name']})")
        return {"month": m, "sections": len(secs), "saved": True}

    return mcp


class McpEndpoint:
    """ASGI app for the `/mcp` route plus the lifespan the SDK's session manager needs (a mounted app's own lifespan never runs).
    `run()` builds a fresh server each time, so the app can start more than once in a process (tests do)."""

    def __init__(self):
        self.manager = None

    @asynccontextmanager
    async def run(self):
        mcp = build_server()
        mcp.streamable_http_app()   # creates mcp.session_manager
        self.manager = mcp.session_manager
        async with self.manager.run():
            yield

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await JSONResponse({"detail": "not found"}, 404)(scope, receive, send)
        req = Request(scope)
        raw = auth.bearer(req)
        if raw is None or await run_in_threadpool(auth.token_user, req) is None:   # a token or nothing: never a session, never AUTH_DISABLED's dev user
            hdr = auth.REALM + (', error="invalid_token"' if raw is not None else "")
            return await JSONResponse({"detail": "an API token is required: Authorization: Bearer hl_..."}, 401, headers={"WWW-Authenticate": hdr})(scope, receive, send)
        if self.manager is None:
            return await JSONResponse({"detail": "MCP server is not running"}, 503)(scope, receive, send)
        await self.manager.handle_request(scope, receive, send)


endpoint = McpEndpoint()
