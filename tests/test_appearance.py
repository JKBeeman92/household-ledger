"""Chart appearance (#132): a colour and a hide switch per name, in one table. The checks, the table's ways in and out (Settings, the
CLI, settings.zip and a seed directory), renames, and the dashboard itself in a browser: with no rows every chart draws exactly as
before; an assigned name keeps its colour everywhere; unassigned names never take a claimed slot; hiding changes no figure."""
from __future__ import annotations
import json, os, shutil, socket, subprocess, sys, time, urllib.request

import pytest

from conftest import GOLDEN, ROOT, SAMPLE, upload_files
from app import admin, appearance, brand_check, db, settings_export
from pipeline import import_exports
from pipeline.run_pipeline import seed_reference_tables

KIT = brand_check.read(ROOT / "app" / "web" / "static" / "brand" / "theme.css")
TOKS = {m: brand_check.tokens(KIT, m) for m in brand_check.MODES}


def rows(conn) -> list[dict]:
    return [dict(r) for r in appearance.rows(conn)]


# ------------------------------------------------------------------ checks (no database)

def test_a_colour_is_a_slot_or_a_colour_the_contrast_check_can_read():
    assert appearance.check_color("series-3") == ("series-3", None)
    assert appearance.check_color(" Series 7 ")[0] == "series-7" and appearance.check_color("4")[0] == "series-4"
    assert appearance.check_color("") == (None, None) and appearance.check_color(None) == (None, None)
    assert appearance.check_color("#2F7F9A") == ("#2f7f9a", None) and appearance.check_color("oklch(0.5 0.12 250)")[1] is None
    for bad in ("series-9", "url(x)", "red", "#12", "var(--danger)", "#00000080", "rgb(1 2 3); background: x", 7):
        assert appearance.check_color(bad)[1], bad


def test_a_fixed_colour_warns_naming_the_mode_and_a_slot_never_does():
    assert appearance.warnings("series-2", TOKS) == [] and appearance.warnings(None, TOKS) == []
    assert appearance.warnings("#2f7f9a", TOKS) == []   # the sample household's: clear in both modes
    faint = appearance.warnings("#fbf9f1", TOKS)   # all but the day panel itself
    assert any(w.startswith("by day") and "faint" in w for w in faint) and not any(w.startswith("by night") and "faint" in w for w in faint)
    day_ok = brand_check.resolve("success", TOKS["light"])   # the day kit's own "kept" green
    near = appearance.warnings(day_ok, TOKS)
    assert any(w.startswith("by day") and "--success" in w and "ΔE 0.0" in w for w in near)
    near_danger = appearance.warnings(brand_check.resolve("danger", TOKS["dark"]), TOKS)
    assert any(w.startswith("by night") and "--danger" in w for w in near_danger)


def test_delta_e_is_oklab_distance_times_100():
    assert brand_check.delta_e("#ffffff", "#ffffff") == pytest.approx(0, abs=1e-6)
    assert brand_check.delta_e("#000000", "#ffffff") == pytest.approx(100, abs=0.1)   # L runs 0 to 1
    L, a, b = brand_check.oklab("#ff0000")
    assert (L, a, b) == pytest.approx((0.628, 0.2249, 0.1258), abs=1e-3)   # Ottosson's published value for sRGB red


def test_appearance_json_is_checked_as_the_page_checks_a_row():
    ok = appearance.parse([{"dimension": "category", "name": " Food ", "color": "3"}, {"dimension": "person", "name": "Sam", "hidden": True},
                           {"dimension": "debt", "name": "Loan"}])   # an "as before" row is no row
    assert ok == [{"dimension": "category", "name": "Food", "color": "series-3", "hidden": False}, {"dimension": "person", "name": "Sam", "color": None, "hidden": True}]
    with pytest.raises(ValueError) as e:
        appearance.parse([{"dimension": "colour", "name": "x"}, {"dimension": "category", "name": "Food", "color": "red"}, {"dimension": "category", "name": ""},
                          {"dimension": "person", "name": "Sam", "hidden": "yes"}])
    msg = str(e.value)
    assert "item 1: dimension" in msg and "item 2: color" in msg and "item 3: name" in msg and "item 4: hidden" in msg
    with pytest.raises(ValueError, match="listed twice"):
        appearance.parse([{"dimension": "category", "name": "Food", "color": "1"}] * 2)


# ------------------------------------------------------------------ the table, Settings, renames, CLI, seed

def test_settings_lists_the_names_the_data_has_and_saves_a_dimension(client, reset_db):
    upload_files(client, GOLDEN / "exports")
    html = client.get("/settings").text
    assert 'id="st-appearance"' in html and 'data-subtabs="appearance"' in html
    with db.pool.connection() as conn:
        have = appearance.present(conn)
    assert have["category"] and "Housing" in have["category"]
    for d in appearance.DIMENSIONS:   # each dimension's list is its own sub-tab, filterable when it is long enough to scan
        slug = d.replace("_", "-")
        assert f'id="sp-appearance-{slug}"' in html
        if have[d]:
            assert f'id="appearance-{slug}-table" data-list="unbounded"' in html
            assert (f'data-filter-table="appearance-{slug}-table"' in html) == (len(have[d]) > appearance.FILTER_FROM), d
            if len(have[d]) > appearance.FILTER_FROM:
                assert f"Filter the {len(have[d])} {appearance.LABELS[d][0].lower()}</label>" in html
    # how colours and hiding work is said once, in plain words, above the sub-tabs; each tab has one short line of its own
    assert html.count('id="appearance-how"') == 1 and html.index('id="appearance-how"') < html.index('data-subtabs="appearance"')
    section = html[html.index('id="sp-appearance"'):html.index('<script src="/static/settings.js"')]
    for word in ("palette slot", "rank", "rebuild", "palette order"):
        assert word not in section, word
    for d, (_, noun, chart) in appearance.LABELS.items():
        tail = (". To take an event off the charts, set On the chart to no on <a href=\"/settings#events\">Life events</a>." if d in appearance.COLOUR_ONLY
                else ", and whether it is hidden there.")   # a kind has a colour only (#235)
        assert f'<p class="muted">The colour of each {noun} on {chart}{tail}</p>' in html
    assert "Saves this tab only." in html
    assert 'id="st-appearance-orphans"' not in html and 'id="sp-appearance-orphans"' not in html   # no leftovers, no tab
    r = client.post("/settings/appearance/save", data={"dimension": "category", "name": ["Housing", "Food"], "color": ["series-2", "#2f7f9a"], "hidden": ["Food"]})
    assert r.status_code == 303 and r.headers["location"] == "/settings#appearance/category"
    with db.pool.connection() as conn:
        assert rows(conn) == [{"dimension": "category", "name": "Food", "color": "#2f7f9a", "hidden": True},
                              {"dimension": "category", "name": "Housing", "color": "series-2", "hidden": False}]
    # back to the palette and shown: the row goes, so the name draws exactly as before
    client.post("/settings/appearance/save", data={"dimension": "category", "name": ["Housing"], "color": [""]})
    with db.pool.connection() as conn:
        assert [r["name"] for r in rows(conn)] == ["Food"]
    assert "Saved the chart colours for categories" in client.get("/settings").text


def test_an_unreadable_colour_refuses_the_save_and_comes_back_under_its_field(client, reset_db):
    upload_files(client, GOLDEN / "exports")
    client.post("/settings/appearance/save", data={"dimension": "category", "name": ["Housing", "Food"], "color": ["series-1", "chartreuse-ish"]})
    with db.pool.connection() as conn:
        assert rows(conn) == []   # nothing saved, not even the good row
    html = client.get("/settings").text
    assert "Not saved: 1 colour could not be read (Food)" in html
    assert 'value="chartreuse-ish"' in html and 'aria-invalid="true"' in html and "A theme colour (series-1 to series-8)" in html
    # the refused form is the one tab known to hold unsaved values: it is rendered marked, so the guard covers it from the start
    assert html.count('action="/settings/appearance/save" data-unsaved>') == 1
    assert 'save" data-unsaved>' not in client.get("/settings").text   # the typed values are shown once; after that the saved ones


def test_a_faint_or_verdict_coloured_choice_is_saved_with_a_warning(client, reset_db):
    upload_files(client, GOLDEN / "exports")
    green = brand_check.resolve("success", TOKS["light"])
    client.post("/settings/appearance/save", data={"dimension": "category", "name": ["Housing"], "color": [green]})
    with db.pool.connection() as conn:
        assert rows(conn)[0]["color"] == green   # warned, never refused
    html = client.get("/settings").text
    assert "Saved anyway, but check: Housing: by day" in html and "--success" in html


def test_orphans_are_listed_with_delete(client, reset_db):
    upload_files(client, GOLDEN / "exports")
    with db.pool.connection() as conn:
        appearance.set_one(conn, "category", "Housing", "series-1", False)
        appearance.set_one(conn, "employer", "Old Mill", "series-4", True); conn.commit()
        assert [o["name"] for o in appearance.page(conn)["orphans"]] == ["Old Mill"]
    html = client.get("/settings").text
    assert 'id="appearance-orphans-table" data-list="unbounded"' in html and "Delete the colour and hide switch kept for the employer Old Mill" in html
    assert 'id="st-appearance-orphans"' in html and "Not in the data <span class=\"count\">1</span>" in html
    r = client.post("/settings/appearance/delete", data={"dimension": "employer", "name": "Old Mill"})
    assert r.headers["location"] == "/settings#appearance/orphans"
    with db.pool.connection() as conn:
        assert [r["name"] for r in rows(conn)] == ["Housing"]
    assert 'id="st-appearance-orphans"' not in client.get("/settings").text   # the last one deleted: the tab goes, the hash falls back


def test_a_rename_keeps_colour_and_hidden(client, reset_db):
    """#133: a category rename carries the category's row; a canonical rename carries the catch-all item's and the debt's; a merge keeps
    the surviving name's own row."""
    upload_files(client, GOLDEN / "exports")
    from app import renames
    with db.pool.connection() as conn:
        cats = sorted(appearance.present(conn)["category"]); debts = appearance.present(conn)["debt"]
        assert "Housing" in cats and debts
        appearance.set_one(conn, "category", "Housing", "series-5", True)
        appearance.set_one(conn, "debt", debts[0], "#2f7f9a", False)
        other = next(c for c in cats if c not in ("Housing",))
        appearance.set_one(conn, "category", other, "series-2", False); conn.commit()
    assert client.post("/settings/mapping/category/rename", data={"old": "Housing", "new": "Home"}).status_code == 303
    assert client.post("/settings/mapping/canonical/rename", data={"old": debts[0], "new": "Renamed Loan"}).status_code == 303
    with db.pool.connection() as conn:
        got = {(r["dimension"], r["name"]): (r["color"], r["hidden"]) for r in rows(conn)}
    assert got[("category", "Home")] == ("series-5", True) and ("category", "Housing") not in got
    assert got[("debt", "Renamed Loan")] == ("#2f7f9a", False) and ("debt", debts[0]) not in got
    client.post("/settings/mapping/category/rename", data={"old": "Home", "new": other})   # a merge: the name kept keeps its own
    with db.pool.connection() as conn:
        got = {(r["dimension"], r["name"]): (r["color"], r["hidden"]) for r in rows(conn)}
    assert got[("category", other)] == ("series-2", False) and ("category", "Home") not in got


def test_cli_set_and_export_round_trip(client, reset_db, tmp_path, capsys):
    f = tmp_path / "appearance.json"
    f.write_text(json.dumps([{"dimension": "person", "name": "Sam", "color": "series-6"}, {"dimension": "item", "name": "Generosity", "hidden": True},
                             {"dimension": "account_type", "name": "owed: student", "color": "#2f7f9a"}]))
    assert admin.main(["set-appearance", str(f)]) == 0
    assert "Saved 3 appearance rows." in capsys.readouterr().out   # display only: no "rebuild" advice
    assert admin.main(["export", "appearance"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert sorted(out, key=lambda x: (x["dimension"], x["name"])) == [{"dimension": "account_type", "name": "owed: student", "color": "#2f7f9a"},
                                                                      {"dimension": "item", "name": "Generosity", "hidden": True}, {"dimension": "person", "name": "Sam", "color": "series-6"}]
    f.write_text(json.dumps([{"dimension": "person", "name": "Sam", "color": "mauve"}]))
    assert admin.main(["set-appearance", str(f)]) == 1   # refused, and nothing changed
    assert "Not saved" in capsys.readouterr().err
    with db.pool.connection() as conn:
        assert len(rows(conn)) == 3


def test_settings_zip_and_a_seed_dir_round_trip(client, reset_db, tmp_path):
    with db.pool.connection() as conn:
        appearance.set_one(conn, "category", "Food", "series-3", False); appearance.set_one(conn, "event_kind", "move", "#2f7f9a", False); conn.commit()
        files = settings_export.settings_files(conn)
        before = rows(conn)
    assert "appearance.json" in files
    (tmp_path / "appearance.json").write_bytes(files["appearance.json"])
    with db.pool.connection() as conn:
        conn.execute("TRUNCATE appearance"); conn.execute("DELETE FROM settings WHERE key=%s", (appearance.WRITTEN,))   # a new install
        seed_reference_tables(conn, tmp_path); conn.commit()
        assert rows(conn) == before
        appearance.set_one(conn, "category", "Food", "series-8", False); conn.commit()
        seed_reference_tables(conn, tmp_path); conn.commit()   # only into an empty table
        assert {r["name"]: r["color"] for r in rows(conn)}["Food"] == "series-8"


def test_clearing_every_colour_survives_a_restart_with_the_seed_dir_still_there(client, reset_db, tmp_path):
    """An empty table is the normal state (a cleared name loses its row), so the seed must run once, not whenever the table is empty:
    a household that moved installs with settings.zip in seed/ and then cleared its colours keeps them cleared (#132 review)."""
    (tmp_path / "appearance.json").write_text(json.dumps([{"dimension": "category", "name": "Food", "color": "series-3"}]))
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM appearance"); conn.execute("DELETE FROM settings WHERE key=%s", (appearance.WRITTEN,))
        seed_reference_tables(conn, tmp_path); conn.commit()   # first boot
        assert [(r["name"], r["color"]) for r in rows(conn)] == [("Food", "series-3")]
    client.post("/settings/appearance/save", data={"dimension": "category", "name": ["Food"], "color": [""]})   # cleared: the row goes
    with db.pool.connection() as conn:
        assert rows(conn) == []
        seed_reference_tables(conn, tmp_path); conn.commit()   # the next start
        assert rows(conn) == []
    with db.pool.connection() as conn:   # an install whose household cleared a row before any seed file arrived: no seed either
        conn.execute("DELETE FROM settings WHERE key=%s", (appearance.WRITTEN,))
        appearance.set_one(conn, "category", "Food", None, False); seed_reference_tables(conn, tmp_path); conn.commit()
        assert rows(conn) == []


def test_the_sample_household_seeds_its_appearance(client, reset_db):
    reset_db(SAMPLE)
    with db.pool.connection() as conn:
        got = {(r["dimension"], r["name"]): (r["color"], r["hidden"]) for r in rows(conn)}
    assert got[("category", "Housing")] == ("series-1", False) and got[("item", "Generosity")] == (None, True)
    assert all(not appearance.warnings(c) for c, _ in got.values())   # the sample's own choices pass the checks


def test_the_dashboard_gets_the_table_as_one_json_block_and_the_pipeline_never_reads_it(client, reset_db):
    with db.pool.connection() as conn:
        appearance.set_one(conn, "category", "Food", "series-3", True); conn.commit()
    html = client.get("/").text
    start = html.index('<script type="application/json" id="ledger-appearance"'); body = html[html.index(">", start) + 1:html.index("</script>", start)]
    assert json.loads(body) == {"category": {"Food": {"color": "series-3", "hidden": True}}}
    for p in (ROOT / "pipeline").glob("*.py"):   # colour is display: no build input (the seed loader only copies the file in)
        text = p.read_text()
        assert "appearance" not in text or p.name == "run_pipeline.py", p.name
    client.post("/settings/appearance/save", data={"dimension": "category", "name": ["Food"], "color": ["series-4"]})
    with db.pool.connection() as conn:   # a save asks for no rebuild
        assert conn.execute("SELECT 1 FROM settings WHERE key=%s", (db.INPUTS_CHANGED,)).fetchone() is None


# ------------------------------------------------------------------ the dashboard in a browser

def _node_ok() -> bool:
    if shutil.which("node") is None:
        return False
    return subprocess.run(["node", "-e", "require('playwright')"], capture_output=True).returncode == 0


browser = pytest.mark.skipif(not _node_ok(), reason="node with playwright is not installed")


@pytest.fixture(scope="module")
def server(client):
    """The app in a process of its own, on the test database, for a real browser."""
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = {**os.environ, "AUTH_DISABLED": "true"}
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log", "--no-proxy-headers"],
                         cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/api/status", timeout=1); break
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    yield url
    p.terminate(); p.wait(10)


@pytest.fixture(scope="module")
def sample(client, server):
    """The sample household, loaded once for the browser tests; each test sets the appearance table it needs."""
    from conftest import TABLES
    from pipeline.run_pipeline import YEARLY_CHECKLIST_DEFAULTS
    with db.pool.connection() as conn:
        conn.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
        for i, (k, label) in enumerate(YEARLY_CHECKLIST_DEFAULTS):
            conn.execute("INSERT INTO yearly_checklist_items (key, label, position, takes_amount) VALUES (%s, %s, %s, %s)", (k, label, (i + 1) * 10, k == "tax_outcome"))
        seed_reference_tables(conn, SAMPLE); db.put_setting(conn, "household", {"name": "Sample", "title": "Sample", "currency": "USD"}); conn.commit()
    assert import_exports.main([str(SAMPLE)]) == 0
    return server


def set_table(items: list[dict]) -> None:
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM appearance")
        for x in items:
            appearance.set_one(conn, x["dimension"], x["name"], x.get("color"), x.get("hidden", False))
        conn.commit()


def harness(url: str, **opts) -> dict:
    p = subprocess.run(["node", str(ROOT / "tests" / "js" / "dashboard_harness.js"), url, json.dumps(opts)], capture_output=True, text=True, timeout=180)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["errors"] == [], out["errors"]
    return out


STACKS = {"cat-stack": 0, "employer": 0, "slush": 0, "earner": 2, "payer": 2}   # chart: the palette offset it has always started from
LEGENDS = {"cat-legend": ("cat-stack", 0), "employer-legend": ("employer", 0), "slush-legend": ("slush", 0), "earner-legend": ("earner", 2),
           "payer-legend": ("payer", 2), "nw-legend": ("nw-chart", 0)}


@browser
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_with_no_rows_every_chart_draws_exactly_as_before(sample, theme):
    """The colour every chart coloured by a name had before #132: series i of a stack takes colors[(i + skip) % 8], debt i takes
    colors[i % 8], the account-type stacks the same, and every life-event marker the reference colour."""
    set_table([])
    s = harness(sample, theme=theme)["before"]
    c = s["colors"]; assert len(c) == 8 and len(set(c)) == 8
    for chart, skip in STACKS.items():
        series = s["charts"][chart]["series"]
        assert series and [f for _, f in series] == [c[(i + skip) % 8] for i in range(len(series))], chart
        assert s["charts"][chart]["note"] is None
    for leg, (chart, skip) in LEGENDS.items():
        sw = [v for _, v in s["legends"][leg]]
        assert sw and sw == [c[(i + skip) % 8] for i in range(len(sw))], leg
    bars = s["charts"]["debts"]["bars"]
    assert bars and [f for _, f in bars] == [c[i % 8] for i in range(len(bars))]
    nw = s["charts"]["nw-chart"]["series"]
    assert nw and [f for _, f in nw] == [c[i % 8] for i in range(len(nw))]
    for chart in ("monthly", "employer"):
        m = s["charts"][chart]
        assert m["markers"] and {f for *_, f in m["markers"]} == {"var(--chart-reference)"} and set(m["lines"]) == {"var(--chart-reference)"}, chart
    assert {k: v["sw"] for k, v in s["tips"]["cat-stack"].items()} == dict(s["charts"]["cat-stack"]["series"])   # the tooltip's swatch is the bar's


@browser
def test_an_assigned_name_keeps_its_colour_and_no_other_name_takes_a_claimed_slot(sample):
    set_table([{"dimension": "category", "name": "Housing", "color": "series-1"}, {"dimension": "category", "name": "Food", "color": "#2f7f9a"},
               {"dimension": "person", "name": "Sam", "color": "series-3"}, {"dimension": "event_kind", "name": "family", "color": "series-5"},
               {"dimension": "debt", "name": "Student Loan", "color": "series-8"}, {"dimension": "account_type", "name": "cash", "color": "series-2"}])
    seen = {}
    for theme in ("light", "dark"):
        for rng in (None, "12m", "ly", "ytd"):
            s = harness(sample, theme=theme, params={"range": rng} if rng else None)["before"]
            c = s["colors"]
            cat = dict(s["charts"]["cat-stack"]["series"])
            assert cat["Housing"] == c[0] and cat["Food"] == "#2f7f9a", (theme, rng)
            others = [f for n, f in cat.items() if n not in ("Housing", "Food")]
            assert c[0] not in others and len(set(others)) == min(len(others), 7)   # slot 1 is claimed; the rest stay apart
            assert dict(s["legends"]["cat-legend"])["Housing"] == c[0] and s["tips"]["cat-stack"]["Housing"]["sw"] == c[0]
            for chart in ("earner", "payer"):
                ppl = dict(s["charts"][chart]["series"]); assert ppl["Sam"] == c[2] and c[2] not in [f for n, f in ppl.items() if n != "Sam"]
            assert dict(s["legends"]["earner-legend"])["Sam"] == c[2]
            assert dict(s["charts"]["debts"]["bars"])["Student Loan"] == c[7]
            assert dict(s["charts"]["nw-chart"]["series"])["asset:cash"] == c[1] and dict(s["legends"]["nw-legend"])["cash"] == c[1]
            fam = [f for _, kind, f in s["charts"]["monthly"]["markers"] if kind == "family"]   # Elanor, 2026-02: not in last year's range
            assert (bool(fam) == (rng != "ly")) and set(fam) <= {c[4]} and {f for _, kind, f in s["charts"]["monthly"]["markers"] if kind != "family"} <= {"var(--chart-reference)"}
            seen.setdefault(theme, set()).add(cat["Housing"])
    assert seen["light"] != seen["dark"] and all(len(v) == 1 for v in seen.values())   # a slot follows the theme, and only the theme


@browser
def test_a_slot_given_to_a_middle_name_leaves_every_name_its_own_colour(sample):
    """Claiming one slot must not make two unassigned names share one while slots are free (#132 review): an unassigned name takes the
    free slot of its rank among the unassigned names, not of its place in the whole order."""
    set_table([])
    names = [n for n, _ in harness(sample)["before"]["charts"]["cat-stack"]["series"]]
    assert len(names) >= 8, names   # the case needs every slot in use
    for k, slot in ((3, 4), (5, 6), (6, 1)):   # the slot it had anyway, twice; then one that belonged to another name
        set_table([{"dimension": "category", "name": names[k], "color": f"series-{slot}"}])
        s = harness(sample)["before"]
        fills = [f for _, f in s["charts"]["cat-stack"]["series"]][:8]
        assert len(set(fills)) == 8 and fills[k] == s["colors"][slot - 1], (k, fills)
        assert [v for _, v in s["legends"]["cat-legend"]][:8] == fills   # the legend agrees with the bars


@browser
def test_hiding_changes_no_total_and_show_is_for_this_viewer_only(sample):
    set_table([])
    plain = harness(sample)["before"]
    set_table([{"dimension": "category", "name": "Housing", "hidden": True}, {"dimension": "item", "name": "Generosity", "hidden": True},
               {"dimension": "account_type", "name": "savings", "hidden": True}, {"dimension": "event_kind", "name": "job", "color": "series-4"},
               {"dimension": "debt", "name": "Student Loan", "hidden": True}])
    r = harness(sample, press="cat-stack", reload=True, fresh=True)
    s = r["before"]
    assert s["tables"] == plain["tables"] and s["tiles"] == plain["tiles"]   # every table and figure, reconciliation included
    assert "reconciliation" in s["tables"] and "dt-nw" in s["tables"] and "dt-debts" in s["tables"]
    assert "Housing" not in dict(s["charts"]["cat-stack"]["series"]) and "Housing" not in dict(s["legends"]["cat-legend"])
    assert s["charts"]["cat-stack"]["note"] == "1 hidden · show"
    # the other names keep the colour they had: the order is the full order, hidden names included
    assert {n: f for n, f in s["charts"]["cat-stack"]["series"]} == {n: f for n, f in plain["charts"]["cat-stack"]["series"] if n != "Housing"}
    assert {n: t["total"] for n, t in s["tips"]["cat-stack"].items()} == {n: t["total"] for n, t in plain["tips"]["cat-stack"].items() if n != "Housing"}
    assert "Generosity" not in dict(s["charts"]["slush"]["series"]) and s["charts"]["slush"]["note"] == "1 hidden · show"
    assert "asset:savings" not in dict(s["charts"]["nw-chart"]["series"]) and "savings" not in dict(s["legends"]["nw-legend"])
    # an event kind has a colour only (#235): its markers stay, coloured, and neither timeline's note counts it
    assert [k for _, k, _ in plain["charts"]["employer"]["markers"]] == ["job", "job"] == [k for _, k, _ in s["charts"]["employer"]["markers"]]
    assert {f for *_, f in s["charts"]["employer"]["markers"]} == {s["colors"][3]} and s["charts"]["employer"]["note"] is None
    assert s["charts"]["monthly"]["note"] is None
    assert "Student Loan" not in dict(s["charts"]["debts"]["bars"]) and s["charts"]["debts"]["note"] == "1 hidden · show"
    # the button's name is its visible words (WCAG 2.5.3, the 0.9.0 release review); what it hides, and for whom, is its title
    assert all(c["noteLabel"] is None for c in s["charts"].values() if c["note"])
    assert s["charts"]["cat-stack"]["noteTitle"] == "Hidden in Settings → Chart appearance: Housing. Show brings them back on this chart, for you only."
    assert s["charts"]["debts"]["noteTitle"].startswith("Hidden in Settings → Chart appearance: Student Loan.")
    # a debt group whose every debt is hidden leaves no empty heading; the others keep theirs
    assert len(plain["charts"]["debts"]["heads"]) == len(s["charts"]["debts"]["heads"]) + 1, (plain["charts"]["debts"]["heads"], s["charts"]["debts"]["heads"])
    # "show" reveals that chart's hidden names for this viewer, remembered like the period filter; another viewer still has them hidden
    for snap in (r["after"], r["reloaded"]):
        assert dict(snap["charts"]["cat-stack"]["series"]) == dict(plain["charts"]["cat-stack"]["series"])
        assert snap["charts"]["cat-stack"]["note"] == "1 hidden, shown here · hide" and snap["params"]["reveal"] == {"cat-stack": 1}
        assert "Generosity" not in dict(snap["charts"]["slush"]["series"])   # only the chart that was pressed
        assert snap["tables"] == plain["tables"]
    assert r["focused"] == "1 hidden, shown here · hide"
    assert r["after"]["charts"]["cat-stack"]["noteTitle"] == "Hidden in Settings → Chart appearance: Housing. Shown on this chart for you only."
    assert "Housing" not in dict(r["other"]["charts"]["cat-stack"]["series"])


# DS-CHART-5: what each chart coloured by a name states, drawn from the data, on the sample household (2026 has six months loaded)
SAMPLE_FINDINGS = {
    "fd-monthly": "Jan 2025 to Jun 2026: income covered outgo in all 18 budget months, and the household kept $29,598.",
    "fd-employer": "Jan 2025 to Jun 2026: Hobbiton Mill paid $75,400 in paychecks.",
    "fd-cat": "2026 (6 mo): Housing and Food took 51% of the $28,484 spent.",
    "fd-slush": "2026 (6 mo): the catch-all envelopes took $3,985, the most Generosity with $2,280.",
    "fd-earner": "2026 (6 mo): Rosie earned 66% of the $38,618 income, Sam 34%.",
    "fd-payer": "2026 (6 mo): personal envelopes took 5% of the $28,484 spent: Rosie $697, Sam $637. The other 95% was shared, and the chart leaves it out.",
}


@browser
def test_each_chart_states_its_finding_from_the_data_and_hiding_changes_none(sample):
    set_table([])
    s = harness(sample)["before"]
    assert s["unreachable"] == []   # every scrolling table box: role=region, tabindex=0 and a name
    for k, text in SAMPLE_FINDINGS.items():
        assert s["findings"][k]["text"] == text, k
        assert text in s["findings"][k]["label"], k   # a11y.js folds it into the chart's accessible name
    # hiding is display only: a finding says what the data says, as the debts and net worth findings always have
    set_table([{"dimension": "category", "name": "Housing", "hidden": True}, {"dimension": "person", "name": "Rosie", "hidden": True},
               {"dimension": "employer", "name": "Hobbiton Mill", "hidden": True}])
    h = harness(sample)["before"]
    assert {k: v["text"] for k, v in h["findings"].items()} == {k: v["text"] for k, v in s["findings"].items()}
    # a period that ends a year early: the latest year in it is the full one, and it says so without a month count
    ly = harness(sample, params={"range": "ly"})["before"]["findings"]
    assert ly["fd-cat"]["text"].startswith("2025: ") and ly["fd-monthly"]["text"].startswith("Jan 2025 to Dec 2025: ")
    set_table([])
