"""The annual check-in (#157): the year review's figures are the sums of their months, a partial year compares a month at a time and
a year that is not loaded is hidden, not zero; year mode only on the year's last month (year_start_month moves it); the yearly
checklist's ticks persist per year and its list is editable; settings.zip, the review, MCP and the CLI carry it."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path

import pytest

from conftest import SAMPLE

_spec = importlib.util.spec_from_file_location("years_rules", Path(__file__).resolve().parent.parent / "pipeline" / "years.py")
yr = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(yr)


def months(first: str, n: int) -> list[str]:
    y, m = map(int, first.split("-")); out = []
    for _ in range(n):
        out.append(f"{y}-{m:02d}"); y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def mrow(m, inc, spend, debt=0.0, pretax=0.0):
    return {"m": m, "income": inc, "spend": spend, "debt": debt, "net": inc - spend - debt, "pretax": pretax,
            "income_real": inc, "spend_real": spend, "debt_real": debt, "net_real": inc - spend - debt, "pretax_real": pretax}


def test_year_of_follows_year_start_month():
    assert yr.year_of("2026-06", 1) == ("2026", months("2026-01", 12))
    assert yr.year_of("2026-06", 7) == ("2025", months("2025-07", 12))
    assert yr.year_of("2026-07", 7)[0] == "2026"


def test_totals_are_sums_of_months_and_a_partial_year_compares_per_month():
    monthly = [mrow(m, 5000, 3000, 500) for m in months("2025-01", 12)] + [mrow(m, 6000, 3000, 500) for m in months("2026-01", 6)]
    cats = [{"m": m, "category": "Food", "amount": 600, "amount_real": 600} for m in months("2025-01", 12)] + \
           [{"m": m, "category": "Food", "amount": 900, "amount_real": 880} for m in months("2026-01", 6)]
    r = yr.review(monthly, cats, [], [], [], [{"m": "2026-03", "label": "Moved", "kind": "move"}, {"m": "2025-03", "label": "Old"}], [], "2026-06")
    assert r["year"] == "2026" and r["months"] == 6 and r["partial"]
    assert r["this"]["income"] == 6 * 6000 and r["this"]["net"] == 6 * 2500 and r["previous"]["income"] == 12 * 5000
    assert r["change"]["income"]["per_month"] == 1000.0 and r["change"]["income"]["pct"] == 0.2   # a month at a time, not 36000 − 60000
    assert r["change"]["savings_rate"] == round(2500 / 6000 - 1500 / 5000, 4)
    assert r["movers"]["nominal"][0] == {"category": "Food", "per_month": 900.0, "was": 600.0, "change": 300.0}
    assert r["movers"]["real"][0]["per_month"] == 880.0
    assert [e["label"] for e in r["events"]] == ["Moved"]
    first = yr.review(monthly[:12], cats[:12], [], [], [], [], [], "2025-12")
    assert first["previous"] is None and first["change"] is None and first["movers"] is None   # no year before: hidden, not zero


def test_net_worth_debts_and_decisions():
    monthly = [mrow(m, 5000, 3000) for m in months("2025-01", 12)]
    nw = [{"m": "2024-12", "net_worth": 10000}, {"m": "2025-12", "net_worth": 40000}]
    bal = [{"m": "2024-12", "account": "Car Loan", "kind": "debt", "type": "auto", "amount": 3000}, {"m": "2025-12", "account": "Car Loan", "kind": "debt", "type": "auto", "amount": 0},
           {"m": "2024-12", "account": "Card", "kind": "debt", "type": "card", "amount": 500}, {"m": "2025-12", "account": "Card", "kind": "debt", "type": "card", "amount": 700}]
    dec = [{"m": "2025-03", "id": 1, "text": "Eat out under $150", "kind": "spend_cap", "target": {"max": 150}, "verdict": "kept"},
           {"m": "2025-04", "id": 1, "text": "Eat out under $150", "kind": "spend_cap", "target": {"max": 150}, "verdict": "partly"},
           {"m": "2025-05", "id": 2, "text": "Pay $400 extra", "kind": "extra_payment", "target": {"amount": 400}, "verdict": "dropped"},
           {"m": "2024-11", "id": 3, "text": "Before the year", "target": {"max": 9}, "verdict": "kept"}]
    r = yr.review(monthly, [], nw, bal, dec, [], [], "2025-12")
    assert r["net_worth"] == {"from": "2024-12", "to": "2025-12", "start": 10000.0, "end": 40000.0, "change": 30000.0}
    assert r["debt"] == {"start": 3500.0, "end": 700.0, "paid_off": ["auto"], "change": -2800.0}
    assert r["decisions"]["checked"] == 3 and r["decisions"]["kept"] == 1.5
    assert [d["text"] for d in r["decisions"]["biggest"]] == ["Pay $400 extra", "Eat out under $150"]
    assert "Car Loan" not in json.dumps(r)   # debts by type, never by account


# ---- through the app ----

@pytest.fixture
def sample(client, reset_db):
    from pipeline import import_exports
    reset_db(SAMPLE)
    assert import_exports.main([str(SAMPLE)]) == 0


def _db():
    from app import db
    return db.pool.connection()


def _set_year_start(client, m):
    from app import db
    with db.pool.connection() as conn:
        rules = db.get_setting(conn, "rules", {}) or {}
        db.put_setting(conn, "rules", {**rules, "year_start_month": m}); conn.commit()
    client.post("/rebuild")


def test_year_mode_only_on_the_years_last_month_and_year_start_month_moves_it(client, sample):
    page = client.get("/checkin/2026-06").text   # June ends a quarter, not the calendar year
    assert 'id="step-year_review"' not in page and 'id="step-yearly"' not in page
    _set_year_start(client, 7)   # July to June: June 2026 ends budget year 2025
    page = client.get("/checkin/2026-06").text
    assert 'id="step-year_review"' in page and 'id="step-yearly"' in page and "Budget year 2025" in page
    y = client.get("/api/datasets").json()["year_review"][0]
    mon = {r["m"]: r for r in client.get("/api/datasets").json()["monthly"]}
    assert y["year"] == "2025" and y["months"] == 12 and not y["partial"]
    assert y["this"]["income"] == round(sum(mon[m]["income"] for m in months("2025-07", 12)), 2)   # the sum of its months
    assert y["previous"]["months"] == 6   # January to June 2025 loaded: a month at a time
    assert y["ahead"] and y["ahead"]["until"] == "2027-06"
    from app import db, review
    with db.pool.connection() as conn:
        ctx = review.build_context(conn, "2026-06")
    assert ctx["year"]["year"] == "2025" and "quarter" in ctx
    from app import mcp_endpoint
    assert "year_review" in mcp_endpoint.DATASETS


def test_checklist_ticks_persist_per_year_and_the_list_is_editable(client, sample):
    _set_year_start(client, 7)
    page = client.get("/checkin/2026-06").text
    assert "Pull credit reports" in page and page.count(">Tick it off</button>") == 7
    r = client.post("/checkin/2026-06/yearly/credit_reports/tick", data={"note": "all three, nothing odd"})
    assert r.headers["location"] == "/checkin/2026-06#yc-credit_reports"
    with _db() as conn:
        t = conn.execute("SELECT year, label, note FROM yearly_checklist WHERE item='credit_reports'").fetchone()
    assert (t["year"], t["label"], t["note"]) == ("2025", "Pull credit reports", "all three, nothing odd")
    assert "all three, nothing odd" in client.get("/checkin/2026-06").text
    # the list: add, rename, move, remove; a past tick keeps its label
    client.post("/yearly-checklist", data={"label": "Review the will"})
    client.post("/yearly-checklist/credit_reports/rename", data={"label": "Pull all three credit reports"})
    client.post("/yearly-checklist/review_the_will/move", data={"dir": "up"})
    from app import yearly_checklist
    with _db() as conn:
        keys = [r["key"] for r in yearly_checklist.items(conn)]
    assert keys[-2:] == ["review_the_will", "yearly_bills"]
    r = client.post("/yearly-checklist", data={"label": "  "})
    assert r.headers["location"] == "/settings?focus=yc-add#yearly-checklist"
    assert "Say what to do once a year" in client.get(r.headers["location"]).text
    client.post("/yearly-checklist/credit_reports/remove")
    with _db() as conn:
        assert conn.execute("SELECT label FROM yearly_checklist WHERE item='credit_reports'").fetchone()["label"] == "Pull credit reports"
    page = client.get("/settings").text
    assert 'id="st-yearly-checklist"' in page and "Review the will" in page
    # every item ticked: the step is done
    with _db() as conn:
        for k in [r["key"] for r in yearly_checklist.items(conn)]:
            yearly_checklist.tick(conn, "2025", k)
        conn.commit()
        from app import checkin
        assert dict((s["key"], s["status"]) for s in checkin.overview(conn, "2026-06")["steps"])["yearly"] == "done"


def test_settings_zip_round_trip_and_the_seed_replaces_only_an_untouched_list(client, sample, tmp_path):
    from app import settings_export
    from pipeline.run_pipeline import YEARLY_CHECKLIST_DEFAULTS, seed_reference_tables
    from app import yearly_checklist
    with _db() as conn:
        assert [(r["key"], r["label"]) for r in yearly_checklist.items(conn)] == YEARLY_CHECKLIST_DEFAULTS   # the migration's seven
        yearly_checklist.add(conn, "Review the will"); yearly_checklist.tick(conn, "2025", "insurance", "renewed"); conn.commit()
        files = settings_export.settings_files(conn)
    body = json.loads(files["yearly_checklist.json"])
    assert [i["key"] for i in body["items"]][-1] == "review_the_will" and body["ticks"][0]["note"] == "renewed"
    (tmp_path / "yearly_checklist.json").write_bytes(files["yearly_checklist.json"])
    with _db() as conn:   # a fresh install: the list as shipped
        conn.execute("DELETE FROM yearly_checklist"); conn.execute("DELETE FROM yearly_checklist_items")
        for i, (k, l) in enumerate(YEARLY_CHECKLIST_DEFAULTS):
            conn.execute("INSERT INTO yearly_checklist_items (key, label, position) VALUES (%s,%s,%s)", (k, l, (i + 1) * 10))
        seed_reference_tables(conn, tmp_path); conn.commit()
        assert "review_the_will" in [r["key"] for r in yearly_checklist.items(conn)]
        assert yearly_checklist.ticks(conn, "2025")["insurance"]["note"] == "renewed"
        yearly_checklist.rename(conn, "insurance", "Insurance, all of it"); conn.commit()
        (tmp_path / "yearly_checklist.json").write_text(json.dumps({"items": [{"key": "only", "label": "Only"}], "ticks": []}))
        seed_reference_tables(conn, tmp_path); conn.commit()
        assert "only" not in [r["key"] for r in yearly_checklist.items(conn)]   # an edited list is never replaced


def test_cli(client, sample, capsys):
    from app import admin
    assert admin.main(["yearly-checklist", "tick", "2025", "insurance", "--note", "renewed"]) == 0
    assert admin.main(["yearly-checklist", "list", "--year", "2025"]) == 0
    out = capsys.readouterr().out
    assert "insurance\tReview insurance renewals and coverage\tdone" in out and "renewed" in out
