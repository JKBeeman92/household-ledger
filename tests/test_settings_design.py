"""Settings, Close a month and the check-in's Next step in keeping with docs/DESIGN.md (#193): one primary button to a view
(DS-BTN-1), a visible label on each filter box and bulk-edit field (DS-FORM-1), figures right-aligned (DS-TYPE-3), a row's name in
each bare-verb button (DS-A11Y-2), one level of sheet (DS-SHEET-2), an empty state's first step as a button (DS-EMPTY-1), and
an error under its field with what was typed kept (DS-FORM-1)."""
from __future__ import annotations
import re

import pytest

from conftest import GOLDEN, SAMPLE, upload_files
from app import checkin, db


@pytest.fixture
def loaded(client, reset_db):
    upload_files(client, GOLDEN / "exports")


@pytest.fixture
def sample(client, reset_db):
    from pipeline import import_exports
    reset_db(SAMPLE)
    assert import_exports.main([str(SAMPLE)]) == 0


def panel(page: str, pid: str) -> str:
    start = page.index(f'id="{pid}"')
    m = re.search(r'<div role="tabpanel" id="sp-', page[start:])
    return page[start:start + m.start()] if m else page[start:]


def test_close_a_month_has_one_primary_button_and_it_is_upload(client, loaded):
    page = client.get("/upload?balances_month=2025-04").text
    primaries = re.findall(r'<button[^>]*class="btn primary"[^>]*>([^<]*)<', page)
    assert primaries == ["Upload and rebuild"], primaries


def test_settings_items_and_accounts_tabs_have_no_primary_button_beside_the_row_save(client, loaded):
    page = client.get("/settings").text
    for pid in ("sp-mapping-items", "sp-accounts"):
        assert "btn primary" not in panel(page, pid), pid
    assert '"class": "btn primary", "aria-label": "Save " + named }, "Save"' in open("app/web/static/settings.js").read()   # the row editor's Save is the one primary


def test_settings_labels_figures_names_and_sheets(client, loaded):
    page = client.get("/settings").text
    for fid in ("mapping-filter", "categories-filter", "canonical-filter", "mapping-csv", "accounts-csv", "balances-csv", "rules-body", "cpi-body", "household-body"):
        assert f'<label for="{fid}">' in page and f'id="{fid}"' in page, fid
    assert 'aria-label="Filter' not in page and 'aria-label="Mapping CSV"' not in page
    assert '<th scope="col" class="num">Rows</th>' in page and '<th scope="col" class="num">Balances</th>' in page
    assert re.search(r'<th data-sort="value" scope="col" class="num">', page) and re.search(r'<th data-sort="apr" scope="col" class="num">', page)
    assert re.search(r'<td data-f="apr" class="num"', page)
    assert 'aria-label="Rename the category ' in page and 'aria-label="Rename the canonical item ' in page
    assert 'aria-label="Move the canonical item ' in page and 'aria-label="Set the debt type of ' in page
    assert '<details class="panel"' not in page   # no sheet inside a sheet (DS-SHEET-2)


def test_the_row_editor_keeps_a_figure_column_right_aligned():
    js = open("app/web/static/settings.js").read()
    assert 'classList.contains("num")' in js


def test_an_empty_settings_goals_tab_offers_the_first_step_as_a_button(client, reset_db):
    with db.pool.connection() as conn:
        db.put_setting(conn, "rules", {**(db.get_setting(conn, "rules", {}) or {}), "people": []}); conn.commit()
    page = client.get("/settings").text
    assert '<a class="btn" href="#rules">Add the household' in page


def test_next_month_step_says_a_verb_and_can_be_undone(client, sample):
    month = "2026-06"

    def status():
        with db.pool.connection() as conn:
            return {x["key"]: x["status"] for x in checkin.overview(conn, month)["steps"]}["next"]

    page = client.get(f"/checkin/{month}").text
    assert "Say there are no changes this month" in page and ">No changes this month</button>" not in page
    assert status() == "todo"
    client.post(f"/checkin/{month}/steps/next/ack")
    assert status() == "done"
    page = client.get(f"/checkin/{month}").text
    assert "No changes this month. <button" in page and "Undo: no changes this month" in page and f"/checkin/{month}/steps/next/undo" in page
    client.post(f"/checkin/{month}/steps/next/undo")
    assert status() == "todo"
    assert "Say there are no changes this month" in client.get(f"/checkin/{month}").text


def test_balances_error_sits_under_its_field_and_keeps_what_was_typed(client, loaded):
    client.post("/balances/2025-04", data={"account": ["Lembas Student Loan"], "amount": ["lots"]})
    page = client.get("/upload?balances_month=2025-04").text
    assert re.search(r'<input id="bal-\d+" name="amount"[^>]*aria-describedby="bal-how-(\d+) bal-err-\1"[^>]*aria-invalid="true"[^>]*value="lots"', page)
    assert re.search(r'<div class="err" id="bal-err-\d+" role="alert">Must be a number', page)
    assert 'value="lots"' not in client.get("/upload?balances_month=2025-04").text   # shown once, as a flash is


def test_cpi_series_url_error_sits_under_its_field_and_keeps_what_was_typed(client, reset_db):
    client.post("/cpi/url", data={"url": "ftp://x/y.csv"})
    page = client.get("/cpi").text
    assert 'value="ftp://x/y.csv"' in page and 'aria-invalid="true" aria-describedby="url-err"' in page
    assert re.search(r'<p class="err" id="url-err" role="alert">The series URL must start with http', page)
    assert 'aria-invalid' not in client.get("/cpi").text


# ---- the UX review of #193 ----
CSS = open("app/web/static/app.css").read()


def test_sort_buttons_keep_their_phone_size_after_the_reset_and_a_header_has_one_arrow():
    reset = CSS.index("th .sortbtn { all: unset")
    phone = CSS.rindex("th .sortbtn { display: inline-flex")
    assert phone > reset and "min-height: 44px" in CSS[phone:phone + 120]   # DS-LAYOUT-2: the reset must not clear it
    assert len(re.findall(r'aria-sort="(?:ascending|descending)"\] \.sortbtn::after', CSS)) == 2 and 'th[aria-sort=ascending]::after' not in CSS   # one arrow, on the button (DS-TYPE-1)


def test_a_disclosure_draws_its_own_marker_and_a_phone_keeps_the_actions_in_reach():
    assert "summary::before" in CSS and "details[open]:not(#dash-root details) > summary::before" in CSS
    assert re.search(r"table\.edit td\.acts[^{]*{[^}]*position: sticky; right: 0", CSS)
    assert ".panel p code" in CSS and "overflow-wrap: anywhere" in CSS


def test_row_buttons_are_named_on_first_render_and_after_a_save(client, loaded):
    page = client.get("/settings").text
    assert 'aria-label="Edit Lembas Student Loan"' in page and 'aria-label="Delete Lembas Student Loan"' in page
    assert page.count('role="region" tabindex="0" aria-label="Accounts table"') == 1
    js = open("app/web/static/settings.js").read()
    assert '"aria-label": "Save " + named' in js and '"aria-label": "Cancel " + named' in js and '"aria-label": "Edit " + what' in js
    assert "o.cancelEdit" in js   # one editor open at a time
    assert re.search(r'<td data-f="escrow" class="num" data-v="">—</td>', page)   # a missing figure is a dash; the stored value stays empty


def test_the_goal_checklist_error_sits_under_its_field_and_lands_on_the_row(client, loaded):
    with db.pool.connection() as conn:
        conn.execute("UPDATE goals SET value=NULL WHERE key=(SELECT key FROM goals ORDER BY key LIMIT 1)"); conn.commit()
        key = conn.execute("SELECT key FROM goals WHERE value IS NULL").fetchone()["key"]
    r = client.post(f"/goals/{key}/confirm", data={"value": "lots of it"})
    assert r.headers["location"] == f"/upload#goal-{key}"
    page = client.get("/upload").text
    assert f'id="goal-{key}" name="value"' in page and 'value="lots of it"' in page and f'aria-describedby="goal-{key}-err"' in page
    assert f'id="goal-{key}-err" role="alert"' in page and re.search(rf'aria-label="Save [^"]+"[^>]*>Save</button>', page)
    assert 'value="lots of it"' not in client.get("/upload").text


def test_a_rejected_balances_history_paste_comes_back_in_its_field(client, loaded):
    client.post("/settings/accounts/balances", data={"body": "Nobody,2024-11,1"})
    page = client.get("/settings").text
    assert re.search(r'<textarea[^>]*id="balances-csv"[^>]*aria-invalid="true"[^>]*>Nobody,2024-11,1</textarea>', page)
    assert 'id="balances-csv-err" role="alert"' in page
    assert 'aria-invalid="true" aria-describedby="balances-csv-err"' not in client.get("/settings").text


def test_the_next_step_flashes_what_was_recorded_and_undone(client, sample):
    r = client.post("/checkin/2026-06/steps/next/ack")
    assert "Recorded: no changes for June 2026." in client.get(r.headers["location"]).text
    page = client.get("/checkin/2026-06").text
    assert "Undo: no changes this month" in page and '<span class="status-chip ok">Done</span> No changes this month.' not in page
    r = client.post("/checkin/2026-06/steps/next/undo")
    assert "Undone: the Next month step is open again." in client.get(r.headers["location"]).text


def test_the_loaded_months_list_opens_only_when_short_and_counts_the_replaced(client, loaded):
    page = client.get("/upload").text
    n = len(re.findall(r"<tr><td class=\"mono\">", page[page.index("<h2>Loaded months</h2>"):]))
    summary = re.search(r"<details (open)?><summary class=\"muted\">Show the (\d+) files?", page)
    assert summary and int(summary.group(2)) == n and bool(summary.group(1)) == (n <= 8)


def test_build_status_and_reconciliation_count_the_same_envelope_months(client, loaded):
    page = client.get("/upload").text
    n = re.search(r"all (\d+) envelope-months reconcile", page)
    assert n and page.count(f"all {n.group(1)} envelope-months reconcile") == 2   # the build status line, and the Reconciliation panel


def test_close_a_month_is_named_one_way_where_it_is_asked(client, loaded):
    page = client.get("/settings").text + client.get("/upload").text
    assert "month-close" not in page.replace("Month-close review", "") and "Month-close checklist" not in page
    assert 'href="/settings#accounts/history">Earlier months' in client.get("/upload").text or "Earlier months" not in client.get("/upload").text
    assert "Consumer price index (CPI)" in client.get("/cpi").text
