"""Contrast of the token pairs the app draws (issue #15), for any theme.css: the shipped theme and the example kits in the tests, and a
household's own kit in BRAND_DIR at start and whenever its theme.css changes (issue #110). Text pairs need 4.5:1 (small text); borders,
focus rings and chart marks need 3:1. Add a pair here when app.css or the dashboard starts using a new combination.

Values may be #rgb, #rrggbb, #rrggbbaa, rgb(), hsl(), oklab() or oklch(), or var() chains of them. A value that is none of those (a
color-mix(), a translucent colour, a keyword) is reported as "could not evaluate", never raised. Local @imports inside the kit's own
directory are followed; remote ones (fonts) are not."""
from __future__ import annotations
import logging, math, re
from pathlib import Path

log = logging.getLogger("ledger.brand")

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
MODES = ("dark", "light")


class Unreadable(ValueError):
    """A token whose value is not a colour this module can evaluate."""


# ------------------------------------------------------------------ parsing

# a quoted URL may itself hold ';' (Google Fonts: wght@400;600), so quotes are matched as a pair before the statement's own ';'
_IMPORT = re.compile(r"""@import\s+(?:url\(\s*(?:"([^"]*)"|'([^']*)'|([^)\s]*))\s*\)|"([^"]*)"|'([^']*)')[^;"']*;""")


def _ref(m: re.Match) -> str:
    return next(g for g in m.groups() if g is not None)


def read(path: Path, root: Path | None = None, _seen: set | None = None) -> str:
    """theme.css with its local @imports inlined first (as the browser applies them), staying inside `root` (default: its directory)."""
    root = (root or path.parent).resolve(); seen = _seen if _seen is not None else set()
    p = path.resolve()
    if p in seen:
        return ""
    seen.add(p)
    css = p.read_text(encoding="utf-8")
    out = []
    for m in _IMPORT.finditer(css):
        ref = _ref(m)
        if re.match(r"^[a-z]+:|^//", ref, re.I):
            continue   # remote (a font service): nothing to check
        q = (p.parent / ref).resolve()
        if q.is_file() and q.is_relative_to(root):
            out.append(read(q, root, seen))
    return "\n".join(out + [_IMPORT.sub("", css)])


def _blocks(css: str):
    css = _IMPORT.sub("", re.sub(r"/\*.*?\*/", "", css, flags=re.S))
    return [(sel.strip(), dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body))) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def tokens(css: str, mode: str) -> dict[str, str]:
    """The custom properties in force in `mode`: :root is the dark default, :root[data-theme="light"] adds the light overrides."""
    out: dict[str, str] = {}
    for sel, decls in _blocks(css):
        sels = [s.strip() for s in sel.split(",")]
        if ":root" in sels or (mode == "light" and ':root[data-theme="light"]' in sels):
            out.update({k: v.strip() for k, v in decls.items()})
    return out


def resolve(name: str, toks: dict[str, str]) -> str:
    """Follow var() to a value; KeyError names the missing token."""
    v, seen = toks[f"--{name}"], {f"--{name}"}
    while (m := re.fullmatch(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^)]*))?\)", v)):
        if m.group(1) in seen:
            raise Unreadable(f"var() loop through {m.group(1)}")
        seen.add(m.group(1))
        if m.group(1) in toks:
            v = toks[m.group(1)]
        elif m.group(2):
            v = m.group(2).strip()
        else:
            raise KeyError(m.group(1))
    return v


# ------------------------------------------------------------------ colour

def _num(s: str, pct_of: float = 1.0) -> float:
    s = s.strip()
    return float(s[:-1]) / 100 * pct_of if s.endswith("%") else float(s)


def _args(body: str) -> tuple[list[str], str | None]:
    body = body.replace(",", " ")
    main, _, alpha = body.partition("/")
    return main.split(), (alpha.strip() or None)


def _opaque(alpha: str | None) -> None:
    if alpha is not None and abs(_num(alpha) - 1) > 1e-6:
        raise Unreadable("translucent: its contrast depends on what is behind it")


def _lin(c: float) -> float:   # sRGB 0..1 -> linear (WCAG 2 uses 0.03928)
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _gamma(c: float) -> float:   # linear -> sRGB 0..1, clipped to the gamut
    c = min(1.0, max(0.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _oklab_to_srgb(L: float, a: float, b: float) -> tuple[float, float, float]:
    # Björn Ottosson's OKLab -> linear sRGB matrices (also those of CSS Color 4)
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    r = 4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_
    g = -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_
    bl = -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_
    return _gamma(r), _gamma(g), _gamma(bl)


def rgb(value: str) -> tuple[float, float, float]:
    """A CSS colour as sRGB 0..1; Unreadable when it is not one of the supported forms or is translucent."""
    v = value.strip().lower()
    if (m := re.fullmatch(r"#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})", v)):
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) == 8:
            _opaque(str(int(h[6:], 16) / 255) if h[6:] != "ff" else None)
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    if (m := re.fullmatch(r"(rgba?|hsla?|oklch|oklab)\((.*)\)", v)):
        fn, (parts, alpha) = m.group(1), _args(m.group(2))
        if fn in ("rgb", "rgba", "hsl", "hsla") and alpha is None and len(parts) == 4:
            parts, alpha = parts[:3], parts[3]
        if len(parts) != 3 or any(p == "none" for p in parts):
            raise Unreadable(f"could not read {value}")
        _opaque(alpha)
        try:
            if fn.startswith("rgb"):
                return tuple(min(1.0, max(0.0, _num(p, 255) / 255)) for p in parts)
            if fn.startswith("hsl"):
                h = float(parts[0].removesuffix("deg")) % 360; s, l = _num(parts[1]), _num(parts[2])
                s, l = (s / 100, l / 100) if not parts[1].endswith("%") and s > 1 else (s, l)
                k = lambda n: (n + h / 30) % 12
                a = s * min(l, 1 - l)
                return tuple(l - a * max(-1, min(k(n) - 3, 9 - k(n), 1)) for n in (0, 8, 4))
            L = _num(parts[0])
            if fn == "oklab":
                return _oklab_to_srgb(L, _num(parts[1], 0.4), _num(parts[2], 0.4))
            C, H = _num(parts[1], 0.4), math.radians(float(parts[2].removesuffix("deg")))
            return _oklab_to_srgb(L, C * math.cos(H), C * math.sin(H))
        except ValueError as e:
            raise Unreadable(f"could not read {value}") from e
    raise Unreadable(f"could not evaluate {value}")


def lum(value: str) -> float:
    r, g, b = (_lin(c) for c in rgb(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: str, b: str) -> float:
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ------------------------------------------------------------------ checking

def check(css: str, label: str = "theme.css") -> list[str]:
    """Every problem with a theme, one line each, e.g. `theme.css light: line-strong on surface-300 = 2.41 (< 3.0) for input borders`."""
    out: list[str] = []
    for mode in MODES:
        toks, missing, unreadable = tokens(css, mode), set(), set()
        for fg, bg, need, use in PAIRS:
            vals = []
            for t in (fg, bg):
                try:
                    vals.append(resolve(t, toks)); rgb(vals[-1])
                except KeyError as e:
                    missing.add(str(e).strip("'\"")); vals = None; break
                except Unreadable as e:
                    unreadable.add(f"--{t}: {e}"); vals = None; break
            if vals:
                r = ratio(*vals)
                if r < need:
                    out.append(f"{label} {mode}: {fg} on {bg} = {r:.2f} (< {need}) for {use}")
        out += [f"{label} {mode}: {m if m.startswith('--') else '--' + m} is not defined" for m in sorted(missing)]
        out += [f"{label} {mode}: {u}" for u in sorted(unreadable)]
    return out


_last: dict = {"key": None, "problems": []}


def check_dir(brand_dir: Path) -> list[str]:
    """Check BRAND_DIR/theme.css, logging one WARNING per problem when it is new or has changed since the last check. A missing file is
    no problem (the shipped theme is used, and the tests check it). Never raises: a theme is a reason to warn, not to stop."""
    p = brand_dir / "theme.css"
    try:
        st = p.stat()
    except OSError:
        _last.update(key=None, problems=[]); return []
    key = (str(p), st.st_mtime_ns, st.st_size)
    if key == _last["key"]:
        return _last["problems"]
    try:
        problems = check(read(p, brand_dir), "brand: theme.css")
    except Exception as e:  # noqa: BLE001
        problems = [f"brand: theme.css could not be checked: {e}"]
    for line in problems:
        log.warning("%s", line)
    if not problems:
        log.info("brand: theme.css in %s passes every contrast check", brand_dir)
    _last.update(key=key, problems=problems)
    return problems


def last_problems() -> list[str]:
    return list(_last["problems"])
