import csv, io, json, re

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response

from .. import db, auth, schema, mapping_hints, brand_check, renames
from . import brand
from ..web_helpers import flash, render
from pipeline.run_pipeline import norm, unmapped_items
from pipeline import mapping_store, accounts_store

router = APIRouter()


EDITORS = {
    "mapping": {"title": "Item mapping", "help": "Each item from your budget tool gets a canonical name and a flow; its category is its canonical item's. Every item must be here, or the build lists it as unmapped.", "help_long": "One row per item in your exports: Item, CanonicalItem, Category, Flow, Note, and optionally DebtType (mortgage, student, auto, card, medical, personal, other; a five-column CSV leaves types as they are). A canonical item has one category, so every row of it gives the same Category (in the row editor, leave Category blank to use the one it has); use Categories and canonical items to rename or move one. Flow is one of income, expense, debt, transfer, plug, zero, exclude, verify. Every item name in the exports must appear here or the build reports it UNMAPPED. Fund moves kept as income items (Fund Transfer, End of Year Rebalance) need Flow transfer, or they count as income; money held for someone else and paid out needs exclude. No name is read literally: the parts names play (which items are paychecks, which categories are recurring bills, and so on) are set under Pipeline rules, Roles (README, \"Roles\"); rules name canonical items, not raw ones."},
    "goals": {"title": "Goals", "help": "One row per goal: key, label, value, unit, note. The Goals tab and the month-close review read these; values are also confirmed each month in the checklist on Close a month. A share is a fraction (0.07) or a percentage (7%)."},
    "accounts": {"title": "Accounts", "help": "What the household owes and owns, for month-end balances (entered on Close a month). Kind is debt or asset. A debt's type is a debt type (empty: its linked item's); an asset's is cash, savings, retirement, brokerage, home, vehicle or other. Linked item is the canonical item a debt's payments go to, so the suggested balance can subtract last month's payment; APR adds a month's interest; escrow is the part of each payment that is not the loan. Person is one of the people, or empty for joint. Emergency marks an asset that counts toward the emergency fund (cash and savings by default). An account with balances is retired (Active no), not deleted: its balances are kept."},
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
    "plug": "a balancing entry your budget tool made; left out of every total",
    "zero": "a placeholder line; kept in the ledger, counted nowhere",
    "exclude": "money held for someone else; left out of the ledger and every chart",
    "verify": "an inflow not yet identified; left out until you confirm it",
}
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


PIPELINE_INPUTS = {"mapping", "goals", "events", "cpi", "rules", "accounts", "balances"}   # read by the pipeline: the dashboard changes only after a rebuild


def check_mapping_row(r: dict) -> tuple[dict, dict]:
    """Clean one mapping row; returns (row, {field: message})."""
    c = {"item": norm(r.get("item") or r.get("Item") or ""), "canonical_item": norm(r.get("canonical_item") or r.get("CanonicalItem") or ""),
         "category": norm(r.get("category") or r.get("Category") or ""), "flow": (r.get("flow") or r.get("Flow") or "").strip(), "note": (r.get("note") or r.get("Note") or "").strip()}
    err = {k: "required" for k in ("item", "canonical_item") if not c[k]}   # category may be blank when the canonical item has one (#133)
    for key in ("debt_type", "DebtType"):   # the CSV's optional sixth column (#141): absent means "leave the type as it is"
        if key in r:
            c["debt_type"] = (r.get(key) or "").strip().lower() or None
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
        if not c["category"] and c["canonical_item"] and c["canonical_item"] not in mapping_store.categories_of(conn):
            err["category"] = f"required: {c['canonical_item']} is a new canonical item"
        if not err and c["item"] != orig:
            _unique_err(conn, "SELECT 1 FROM item_mapping WHERE item=%s", (c["item"],), err, "item", "already in the mapping")
        if not err:
            try:   # the new row first, then the renamed one goes: its canonical item stays in use throughout
                mapping_store.write_rows(conn, [c])
                if orig and orig != c["item"]:
                    mapping_store.delete_items(conn, [orig])
                c["category"] = mapping_store.categories_of(conn)[c["canonical_item"]]
            except mapping_store.MappingError as e:
                err["category"] = str(e).split(": ", 1)[1]
    elif kind == "accounts":
        c, err = accounts_store.row_save(conn, f)
        was = accounts_store.norm(f.get("original") or "")
        if not err and was and c["name"] != was:   # a decision's extra payment names its account (#155): the rename reaches it, and is logged
            from .. import decisions
            decisions.rewrite_targets(conn, "account", was, c["name"])
            conn.execute("INSERT INTO renames (dimension, old, new, by) VALUES ('account', %s, %s, %s)", (was, c["name"], ""))
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
        return mapping_store.delete_items(conn, [norm(f.get("original") or "")])
    if kind == "goals":
        return conn.execute("DELETE FROM goals WHERE key=%s", ((f.get("original") or "").strip(),)).rowcount
    if kind == "accounts":
        return accounts_store.delete_account(conn, accounts_store.norm(f.get("original") or ""))
    return conn.execute("DELETE FROM life_events WHERE month=%s AND label=%s", ((f.get("original_m") or "").strip(), (f.get("original_label") or "").strip())).rowcount


def _mapping_csv(conn) -> str:
    out = io.StringIO(); w = csv.writer(out); w.writerow(["Item", "CanonicalItem", "Category", "Flow", "Note", "DebtType"])
    for r in mapping_store.rows(conn):
        w.writerow([r["item"], r["canonical_item"], r["category"], r["flow"], r["note"], r["debt_type"] or ""])
    return out.getvalue()


def _problems(problems: list[str]) -> ValueError:
    return ValueError(f"{len(problems)} problem{'s' * (len(problems) != 1)}: " + "; ".join(problems[:8]) + (f"; and {len(problems) - 8} more" if len(problems) > 8 else ""))


def _mapping_rows(body: str, existing: set[str] | None = None) -> tuple[list[dict], list[int]]:
    """Check a mapping CSV and return its clean rows; raises ValueError naming each bad line. With `existing`, an item already in the
    mapping is a problem too (the draft only adds rows, it never overwrites one). Returns the rows and their CSV line numbers."""
    reader = csv.DictReader(io.StringIO(body.lstrip("\ufeff")))
    rows = list(reader)
    if not rows or not {"Item", "CanonicalItem", "Category", "Flow"}.issubset(rows[0].keys()):
        raise ValueError("mapping needs columns Item, CanonicalItem, Category, Flow, Note (and optionally DebtType)")
    clean, lines, problems, seen = [], [], [], {}
    for n, r in enumerate(rows, start=2):
        c, err = check_mapping_row({k: v for k, v in r.items() if isinstance(v, str)})
        if not err and c["item"] in seen:
            err["item"] = f"duplicate of line {seen[c['item']]}"
        if not err and existing is not None and c["item"] in existing:
            err["item"] = "already in the mapping"
        for f, m in err.items():
            problems.append(f"line {n}: " + (f"bad Flow on: {c['item'] or '(blank)'} ({m})" if f == "flow" else f"{f} {m}" + (f" on: {c['item']}" if c["item"] else "")))
        if not err:
            seen[c["item"]] = n; clean.append(c); lines.append(n)
    if problems:
        raise _problems(problems)
    return clean, lines


def _mapping_add(conn, body: str) -> str:
    """Add the rows of a mapping CSV (the reviewed draft) without touching any existing row; all or nothing, like the bulk import."""
    existing = {r["item"] for r in conn.execute("SELECT item FROM item_mapping").fetchall()}
    clean, lines = _mapping_rows(body, existing)
    mapping_store.write_rows(conn, clean, lines=lines)
    return f"{len(clean)} drafted mapping row{'s' * (len(clean) != 1)}"


def _mapping_list(conn) -> list[dict]:
    return mapping_store.rows(conn)


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
    clean, lines = _mapping_rows(body)
    mapping_store.write_rows(conn, clean, replace=True, lines=lines)   # one canonical item with two categories is refused, by line
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
    if key == "accounts":
        return accounts_store.accounts_csv(conn)
    if key == "balances":   # not a Settings editor: the typed month-end balances, for the CLI and sources.zip (#143)
        return accounts_store.balances_csv(conn)
    if key == "checkins":   # not a Settings editor: the check-ins done, for settings.zip and the seed loader (#164)
        rows = conn.execute("SELECT month, mode, steps, completed_at, completed_by, reopened_at FROM checkins ORDER BY month").fetchall()
        return json.dumps([{**r, **{k: r[k].isoformat() if r[k] else None for k in ("completed_at", "reopened_at")}} for r in rows], indent=1)
    if key == "yearly_bills":   # not a Settings editor: the household's word on yearly bills (#151), for settings.zip and the seed loader
        from .. import yearly_bills
        return yearly_bills.export_csv(conn)
    if key == "decisions":   # not a Settings editor: the decisions log and its monthly checks (#155), for settings.zip and the seed loader
        from .. import decisions
        return decisions.export_json(conn)
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


def _save_editor(conn, key: str, body: str, by: str = "") -> str:
    if key == "mapping":
        return _mapping_import(conn, body)
    if key == "accounts":
        return accounts_store.import_accounts(conn, body)
    if key == "balances":
        return accounts_store.import_balances(conn, body, by)
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
        rows = {"mapping": mapping_store.rows(conn),
                "goals": [{**r, "value": (f"{r['value']:f}".rstrip("0").rstrip(".") if r["value"] is not None else None)} for r in conn.execute("SELECT key, value, unit, label, note FROM goals ORDER BY key").fetchall()],
                "events": conn.execute("SELECT month AS m, label, kind, person FROM life_events ORDER BY month, label").fetchall(),
                "accounts": [accounts_store.out(a) for a in accounts_store.list_accounts(conn)]}
        balance_counts = {r["account"]: r for r in conn.execute("SELECT account, count(*) n, min(budget_month) first, max(budget_month) last FROM balances GROUP BY account").fetchall()}
        categories = sorted({r["category"] for r in rows["mapping"] if r["category"]})
        rules = db.get_setting(conn, "rules", {}) or {}
        pretax = pretax_rows(conn, list(rules.get("people") or []))
        household = db.get_setting(conn, "household", {}) or {}
        name_families = mapping_hints.families(rows["mapping"], rules)
        unmapped = len(unmapped_items(conn))
        defs, mapping_notes = renames.overview(conn), renames.notes(conn)
    from .. import yearly_bills as _yb
    with db.pool.connection() as conn:
        bills = _yb.for_page(conn)
    from .yearly_bills import slug as _yb_slug
    return render(request, "settings.html", user, active="settings", editors=editors, rows=rows, flows=FLOWS, flow_help=FLOW_HELP, bills=bills,
                  yb_form=request.session.pop("yb_form", None), yb_slug=_yb_slug,
                  categories=categories, rules=rules, hh=household, name_families=name_families, unmapped=unmapped,
                  brand_problems=brand_check.check_dir(brand.BRAND_DIR), role_fields=ROLE_FIELDS, role_defaults=ROLE_DEFAULTS,
                  event_choices={"kind": [""] + list(EVENT_KINDS)}, people=list(rules.get("people") or []),
                  defs=defs, mapping_notes=mapping_notes, canonical_category={c["name"]: c["category"] for c in defs["canonical"]},
                  debt_types=mapping_store.DEBT_TYPES, balance_counts=balance_counts, pretax=pretax,
                  account_choices={"kind": list(accounts_store.KINDS), "type": [""] + sorted(set(accounts_store.bal.DEBT_TYPES + accounts_store.bal.ASSET_TYPES)),
                                   "emergency": ["", "yes", "no"], "in_net_worth": ["yes", "no"], "active": ["yes", "no"]})


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


def _who(user) -> str:
    return (user or {}).get("email") or (user or {}).get("name") or ""


def _definition_change(request: Request, fn, *args, back: str = "/settings#mapping") -> RedirectResponse:
    """Run one rename or move in its own transaction and say what happened; back to the sub-tab it was made on."""
    with db.pool.connection() as conn:
        try:
            what = fn(conn, *args); conn.commit()
            flash(request, f"{what[0].upper()}{what[1:]}. The dashboard shows it after a rebuild: press Rebuild now.")
        except ValueError as e:
            conn.rollback(); flash(request, f"Not changed — {e}", "bad")
    return RedirectResponse(back, status_code=303)


@router.post("/settings/mapping/category/rename")
async def category_rename(request: Request, user=Depends(auth.require_editor)):
    f = await request.form()
    return _definition_change(request, renames.rename_category, str(f.get("old", "")), str(f.get("new", "")), _who(user), back="/settings#mapping/categories")


@router.post("/settings/mapping/canonical/rename")
async def canonical_rename(request: Request, user=Depends(auth.require_editor)):
    f = await request.form()
    return _definition_change(request, renames.rename_canonical, str(f.get("old", "")), str(f.get("new", "")), _who(user), back="/settings#mapping/canonical")


@router.post("/settings/mapping/canonical/move")
async def canonical_move(request: Request, user=Depends(auth.require_editor)):
    f = await request.form()
    return _definition_change(request, renames.move_canonical, str(f.get("name", "")), str(f.get("category", "")), back="/settings#mapping/canonical")


@router.post("/settings/mapping/canonical/debt-type")
async def canonical_debt_type(request: Request, user=Depends(auth.require_editor)):
    """Set or clear a canonical item's debt type (#141)."""
    f = await request.form()
    name, t = str(f.get("name", "")), str(f.get("debt_type", ""))
    def change(conn):
        mapping_store.set_debt_type(conn, name, t); db.mark_inputs_changed(conn)
        return f"{name} is {'a ' + t + ' debt' if t else 'no longer typed'}"
    return _definition_change(request, change, back="/settings#mapping/debts")


@router.post("/settings/mapping/notes/dismiss")
async def mapping_notes_dismiss(request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        conn.execute("UPDATE mapping_migration_notes SET dismissed_at=now() WHERE dismissed_at IS NULL"); conn.commit()
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
    still matches change, and only their CanonicalItem: flow and note stay as they are, and the category is the canonical item's
    (for a new canonical name, the category most of the family's rows had)."""
    form = await request.form()
    base, canon = norm(str(form.get("base", ""))), norm(str(form.get("canonical_item", "")))
    with db.pool.connection() as conn:
        rules = db.get_setting(conn, "rules", {}) or {}
        fam = next((f for f in mapping_hints.families(_mapping_list(conn), rules) if f["base"].lower() == base.lower()), None)
        if not fam or not canon:
            flash(request, "Nothing changed: that group of names no longer differs (reload the page).", "bad")
            return RedirectResponse("/settings#mapping", status_code=303)
        items = [i["item"] for i in fam["items"] if i["canonical_item"] != canon]
        cur = {r["item"]: r for r in _mapping_list(conn)}
        cats = mapping_store.categories_of(conn)
        if canon not in cats:   # a new canonical name takes the category most of the family's rows have now
            votes = sorted(cur[i["item"]]["category"] for i in fam["items"])
            cats[canon] = max(votes, key=votes.count)
        moved = [f"{i} from {cur[i]['category']}" for i in items if cur[i]["category"] != cats[canon]]
        mapping_store.write_rows(conn, [{**cur[i], "canonical_item": canon, "category": cats[canon]} for i in items])
        db.mark_inputs_changed(conn); conn.commit()
    flash(request, f"{len(items)} item{'s' * (len(items) != 1)} now use the canonical name {canon}"
                   + (f", so they are in {cats[canon]} ({'; '.join(moved)})" if moved else "") + ". The dashboard shows it after a rebuild: press Rebuild now.")
    return RedirectResponse("/settings#mapping", status_code=303)


@router.post("/settings/{kind}/row")
async def settings_row_save(request: Request, kind: str, user=Depends(auth.require_editor_api)):
    if kind not in ("mapping", "goals", "events", "accounts"):
        raise HTTPException(404)
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    with db.pool.connection() as conn:
        try:
            row, err = _row_save(conn, kind, form)
            warning = person_warning(conn, [row.get("person")]) if kind in ("events", "accounts") and not err else ""
            if not err:
                db.mark_inputs_changed(conn)
            conn.commit()
        except Exception:  # noqa: BLE001
            conn.rollback(); raise
    return JSONResponse({"ok": not err, "row": row, "errors": err, "rebuild_needed": not err, "warning": warning})  # 200 with ok=false: a rejected row is an answer, not a failed request (keeps the console clean)


@router.post("/settings/{kind}/row/delete")
async def settings_row_delete(request: Request, kind: str, user=Depends(auth.require_editor_api)):
    if kind not in ("mapping", "goals", "events", "accounts"):
        raise HTTPException(404)
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    with db.pool.connection() as conn:
        try:
            n = _row_delete(conn, kind, form)
        except ValueError as e:   # an account with balances (#143): say why, keep the row
            conn.rollback()
            return JSONResponse({"ok": False, "errors": {"_": str(e)}, "rebuild_needed": False})
        if n:
            db.mark_inputs_changed(conn)
        conn.commit()
    return JSONResponse({"ok": n > 0, "deleted": n, "rebuild_needed": n > 0}, status_code=200 if n else 404)


PRETAX = "pretax_contribution:"   # goals per person (#152): pre-tax saving that never reaches the budget
PRETAX_UNITS = {"share": "share of gross", "usd": "usd/month"}
PRETAX_NOTE = "Ask at each month-close: 401(k), 403(b), HSA and the like, taken from pay before it reaches the budget."


def pretax_rows(conn, people: list) -> list[dict]:
    """One row per person in the people rule (and any other person with a contribution stored): value as typed, and its unit."""
    have = {r["key"][len(PRETAX):]: r for r in conn.execute("SELECT key, value, unit FROM goals WHERE key LIKE %s ORDER BY key", (PRETAX + "%",)).fetchall()}
    out = []
    for p in list(people) + [p for p in have if p not in people]:
        r = have.get(p); share = not r or str(r["unit"]).startswith("share")
        v = None if not r or r["value"] is None else (f"{(r['value'] * 100).normalize():f}%" if share else f"{r['value'].normalize():f}")
        out.append({"person": p, "value": v or "", "unit": "share" if share else "usd"})
    return out


@router.post("/settings/goals/pretax")
async def pretax_form(request: Request, user=Depends(auth.require_editor)):
    """Pre-tax saving per person (#152): a share of the household's gross (7% or 0.07) or dollars a month. Blank removes it."""
    f = await request.form()
    rows = list(zip(f.getlist("person"), f.getlist("value"), f.getlist("unit")))
    problems, done = [], 0
    with db.pool.connection() as conn:
        for person, raw, unit in rows:
            person, raw = str(person).strip(), str(raw).strip()
            key, u = PRETAX + person, PRETAX_UNITS.get(str(unit), "share of gross")
            if not person:
                continue
            if not raw:
                done += conn.execute("DELETE FROM goals WHERE key=%s", (key,)).rowcount; continue
            v, problem = parse_goal_value(raw, u)
            if problem or v is None:
                problems.append(f"{person}: {problem or 'enter a number'}"); continue
            conn.execute("INSERT INTO goals (key, value, unit, label, note, confirmed_at) VALUES (%s,%s,%s,%s,%s,now()) ON CONFLICT (key) DO UPDATE "
                         "SET value=EXCLUDED.value, unit=EXCLUDED.unit, confirmed_at=now(), updated_at=now()", (key, v, u, f"Pre-tax saving, {person}", PRETAX_NOTE))
            done += 1
        if problems:
            conn.rollback(); flash(request, "Not saved — " + "; ".join(problems), "bad")
        else:
            db.mark_inputs_changed(conn); conn.commit()
            flash(request, f"Saved pre-tax saving for {done} {'person' if done == 1 else 'people'}. The dashboard shows it after a rebuild: press Rebuild now.")
    return RedirectResponse("/settings#goals", status_code=303)


@router.post("/settings/accounts/balances")
def balances_backfill(request: Request, body: str = Form(...), user=Depends(auth.require_editor)):
    """Balances history (#143): past month-ends typed or pasted as CSV (account,YYYY-MM,amount); every line checked first."""
    with db.pool.connection() as conn:
        try:
            what = accounts_store.import_balances(conn, body, _who(user))
            db.mark_inputs_changed(conn); conn.commit()
            flash(request, f"Saved {what}. The dashboard shows them after a rebuild: press Rebuild now.")
        except ValueError as e:
            conn.rollback(); flash(request, f"Not saved — {e}", "bad")
    return RedirectResponse("/settings#accounts/history", status_code=303)


@router.get("/settings/accounts/balances.csv")
def balances_export(user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        body = accounts_store.balances_csv(conn)
    return Response(body, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="balances.csv"'})


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


from ..roles import ROLE_DEFAULTS   # noqa: E402  (shown as placeholders; equal to build_ledger.RULES)


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
