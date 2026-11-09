"""The mobile layout pass (issue #24) is CSS; these guard its load-bearing rules and the token-only colour rule."""
from __future__ import annotations
import re

from conftest import ROOT

CSS = (ROOT / "app" / "web" / "static" / "app.css").read_text()


def phone_block() -> str:
    m = re.search(r"@media \(max-width: 820px\) \{(.*?)\n\}\n@media", CSS, re.S)
    assert m, "no phone breakpoint in app.css"
    return m.group(1)


def test_tabs_scroll_and_tap_targets():
    b = phone_block()
    assert re.search(r"\.tabs \{[^}]*flex-wrap: nowrap;[^}]*overflow-x: auto", b)
    assert "min-height: 44px" in b


def test_year_tables_keep_first_column():
    assert re.search(r"\.tbl th:first-child, #dash-root\.hl \.tbl td:first-child \{[^}]*position: sticky; left: 0", phone_block())


def test_radars_stack_on_phones():
    assert re.search(r"max-width: 600px\) \{[^}]*\.radars[^}]*grid-template-columns: 1fr", CSS, re.S)


def test_no_hex_colours_in_app_css():
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b(?![-\w])", re.sub(r"#dash-root|#theme-toggle", "", CSS))


def test_page_is_scalable_viewport(client, reset_db):
    assert "width=device-width, initial-scale=1" in client.get("/").text
