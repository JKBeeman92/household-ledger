import csv, io, json, re

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response

from .. import db, auth, schema, mapping_hints, brand_check
from . import brand
from ..web_helpers import flash, render
from pipeline.run_pipeline import norm, unmapped_items

router = APIRouter()


EDITORS = {
    "mapping": {"title": "Item mapping", "help": "Each EveryDollar item gets a canonical name, a category and a flow. Every item must be here, or the build lists it as unmapped.", "help_long": "One row per EveryDollar item: Item, CanonicalItem, Category, Flow, Note. Flow is one of income, expense, debt, transfer, plug, zero, exclude, verify. Every EveryDollar item name must appear here or the build reports it UNMAPPED. Fund moves kept as income items (Fund Transfer, End of Year Rebalance) need Flow transfer, or they count as income; money held for someone else and paid out needs exclude. No name is read literally: the parts names play (which items are paychecks, which categories are recurring bills, and so on) are set under Pipeline rules, Roles (README, \"Roles\"); rules name canonical items, not raw ones."},
    "goals": {"title": "Goals", "help": "One row per goal: key, label, value, unit, note. The Goals tab and the month-close review read these; values are also confirmed each month in the checklist on Close a month. A share is a fraction (0.07) or a percentage (7%)."},
    "events": {"title": "Life events", "help": "One row per event: month (YYYY-MM), label, and optionally its kind (job, move, family, health, purchase, debt, other) and the person it concerns (one of the people in Pipeline rules). Drawn as markers on the timeline charts; the Employer chart shows job events and events with no kind."},
    "cpi": {"title": "CPI index", "help": "CSV: month, cpi, source. A price index (consumer prices, any base) used for the real-amounts toggle. US households: CPI-U, appending the newest month from FRED CPIAUCSL; mark estimates in source."},
    "rules": {"title": "Pipeline rules", "help": "JSON object of household-specific classification rules. Unknown keys and invalid regular expressions are rejected on save."},
    "household": {"title": "Household", "help": "JSON object: name, title, currency, theme. Shown in the header and page titles."},
}


FLOWS = ("income", "expense", "debt", "transfer", "plug", "zero", "exclude", "verify")
FLOW_HELP = {   # one line each, shown beside the Flow choice (what the pipeline does with the row)
    "income": "money coming in: paychecks, refunds, interest",
    "expense": "ordinary spending",
    "debt": "payments on a loan or card",
    "transfer": "a move between your own envelopes or accounts; left out of income and spending",
    "plug": "a balancing entry EveryDollar made; left out of every total",
    "zero": "a placeholder line; kept in the ledger, counted nowhere",
    "exclude": "money held for someone else; left out of the ledger and every chart",
    "verify": "an inflow not yet identified; left out until you confirm it",
}
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


def parse_goal_value(raw, unit: str) -> tuple[float | None, str | None]:
    """A goal's value from what was typed: blank is "not known yet"; "7%" is 0.07; a "share of …" must lie between 0 and 1,
    because the Goals tab and the review read it as a fraction (7 would be 700%)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, None
    text = str(raw).strip().replace(",", "")
    pct = text.endswith("%")
    try:
        v = float(text.rstrip("%").strip())
        if v != v or abs(v) == float("inf") or abs(v) >= 1e10:
            raise ValueError
    except ValueError:
        return None, "must be a number (or a percentage such as 7%) or blank"
    if pct:
        v /= 100
    if (unit or "").lower().startswith("share") and not 0 <= v <= 1:
        return None, f"a share is between 0 and 1; for {v:g}% write {v:g}%" if v > 1 else "a share is between 0 and 1"
    return v, None


def check_goal_row(r: dict) -> tuple[dict, dict]:
    c = {"key": (r.get("key") or "").strip(), "unit": (r.get("unit") or "").strip(), "label": (r.get("label") or "").strip(), "note": (r.get("note") or "").strip()}
    err = {k: "required" for k in ("key", "label") if not c[k]}
    c["value"], problem = parse_goal_value(r.get("value"), c["unit"])
    if problem:
        err["value"] = problem
    return c, err


EVENT_KINDS = ("job", "move", "family", "health", "purchase", "debt", "other")   # db/migrations/0109_life_event_kind.sql; a fixed list (#109)


def check_event_row(r: dict) -> tuple[dict, dict]:
    """{m, label, kind?, person?}: kind is one of EVENT_KINDS or empty, person is free text (checked against the people rule by
    person_warning, which warns but never refuses). Empty kind and person are None."""
    err = {f: "must be text" for f in ("m", "label", "kind", "person") if r.get(f) is not None and not isinstance(r.get(f), str)}
    txt = lambda f: "" if f in err else (r.get(f) or "").strip()
    c = {"m": txt("m"), "label": txt("label"), "kind": txt("kind").lower() or None, "person": txt("person") or None}
    if "m" not in err and not MONTH_RE.match(c["m"]):
        err["m"] = "month must look like YYYY-MM"
    if "label" not in err and not c["label"]:
        err["label"] = "required"
    if c["kind"] and c["kind"] not in EVENT_KINDS:
        err["kind"] = "kind must be one of " + ", ".join(EVENT_KINDS) + ", or empty"
    return c, err


def event_out(r: dict) -> dict:
    """An event as the JSON editor, settings.zip, seeds and events.json carry it: kind and person only when set."""
    return {"m": r["m"], "label": r["label"], **{k: r[k] for k in ("kind", "person") if r.get(k)}}


def person_warning(conn, people: list) -> str:
    """A warning naming any person not in the people rule, or ''. Saved anyway: the rule may simply not be filled in yet."""
    known = set((db.get_setting(conn, "rules", {}) or {}).get("people") or [])
    odd = sorted({p for p in people if p and p not in known})
    return f"{', '.join(odd)} {'is' if len(odd) == 1 else 'are'} not in the people rule (Pipeline rules); saved anyway" if odd else ""


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
            conn.execute("INSERT INTO life_events (month,label,kind,person) VALUES (%s,%s,%s,%s) ON CONFLICT (month,label) DO UPDATE SET kind=EXCLUDED.kind, person=EXCLUDED.person",
                         (c["m"], c["label"], c["kind"], c["person"]))
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


def _problems(problems: list[str]) -> ValueError:
    return ValueError(f"{len(problems)} problem{'s' * (len(problems) != 1)}: " + "; ".join(problems[:8]) + (f"; and {len(problems) - 8} more" if len(problems) > 8 else ""))


def _mapping_rows(body: str, existing: set[str] | None = None) -> list[dict]:
    """Check a mapping CSV and return its clean rows; raises ValueError naming each bad line. With `existing`, an item already in the
    mapping is a problem too (the draft only adds rows, it never overwrites one)."""
    reader = csv.DictReader(io.StringIO(body.lstrip("\ufeff")))
    rows = list(reader)
    if not rows or not {"Item", "CanonicalItem", "Category", "Flow"}.issubset(rows[0].keys()):
        raise ValueError("mapping needs columns Item, CanonicalItem, Category, Flow, Note")
    clean, problems, seen = [], [], {}
    for n, r in enumerate(rows, start=2):
        c, err = check_mapping_row({k: v for k, v in r.items() if isinstance(v, str)})
        if not err and c["item"] in seen:
            err["item"] = f"duplicate of line {seen[c['item']]}"
        if not err and existing is not None and c["item"] in existing:
            err["item"] = "already in the mapping"
        for f, m in err.items():
            problems.append(f"line {n}: " + (f"bad Flow on: {c['item'] or '(blank)'} ({m})" if f == "flow" else f"{f} {m}" + (f" on: {c['item']}" if c["item"] else "")))
        if not err:
            seen[c["item"]] = n; clean.append(c)
    if problems:
        raise _problems(problems)
    return clean


def _mapping_add(conn, body: str) -> str:
    """Add the rows of a mapping CSV (the reviewed draft) without touching any existing row; all or nothing, like the bulk import."""
    existing = {r["item"] for r in conn.execute("SELECT item FROM item_mapping").fetchall()}
    clean = _mapping_rows(body, existing)
    conn.cursor().executemany("INSERT INTO item_mapping (item,canonical_item,category,flow,note) VALUES (%s,%s,%s,%s,%s)",
                              [(c["item"], c["canonical_item"], c["category"], c["flow"], c["note"]) for c in clean])
    return f"{len(clean)} drafted mapping row{'s' * (len(clean) != 1)}"


def _mapping_list(conn) -> list[dict]:
    return conn.execute("SELECT item, canonical_item, category, flow, note FROM item_mapping ORDER BY category, canonical_item, item").fetchall()


def draft_mapping(conn) -> list[dict]:
    """Draft rows for every item the exports carry that the mapping lacks (#105); see app/mapping_hints.draft."""
    return mapping_hints.draft(unmapped_items(conn), _mapping_list(conn), db.get_setting(conn, "rules", {}) or {})


def draft_csv(rows: list[dict]) -> str:
    out = io.StringIO(); w = csv.writer(out); w.writerow(["Item", "CanonicalItem", "Category", "Flow", "Note"])
    for r in rows:
        w.writerow([r["item"], r["canonical_item"], r["category"], r["flow"], r["note"]])
    return out.getvalue()


def _mapping_import(conn, body: str) -> str:
    """Bulk CSV: every row is checked first and the errors name the line; nothing is written unless all rows are valid. Rows are upserted and
    items absent from the CSV deleted, in one transaction, so the table is never emptied and refilled."""
    clean = _mapping_rows(body)
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
        return json.dumps([event_out(r) for r in conn.execute("SELECT month AS m, label, kind, person FROM life_events ORDER BY month, label").fetchall()], indent=1)
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
            conn.cursor().executemany("INSERT INTO life_events (month,label,kind,person) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                                      [(c["m"], c["label"], c["kind"], c["person"]) for c, _ in checked])
            warn = person_warning(conn, [c["person"] for c, _ in checked])
            return f"{len(data)} {key}" + (f" ({warn})" if warn else "")
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
                "events": conn.execute("SELECT month AS m, label, kind, person FROM life_events ORDER BY month, label").fetchall()}
        categories = sorted({r["category"] for r in rows["mapping"] if r["category"]})
        rules = db.get_setting(conn, "rules", {}) or {}
        household = db.get_setting(conn, "household", {}) or {}
        name_families = mapping_hints.families(rows["mapping"], rules)
        unmapped = len(unmapped_items(conn))
    return render(request, "settings.html", user, active="settings", editors=editors, rows=rows, flows=FLOWS, flow_help=FLOW_HELP,
                  categories=categories, rules=rules, hh=household, name_families=name_families, unmapped=unmapped,
                  brand_problems=brand_check.check_dir(brand.BRAND_DIR), role_fields=ROLE_FIELDS, role_defaults=ROLE_DEFAULTS,
                  event_choices={"kind": [""] + list(EVENT_KINDS)}, people=list(rules.get("people") or []))


@router.get("/settings/mapping.csv")
def mapping_export(user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        body = _mapping_csv(conn)
    return Response(body, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="item_mapping.csv"'})


@router.get("/settings/mapping/draft", response_class=HTMLResponse)
def mapping_draft_page(request: Request, user=Depends(auth.require_editor)):
    """The draft for review (#105): nothing is saved until the editor presses Add."""
    with db.pool.connection() as conn:
        rows = draft_mapping(conn)
    return render(request, "mapping_draft.html", user, active="settings", rows=rows, body=draft_csv(rows) if rows else "")


@router.post("/settings/mapping/draft")
def mapping_draft_save(request: Request, body: str = Form(...), user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        try:
            what = _mapping_add(conn, body)
            db.mark_inputs_changed(conn); conn.commit()
            flash(request, f"Added {what}. The dashboard shows them after a rebuild: press Rebuild now.")
        except Exception as e:  # noqa: BLE001
            conn.rollback(); flash(request, f"Not saved — {e}", "bad")
            return RedirectResponse("/settings/mapping/draft", status_code=303)
    return RedirectResponse("/settings#mapping", status_code=303)


@router.get("/settings/mapping/suggest")
def mapping_suggest(item: str = "", user=Depends(auth.require_editor)):
    """The row an existing mapping suggests for a new item name (#106), for the inline editor. {"suggestion": null} when none."""
    with db.pool.connection() as conn:
        s = mapping_hints.suggest(item, _mapping_list(conn), db.get_setting(conn, "rules", {}) or {})
    return JSONResponse({"suggestion": s})


@router.post("/settings/mapping/family")
async def mapping_family(request: Request, user=Depends(auth.require_editor)):
    """Give every item of one name family (#106) the same CanonicalItem. The family is recomputed here, so only items whose base name
    still matches change, and only their CanonicalItem: category, flow and note stay as they are."""
    form = await request.form()
    base, canon = norm(str(form.get("base", ""))), norm(str(form.get("canonical_item", "")))
    with db.pool.connection() as conn:
        rules = db.get_setting(conn, "rules", {}) or {}
        fam = next((f for f in mapping_hints.families(_mapping_list(conn), rules) if f["base"].lower() == base.lower()), None)
        if not fam or not canon:
            flash(request, "Nothing changed: that group of names no longer differs (reload the page).", "bad")
            return RedirectResponse("/settings#mapping", status_code=303)
        items = [i["item"] for i in fam["items"] if i["canonical_item"] != canon]
        conn.execute("UPDATE item_mapping SET canonical_item=%s, updated_at=now() WHERE item = ANY(%s)", (canon, items))
        db.mark_inputs_changed(conn); conn.commit()
    flash(request, f"{len(items)} item{'s' * (len(items) != 1)} now use the canonical name {canon}. The dashboard shows it after a rebuild: press Rebuild now.")
    return RedirectResponse("/settings#mapping", status_code=303)


@router.post("/settings/{kind}/row")
async def settings_row_save(request: Request, kind: str, user=Depends(auth.require_editor_api)):
    if kind not in ("mapping", "goals", "events"):
        raise HTTPException(404)
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    with db.pool.connection() as conn:
        try:
            row, err = _row_save(conn, kind, form)
            warning = person_warning(conn, [row.get("person")]) if kind == "events" and not err else ""
            if not err:
                db.mark_inputs_changed(conn)
            conn.commit()
        except Exception:  # noqa: BLE001
            conn.rollback(); raise
    return JSONResponse({"ok": not err, "row": row, "errors": err, "rebuild_needed": not err, "warning": warning})  # 200 with ok=false: a rejected row is an answer, not a failed request (keeps the console clean)


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


def _save_setting(conn, key: str, data: dict) -> list[str]:
    problems = schema.validate(data, schema.load(key))
    if not problems:
        db.put_setting(conn, key, data)
    return problems


@router.post("/settings/household/form")
async def household_form(request: Request, user=Depends(auth.require_editor)):
    """The Household editor as plain fields; the same schema check as the JSON editor."""
    f = {k: (v.strip() if isinstance(v, str) else v) for k, v in (await request.form()).items()}
    with db.pool.connection() as conn:
        data = {**(db.get_setting(conn, "household", {}) or {})}
        for k in ("name", "title", "currency", "locale"):
            if f.get(k):
                data[k] = f[k].upper() if k == "currency" else f[k]
        problems = _save_setting(conn, "household", data)
        if problems:
            conn.rollback(); flash(request, "Not saved — " + "; ".join(problems[:4]), "bad")
        else:
            conn.commit(); flash(request, "Saved the household's name and display settings.")
    return RedirectResponse("/settings#household", status_code=303)


ROLE_FIELDS = [   # (rules key, plain label, what it changes) for the Roles form (issue #162); defaults are build_ledger.RULES'
    ("paycheck_items", "Items that are paychecks", "Canonical items. The Employer chart, the Goals tab's take-home and the review's paycheck count read them."),
    ("recurring_categories", "Categories of recurring bills", "The Recurring tab lists their spending."),
    ("plan_keep_categories", "Categories whose plan is kept", "On the Last month tab, their suggested plan stays the current plan instead of the twelve-month average."),
    ("income_groups", "Budget groups that hold income", "As named in the export. New source profiles and the mapping draft start from these."),
    ("debt_groups", "Budget groups that hold debt payments", "As named in the export. The debt-plug rule and the off-budget count read them."),
    ("loan_categories", "Categories of loans between family", "The Method tab counts their inflows and repayments."),
]


ROLE_DEFAULTS = {   # shown as placeholders; equal to build_ledger.RULES (tests/test_role_literals.py checks it)
    "paycheck_items": ["Paycheck"], "recurring_categories": ["Subscriptions", "Utilities", "Insurance"],   # role default
    "plan_keep_categories": ["Housing", "Debt", "Insurance"], "income_groups": ["Income"], "debt_groups": ["Debt"],   # role default
    "loan_categories": ["Family Loan"],   # role default
}


@router.post("/settings/rules/roles")
async def rules_roles_form(request: Request, user=Depends(auth.require_editor)):
    """The role rules as one name per line (a name may contain a comma); a blank field means the default. Only fields whose text
    changed are written, so a rule set to [] ("none") in the JSON editor survives a save of the other fields."""
    form = await request.form()
    lines = lambda v: [n.strip() for n in str(v or "").splitlines() if n.strip()]
    with db.pool.connection() as conn:
        rules = {**(db.get_setting(conn, "rules", {}) or {})}
        for key, _, _ in ROLE_FIELDS:
            names = lines(form.get(key))
            if names == lines(form.get(f"{key}__was")):
                continue   # untouched
            if names:
                rules[key] = names
            else:
                rules.pop(key, None)
        problems = _save_setting(conn, "rules", rules)
        if problems:
            conn.rollback(); flash(request, "Not saved — " + "; ".join(problems[:4]), "bad")
        else:
            db.mark_inputs_changed(conn); conn.commit()
            flash(request, "Saved the roles. The dashboard shows them after a rebuild: press Rebuild now.")
    return RedirectResponse("/settings#rules", status_code=303)


@router.post("/settings/rules/people")
async def rules_people_form(request: Request, user=Depends(auth.require_editor)):
    """People and employers, the two rules every household sets, without editing JSON."""
    form = await request.form()
    people = [p.strip() for p in str(form.get("people", "")).split(",") if p.strip()]
    employers = [{"match": m.strip().lower(), "name": n.strip()} for m, n in zip(form.getlist("emp_match"), form.getlist("emp_name"))
                 if str(m).strip() and str(n).strip()]   # the pipeline searches the lower-cased merchant
    with db.pool.connection() as conn:
        rules = {**(db.get_setting(conn, "rules", {}) or {}), "people": people, "employers": employers}
        problems = _save_setting(conn, "rules", rules)
        if problems:
            conn.rollback(); flash(request, "Not saved — " + "; ".join(problems[:4]), "bad")
        else:
            db.mark_inputs_changed(conn); conn.commit()
            flash(request, f"Saved {len(people)} people and {len(employers)} employer rule(s). The dashboard shows them after a rebuild: press Rebuild now.")
    return RedirectResponse("/settings#rules", status_code=303)
