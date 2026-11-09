import csv, io, json

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db, auth
from ..web_helpers import flash, render
from pipeline.run_pipeline import norm

router = APIRouter()


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


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        editors = [{"key": k, "body": _editor_body(conn, k), **v} for k, v in EDITORS.items()]
    return render(request, "settings.html", user, active="settings", editors=editors)


@router.post("/settings/{key}")
def settings_save(request: Request, key: str, body: str = Form(...), user=Depends(auth.require_editor)):
    if key not in EDITORS:
        raise HTTPException(404)
    with db.pool.connection() as conn:
        try:
            what = _save_editor(conn, key, body); conn.commit()
            flash(request, f"Saved {what}. Rebuild from the Close-a-month page to apply.")
        except Exception as e:  # noqa: BLE001
            conn.rollback(); flash(request, f"Not saved — {e}", "bad")
    return RedirectResponse("/settings", status_code=303)
