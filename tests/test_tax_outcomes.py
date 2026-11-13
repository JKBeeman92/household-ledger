"""The yearly withholding check (#153, part 2): the annual check-in's tax-outcome item records one signed amount per tax year (a refund
positive, a bill negative, never a typed sign); the year in review shows the trend and, after a refund or a bill of at least
withholding_note_at two tax years running, the note; settings.zip, the seed loader, the CLI and the sample household carry it."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path

import pytest

from conftest import SAMPLE

_spec = importlib.util.spec_from_file_location("years_rules_w", Path(__file__).resolve().parent.parent / "pipeline" / "years.py")
yr = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(yr)
MONTHLY = [{"m": f"2025-{i:02d}", "income": 5000, "spend": 3000, "debt": 0, "net": 2000, "pretax": 0} for i in range(1, 13)]


def w(outcomes, at=1000):
    return yr.review(MONTHLY, [], [], [], [], [], [], "2025-12", tax_outcomes=outcomes, withholding_note_at=at)["withholding"]


def test_the_note_needs_two_consecutive_years_over_the_threshold():
    assert w([]) is None
    assert w([{"year": "2024", "amount": 1500}])["adjust"] is False
    r = w([{"year": "2025", "amount": -1200}, {"year": "2024", "amount": -1000}])
    assert r["adjust"] and r["adjust_kind"] == "owed" and [o["year"] for o in r["outcomes"]] == ["2024", "2025"]
    assert w([{"year": "2024", "amount": 1500}, {"year": "2025", "amount": -1100}])["adjust_kind"] == "mixed"
    assert w([{"year": "2023", "amount": 1500}, {"year": "2025", "amount": 1500}])["adjust"] is False   # not running: a year missing
    assert w([{"year": "2024", "amount": 1500}, {"year": "2025", "amount": 900}])["adjust"] is False
    assert w([{"year": "2024", "amount": 600}, {"year": "2025", "amount": 700}], at=500)["adjust"] is True


@pytest.fixture
def sample(client, reset_db):
    from pipeline import import_exports
    reset_db(SAMPLE)
    assert import_exports.main([str(SAMPLE)]) == 0


def _db():
    from app import db
    return db.pool.connection()


def _year_start(client, m):
    from app import db
    with db.pool.connection() as conn:
        rules = db.get_setting(conn, "rules", {}) or {}
        db.put_setting(conn, "rules", {**rules, "year_start_month": m}); conn.commit()
    client.post("/rebuild")


def test_sample_household_shows_the_trend_and_the_note(client, sample):
    _year_start(client, 7)
    y = client.get("/api/datasets").json()["year_review"][0]
    assert [o["year"] for o in y["withholding"]["outcomes"]] == ["2023", "2024", "2025"] and y["withholding"]["adjust_kind"] == "refund"
    page = client.get("/checkin/2026-06").text
    assert "consider adjusting withholding" in page and "Tax year 2025: a refund of $1,585" in page
    from app import db, review
    with db.pool.connection() as conn:
        assert review.build_context(conn, "2026-06")["year"]["withholding"]["adjust"] is True


def test_the_checklist_item_records_a_signed_outcome_and_a_bad_amount_writes_nothing(client, sample):
    _year_start(client, 7)
    page = client.get("/checkin/2026-06").text
    assert 'name="tax_year"' in page and 'value="2025"' in page   # last calendar year, prefilled
    r = client.post("/checkin/2026-06/yearly/tax_outcome/tick", data={"tax_year": "2025", "kind": "refund", "amount": "lots"})
    page = client.get(r.headers["location"]).text
    assert 'aria-invalid="true" aria-describedby="yc-tax_outcome-err"' in page and "The amount is a number" in page
    assert page.count("The amount is a number") + page.count("the amount is a number") == 1   # said once, under its field
    with _db() as conn:
        assert conn.execute("SELECT 1 FROM yearly_checklist WHERE item='tax_outcome'").fetchone() is None
    client.post("/checkin/2026-06/yearly/tax_outcome/tick", data={"tax_year": "2025", "kind": "owed", "amount": "$1,240", "note": "the side work"})
    with _db() as conn:
        o = conn.execute("SELECT amount, note FROM tax_outcomes WHERE year='2025'").fetchone()
        assert (float(o["amount"]), o["note"]) == (-1240.0, "the side work")
        assert conn.execute("SELECT 1 FROM yearly_checklist WHERE item='tax_outcome' AND year='2025'").fetchone()
    assert "2025, a bill of $1,240" in client.get("/checkin/2026-06").text


def test_settings_zip_seed_and_cli(client, sample, tmp_path, capsys):
    from app import admin, settings_export
    from pipeline.run_pipeline import seed_reference_tables
    assert admin.main(["tax", "outcome", "2026", "owed", "300", "--note", "late 1099"]) == 0
    with _db() as conn:
        files = settings_export.settings_files(conn)
    body = json.loads(files["tax_outcomes.json"])
    assert {o["year"]: o["amount"] for o in body}["2026"] == -300.0
    (tmp_path / "tax_outcomes.json").write_bytes(files["tax_outcomes.json"])
    with _db() as conn:
        conn.execute("DELETE FROM tax_outcomes"); seed_reference_tables(conn, tmp_path); conn.commit()
        assert float(conn.execute("SELECT amount FROM tax_outcomes WHERE year='2026'").fetchone()["amount"]) == -300.0
    assert admin.main(["tax", "forget-outcome", "2026"]) == 0
    assert "outcome deleted" in capsys.readouterr().out
