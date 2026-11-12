"""The parts of docs/DESIGN.md a machine can check: tokens only in the app's own styles (DS-TOKEN-1, DS-TOKEN-2), the files a kit
supplies and what each falls back to (section 7), glyphs replaced by id (DS-GLYPH-3), and the flourish words (DS-WORD-2).
What needs an eye is left to the ux-critique workflow (AGENTS.md, "UX review")."""
from __future__ import annotations
import json, re

import pytest

from conftest import ROOT
from app import brand_kit

WEB = ROOT / "app" / "web"
OWN = [WEB / "static" / "app.css", *sorted((WEB / "static").glob("*.js")), *sorted((WEB / "templates").rglob("*.html"))]
KITS = {"bearings": WEB / "static" / "brand", "red-book": ROOT / "examples" / "brand-kits" / "red-book",
        "war-of-the-ring": ROOT / "examples" / "brand-kits" / "war-of-the-ring"}
GLYPH_IDS = {"dashboard", "check-in", "close-month", "reviews", "settings", "day", "night", "sign-out", "savings-rate", "net-worth", "emergency-fund"}


@pytest.mark.parametrize("path", OWN, ids=lambda p: p.name)
def test_no_colour_typeface_or_alias_is_typed_into_the_app(path):
    text = re.sub(r"&#\w+;", "", path.read_text(encoding="utf-8"))
    assert not re.findall(r"#[0-9a-fA-F]{6}\b|:\s*#[0-9a-fA-F]{3}\b|\brgba?\(|\bhsla?\(", text), "a colour is typed here; use a token (DS-TOKEN-1)"
    assert not re.findall(r"--(?:cds|color)-[\w-]+|--font-anthropic|--brand[01]\b", text), "an alias for a contract token (DS-TOKEN-2)"
    assert not re.findall(r"font-family:\s*[\"'A-Z]", text), "a typeface is named here; use --font-display, --font-sans or --font-mono (DS-TOKEN-1)"


def test_app_css_sets_no_pixel_font_size():
    assert not re.findall(r"font-size:\s*\d+px", (WEB / "static" / "app.css").read_text(encoding="utf-8")), "sizes come from --text-* (DS-TYPE-1)"


def test_default_glyph_set_is_the_documented_one():
    ids = set(re.findall(r'<symbol id="([\w-]+)"', (WEB / "static" / "glyphs.svg").read_text(encoding="utf-8")))
    assert ids == GLYPH_IDS
    listed = re.search(r"Ids: `([^`]+)`", (ROOT / "docs" / "DESIGN.md").read_text(encoding="utf-8")).group(1).split()
    assert set(listed) == GLYPH_IDS   # the list in the design system is the list in the file


@pytest.mark.parametrize("kit", KITS)
def test_a_kit_only_replaces_glyphs_and_words_that_exist(kit):
    d = KITS[kit]
    if (d / "glyphs.svg").is_file():
        assert set(re.findall(r'<symbol id="([\w-]+)"', (d / "glyphs.svg").read_text(encoding="utf-8"))) <= GLYPH_IDS
    if (d / "words.json").is_file():
        assert set(json.loads((d / "words.json").read_text(encoding="utf-8"))) <= set(brand_kit.WORDS)


def test_glyphs_merge_by_id_and_fall_back(tmp_path):
    (tmp_path / "theme.css").write_text(":root{}")
    plain = brand_kit.glyph_sprite(tmp_path)   # its own kit, no glyphs.svg: the plain set, not Bearings' chart symbols
    assert plain.count("<symbol") == len(GLYPH_IDS) and 'id="check-in"' in plain
    (tmp_path / "glyphs.svg").write_text('<svg><symbol id="check-in" viewBox="0 0 24 24"><path d="M1 1h22"/></symbol><symbol id="not-a-page"><path d="M0 0"/></symbol></svg>')
    brand_kit._cache.clear()
    merged = brand_kit.glyph_sprite(tmp_path)
    assert 'd="M1 1h22"' in merged and "not-a-page" not in merged and merged.count("<symbol") == len(GLYPH_IDS)
    brand_kit._cache.clear()
    assert brand_kit.glyph_sprite(tmp_path / "missing") == brand_kit.glyph_sprite(brand_kit.BRAND_DEFAULT)   # no kit: Bearings' own


def test_words_fall_back_and_ignore_what_is_not_a_flourish(tmp_path):
    brand_kit._cache.clear()
    assert brand_kit.words(tmp_path / "missing")["checkin_eyebrow"] == "Position at month's end"   # no kit: Bearings' words
    (tmp_path / "theme.css").write_text(":root{}")
    assert brand_kit.words(tmp_path) == brand_kit.WORDS   # its own kit, no words.json: the plain words
    (tmp_path / "words.json").write_text(json.dumps({"name": "The Red Book", "close_button": "Seal the month", "tagline": ""}))
    brand_kit._cache.clear()
    w = brand_kit.words(tmp_path)
    assert w["name"] == "The Red Book" and "close_button" not in w and w["tagline"] == brand_kit.WORDS["tagline"]
    (tmp_path / "words.json").write_text("{not json")
    brand_kit._cache.clear()
    assert brand_kit.words(tmp_path) == brand_kit.WORDS


def test_a_missing_mark_cut_falls_back_to_the_kits_own_mark(tmp_path):
    (tmp_path / "mark.svg").write_text("<svg/>")
    for cut in ("mark-small.svg", "mark-dark.svg", "mark-small-dark.svg"):
        assert brand_kit.find(cut, tmp_path) == tmp_path / "mark.svg"
    (tmp_path / "mark-small.svg").write_text("<svg/>")
    assert brand_kit.find("mark-small-dark.svg", tmp_path) == tmp_path / "mark-small.svg"
    assert brand_kit.find("mark-small.svg", tmp_path / "missing") == brand_kit.BRAND_DEFAULT / "mark-small.svg"
    assert brand_kit.find("nothing.svg", tmp_path) is None


def test_the_shell_draws_the_system(client):
    page = client.get("/").text
    assert 'class="admin"' in page and "/brand/glyphs.svg#settings" in page and "/brand/mark-small.svg" in page
    assert page.index('class="admin"') < page.index('aria-label="Pages"')   # administration is apart from the monthly tabs (DS-NAV-2)
    sprite = client.get("/brand/glyphs.svg")
    assert sprite.status_code == 200 and sprite.headers["content-type"].startswith("image/svg+xml") and sprite.text.count("<symbol") == len(GLYPH_IDS)
    assert client.get("/brand/mark-small-dark.svg").status_code == 200 and client.get("/brand/words.json").status_code == 200


def test_a_page_that_does_not_exist_keeps_the_header_and_a_way_back(client):
    page = client.get("/no-such-page", headers={"accept": "text/html"})   # DS-ERROR-1
    assert page.status_code == 404 and "That page does not exist" in page.text and 'aria-label="Pages"' in page.text and 'href="/"' in page.text
    api = client.get("/no-such-page", headers={"accept": "application/json"})
    assert api.status_code == 404 and api.json() == {"detail": "Not Found"}


def test_every_page_has_a_main_landmark_and_a_skip_link_first(client):
    page = client.get("/").text   # DS-A11Y-4
    assert page.count("<main") == 1 and page.index('class="skip"') < page.index('class="topbar"')
    assert "Switch to day mode" in page and "Switch between day and night" not in page   # DS-NAV-3
