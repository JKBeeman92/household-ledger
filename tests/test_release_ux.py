"""The 0.9.0 release-wide UX review: one name per thing across pages, and the markup fixes it asked for.

Each test pins a finding by what a household reads: the first-run card, Close a month and Settings → Sources say "source profile"
for how another tool's columns are read and keep "Item mapping" for the category rows (DS-WORD-3); the batch review says which
files replace a stored month and how to fix a wrong guess (DS-WORD-4); Settings → Life events says when an event's kind is hidden;
the check-in's By person names the shared row Joint (DS-WORD-7); and the fields and markers the review found are labelled."""
from __future__ import annotations
import json, re

from conftest import ROOT, SAMPLE, upload_files
from test_back_catalogue import household24  # noqa: F401  the 24-month sample, as the back-catalogue tests use it

TEMPLATES = ROOT / "app" / "web" / "templates"


def _text(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- one name per thing (DS-WORD-3)
def test_the_column_rule_is_a_source_profile_and_item_mapping_is_the_category_rows():
    first = _text("dashboard.html")
    card = first[first.index('id="empty-state"'):first.index("</ol>", first.index('id="empty-state"'))]
    assert "read through a <b>source profile</b>, set up once on upload" in card and "<b>Item mapping</b> (Settings)" in card
    assert "mapping" not in card.replace("Item mapping", "")   # no bare "mapping" left for either thing
    page = _text("upload_map.html")
    assert '<h1 class="page-title">Set up a source profile</h1>' in page and ">Save the source profile, store and rebuild</button>" in page
    assert "The source profile is saved under this name" in page and "mapping" not in page.replace("map/", "").replace("map(", "")
    upload = _text("upload.html")
    assert "set up a source profile once" in upload and "after editing Item mapping, CPI" in upload and "map their columns" not in upload
    assert "need no setting up" in _text("sources.html")
    # the working pages of Close a month take its name for their eyebrow (DS-WORD-3), as the header's tab does
    for n in ("upload.html", "upload_map.html", "upload_batch.html"):
        assert '<p class="eyebrow">Close a month</p>' in _text(n) and "Month close" not in _text(n), n
    rule = (ROOT / "docs" / "DESIGN.md").read_text(encoding="utf-8")
    assert "**source profile**" in rule and "**Item mapping**" in rule


def test_no_template_calls_either_thing_a_mapping_alone():
    """DS-WORD-3 on every page, not only the ones the review read: once Item mapping, ids, URLs, Jinja and comments are taken out,
    the bare word is left in no template; two messages from the routes are checked by name."""
    left = []
    for p in sorted(TEMPLATES.rglob("*.html")):
        t = re.sub(r"\{#.*?#\}|<!--.*?-->|\{%.*?%\}|\{\{.*?\}\}", "", p.read_text(encoding="utf-8"), flags=re.S)
        t = re.sub(r"(?m)^\s*//.*$", "", t).replace("Item mapping", "")
        t = re.sub(r"[\w/#.-]*[-_/#.]mapping[\w/#.-]*|mapping[-_/#.][\w/#.-]*|[\"']mapping[\"']", "", t)
        left += [f"{p.relative_to(TEMPLATES)}: …{t[max(0, m.start() - 40):m.end() + 20]}…" for m in re.finditer(r"(?i)\bmapping\b", t)]
    assert not left, left
    routes = ROOT / "app" / "routes"
    assert "to set up its source profile" in (routes / "sources.py").read_text(encoding="utf-8")
    assert "already in the mapping" not in (routes / "settings.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- Settings → Sources
def test_sources_page_labels_the_import_field_and_its_empty_state_names_the_next_step(client, reset_db):
    reset_db(SAMPLE)
    page = client.get("/settings/sources").text
    assert '<label for="src-import-body">Profile JSON</label>' in page and 'id="src-import-body"' in page   # DS-FORM-1: a visible label
    assert 'aria-label="Profile JSON to import"' not in page
    empty = page[page.index('id="src-empty"'):]
    empty = empty[:empty.index("</section>")]
    assert "Your own source profiles will be listed here" in empty and '<a class="btn" href="/upload">Close a month</a>' in empty   # DS-EMPTY-1


def test_saving_an_unchanged_profile_says_nothing_was_re_read(client, reset_db):
    from test_back_catalogue import TALLY, save_tally
    reset_db(SAMPLE)
    save_tally()
    body = json.dumps(TALLY)
    r = client.post("/settings/sources", data={"form": "Tally", "body": body}, follow_redirects=True)
    assert "No change to the profile “Tally”: nothing to re-read." in r.text


# ---------------------------------------------------------------- the batch review (DS-WORD-4)
def test_the_batch_review_marks_files_that_replace_a_stored_month_and_says_how_to_fix_a_wrong_one(client, reset_db, household24, tmp_path):
    import shutil
    from app import db
    from pipeline import filenames, import_exports
    from test_back_catalogue import make_zip, save_tally, tally_files
    reset_db(household24)
    for f in household24.glob("*.csv"):   # January 2025 already stored, from its own exports
        if filenames.is_export(f.name) and filenames.budget_month(f.name) == "2025-01":
            shutil.copy(f, tmp_path / f.name)
    with db.pool.connection() as conn:
        assert import_exports.run(conn, [str(tmp_path)], out=lambda s: None) == 0
    save_tally()
    r = client.post("/upload", files=[("files", ("tally.zip", make_zip(tally_files(household24, ["2025-01", "2025-02"])), "application/zip"))])
    page = client.get(r.headers["location"]).text
    assert "<strong>2 files replace a month you already have</strong>" in page and "(Jan 2025)" in page
    assert "storing them replaces the files stored for that month." in page   # files counted as files, the month as a month
    rows = re.findall(r'<span class="status-chip ([a-z]*)">([^<]+)</span></td>\s*<td class="mono nowrap">([^<]+)</td>', page)
    assert [(c, w) for c, w, n in rows if "2025-01" in n] == [("warn", "Replaces"), ("warn", "Replaces")]
    assert [(c, w) for c, w, n in rows if "2025-02" in n] == [("ok", "Ready"), ("ok", "Ready")]
    assert [n for _, _, n in rows][:2] == sorted(n for _, _, n in rows if "2025-01" in n)   # before the ready ones
    fix = page[page.index('id="batch-months"'):]
    assert "check the Budget month column below" in fix and 'href="/settings/sources"' in fix.split("</p>")[0] and "upload them again" in fix


# ---------------------------------------------------------------- Settings → Life events and Chart appearance
def test_life_events_say_when_a_hidden_kind_keeps_an_event_off_the_chart(client, reset_db):
    from app import db
    reset_db(SAMPLE)
    upload_files(client, SAMPLE)
    with db.pool.connection() as conn:
        kind = conn.execute("SELECT kind FROM life_events WHERE kind IS NOT NULL AND on_chart ORDER BY month LIMIT 1").fetchone()["kind"]
        n = conn.execute("SELECT count(*) n FROM life_events WHERE kind=%s AND on_chart", (kind,)).fetchone()["n"]
        conn.execute("INSERT INTO appearance (dimension, name, hidden) VALUES ('event_kind', %s, true)", (kind,)); conn.commit()
    page = client.get("/settings").text
    grid = page[page.index('id="events-editor"'):]
    grid = grid[:grid.index("</section>")]
    assert grid.count('<td data-f="on_chart" data-v="yes">yes, hidden kind</td>') == n   # the stored value stays in data-v for the editor
    assert f'id="hidden-kinds" nonce=' in grid and json.dumps([kind]) in grid   # settings.js says it the same way after a row save
    help_ = grid[grid.index('id="events-columns"'):]
    assert "takes all its markers off the chart, for everyone; On the chart switches one event" in help_
    assert 'href="/settings#appearance/event-kind"' in help_ and "Drawn as markers" not in grid   # the long help moved into the details
    assert "hiding a kind takes all its markers off the chart" in page[page.index('id="appearance-how"'):]
    js = (ROOT / "app" / "web" / "static" / "settings.js").read_text(encoding="utf-8")
    assert 'return "yes, hidden kind"' in js


def test_the_not_saved_marker_takes_the_tab_colour_and_is_announced():
    page = _text("settings.html")
    assert "s.className = 'unsaved'" in page and "className = 'muted'; s.textContent = ' · not saved'" not in page
    assert "say.textContent = `${name}: changes not saved yet.`" in page   # through the polite #rowstatus region
    css = (ROOT / "app" / "web" / "static" / "app.css").read_text(encoding="utf-8")
    assert ".subtabs [role=tab] .unsaved { color: inherit; font-weight: var(--weight-strong); }" in css


def test_deleting_an_orphan_says_what_was_deleted_and_what_happens_if_the_name_returns(client, reset_db):
    from app import db
    reset_db(SAMPLE)
    with db.pool.connection() as conn:
        conn.execute("INSERT INTO appearance (dimension, name, color) VALUES ('category', 'Gone Category', 'series-2')"); conn.commit()
    r = client.post("/settings/appearance/delete", data={"dimension": "category", "name": "Gone Category"}, follow_redirects=True)
    assert "Deleted the colour and hide switch kept for Gone Category; if Gone Category comes back, it is automatic again." in r.text


# ---------------------------------------------------------------- the dashboard and the check-in
def test_the_dashboard_marks_its_event_lists_and_the_hidden_note_has_no_second_name():
    page = _text("dashboard.html")
    for draw in ("event-list", "coming-list"):
        assert re.search(rf'<ul class="key row events" data-list="unbounded" data-draw="{draw}"', page), draw   # AGENTS.md, "Long lists"
    note = page[page.index("const hiddenNote = "):page.index("p.append(b); el.append(p);")]
    assert "aria-label" not in note and "b.title = " in note   # WCAG 2.5.3: the visible words are the name


def test_checkin_by_person_calls_the_shared_row_joint_and_breaks_each_person_into_lines():
    from app.web_helpers import templates
    rows = [{"label": "Rosie", "joint": False, "earned": 100, "earned_share": 0.5, "earned_change": 10, "spent": 10, "spent_change": -5, "accounts": [], "decisions": []},
            {"label": "Joint", "joint": True, "earned": 40, "earned_change": 0, "spent": 300, "spent_change": 20, "accounts": [], "decisions": []}]
    page = templates.env.get_template("checkin/_standing.html").render(c={"people": rows}, household={}, month="2026-06", request=None, csrf_field=lambda req: "")
    joint = page[page.index("<dt>Joint</dt>"):]
    assert "Joint income $40, no change since last month.<br>Joint spending $300, +$20 since last month." in joint and "Shared" not in page   # DS-WORD-7
    assert "+$10 since last month.<br>Own envelopes $10" in page
