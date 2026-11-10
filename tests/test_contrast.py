"""WCAG contrast of the token pairs the app actually draws (issue #15), for the shipped theme in both modes.
Text pairs need 4.5:1 (small text); borders, focus rings and chart marks need 3:1. Add a pair here when app.css or the dashboard
starts using a new combination. Ratios are computed from the theme files themselves, so a theme edit that regresses fails here."""
from __future__ import annotations
import re

import pytest

from conftest import ROOT

THEMES = {"verdigris": ROOT / "app" / "web" / "static" / "brand" / "theme.css"}

TEXT = 4.5
GRAPHIC = 3.0
# (foreground token, background token, minimum ratio, where it is used)
PAIRS = [
    ("ink", "surface-100", TEXT, "body text"), ("ink", "surface-200", TEXT, "panel text"), ("ink", "surface-300", TEXT, "inputs, popovers"),
    ("ink-muted", "surface-100", TEXT, "secondary text"), ("ink-muted", "surface-200", TEXT, "notes, labels"), ("ink-muted", "surface-300", TEXT, "popover labels"),
    ("ink-faint", "surface-100", TEXT, "footer"),
    ("accent", "surface-100", TEXT, "links"), ("accent", "surface-200", TEXT, "links in panels"),
    ("on-accent", "accent", TEXT, "primary button, selected tab, current nav"), ("on-accent", "accent-hover", TEXT, "primary button hover"),
    ("accent-2-text", "surface-100", TEXT, "eyebrow"), ("accent-2-text", "surface-200", TEXT, "eyebrow, 'how to read this' in panels"),
    ("success", "surface-200", TEXT, "tile deltas"), ("warning", "surface-200", TEXT, "filled rows"), ("danger", "surface-200", TEXT, "negative amounts"),
    ("focus", "surface-100", GRAPHIC, "focus ring"), ("focus", "surface-200", GRAPHIC, "focus ring in panels"), ("focus", "surface-300", GRAPHIC, "focus ring on inputs"),
    ("line-strong", "surface-100", GRAPHIC, "button borders"), ("line-strong", "surface-300", GRAPHIC, "input borders"),
    ("accent-2", "surface-200", GRAPHIC, "dotted <dfn> underline"),
] + [(f"series-{i}", "surface-200", GRAPHIC, "chart marks") for i in range(1, 9)]


def _blocks(css: str):
    css = re.sub(r"/\*.*?\*/|@import\s+url\([^)]*\)[^;]*;", "", css, flags=re.S)
    return [(sel.strip(), dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body))) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def tokens(theme: str, mode: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for sel, decls in _blocks(THEMES[theme].read_text()):
        sels = [s.strip() for s in sel.split(",")]
        if ":root" in sels or (mode == "light" and ':root[data-theme="light"]' in sels):
            out.update({k: v.strip() for k, v in decls.items()})
    return out


def resolve(name: str, toks: dict[str, str]) -> str:
    v = toks[f"--{name}"]
    while (m := re.fullmatch(r"var\((--[\w-]+)\)", v)):
        v = toks[m.group(1)]
    return v


def lum(hexv: str) -> float:
    h = hexv.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a: str, b: str) -> float:
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("mode", ["dark", "light"])
def test_token_pairs_meet_wcag(theme, mode):
    toks = tokens(theme, mode)
    bad = []
    for fg, bg, need, use in PAIRS:
        r = ratio(resolve(fg, toks), resolve(bg, toks))
        if r < need:
            bad.append(f"{fg} on {bg} = {r:.2f} (< {need}) for {use}")
    assert not bad, f"{theme}/{mode}: " + "; ".join(bad)


@pytest.mark.parametrize("theme", THEMES)
def test_theme_defines_text_variant_of_accent_2(theme):
    # a theme must define the token in both modes (docs/BRANDING.md); resolve() raises KeyError when it is missing
    for mode in ("dark", "light"):
        resolve("accent-2-text", tokens(theme, mode))


def test_ratio_helper():
    assert round(ratio("#000000", "#ffffff"), 1) == 21.0
    assert round(ratio("#777777", "#ffffff"), 2) == 4.48
