"""Emergency-fund coverage (#150): emergency money over the trailing-twelve average outgo; "—" with no emergency account."""
from __future__ import annotations

import pytest

from conftest import GOLDEN, upload_files
from app import db, review, mcp_endpoint
from pipeline.run_pipeline import rebuild


@pytest.fixture
def loaded(client, reset_db):
    upload_files(client, GOLDEN / "exports")


def ef(client):
    return {r["m"]: r for r in client.get("/api/datasets").json()["emergency_fund"]}


def test_coverage_matches_a_hand_calculation(client, loaded):
    rows, monthly = ef(client), {r["m"]: r for r in client.get("/api/datasets").json()["monthly"]}
    outgo = lambda *ms: sum(monthly[m]["spend"] + monthly[m]["debt"] for m in ms) / len(ms)
    feb = rows["2025-02"]
    assert feb["emergency"] == 4500 and feb["outgo"] == round(outgo("2025-01", "2025-02"), 2) and feb["months"] == round(4500 / outgo("2025-01", "2025-02"), 2)
    assert rows["2025-04"]["outgo_months"] == 4 and rows["2025-04"]["unconfirmed"] == 1   # carried, not yet confirmed
    assert rows["2025-02"]["change"] == round(rows["2025-02"]["months"] - rows["2025-01"]["months"], 2)


def test_no_emergency_account_shows_a_dash_and_says_where_to_mark_one(client, loaded):
    with db.pool.connection() as conn:
        conn.execute("UPDATE accounts SET emergency=false"); conn.commit(); rebuild(conn)
    assert all(r["months"] is None and r["emergency"] is None for r in ef(client).values())
    html = client.get("/").text
    assert "Mark one on Settings → Accounts" in html and 'data-draw="ef-lines"' in html


def test_review_and_mcp(client, loaded):
    with db.pool.connection() as conn:
        conn.execute("INSERT INTO goals (key, value, unit, label) VALUES ('emergency_months', 6, 'months', 'Emergency fund target')"); conn.commit(); rebuild(conn)
        ctx = review.build_context(conn, "2025-02")
    assert ctx["emergency_fund"]["target"] == 6 and ctx["emergency_fund"]["months"] == ef(client)["2025-02"]["months"]
    assert "emergency_fund" in mcp_endpoint.DATASETS
