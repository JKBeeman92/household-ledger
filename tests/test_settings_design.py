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
    assert '"class": "btn primary" }, "Save"' in open("app/web/static/settings.js").read()   # the row editor's Save is the one primary


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
    assert '<span class="status-chip ok">Done</span> No changes this month.' in page and f"/checkin/{month}/steps/next/undo" in page
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
