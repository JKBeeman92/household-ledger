"""The averaging window (issue #236): one rule, in pipeline/averaging.py, for the months the look ahead's pace, the emergency fund's
outgo and the suggested plan average. A life event marked as the new normal (#159) moves it only once six budget months after it are
loaded, says the month it will move until then, and stops moving it twelve months after it; every figure counts the same months."""
from __future__ import annotations
import json, shutil

from test_rules_defaults import build, ds
from test_pipeline_golden import run_scripts
from pipeline import averaging as av, decisions as dec, look_ahead as la

MONTHS = [f"{y}-{m:02d}" for y in (2025, 2026) for m in range(1, 13)]   # 2025-01 .. 2026-12
MOVE = {"m": "2025-05", "label": "Moved to Bywater", "kind": "move", "new_normal": True}


def _w(c, months=MONTHS, events=(MOVE,)):
    w = av.window(months, c, list(events))
    return w["from"], w["n"], w["since"], w["switch"]


def test_fewer_than_six_months_after_the_event_nothing_moves_and_the_switch_month_is_said():
    assert _w("2025-04") == ("2025-01", 4, None, None)   # before the event: nothing to say
    assert _w("2025-05") == ("2025-01", 5, None, "2025-11")   # the event's own month: six after it are Jun .. Nov
    assert _w("2025-10") == ("2025-01", 10, None, "2025-11")   # five after it: still the trailing months
    w = av.window(MONTHS, "2025-10", [MOVE])
    assert (w["switch_since"], w["switch_event"]) == ("Moved to Bywater", "2025-05") and w["months"] == MONTHS[:10]


def test_exactly_six_months_after_the_event_the_window_starts_at_it():
    assert _w("2025-11") == ("2025-05", 7, "Moved to Bywater", None)   # the event's month and the six after it
    assert av.window(MONTHS, "2025-11", [MOVE])["months"] == MONTHS[4:11]


def test_between_six_and_twelve_months_the_window_is_every_month_since():
    assert _w("2026-01") == ("2025-05", 9, "Moved to Bywater", None)
    assert _w("2026-03") == ("2025-05", 11, "Moved to Bywater", None)


def test_twelve_months_on_the_window_is_the_trailing_twelve_again_and_the_notes_go():
    assert _w("2026-04") == ("2025-05", 12, None, None)   # the trailing twelve are all since the move: nothing to say
    assert _w("2026-05") == ("2025-06", 12, None, None)   # the cap: the plain last twelve
    assert _w("2026-12") == ("2026-01", 12, None, None)
    w = av.window(MONTHS, "2026-05", [MOVE])
    assert w["since"] is None and w["switch"] is None and w["switch_since"] is None


def test_a_missing_budget_month_is_not_counted_and_the_cap_still_holds():
    gap = [m for m in MONTHS if m != "2025-07"]   # July 2025 never loaded
    assert _w("2025-11", gap) == ("2025-01", 10, None, "2025-12")   # five loaded after the move, so one more: December
    assert _w("2025-12", gap) == ("2025-05", 7, "Moved to Bywater", None)   # Jun, Aug .. Dec are six
    assert _w("2026-04", gap) == ("2025-05", 11, "Moved to Bywater", None)   # the trailing twelve would reach back to Apr 2025
    assert _w("2026-05", gap) == ("2025-05", 12, None, None)   # twelve months on: the trailing twelve (Jul missing), no note
    assert _w("2025-08", gap) == ("2025-01", 7, None, "2025-12")


def test_only_a_marked_event_that_has_happened_moves_anything():
    plain = {k: v for k, v in MOVE.items() if k != "new_normal"}
    assert _w("2025-12", events=[plain]) == ("2025-01", 12, None, None)   # never automatic
    assert _w("2025-03", events=[MOVE]) == ("2025-01", 3, None, None)   # not happened yet
    assert _w("2025-12", events=[]) == _w("2025-12", events=[plain])
    assert av.window([], None, [MOVE])["n"] == 0
    since = MONTHS[4:]   # a household that started at the event: the window is already all since it, so no switch is announced
    assert _w("2025-08", since) == ("2025-05", 4, None, None)


def test_two_marked_events_the_latest_switched_cuts_and_the_next_switch_names_its_month():
    job = {"m": "2025-02", "label": "New job", "new_normal": True}
    later = {"m": "2025-09", "label": "Moved", "new_normal": True}
    w = av.window(MONTHS, "2025-12", [job, later])
    assert (w["from"], w["n"], w["since"], w["switch"], w["switch_since"]) == ("2025-02", 11, "New job", "2026-03", "Moved")
    w = av.window(MONTHS, "2026-03", [job, later])
    assert (w["from"], w["n"], w["since"], w["switch"]) == ("2025-09", 7, "Moved", None)
    w = av.window(MONTHS, "2025-05", [job, {"m": "2025-04", "label": "Moved", "new_normal": True}])   # both still to switch
    assert (w["n"], w["since"], w["switch"], w["switch_since"]) == (5, None, "2025-08", "New job")   # the job first, in August
    w = av.window(MONTHS, "2026-03", [job, later])
    assert av.fields("pace", w) == {"pace_from": "2025-09", "pace_months": 7, "pace_since": "Moved", "pace_switch": None,
                                    "pace_switch_since": None, "pace_switch_event": None}


# eight budget months a net of 350 and 1000 of Food, then from the move 250 and 300
def _monthly(months):
    return [{"m": m, "net": 250.0 if m >= "2025-05" else 350.0} for m in months]
def _budget(months):
    return [{"m": m, "category": "Food", "flow": "expense", "group": "Food", "planned": 400, "actual": (300 if m >= "2025-05" else 400)} for m in months]


def test_the_pace_and_the_suggested_plan_average_the_same_months():
    months = MONTHS[:11]   # to Nov 2025: six after the move
    w = av.window(months, None, [MOVE])
    rows = la.look_ahead(_monthly(months), [], [], [], [], [], "2025-11", window=w)
    plan = dec.plan_suggestions(_budget(months), [], lambda ms: av.window(ms, None, [MOVE]))
    assert rows[0]["pace_from"] == plan[0]["avg_from"] == "2025-05" and rows[0]["pace_months"] == plan[0]["avg_months"] == 7
    assert rows[0]["pace"] == 250 and plan[0]["avg12"] == 300 and plan[0]["suggest"] == 300 and rows[0]["pace_since"] == plan[0]["avg_since"] == "Moved to Bywater"
    assert rows[0]["pace_3"] == 250   # the last three, whatever the window
    early = MONTHS[:8]   # to Aug 2025: three after the move, so the trailing months and the month they move
    rows = la.look_ahead(_monthly(early), [], [], [], [], [], "2025-08", window=av.window(early, None, [MOVE]))
    plan = dec.plan_suggestions(_budget(early), [], lambda ms: av.window(ms, None, [MOVE]))
    assert rows[0]["pace_months"] == plan[0]["avg_months"] == 8 and rows[0]["pace_since"] is None and plan[0]["avg_since"] is None
    assert rows[0]["pace"] == round((350 * 4 + 250 * 4) / 8, 2) and plan[0]["avg12"] == round((400 * 4 + 300 * 4) / 8, 2)   # the closing month counted
    assert rows[0]["pace_switch"] == plan[0]["avg_switch"] == "2025-11" and plan[0]["avg_switch_since"] == "Moved to Bywater" and plan[0]["avg_switch_event"] == "2025-05"
    plain = dec.plan_suggestions(_budget(early), [])   # no window given: the last twelve, the latest included
    assert plain[0]["avg_months"] == 8 and plain[0]["avg_switch"] is None


def test_the_standalone_scripts_say_the_switch_month_and_one_count_everywhere(tmp_path):
    """The golden exports (Jan to Apr 2025) with a move in February: unmarked, nothing; marked, two months after it are loaded, so the
    pace, the outgo and the plan stay on the four months and each says August."""
    wd = build(tmp_path / "plain", {})
    (wd / "events.json").write_text(json.dumps([{"m": "2025-02", "label": "Moved to Bywater", "kind": "move"}])); run_scripts(wd)
    la0, ef0, ps0 = ds(wd, "look_ahead")[0], ds(wd, "emergency_fund")[-1], ds(wd, "plan_suggestions")[0]
    assert la0["pace_switch"] is None and ef0["outgo_switch"] is None and ps0["avg_switch"] is None
    wd2 = tmp_path / "marked"; shutil.copytree(wd, wd2)
    (wd2 / "events.json").write_text(json.dumps([{"m": "2025-02", "label": "Moved to Bywater", "kind": "move", "new_normal": True}])); run_scripts(wd2)
    la1, ef1, ps = ds(wd2, "look_ahead")[0], ds(wd2, "emergency_fund"), ds(wd2, "plan_suggestions")
    assert la1["pace_months"] == ef1[-1]["outgo_months"] == ps[0]["avg_months"] == 4   # one count: Jan to Apr, April included
    assert la1["pace_from"] == ef1[-1]["outgo_from"] == ps[0]["avg_from"] == "2025-01"
    assert la1["pace_since"] is None and ef1[-1]["outgo_since"] is None and all(r["avg_since"] is None for r in ps)
    assert la1["pace_switch"] == ef1[-1]["outgo_switch"] == "2025-08" and all(r["avg_switch"] == "2025-08" for r in ps)
    assert la1["pace_switch_since"] == ef1[-1]["outgo_switch_since"] == "Moved to Bywater" and la1["pace_switch_event"] == "2025-02"
    assert ef1[0]["outgo_switch"] is None and ef1[1]["outgo_switch"] == "2025-08"   # each month its own window: January is before it
    assert la1["pace"] == la0["pace"] and ef1[-1]["outgo"] == ef0["outgo"]   # nothing moved
    yr = ds(wd2, "year_review")[0]["ahead"]
    assert yr["pace_switch"] == "2025-08" and yr["pace_months"] == 4
