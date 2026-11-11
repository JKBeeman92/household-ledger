"""Roles live in rules, not in code (issue #162): no chart depends on a category, canonical item or budget group name typed in the
code. The default names may appear only in build_ledger.RULES, the schema, tests, and on lines marked as a mirror of those defaults
("role default"), a display label ("label, not a role") or a value of the internal file format ("format value, not a role")."""
from __future__ import annotations
import csv, json, re, shutil
from pathlib import Path

import pytest

from conftest import GOLDEN
from test_pipeline_golden import EXPECTED
from test_rules_defaults import ds

ROOT = Path(__file__).resolve().parent.parent
ROLE_KEYS = ("paycheck_items", "recurring_categories", "plan_keep_categories", "income_groups", "debt_groups",
             "goal_housing_categories", "goal_giving_categories")
MARKERS = ("role default", "label, not a role", "format value, not a role")


def rules_defaults() -> dict:
    """build_ledger.RULES, read from the source (the script runs its pipeline on import)."""
    src = (ROOT / "pipeline" / "build_ledger.py").read_text()
    block = src[src.index("RULES = {"):src.index("\n}\n", src.index("RULES = {")) + 2]
    return {k: json.loads(re.search(rf'"{k}":\s*(\[[^\]]*\])', block).group(1)) for k in ROLE_KEYS}


def code_files():
    yield from (ROOT / "pipeline").glob("*.py")
    yield from (p for p in (ROOT / "app").rglob("*.py"))
    yield from (ROOT / "app" / "web").rglob("*.html")
    yield from (ROOT / "app" / "web").rglob("*.js")


def test_no_role_name_is_typed_in_the_code():
    names = sorted({n for v in rules_defaults().values() for n in v})
    rx = re.compile(r"""(["'])(""" + "|".join(map(re.escape, names)) + r""")\1""")
    hits = []
    for p in code_files():
        text = p.read_text()
        if p.name == "build_ledger.py":   # the RULES block is where the defaults live
            start = text.index("RULES = {"); text = text[:start] + text[text.index("\n}\n", start):]
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line) and not any(m in line for m in MARKERS):
                hits.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    assert not hits, "role names typed in the code; read the rule instead (README, \"Roles\"):\n" + "\n".join(hits)


def test_every_mirror_of_the_defaults_matches_build_ledger():
    from app.routes.settings import ROLE_DEFAULTS
    from app import schema
    want = rules_defaults()
    assert ROLE_DEFAULTS == {k: want[k] for k in ROLE_DEFAULTS}
    props = schema.load("rules")["properties"]
    for k in ROLE_KEYS:
        assert f"Empty (null) means {json.dumps(want[k])}." in props[k]["description"], k


def renamed(tmp_path: Path, rename: dict[str, str], rules: dict) -> Path:
    """The golden household with mapping names renamed (CanonicalItem, Category or Group in the exports) and the roles saying so."""
    base = json.loads((GOLDEN / "rules.json").read_text())
    wd = tmp_path
    (wd / "txns").mkdir(parents=True); (wd / "summaries").mkdir()
    for f in sorted((GOLDEN / "exports").glob("*.csv")):
        rows = list(csv.reader(open(f, newline="", encoding="utf-8-sig")))
        rows = [rows[0]] + [[rename.get(c, c) if i == 0 else c for i, c in enumerate(r)] for r in rows[1:]]   # column 0 is Group
        with open(wd / ("txns" if "Transactions" in f.name else "summaries") / f.name, "w", newline="") as out:
            csv.writer(out, lineterminator="\n").writerows(rows)
    with open(GOLDEN / "item_mapping.csv", newline="") as f:
        m = list(csv.DictReader(f))
    for r in m:
        r["CanonicalItem"] = rename.get(r["CanonicalItem"], r["CanonicalItem"]); r["Category"] = rename.get(r["Category"], r["Category"])
    with open(wd / "item_mapping.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(m[0]), lineterminator="\n"); w.writeheader(); w.writerows(m)
    for n in ("cpi_u.csv", "events.json"):
        shutil.copy(GOLDEN / n, wd / n)
    (wd / "rules.json").write_text(json.dumps({**base, **rules}))
    from test_pipeline_golden import run_scripts
    run_scripts(wd)
    return wd


def test_renaming_paycheck_with_its_rule_keeps_the_employer_chart(tmp_path):
    base = json.loads((GOLDEN / "rules.json").read_text())
    inc = {p: ["Salary" if i == "Paycheck" else i for i in items] for p, items in (base.get("income_by_person") or {}).items()}
    wd = renamed(tmp_path / "salary", {"Paycheck": "Salary"}, {"paycheck_items": ["Salary"], "income_by_person": inc})
    want = json.loads((EXPECTED / "dash_data" / "employer.json").read_text())
    assert want and ds(wd, "employer") == want
    assert ds(wd, "people") == json.loads((EXPECTED / "dash_data" / "people.json").read_text())
    broken = renamed(tmp_path / "forgot", {"Paycheck": "Salary"}, {"income_by_person": inc})   # renamed, rule not changed
    assert ds(broken, "employer") == []


def test_renaming_recurring_categories_and_groups_with_their_rules(tmp_path):
    rename = {"Subscriptions": "Streaming", "Income": "Earnings", "Debt": "Loans"}
    wd = renamed(tmp_path, rename, {"recurring_categories": ["Streaming", "Utilities", "Insurance"], "income_groups": ["Earnings"], "debt_groups": ["Loans"]})
    assert ds(wd, "recurring") == json.loads((EXPECTED / "dash_data" / "recurring.json").read_text())
    want = {r["kind"]: r["value"] for r in json.loads((EXPECTED / "dash_data" / "exclusions.json").read_text())}
    got = {r["kind"]: r["value"] for r in ds(wd, "exclusions")}
    assert got == want
    assert [r["Amount"] for r in csv.DictReader(open(wd / "ledger.csv"))] == [r["Amount"] for r in csv.DictReader(open(EXPECTED / "ledger.csv"))]
    led = list(csv.DictReader(open(wd / "ledger.csv"))); exp = list(csv.DictReader(open(EXPECTED / "ledger.csv")))
    assert [(r["IsTransfer"], r["IsPlug"], r["InTotals"]) for r in led] == [(r["IsTransfer"], r["IsPlug"], r["InTotals"]) for r in exp]


def test_mapping_draft_and_source_proposals_follow_the_group_roles():
    from app import mapping_hints as mh
    from pipeline import sources as src
    assert mh.flow_for("Wages", "Earnings", {"income_groups": ["Earnings"]}) == "income"
    assert mh.flow_for("Wages", "Earnings") == "expense"
    assert mh.flow_for("Car", "Loans", {"debt_groups": ["Loans"]}) == "debt"
    data = b"Group,Item,Planned,Spent\nEarnings,Wages,100,100\nFood,Groceries,50,-40\n"
    assert src.propose("budget_items", data, ["Earnings"])["income_groups"] == ["Earnings"]
    assert src.propose("budget_items", data)["income_groups"] == src.DEFAULT_INCOME_GROUPS


def test_review_counts_paychecks_by_the_rule(client, reset_db):
    from conftest import SAMPLE
    from app import db, review
    reset_db(SAMPLE)
    client.post("/upload", files=[("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted(SAMPLE.glob("*EveryDollar*.csv"))])
    with db.pool.connection() as conn:
        month = conn.execute("SELECT max(budget_month) m FROM source_files").fetchone()["m"]
        n = review.build_context(conn, month)["paycheck_envelopes_with_money"]
        assert n > 0
        rules = db.get_setting(conn, "rules", {}) or {}
        db.put_setting(conn, "rules", {**rules, "paycheck_items": []}); conn.commit()
        assert review.build_context(conn, month)["paycheck_envelopes_with_money"] == 0
        db.put_setting(conn, "rules", rules); conn.commit()


def test_roles_form_saves_names_and_blank_means_default(client, reset_db):
    from app import db
    page = client.get("/settings").text
    assert 'action="/settings/rules/roles"' in page and "Items that are paychecks" in page and 'placeholder="Paycheck"' in page
    with db.pool.connection() as conn:   # an explicit [] ("none") set in the JSON editor, and a name with a comma
        db.put_setting(conn, "rules", {**(db.get_setting(conn, "rules", {}) or {}), "debt_groups": [], "recurring_categories": ["Bills, monthly"]}); conn.commit()
    page = client.get("/settings").text
    assert "(none: set to [] in the JSON)" in page and ">Bills, monthly</textarea>" in page
    r = client.post("/settings/rules/roles", data={"paycheck_items": "Salary\nContract Income", "paycheck_items__was": "",
                                                   "debt_groups": "", "debt_groups__was": "",
                                                   "recurring_categories": "Bills, monthly", "recurring_categories__was": "Bills, monthly",
                                                   "income_groups": " ", "income_groups__was": ""})
    assert r.status_code == 303
    with db.pool.connection() as conn:
        rules = db.get_setting(conn, "rules", {})
    assert rules["paycheck_items"] == ["Salary", "Contract Income"] and rules["debt_groups"] == [] and "income_groups" not in rules
    assert rules["recurring_categories"] == ["Bills, monthly"]
    client.post("/settings/rules/roles", data={"recurring_categories": "", "recurring_categories__was": "Bills, monthly"})   # cleared: back to the default
    with db.pool.connection() as conn:
        assert "recurring_categories" not in db.get_setting(conn, "rules", {})
