"""Other budget tools' sources (issue #96): the mapping step after an upload, and the saved source profiles.

A pair of files that is not named like exports (the internal format, or EveryDollar's) waits in upload_pending while the editor confirms which file is which,
the budget month, and how the columns map (proposed from the headers, or taken from a saved profile that recognises them). On
confirm the profile is saved, both files are stored as uploaded with their rows read through it, and the ledger is rebuilt."""
from __future__ import annotations
import collections, json, secrets

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import auth, db
from ..builds import run_background
from ..web_helpers import flash, render, month_text
from pipeline import sources as src
from pipeline.run_pipeline import build_running, ingest, load_profiles, reapply_profile, save_profile

router = APIRouter()
KIND_LABEL = {"transactions": "Transactions", "budget_items": "Budget"}
TXN_LABELS = {"date": "Date", "amount": "Amount (one signed column)", "debit": "Debit / money out", "credit": "Credit / money in",
              "type": "Type (income or expense)", "merchant": "Merchant / payee", "account": "Account", "note": "Note / memo",
              "group": "Group (budget category)", "item": "Item (budget line)"}
BUDGET_LABELS = {"group": "Group (budget category)", "item": "Item (budget line)", "planned": "Planned", "spent": "Spent / actual"}


def stash(conn, files: list[tuple[str, bytes]], by: str) -> str:
    """Keep files for the mapping step; returns the batch token. Entries older than a day are cleared on the way."""
    batch = secrets.token_urlsafe(12)
    conn.execute("DELETE FROM upload_pending WHERE created_at < now() - interval '1 day'")
    for name, data in files:
        conn.execute("INSERT INTO upload_pending (batch, filename, data, created_by) VALUES (%s,%s,%s,%s)", (batch, name, data, by))
    conn.commit()
    return batch


def _pending(conn, batch: str) -> list[dict]:
    return [dict(r, data=bytes(r["data"])) for r in conn.execute("SELECT id, filename, data FROM upload_pending WHERE batch=%s ORDER BY id", (batch,)).fetchall()]


def _month_guess(data: bytes, sec: dict) -> str:
    """The most common YYYY-MM among the transaction dates, read with the section's date format; '' when none parse."""
    try:
        _, rows = src.read_csv(data)
        col, fmt = sec["columns"].get("date"), sec.get("date_format", "MM/DD/YYYY")
        ms = collections.Counter()
        for r in rows:
            try:
                ms[src.parse_date(r.get(col, ""), fmt)[:7]] += 1   # YYYY-MM-DD: its budget-month part
            except src.ProfileError:
                pass
        return ms.most_common(1)[0][0] if ms else ""
    except Exception:  # noqa: BLE001  a guess only
        return ""


def income_groups(conn) -> list[str]:
    """The income_groups rule: what a new profile's income groups start as."""
    v = (db.get_setting(conn, "rules", {}) or {}).get("income_groups")
    return list(v) if v is not None else src.DEFAULT_INCOME_GROUPS


def _plan(conn, files: list[dict]) -> dict:
    """What the mapping page shows: each file's header and first rows, its kind, the profile that recognises it (if any), and a
    starting spec for both sections."""
    profiles = load_profiles(conn)
    out, kinds, matched = [], [], None
    for f in files:
        try:
            header, rows = src.read_csv(f["data"])
        except src.ProfileError as e:
            out.append({**f, "error": str(e)}); continue
        m = src.match_profile(header, [dict(p["spec"], _id=p["id"], _name=p["name"]) for p in profiles])
        if m:
            matched = matched or m[0]; kind = m[1]
        else:
            cols = src.guess_columns(header, "budget_items")
            kind = "budget_items" if cols.get("planned") and cols.get("spent") else "transactions"
        out.append({**f, "header": header, "sample": rows[:5], "rows": len(rows), "kind": kind})
        kinds.append(kind)
    if len(kinds) == 2 and kinds[0] == kinds[1]:   # two files guessed alike: the second is the other kind
        out[1]["kind"] = "budget_items" if out[0]["kind"] == "transactions" else "transactions"
    by_kind = {f["kind"]: f for f in out if "header" in f}
    spec = {"name": matched["_name"] if matched else "", "transactions": None, "budget": None}
    for kind, key in (("transactions", "transactions"), ("budget_items", "budget")):
        if matched and matched.get(key) and (kind not in by_kind or src.which_kind(by_kind[kind]["header"], matched) == kind):
            spec[key] = matched[key]
        elif kind in by_kind:
            spec[key] = src.propose(kind, by_kind[kind]["data"], income_groups(conn))
    month = _month_guess(by_kind["transactions"]["data"], spec["transactions"]) if "transactions" in by_kind and spec["transactions"] else ""
    return {"files": out, "spec": spec, "profile_id": matched["_id"] if matched else None, "month": month, "profiles": profiles,
            "income_groups": income_groups(conn)}


@router.get("/upload/map/{batch}", response_class=HTMLResponse)
def map_page(request: Request, batch: str, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        files = _pending(conn, batch)
        if not files:
            flash(request, "Those files are no longer waiting (they are kept for a day). Upload them again.", "bad")
            return RedirectResponse("/upload", status_code=303)
        plan = _plan(conn, files)
    return render(request, "upload_map.html", user, active="upload", batch=batch, plan=plan, txn_labels=TXN_LABELS, budget_labels=BUDGET_LABELS,
                  date_formats=src.DATE_FORMATS, kind_label=KIND_LABEL, default_income_groups=plan["income_groups"], error=None)


def _section_from_form(form, prefix: str, kind: str, headers: list[str], groups: list[str] | None = None) -> dict:
    fields = src.TXN_FIELDS if kind == "transactions" else src.BUDGET_FIELDS
    cols = {f: (form.get(f"{prefix}col_{f}") or None) for f in fields}
    lst = lambda k, d: [v.strip() for v in (form.get(k) or d).split(",") if v.strip()]
    sec = {"headers": headers, "columns": cols, "decimal": form.get(f"{prefix}decimal") or ".", "thousands": form.get(f"{prefix}thousands", ",")}
    if kind == "transactions":
        sec.update(date_format=form.get(f"{prefix}date_format") or "MM/DD/YYYY", amount_mode=form.get(f"{prefix}amount_mode") or "signed",
                   expense_sign=form.get(f"{prefix}expense_sign") or "negative", income_values=lst(f"{prefix}income_values", "income"),
                   income_groups=lst(f"{prefix}income_groups", ", ".join(groups if groups is not None else src.DEFAULT_INCOME_GROUPS)))
    else:
        sec.update(spent_sign=form.get(f"{prefix}spent_sign") or "negative", income_groups=lst(f"{prefix}income_groups", ", ".join(groups if groups is not None else src.DEFAULT_INCOME_GROUPS)))
    return sec


@router.post("/upload/map/{batch}")
async def map_confirm(request: Request, batch: str, background: BackgroundTasks, user=Depends(auth.require_editor)):
    form = await request.form()
    with db.pool.connection() as conn:
        files = _pending(conn, batch)
        if not files:
            flash(request, "Those files are no longer waiting (they are kept for a day). Upload them again.", "bad")
            return RedirectResponse("/upload", status_code=303)
        plan = _plan(conn, files)
        try:
            kinds = {f["id"]: form.get(f"kind_{f['id']}") for f in files}
            if sorted(kinds.values()) != sorted(set(kinds.values())) or not set(kinds.values()) <= {"transactions", "budget_items"}:
                raise src.ProfileError("say which file is the transactions file and which is the budget file (one of each)")
            by_kind = {k: next(f for f in files if f["id"] == i) for i, k in kinds.items()}
            existing = {p["name"]: p for p in plan["profiles"]}
            name = (form.get("profile_name") or "").strip()
            old = existing.get(name)
            spec = {"name": name}
            for kind, key, prefix in (("transactions", "transactions", "t_"), ("budget_items", "budget", "b_")):
                if kind in by_kind:
                    spec[key] = _section_from_form(form, prefix, kind, src.headers_of(by_kind[kind]["data"]), income_groups(conn))
                elif old and old["spec"].get(key):
                    spec[key] = old["spec"][key]   # one file of the pair: the other half of a saved profile stays as it was
                else:
                    raise src.ProfileError("a new source needs both files, its transactions and its budget, to set up the mapping")
            src.validate_profile(spec)
            for kind, f in by_kind.items():   # read both before storing anything, so a bad mapping stores nothing
                src.apply(kind, f["data"], spec)
            month = form.get("month") or ""
            changed = old is not None and old["spec"] != spec and old["files"]
            pid = save_profile(conn, spec, commit=not changed)
            if changed:
                reapply_profile(conn, pid)   # commits the profile with the re-read rows, or rolls both back
            for kind, f in by_kind.items():
                ingest(conn, f["filename"], f["data"], uploaded_by=user.get("email", ""), profile={"id": pid, "spec": spec}, kind=kind, budget_month=month)
            conn.execute("DELETE FROM upload_pending WHERE batch=%s", (batch,)); conn.commit()
        except (src.ProfileError, ValueError) as e:
            conn.rollback()
            return render(request, "upload_map.html", user, active="upload", batch=batch, plan=plan, txn_labels=TXN_LABELS, budget_labels=BUDGET_LABELS,
                          date_formats=src.DATE_FORMATS, kind_label=KIND_LABEL, default_income_groups=plan["income_groups"], error=str(e), form=form)
        running = build_running(conn)
    if not running:
        background.add_task(run_background)
    flash(request, f"Stored {len(by_kind)} file{'s' if len(by_kind) != 1 else ''} for {month_text(month)} with the profile “{name}”. "
                   + ("A rebuild is already running; it will run again to include them." if running else "Rebuilding in the background."))
    return RedirectResponse("/upload", status_code=303)


@router.post("/upload/map/{batch}/discard")
def map_discard(request: Request, batch: str, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM upload_pending WHERE batch=%s", (batch,)); conn.commit()
    flash(request, "Discarded the files waiting for a mapping.")
    return RedirectResponse("/upload", status_code=303)


# ---------------------------------------------------------------- saved profiles
@router.get("/settings/sources", response_class=HTMLResponse)
def profiles_page(request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        profiles = load_profiles(conn)
    return render(request, "sources.html", user, active="settings", profiles=[dict(p, body=json.dumps(p["spec"], indent=1, ensure_ascii=False)) for p in profiles],
                  builtin=json.dumps(src.BUILTIN, indent=1))


@router.get("/settings/sources/everydollar.json")
def builtin_export(user=Depends(auth.require_editor)):
    """The built-in EveryDollar profile (#161), as a profile JSON like any other; read-only here, importable elsewhere."""
    return Response(json.dumps(src.BUILTIN, indent=1), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="everydollar.json"'})


@router.get("/settings/sources/{pid}.json")
def profile_export(pid: int, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        row = conn.execute("SELECT name, spec FROM source_profiles WHERE id=%s", (pid,)).fetchone()
    if row is None:
        return Response("no such profile\n", status_code=404, media_type="text/plain")
    fname = "".join(c if c.isalnum() or c in "-_" else "-" for c in row["name"].lower()).strip("-") or "profile"
    return Response(json.dumps(row["spec"], indent=1, ensure_ascii=False) + "\n", media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.profile.json"'})


@router.post("/settings/sources")
def profile_save(request: Request, background: BackgroundTasks, body: str = Form(...), user=Depends(auth.require_editor)):
    """Import a profile, or save an edited one (same name). An edit is re-applied to every file stored with it, then rebuilt."""
    try:
        spec = json.loads(body)
    except json.JSONDecodeError as e:
        flash(request, f"Not valid JSON: {e}", "bad"); return RedirectResponse("/settings/sources", status_code=303)
    with db.pool.connection() as conn:
        try:
            src.validate_profile(spec)
            before = conn.execute("SELECT id, spec FROM source_profiles WHERE name=%s", (str(spec["name"]).strip(),)).fetchone()
            changed = bool(before) and before["spec"] != spec
            pid = save_profile(conn, spec, commit=not changed)
            n = reapply_profile(conn, pid) if changed else 0   # commits the profile with the re-read rows, or rolls both back
        except (src.ProfileError, ValueError) as e:
            conn.rollback()
            flash(request, f"Profile not saved: {e}", "bad"); return RedirectResponse("/settings/sources", status_code=303)
        running = build_running(conn)
    if n and not running:
        background.add_task(run_background)
    flash(request, f"Saved the profile “{spec['name']}”." + (f" Re-read {n} stored file{'s' if n != 1 else ''} with it; rebuilding." if n else ""))
    return RedirectResponse("/settings/sources", status_code=303)


@router.post("/settings/sources/{pid}/delete")
def profile_delete(request: Request, pid: int, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        used = conn.execute("SELECT count(*) n FROM source_files WHERE profile_id=%s", (pid,)).fetchone()["n"]
        if used:
            flash(request, f"That profile still reads {used} stored file{'s' if used != 1 else ''}; delete those months first.", "bad")
        else:
            conn.execute("DELETE FROM source_profiles WHERE id=%s", (pid,)); conn.commit(); flash(request, "Profile deleted.")
    return RedirectResponse("/settings/sources", status_code=303)
