"""Accounts and month-end balances (#143): entering, carrying, estimating, backfilling and correcting, at Close a month, on Settings and
from the CLI; corrections keep history; the datasets reach the dashboard, and only totals by type reach the review and MCP."""
from __future__ import annotations
import io, json
from decimal import Decimal

import pytest

from conftest import GOLDEN, upload_files
from app import admin, db, review, mcp_endpoint
from pipeline import balances as bal, mapping_store


@pytest.fixture
def loaded(client, reset_db):   # the golden fixture seeds five accounts and eight balances (tests/fixtures/golden/*.csv)
    upload_files(client, GOLDEN / "exports")


def q(sql, *p):
    with db.pool.connection() as conn:
        return conn.execute(sql, p).fetchall()


def test_debt_types_match_the_mapping_store():
    assert bal.DEBT_TYPES == mapping_store.DEBT_TYPES


@pytest.mark.parametrize("kind,prev,paid,apr,escrow,want", [
    ("asset", 4200, 0, None, None, 4200),
    ("debt", 18000, 275, None, None, 17725),
    ("debt", 18000, 275, 0.055, None, 17807.5),           # 18000 × 0.055 / 12 = 82.50 interest
    ("debt", 200000, 1500, 0.06, 400, 199900),            # escrow is not the loan: 1000 interest − 1100 principal and interest paid
    ("debt", 100, 500, None, None, 0),                    # never below zero
    ("debt", 1000, 0, None, 300, 1000),                   # no payment: no escrow either
])
def test_suggest(kind, prev, paid, apr, escrow, want):
    assert bal.suggest(kind, prev, paid, apr, escrow) == want


def test_dataset_estimates_debts_carries_assets_and_stops_retired_accounts(client, loaded):
    rows = client.get("/api/datasets").json()["balances"]
    by = {(r["account"], r["m"]): r for r in rows}
    assert by[("Lembas Student Loan", "2025-02")]["source"] == "estimated" and by[("Lembas Student Loan", "2025-02")]["amount"] == 17807.5
    assert (by[("Lembas Student Loan", "2025-03")]["source"], by[("Lembas Student Loan", "2025-03")]["confirmed"]) == ("entered", True)
    assert by[("Green Hills Savings", "2025-04")]["source"] == "carried" and not by[("Green Hills Savings", "2025-04")]["confirmed"]
    assert ("Bywater Checking", "2025-03") not in by   # retired after 2025-02
    assert min(m for (a, m) in by if a == "Bag End Smial") == "2024-12"   # starts where it starts
    totals = client.get("/api/datasets").json()["balance_totals"]
    assert not any("account" in t for t in totals)
    assert all(t["type"] != "other" for t in totals)   # Old Pony Fund is out of net worth


def test_settings_row_editor_checks_and_saves(client, loaded):
    ok = client.post("/settings/accounts/row", data={"name": "Mithril Mortgage", "kind": "debt", "type": "mortgage", "debt_item": "Student Loan",
                                                     "apr": "6.5%", "escrow": "400", "active": "yes"}).json()
    assert ok["ok"] and ok["row"]["apr"] == "6.5%" and ok["row"]["emergency"] == "no"
    assert q("SELECT apr, escrow FROM accounts WHERE name='Mithril Mortgage'")[0] == {"apr": Decimal("0.065"), "escrow": 400}
    bad = client.post("/settings/accounts/row", data={"name": "Pony", "kind": "asset", "type": "mortgage", "apr": "3%"}).json()
    assert not bad["ok"] and "type" in bad["errors"] and "apr" in bad["errors"]
    bad = client.post("/settings/accounts/row", data={"name": "Loan", "kind": "debt", "debt_item": "No Such Item"}).json()
    assert "not a canonical item" in bad["errors"]["debt_item"]
    assert client.post("/settings/accounts/row", data={"name": "Mithril Mortgage", "kind": "debt"}).json()["errors"]["name"] == "already an account"
    saving = client.post("/settings/accounts/row", data={"name": "Pantry Jar", "kind": "asset", "type": "cash"}).json()["row"]
    assert saving["emergency"] == "yes"   # cash and savings count toward the emergency fund unless switched off


def test_account_with_balances_is_retired_not_deleted_and_a_rename_carries_its_history(client, loaded):
    r = client.post("/settings/accounts/row/delete", data={"original": "Green Hills Savings"}).json()
    assert not r["ok"] and "retire it instead" in r["errors"]["_"]
    client.post("/settings/accounts/balances", data={"body": "Green Hills Savings,2025-02,4600"})
    r = client.post("/settings/accounts/row", data={"original": "Green Hills Savings", "name": "Green Hill Savings", "kind": "asset", "type": "savings"}).json()
    assert r["ok"]
    assert {x["account"] for x in q("SELECT account FROM balances WHERE account LIKE 'Green Hill%%'")} == {"Green Hill Savings"}
    assert [x["account"] for x in q("SELECT account FROM balance_history")] == ["Green Hill Savings"]
    assert client.post("/settings/accounts/row/delete", data={"original": "Pantry Jar"}).status_code == 404
    client.post("/settings/accounts/row", data={"name": "Pantry Jar", "kind": "asset"})
    assert client.post("/settings/accounts/row/delete", data={"original": "Pantry Jar"}).json()["ok"]


def test_close_a_month_suggests_then_stores_carried_or_entered_and_keeps_history(client, loaded):
    page = client.get("/upload?balances_month=2025-04").text
    assert "Balances at the end of April 2025" in page and 'value="17325.70"' in page and "To confirm" in page
    form = {"account": ["Lembas Student Loan", "Green Hills Savings", "Bag End Smial"], "amount": ["17325.70", "4,710.00", ""]}
    r = client.post("/balances/2025-04", data=form)
    assert r.status_code == 303 and r.headers["location"] == "/upload?balances_month=2025-04#balances"
    got = {x["account"]: x for x in q("SELECT account, amount, source FROM balances WHERE budget_month='2025-04'")}
    assert got["Lembas Student Loan"]["source"] == "carried" and got["Green Hills Savings"] == {"account": "Green Hills Savings", "amount": 4710, "source": "entered"}
    assert "Bag End Smial" not in got   # blank: still to confirm
    client.post("/balances/2025-04", data={"account": ["Lembas Student Loan"], "amount": ["17300"]})   # a correction
    assert q("SELECT amount, source FROM balances WHERE account='Lembas Student Loan' AND budget_month='2025-04'")[0] == {"amount": 17300, "source": "entered"}
    assert q("SELECT amount, source, event FROM balance_history") == [{"amount": Decimal("17325.70"), "source": "carried", "event": "replaced"}]
    r = client.post("/balances/2025-04", data={"account": ["Lembas Student Loan"], "amount": ["lots"]})
    assert q("SELECT amount FROM balances WHERE account='Lembas Student Loan' AND budget_month='2025-04'")[0]["amount"] == 17300
    page = client.get("/upload?balances_month=2025-04").text
    assert "lots" not in page and "must be a number" in page


def test_backfill_checks_every_line_before_storing_any(client, loaded):
    before = q("SELECT count(*) n FROM balances")[0]["n"]
    client.post("/settings/accounts/balances", data={"body": "account,month,amount\nGreen Hills Savings,2024-11,4000\nNobody,2024-11,1\nGreen Hills Savings,2024-13,1"})
    assert q("SELECT count(*) n FROM balances")[0]["n"] == before
    page = client.get("/settings").text
    assert "Nobody is not an account" in page and "line 4: month must look like YYYY-MM" in page
    client.post("/settings/accounts/balances", data={"body": "Green Hills Savings,2024-11,4000\nGreen Hills Savings,2024-10,3900.5"})   # no header
    assert q("SELECT count(*) n FROM balances")[0]["n"] == before + 2
    assert q("SELECT source FROM balances WHERE budget_month='2024-10'")[0]["source"] == "entered"


def test_cli_sets_and_exports_accounts_and_balances(client, loaded, capsys, tmp_path):
    f = tmp_path / "b.csv"; f.write_text("account,month,amount\nBag End Smial,2025-04,315000\n")
    assert admin.main(["set-balances", str(f)]) == 0
    assert q("SELECT entered_by FROM balances WHERE budget_month='2025-04'")[0]["entered_by"].endswith("(cli)")
    capsys.readouterr()
    assert admin.main(["export", "balances"]) == 0 and "Bag End Smial,2025-04,315000.00,entered" in capsys.readouterr().out
    assert admin.main(["export", "accounts"]) == 0
    body = capsys.readouterr().out
    assert body.startswith("name,kind,type,person,debt_item,apr,escrow,emergency,in_net_worth,active") and "Lembas Student Loan,debt,,Rosie,Student Loan,5.5%" in body
    f.write_text(body.replace("Old Pony Fund", "New Pony Fund"))   # dropping an account that has balances is refused
    assert admin.main(["set-accounts", str(f)]) == 1 and "Old Pony Fund is not in the CSV but has 1 balance" in capsys.readouterr().err


def test_review_and_mcp_see_totals_by_type_never_account_names(client, loaded):
    with db.pool.connection() as conn:
        ctx = review.build_context(conn, "2025-03")
    text = json.dumps(ctx["balances"])
    assert ctx["balances"]["month"] and "Lembas" not in text and "Green Hills" not in text
    assert "balance_totals" in mcp_endpoint.DATASETS and "balances" not in mcp_endpoint.DATASETS


def test_settings_zip_and_sources_zip_seed_a_new_install(client, loaded, reset_db, tmp_path):
    import zipfile
    seed = tmp_path / "seed"
    zipfile.ZipFile(io.BytesIO(client.get("/export/settings.zip").content)).extractall(seed)
    zipfile.ZipFile(io.BytesIO(client.get("/export/sources.zip").content)).extract("balances.csv", seed)
    want_acc = q("SELECT * FROM accounts ORDER BY name"); want_bal = q("SELECT account, budget_month, amount, source FROM balances ORDER BY 1, 2")
    reset_db(seed)
    assert [{k: v for k, v in r.items() if k != "updated_at"} for r in q("SELECT * FROM accounts ORDER BY name")] == \
           [{k: v for k, v in r.items() if k != "updated_at"} for r in want_acc]
    assert q("SELECT account, budget_month, amount, source FROM balances ORDER BY 1, 2") == want_bal
