"""The dashboard page must read sensibly for any household, including a new install with no data."""
from __future__ import annotations
import re

import pytest

from conftest import GOLDEN, ROOT, upload_files

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


def test_real_dollar_base_month_is_the_one_the_pipeline_used(client, reset_db):
    from app import db
    upload_files(client, GOLDEN / "exports")   # budget months 2025-01..04; the golden rule names 2025-04
    assert "Apr 2025 prices" in client.get("/").text
    with db.pool.connection() as conn:   # no rule: the pipeline uses the latest CPI month, which is later than the latest budget month
        db.put_setting(conn, "rules", {k: v for k, v in db.get_setting(conn, "rules").items() if k != "cpi_base_month"})
        conn.execute("INSERT INTO cpi VALUES ('2025-10', 330, 'test')"); conn.commit()
    client.post("/rebuild")
    html = client.get("/").text
    assert "Oct 2025 prices" in html and "Apr 2025 prices" not in html
    base = client.get("/api/dataset/build_meta").json()[0]
    assert base["cpi_base_month"] == "2025-10"
    with db.pool.connection() as conn:   # an edit not yet built must not relabel figures that are still in the old base
        conn.execute("INSERT INTO cpi VALUES ('2025-11', 331, 'test')"); conn.commit()
    assert "Oct 2025 prices" in client.get("/").text


def test_base_month_before_any_build_follows_the_pipeline_rule(client, reset_db):
    from app import db
    with db.pool.connection() as conn:
        db.put_setting(conn, "rules", {}); conn.execute("INSERT INTO cpi VALUES ('2026-10', 330, 'test') ON CONFLICT DO NOTHING"); conn.commit()
        from app.web_helpers import _span
        assert _span(conn)["cpi_base_iso"] == "2026-10"
def test_goal_captions_quote_the_households_own_goals(client, reset_db):
    html = client.get("/").text
    for literal in ("against the 10% floor", "against the 10% target", "against the 25% cap"):
        assert literal not in html, literal
    assert "goalPct('giving_floor')" in html and "goalPct('joy_target')" in html and "goalPct('housing_cap')" in html


def _radar_groups(totals):
    """Run the page's own radarGroups() in node (skipped where node is not installed)."""
    import json, shutil, subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    src = (ROOT / "app" / "web" / "templates" / "dashboard.html").read_text()
    m = re.search(r"^const radarGroups = .*?^\};$", src, re.S | re.M)
    assert m, "radarGroups() is missing from the dashboard"
    prog = m.group(0) + f"\nprocess.stdout.write(JSON.stringify(radarGroups({json.dumps(totals)})));"
    return json.loads(subprocess.run([node, "-e", prog], check=True, capture_output=True, text=True).stdout)


def test_radar_stars_come_from_the_households_own_categories():
    src = (ROOT / "app" / "web" / "templates" / "dashboard.html").read_text()
    assert "RADAR_GROUPS" not in src and "Gifts & Holiday" not in src
    assert "ranked by their total over all years" in src  # the split rule is stated in "How to read this"
    cats = [[f"C{i}", 100 - i] for i in range(10)]
    g = _radar_groups(cats)
    assert [len(x) for x in g] == [4, 3, 3] and sorted(c for x in g for c in x) == sorted(c for c, _ in cats)
    assert g[0][0] == "C0"
    assert [len(x) for x in _radar_groups(cats[:8])] == [4, 4]
    assert [len(x) for x in _radar_groups(cats[:4])] == [4]
    assert _radar_groups([["Food", 5], ["Fun", 0], ["Rent", -2]]) == [["Food"]]  # categories with no spending never make a spoke
    assert _radar_groups([]) == [] and _radar_groups([["Fun", 0]]) == []  # no data: no star at all
