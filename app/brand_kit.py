"""What a brand kit supplies beyond its theme.css (docs/DESIGN.md, "The kit contract"): marks in two cuts and two modes, glyphs that
replace the default set by id, and the flourish words. Each has a fallback, so a kit that is only a theme.css still works.

A kit that brings its own theme.css is its own kit: what it leaves out falls back to the plain defaults, never to Bearings' chart
symbols or wording. A directory with no theme.css (a custom.css, a mark) is dressing on Bearings and keeps Bearings' glyphs and words."""
from __future__ import annotations
import json, logging, os, re
from pathlib import Path

from markupsafe import Markup, escape

log = logging.getLogger("ledger.brand")
STATIC = Path(__file__).resolve().parent / "web" / "static"
BRAND_DEFAULT = STATIC / "brand"
BRAND_DIR = Path(os.environ.get("BRAND_DIR", "/data/brand"))

# a mark the kit lacks is drawn from the nearest one it has: the small cut before the full seal, the day mark before nothing
MARK_FALLBACKS = {"mark-small.svg": ("mark.svg",), "mark-dark.svg": ("mark.svg",),
                  "mark-small-dark.svg": ("mark-small.svg", "mark-dark.svg", "mark.svg")}
# the plain words every kit starts from; a kit's words.json replaces any of them (control labels are not words a kit can change)
WORDS = {"name": "Bearings", "tagline": "The monthly check-in for household money.",
         "dashboard_eyebrow": "Where the household stands", "checkin_eyebrow": "Month's end",
         "login_line": "Sign in to continue.", "footer": "Every figure is recomputed from the stored files on each rebuild."}
_SYMBOL = re.compile(r'<symbol\b[^>]*\bid="([\w-]+)"[^>]*>.*?</symbol>', re.S)
_cache: dict = {}


def kit_dir(brand_dir: Path | None = None) -> Path:
    """Where glyphs and words come from: BRAND_DIR when it holds a theme of its own, else the shipped kit."""
    d = brand_dir or BRAND_DIR
    return d if (d / "theme.css").is_file() else BRAND_DEFAULT


def find(path: str, brand_dir: Path | None = None) -> Path | None:
    """The file to serve for /brand/<path>: the kit's, else (for a mark) the kit's nearest mark, else the shipped default."""
    d = brand_dir or BRAND_DIR
    for name in (path, *MARK_FALLBACKS.get(path, ())):
        if (d / name).is_file():
            return d / name
    p = BRAND_DEFAULT / path
    return p if p.is_file() else None


def _cached(key: str, path: Path, load):
    try:
        stamp = (str(path), path.stat().st_mtime_ns)
    except OSError:
        stamp = (str(path), None)
    if _cache.get(key, (None, None))[0] != stamp:
        _cache[key] = (stamp, load(path) if stamp[1] is not None else None)
    return _cache[key][1]


def words(brand_dir: Path | None = None) -> dict:
    def load(p: Path):
        try:
            got = json.loads(p.read_text(encoding="utf-8"))
            return {k: str(v) for k, v in got.items() if k in WORDS and isinstance(v, str) and v.strip()} if isinstance(got, dict) else {}
        except (OSError, ValueError) as e:
            log.warning("brand: words.json could not be read (%s); the plain words are used", e)
            return {}
    return {**WORDS, **(_cached("words", kit_dir(brand_dir) / "words.json", load) or {})}


def glyph_sprite(brand_dir: Path | None = None) -> str:
    """The default glyph set with the kit's symbols laid over it by id."""
    read = lambda p: {m.group(1): m.group(0) for m in _SYMBOL.finditer(p.read_text(encoding="utf-8"))}
    base = _cached("glyphs-default", STATIC / "glyphs.svg", read) or {}
    over = _cached("glyphs-kit", kit_dir(brand_dir) / "glyphs.svg", read) or {}
    merged = {**base, **{k: v for k, v in over.items() if k in base}}   # a kit replaces glyphs; it does not add destinations
    return '<svg xmlns="http://www.w3.org/2000/svg">\n' + "\n".join(merged.values()) + "\n</svg>\n"


def glyph(name: str, cls: str = "glyph") -> Markup:
    """A glyph for a template: decorative, always beside its label (DS-GLYPH-2)."""
    return Markup(f'<svg class="{escape(cls)}" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><use href="/brand/glyphs.svg#{escape(name)}"/></svg>')
