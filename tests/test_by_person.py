"""Each person's share at the check-in (#156): the by_person dataset, standalone on the golden folder; it matches the earner and payer
charts (the yearly `people` dataset) month by month and is empty with fewer than two people; the review, the check-in's Where we
stand, the Last month tab, the CLI and datasets.zip carry it (MCP: tests/test_mcp.py)."""
from __future__ import annotations
import collections, io, json, shutil, subprocess, sys, zipfile
from pathlib import Path

import pytest

from conftest import GOLDEN, ROOT, upload_files

SCRIPTS = ["build_ledger.py", "aggregate.py", "make_datasets.py", "make_budget_items.py"]
REFS = ["item_mapping.csv", "cpi_u.csv", "rules.json", "events.json", "accounts.csv", "balances.csv"]


def build(wd: Path, **rules) -> dict[str, list]:
    """Run the four scripts as standalone scripts on a copy of the golden folder, with `rules` laid over its rules.json."""
    (wd / "txns").mkdir(parents=True); (wd / "summaries").mkdir(); (wd / "dash_data").mkdir()
    for f in sorted((GOLDEN / "exports").glob("*.csv")):
        shutil.copy(f, wd / ("txns" if "Transactions" in f.name else "summaries") / f.name)
    for n in REFS:
        shutil.copy(GOLDEN / n, wd / n)
    (wd / "rules.json").write_text(json.dumps({**json.loads((GOLDEN / "rules.json").read_text()), **rules}))
    for s in SCRIPTS:
        p = subprocess.run([sys.executable, "-I", str(ROOT / "pipeline" / s)], cwd=wd, capture_output=True, text=True, timeout=120)
        assert p.returncode == 0, f"{s} failed:\n{p.stdout}{p.stderr}"
    return {n: json.loads((wd / "dash_data" / f"{n}.json").read_text()) for n in ("by_person", "people")}


# ---- the pipeline, standalone ----

def test_by_person_sums_to_the_earner_and_payer_charts(tmp_path):
    ds = build(tmp_path)
    rows, people = ds["by_person"], ds["people"]
    assert {r["who"] for r in rows} == {"Rosie", "Sam", "Household"}
    year = collections.defaultdict(float)
    for r in rows:
        year[("earner", r["m"][:4], r["who"])] += r["earned"]; year[("payer", r["m"][:4], r["who"])] += r["spent"]
    for p in people:   # the charts' every bar is the sum of the block's months
        assert round(year[(p["role"], p["y"], p["who"])], 2) == p["amount"], p
    for m in {r["m"] for r in rows}:
        mine = [r for r in rows if r["m"] == m]
        assert [r["who"] for r in mine] == ["Rosie", "Sam", "Household"]   # the People rule's order, joint last
        assert abs(sum(r["earned_share"] for r in mine) - 1) < 0.001


def test_a_person_with_no_rule_rows_still_shows_and_the_first_month_has_no_change(tmp_path):
    rows = build(tmp_path)["by_person"]
    sam = [r for r in rows if r["who"] == "Sam"]
    assert sam and all(r["spent"] == 0 for r in sam)   # no payer rule for Sam in the golden rules: a zero, not a missing row
    first = min(r["m"] for r in rows)
    assert all(r["earned_change"] is None and r["spent_change"] is None for r in rows if r["m"] == first)
    feb = {r["who"]: r for r in rows if r["m"] == "2025-02"}
    jan = {r["who"]: r for r in rows if r["m"] == "2025-01"}
    assert feb["Rosie"]["spent_change"] == round(feb["Rosie"]["spent"] - jan["Rosie"]["spent"], 2)


@pytest.mark.parametrize("people", [[], ["Rosie"], ["Rosie", " Rosie "], ["Rosie", "Household"]])
def test_fewer_than_two_people_is_an_empty_dataset(tmp_path, people):
    ds = build(tmp_path, people=people)
    assert ds["by_person"] == [] and ds["people"]   # the charts are untouched


# ---- the app ----

@pytest.fixture
def loaded(client, reset_db):
    upload_files(client, GOLDEN / "exports")   # 2025-01 … 2025-04, people Rosie and Sam


def _db():
    from app import db
    return db.pool.connection()


def _one_person(client):
    with _db() as conn:
        conn.execute("""UPDATE settings SET value = jsonb_set(value, '{people}', '["Rosie"]') WHERE key='rules'"""); conn.commit()
    client.post("/rebuild")


def test_review_gets_per_person_totals_only_with_two_people(client, loaded):
    from app import review
    with _db() as conn:
        ctx = review.build_context(conn, "2025-04")
    bp = ctx["by_person"]
    assert [r["who"] for r in bp["people"]] == ["Rosie", "Sam", "Household"] and "over or under" in bp["note"]
    assert set(bp["people"][0]) == {"who", "earned", "earned_share", "earned_change", "spent", "spent_change"}   # totals only, no accounts
    _one_person(client)
    with _db() as conn:
        assert "by_person" not in review.build_context(conn, "2025-04")


def test_checkin_where_we_stand_shows_each_person_and_joint(client, loaded):
    page = client.get("/checkin/2025-04").text
    assert 'id="standing-people"' in page
    sec = page[page.index('id="standing-people"'):]
    sec = sec[:sec.index("</dl>")]
    assert "<dt>Rosie</dt>" in sec and "<dt>Sam</dt>" in sec and "<dt>Joint</dt>" in sec
    assert "Lembas Student Loan" in sec.split("<dt>Sam</dt>")[0]   # Rosie's account, under Rosie
    assert "Green Hills Savings" in sec.split("<dt>Joint</dt>")[1]   # an account that names no one, under Joint
    assert " over " not in sec and " under " not in sec   # figures, never a verdict
    _one_person(client)
    assert 'id="standing-people"' not in client.get("/checkin/2025-04").text


def test_dashboard_block_is_drawn_from_the_dataset_only(client, loaded):
    page = client.get("/").text
    assert 'id="lm-people-card" hidden' in page   # shown by the page only when by_person has rows
    assert 'data-draw="lm-people-rows" data-source="by_person"' in page
    assert "dash.data('by_person')" in page and "/api/by_person" not in page
    assert client.get("/api/datasets").json()["by_person"]


def test_cli_prints_the_month_and_says_why_when_empty(client, loaded, capsys):
    from app import admin
    assert admin.main(["by-person", "2025-04"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("month\tperson\tearned") and [l.split("\t")[1] for l in out[1:]] == ["Rosie", "Sam", "Joint"]
    assert admin.main(["by-person", "April"]) == 2
    _one_person(client)
    assert admin.main(["by-person"]) == 1 and "fewer than two people" in capsys.readouterr().err


def test_datasets_zip_carries_by_person(client, loaded):
    z = zipfile.ZipFile(io.BytesIO(client.get("/export/datasets.zip").content))
    name = next(n for n in z.namelist() if n.startswith("by_person"))
    assert json.loads(z.read(name))


def test_accounts_and_decisions_naming_someone_else_sit_under_joint():
    """A name the People rule lacks (a removed person), or one typed with other case or spacing, never drops out of the block."""
    from app.checkin.step_standing import by_person
    m = "2026-06"
    row = lambda who: {"m": m, "who": who, "earned": 0, "earned_share": 0, "earned_change": 0, "spent": 0, "spent_change": 0}
    ds = {"by_person": [row("Rosie"), row("Sam"), row("Household")],
          "balances": [{"m": m, "account": a, "person": p} for a, p in [("A", " rosie "), ("B", "Gandalf"), ("C", None), ("D", "Sam")]],
          "decisions": [{"m": m, "text": t, "person": p} for t, p in [("Rosie's", "ROSIE"), ("Old", "Gandalf"), ("Nobody's", None)]]}
    out = {r["label"]: r for r in by_person(ds, m)}
    assert out["Rosie"]["accounts"] == ["A"] and out["Rosie"]["decisions"] == ["Rosie's"]
    assert out["Sam"]["accounts"] == ["D"] and out["Sam"]["decisions"] == []
    assert out["Joint"]["accounts"] == ["B", "C"] and out["Joint"]["decisions"] == ["Old"]   # a decision naming no one stays off


def test_checkin_says_no_change_for_a_change_that_rounds_to_nothing():
    """The check-in and the Last month table read a zero change alike: "no change", never "$0" here and "+$0" there."""
    from app.web_helpers import templates
    r = {"label": "Rosie", "joint": False, "earned": 100, "earned_share": 0.5, "earned_change": 0.2, "spent": 10, "spent_change": -5,
         "accounts": [], "decisions": []}
    page = templates.env.get_template("checkin/_standing.html").render(c={"people": [r]}, household={}, month="2026-06", request=None,
                                                                         csrf_field=lambda req: "")
    dd = page[page.index("<dt>Rosie</dt>"):]
    assert "Earned $100 (50% of the month's income), no change since last month" in dd and "−$5 since last month" in dd


def test_dashboard_reads_zero_change_and_absent_joint_income_alike():
    page = (ROOT / "app" / "web" / "templates" / "dashboard.html").read_text()
    assert "'no change'" in page and 'data-draw="lm-people-month"' in page
