"""A household's own BRAND_DIR kit is checked at start and when its theme.css changes (#110): one WARNING per problem, never a refusal."""
from __future__ import annotations
import logging, os

import pytest

from conftest import ROOT
from app import brand_check as bc

SHIPPED = ROOT / "app" / "web" / "static" / "brand" / "theme.css"


@pytest.fixture(autouse=True)
def fresh():
    bc._last.update(key=None, problems=[])
    yield
    bc._last.update(key=None, problems=[])


def kit(tmp_path, extra: str = "", name: str = "theme.css"):
    (tmp_path / name).write_text(SHIPPED.read_text() + "\n" + extra)
    return tmp_path


def test_a_faint_border_in_one_mode_is_named_with_its_ratio_and_use(tmp_path):
    kit(tmp_path, ':root[data-theme="light"] { --line-strong: #e8e8e8; }')
    problems = bc.check(bc.read(tmp_path / "theme.css"), "brand: theme.css")
    assert len(problems) == 2 and all(p.startswith("brand: theme.css light: line-strong on surface-") for p in problems)
    assert any(" (< 3.0) for input borders" in p for p in problems)


def test_a_missing_token_and_an_unreadable_value_are_reported_not_raised():
    css = SHIPPED.read_text().replace("--accent-2-text", "--accent-2-txt") + "\n:root { --series-3: color-mix(in srgb, red, blue); }"
    problems = bc.check(css)
    assert "theme.css dark: --accent-2-text is not defined" in problems and "theme.css light: --accent-2-text is not defined" in problems
    assert "theme.css dark: --series-3: could not evaluate color-mix(in srgb, red, blue)" in problems
    assert bc.check(SHIPPED.read_text() + "\n:root { --ink: #ffffff80; }")[-1].endswith("--ink: translucent: its contrast depends on what is behind it")


def test_modern_colour_forms_are_evaluated():
    css = SHIPPED.read_text() + "\n:root { --series-1: oklch(70% 0.15 150); --series-2: hsl(30 90% 60%); --series-4: rgb(120 170 230); --focus: #fc6; }"
    assert not [p for p in bc.check(css) if "could not" in p]


def test_local_imports_are_followed_inside_the_kit_only(tmp_path):
    (tmp_path / "tokens.css").write_text(SHIPPED.read_text())
    (tmp_path.parent / "outside.css").write_text(":root { --ink: #000; }")
    (tmp_path / "theme.css").write_text('@import url("https://fonts.example/x.css");\n@import "tokens.css";\n@import url(../outside.css);\n'
                                        ':root[data-theme="light"] { --line-strong: #e8e8e8; }')
    css = bc.read(tmp_path / "theme.css")
    assert "--surface-100" in css and "fonts.example" not in css and "--ink: #000" not in css
    assert any("line-strong" in p for p in bc.check(css))


def test_check_dir_warns_once_per_change_and_never_raises(tmp_path, caplog):
    assert bc.check_dir(tmp_path) == []   # no theme.css: the shipped theme is used
    kit(tmp_path, ':root[data-theme="light"] { --line-strong: #e8e8e8; }')
    with caplog.at_level(logging.WARNING, logger="ledger.brand"):
        first = bc.check_dir(tmp_path); again = bc.check_dir(tmp_path)
    assert first == again and len([r for r in caplog.records if r.name == "ledger.brand"]) == len(first) == 2
    caplog.clear()
    kit(tmp_path)   # fixed
    os.utime(tmp_path / "theme.css", ns=(1, 10**18))
    with caplog.at_level(logging.INFO, logger="ledger.brand"):
        assert bc.check_dir(tmp_path) == []
    assert any("passes every contrast check" in r.getMessage() for r in caplog.records)
    (tmp_path / "theme.css").write_bytes(b"\xff\xfe not css")
    assert bc.check_dir(tmp_path)[0].startswith("brand: theme.css could not be checked")


def test_serving_theme_css_rechecks_and_settings_shows_the_count(client, monkeypatch, tmp_path, caplog):
    from app.routes import brand
    monkeypatch.setattr(brand, "BRAND_DIR", kit(tmp_path, ':root { --ink-faint: #333; }'))
    with caplog.at_level(logging.WARNING, logger="ledger.brand"):
        assert client.get("/brand/theme.css").status_code == 200
    assert any("ink-faint on surface-100" in r.getMessage() for r in caplog.records)
    page = client.get("/settings").text
    assert 'id="brand-problems"' in page and "Your theme fails 1 contrast check" in page


def test_shipped_theme_shows_no_warning_on_settings(client):
    assert 'id="brand-problems"' not in client.get("/settings").text
