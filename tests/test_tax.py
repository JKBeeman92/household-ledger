"""Estimated tax at the check-in (#153, part 1): nothing anywhere without contract income items; the year's set-aside against what is
expected, by hand; the quarter-mode step with the next deadline and its tick; deadlines in coming_up; settings.zip and the CLI."""
from __future__ import annotations
import importlib.util
from pathlib import Path

import pytest

from conftest import GOLDEN, SAMPLE, upload_files

_spec = importlib.util.spec_from_file_location("tax_rules", Path(__file__).resolve().parent.parent / "pipeline" / "tax.py")
tx = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(tx)


def row(m, item, actual=0.0, planned=0.0, flow="income"):
    return {"m": m, "canonical": item, "flow": flow, "planned": planned, "actual": actual}


DL = [{"m": "2026-04", "label": "Q1"}, {"m": "2026-06", "label": "Q2"}, {"m": "2026-09", "label": "Q3"}, {"m": "2027-01", "label": "Q4"}]


def test_set_aside_against_expected_matches_a_hand_calculation():
    rows = [row(f"2026-{m:02d}", "Contract Income", 2000) for m in range(1, 7)] + [row("2025-12", "Contract Income", 5000)]
    rows += [row(f"2026-{m:02d}", "Tax Set-Aside", 0, 400, "expense") for m in range(1, 7)]
    s = tx.summary(rows, ["Contract Income"], ["Tax Set-Aside"], 0.25, DL, [], "2026-06")
    assert s["contract_income"] == 12000 and s["expected"] == 3000   # 6 × 2,000 this year, 25%; December 2025 is last tax year
    assert s["set_aside"] == 2400 and s["short"] == 600                # 6 × 400 planned into the envelope
    assert s["quarter_income"] == 6000 and s["quarter_expected"] == 1500 and s["quarter_months"] == ["2026-04", "2026-05", "2026-06"]
    assert s["next_deadline"] == "2026-09" and s["next_label"] == "Q3" and not s["needs_dates"]


def test_an_account_and_recorded_payments_count_as_set_aside():
    rows = [row("2026-03", "Contract Income", 4000)]
    s = tx.summary(rows, ["Contract Income"], ["Tax Savings"], 0.3, DL, [{"month": "2026-03", "amount": 900}], "2026-03", {"Tax Savings": 300.0})
    assert s["set_aside"] == 1200 and s["set_aside_kind"] == "account" and s["paid_this_year"] == 900


def test_nothing_without_contract_income_and_dates_needed_after_the_last():
    assert tx.summary([row("2026-03", "Contract Income", 1)], [], [], 0.25, DL, [], "2026-03") is None
    s = tx.summary([row("2027-01", "Contract Income", 1)], ["Contract Income"], [], None, DL, [], "2027-01")
    assert s["needs_dates"] and s["next_deadline"] is None and s["expected"] is None
    assert [r["m"] for r in tx.deadline_rows(DL, "2026-06", 1500)] == ["2026-09", "2027-01"]
    assert tx.deadline_rows(DL, "2026-06", 1500)[0]["amount"] == 1500 and tx.deadline_rows(DL, "2026-06")[1]["kind"] == "tax_deadline"


# ---- through the app ----

def _db():
    from app import db
    return db.pool.connection()


def test_nothing_is_shown_anywhere_without_contract_income_items(client, reset_db):
    upload_files(client, GOLDEN / "exports")
    with _db() as conn:
        conn.execute("INSERT INTO goals (key, value, unit, label, note) VALUES ('tax_set_aside_pct', 0.25, 'share of contract income', 'x', 'Ask at each month-close')"); conn.commit()
    client.post("/rebuild")
    data = client.get("/api/datasets").json()
    assert data["tax"] == [] and not [r for r in data["coming_up"] if r["kind"] == "tax_deadline"]
    from app.routes.checklist import checklist_goals
    with _db() as conn:
        assert "tax_set_aside_pct" not in {g["key"] for g in checklist_goals(conn)}
        from app import checkin
        assert "tax" not in {s["key"] for s in checkin.overview(conn, "2025-03")["steps"]}   # March is a quarter's end
    page = client.get("/checkin/2025-04").text
    assert "Estimated tax" not in page


@pytest.fixture
def sample(client, reset_db):
    from pipeline import import_exports
    reset_db(SAMPLE)
    assert import_exports.main([str(SAMPLE)]) == 0


def test_sample_household_quarter_step_tick_and_coming_up(client, sample):
    data = client.get("/api/datasets").json()
    (s,) = data["tax"]
    assert s["m"] == "2026-06" and s["pct"] == 0.25 and s["set_aside"] == 6 * 450 and s["next_deadline"] == "2026-09"
    assert s["expected"] == round(s["contract_income"] * 0.25, 2)
    assert ("tax_deadline", "2026-09") in {(r["kind"], r["m"]) for r in data["coming_up"]}
    page = client.get("/checkin/2026-06").text   # June ends calendar Q2: quarter mode
    step = page[page.find('id="step-tax"'):]
    assert "Estimated tax" in page and "Q3 federal estimate" in step and "Mark this quarter's estimate paid" in step
    assert "Estimated tax</dt>" in page   # the Where we stand line, on every check-in
    r = client.post("/tax/2026-06/paid", data={"amount": "1,250"})
    assert r.headers["location"] == "/checkin/2026-06#step-tax"
    with _db() as conn:
        p = conn.execute("SELECT amount, deadline_m, label FROM tax_payments WHERE month='2026-06'").fetchone()
        assert float(p["amount"]) == 1250 and p["deadline_m"] == "2026-09" and p["label"] == "Q3 federal estimate"
        from app import checkin
        assert {x["key"]: x["status"] for x in checkin.overview(conn, "2026-06")["steps"]}["tax"] == "done"
    client.post("/tax/2026-06/unpaid")
    with _db() as conn:
        assert conn.execute("SELECT 1 FROM tax_payments").fetchone() is None


def test_settings_form_saves_rules_and_goal_and_blank_turns_it_off(client, sample):
    r = client.post("/tax/settings", data={"items": "Contract Income", "aside": "Tax Set-Aside", "pct": "30%", "deadlines": "2026-09 Q3\n2027-01 Q4"})
    assert r.headers["location"] == "/settings?focus=tax-settings#goals"
    from app import db, tax
    with _db() as conn:
        assert tax.goal(conn) == 0.3 and tax.rules(conn)["deadlines"] == [{"m": "2026-09", "label": "Q3"}, {"m": "2027-01", "label": "Q4"}]
    r = client.post("/tax/settings", data={"items": "Second Breakfast", "aside": "", "pct": "30%", "deadlines": ""})
    assert "Second Breakfast is not one of your budget items" in client.get(r.headers["location"]).text
    r = client.post("/tax/settings", data={"items": "Contract Income", "pct": "25%", "deadlines": "Sept Q3"})
    assert "start with the month as YYYY-MM" in client.get(r.headers["location"]).text
    client.post("/tax/settings", data={"items": "", "aside": "", "pct": "", "deadlines": ""})
    with _db() as conn:
        assert tax.goal(conn) is None and not tax.enabled(conn)


def test_settings_zip_and_cli(client, sample, reset_db, tmp_path, capsys):
    from app import admin
    from app.settings_export import write_dir
    assert admin.main(["tax", "paid", "2026-06", "--amount", "1200"]) == 0
    assert admin.main(["tax", "paid", "June", "--amount", "1"]) == 2
    with _db() as conn:
        write_dir(conn, tmp_path)
    assert '"amount": 1200.0' in (tmp_path / "tax_payments.json").read_text()
    reset_db(tmp_path)
    with _db() as conn:
        assert float(conn.execute("SELECT amount FROM tax_payments WHERE month='2026-06'").fetchone()["amount"]) == 1200


def test_errors_sit_under_their_field_and_nothing_due_can_be_undone(client, sample):
    """From the #153 UX review: DS-FORM-1 (an error under its field, marked aria-invalid, the typed value kept), DS-BTN-2 (the
    nothing-due button names the act), and the nothing-due choice shown with an Undo, as Books does."""
    r = client.post("/tax/2026-06/paid", data={"amount": "about a grand"})
    page = client.get(r.headers["location"]).text
    step = page[page.find('id="step-tax"'):]
    assert 'aria-invalid="true" aria-describedby="tax-amount-err"' in step and 'id="tax-amount-err"' in step
    assert 'value="about a grand"' in step and "The amount paid is a number" in step
    assert 'aria-invalid' not in client.get("/checkin/2026-06").text[page.find('id="step-tax"'):].split("</section>")[0]   # shown once
    r = client.post("/tax/settings", data={"items": "Second Breakfast", "aside": "", "pct": "30%", "deadlines": "2026-09 Q3"})
    html = client.get(r.headers["location"]).text
    assert 'id="tax-items"' in html and 'aria-describedby="tax-items-err"' in html and "Second Breakfast is not one of your budget items" in html
    assert 'value="Second Breakfast"' in html and "2026-09 Q3</textarea>" in html
    assert "Say nothing is due this quarter" in step
    client.post("/checkin/2026-06/steps/tax/ack")
    step = client.get("/checkin/2026-06").text
    step = step[step.find('id="step-tax"'):]
    assert "Nothing due this quarter." in step and "/steps/tax/undo" in step and "Mark this quarter's estimate paid" not in step.split("</section>")[0]
    client.post("/checkin/2026-06/steps/tax/undo")
    with _db() as conn:
        from app import checkin
        assert {x["key"]: x["status"] for x in checkin.overview(conn, "2026-06")["steps"]}["tax"] == "todo"
