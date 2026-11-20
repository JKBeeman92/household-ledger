"""Other budget tools' sources (issue #96): the mapping step after an upload, and the saved source profiles.

A pair of files that is not named like exports (the internal format, or EveryDollar's) waits in upload_pending while the editor confirms which file is which,
the budget month, and how the columns map (proposed from the headers, or taken from a saved profile that recognises them). On
confirm the profile is saved, both files are stored as uploaded with their rows read through it, and the ledger is rebuilt.

A back catalogue of such files (a zip, or more than two at once) waits there too, for the batch page (#165): a dry run of the saved
profiles (file → profile → kind → month, and how the month was found), confirmed once for the whole batch, stored in one
transaction and rebuilt once."""
from __future__ import annotations
import json, re, secrets

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import auth, db
from ..builds import run_background
from ..web_helpers import flash, render, month_text
from pipeline import sources as src
from pipeline import filenames
from pipeline.run_pipeline import (FN_RX, build_running, ingest, load_profiles, plan_files, plan_named, reapply_profile, save_profile,
                                   store_planned, stored_files)
from psycopg.types.json import Jsonb

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
    """The most common YYYY-MM among the transaction dates, read with the section's date format; '' when none parse or two months
    tie (src.month_of_dates, which the bulk paths use too: #165). The editor confirms or types it."""
    try:
        return src.month_of_dates(data, sec)[0] or ""
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
            if old and old["spec"].get("month_from"):
                spec["month_from"] = old["spec"]["month_from"]   # the month rule is set on Settings → Sources; this page keeps it (#165)
            for kind, key, prefix in (("transactions", "transactions", "t_"), ("budget_items", "budget", "b_")):
                if kind in by_kind:
                    spec[key] = _section_from_form(form, prefix, kind, src.headers_of(by_kind[kind]["data"]), income_groups(conn))
                elif old and old["spec"].get(key):
                    spec[key] = old["spec"][key]   # one file of the pair: the other half of a saved profile stays as it was
                else:
                    raise src.ProfileError("a new source needs both files, its transactions and its budget, to set up its source profile")
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
    flash(request, "Discarded the files waiting for a source profile.")
    return RedirectResponse("/upload", status_code=303)


# ---------------------------------------------------------------- a back catalogue through the saved profiles (#165)
STATE_LABEL = {"ready": "Ready", "replaces": "Replaces", "unchanged": "Unchanged", "refused": "Not stored"}


def _batch_plan(conn, batch: str) -> tuple[list[tuple[str, bytes]], list[dict]]:
    """The batch's files and their dry run. Worked out again on every view and on confirm, from the files as uploaded and the
    profiles as saved, so what is stored is what the table showed (or newer, if a profile was edited in between)."""
    pending = _pending(conn, batch)
    files = [(f["filename"], f["data"]) for f in pending]
    # A zip's files named like exports wait here with the rest (upload()), so the batch's own export wins its month and kind over a
    # profiled file (as on the command line), and the whole zip is one confirmation and one rebuild (#165 review)
    named = [i for i, (n, _) in enumerate(files) if FN_RX.match(n)]
    other = [i for i in range(len(files)) if i not in set(named)]
    plan = plan_named([files[i] for i in named], stored_files(conn))
    for e in plan:
        e["i"] = named[e["i"]]
    if other:
        present = {filenames.parse(files[i][0]) for i in named} - {None}
        for e in plan_files(conn, [files[i] for i in other], default_mode=True, present=present):
            plan.append(dict(e, i=other[e["i"]]))
    conn.commit()   # read only
    # problems first, then by month and kind: a long table is read for what will not be stored
    order = {"refused": 0, "ready": 1, "unchanged": 2}
    plan.sort(key=lambda e: (order[e["state"]], e["month"] or "", e["kind"] or "", e["name"]))
    return files, plan


_MONTH_RX = re.compile(r"(?<![\w-])\d{4}-(0[1-9]|1[0-2])(?![\w-])")   # a bare budget month in a sentence, not part of a file name or date


@router.get("/upload/batch/{batch}", response_class=HTMLResponse)
def batch_page(request: Request, batch: str, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        files, plan = _batch_plan(conn, batch)
        stored = stored_files(conn); conn.commit()
    if not files:
        flash(request, "Those files are no longer waiting (they are kept for a day). Upload them again.", "bad")
        return RedirectResponse("/upload", status_code=303)
    # a ready file whose month and kind are already stored replaces that file: its own stamp, listed after the refused ones, so a
    # wrongly guessed month is seen before the one confirmation (display only: the plan and what is stored are unchanged)
    plan = [dict(e, replaces=e["state"] == "ready" and (e["kind"], e["month"]) in stored) for e in plan]
    plan.sort(key=lambda e: 0 if e["state"] == "refused" else 1 if e["replaces"] else 2)   # stable: months stay in order within each
    # the page speaks months the household's way ("May 2026", DS-WORD-6); the dry run itself keeps ISO months, as the CLI prints them
    plan = [dict(e, **{k: _MONTH_RX.sub(lambda m: month_text(m.group(0)), e[k]) for k in ("how", "detail") if e.get(k)}) for e in plan]
    ready = [e for e in plan if e["state"] == "ready"]
    return render(request, "upload_batch.html", user, active="upload", batch=batch, plan=plan, state_label=STATE_LABEL, ready=ready,
                  months=sorted({e["month"] for e in ready}), profiles=sorted({e["profile"] for e in ready if e["profile"]}),
                  named=sum(1 for e in ready if e.get("named")), replacing=[e for e in ready if e["replaces"]],
                  refused=[e for e in plan if e["state"] == "refused"], unchanged=[e for e in plan if e["state"] == "unchanged"])


@router.post("/upload/batch/{batch}")
def batch_confirm(request: Request, batch: str, background: BackgroundTasks, user=Depends(auth.require_editor)):
    """The one confirmation for the whole batch: every ready file stored in one transaction (or none), then one rebuild."""
    with db.pool.connection() as conn:
        files, plan = _batch_plan(conn, batch)
        if not files:
            flash(request, "Those files are no longer waiting (they are kept for a day). Upload them again.", "bad")
            return RedirectResponse("/upload", status_code=303)
        if not any(e["state"] == "ready" for e in plan):
            flash(request, "Nothing in this batch can be stored; the table says why for each file.", "bad")
            return RedirectResponse(f"/upload/batch/{batch}", status_code=303)
        try:
            done = store_planned(conn, plan, files, user.get("email", ""), atomic=True)
        except ValueError as e:
            conn.rollback()
            flash(request, f"Nothing was stored: {str(e)[:300]}", "bad")
            return RedirectResponse(f"/upload/batch/{batch}", status_code=303)
        # per-file results live in the database, as an upload's do: a back catalogue would overflow the session cookie
        results = done + [{"name": e["name"], "ok": False, "detail": e["detail"]} for e in plan if e["state"] == "refused"] \
                       + [{"name": e["name"], "ok": False, "detail": e["detail"]} for e in plan if e["state"] == "unchanged"]
        conn.execute("INSERT INTO upload_batches (uploaded_by, results) VALUES (%s, %s)", (user.get("email", ""), Jsonb(results)))
        conn.execute("DELETE FROM upload_batches WHERE id <= (SELECT max(id) FROM upload_batches) - 20")
        conn.execute("DELETE FROM upload_pending WHERE batch=%s", (batch,)); conn.commit()
        running = build_running(conn)
    if not running:
        background.add_task(run_background)
    months = len({e["month"] for e in plan if e["state"] == "ready"})
    flash(request, f"Stored {len(done)} file{'s' if len(done) != 1 else ''} ({months} month{'s' if months != 1 else ''}). "
                   + ("A rebuild is already running; it will run again to include them." if running else "Rebuilding in the background; progress is shown below."))
    return RedirectResponse("/upload", status_code=303)


@router.post("/upload/batch/{batch}/discard")
def batch_discard(request: Request, batch: str, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM upload_pending WHERE batch=%s", (batch,)); conn.commit()
    flash(request, "Discarded the files; nothing was stored.")
    return RedirectResponse("/upload", status_code=303)


# ---------------------------------------------------------------- saved profiles
@router.get("/settings/sources", response_class=HTMLResponse)
def profiles_page(request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        profiles = load_profiles(conn)
    return render(request, "sources.html", user, active="settings", src_form=request.session.pop("src_form", None), profiles=[dict(p, body=json.dumps(p["spec"], indent=1, ensure_ascii=False)) for p in profiles],
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
def profile_save(request: Request, background: BackgroundTasks, body: str = Form(...), form: str = Form("import"), user=Depends(auth.require_editor)):
    """Import a profile, or save an edited one (same name). An edit is re-applied to every file stored with it, then rebuilt."""
    try:
        spec = json.loads(body)
    except json.JSONDecodeError as e:
        flash(request, "Not saved: the profile is not valid JSON.", "bad")
        request.session["src_form"] = {"form": form, "body": body, "error": f"Not valid JSON: {e}."}   # kept, with the error under it (DS-FORM-1)
        return RedirectResponse("/settings/sources", status_code=303)
    with db.pool.connection() as conn:
        try:
            src.validate_profile(spec)
            before = conn.execute("SELECT id, spec FROM source_profiles WHERE name=%s", (str(spec["name"]).strip(),)).fetchone()
            changed = bool(before) and before["spec"] != spec
            pid = save_profile(conn, spec, commit=not changed)
            n = reapply_profile(conn, pid) if changed else 0   # commits the profile with the re-read rows, or rolls both back
        except (src.ProfileError, ValueError) as e:
            conn.rollback()
            flash(request, "Not saved: see the profile marked below.", "bad")
            request.session["src_form"] = {"form": form, "body": body, "error": f"{str(e)[:1].upper()}{str(e)[1:]}."}
            return RedirectResponse("/settings/sources", status_code=303)
        running = build_running(conn)
    if n and not running:
        background.add_task(run_background)
    if before and not changed:   # the button offers a re-read; say why none happened
        flash(request, f"No change to the profile “{spec['name']}”: nothing to re-read.")
        return RedirectResponse("/settings/sources", status_code=303)
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
