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
    assert 'data-fields="m,label,kind,person,on_chart,new_normal"' in page and "New normal" in page


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
    # a seed takes what set-events and the JSON editor take: yes/no and null as well as true/false, and refuses the rest
    (tmp_path / "events.json").write_text(json.dumps([{"m": "2025-01", "label": "a", "new_normal": "yes"}, {"m": "2025-02", "label": "b", "new_normal": None}]))
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM life_events"); seed_reference_tables(conn, tmp_path); conn.commit()
        assert nn(conn) == {"a": True, "b": False}
    (tmp_path / "events.json").write_text(json.dumps([{"m": "2025-01", "label": "x", "new_normal": "sometimes"}]))
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM life_events")
        with pytest.raises(ValueError, match=r"event 2025-01 'x': new_normal must be yes or no, true or false"):
            seed_reference_tables(conn, tmp_path)
        conn.rollback()


def test_the_new_normal_and_the_on_the_chart_switch_travel_together(client, reset_db, tmp_path):
    # #159 with #140: an event switched off the chart can still be the new normal; each flag is written only when it differs from its
    # default, and neither overwrites the other on any way in or out
    body = [{"m": "2025-03", "label": "Envelopes", "kind": "other", "on_chart": False, "new_normal": True}, {"m": "2025-04", "label": "Plain"}]
    r = client.post("/settings/events", data={"body": json.dumps(body)})
    assert "Not saved" not in client.get(r.headers["location"]).text
    from app.routes.settings import _editor_body
    with db.pool.connection() as conn:
        assert json.loads(_editor_body(conn, "events")) == body
        assert [tuple(x.values()) for x in conn.execute("SELECT label, on_chart, new_normal FROM life_events ORDER BY month").fetchall()] == [("Envelopes", False, True), ("Plain", True, False)]
    r = client.post("/settings/events/row", data={"m": "2025-03", "label": "Envelopes", "kind": "other", "on_chart": "yes", "new_normal": "yes",
                                                  "original_m": "2025-03", "original_label": "Envelopes"}).json()
    assert r["ok"] and (r["row"]["on_chart"], r["row"]["new_normal"]) == ("yes", "yes")
    import io, zipfile
    z = zipfile.ZipFile(io.BytesIO(client.get("/export/settings.zip").content))
    assert json.loads(z.read("events.json"))[0] == {"m": "2025-03", "label": "Envelopes", "kind": "other", "new_normal": True}
    (tmp_path / "events.json").write_text(json.dumps(body))
    from pipeline.run_pipeline import seed_reference_tables, export_for_pipeline
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM life_events"); seed_reference_tables(conn, tmp_path); conn.commit()
        assert [tuple(x.values()) for x in conn.execute("SELECT label, on_chart, new_normal FROM life_events ORDER BY month").fetchall()] == [("Envelopes", False, True), ("Plain", True, False)]
        wd = tmp_path / "wd"; wd.mkdir(); export_for_pipeline(conn, wd)
    assert json.loads((wd / "events.json").read_text()) == body   # what the events dataset and before_after read


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


def test_the_new_normal_off_the_chart_still_moves_every_window(client, reset_db):
    # #159 with #140: switching the birth off the chart hides its marker but it is still the new normal for the comparison, the look
    # ahead's pace and the emergency fund's outgo
    reset_db(SAMPLE)
    upload_files(client, SAMPLE)
    with db.pool.connection() as conn:
        conn.execute("UPDATE life_events SET on_chart=false WHERE label='Elanor is born'"); conn.commit()
    from pipeline.run_pipeline import rebuild
    with db.pool.connection() as conn:
        rebuild(conn)
    data = client.get("/api/datasets").json()
    ev = next(r for r in data["events"] if r["label"] == "Elanor is born")
    assert ev.get("on_chart") is False and ev.get("new_normal") is True
    birth = [r for r in data["before_after"] if r["label"] == "Elanor is born"]
    assert {r["n"] for r in birth} == {3, 6, 12} and all(r["new_normal"] for r in birth)
    assert data["look_ahead"][0]["pace_since"] == "Elanor is born"
    assert data["emergency_fund"][-1]["outgo_since"] == "Elanor is born"


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


def test_a_category_that_first_appears_late_in_the_after_side_is_not_new_since_the_event():
    """New since the event: first spent in the event's first three months. One that starts in month 8 of a 12-month after side began
    with something else (the reviewer's case: Baby counted as new since an event eleven months before the birth)."""
    months = [f"2025-{m:02d}" for m in range(1, 13)]
    monthly = [{"m": m, "income": 1000.0, "spend": 500.0, "debt": 0.0, "net": 500.0} for m in months]
    def cats(m):
        c = {"Food": 400.0}
        if m >= "2025-04": c["Gym"] = 30.0      # the event's own month: new since it
        if m >= "2025-06": c["Streaming"] = 15.0   # the event's third month: still new since it
        if m >= "2025-07": c["Baby"] = 200.0    # month four on: not the event's doing
        return [{"m": m, "category": k, "amount": v} for k, v in c.items()]
    cat_month = [r for m in months for r in cats(m)]
    ev = {"m": "2025-04", "label": "New job"}
    r12 = ba.compare(ev, 12, monthly, cat_month)
    assert [c["category"] for c in r12["new_categories"]] == ["Gym", "Streaming"]
    assert next(c for c in r12["movers"]["by_dollars"] if c["category"] == "Baby")["new"] is False   # still a mover, just not new
    assert [c["category"] for c in ba.compare(ev, 3, monthly, cat_month)["new_categories"]] == ["Gym", "Streaming"]


def test_the_check_in_says_since_the_new_normal_and_the_old_copy_returns_unmarked(client, reset_db):
    """Each window that moves says so where it renders: the quarter step's pace, the next-month step's suggested plan and the year
    review's pace; with nothing marked, the twelve-month wording is back."""
    from pipeline.run_pipeline import rebuild
    reset_db(SAMPLE)
    upload_files(client, SAMPLE)
    with db.pool.connection() as conn:   # a July year, so June 2026 is a year's last month and its check-in has the year in review
        db.put_setting(conn, "rules", {**(db.get_setting(conn, "rules") or {}), "year_start_month": 7}); conn.commit()
        rebuild(conn)
    page = client.get("/checkin/2026-06").text
    assert "Mark Year in review as read" in page   # year mode
    assert '>Add to the plan</button>' in page and 'aria-label="Add to the plan: Food at $940"' in page and "Add “Plan" not in page   # the figure once, the name in the button's
    # one shape everywhere (the 0.9.0 release review): the N budget months since the event (its month), the household's new normal,
    # and the pace said once as what it is
    assert "keeps its pace, the average net kept a month, over the 5 budget months since Elanor is born (Feb 2026), the household's new normal: " in page   # the quarter step
    assert "budget months since Elanor is born (Feb 2026), the household's new normal, rounded up" in page and "The average of the " in page   # the suggested plan
    assert "at its pace, the average net kept a month, over the 5 budget months since Elanor is born (Feb 2026), the household's new normal," in page   # the year in review
    assert "its new normal" not in page
    assert "the last twelve months" not in page and "The twelve-month average," not in page
    with db.pool.connection() as conn:
        conn.execute("UPDATE life_events SET new_normal=false"); conn.commit()
        rebuild(conn)
    page = client.get("/checkin/2026-06").text
    assert "since Elanor is born" not in page
    assert "keeps its pace, the average net kept a month, over the last twelve months: " in page and "The twelve-month average, rounded up" in page and "at its pace, the average net kept a month, over the last twelve months," in page


def test_in_real_dollars_the_movers_are_ranked_in_real_dollars():
    months = ["2025-01", "2025-02"]
    monthly = [{"m": m, "income": 1000.0, "spend": 300.0, "debt": 0.0, "net": 700.0} for m in months]
    # Fuel doubles in dollars but mostly with prices; Food rises less in dollars and all of it real
    cat_month = [{"m": "2025-01", "category": "Fuel", "amount": 100.0, "amount_real": 100.0}, {"m": "2025-01", "category": "Food", "amount": 100.0, "amount_real": 100.0},
                 {"m": "2025-02", "category": "Fuel", "amount": 200.0, "amount_real": 120.0}, {"m": "2025-02", "category": "Food", "amount": 150.0, "amount_real": 150.0}]
    mv = ba.compare({"m": "2025-02", "label": "x"}, 3, monthly, cat_month)["movers"]
    assert [r["category"] for r in mv["by_dollars"]] == ["Fuel", "Food"] and [r["category"] for r in mv["by_dollars_real"]] == ["Food", "Fuel"]
    assert [r["category"] for r in mv["by_pct"]] == ["Fuel", "Food"] and [r["category"] for r in mv["by_pct_real"]] == ["Food", "Fuel"]


# ------------------------------------------------------------------ in a real browser (the UX review of #159)

from test_appearance import browser, server, sample  # noqa: E402,F401  the app in its own process on the sample household


def ba_harness(url: str, **opts) -> dict:
    import subprocess
    from conftest import ROOT
    p = subprocess.run(["node", str(ROOT / "tests" / "js" / "before_after_harness.js"), url, json.dumps(opts)], capture_output=True, text=True, timeout=180)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["errors"] == [], out["errors"]
    return out


@browser
@pytest.mark.parametrize("width,theme", [(390, "light"), (390, "dark"), (1280, "light"), (1280, "dark")])
def test_the_change_column_is_in_view_on_a_phone_and_nothing_scrolls_the_page(sample, width, theme):
    """The Change column is the point of the comparison and the movers: at 390 px every Change cell ends inside its table's box, no
    table needs a sideways swipe, and the headers say Before and After (the key says once that each is a month's average)."""
    s = ba_harness(sample, width=width, theme=theme)
    for view in ("overview", "overviewPct"):
        o = s[view]
        assert o["pageOverflow"] <= 0 and len(o["tables"]) == 2, view
        for t in o["tables"]:
            assert t["heads"][1:] == ["Before", "After", "Change"]
            assert t["overflow"] <= 0 and t["changeOut"] == 0, (view, width, t)
            assert t["rows"] and all(r[3] for r in t["rows"])
    assert s["lastmonth"]["pageOverflow"] <= 0
    assert s["overview"]["options"][-1] == "Feb 2026: Elanor is born (family)"   # short enough for a phone's field; the note names the new normal
    assert s["overview"]["span"].startswith("Elanor is born (family) is the household's new normal (Settings → Life events). Before: ")


@browser
def test_last_month_stamps_the_emergency_fund_and_keys_the_kept_rows(sample):
    s = ba_harness(sample, width=390, settings="Moved to a bigger hole")
    lm = s["lastmonth"]
    ef = lm["ef"]   # DS-STATUS-1: 2.0 months against a target of 6 is a stamp, as the sibling rows are; the sentence stays plain
    assert ef["stamps"] == ["Needs a look"] and ef["downs"] == 0
    assert "(target 6 months)" in ef["text"] and "4.0 months short of the target." in ef["text"] and "emergency_months" not in ef["text"]
    assert ef["dts"][1] == "Emergency savings"
    key = lm["key"]   # built from the rule as built: the default keeps Housing, Debt and Insurance
    assert key[-1]["mark"] == "=" and "Housing, Debt and Insurance keep their plan" in key[-1]["text"] and not any(k["mark"] == "↑10" for k in key)
    kept = {c["category"] for c in lm["cats"] if c["mark"]}
    assert kept == {"Housing", "Debt", "Insurance"} and all(c["hidden"] == ", kept at its plan" for c in lm["cats"] if c["mark"])
    assert not any(c["hidden"] for c in lm["cats"] if not c["mark"])
    assert lm["avgSince"] == "Averages use the 4 budget months since Elanor is born (Feb 2026), the household's new normal."   # its own, without the plan
    assert "suggested plan" in lm["detailSince"]
    # one shape for the since-note (the 0.9.0 release review): the count, the event and its month, then "not the last twelve"
    assert lm["detailSince"] == "The averages and the suggested plan use the 4 budget months since Elanor is born (Feb 2026), the household's new normal, not the last twelve."
    assert lm["aheadSince"].startswith("The pace is the average net kept a month over the 5 budget months since Elanor is born (Feb 2026), the household's new normal, not the last twelve.")
    # the Average card's key says what its colours mean, as the plan card's does (DS-CHART-5), and keeps the direction in words
    assert lm["avgKey"][:4] == ["worse than usual", "better than usual", "a bar to the right is above its average, to the left below it", "for savings, giving and debt, more is better"]
    st = s["settings"]   # the offer on an edited move row; heard when it appears, and gone once yes is chosen or the kind is not a move
    assert st["opened"] == {"text": "If this month changed what a normal month costs, set New normal to yes: the pace, the emergency fund and the suggested plan will average from here once three months are loaded.", "role": "status", "live": "polite", "describedby": "new-normal-hint", "value": "no"}
    assert st["yes"]["text"] == "" and st["yes"]["describedby"] == "new-normal-hint" and st["job"]["text"] == ""


@browser
def test_the_emergency_fund_stamp_reads_on_track_at_the_target_as_shown_and_neutral_without_one(client, sample):
    """The stamp agrees with the figures as shown: a target a few hundredths above the coverage reads the same to one decimal, so it
    is on track, not "0.0 months short"; with no emergency_months goal the stamp is neutral and the line links to Settings → Goals."""
    with db.pool.connection() as conn:
        months = conn.execute("SELECT rows FROM datasets WHERE name='emergency_fund'").fetchone()["rows"][-1]["months"]
        goal = conn.execute("SELECT key, value, unit, label, note FROM goals WHERE key='emergency_months'").fetchone()
    shown = round(months, 1)
    try:
        with db.pool.connection() as conn:
            conn.execute("UPDATE goals SET value=%s WHERE key='emergency_months'", (shown + 0.04,)); conn.commit()
        assert client.post("/rebuild").status_code in (200, 303)
        ef = ba_harness(sample, width=390)["lastmonth"]["ef"]
        assert ef["stamps"] == ["On track"] and "At or above the target." in ef["text"] and "short" not in ef["text"], ef
        with db.pool.connection() as conn:
            conn.execute("DELETE FROM goals WHERE key='emergency_months'"); conn.commit()
        assert client.post("/rebuild").status_code in (200, 303)
        ef = ba_harness(sample, width=390)["lastmonth"]["ef"]
        assert ef["stamps"] == ["No goal set"] and "No target set." in ef["text"] and ["Set one on Settings → Goals", "/settings#goals"] in ef["links"], ef
    finally:
        with db.pool.connection() as conn:
            conn.execute("DELETE FROM goals WHERE key='emergency_months'")
            conn.execute("INSERT INTO goals (key, value, unit, label, note) VALUES (%(key)s, %(value)s, %(unit)s, %(label)s, %(note)s)", goal); conn.commit()
        client.post("/rebuild")


@browser
def test_the_key_and_the_marks_follow_a_households_own_keep_list(client, sample):
    with db.pool.connection() as conn:
        rules = db.get_setting(conn, "rules")
        db.put_setting(conn, "rules", {**rules, "plan_keep_categories": ["Food"]}); conn.commit()
    try:
        assert client.post("/rebuild").status_code in (200, 303)
        lm = ba_harness(sample, width=1280)["lastmonth"]
        assert lm["key"][-1]["text"] == "=Food keeps its plan (Settings → Pipeline rules, Roles)"
        assert [c["category"] for c in lm["cats"] if c["mark"]] == ["Food"]
        food = next(c for c in lm["cats"] if c["category"] == "Food")
        assert food["suggest"].startswith("= ") and food["hidden"] == ", kept at its plan"
        with db.pool.connection() as conn:
            db.put_setting(conn, "rules", {**rules, "plan_keep_categories": []}); conn.commit()
        assert client.post("/rebuild").status_code in (200, 303)
        lm = ba_harness(sample, width=1280)["lastmonth"]
        assert not any(k["mark"] == "=" for k in lm["key"]) and not any(c["mark"] for c in lm["cats"])   # nothing kept: no mark to explain
    finally:
        with db.pool.connection() as conn:
            db.put_setting(conn, "rules", rules); conn.commit()
        client.post("/rebuild")


def test_the_by_percent_floor_on_the_page_is_the_pipelines_and_a_missing_window_is_not_disabled():
    from conftest import ROOT
    page = (ROOT / "app" / "web" / "templates" / "dashboard.html").read_text()
    assert f"const BA_PCT_FLOOR = {ba.PCT_FLOOR};" in page and "$25" not in page   # formatted with the household's currency
    assert "add balances (Settings → Accounts)" not in page and "add('*'" not in page   # a missing net worth is a dash, the reason and any growth under the table
    assert "b.disabled" not in page and "aria-disabled" in page   # a window with no row stays in the tab order; the line under the field says why
    css = (ROOT / "app" / "web" / "static" / "app.css").read_text()
    assert '.hl .seg button[aria-disabled="true"] { opacity: .5; cursor: not-allowed; }' in css   # it looks as unavailable as .btn:disabled
