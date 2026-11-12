"""Pre-tax saving (#152): contributions per person (a share of household gross, or dollars a month) give a second savings rate,
(net + pre-tax) ÷ (income + pre-tax), and count as "what you did" in net worth's split, which still adds up."""
from __future__ import annotations

import pytest

from conftest import GOLDEN, upload_files
from app import db, review
from pipeline.run_pipeline import rebuild


@pytest.fixture
def loaded(client, reset_db):
    upload_files(client, GOLDEN / "exports")


def goal(conn, key, value, unit):
    conn.execute("INSERT INTO goals (key, value, unit, label) VALUES (%s,%s,%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, unit=EXCLUDED.unit",
                 (key, value, unit, key))


def data(client, name):
    return client.get("/api/datasets").json()[name]


def test_both_savings_rates_match_a_hand_calculation(client, loaded):
    with db.pool.connection() as conn:
        goal(conn, "gross_annual", 72000, "usd"); goal(conn, "pretax_contribution:Rosie", 0.1, "share of gross"); goal(conn, "pretax_contribution:Sam", 50, "usd/month")
        conn.commit(); rebuild(conn)
    m = data(client, "monthly")[0]
    assert m["pretax"] == 0.1 * 6000 + 50
    y = data(client, "yearly")[0]
    assert y["pretax"] == 650 * 4 and y["savings_rate_pretax"] == round((y["net"] + 2600) / (y["income"] + 2600), 4)
    assert y["savings_rate_pretax"] > y["savings_rate"]
    with db.pool.connection() as conn:
        sr = review.build_context(conn, "2025-02")["savings_rate"]
    jan_feb = [r for r in data(client, "monthly") if r["m"] <= "2025-02"]
    net, inc = sum(r["net"] for r in jan_feb), sum(r["income"] for r in jan_feb)
    assert sr["trailing12"]["including_pretax"] == round((net + 1300) / (inc + 1300), 4) and sr["trailing12"]["take_home"] == round(net / inc, 4)


def test_net_worth_counts_contributions_as_yours_and_still_adds_up(client, loaded):
    before = {r["m"]: r for r in data(client, "net_worth")}
    with db.pool.connection() as conn:
        goal(conn, "pretax_contribution:Sam", 50, "usd/month"); conn.commit(); rebuild(conn)
    after = {r["m"]: r for r in data(client, "net_worth")}
    feb = after["2025-02"]
    assert feb["you_did"] == round(before["2025-02"]["you_did"] + 50, 2) and feb["happened"] == round(before["2025-02"]["happened"] - 50, 2)
    assert feb["change"] == pytest.approx(feb["you_did"] + feb["happened"] + feb["added"] - feb["closed"], abs=0.005)


def test_a_share_with_no_gross_shows_a_dash(client, loaded):
    with db.pool.connection() as conn:
        goal(conn, "pretax_contribution:Rosie", 0.1, "share of gross"); conn.commit(); rebuild(conn)
    assert data(client, "monthly")[0]["pretax"] is None and data(client, "yearly")[0]["savings_rate_pretax"] is None


def test_settings_form_per_person(client, loaded):
    page = client.get("/settings").text
    assert 'action="/settings/goals/pretax"' in page and 'aria-label="Pre-tax saving for Rosie"' in page
    r = client.post("/settings/goals/pretax", data={"person": ["Rosie", "Sam"], "value": ["10%", "75"], "unit": ["share", "usd"]})
    assert r.status_code == 303 and r.headers["location"] == "/settings#goals"
    with db.pool.connection() as conn:
        got = {r["key"]: (float(r["value"]), r["unit"]) for r in conn.execute("SELECT key, value, unit FROM goals WHERE key LIKE 'pretax%%'").fetchall()}
    assert got == {"pretax_contribution:Rosie": (0.1, "share of gross"), "pretax_contribution:Sam": (75.0, "usd/month")}
    client.post("/settings/goals/pretax", data={"person": ["Rosie", "Sam"], "value": ["12", ""], "unit": ["share", "usd"]})   # 12 as a share: refused
    assert "a share is between 0 and 1" in client.get("/settings").text
    client.post("/settings/goals/pretax", data={"person": ["Rosie", "Sam"], "value": ["10%", ""], "unit": ["share", "usd"]})   # blank removes
    with db.pool.connection() as conn:
        assert [r["key"] for r in conn.execute("SELECT key FROM goals WHERE key LIKE 'pretax%%'").fetchall()] == ["pretax_contribution:Rosie"]
