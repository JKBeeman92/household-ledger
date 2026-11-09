"""Helpers shared by the route modules: templates, flash messages, page rendering and budget-month queries."""
from __future__ import annotations
import datetime as _dt
from pathlib import Path

from fastapi import Request
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


def render(request: Request, name: str, user, **ctx):
    flashes = request.session.pop("flashes", []) if hasattr(request, "session") else []
    with db.pool.connection() as conn:
        household = db.get_setting(conn, "household", {})
    return templates.TemplateResponse(request, name, {"household": household, "user": user, "get_flashes": lambda: flashes, "theme_mode": None, **ctx})


def _span(conn) -> dict:
    """Years covered and the CPI base month, for the page's text."""
    r = conn.execute("SELECT min(budget_month) a, max(budget_month) b FROM source_files").fetchone()
    rules = db.get_setting(conn, "rules", {}) or {}
    base = rules.get("cpi_base_month") or (r["b"] or "2026-09")
    bd = _dt.date(int(base[:4]), int(base[5:7]), 1)
    fy, ly = (int(r["a"][:4]) if r["a"] else bd.year), (int(r["b"][:4]) if r["b"] else bd.year)
    return {"first_year": fy, "last_year": ly, "years": ly - fy + 1, "cpi_base_short": bd.strftime("%b %Y"), "cpi_base_long": bd.strftime("%B %Y")}


def _months(conn):
    return [r["budget_month"] for r in conn.execute("SELECT DISTINCT budget_month FROM source_files ORDER BY 1 DESC").fetchall()]
