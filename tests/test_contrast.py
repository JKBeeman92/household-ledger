"""WCAG contrast of the token pairs the app actually draws (issue #15), for the shipped theme in both modes. The pairs and the arithmetic
live in app/brand_check.py, which also checks a household's own BRAND_DIR kit at start (#110), so there is one list of pairs.
Ratios are computed from the theme files themselves, so a theme edit that regresses fails here."""
from __future__ import annotations

import pytest

from conftest import ROOT
from app.brand_check import PAIRS, check, ratio, read, resolve, tokens

THEMES = {"bearings": ROOT / "app" / "web" / "static" / "brand" / "theme.css",
          "red-book (example kit)": ROOT / "examples" / "brand-kits" / "red-book" / "theme.css",
          "war-of-the-ring (example kit)": ROOT / "examples" / "brand-kits" / "war-of-the-ring" / "theme.css"}


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("mode", ["dark", "light"])
def test_token_pairs_meet_wcag(theme, mode):
    toks = tokens(read(THEMES[theme]), mode)
    bad = []
    for fg, bg, need, use in PAIRS:
        r = ratio(resolve(fg, toks), resolve(bg, toks))
        if r < need:
            bad.append(f"{fg} on {bg} = {r:.2f} (< {need}) for {use}")
    assert not bad, f"{theme}/{mode}: " + "; ".join(bad)


@pytest.mark.parametrize("theme", THEMES)
def test_shipped_themes_pass_the_start_up_check(theme):
    assert check(read(THEMES[theme])) == []


@pytest.mark.parametrize("theme", THEMES)
def test_theme_defines_text_variant_of_accent_2(theme):
    # a theme must define the token in both modes (docs/BRANDING.md); resolve() raises KeyError when it is missing
    for mode in ("dark", "light"):
        resolve("accent-2-text", tokens(read(THEMES[theme]), mode))


def test_ratio_helper():
    assert round(ratio("#000000", "#ffffff"), 1) == 21.0
    assert round(ratio("#777777", "#ffffff"), 2) == 4.48
