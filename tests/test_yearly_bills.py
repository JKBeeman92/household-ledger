"""Irregular yearly bills (#151): detection (2 of the last 3 years, within a month, never a monthly bill), the household's list
winning (confirmed, added, dismissed across rebuilds), the next due month, the envelope's set-aside against a hand calculation,
coming_up, Settings, the CLI report, settings.zip, MCP and the sample household."""
from __future__ import annotations
import importlib.util
from pathlib import Path

import pytest

from conftest import GOLDEN, SAMPLE, upload_files

_spec = importlib.util.spec_from_file_location("yearly_bills_rules", Path(__file__).resolve().parent.parent / "pipeline" / "yearly_bills.py")
yb = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(yb)


def months(first: str, last: str) -> list[str]:
    out, (y, m) = [], map(int, first.split("-"))
    while f"{y}-{m:02d}" <= last:
        out.append(f"{y}-{m:02d}"); y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def row(m, item, actual, planned=0.0, flow="expense"):
    return {"m": m, "canonical": item, "category": "X", "flow": flow, "planned": planned, "actual": actual}


LOADED = months("2023-07", "2026-06")


def test_detects_an_item_paid_in_about_the_same_month_two_years_of_three():
    rows = [row("2024-09", "Car Insurance", 1180), row("2025-10", "Car Insurance", 1240)]   # a month late the second year
    (b,) = yb.detect(yb.paid_by_item(rows), "2026-06", set(LOADED))
    assert b["item"] == "Car Insurance" and b["month"] == 9 and b["amount"] == 1240 and b["years_seen"] == 2 and b["evidence"] == ["2024-09", "2025-10"]


def test_monthly_quarterly_once_and_drifting_items_are_not_yearly():
    monthly = [row(m, "Electric", 90) for m in LOADED]
    quarterly = [row(m, "Water", 60) for m in LOADED if int(m[5:]) in (1, 4, 7, 10)]
    once = [row("2025-12", "Holiday", 380)]
    drift = [row("2024-02", "Gift", 50), row("2025-06", "Gift", 50)]   # four months apart
    assert yb.detect(yb.paid_by_item(monthly + quarterly + once + drift), "2026-06", set(LOADED)) == []


def test_tolerance_and_min_years_are_rules():
    rows = [row("2024-09", "Premium", 100), row("2025-11", "Premium", 100)]
    assert yb.detect(yb.paid_by_item(rows), "2026-06", set(LOADED)) == []
    assert [b["item"] for b in yb.detect(yb.paid_by_item(rows), "2026-06", set(LOADED), tol=2)] == ["Premium"]
    one = [row("2025-09", "Premium", 100)]
    assert [b["item"] for b in yb.detect(yb.paid_by_item(one), "2026-06", set(LOADED), min_years=1)] == ["Premium"]


def test_next_due_and_envelope_set_aside_match_a_hand_calculation():
    rows = [row("2024-09", "Car Insurance", 1180), row("2025-09", "Car Insurance", 1240)]
    rows += [row(m, "Car Insurance Fund", 0, planned=100) for m in months("2025-01", "2026-06")]
    rows += [row("2025-09", "Car Insurance Fund", 1240)]   # the bill was paid out of the envelope that month
    (b,) = yb.bills(rows, [], "2026-06", set(LOADED), ["Car Insurance", "Car Insurance Fund"])
    assert b["funded_by"] == "Car Insurance Fund" and b["funded_guess"] and b["funded_kind"] == "envelope"
    assert b["last_paid"] == "2025-09" and b["next_due"] == "2026-09" and b["months_left"] == 3
    assert b["set_aside"] == 900.0   # Oct 2025 .. Jun 2026: nine months of $100, nothing spent since the bill fell due
    assert b["short"] == 340.0 and b["per_month"] == round(340 / 3, 2)


def test_the_households_list_wins_and_a_dismissal_stays():
    rows = [row("2024-09", "Car Insurance", 1180), row("2025-09", "Car Insurance", 1240), row("2025-03", "Tag Renewal", 40)]
    stored = [{"item": "Car Insurance", "source": "dismissed", "month": "9", "amount": "", "every_years": "1", "funded_by": ""},
              {"item": "Tag Renewal", "source": "added", "month": "3", "amount": "45", "every_years": "2", "funded_by": "Savings Account"}]
    out = {b["item"]: b for b in yb.bills(rows, stored, "2026-06", set(LOADED), ["Car Insurance", "Tag Renewal"], {"Savings Account": 500.0})}
    assert out["Car Insurance"]["source"] == "dismissed" and out["Car Insurance"]["next_due"] is None
    tag = out["Tag Renewal"]
    assert tag["source"] == "added" and tag["amount"] == 45 and tag["next_due"] == "2027-03"   # paid March 2025, every two years
    assert tag["funded_kind"] == "account" and tag["set_aside"] == 500.0 and tag["short"] == 0
    rows_cu = yb.coming_up_rows(list(out.values()))
    assert rows_cu == [{"m": "2027-03", "kind": "yearly_bill", "label": "Tag Renewal due", "public_label": "Tag Renewal due", "amount": 45, "source": "planned", "ref": "Tag Renewal"}]


# ---- through the app ----

@pytest.fixture
def sample(client, reset_db):
    from pipeline import import_exports
    reset_db(SAMPLE)
    assert import_exports.main([str(SAMPLE)]) == 0


def _db():
    from app import db
    return db.pool.connection()


def test_sample_household_bills_are_found_with_their_envelope(client, sample):
    data = client.get("/api/datasets").json()
    bills = {b["item"]: b for b in data["yearly_bills"]}
    assert set(bills) == {"Smial Insurance", "Cart Registration"}   # a monthly bill is not
    s = bills["Smial Insurance"]
    assert s["month"] == 2 and s["amount"] == 960 and s["funded_by"] == "Smial Insurance Fund" and s["next_due"] == "2027-02"
    assert s["set_aside"] == 320.0   # March .. June 2026, $80 a month since the February premium
    c = bills["Cart Registration"]
    assert c["month"] == 3 and c["funded_by"] is None and c["set_aside"] is None and c["next_due"] == "2027-03"
    kinds = [r for r in data["coming_up"] if r["kind"] == "yearly_bill"]
    assert {r["ref"] for r in kinds} == {"Smial Insurance", "Cart Registration"}


def test_settings_confirm_dismiss_restore_and_rebuild(client, sample):
    page = client.get("/settings").text
    assert 'id="st-yearly-bills"' in page and 'id="yb-smial-insurance"' in page and '<span class="status-chip ">Found</span>' in page
    assert 'aria-label="Accept Smial Insurance as found">Accept as found</button>' in page and 'aria-label="Dismiss Cart Registration">Dismiss</button>' in page
    r = client.post("/yearly-bills", data={"item": "Smial Insurance", "month": "2", "amount": "1000", "every_years": "1", "funded_by": "Smial Insurance Fund", "source": "confirmed"})
    assert r.status_code == 303 and r.headers["location"] == "/settings?focus=yb-smial-insurance#yearly-bills"   # back to the row (DS-A11Y-5)
    client.post("/yearly-bills/Cart Registration/dismiss")
    client.post("/rebuild")
    bills = {b["item"]: b for b in client.get("/api/datasets").json()["yearly_bills"]}
    assert bills["Smial Insurance"]["source"] == "confirmed" and bills["Smial Insurance"]["amount"] == 1000
    assert bills["Cart Registration"]["source"] == "dismissed"
    client.post("/rebuild")   # a dismissal survives every rebuild
    assert {b["item"]: b for b in client.get("/api/datasets").json()["yearly_bills"]}["Cart Registration"]["source"] == "dismissed"
    client.post("/yearly-bills/Cart Registration/restore"); client.post("/rebuild")
    assert {b["item"]: b for b in client.get("/api/datasets").json()["yearly_bills"]}["Cart Registration"]["source"] == "detected"
    r = client.post("/yearly-bills", data={"item": "Second Breakfast", "month": "5", "amount": "€45", "source": "added", "form": "add"})
    assert r.headers["location"] == "/settings?focus=yb-add#yearly-bills"
    page = client.get(r.headers["location"]).text   # the add form comes back open, with what was typed and the field marked
    assert "Second Breakfast is not one of your budget items. Pick one from the list" in page and 'value="Second Breakfast" aria-invalid="true"' in page
    assert '<details class="how" id="yb-add" open>' in page and 'value="€45"' in page
    client.post("/yearly-bills", data={"item": "Groceries", "month": "5", "amount": "1.234,50", "source": "added"})
    from app import db
    with db.pool.connection() as conn:   # a figure typed with a currency sign and another locale's grouping still reads
        assert float(conn.execute("SELECT amount FROM yearly_bills WHERE item='Groceries'").fetchone()["amount"]) == 1234.5


def test_cli_report_is_read_only_and_setters_work(client, sample, capsys):
    from app import admin
    with _db() as conn:
        before = conn.execute("SELECT count(*) AS n FROM yearly_bills").fetchone()["n"]
    assert admin.main(["yearly-bills"]) == 0
    out = capsys.readouterr()
    assert "Smial Insurance\tdetected\tFebruary" in out.out and "2025-02,2026-02" in out.out and "2 found and not yet confirmed" in out.err
    with _db() as conn:
        assert conn.execute("SELECT count(*) AS n FROM yearly_bills").fetchone()["n"] == before
    assert admin.main(["yearly-bills", "dismiss", "Cart Registration"]) == 0
    assert admin.main(["yearly-bills", "add", "Groceries", "--month", "13"]) == 2
    with _db() as conn:
        assert conn.execute("SELECT source FROM yearly_bills WHERE item='Cart Registration'").fetchone()["source"] == "dismissed"


def test_settings_zip_mcp_and_check_in_carry_them(client, sample, reset_db, tmp_path):
    from app import mcp_endpoint
    from app.settings_export import write_dir
    client.post("/yearly-bills/Cart Registration/dismiss")
    with _db() as conn:
        write_dir(conn, tmp_path)
    assert "Cart Registration,3,,1,,dismissed" in (tmp_path / "yearly_bills.csv").read_text()
    assert "yearly_bills" in mcp_endpoint.DATASETS
    acct = mcp_endpoint.PUBLIC["yearly_bills"]({"item": "x", "funded_by": "Bywater Checking", "funded_kind": "account"})
    assert acct["funded_by"] is None
    reset_db(tmp_path)
    with _db() as conn:
        assert conn.execute("SELECT source FROM yearly_bills WHERE item='Cart Registration'").fetchone()["source"] == "dismissed"


def test_coming_up_step_says_what_is_set_aside(client, sample):
    from app import db
    with db.pool.connection() as conn:   # a bill due within three months of June: confirm the premium as July's
        conn.execute("INSERT INTO yearly_bills (item, month, amount, source, funded_by) VALUES ('Smial Insurance', 8, 960, 'confirmed', 'Smial Insurance Fund')"); conn.commit()
    client.post("/rebuild")
    page = client.get("/checkin/2026-06").text
    step = page[page.find('id="step-coming_up"'):]
    assert "Yearly bill: Smial Insurance due" in step and "set aside in Smial Insurance Fund" in step
    assert 'data-draw="lm-bills"' in client.get("/").text


def test_moving_a_bills_month_keeps_its_last_payment_for_the_set_aside():
    rows = [row("2025-02", "Premium", 960), row("2026-02", "Premium", 960)] + [row(m, "Premium Fund", 0, planned=80) for m in months("2025-01", "2026-06")]
    stored = [{"item": "Premium", "source": "confirmed", "month": "8", "amount": "960", "every_years": "1", "funded_by": "Premium Fund"}]
    (b,) = yb.bills(rows, stored, "2026-06", set(LOADED), ["Premium", "Premium Fund"])
    assert b["last_paid"] == "2026-02" and b["next_due"] == "2026-08" and b["set_aside"] == 320.0   # March .. June, not every month on record


def test_cli_report_works_on_an_install_built_before_yearly_bills(client, sample, capsys):
    """The owner's check runs this branch's report against a 0.6 database: no yearly_bills dataset (and, there, no table). It works
    the bills out from the stored budget rows, in a read-only transaction, and saves nothing."""
    from app import admin, db
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM datasets WHERE name='yearly_bills'"); conn.commit()
    assert admin.main(["yearly-bills", "report"]) == 0
    out = capsys.readouterr()
    assert "worked out now" in out.err and "Smial Insurance\tdetected\tFebruary" in out.out and "Cart Registration" in out.out
    with db.pool.connection() as conn:
        assert conn.execute("SELECT 1 FROM datasets WHERE name='yearly_bills'").fetchone() is None


def test_a_bill_whose_month_passed_unpaid_is_flagged_not_dropped():
    """From the homelab check (#200): a bill seen in 2023 and 2024 and not in 2025 stays listed (paid late, or a year skipped), but
    says it was not paid, so a cancelled subscription is dismissed rather than lingering."""
    rows = [row("2024-07", "Streaming Annual", 60), row("2025-07", "Streaming Annual", 60)]
    (b,) = yb.bills(rows, [], "2026-09", set(months("2023-10", "2026-09")), ["Streaming Annual"])
    assert b["missed"] == "2026-07" and b["next_due"] == "2027-07"
    (ok,) = yb.bills(rows + [row("2026-07", "Streaming Annual", 60)], [], "2026-09", set(months("2023-10", "2026-09")), ["Streaming Annual"])
    assert ok["missed"] is None
    (wait,) = yb.bills(rows, [], "2026-07", set(months("2023-10", "2026-07")), ["Streaming Annual"])
    assert wait["missed"] is None   # July 2026 is the closing month: still inside its month's tolerance, not yet missed


def test_a_failed_change_comes_back_in_its_own_row(client, sample):
    """DS-FORM-1: a row's Change (or Confirm) that fails reopens that row's Change with what was typed and the error under the field,
    never the Add form."""
    r = client.post("/yearly-bills", data={"item": "Smial Insurance", "month": "2", "amount": "1000", "every_years": "eleven", "source": "confirmed"})
    assert r.headers["location"] == "/settings?focus=yb-smial-insurance-change#yearly-bills"
    page = client.get("/settings").text
    assert '<details id="yb-smial-insurance-change" open>' in page and '<details class="how" id="yb-add" >' in page
    assert 'id="yb-smial-insurance-e" name="every_years" inputmode="numeric" size="2" value="eleven" aria-invalid="true" aria-describedby="yb-smial-insurance-err"' in page
    assert 'id="yb-smial-insurance-err" role="alert" tabindex="-1">Every is a whole number of years' in page


def test_an_amount_with_no_figure_in_it_is_refused_not_saved_blank():
    from app.yearly_bills import parse_amount
    assert parse_amount("") is None and parse_amount("  ") is None and parse_amount("USD 45") == 45
    with pytest.raises(ValueError):
        parse_amount("lots")


def test_restoring_a_hand_added_bill_brings_it_back_with_its_values(client, sample):
    """#206: add, dismiss, restore, rebuild: the bill is back as added, with its month, amount, cadence and funding."""
    client.post("/yearly-bills", data={"item": "Groceries", "month": "5", "amount": "45", "every_years": "2", "funded_by": "Smial Insurance Fund", "source": "added"})
    client.post("/yearly-bills/Groceries/dismiss")
    with _db() as conn:
        r = conn.execute("SELECT source, dismissed_from, month, amount, every_years, funded_by FROM yearly_bills WHERE item='Groceries'").fetchone()
    assert (r["source"], r["dismissed_from"], r["month"], float(r["amount"]), r["every_years"], r["funded_by"]) == ("dismissed", "added", 5, 45.0, 2, "Smial Insurance Fund")
    client.post("/rebuild")
    assert {b["item"]: b for b in client.get("/api/datasets").json()["yearly_bills"]}["Groceries"]["source"] == "dismissed"
    r = client.post("/yearly-bills/Groceries/restore", follow_redirects=False)
    assert r.headers["location"] == "/settings?focus=yb-groceries#yearly-bills"   # lands on the restored row (DS-A11Y-5)
    assert "Restored Groceries as you added it" in client.get(r.headers["location"]).text
    client.post("/rebuild")
    g = {b["item"]: b for b in client.get("/api/datasets").json()["yearly_bills"]}["Groceries"]
    assert (g["source"], g["month"], g["amount"], g["every_years"], g["funded_by"]) == ("added", 5, 45.0, 2, "Smial Insurance Fund")
    # a confirmed bill comes back confirmed; a detected one still goes back to the detector (test above)
    client.post("/yearly-bills", data={"item": "Smial Insurance", "month": "2", "amount": "1000", "funded_by": "Smial Insurance Fund", "source": "confirmed"})
    client.post("/yearly-bills/Smial Insurance/dismiss"); client.post("/yearly-bills/Smial Insurance/restore")
    with _db() as conn:
        r = conn.execute("SELECT source, dismissed_from, amount FROM yearly_bills WHERE item='Smial Insurance'").fetchone()
    assert (r["source"], r["dismissed_from"], float(r["amount"])) == ("confirmed", None, 1000.0)


def test_a_bill_dismissed_before_0206_restores_to_the_detector(client, sample):
    """Rows dismissed before the migration have dismissed_from NULL and keep the old behaviour: Restore deletes the row."""
    with _db() as conn:
        conn.execute("INSERT INTO yearly_bills (item, month, source) VALUES ('Groceries', 5, 'dismissed')"); conn.commit()
    r = client.post("/yearly-bills/Groceries/restore", follow_redirects=False)
    assert r.headers["location"] == "/settings?focus=yearly-bills#yearly-bills"   # the last dismissed one: the section, not the page top
    with _db() as conn:
        assert conn.execute("SELECT 1 FROM yearly_bills WHERE item='Groceries'").fetchone() is None
