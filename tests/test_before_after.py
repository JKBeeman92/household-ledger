"""Before and after a life event, and the household's new normal (issue #159): figures that match hand sums, a short side that says
so, categories new since the event; a life event marked as the new normal moves the look ahead's pace (#148), the emergency fund's
outgo (#150) and the suggested plan's average (#162), and each says so; the mark is validated and round-tripped on every way in and
out, reaches the review when the month is within six months after it, and the dataset is served over MCP."""
from __future__ import annotations
import json, shutil

import pytest

from conftest import SAMPLE, upload_files
from test_rules_defaults import build, ds
from test_pipeline_golden import run_scripts
from app import admin, db, mcp_endpoint, review
from pipeline import before_after as ba, decisions as dec, look_ahead as la

# eight budget months, a move in 2025-05: before it 1000 in and 600 out (Food 400, Rent 200), after it 1200 in and 900 out (Rent 500,
# Food 300, Childcare 100), with the debt line apart; in real dollars every figure is nine tenths
MONTHS = [f"2025-{m:02d}" for m in range(1, 9)]
def _mon(m):
    after = m >= "2025-05"
    inc, sp, debt = (1200.0, 900.0, 50.0) if after else (1000.0, 600.0, 50.0)
    return {"m": m, "income": inc, "spend": sp, "debt": debt, "net": inc - sp - debt,
            "income_real": inc * .9, "spend_real": sp * .9, "debt_real": debt * .9, "net_real": (inc - sp - debt) * .9}
MONTHLY = [_mon(m) for m in MONTHS]
def _cats(m):
    c = {"Rent": 500.0, "Food": 300.0, "Childcare": 100.0} if m >= "2025-05" else {"Rent": 200.0, "Food": 400.0}
    return [{"m": m, "category": k, "amount": v, "amount_real": v * .9} for k, v in {**c, "Debt": 50.0}.items()]
CAT_MONTH = [r for m in MONTHS for r in _cats(m)]
MOVE = {"m": "2025-05", "label": "Moved to Bywater", "kind": "move"}


def test_figures_match_hand_sums_and_a_short_side_says_so():
    r = ba.compare(MOVE, 3, MONTHLY, CAT_MONTH, set_apart={"Debt"})
    assert (r["before"]["from"], r["before"]["to"], r["before"]["months"]) == ("2025-02", "2025-04", 3)
    assert (r["after"]["from"], r["after"]["to"], r["after"]["months"]) == ("2025-05", "2025-07", 3) and r["short"] is False
    assert (r["before"]["income"], r["before"]["spend"], r["before"]["net"], r["after"]["spend"], r["after"]["net"]) == (1000, 600, 350, 900, 250)
    assert r["change"]["spend"] == 300 and r["change"]["spend_pct"] == 0.5 and r["change"]["net"] == -100
    assert r["before"]["savings_rate"] == 0.35 and r["after"]["savings_rate"] == round(250 / 1200, 4) and r["change"]["savings_rate"] == round(250 / 1200 - .35, 4)
    assert r["before"]["spend_real"] == 540 and r["change"]["spend_real"] == 270   # the CPI toggle: in one month's prices
    assert [m["category"] for m in r["movers"]["by_dollars"]] == ["Rent", "Childcare", "Food"]   # living spend; the debt line apart
    assert r["movers"]["by_dollars"][0] == {"category": "Rent", "before": 200, "after": 500, "change": 300, "pct": 1.5, "before_real": 180, "after_real": 450,
                                            "change_real": 270, "pct_real": 1.5, "new": False}
    assert [m["category"] for m in r["movers"]["by_pct"]] == ["Rent", "Food"]   # Childcare had nothing before: no percent of nothing
    assert r["new_categories"] == [{"category": "Childcare", "after": 100, "after_real": 90}]
    six = ba.compare(MOVE, 6, MONTHLY, CAT_MONTH)
    assert six["short"] and (six["before"]["months"], six["after"]["months"]) == (4, 4) and six["before"]["from"] == "2024-11"
    assert ba.compare(MOVE, 6, MONTHLY, CAT_MONTH, through="2025-06")["after"]["months"] == 2   # as of a month: later months are not seen
    assert ba.compare({"m": "2025-01", "label": "x"}, 3, MONTHLY, CAT_MONTH) is None   # nothing loaded before: no row
    rows = ba.before_after([MOVE, {"m": "2026-01", "label": "Still to come"}], MONTHLY, CAT_MONTH)
    assert [(r["label"], r["n"]) for r in rows] == [("Moved to Bywater", 3), ("Moved to Bywater", 6), ("Moved to Bywater", 12)]


def test_the_new_normal_cuts_a_trailing_window_once_it_has_three_months():
    win = MONTHS   # 2025-01 .. 2025-08
    assert ba.cut(win, [MOVE]) == (win, None)   # not marked: never automatic
    marked = {**MOVE, "new_normal": True}
    assert ba.cut(win, [marked]) == (MONTHS[4:], marked)
    assert ba.cut(MONTHS[:6], [marked]) == (MONTHS[:6], None)   # two months since the move: the old window
    early = {"m": "2025-02", "label": "New job", "new_normal": True}
    assert ba.cut(MONTHS[:6], [marked, early]) == (MONTHS[1:6], early)   # the latest has too few, so the one before it
    assert ba.cut(MONTHS[2:], [early]) == (MONTHS[2:], None)   # before the window: it is already all since
    assert ba.cut(MONTHS[:3], [{"m": "2025-09", "label": "Later", "new_normal": True}]) == (MONTHS[:3], None)   # not happened yet
    assert ba.cut([], [marked]) == ([], None)


def test_the_look_ahead_and_the_suggested_plan_average_from_the_new_normal():
    rows = la.look_ahead(MONTHLY, [], [], [], [], [], "2025-08")
    assert rows[0]["pace_from"] == "2025-01" and rows[0]["pace_since"] is None and rows[0]["pace"] == round((350 * 4 + 250 * 4) / 8, 2)
    rows = la.look_ahead(MONTHLY, [], [], [], [], [], "2025-08", baseline_from="2025-05", baseline_label="Moved to Bywater")
    assert rows[0]["pace_from"] == "2025-05" and rows[0]["pace_months"] == 4 and rows[0]["pace"] == 250 and rows[0]["pace_since"] == "Moved to Bywater"
    budget = [{"m": m, "category": "Food", "flow": "expense", "group": "Food", "planned": 400, "actual": (300 if m >= "2025-05" else 400)} for m in MONTHS]
    plain = dec.plan_suggestions(budget, [])
    assert plain[0]["avg_from"] == "2025-01" and plain[0]["avg_months"] == 7 and plain[0]["avg_since"] is None and plain[0]["suggest"] == 360   # (4×400 + 3×300) / 7, rounded up
    cut = dec.plan_suggestions(budget, [], lambda w: ba.cut(w, [{**MOVE, "new_normal": True}]))
    assert cut[0]["avg_from"] == "2025-05" and cut[0]["avg_months"] == 3 and cut[0]["avg12"] == 300 and cut[0]["suggest"] == 300 and cut[0]["avg_since"] == "Moved to Bywater"


def test_marking_an_event_moves_each_window_in_the_standalone_scripts_and_each_says_so(tmp_path):
    """The golden exports (four budget months) with one event: not marked, nothing moves; marked, the pace and the outgo start at it."""
    wd = build(tmp_path / "plain", {})
    (wd / "events.json").write_text(json.dumps([{"m": "2025-02", "label": "Moved to Bywater", "kind": "move"}])); run_scripts(wd)
    la0, ef0 = ds(wd, "look_ahead")[0], ds(wd, "emergency_fund")[-1]
    assert la0["pace_from"] == "2025-01" and la0["pace_since"] is None and ef0["outgo_from"] == "2025-01" and ef0["outgo_since"] is None
    assert all(r["avg_since"] is None for r in ds(wd, "plan_suggestions"))
    ba0 = ds(wd, "before_after")
    assert {r["n"] for r in ba0} == {3, 6, 12} and all(r["label"] == "Moved to Bywater" and not r["new_normal"] for r in ba0)
    monthly = {r["m"]: r for r in ds(wd, "monthly")}
    six = next(r for r in ba0 if r["n"] == 6)
    assert six["short"] and six["before"]["months"] == 1 and six["after"]["months"] == 3
    assert six["after"]["spend"] == round(sum(monthly[m]["spend"] for m in ("2025-02", "2025-03", "2025-04")) / 3, 2)   # a hand sum
    wd2 = tmp_path / "marked"; shutil.copytree(wd, wd2)
    (wd2 / "events.json").write_text(json.dumps([{"m": "2025-02", "label": "Moved to Bywater", "kind": "move", "new_normal": True}])); run_scripts(wd2)
    la1, ef1 = ds(wd2, "look_ahead")[0], ds(wd2, "emergency_fund")[-1]
    assert la1["pace_from"] == "2025-02" and la1["pace_months"] == 3 and la1["pace_since"] == "Moved to Bywater"
    assert la1["pace"] == round(sum(monthly[m]["net"] for m in ("2025-02", "2025-03", "2025-04")) / 3, 2)
    assert ef1["outgo_from"] == "2025-02" and ef1["outgo_months"] == 3 and ef1["outgo_since"] == "Moved to Bywater"
    assert ef1["outgo"] == round(sum(monthly[m]["spend"] + monthly[m]["debt"] for m in ("2025-02", "2025-03", "2025-04")) / 3, 2)
    assert ds(wd2, "emergency_fund")[1]["outgo_since"] is None   # 2025-02 itself: one month since, the old window
    assert all(r["new_normal"] for r in ds(wd2, "before_after"))


def test_check_event_row_takes_yes_no_and_booleans_and_refuses_anything_else():
    from app.routes.settings import check_event_row, event_out
    for v, want in ((True, True), ("yes", True), ("Yes", True), ("true", True), (False, False), ("no", False), ("", False), (None, False)):
        c, err = check_event_row({"m": "2025-01", "label": "x", "new_normal": v})
        assert not err and c["new_normal"] is want, v
    for v in (1, "maybe", ["yes"]):
        assert check_event_row({"m": "2025-01", "label": "x", "new_normal": v})[1] == {"new_normal": "yes or no"}
    assert event_out({"m": "2025-01", "label": "x", "kind": None, "person": None, "new_normal": False}) == {"m": "2025-01", "label": "x"}
    assert event_out({"m": "2025-01", "label": "x", "new_normal": True}) == {"m": "2025-01", "label": "x", "new_normal": True}


def nn(conn):
    return {r["label"]: r["new_normal"] for r in conn.execute("SELECT label, new_normal FROM life_events").fetchall()}


def test_existing_events_are_not_marked_and_the_row_editor_marks_one(client, reset_db):
    with db.pool.connection() as conn:
        assert nn(conn) and not any(nn(conn).values())   # the migration's default: never ticked for the household
    r = client.post("/settings/events/row", data={"m": "2026-02", "label": "Elanor is born", "kind": "family", "new_normal": "yes"}).json()
    assert r["ok"] and r["row"]["new_normal"] == "yes"
    r = client.post("/settings/events/row", data={"m": "2026-03", "label": "Moved", "kind": "move", "new_normal": "perhaps"}).json()
    assert not r["ok"] and set(r["errors"]) == {"new_normal"}
    r = client.post("/settings/events/row", data={"m": "2026-02", "label": "Elanor is born", "kind": "family", "new_normal": "no",
                                                  "original_m": "2026-02", "original_label": "Elanor is born"}).json()
    assert r["ok"] and r["row"]["new_normal"] == "no"
    with db.pool.connection() as conn:
        assert nn(conn)["Elanor is born"] is False
    page = client.get("/settings").text
    assert 'data-fields="m,label,kind,person,new_normal"' in page and "New normal" in page


def test_json_editor_settings_zip_cli_and_seeds_round_trip_the_mark(client, reset_db, tmp_path):
    body = [{"m": "2025-03", "label": "New job", "kind": "job", "new_normal": True}, {"m": "2025-04", "label": "Plain"}]
    r = client.post("/settings/events", data={"body": json.dumps(body)})
    assert "Not saved" not in client.get(r.headers["location"]).text
    from app.routes.settings import _editor_body
    with db.pool.connection() as conn:
        assert json.loads(_editor_body(conn, "events")) == body   # new_normal only when set, as kind and person
    bad = tmp_path / "bad.json"; bad.write_text(json.dumps([{"m": "2025-03", "label": "x", "new_normal": "sometimes"}]))
    assert admin.main(["set-events", str(bad)]) == 1
    good = tmp_path / "good.json"; good.write_text(json.dumps([{"m": "2025-05", "label": "Moved", "kind": "move", "new_normal": True}]))
    assert admin.main(["set-events", str(good)]) == 0
    with db.pool.connection() as conn:
        assert nn(conn) == {"Moved": True}
    import io, zipfile
    z = zipfile.ZipFile(io.BytesIO(client.get("/export/settings.zip").content))
    assert json.loads(z.read("events.json")) == [{"m": "2025-05", "label": "Moved", "kind": "move", "new_normal": True}]
    from pipeline.run_pipeline import seed_reference_tables
    (tmp_path / "events.json").write_bytes(z.read("events.json"))
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM life_events"); seed_reference_tables(conn, tmp_path); conn.commit()
        assert nn(conn) == {"Moved": True}
    (tmp_path / "events.json").write_text(json.dumps([{"m": "2025-01", "label": "x", "new_normal": "yes"}]))
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM life_events")
        with pytest.raises(ValueError, match=r"event 2025-01 'x': new_normal must be true or false"):
            seed_reference_tables(conn, tmp_path)
        conn.rollback()


def test_sample_household_marks_the_birth_and_every_window_says_so(client, reset_db):
    reset_db(SAMPLE)
    upload_files(client, SAMPLE)
    data = client.get("/api/datasets").json()
    birth = [r for r in data["before_after"] if r["label"] == "Elanor is born"]
    assert {r["n"] for r in birth} == {3, 6, 12} and all(r["new_normal"] for r in birth)
    six = next(r for r in birth if r["n"] == 6)
    assert six["short"] and six["before"]["months"] == 6 and six["after"]["months"] == 5   # Feb to Jun 2026 loaded of Feb to Jul
    assert [c["category"] for c in six["new_categories"]] == ["Baby"]
    monthly = {r["m"]: r for r in data["monthly"]}
    assert six["before"]["spend"] == round(sum(monthly[m]["spend"] for m in ("2025-08", "2025-09", "2025-10", "2025-11", "2025-12", "2026-01")) / 6, 2)
    la0 = data["look_ahead"][0]
    assert la0["pace_from"] == "2026-02" and la0["pace_months"] == 5 and la0["pace_since"] == "Elanor is born"
    ef = data["emergency_fund"][-1]
    assert ef["outgo_from"] == "2026-02" and ef["outgo_since"] == "Elanor is born"
    ps = data["plan_suggestions"]
    assert ps and all(r["avg_from"] == "2026-02" and r["avg_months"] == 4 and r["avg_since"] == "Elanor is born" for r in ps)
    page = client.get("/").text
    assert 'id="ba-card"' in page and 'data-draw="ba-compare"' in page and 'data-draw="ahead-since"' in page and 'data-draw="lm-since"' in page


def test_review_names_the_comparison_within_six_months_of_the_new_normal(client, reset_db):
    reset_db(SAMPLE)
    upload_files(client, SAMPLE)
    with db.pool.connection() as conn:
        ctx = review.build_context(conn, "2026-04")
        nn_ = ctx["new_normal"]
        assert nn_["event"] == {"m": "2026-02", "label": "Elanor is born", "kind": "family"} and nn_["n"] == 6
        assert nn_["after"]["months"] == 3 and nn_["after"]["to"] == "2026-07" and "Baby" in nn_["new_categories"]   # as of April: three months seen
        assert "merchant" not in json.dumps(nn_).lower() and "new_normal" in review.SYSTEM
        assert "new_normal" not in review.build_context(conn, "2026-01")   # before the event
        conn.execute("UPDATE life_events SET new_normal=false"); conn.commit()
    from pipeline.run_pipeline import rebuild
    with db.pool.connection() as conn:
        rebuild(conn)
        assert "new_normal" not in review.build_context(conn, "2026-04")   # not marked: the review has nothing to name


def test_before_after_is_served_over_mcp(client, reset_db):
    assert "before_after" in mcp_endpoint.DATASETS
    reset_db(SAMPLE)
    upload_files(client, SAMPLE)
    from test_mcp import call
    from test_api_tokens import make_token
    err, rows = call(make_token(), "get_dataset", {"name": "before_after"})
    assert not err and rows and {"m", "label", "n", "before", "after", "change", "movers", "new_categories"} <= set(rows[0])
    assert "merchant" not in json.dumps(rows).lower()
    import io, zipfile   # and it leaves in datasets.zip, like every dataset
    assert "before_after.json" in zipfile.ZipFile(io.BytesIO(client.get("/export/datasets.zip").content)).namelist()
