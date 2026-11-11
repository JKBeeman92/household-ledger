"""The life-event list under "Income against outgo" is collapsed behind a count (#139), so a dozen events no longer push the next chart off
the screen. It already followed the period filter (HL.rd); the browser check of that lives in the PR notes, this pins the structure."""
from __future__ import annotations
import re

from conftest import ROOT

PAGE = (ROOT / "app" / "web" / "templates" / "dashboard.html").read_text()


def test_event_list_sits_in_a_closed_details_with_a_count():
    m = re.search(r'<details class="how evlist"(?P<attrs>[^>]*)><summary>[^<]*</summary><ul [^>]*data-draw="event-list"', PAGE)
    assert m, "the event list must be inside <details class=\"how evlist\">"
    assert "open" not in m["attrs"]                     # closed by default
    assert "hidden" not in m["attrs"]                   # dash-shim skips draws inside [hidden]: the box could never show itself
    draw = PAGE[PAGE.index("dash.draw('event-list'"):]
    draw = draw[:draw.index("\n});")]
    assert "HL.rd('events')" in draw                    # follows the period filter, like the markers
    assert "Life events in this period (${n})" in draw
    assert "style.display" in draw and ".hidden" not in draw
