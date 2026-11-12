"""Helpers shared by the route modules: templates, flash messages, page rendering and budget-month queries."""
from __future__ import annotations
import datetime as _dt
import re
from pathlib import Path

from fastapi import Request
from jinja2 import pass_context
from pipeline.run_pipeline import build_running
from fastapi.templating import Jinja2Templates

from . import brand_kit, db, formatting
from .csrf import csrf_field
from .security import csp_nonce

ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=ROOT / "web" / "templates")
templates.env.globals["csrf_field"] = csrf_field   # every POST form calls this; renders the hidden token input (app/csrf.py)
templates.env.globals["glyph"] = brand_kit.glyph         # {{ glyph("check-in") }}: a decorative glyph beside its label (docs/DESIGN.md)
templates.env.globals["csp_nonce"] = csp_nonce     # every inline <script> carries nonce="{{ csp_nonce(request) }}" (app/security.py)


def _fmt_of(ctx) -> dict:
    f = ctx.get("fmt")
    return f if isinstance(f, dict) and f.get("currency") else _format_settings(ctx.get("household") or {})


def _register_formatters(env) -> None:
    """{{ x|money }}, {{ x|money(2) }}, {{ x|money(signed=True) }}, {{ d|date }}, {{ d|datetime }}, {{ m|month }}, {{ m|month_short }},
    {{ f|share }}, {{ n|number }}: what a page shows a person, in the household's currency and locale (DS-WORD-6, app/formatting.py)."""
    def money(ctx, v, places=0, signed=False):
        f = _fmt_of(ctx); return formatting.money(v, f["currency"], f["locale"], places, signed)
    def loc(fn):
        return pass_context(lambda ctx, v, *a, **k: fn(v, _fmt_of(ctx)["locale"], *a, **k))
    env.filters["money"] = pass_context(money)
    env.filters["number"] = loc(formatting.number)
    env.filters["plain"] = formatting.plain
    env.filters["share"] = loc(formatting.share)
    env.filters["date"] = loc(formatting.date)
    env.filters["datetime"] = loc(formatting.datetime)
    env.filters["clock"] = loc(formatting.time)
    env.filters["month"] = loc(formatting.month)
    env.filters["month_short"] = loc(formatting.month_short)


_register_formatters(templates.env)


def _decision_text(ctx, d) -> str:
    """'Eating Out was $372: kept', with the amount in the household's currency (#155)."""
    from . import decisions
    f = _fmt_of(ctx)
    return decisions.auto_text(d, lambda v: formatting.money(v, f["currency"], f["locale"]))


templates.env.globals["auto_text"] = pass_context(_decision_text)


def flash(request: Request, text: str, kind: str = "ok"):
    # reassign rather than append in place: the session only persists when a key is set, so a flash added to a pending list would be lost
    request.session["flashes"] = [*request.session.get("flashes", []), {"text": text, "kind": kind}]


def _format_settings(household: dict) -> dict:
    """Currency (ISO 4217) and locale (BCP 47) for the page's number formatting; bad values fall back to USD / en-US."""
    cur, loc = str((household or {}).get("currency") or ""), str((household or {}).get("locale") or "")
    return {"currency": cur.upper() if re.fullmatch(r"[A-Za-z]{3}", cur) else "USD",
            "locale": loc if re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", loc) else "en-US"}


def _words() -> dict:
    """The kit's flourish words; the route module holds the brand directory, so a test that swaps it is honoured."""
    from .routes import brand
    return brand_kit.words(brand.BRAND_DIR)


def render(request: Request, name: str, user, **ctx):
    flashes = request.session.pop("flashes", []) if hasattr(request, "session") else []
    with db.pool.connection() as conn:
        household = db.get_setting(conn, "household", {})
        # not while a build runs: that build already reads the change, and "Rebuild now" beside "Rebuilding…" contradicts itself
        rebuild_needed = bool(user and user.get("is_editor")) and db.rebuild_needed(conn) and not build_running(conn)
    return templates.TemplateResponse(request, name, {"household": household, "fmt": _format_settings(household), "words": _words(), "user": user, "get_flashes": lambda: flashes, "theme_mode": None,
                                                      "rebuild_needed": rebuild_needed, **ctx})


def household_locale() -> str:
    """The household's locale, for copy written in a route; the default when there is no database at hand (a unit test)."""
    try:
        with db.pool.connection() as conn:
            return _format_settings(db.get_setting(conn, "household", {}) or {})["locale"]
    except Exception:
        return "en-US"


def month_text(m: str) -> str:
    """A budget month for a flash or other copy written in a route: "November 2026" in the household's locale (DS-WORD-6)."""
    return formatting.month(m, household_locale())


def number_text(n) -> str:
    """A count for copy written in a route, grouped for the household's locale: 12,345 or 12.345."""
    return formatting.number(n, household_locale())


def _span(conn) -> dict:
    """Years covered and the CPI base month, for the page's text."""
    r = conn.execute("SELECT min(budget_month) a, max(budget_month) b FROM source_files").fetchone()
    built = conn.execute("SELECT rows FROM datasets WHERE name='build_meta'").fetchone()   # what the pipeline emitted: the month every real-dollar figure is in
    base = built["rows"][0]["cpi_base_month"] if built and built["rows"] else None
    if not base:   # nothing built yet: say what the next build will use (the pipeline's rule: the cpi_base_month rule, else the latest CPI month)
        rules = db.get_setting(conn, "rules", {}) or {}
        base = rules.get("cpi_base_month") or conn.execute("SELECT max(month) m FROM cpi").fetchone()["m"] or r["b"] or "2026-09"
    bd = _dt.date(int(base[:4]), int(base[5:7]), 1)
    loc = _format_settings(db.get_setting(conn, "household", {}) or {})["locale"]
    fy, ly = (int(r["a"][:4]) if r["a"] else bd.year), (int(r["b"][:4]) if r["b"] else bd.year)
    return {"first_year": fy, "last_year": ly, "years": ly - fy + 1, "cpi_base_short": formatting.month_short(base, loc), "cpi_base_long": formatting.month(base, loc),
            "cpi_base_iso": base, "has_data": bool(r["a"]), "first_month": r["a"], "last_month": r["b"]}


def _months(conn):
    return [r["budget_month"] for r in conn.execute("SELECT DISTINCT budget_month FROM source_files ORDER BY 1 DESC").fetchall()]
