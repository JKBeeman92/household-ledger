"""Dashboard period filter (issue #116): one range for every timeline, resolved against the loaded months. Behaviour (presets,
disabled ones, URL and reload) is checked in a real browser in the PR; these guard the wiring it depends on."""
from __future__ import annotations
import re

from conftest import ROOT

SRC = (ROOT / "app" / "web" / "templates" / "dashboard.html").read_text()
TIMELINES = ["monthly", "employer", "goal-lines", "annual", "net", "cat-stack", "debts", "slush", "earner", "payer"]
UNFILTERED = ["t-inc-n", "lm-var", "lm-cats", "lm-review", "goal-table", "cat-pivot", "yearly"]


def _draw_body(name: str) -> str:
    i = SRC.index(f"dash.draw('{name}'")
    return SRC[i:SRC.index("\ndash.draw(", i + 1)] if "\ndash.draw(" in SRC[i + 1:] else SRC[i:]


def test_every_timeline_reads_through_the_range():
    for name in TIMELINES:
        body = _draw_body(name)[:600]
        assert "HL.rd(" in body, f"{name} draws every month regardless of the period"


def test_tiles_last_month_and_tables_are_not_filtered():
    for name in UNFILTERED:
        if f"dash.draw('{name}'" in SRC:
            assert "HL.rd(" not in _draw_body(name)[:400], f"{name} should show its own fixed period"


def test_control_is_labelled_and_announces_the_period(client, reset_db):
    html = client.get("/").text
    assert re.search(r'<label for="range-pick">Period</label>\s*<select id="range-pick">', html)
    assert 'id="range-status" class="range-status" role="status" aria-live="polite"' in html
    assert "URLSearchParams(location.search).get('range')" in html   # a view can be linked


def test_presets_cover_the_requested_periods():
    for key, label in [("all", "All time"), ("12m", "Last 12 months"), ("ytd", "This year"), ("ly", "Last year"), ("5y", "Last 5 years")]:
        assert f"['{key}', '{label}']" in SRC
    assert "since:" in SRC and "custom:" in SRC
