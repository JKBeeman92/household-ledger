"""End to end on examples/sample-household (what a first-time user loads): import it, expect a clean reconciliation and a full build."""
from __future__ import annotations
import os

import psycopg

from conftest import SAMPLE
from pipeline import import_exports
from pipeline.run_pipeline import reconcile


def test_sample_household_imports_and_reconciles_clean(client, reset_db):
    reset_db(SAMPLE)
    assert import_exports.main([str(SAMPLE)]) == 0
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rec = reconcile(conn)
        assert rec["clean"], rec["problems"]
        assert conn.execute("SELECT ok FROM build_log ORDER BY id DESC LIMIT 1").fetchone() == (True,)
    assert client.get("/api/status").json()["months"] == 18
    data = client.get("/api/datasets").json()
    assert len(data["monthly"]) == 18 and data["budget_items"] and data["yearly"]
    assert client.get("/").status_code == 200


def test_sample_household_has_accounts_and_two_years_of_balances(client, reset_db):
    """#143: six accounts, month-ends from a backfilled 2024-07 to the month before the last export, so the latest month waits for
    the check-in: its debt is estimated, its assets carried."""
    reset_db(SAMPLE)
    assert import_exports.main([str(SAMPLE)]) == 0
    rows = client.get("/api/datasets").json()["balances"]
    assert len({r["account"] for r in rows}) == 6 and min(r["m"] for r in rows) == "2024-07"
    last = [r for r in rows if r["m"] == "2026-06"]
    assert {r["source"] for r in last if r["kind"] == "debt"} == {"estimated"} and {r["source"] for r in last if r["kind"] == "asset"} == {"carried"}
    assert any(r["source"] == "carried" and r["account"] == "Pony and Cart" for r in rows if r["m"] < "2026-06")   # valued quarterly


def test_sample_household_has_a_payoff_estimate_and_something_coming_up(client, reset_db):
    """#144: the student loan's payoff at last month's $275, from June's estimated balance, and the nursery start still to come."""
    reset_db(SAMPLE)
    assert import_exports.main([str(SAMPLE)]) == 0
    data = client.get("/api/datasets").json()
    (loan,) = data["payoff"]
    assert loan["account"] == "Lembas Student Loan" and loan["m"] == "2026-06" and loan["status"] == "date" and loan["payment"] == 275
    assert loan["payoff"] > "2026-06" and [n["code"] for n in loan["notes"]] == ["estimated"] and loan["moved"] == 0
    kinds = {(r["kind"], r["m"]) for r in data["coming_up"]}
    assert ("event", "2026-09") in kinds and ("payoff", loan["payoff"]) in kinds
