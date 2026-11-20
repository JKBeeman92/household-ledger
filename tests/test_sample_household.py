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


def test_sample_household_shows_each_person_at_the_check_in(client, reset_db):
    """#156: two people, so the By person block has Rosie, Sam and the joint part for the latest month, each year of it summing to the
    earner and payer charts; Rosie's student loan decision and her accounts sit under her name at the check-in."""
    reset_db(SAMPLE)
    assert import_exports.main([str(SAMPLE)]) == 0
    data = client.get("/api/datasets").json()
    last = [r for r in data["by_person"] if r["m"] == "2026-06"]
    assert [r["who"] for r in last] == ["Rosie", "Sam", "Household"] and all(r["earned_change"] is not None for r in last)
    assert all(r["spent"] > 0 for r in last if r["who"] != "Household")   # each has a fun-money envelope
    for p in data["people"]:
        assert round(sum(r["earned" if p["role"] == "earner" else "spent"] for r in data["by_person"] if r["m"][:4] == p["y"] and r["who"] == p["who"]), 2) == p["amount"]
    page = client.get("/checkin/2026-06").text
    sec = page[page.index('id="standing-people"'):]; sec = sec[:sec.index("</dl>")]
    rosie = sec.split("<dt>Rosie</dt>")[1].split("<dt>Sam</dt>")[0]
    assert "Lembas Student Loan" in rosie and "An extra $50 on the student loan" in rosie and "Pony and Cart" in sec.split("<dt>Sam</dt>")[1]
