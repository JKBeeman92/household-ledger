"""Dashboard accessibility wiring (issue #15): tabs, panels, chart and definition helpers, print. Keyboard behaviour itself is checked in a
real browser (see the issue's PR); these guard the markup and files that behaviour depends on."""
from __future__ import annotations
import re

from conftest import ROOT

STATIC = ROOT / "app" / "web" / "static"
CSS = (STATIC / "app.css").read_text()
JS = (STATIC / "a11y.js").read_text()


def test_tabs_are_wired_to_panels(client, reset_db):
    html = client.get("/").text
    tabs = re.findall(r'<button[^>]*role="tab"[^>]*>', html)
    assert len(tabs) == 8
    for t in tabs:
        name = re.search(r'data-tab="(\w+)"', t).group(1)
        assert f'id="tab-{name}"' in t and f'aria-controls="{name}"' in t
        assert re.search(rf'<section id="{name}" class="page" role="tabpanel" aria-labelledby="tab-{name}" tabindex="0"', html)
    assert [re.search(r'tabindex="(-?\d)"', t).group(1) for t in tabs] == ["0"] + ["-1"] * 7, "roving tabindex: only the selected tab is in the tab order"


def test_tab_keys_and_helper_script(client, reset_db):
    html = client.get("/").text
    for key in ("ArrowRight", "ArrowLeft", "Home", "End"):
        assert key in html
    assert '<script src="/static/a11y.js"></script>' in html
    assert client.get("/static/a11y.js").status_code == 200
    assert not re.search(r"\son(click|keydown|pointer\w+|load)=", html), "the CSP blocks inline handlers"


def test_helper_covers_charts_definitions_and_print():
    assert "role', 'img'" in JS and "aria-label" in JS
    assert "dfn[title]" in JS and "tabIndex = 0" in JS and "Escape" in JS and "click" in JS
    assert "beforeprint" in JS and "matchMedia('print')" in JS


def test_print_stylesheet_hides_chrome_and_uses_tokens_only():
    m = re.search(r"@media print \{(.*?)\n\}", CSS, re.S)
    assert m, "no print stylesheet"
    block = m.group(1)
    assert re.search(r"\.topbar[^{]*\{[^}]*display: none", block) and ".controls" in block
    assert "max-height: none" in block and "break-inside: avoid" in block
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", re.sub(r"#dash-root", "", block))


def test_accent_2_text_variant_used_for_small_text(client, reset_db):
    assert "color: var(--accent-2-text" in CSS
    assert "var(--accent-2-text" in client.get("/").text


def test_charts_without_a_table_get_one_and_say_their_span():
    """Issue #122: the figures behind each chart are reachable without a mouse, and the label states the data's own range."""
    html = (ROOT / "app" / "web" / "templates" / "dashboard.html").read_text()
    for name in ("monthly", "annual", "employer", "goal-lines", "debts", "net", "dti", "nw", "ef", "slush", "radar"):   # slush, radar: DS-CHART-5 (#191)
        assert f'data-draw="dt-{name}"' in html, name
    assert html.count('<details class="datatable"><summary>Show the figures as a table</summary>') == 11
    assert "if (chart.dataset.span) parts.push('Figures from ' + chart.dataset.span + '.');" in JS
    assert "The same figures are in a table after the chart." in JS
