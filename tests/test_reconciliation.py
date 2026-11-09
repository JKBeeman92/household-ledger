"""Reconciliation (sum of transactions per envelope == BudgetItems Spent) is the app's central invariant: a failure must surface."""
from __future__ import annotations
import os

from conftest import GOLDEN, RECON_FAIL
from pipeline import import_exports
from pipeline.run_pipeline import reconcile


def _problems(conn):
    return {(p["budget_month"], p["item"]): p["status"] for p in reconcile(conn)["problems"]}


def test_golden_exports_reconcile_clean(reset_db):
    import psycopg
    assert import_exports.main([str(GOLDEN / "exports")]) == 0
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rec = reconcile(conn)
    assert rec["clean"] and rec["problems"] == [] and rec["counts"]["OK"] > 0


def test_mismatch_is_reported_not_swallowed(reset_db, capsys):
    import psycopg
    assert import_exports.main([str(RECON_FAIL / "exports")]) == 1
    out = capsys.readouterr().out
    assert "PROBLEM" in out and "MISMATCH" in out and "ITEM_NOT_IN_SUMMARY" in out
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        assert _problems(conn) == {("2025-02", "Groceries"): "MISMATCH", ("2025-02", "Fuel"): "ITEM_NOT_IN_SUMMARY"}
