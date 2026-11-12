"""Debt-to-income (#145): debt and housing payments over gross (and take-home), each month and over the trailing twelve; "—" with no
gross stated; a mortgage in a housing category counted once; the household's own caps only."""
from __future__ import annotations
import json

import pytest

from conftest import GOLDEN, upload_files
from app import db, review, mcp_endpoint
from pipeline import mapping_store
from pipeline.run_pipeline import rebuild


@pytest.fixture
def loaded(client, reset_db):
    upload_files(client, GOLDEN / "exports")


def dti(client):
    return {r["m"]: r for r in client.get("/api/datasets").json()["debt_to_income"]}


def set_goal(conn, key, value, unit="share of gross"):
    conn.execute("INSERT INTO goals (key, value, unit, label) VALUES (%s,%s,%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, value, unit, key))


def test_no_gross_stated_gives_none_not_zero(client, loaded):
    rows = dti(client)
    assert rows["2025-01"]["debt"] == 275 and rows["2025-01"]["dti"] is None and rows["2025-01"]["dti_12"] is None
    assert rows["2025-01"]["dti_takehome"] == pytest.approx(275 / 3760, abs=1e-4)   # take-home needs no stated gross


def test_ratios_match_a_hand_calculation(client, loaded):
    with db.pool.connection() as conn:
        set_goal(conn, "gross_annual", 72000, "usd"); conn.commit(); rebuild(conn)
    rows = dti(client)
    jan, mar = rows["2025-01"], rows["2025-03"]
    assert jan["gross"] == 6000 and jan["dti"] == round(275 / 6000, 4) and jan["housing_dti"] == round(1450 / 6000, 4)
    # trailing: Jan–Mar debt 825 over 3 × 6000; housing 1450 + 1547.80 + 1534.30
    assert mar["months_12"] == 3 and mar["dti_12"] == round(825 / 18000, 4) and mar["housing_dti_12"] == round((1450 + 1547.80 + 1534.30) / 18000, 4)
    assert mar["housing_takehome"] == round(1534.30 / 5640, 4)   # a three-paycheck month lowers the take-home share


def test_a_mortgage_counts_toward_housing_once(client, loaded):
    with db.pool.connection() as conn:
        set_goal(conn, "gross_annual", 72000, "usd")
        mapping_store.set_debt_type(conn, "Student Loan", "mortgage"); conn.commit(); rebuild(conn)
    assert dti(client)["2025-01"]["housing"] == 1450 + 275   # rent plus the mortgage-type debt
    from app import renames
    with db.pool.connection() as conn:
        renames.move_canonical(conn, "Student Loan", "Housing"); conn.commit(); rebuild(conn)   # now in a housing category too
    row = dti(client)["2025-01"]
    assert row["housing"] == 1450 + 275 and row["debt"] == 275


def test_dashboard_review_and_mcp(client, loaded):
    with db.pool.connection() as conn:
        set_goal(conn, "gross_annual", 72000, "usd"); set_goal(conn, "dti_cap", 0.2); conn.commit(); rebuild(conn)
        ctx = review.build_context(conn, "2025-03")
    d = ctx["debt_to_income"]
    assert d["dti"] == round(275 / 6000, 4) and d["caps"] == {"dti_cap": 0.2} and "housing_dti_cap" not in d["caps"]
    assert "debt_to_income" in mcp_endpoint.DATASETS
    html = client.get("/").text
    assert 'data-draw="lm-dti"' in html and 'data-draw="dti-lines"' in html
