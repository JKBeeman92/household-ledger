"""Helpers shared by the route modules: templates, flash messages, page rendering and budget-month queries."""
from __future__ import annotations
import datetime as _dt
import re
from pathlib import Path

from fastapi import Request
from pipeline.run_pipeline import build_running
from fastapi.templating import Jinja2Templates

from . import db
from .csrf import csrf_field
from .security import csp_nonce

ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=ROOT / "web" / "templates")
templates.env.globals["csrf_field"] = csrf_field   # every POST form calls this; renders the hidden token input (app/csrf.py)
templates.env.globals["csp_nonce"] = csp_nonce     # every inline <script> carries nonce="{{ csp_nonce(request) }}" (app/security.py)


def flash(request: Request, text: str, kind: str = "ok"):
    # reassign rather than append in place: the session only persists when a key is set, so a flash added to a pending list would be lost
    request.session["flashes"] = [*request.session.get("flashes", []), {"text": text, "kind": kind}]


def _format_settings(household: dict) -> dict:
    """Currency (ISO 4217) and locale (BCP 47) for the page's number formatting; bad values fall back to USD / en-US."""
    cur, loc = str((household or {}).get("currency") or ""), str((household or {}).get("locale") or "")
    return {"currency": cur.upper() if re.fullmatch(r"[A-Za-z]{3}", cur) else "USD",
            "locale": loc if re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", loc) else "en-US"}


def render(request: Request, name: str, user, **ctx):
    flashes = request.session.pop("flashes", []) if hasattr(request, "session") else []
    with db.pool.connection() as conn:
        household = db.get_setting(conn, "household", {})
        # not while a build runs: that build already reads the change, and "Rebuild now" beside "Rebuilding…" contradicts itself
        rebuild_needed = bool(user and user.get("is_editor")) and db.rebuild_needed(conn) and not build_running(conn)
    return templates.TemplateResponse(request, name, {"household": household, "fmt": _format_settings(household), "user": user, "get_flashes": lambda: flashes, "theme_mode": None,
                                                      "rebuild_needed": rebuild_needed, **ctx})


def _span(conn) -> dict:
    """Years covered and the CPI base month, for the page's text."""
    r = conn.execute("SELECT min(budget_month) a, max(budget_month) b FROM source_files").fetchone()
    built = conn.execute("SELECT rows FROM datasets WHERE name='build_meta'").fetchone()   # what the pipeline emitted: the month every real-dollar figure is in
    base = built["rows"][0]["cpi_base_month"] if built and built["rows"] else None
    if not base:   # nothing built yet: say what the next build will use (the pipeline's rule: the cpi_base_month rule, else the latest CPI month)
        rules = db.get_setting(conn, "rules", {}) or {}
        base = rules.get("cpi_base_month") or conn.execute("SELECT max(month) m FROM cpi").fetchone()["m"] or r["b"] or "2026-09"
    bd = _dt.date(int(base[:4]), int(base[5:7]), 1)
    fy, ly = (int(r["a"][:4]) if r["a"] else bd.year), (int(r["b"][:4]) if r["b"] else bd.year)
    return {"first_year": fy, "last_year": ly, "years": ly - fy + 1, "cpi_base_short": bd.strftime("%b %Y"), "cpi_base_long": bd.strftime("%B %Y"),
            "cpi_base_iso": base, "has_data": bool(r["a"]), "first_month": r["a"], "last_month": r["b"]}


def _months(conn):
    return [r["budget_month"] for r in conn.execute("SELECT DISTINCT budget_month FROM source_files ORDER BY 1 DESC").fetchall()]
