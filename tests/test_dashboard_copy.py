"""The dashboard page must read sensibly for any household, including a new install with no data."""
from __future__ import annotations
import re

import pytest

from conftest import GOLDEN, upload_files

# wording written for the original household's history; the Method tab now generates its narrative from the datasets
RETIRED = ["family-loan", "pass-through fund", "Trauma Fund", "Boa", "Josh", "CJ", "NCM", "U-Haul", "Steam", "Pulmonx", "Student loans end", "first Sunday"]
STABLE_IDS = ["overview", "lastmonth", "goals", "where", "leaks", "position", "people", "method", "excl-table", "verify-table", "cpi-table", "method-exclusions"]


@pytest.mark.parametrize("loaded", [False, True])
def test_page_has_no_original_household_copy_and_keeps_ids(client, reset_db, loaded):
    if loaded:
        upload_files(client, GOLDEN / "exports")
    html = client.get("/").text
    for word in RETIRED:
        assert word not in html, word
    for i in STABLE_IDS:
        assert f'id="{i}"' in html, i
    assert not re.search(r"<th[^>]*>\s*20\d\d\s*</th>", html), "year columns are built from the data"


def test_empty_install_says_so_instead_of_claiming_a_span(client, reset_db):
    html = client.get("/").text
    assert 'id="empty-state"' in html and "/upload" in html
    assert "-year audit" not in html and "Start here for the shape" not in html


def test_loaded_install_shows_its_span(client, reset_db):
    upload_files(client, GOLDEN / "exports")
    html = client.get("/").text
    assert 'id="empty-state"' not in html and "-year audit" in html


def test_exclusions_dataset_is_self_describing(client, reset_db):
    upload_files(client, GOLDEN / "exports")
    rows = client.get("/api/dataset/exclusions").json()
    kinds = {r["kind"] for r in rows}
    assert {"excluded_item", "transfers_tagged", "plugs", "rows_in_totals", "months"} <= kinds
    assert all("(2018)" not in r["key"] and "(2022+)" not in r["key"] for r in rows)
    assert next(r for r in rows if r["kind"] == "excluded_item")["item"] == "Venmo Pass-Through"
