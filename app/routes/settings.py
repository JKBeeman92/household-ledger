import csv, io, json, re

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response

from .. import db, auth, schema
from ..web_helpers import flash, render
from pipeline.run_pipeline import norm

router = APIRouter()


EDITORS = {
    "mapping": {"title": "Item mapping", "help": "One row per EveryDollar item: Item, CanonicalItem, Category, Flow, Note. Flow is one of income, expense, debt, transfer, plug, zero, exclude, verify. Every EveryDollar item name must appear here or the build reports it UNMAPPED. Fund moves kept as income items (Fund Transfer, End of Year Rebalance) need Flow transfer, or they count as income; money held for someone else and paid out needs exclude. CanonicalItem Paycheck and the categories Subscriptions, Utilities and Insurance are read by name (README, \"Names the pipeline relies on\"); rules name canonical items, not raw ones."},
    "goals": {"title": "Goals", "help": "One row per goal: key, label, value, unit, note. The Goals tab and the month-close review read these. value may be null when not yet known."},
    "events": {"title": "Life events", "help": "One row per event: month (YYYY-MM) and label. Drawn as markers on the timeline charts."},
    "cpi": {"title": "CPI index", "help": "CSV: month, cpi, source. A price index (consumer prices, any base) used for the real-amounts toggle. US households: CPI-U, appending the newest month from FRED CPIAUCSL; mark estimates in source."},
    "rules": {"title": "Pipeline rules", "help": "JSON object of household-specific classification rules. Unknown keys and invalid regular expressions are rejected on save."},
    "household": {"title": "Household", "help": "JSON object: name, title, currency, theme. Shown in the header and page titles."},
}


FLOWS = ("income", "expense", "debt", "transfer", "plug", "zero", "exclude", "verify")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


PIPELINE_INPUTS = {"mapping", "goals", "events", "cpi", "rules"}   # read by the pipeline: the dashboard changes only after a rebuild


def check_mapping_row(r: dict) -> tuple[dict, dict]:
    """Clean one mapping row; returns (row, {field: message})."""
    c = {"item": norm(r.get("item") or r.get("Item") or ""), "canonical_item": norm(r.get("canonical_item") or r.get("CanonicalItem") or ""),
         "category": norm(r.get("category") or r.get("Category") or ""), "flow": (r.get("flow") or r.get("Flow") or "").strip(), "note": (r.get("note") or r.get("Note") or "").strip()}
    err = {k: "required" for k in ("item", "canonical_item", "category") if not c[k]}
    if c["flow"] not in FLOWS:
        err["flow"] = f"bad Flow '{c['flow']}': one of {', '.join(FLOWS)}"
    return c, err


def check_goal_row(r: dict) -> tuple[dict, dict]:
    c = {"key": (r.get("key") or "").strip(), "unit": (r.get("unit") or "").strip(), "label": (r.get("label") or "").strip(), "note": (r.get("note") or "").strip()}
    err = {k: "required" for k in ("key", "label") if not c[k]}
    v = r.get("value")
    if v is None or (isinstance(v, str) and not v.strip()):
        c["value"] = None
    else:
        try:
            c["value"] = float(v)
            if c["value"] != c["value"] or abs(c["value"]) == float("inf"):
                raise ValueError
        except (TypeError, ValueError):
            c["value"] = None; err["value"] = "must be a number or blank"
    return c, err


def check_event_row(r: dict) -> tuple[dict, dict]:
    c = {"m": (r.get("m") or "").strip(), "label": (r.get("label") or "").strip()}
    err = {}
    if not MONTH_RE.match(c["m"]):
        err["m"] = "month must look like YYYY-MM"
    if not c["label"]:
        err["label"] = "required"
    return c, err


def _unique_err(conn, sql: str, params: tuple, err: dict, field: str, msg: str):
    if conn.execute(sql, params).fetchone():
        err[field] = msg


def _row_save(conn, kind: str, f: dict) -> tuple[dict, dict]:
    """Upsert (or rename) one row of a reference table in its own transaction. Returns (clean row, errors); nothing is written when errors."""
    if kind == "mapping":
        c, err = check_mapping_row(f); orig = norm(f.get("original") or "")
        if not err and c["item"] != orig:
            _unique_err(conn, "SELECT 1 FROM item_mapping WHERE item=%s", (c["item"],), err, "item", "already in the mapping")
        if not err:
            if orig and orig != c["item"]:
                conn.execute("DELETE FROM item_mapping WHERE item=%s", (orig,))
            conn.execute("INSERT INTO item_mapping (item,canonical_item,category,flow,note) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (item) DO UPDATE SET canonical_item=EXCLUDED.canonical_item, category=EXCLUDED.category, flow=EXCLUDED.flow, note=EXCLUDED.note, updated_at=now()",
                         (c["item"], c["canonical_item"], c["category"], c["flow"], c["note"]))
    elif kind == "goals":
        c, err = check_goal_row(f); orig = (f.get("original") or "").strip()
        if not err and c["key"] != orig:
            _unique_err(conn, "SELECT 1 FROM goals WHERE key=%s", (c["key"],), err, "key", "already a goal")
        if not err:
            if orig and orig != c["key"]:
                conn.execute("UPDATE goals SET key=%s WHERE key=%s", (c["key"], orig))   # a rename keeps confirmed_at
            conn.execute("INSERT INTO goals (key,value,unit,label,note) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, unit=EXCLUDED.unit, label=EXCLUDED.label, note=EXCLUDED.note, updated_at=now()",
                         (c["key"], c["value"], c["unit"], c["label"], c["note"]))
    else:
        c, err = check_event_row(f); om, ol = (f.get("original_m") or "").strip(), (f.get("original_label") or "").strip()
        if not err and (c["m"], c["label"]) != (om, ol):
            _unique_err(conn, "SELECT 1 FROM life_events WHERE month=%s AND label=%s", (c["m"], c["label"]), err, "label", "that event already exists")
        if not err:
            if om or ol:
                conn.execute("DELETE FROM life_events WHERE month=%s AND label=%s", (om, ol))
            conn.execute("INSERT INTO life_events (month,label) VALUES (%s,%s) ON CONFLICT DO NOTHING", (c["m"], c["label"]))
    return c, err


def _row_delete(conn, kind: str, f: dict) -> int:
    if kind == "mapping":
        return conn.execute("DELETE FROM item_mapping WHERE item=%s", (norm(f.get("original") or ""),)).rowcount
    if kind == "goals":
        return conn.execute("DELETE FROM goals WHERE key=%s", ((f.get("original") or "").strip(),)).rowcount
    return conn.execute("DELETE FROM life_events WHERE month=%s AND label=%s", ((f.get("original_m") or "").strip(), (f.get("original_label") or "").strip())).rowcount


def _mapping_csv(conn) -> str:
    out = io.StringIO(); w = csv.writer(out); w.writerow(["Item", "CanonicalItem", "Category", "Flow", "Note"])
    for r in conn.execute("SELECT item, canonical_item, category, flow, note FROM item_mapping ORDER BY category, canonical_item, item").fetchall():
        w.writerow([r["item"], r["canonical_item"], r["category"], r["flow"], r["note"]])
    return out.getvalue()


def _mapping_import(conn, body: str) -> str:
    """Bulk CSV: every row is checked first and the errors name the line; nothing is written unless all rows are valid. Rows are upserted and
    items absent from the CSV deleted, in one transaction, so the table is never emptied and refilled."""
    reader = csv.DictReader(io.StringIO(body.lstrip("\ufeff")))
    rows = list(reader)
    if not rows or not {"Item", "CanonicalItem", "Category", "Flow"}.issubset(rows[0].keys()):
        raise ValueError("mapping needs columns Item, CanonicalItem, Category, Flow, Note")
    clean, problems, seen = [], [], {}
    for n, r in enumerate(rows, start=2):
        c, err = check_mapping_row({k: v for k, v in r.items() if isinstance(v, str)})
        if not err and c["item"] in seen:
            err["item"] = f"duplicate of line {seen[c['item']]}"
        for f, m in err.items():
            problems.append(f"line {n}: " + (f"bad Flow on: {c['item'] or '(blank)'} ({m})" if f == "flow" else f"{f} {m}" + (f" on: {c['item']}" if c["item"] else "")))
        if not err:
            seen[c["item"]] = n; clean.append(c)
    if problems:
        raise ValueError(f"{len(problems)} problem{'s' * (len(problems) != 1)}: " + "; ".join(problems[:8]) + (f"; and {len(problems) - 8} more" if len(problems) > 8 else ""))
    cur = conn.cursor()
    cur.executemany("INSERT INTO item_mapping (item,canonical_item,category,flow,note) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (item) DO UPDATE SET canonical_item=EXCLUDED.canonical_item, category=EXCLUDED.category, flow=EXCLUDED.flow, note=EXCLUDED.note, updated_at=now()",
                    [(c["item"], c["canonical_item"], c["category"], c["flow"], c["note"]) for c in clean])
    conn.execute("DELETE FROM item_mapping WHERE item <> ALL(%s)", ([c["item"] for c in clean],))
    return f"{len(clean)} mapping rows"


def _cpi_rows(body: str) -> list[tuple[str, str, str]]:
    """Check the whole CPI CSV before anything is deleted: a header with month and cpi, at least one row, month as YYYY-MM, a positive number, no month twice."""
    reader = csv.DictReader(io.StringIO(body.lstrip("\ufeff")))
    if not {"month", "cpi"}.issubset(reader.fieldnames or []):
        raise ValueError("CPI needs a header line month,cpi,source")
    out, problems, seen = [], [], {}
    for r in reader:
        n = reader.line_num
        m, raw = (r.get("month") or "").strip(), (r.get("cpi") or "").strip()
        if not MONTH_RE.match(m):
            problems.append(f"line {n}: month must look like YYYY-MM"); continue
        try:
            v = float(raw)
            if not 0 < v < 1e6 or v != v:
                raise ValueError
        except ValueError:
            problems.append(f"line {n}: cpi must be a positive number"); continue
        if m in seen:
            problems.append(f"line {n}: month {m} repeats line {seen[m]}"); continue
        seen[m] = n; out.append((m, raw, (r.get("source") or "").strip()))
    if not out and not problems:
        problems.append("no CPI rows: the table would be emptied")
    if problems:
        raise ValueError(f"{len(problems)} problem{'s' * (len(problems) != 1)}: " + "; ".join(problems[:8]) + (f"; and {len(problems) - 8} more" if len(problems) > 8 else ""))
    return out


def _editor_body(conn, key: str) -> str:
    if key == "mapping":
        return _mapping_csv(conn)
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
        return _mapping_import(conn, body)
    if key == "cpi":
        rows = _cpi_rows(body)
        base = (db.get_setting(conn, "rules", {}) or {}).get("cpi_base_month")
        if base and base not in {r[0] for r in rows}:
            raise ValueError(f"the pipeline rules name {base} as cpi_base_month, so that month must stay in the CPI table (change the rule first)")
        conn.execute("DELETE FROM cpi")
        conn.cursor().executemany("INSERT INTO cpi VALUES (%s,%s,%s)", rows)
        return f"{len(rows)} CPI months"
    data = json.loads(body)
    if key in ("goals", "events"):
        if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
            raise ValueError(f"{key} must be a JSON list of objects")
        chk = check_goal_row if key == "goals" else check_event_row
        checked = [chk(x) for x in data]
        problems = [f"item {i}: {f} {m}" for i, (_, e) in enumerate(checked, 1) for f, m in e.items()]
        if problems:
            raise ValueError("; ".join(problems[:8]))
        if key == "goals":
            seen = {r["key"]: r["confirmed_at"] for r in conn.execute("SELECT key, confirmed_at FROM goals").fetchall()}   # editing the list must not forget confirmations
            conn.execute("DELETE FROM goals")
            conn.cursor().executemany("INSERT INTO goals (key,value,unit,label,note,confirmed_at) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, unit=EXCLUDED.unit, label=EXCLUDED.label, note=EXCLUDED.note", [(c["key"], c["value"], c["unit"], c["label"], c["note"], seen.get(c["key"])) for c, _ in checked])
        else:
            conn.execute("DELETE FROM life_events")
            conn.cursor().executemany("INSERT INTO life_events VALUES (%s,%s) ON CONFLICT DO NOTHING", [(c["m"], c["label"]) for c, _ in checked])
        return f"{len(data)} {key}"
    if key in ("rules", "household"):
        problems = schema.validate(data, schema.load(key))
        if problems:
            raise ValueError(f"{len(problems)} problem{'s' * (len(problems) != 1)}: " + "; ".join(problems[:8]) + (f"; and {len(problems) - 8} more" if len(problems) > 8 else ""))
        base = data.get("cpi_base_month") if key == "rules" else None
        if base and conn.execute("SELECT 1 FROM cpi WHERE month=%s", (base,)).fetchone() is None:
            raise ValueError(f"cpi_base_month {base} is not a month in the CPI table")
        db.put_setting(conn, key, data)
        return key
    raise ValueError("unknown editor")


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        editors = [{"key": k, "body": _editor_body(conn, k), "fields": schema.help_fields(k) if k in schema.NAMES else [], **v} for k, v in EDITORS.items()]
        rows = {"mapping": conn.execute("SELECT item, canonical_item, category, flow, note FROM item_mapping ORDER BY category, canonical_item, item").fetchall(),
                "goals": [{**r, "value": (f"{r['value']:f}".rstrip("0").rstrip(".") if r["value"] is not None else None)} for r in conn.execute("SELECT key, value, unit, label, note FROM goals ORDER BY key").fetchall()],
                "events": conn.execute("SELECT month AS m, label FROM life_events ORDER BY month, label").fetchall()}
    return render(request, "settings.html", user, active="settings", editors=editors, rows=rows, flows=FLOWS)


@router.get("/settings/mapping.csv")
def mapping_export(user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        body = _mapping_csv(conn)
    return Response(body, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="item_mapping.csv"'})


@router.post("/settings/{kind}/row")
async def settings_row_save(request: Request, kind: str, user=Depends(auth.require_editor_api)):
    if kind not in ("mapping", "goals", "events"):
        raise HTTPException(404)
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    with db.pool.connection() as conn:
        try:
            row, err = _row_save(conn, kind, form)
            if not err:
                db.mark_inputs_changed(conn)
            conn.commit()
        except Exception:  # noqa: BLE001
            conn.rollback(); raise
    return JSONResponse({"ok": not err, "row": row, "errors": err, "rebuild_needed": not err})  # 200 with ok=false: a rejected row is an answer, not a failed request (keeps the console clean)


@router.post("/settings/{kind}/row/delete")
async def settings_row_delete(request: Request, kind: str, user=Depends(auth.require_editor_api)):
    if kind not in ("mapping", "goals", "events"):
        raise HTTPException(404)
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    with db.pool.connection() as conn:
        n = _row_delete(conn, kind, form)
        if n:
            db.mark_inputs_changed(conn)
        conn.commit()
    return JSONResponse({"ok": n > 0, "deleted": n, "rebuild_needed": n > 0}, status_code=200 if n else 404)


@router.post("/settings/{key}")
def settings_save(request: Request, key: str, body: str = Form(...), user=Depends(auth.require_editor)):
    if key not in EDITORS:
        raise HTTPException(404)
    with db.pool.connection() as conn:
        try:
            what = _save_editor(conn, key, body)
            if key in PIPELINE_INPUTS:
                db.mark_inputs_changed(conn)
            conn.commit()
            flash(request, f"Saved {what}." + (" The dashboard shows it after a rebuild: press Rebuild now." if key in PIPELINE_INPUTS else ""))
        except Exception as e:  # noqa: BLE001
            conn.rollback(); flash(request, f"Not saved — {e}", "bad")
    return RedirectResponse(f"/settings#{key}", status_code=303)   # back to the tab that was saved
