"""MCP server (#33): token-only, derived datasets only, one separately guarded write tool."""
from __future__ import annotations
import asyncio, dataclasses, json
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from starlette.routing import Route

from app import mcp_endpoint
from conftest import GOLDEN
from test_api_tokens import bearer, make_token, sql

URL = "http://testserver/mcp"
READ_TOOLS = {"list_months", "get_dataset", "month_summary", "category_trend", "goals", "review", "review_instructions"}
SECTIONS = [{"order": 1, "heading": "The month in one line", "text": "A quiet month in Hobbiton."}, {"heading": "Next month", "text": "Keep the garden envelope as it is."}]


@pytest.fixture
def loaded(client, reset_db):
    """The golden household, loaded and rebuilt synchronously (an upload rebuilds in the background, which would race the test)."""
    from app import db
    from pipeline.run_pipeline import import_dir
    with db.pool.connection() as conn:
        import_dir(conn, GOLDEN / "exports")


@pytest.fixture
def auth_on(monkeypatch):
    from app import auth
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, auth_disabled=False))


def mcp_session(token: str | None, fn):
    """Run fn(session) in an in-process MCP client session against a fresh endpoint (its own session manager, this event loop)."""
    async def go():
        ep = mcp_endpoint.McpEndpoint()
        async with ep.run():
            http = httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(routes=[Route("/mcp", ep)])), headers=bearer(token) if token else {})
            async with http, streamable_http_client(URL, http_client=http) as (r, w, _), ClientSession(r, w) as s:
                await s.initialize()
                return await fn(s)
    return asyncio.run(go())


def call(token, name, args=None):
    async def fn(s):
        res = await s.call_tool(name, args or {})
        if res.isError:
            return True, res.content[0].text
        sc = res.structuredContent
        if sc is None:
            try:
                return False, json.loads(res.content[0].text)
            except ValueError:
                return False, res.content[0].text
        return False, sc["result"] if set(sc) == {"result"} else sc
    return mcp_session(token, fn)


def test_lists_the_tools_with_a_read_token(loaded):
    async def fn(s):
        return {t.name for t in (await s.list_tools()).tools}
    assert mcp_session(make_token(), fn) == READ_TOOLS | {"save_review"}


def test_every_read_tool_answers(loaded):
    tok = make_token()
    err, months = call(tok, "list_months")
    assert not err and months and set(months[0]) == {"month", "income", "spend", "debt", "net", "has_review"}
    m = months[0]["month"]
    err, ds = call(tok, "get_dataset", {"name": "monthly"})
    assert not err and any(r["m"] == m for r in ds)
    err, summ = call(tok, "month_summary", {"m": m})
    assert not err and summ["month"] == m and summ["totals"]["m"] == m and summ["categories"] and summ["envelopes"] and summ["has_review"] is False
    cat = summ["categories"][0]["category"]
    err, trend = call(tok, "category_trend", {"category": cat.lower(), "months": 3})
    assert not err and 1 <= len(trend) <= 3 and all(r["category"] == cat for r in trend) and [r["month"] for r in trend] == sorted(r["month"] for r in trend)
    err, g = call(tok, "goals")
    assert not err and g and "key" in g[0]
    err, text = call(tok, "review_instructions")
    assert not err and "month-close review" in text
    err, text = call(tok, "review", {"m": m})
    assert err and "no review is stored" in text


def test_bad_arguments_are_tool_errors(loaded):
    tok = make_token()
    for name, args, why in [("get_dataset", {"name": "ledger"}, "unknown dataset"), ("month_summary", {"m": "2999-01"}, "not a built month"),
                            ("month_summary", {"m": "March"}, "YYYY-MM"), ("category_trend", {"category": "Nope"}, "unknown category"),
                            ("category_trend", {"category": "Food", "months": 0}, "1 to 120")]:
        err, text = call(tok, name, args)
        assert err and why in text, (name, text)


def test_transactions_merchants_and_notes_are_never_served(loaded):
    tok = make_token()
    async def fn(s):
        return {t.name for t in (await s.list_tools()).tools}
    assert not any("transaction" in n for n in mcp_session(tok, fn))
    assert "verify" not in mcp_endpoint.DATASETS and "ledger" not in mcp_endpoint.DATASETS and "transactions" not in mcp_endpoint.DATASETS
    assert "balances" not in mcp_endpoint.DATASETS   # names accounts (#143); balance_totals is the same by type
    err, text = call(tok, "get_dataset", {"name": "balances"})
    assert err and "unknown dataset" in text
    err, rows = call(tok, "get_dataset", {"name": "balance_totals"})
    assert not err and rows and not any("account" in r for r in rows)
    err, text = call(tok, "get_dataset", {"name": "verify"})
    assert err and "unknown dataset" in text
    assert "payoff" not in mcp_endpoint.DATASETS   # names accounts (#144); coming_up is served with its public labels
    err, text = call(tok, "get_dataset", {"name": "payoff"})
    assert err and "unknown dataset" in text
    accounts = {r["name"] for r in sql("SELECT name FROM accounts")}
    err, rows = call(tok, "get_dataset", {"name": "coming_up"})
    assert not err and all(set(r) == {"m", "kind", "label", "amount", "source"} for r in rows)
    assert not any(a in json.dumps(rows) for a in accounts)
    names = {r["name"] for r in sql("SELECT name FROM datasets")}
    for n in names & set(mcp_endpoint.DATASETS):
        err, rows = call(tok, "get_dataset", {"name": n})
        assert not err and "merchant" not in json.dumps(rows).lower()


def test_save_review_needs_the_review_scope_and_stores_like_any_review(loaded):
    m = call(make_token(), "list_months")[1][0]["month"]
    err, text = call(make_token("read"), "save_review", {"m": m, "sections": SECTIONS})
    assert err and "scope (read) does not include review" in text
    assert sql("SELECT count(*) n FROM reviews")[0]["n"] == 0
    tok = make_token("read+review", name="Bilbo's agent")
    err, res = call(tok, "save_review", {"m": m, "sections": SECTIONS})
    assert not err and res == {"month": m, "sections": 2, "saved": True}
    row = sql("SELECT model, sections, hand_edited_at FROM reviews WHERE month=%s", m)[0]
    assert row["model"] == "agent via MCP (Bilbo's agent)" and row["hand_edited_at"] is None
    assert [s["heading"] for s in row["sections"]] == ["The month in one line", "Next month"] and [s["order"] for s in row["sections"]] == [1, 2]
    ds = sql("SELECT rows FROM datasets WHERE name='review'")[0]["rows"]
    assert {r["m"] for r in ds} == {m} and len(ds) == 2
    err, rev = call(tok, "review", {"m": m})
    assert not err and rev["written_by"] == "agent via MCP (Bilbo's agent)" and len(rev["sections"]) == 2
    assert call(tok, "list_months")[1][0]["has_review"] is True


@pytest.mark.parametrize("sections", [[], "text", [{"heading": "x"}], [{"heading": " ", "text": "y"}], [{"heading": "h", "text": "t" * 5001}], [{"heading": "h", "text": "t"}] * 13])
def test_save_review_rejects_malformed_sections(loaded, sections):
    m = call(make_token(), "list_months")[1][0]["month"]
    err, _ = call(make_token("read+review"), "save_review", {"m": m, "sections": sections})
    assert err and sql("SELECT count(*) n FROM reviews")[0]["n"] == 0


def test_save_review_refuses_an_unbuilt_month(loaded):
    err, text = call(make_token("read+review"), "save_review", {"m": "2999-01", "sections": SECTIONS})
    assert err and "not a built month" in text and sql("SELECT count(*) n FROM reviews")[0]["n"] == 0


# ---- through the real app: auth in front of the endpoint ----
def rpc(client, headers=None, method="tools/list", **kw):
    h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **(headers or {})}
    return client.post("/mcp", headers=h, content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}), **kw)


def test_no_token_is_401_even_with_auth_disabled(client, reset_db):
    for h in ({}, bearer("hl_" + "A" * 43), {"Authorization": "Bearer "}):
        r = rpc(client, h)
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer realm="), h
    assert client.get("/mcp").status_code == 401 and client.delete("/mcp").status_code == 401


def test_cookie_session_never_opens_it(client, reset_db, auth_on):
    client.get("/upload")   # the test client now holds a session cookie
    assert rpc(client).status_code == 401


def test_revoked_token_is_refused(client, reset_db):
    tok = make_token()
    assert rpc(client, bearer(tok), "initialize").status_code in (200, 400)   # authenticated: the SDK, not the 401, answers
    sql("UPDATE api_tokens SET revoked_at = now()")
    assert rpc(client, bearer(tok)).status_code == 401


def test_bearer_post_is_not_stopped_by_csrf_and_works_on_the_real_app(client, raw_client, loaded):
    tok = make_token()
    r = rpc(raw_client, bearer(tok))   # no CSRF token, no cookie
    assert r.status_code == 200 and {t["name"] for t in r.json()["result"]["tools"]} == READ_TOOLS | {"save_review"}
    r = raw_client.post("/mcp", headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **bearer(tok)},
                        content=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_months", "arguments": {}}}))
    assert r.status_code == 200 and r.json()["result"]["isError"] is False


def test_token_cannot_use_the_editor_routes_it_never_could(client, reset_db):
    assert client.post("/api/build", headers=bearer(make_token("read+review"))).status_code in (403, 404, 405)
