"""Household Ledger — FastAPI app. Routes: dashboard, upload/close-a-month, settings editors, JSON datasets, OIDC login."""
from __future__ import annotations
import csv, io, json, logging
from pathlib import Path

from fastapi import FastAPI, Request, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from . import db, auth, review
from pipeline.run_pipeline import ingest, reconcile, rebuild, norm

log = logging.getLogger("ledger")
ROOT = Path(__file__).resolve().parent
BRAND_DEFAULT = ROOT / "web" / "static" / "brand"
BRAND_DIR = Path(__import__("os").environ.get("BRAND_DIR", "/data/brand"))

app = FastAPI(title="Household Ledger", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret or "dev-only-insecure-secret-change-me", https_only=settings.base_url.startswith("https"), same_site="lax", max_age=12 * 3600)
app.mount("/static", StaticFiles(directory=ROOT / "web" / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "web" / "templates")


@app.on_event("startup")
def _startup():
    problems = settings.validate()
    for p in problems:
        log.warning("config: %s", p)
    db.init()


# ---------------------------------------------------------------- helpers
def flash(request: Request, text: str, kind: str = "ok"):
    request.session.setdefault("flashes", []).append({"text": text, "kind": kind})


def render(request: Request, name: str, user, **ctx):
    flashes = request.session.pop("flashes", []) if hasattr(request, "session") else []
    with db.pool.connection() as conn:
        household = db.get_setting(conn, "household", {})
    return templates.TemplateResponse(request, name, {"household": household, "user": user, "get_flashes": lambda: flashes, "theme_mode": None, **ctx})


# ---------------------------------------------------------------- brand (overridable)
@app.get("/brand/{path:path}")
def brand(path: str):
    if "/" in path or path.startswith("."):
        raise HTTPException(404)
    for base in (BRAND_DIR, BRAND_DEFAULT):
        p = base / path
        if p.is_file():
            return FileResponse(p)
    if path == "custom.css":
        return Response("/* no custom.css in BRAND_DIR */", media_type="text/css")
    raise HTTPException(404)


# ---------------------------------------------------------------- auth
@app.get("/login", response_class=HTMLResponse)
def login(request: Request, next: str = "/", error: str = ""):
    if auth.current_user(request):
        return RedirectResponse(next if next.startswith("/") else "/")
    return render(request, "login.html", None, next=next, error=error)


@app.get("/auth/start")
async def auth_start(request: Request, next: str = "/"):
    return await auth.start_login(request, next)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    try:
        return await auth.finish_login(request)
    except HTTPException as e:
        return RedirectResponse(f"/login?error={e.detail}", status_code=303)
    except Exception as e:  # noqa: BLE001
        log.exception("oidc callback failed")
        return RedirectResponse(f"/login?error=sign-in failed: {type(e).__name__}", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(auth.logout_url(), status_code=303)


# ---------------------------------------------------------------- pages
def _span(conn) -> dict:
    """Years covered and the CPI base month, for the page's text."""
    r = conn.execute("SELECT min(budget_month) a, max(budget_month) b FROM source_files").fetchone()
    rules = db.get_setting(conn, "rules", {}) or {}
    base = rules.get("cpi_base_month") or (r["b"] or "2026-09")
    import datetime as _dt
    bd = _dt.date(int(base[:4]), int(base[5:7]), 1)
    fy, ly = (int(r["a"][:4]) if r["a"] else bd.year), (int(r["b"][:4]) if r["b"] else bd.year)
    return {"first_year": fy, "last_year": ly, "years": ly - fy + 1, "cpi_base_short": bd.strftime("%b %Y"), "cpi_base_long": bd.strftime("%B %Y")}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user=Depends(auth.require_user)):
    with db.pool.connection() as conn:
        span = _span(conn)
    return render(request, "dashboard.html", user, active="dashboard", span=span)


def _months(conn):
    return [r["budget_month"] for r in conn.execute("SELECT DISTINCT budget_month FROM source_files ORDER BY 1 DESC").fetchall()]


@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request, user=Depends(auth.require_user)):
    with db.pool.connection() as conn:
        files = conn.execute("SELECT budget_month, kind, filename, row_count, uploaded_at FROM source_files ORDER BY budget_month DESC, kind").fetchall()
        months = _months(conn)
        last = conn.execute("SELECT * FROM build_log ORDER BY id DESC LIMIT 1").fetchone()
    return render(request, "upload.html", user, active="upload", files=files, months=months, last_build=last, review_enabled=bool(settings.anthropic_api_key))


@app.post("/upload")
async def upload(request: Request, files: list[UploadFile] = File(...), user=Depends(auth.require_user)):
    stored, errors = [], []
    with db.pool.connection() as conn:
        for f in files:
            try:
                info = ingest(conn, f.filename, await f.read(), uploaded_by=user.get("email", ""))
                stored.append(f"{info['budget_month']} {info['kind']} ({info['rows']} rows)")
            except Exception as e:  # noqa: BLE001
                conn.rollback(); errors.append(f"{f.filename}: {e}")
        conn.commit()
        if stored:
            try:
                res = rebuild(conn)
                rec = res["reconciliation"]
                flash(request, f"Stored {', '.join(stored)}. Rebuilt: {res['summary']}.", "ok" if rec["clean"] else "bad")
                for p in rec.get("problems", [])[:10]:
                    flash(request, f"Reconciliation: {p}", "bad")
            except Exception as e:  # noqa: BLE001
                flash(request, f"Files stored but rebuild failed: {str(e)[:800]}", "bad")
    for e in errors:
        flash(request, e, "bad")
    return RedirectResponse("/upload", status_code=303)


@app.post("/rebuild")
def rebuild_now(request: Request, user=Depends(auth.require_user)):
    with db.pool.connection() as conn:
        try:
            res = rebuild(conn)
            flash(request, f"Rebuilt: {res['summary']}.", "ok" if res["reconciliation"]["clean"] else "bad")
        except Exception as e:  # noqa: BLE001
            flash(request, f"Rebuild failed: {str(e)[:800]}", "bad")
    return RedirectResponse("/upload", status_code=303)


@app.post("/review/generate")
def review_generate(request: Request, month: str = Form(...), user=Depends(auth.require_user)):
    with db.pool.connection() as conn:
        try:
            res = review.generate(conn, month)
            flash(request, f"Review written for {month} ({len(res['sections'])} sections). See the dashboard's Last month tab.")
        except Exception as e:  # noqa: BLE001
            conn.rollback(); flash(request, f"Review failed: {str(e)[:800]}", "bad")
    return RedirectResponse("/upload", status_code=303)


# ---------------------------------------------------------------- settings editors
EDITORS = {
    "mapping": {"title": "Item mapping", "help": "CSV: Item, CanonicalItem, Category, Flow, Note. Flow is one of income, expense, debt, transfer, plug, zero, exclude, verify. Every EveryDollar item name must appear here or the build reports it UNMAPPED."},
    "goals": {"title": "Goals", "help": "JSON list of {key, value, unit, label, note}. The Goals tab and the month-close review read these. value may be null when not yet known."},
    "events": {"title": "Life events", "help": "JSON list of {m: \"YYYY-MM\", label}. Drawn as markers on the timeline charts."},
    "cpi": {"title": "CPI index", "help": "CSV: month, cpi, source. Used for the real-dollars toggle. Append the newest month from FRED CPIAUCSL; mark estimates in source."},
    "rules": {"title": "Pipeline rules", "help": "JSON. Household-specific classification: cpi_base_month, people, income_by_person, payer_prefix_by_person, payer_items_by_person, employers, employer_before, debt_plug_until, transfer_item_words."},
    "household": {"title": "Household", "help": "JSON: name, title, currency. Shown in the header and page titles."},
}


def _editor_body(conn, key: str) -> str:
    if key == "mapping":
        out = io.StringIO(); w = csv.writer(out); w.writerow(["Item", "CanonicalItem", "Category", "Flow", "Note"])
        for r in conn.execute("SELECT item, canonical_item, category, flow, note FROM item_mapping ORDER BY category, canonical_item, item").fetchall():
            w.writerow([r["item"], r["canonical_item"], r["category"], r["flow"], r["note"]])
        return out.getvalue()
    if key == "cpi":
        out = io.StringIO(); w = csv.writer(out); w.writerow(["month", "cpi", "source"])
        for r in conn.execute("SELECT month, cpi, source FROM cpi ORDER BY month").fetchall():
            w.writerow([r["month"], r["cpi"], r["source"]])
        return out.getvalue()
    if key == "goals":
        rows = conn.execute("SELECT key, value, unit, label, note FROM goals ORDER BY key").fetchall()
        return json.dumps([{**r, "value": float(r["value"]) if r["value"] is not None else None} for r in rows], indent=1)
    if key == "events":
        return json.dumps([{"m": r["month"], "label": r["label"]} for r in conn.execute("SELECT month, label FROM life_events ORDER BY month, label").fetchall()], indent=1)
    return json.dumps(db.get_setting(conn, key, {}), indent=1)


def _save_editor(conn, key: str, body: str) -> str:
    if key == "mapping":
        rows = list(csv.DictReader(io.StringIO(body)))
        need = {"Item", "CanonicalItem", "Category", "Flow"}
        if not rows or not need.issubset(rows[0].keys()):
            raise ValueError("mapping needs columns Item, CanonicalItem, Category, Flow, Note")
        bad = [r["Item"] for r in rows if r["Flow"] not in {"income", "expense", "debt", "transfer", "plug", "zero", "exclude", "verify"}]
        if bad:
            raise ValueError(f"bad Flow on: {', '.join(bad[:5])}")
        conn.execute("DELETE FROM item_mapping")
        conn.cursor().executemany("INSERT INTO item_mapping (item,canonical_item,category,flow,note) VALUES (%s,%s,%s,%s,%s)",
                                  [(norm(r["Item"]), r["CanonicalItem"], r["Category"], r["Flow"], r.get("Note", "") or "") for r in rows])
        return f"{len(rows)} mapping rows"
    if key == "cpi":
        rows = list(csv.DictReader(io.StringIO(body)))
        conn.execute("DELETE FROM cpi")
        conn.cursor().executemany("INSERT INTO cpi VALUES (%s,%s,%s)", [(r["month"], r["cpi"], r.get("source", "")) for r in rows])
        return f"{len(rows)} CPI months"
    data = json.loads(body)
    if key == "goals":
        conn.execute("DELETE FROM goals")
        conn.cursor().executemany("INSERT INTO goals (key,value,unit,label,note) VALUES (%s,%s,%s,%s,%s)", [(g["key"], g.get("value"), g.get("unit", ""), g["label"], g.get("note", "")) for g in data])
        return f"{len(data)} goals"
    if key == "events":
        conn.execute("DELETE FROM life_events")
        conn.cursor().executemany("INSERT INTO life_events VALUES (%s,%s)", [(e["m"], e["label"]) for e in data])
        return f"{len(data)} events"
    if key in ("rules", "household"):
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        db.put_setting(conn, key, data)
        return key
    raise ValueError("unknown editor")


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user=Depends(auth.require_user)):
    with db.pool.connection() as conn:
        editors = [{"key": k, "body": _editor_body(conn, k), **v} for k, v in EDITORS.items()]
    return render(request, "settings.html", user, active="settings", editors=editors)


@app.post("/settings/{key}")
def settings_save(request: Request, key: str, body: str = Form(...), user=Depends(auth.require_user)):
    if key not in EDITORS:
        raise HTTPException(404)
    with db.pool.connection() as conn:
        try:
            what = _save_editor(conn, key, body); conn.commit()
            flash(request, f"Saved {what}. Rebuild from the Close-a-month page to apply.")
        except Exception as e:  # noqa: BLE001
            conn.rollback(); flash(request, f"Not saved — {e}", "bad")
    return RedirectResponse("/settings", status_code=303)


# ---------------------------------------------------------------- JSON
@app.get("/api/datasets")
def api_datasets(user=Depends(auth.require_user_api)):
    with db.pool.connection() as conn:
        rows = conn.execute("SELECT name, rows FROM datasets").fetchall()
    return JSONResponse({r["name"]: r["rows"] for r in rows})


@app.get("/api/dataset/{name}")
def api_dataset(name: str, user=Depends(auth.require_user_api)):
    with db.pool.connection() as conn:
        r = conn.execute("SELECT rows FROM datasets WHERE name=%s", (name,)).fetchone()
    if not r:
        raise HTTPException(404)
    return JSONResponse(r["rows"])


@app.get("/api/status")
def api_status(user=Depends(auth.require_user_api)):
    with db.pool.connection() as conn:
        months = _months(conn)
        last = conn.execute("SELECT started_at, finished_at, ok, summary FROM build_log ORDER BY id DESC LIMIT 1").fetchone()
    return {"months": len(months), "first": months[-1] if months else None, "last": months[0] if months else None, "last_build": last}


@app.get("/healthz")
def healthz():
    with db.pool.connection() as conn:
        conn.execute("SELECT 1")
    return {"ok": True}
