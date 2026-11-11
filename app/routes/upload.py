from fastapi import APIRouter, BackgroundTasks, Request, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import settings
from .. import db, auth, review
from ..web_helpers import flash, render, _months
from ..uploads import MAX_FILE_BYTES, expand_zip, is_zip, Item
from psycopg.types.json import Jsonb
from ..builds import build_status, run_background
from .findings import latest_findings
from .checklist import checklist_goals
from pipeline.run_pipeline import ingest, build_running, FN_RX
from .sources import stash

router = APIRouter()


@router.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        files = conn.execute("SELECT f.budget_month, f.kind, f.filename, f.row_count, f.uploaded_at, f.sha256, h.event_at AS replaced_at, h.sha256 AS previous_sha256 FROM source_files f "
                             "LEFT JOIN LATERAL (SELECT event_at, sha256 FROM source_file_history WHERE kind=f.kind AND budget_month=f.budget_month AND event='replaced' AND event_at = f.uploaded_at "
                             "ORDER BY id DESC LIMIT 1) h ON true ORDER BY f.budget_month DESC, f.kind").fetchall()
        months = _months(conn)
        build = build_status(conn)
        findings = latest_findings(conn)
        checklist = checklist_goals(conn)
        review_ok = review.providers.is_configured(conn)
        last = conn.execute("SELECT created_at, results FROM upload_batches ORDER BY id DESC LIMIT 1").fetchone()
    return render(request, "upload.html", user, active="upload", findings=findings, checklist=checklist, files=files, months=months, build=build, review_enabled=review_ok, last_upload=last, max_upload_mb=settings.max_upload_mb, import_dir_set=bool(settings.import_dir))


async def _read_capped(f: UploadFile) -> bytes | None:
    data = await f.read(MAX_FILE_BYTES + 1)
    return None if len(data) > MAX_FILE_BYTES else data


@router.post("/upload")
async def upload(request: Request, background: BackgroundTasks, files: list[UploadFile] = File(...), user=Depends(auth.require_editor)):
    items: list[Item] = []
    for f in files:
        name = f.filename or ""
        data = await _read_capped(f)
        if data is None:
            items.append(Item(name, error=f"larger than {MAX_FILE_BYTES // 2**20} MB"))
        elif is_zip(name, data):
            items += expand_zip(name, data)
        else:
            items.append(Item(name, data))
    # files not named like EveryDollar's exports go to the mapping step (#96); a zip stays an EveryDollar back catalogue
    loose = [it for it in items if it.data is not None and not it.source and not FN_RX.match(it.name)]
    other = [it for it in loose if it.name.lower().endswith(".csv")]
    items = [it for it in items if it not in loose] + [
        Item(it.name, error="not an EveryDollar export filename (<hex>-MM-YYYY-EveryDollar-Transactions|BudgetItems.csv), "
                            "and not a CSV another budget tool could have written") for it in loose if it not in other]
    if len(other) > 2:
        for it in other:
            items.append(Item(it.name, error="more than two files that are not EveryDollar exports: upload one month's pair (transactions and budget) at a time"))
        other = []
    results, stored = [], 0
    with db.pool.connection() as conn:
        batch = stash(conn, [(it.name, it.data) for it in other], user.get("email", "")) if other else None
        for it in items:
            res = {"name": it.name, "source": it.source, "ok": False, "detail": it.error}
            if it.data is not None:
                try:
                    info = ingest(conn, it.name, it.data, uploaded_by=user.get("email", ""))
                    res.update(ok=True, detail=f"{info['budget_month']} {info['kind']}, {info['rows']} rows"); stored += 1
                except Exception as e:  # noqa: BLE001
                    conn.rollback(); res["detail"] = str(e)[:300]
            results.append(res)
        # per-file results live in the database: a zip of a back catalogue would overflow the ~4 KB session cookie
        if results:   # only files waiting for the mapping step: nothing was stored or rejected yet
            conn.execute("INSERT INTO upload_batches (uploaded_by, results) VALUES (%s, %s)", (user.get("email", ""), Jsonb(results)))
            conn.execute("DELETE FROM upload_batches WHERE id <= (SELECT max(id) FROM upload_batches) - 20")
            conn.commit()
        if stored:
            if build_running(conn):
                flash(request, f"Stored {stored} file{'s' if stored != 1 else ''}. A rebuild is already running; it will run again to include them.", "bad")
            else:
                background.add_task(run_background)
                flash(request, f"Stored {stored} file{'s' if stored != 1 else ''}. Rebuilding in the background; progress is shown below.")
    if stored < len(results):
        flash(request, f"{len(results) - stored} of {len(results)} files were not stored; see the upload result below.", "bad")
    if batch:
        return RedirectResponse(f"/upload/map/{batch}", status_code=303)
    return RedirectResponse("/upload", status_code=303)


@router.post("/rebuild")
def rebuild_now(request: Request, background: BackgroundTasks, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        busy = build_running(conn)
    if busy:
        flash(request, "A rebuild is already running; wait for it to finish.", "bad")
    else:
        background.add_task(run_background)
        flash(request, "Rebuilding in the background; progress is shown below.")
    return RedirectResponse("/upload", status_code=303)


@router.post("/review/generate")
def review_generate(request: Request, month: str = Form(...), confirm: str = Form(""), user=Depends(auth.require_editor)):
    if confirm != "yes":   # the cost guard: a provider call only follows the confirmation page
        return RedirectResponse(f"/reviews/confirm?month={month}", status_code=303)
    with db.pool.connection() as conn:
        try:
            res = review.generate(conn, month)
            flash(request, f"Review written for {month} ({len(res['sections'])} sections). See the dashboard's Last month tab.")
        except Exception as e:  # noqa: BLE001
            conn.rollback(); flash(request, f"Review failed: {str(e)[:800]}", "bad")
    return RedirectResponse("/upload", status_code=303)
