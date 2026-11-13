"""The decisions log (#155): each target kind checked against the right figure (actual for caps, payments and saves; next month's
planned for a plan change), pending (never dropped) until the month is built, a spanning decision checked each month and closed at
`until`, the soft cap, catch-up, renames, settings.zip, the review and MCP, and the Suggested plan as a dataset."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path

import pytest

from conftest import GOLDEN, SAMPLE, upload_files

_spec = importlib.util.spec_from_file_location("decisions_rules", Path(__file__).resolve().parent.parent / "pipeline" / "decisions.py")
rules = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(rules)


def rows(**by_item):   # one budget row per item: (planned, actual), in category Food
    return [{"m": "2026-06", "canonical": k.replace("_", " "), "category": "Food", "flow": "expense", "planned": p, "actual": a} for k, (p, a) in by_item.items()]


# ---- the rules, pure ----

@pytest.mark.parametrize("kind,target,month,want", [
    ("spend_cap", {"item": "Eating Out", "max": 400}, rows(Eating_Out=(400, 372)), ("kept", 372)),
    ("spend_cap", {"item": "Eating Out", "max": 400}, rows(Eating_Out=(400, 420)), ("partly", 420)),     # within 10%
    ("spend_cap", {"item": "Eating Out", "max": 400}, rows(Eating_Out=(400, 480)), ("dropped", 480)),
    ("spend_cap", {"category": "Food", "max": 900}, rows(Eating_Out=(400, 372), Groceries=(700, 500)), ("kept", 872)),
    ("save", {"item": "Long Term Savings", "min": 300}, rows(Long_Term_Savings=(300, 300)), ("kept", 300)),
    ("save", {"item": "Long Term Savings", "min": 300}, rows(Long_Term_Savings=(300, 180)), ("partly", 180)),   # at least half
    ("save", {"item": "Long Term Savings", "min": 300}, rows(Long_Term_Savings=(300, 100)), ("dropped", 100)),
    ("extra_payment", {"item": "Car Loan", "extra": 200, "base": 350}, rows(Car_Loan=(350, 550)), ("kept", 550)),
    ("extra_payment", {"item": "Car Loan", "extra": 200, "base": 350}, rows(Car_Loan=(350, 450)), ("partly", 450)),
    ("extra_payment", {"item": "Car Loan", "extra": 200, "base": 350}, rows(Car_Loan=(350, 350)), ("dropped", 350)),
    ("plan_change", {"item": "Groceries", "planned": 650, "from": 720}, rows(Groceries=(650, 900)), ("kept", 650)),   # the plan, not what was spent
    ("plan_change", {"item": "Groceries", "planned": 650, "from": 720}, rows(Groceries=(680, 600)), ("partly", 680)),   # moved more than halfway
    ("plan_change", {"item": "Groceries", "planned": 650, "from": 720}, rows(Groceries=(720, 600)), ("dropped", 720)),
    ("other", None, rows(Groceries=(1, 1)), ("unknown", None)),
    ("spend_cap", {"item": "Takeaway", "max": 100}, rows(Groceries=(1, 1)), ("unknown", None)),        # names nothing in the budget
    ("spend_cap", {"item": "Eating Out", "max": 400}, None, ("pending", None)),                       # the month is not built
    ("plan_change", {"item": "Groceries", "planned": 650, "from": 720}, None, ("pending", None)),
])
def test_each_target_kind_is_checked_against_the_right_figure(kind, target, month, want):
    assert rules.auto(kind, target, month) == want


def test_months_in_force_span_and_payoff():
    assert rules.months_in_force({"made_in": "2026-05"}, "2026-12") == ["2026-06"]
    assert rules.months_in_force({"made_in": "2026-05", "until": "2026-08"}, "2026-12") == ["2026-06", "2026-07", "2026-08"]
    assert rules.months_in_force({"made_in": "2026-05", "until": "2026-08"}, "2026-07") == ["2026-06", "2026-07"]   # not yet loaded
    assert rules.months_in_force({"made_in": "2026-11", "until": "2027-02"}, "2027-02") == ["2026-12", "2027-01", "2027-02"]
    payoff = {"made_in": "2026-05", "target": {"until_payoff": True}, "status": "open"}
    assert rules.months_in_force(payoff, "2026-09") == ["2026-06", "2026-07", "2026-08", "2026-09"]
    assert rules.months_in_force({**payoff, "status": "done"}, "2026-09") == ["2026-06"]


def test_judge_keeps_a_confirmed_check_and_keep_rate_counts_partly_half():
    ds = [{"id": 1, "made_in": "2026-04", "text": "cap", "kind": "spend_cap", "target": {"item": "Eating Out", "max": 400}, "until": "2026-06"}]
    budget = rows(Eating_Out=(400, 480)) + [dict(r, m="2026-05") for r in rows(Eating_Out=(400, 372))]
    confirmed = {(1, "2026-05"): {"verdict": "partly", "auto_verdict": "partly", "auto_figure": 410.0}}   # what it said when confirmed
    out = rules.judge(ds, confirmed, budget, {"2026-05", "2026-06"}, "2026-06")
    assert [(r["m"], r["auto_verdict"], r["auto_figure"], r["verdict"]) for r in out] == [("2026-05", "partly", 410.0, "partly"), ("2026-06", "dropped", 480, None)]
    rate = rules.keep_rate(out + [dict(out[1], verdict="kept")], "2026-06")
    assert rate["checked"] == 2 and rate["kept"] == 1.5 and rate["rate"] == 0.75


def test_plan_suggestions_match_the_last_month_tab_formula():
    budget = [{"m": f"2026-{i:02d}", "category": "Food", "flow": "expense", "planned": 700, "actual": 600 + i} for i in range(1, 8)] + \
             [{"m": "2026-07", "category": "Housing", "flow": "expense", "planned": 1500, "actual": 1500}]
    s = {r["category"]: r for r in rules.plan_suggestions(budget, ["Housing"])}
    avg = sum(600 + i for i in range(1, 7)) / 6   # the six months before July, divided by the months present
    assert s["Food"]["avg12"] == round(avg, 2) and s["Food"]["suggest"] == 610 and s["Food"]["m"] == "2026-07"   # 603.5 rounds up to 610
    assert s["Housing"]["suggest"] == 1500   # a keep category keeps its plan


# ---- through the app ----

@pytest.fixture
def loaded(client, reset_db):
    upload_files(client, GOLDEN / "exports")   # 2025-01 … 2025-04


def _db():
    from app import db
    return db.pool.connection()


def test_add_check_carry_and_close(client, loaded):
    from app import checkin, decisions
    r = client.post("/checkin/2025-03/decisions", data={"text": "Groceries under $50", "kind": "spend_cap", "what": "item:Groceries", "amount": "50"})
    assert r.status_code == 303 and r.headers["location"] == "/checkin/2025-03#step-next"
    with _db() as conn:
        (d,) = decisions.in_force(conn, "2025-04")
        assert d["auto_verdict"] in ("kept", "partly", "dropped") and d["auto_figure"] is not None and d["last_month"]
        steps = {s["key"]: s["status"] for s in checkin.overview(conn, "2025-04")["steps"]}
        assert steps["decisions"] == "todo"
    client.post(f"/checkin/2025-04/decisions/{d['id']}/carry")
    with _db() as conn:
        assert conn.execute("SELECT until FROM decisions WHERE id=%s", (d["id"],)).fetchone()["until"] == "2025-05"
    client.post(f"/checkin/2025-04/decisions/{d['id']}/verdict", data={"verdict": "partly"})
    with _db() as conn:
        c = conn.execute("SELECT verdict, auto_verdict, auto_figure FROM decision_checks WHERE decision_id=%s", (d["id"],)).fetchone()
        assert c["verdict"] == "partly" and c["auto_verdict"] == d["auto_verdict"] and float(c["auto_figure"]) == d["auto_figure"]
        assert conn.execute("SELECT status FROM decisions WHERE id=%s", (d["id"],)).fetchone()["status"] == "open"   # runs to May now
        assert {s["key"]: s["status"] for s in checkin.overview(conn, "2025-04")["steps"]}["decisions"] == "done"


def test_a_month_not_built_is_pending_never_dropped(client, loaded):
    from app import decisions
    with _db() as conn:
        i, _ = decisions.add(conn, "2025-04", "Groceries planned at $400", "plan_change", {"item": "Groceries", "planned": 400})
        conn.commit()
        (d,) = decisions.in_force(conn, "2025-05")
        assert d["auto_verdict"] == "pending" and decisions.auto_text(d).startswith("Waiting for the month's files")
        assert conn.execute("SELECT target FROM decisions WHERE id=%s", (i,)).fetchone()["target"]["from"] is not None   # the plan it moves from


def test_targets_are_checked_and_the_soft_cap_says_so(client, loaded):
    from app import decisions
    with _db() as conn:
        with pytest.raises(ValueError, match="no budget item"):
            decisions.add(conn, "2025-04", "x", "spend_cap", {"item": "Second Breakfast", "max": 1})
        with pytest.raises(ValueError, match="most to spend"):
            decisions.add(conn, "2025-04", "x", "spend_cap", {"item": "Groceries"})
        for k in range(3):
            decisions.add(conn, "2025-04", f"decision {k}")
        conn.commit()
    r = client.post("/checkin/2025-04/decisions", data={"text": "a fourth"})
    page = client.get(r.headers["location"]).text
    assert "three is usually enough" in page


def test_catch_up_checks_each_missed_month_and_next_month_is_only_on_the_latest(client, loaded):
    from app import checkin, decisions
    with _db() as conn:
        decisions.add(conn, "2025-01", "Groceries under $500, Feb and Mar", "spend_cap", {"item": "Groceries", "max": 500}, until="2025-03"); conn.commit()
        for m in ("2025-02", "2025-03"):
            keys = [s["key"] for s in checkin.overview(conn, m)["steps"]]
            assert "decisions" in keys and "next" not in keys   # an earlier month: the short form
        assert "next" in [s["key"] for s in checkin.overview(conn, "2025-04")["steps"]]
        assert "decisions" not in [s["key"] for s in checkin.overview(conn, "2025-04")["steps"]]   # its span ended in March


def test_no_changes_completes_the_next_month_step(client, loaded):
    from app import checkin
    client.post("/checkin/2025-04/steps/next/ack")
    with _db() as conn:
        assert {s["key"]: s for s in checkin.overview(conn, "2025-04")["steps"]}["next"]["status"] == "done"


def test_renames_reach_decision_targets(client, loaded):
    from app import decisions, renames
    with _db() as conn:
        decisions.add(conn, "2025-04", "Groceries under $400", "spend_cap", {"item": "Groceries", "max": 400})
        renames.rename_canonical(conn, "Groceries", "Provisions"); conn.commit()
        assert conn.execute("SELECT target->>'item' AS i FROM decisions").fetchone()["i"] == "Provisions"
        decisions.rewrite_targets(conn, "account", "Lembas Student Loan", "Lembas Loan")   # what an account rename on Settings calls
        conn.rollback()


def test_settings_zip_and_seed_carry_decisions_and_their_checks(client, loaded, reset_db, tmp_path):
    from app import decisions
    from app.settings_export import write_dir
    with _db() as conn:
        i, _ = decisions.add(conn, "2025-03", "Groceries under $50", "spend_cap", {"item": "Groceries", "max": 50}, note="private")
        decisions.confirm(conn, i, "2025-04", "dropped"); conn.commit()
        write_dir(conn, tmp_path)
    items = json.loads((tmp_path / "decisions.json").read_text())
    assert items[0]["text"] == "Groceries under $50" and items[0]["checks"][0]["verdict"] == "dropped" and items[0]["status"] == "dropped"
    reset_db(tmp_path)
    with _db() as conn:
        d = conn.execute("SELECT id, text, note, status FROM decisions").fetchone()
        c = conn.execute("SELECT verdict, auto_verdict FROM decision_checks WHERE decision_id=%s", (d["id"],)).fetchone()
        assert d["text"] == "Groceries under $50" and d["note"] == "private" and d["status"] == "dropped" and c["verdict"] == "dropped"


def test_review_and_datasets_carry_decisions_but_never_the_note(client, loaded):
    from app import decisions, mcp_endpoint, review
    with _db() as conn:
        decisions.add(conn, "2025-03", "Groceries under $50", "spend_cap", {"item": "Groceries", "max": 50}, note="secret note"); conn.commit()
    client.post("/rebuild")
    data = client.get("/api/datasets").json()
    (row,) = data["decisions"]
    assert row["m"] == "2025-04" and row["auto_verdict"] in ("kept", "partly", "dropped") and "note" not in row
    assert data["decision_summary"][0]["in_force"] == 1 and data["plan_suggestions"]
    with _db() as conn:
        ctx = review.build_context(conn, "2025-04")
    assert ctx["continuity"]["decisions"][0]["text"] == "Groceries under $50" and "secret note" not in json.dumps(ctx)
    assert "suggested change for this month was made" not in ctx["continuity"]["note"]
    assert {"decisions", "decision_summary", "plan_suggestions"} <= set(mcp_endpoint.DATASETS)


def test_cli_adds_checks_and_lists(client, loaded, capsys):
    from app import admin
    assert admin.main(["decisions", "add", "2025-03", "Groceries under $50", "--kind", "spend_cap", "--item", "Groceries", "--amount", "50"]) == 0
    assert admin.main(["decisions", "list", "2025-04"]) == 0
    out = capsys.readouterr().out
    assert "Groceries under $50" in out and "app: " in out
    did = int(out.strip().splitlines()[-1].split("\t")[0])
    assert admin.main(["decisions", "check", str(did), "2025-04", "kept"]) == 0
    assert admin.main(["decisions", "add", "2025-03", "x", "--kind", "spend_cap", "--item", "Nope", "--amount", "1"]) == 2


def test_the_check_in_and_last_month_tab_show_them(client, loaded):
    from app import decisions
    with _db() as conn:
        decisions.add(conn, "2025-03", "Groceries under $50", "spend_cap", {"item": "Groceries", "max": 50}); conn.commit()
    page = client.get("/checkin/2025-04").text
    step = page[page.find('id="step-decisions"'):page.find('id="step-goals"')]
    assert "Groceries under $50" in step and "Groceries was $" in step and "Carry it on next month" in step
    nxt = page[page.find('id="step-next"'):]
    assert "Add the decision" in nxt and "No changes this month" in nxt
    assert 'data-draw="lm-decisions"' in client.get("/").text


def test_sample_household_has_three_months_of_decisions(client, reset_db):
    from pipeline import import_exports
    reset_db(SAMPLE)
    assert import_exports.main([str(SAMPLE)]) == 0
    data = client.get("/api/datasets").json()
    made = {r["made_in"] for r in data["decisions"]}
    assert made == {"2026-03", "2026-04", "2026-05"}
    june = [r for r in data["decisions"] if r["m"] == "2026-06"]
    assert len(june) == 2 and all(r["verdict"] is None for r in june)   # waiting for the June check-in
    assert data["decision_summary"][0]["checked"] == 3


def test_no_step_template_reads_a_context_key_named_like_a_dict_method():
    """c.items, c.keys, c.values in a step's partial reach the dict's own method, not the context (a mistake made twice in 0.7.0)."""
    import re
    root = Path(__file__).resolve().parent.parent / "app" / "web" / "templates" / "checkin"
    bad = [f"{p.name}: {m.group(0)}" for p in root.glob("*.html") for m in re.finditer(r"\bc\.(items|keys|values|get|update|pop)\b(?!\()", p.read_text())]
    assert not bad, bad


def test_no_template_reads_a_context_dict_key_named_like_a_dict_method():
    """The same trap outside the check-in: settings.html once read tax_cfg.items (#153). Any `<name>.items|` or `.keys|` in a template
    is the dict's method, not a key."""
    import re
    root = Path(__file__).resolve().parent.parent / "app" / "web" / "templates"
    bad = [f"{p.name}: {m.group(0)}" for p in root.rglob("*.html") for m in re.finditer(r"\b\w+\.(items|keys|values)\s*\|", p.read_text())]
    assert not bad, bad
