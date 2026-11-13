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


def test_dashboard_charts_draw_fixes_courses_and_waypoints():
    """DS-CHART-1, 2, 3 (#191): line charts are fixes on a track (HL.fixes), goals are waypoints in --accent-2 (HL.waypoint); no chart
    draws a filled area or a dashed reference line of its own any more."""
    page = (ROOT / "app" / "web" / "templates" / "dashboard.html").read_text()
    assert "const fixes = (svg, pts, col" in page and "const waypoint = (svg, x1, x2, yv, label" in page and "const waypoints = (svg, x1, x2, items)" in page and "var(--accent-2)" in page
    assert "d3.area()" not in page and "stroke-dasharray', '5 4'" not in page and "url(#hl-inc)" not in page
    for chart in ("monthly", "goal-lines", "ef-lines", "dti-lines", "nw-chart"):
        start = page.index(f"dash.draw('{chart}'"); body = page[start:page.index("\ndash.draw(", start + 10)]
        assert "HL.fixes(" in body, chart


def test_templates_set_no_inline_style_except_a_custom_property_that_carries_data():
    """DS-SPACE-1: a page spaces itself with the classes in app.css; the dashboard's styles live there too (#191), and it has no <style> block."""
    bad = {}
    assert "<style" not in (WEB / "templates" / "dashboard.html").read_text(encoding="utf-8")
    for path in sorted((WEB / "templates").rglob("*.html")):
        found = [m for m in re.findall(r'style="([^"]*)"', path.read_text(encoding="utf-8")) if not m.startswith("--")]
        if found:
            bad[path.name] = found
    assert not bad, f"inline style: use a class from app.css (DS-SPACE-1): {bad}"


def test_dashboard_sheets_stamps_glyphs_and_accent_2():
    """#191: chart cards are sheets and tiles stay flat (DS-SHEET-1, 2), verdicts are stamps (DS-STATUS-1), the figure glyphs are placed
    and hidden from assistive technology (DS-GLYPH-1, 2), and --accent-2 marks only destinations (DS-COLOR-2)."""
    css = (WEB / "static" / "app.css").read_text(encoding="utf-8")
    page = (WEB / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert re.search(r"\.hl \.card:not\(\.tile\) \{ border: 2px solid var\(--ink\); box-shadow: inset", css)
    assert not re.search(r"\.hl \.chip[ .:{,]", css) and "border-radius: 999px" not in css   # no round pill left
    assert "HL.stamp" in page and "'status-chip'" in page and "className = 'chip" not in page
    for g in ("savings-rate", "net-worth", "emergency-fund"):
        assert f"glyph('{g}')" in page, g
    dash_css = css[css.index("The dashboard page (#dash-root.hl)"):]
    lines = [ln for ln in dash_css.splitlines() if "--accent-2" in ln and not ln.lstrip().startswith("/*")]
    assert all(".key-mark.wp" in ln or ".eyebrow" in ln for ln in lines), lines   # destinations (the waypoint key) and eyebrows only


def test_the_spacing_classes_the_pages_use_are_built_from_tokens():
    css = (WEB / "static" / "app.css").read_text(encoding="utf-8")
    for cls in ("mt-0", "mt-2", "mt-3", "mt-4", "mb-0", "mb-2", "mb-3", "ml-2", "ml-4", "m-0", "measure", "page-title", "edge", "edge-note", "filterbox", "err"):
        assert re.search(rf"\.{cls}\b[^{{]*{{", css), cls
    assert re.search(r"\.measure\s*{\s*max-width:\s*75ch", css)   # DS-TYPE-4
    used = set()
    for path in (WEB / "templates").rglob("*.html"):
        for c in re.findall(r'class="([^"{}]*)"', path.read_text(encoding="utf-8")):
            used |= {x for x in c.split() if re.fullmatch(r"m[tbl]?-\d", x)}
    assert used <= {"mt-0", "mt-2", "mt-3", "mt-4", "mb-0", "mb-2", "mb-3", "ml-2", "ml-4", "m-0"}, used   # no class the stylesheet lacks


def test_no_route_flashes_not_saved_with_a_dash():
    for path in sorted((ROOT / "app" / "routes").glob("*.py")):
        assert "Not saved —" not in path.read_text(encoding="utf-8"), f"{path.name}: write \"Not saved: …\""


def test_dashboard_motion_is_on_the_tokens_and_a_missing_goal_figure_has_no_verdict():
    """DS-MOTION-1: no dashboard animation runs longer than --dur-slow (they read the token, never a typed ms). And a gauge whose
    figure or goal is null is not drawn as on track: Number(null) is 0."""
    import re
    css = (ROOT / "app" / "web" / "static" / "app.css").read_text()
    assert not re.search(r"animation:\s*hl-[\w-]+\s+\d+m?s", css)
    dash = (ROOT / "app" / "web" / "templates" / "dashboard.html").read_text()
    assert "x == null || x === '' ? NaN : Number(x)" in dash


def test_display_size_is_a_token_every_kit_sets_and_the_dashboard_types_no_size():
    """#191b: the headline figure takes --text-display (DESIGN.md section 3), set by every kit; no size is a multiple of a token, no weight a number."""
    css = (WEB / "static" / "app.css").read_text(encoding="utf-8")
    for name, d in KITS.items():
        assert re.search(r"--text-display:\s*\d+px", (d / "theme.css").read_text(encoding="utf-8")), name
    assert "--text-display" in css and "var(--text-display)" in css and "--text-display" in (ROOT / "docs" / "DESIGN.md").read_text(encoding="utf-8")
    dash = css[css.index("The dashboard page (#dash-root.hl)"):]
    assert not re.search(r"font-size:\s*calc\(var\(--text", dash) and not re.search(r"font-weight:\s*\d", dash), "a size or weight of its own (DS-TYPE-1)"
    page = (WEB / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert not re.search(r'class="glyph[ "]', page) and "key-mark" in page   # .glyph is the SVG set; a key's marks are .key-mark, hidden from assistive technology
    assert all('aria-hidden="true"' in tag for tag in re.findall(r'<span class="key-mark[^>]*>', page))


def test_chart_findings_are_read_into_the_text_alternative():
    """DS-CHART-5: a chart's one-line finding is drawn from its data and a11y.js adds it to the chart's aria-label."""
    page = (WEB / "templates" / "dashboard.html").read_text(encoding="utf-8")
    for n in ("goal", "ef", "dti", "debts", "nw", "net", "lm-var", "lm-avg"):
        assert f'data-draw="fd-{n}"' in page and f"finding('{n}'" in page, n
    assert "querySelector('.finding')" in (WEB / "static" / "a11y.js").read_text(encoding="utf-8")


def test_every_figures_table_tells_its_own_chart_the_span():
    """DS-CHART-5: a table named apart from its chart (nw, ef, dti) passes the chart's name, so the chart's text alternative reads the
    data's range and not the axis ticks."""
    import re
    page = (ROOT / "app" / "web" / "templates" / "dashboard.html").read_text()
    charts = set(re.findall(r'class="chart[^"]*" data-draw="([^"]+)"', page))
    for m in re.finditer(r"^table\('([^']+)',.*$", page, re.M):
        named = re.search(r", '([a-z-]+)'\);", m.group(0))
        target = named.group(1) if named else m.group(1)
        assert target in charts or m.group(1) == "radar", (m.group(1), target)


def test_every_table_scroller_is_a_named_focusable_region():
    """DS-FOCUS-1, DS-A11Y-1: a .tablewrap scrolls sideways on a phone, so a keyboard has to reach it and a screen reader has to name it."""
    import re
    bad = []
    for path in sorted((WEB / "templates").rglob("*.html")):
        for tag in re.findall(r'<div class="tablewrap"[^>]*>', path.read_text(encoding="utf-8")):
            if 'role="region"' not in tag or 'tabindex="0"' not in tag or not re.search(r'aria-label(ledby)?="', tag):
                bad.append(f"{path.name}: {tag}")
    assert not bad, bad


def test_no_line_comment_swallows_code_on_the_dashboard():
    """A `//` comment runs to the end of the line, so it must not sit in front of more code on the same line (it hid the gauges once)."""
    import re
    page = (ROOT / "app" / "web" / "templates" / "dashboard.html").read_text()
    bad = [ln for ln in page.splitlines() if re.search(r";\s+//[^\n]*;\s*(const|let|var|el\.|track\.)\s", ln)]
    assert not bad, bad[:3]
