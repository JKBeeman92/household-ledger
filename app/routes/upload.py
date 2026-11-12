from pathlib import Path
import json
import logging
from pipeline.run_pipeline import seed_reference_tables, import_dir
from pipeline import mapping_store
from fastapi import APIRouter, BackgroundTasks, Request, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import settings
from .. import db, auth, review
from ..web_helpers import flash, render, _months, month_text, number_text
from ..uploads import MAX_FILE_BYTES, expand_zip, is_zip, Item
from psycopg.types.json import Jsonb
from ..builds import build_status, run_background
from .findings import latest_findings
from .checklist import checklist_goals
from .balances import form_context as balances_form
from pipeline.run_pipeline import ingest, build_running, FN_RX, EXPORT_NAMES
from .sources import stash

router = APIRouter()
log = logging.getLogger("ledger")


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
        bal = balances_form(conn, request.query_params.get("balances_month"))
    with db.pool.connection() as conn:
        review_job = review.latest_job(conn)
    return render(request, "upload.html", user, active="upload", bal=bal, review_job=review_job, findings=findings, checklist=checklist, files=files, months=months, build=build, review_enabled=review_ok, last_upload=last, max_upload_mb=settings.max_upload_mb, import_dir_set=bool(settings.import_dir))


async def _read_capped(f: UploadFile) -> bytes | None:
    data = await f.read(MAX_FILE_BYTES + 1)
    return None if len(data) > MAX_FILE_BYTES else data


@router.post("/upload")
async def upload(request: Request, background: BackgroundTasks, files: list[UploadFile] = File(...), next: str = Form(""), user=Depends(auth.require_editor)):
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
    # files not named like exports (internal format or EveryDollar's) go to the mapping step (#96); a zip holds named exports only
    loose = [it for it in items if it.data is not None and not it.source and not FN_RX.match(it.name)]
    other = [it for it in loose if it.name.lower().endswith(".csv")]
    items = [it for it in items if it not in loose] + [
        Item(it.name, error=f"not named like a monthly export ({EXPORT_NAMES}), "
                            "and not a CSV another budget tool could have written") for it in loose if it not in other]
    if len(other) > 2:
        for it in other:
            items.append(Item(it.name, error="more than two files that are not named like exports: upload one month's pair (transactions and budget) at a time"))
        other = []
    results, stored = [], 0
    with db.pool.connection() as conn:
        batch = stash(conn, [(it.name, it.data) for it in other], user.get("email", "")) if other else None
        for it in items:
            res = {"name": it.name, "source": it.source, "ok": False, "detail": it.error}
            if it.data is not None:
                try:
                    info = ingest(conn, it.name, it.data, uploaded_by=user.get("email", ""))
                    res.update(ok=True, detail=f"{month_text(info['budget_month'])}, {info['kind'].replace('_', ' ')}, {number_text(info['rows'])} rows"); stored += 1
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
    return RedirectResponse(auth._safe_next(next) if next else "/upload", status_code=303)   # the check-in's Files step returns to it (#164)


@router.post("/rebuild")
def rebuild_now(request: Request, background: BackgroundTasks, next: str = Form(""), user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        busy = build_running(conn)
    if busy:
        flash(request, "A rebuild is already running; wait for it to finish.", "bad")
    else:
        background.add_task(run_background)
        flash(request, "Rebuilding in the background; the dashboard updates when it finishes." if next else "Rebuilding in the background; progress is shown below.")
    return RedirectResponse(auth._safe_next(next) if next else "/upload", status_code=303)   # the banner's button returns where it was pressed


@router.post("/review/generate")
def review_generate(request: Request, background: BackgroundTasks, month: str = Form(...), confirm: str = Form(""), user=Depends(auth.require_editor)):
    if confirm != "yes":   # the cost guard: a provider call only follows the confirmation page
        return RedirectResponse(f"/reviews/confirm?month={month}", status_code=303)
    with db.pool.connection() as conn:   # the model can take minutes: queue it and return; Close a month shows progress (issue #124)
        if (why := review.providers.resolve(conn).problem()):
            flash(request, f"Review not started: no review provider is configured: {why}", "bad")
        elif (job := review.start_job(conn, month, user.get("email", ""))) is None:
            flash(request, f"A review of {month_text(month)} is already being written; its progress is below.")
        else:
            background.add_task(review.run_job, job)
    return RedirectResponse("/upload#review-job", status_code=303)


@router.get("/review/status")
def review_status(user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        job = review.latest_job(conn)
    return {"job": job and {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in job.items()}}


SAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "sample-household"   # shipped in the image (Dockerfile)


def _load_sample() -> None:
    """Background: store the sample household's exports and rebuild once (import_dir), on a connection of its own."""
    try:
        with db.pool.connection() as conn:
            import_dir(conn, SAMPLE_DIR, uploaded_by="sample")
    except Exception as e:  # noqa: BLE001  import_dir and rebuild record their own failures in build_log
        log.error("loading the sample household failed: %s", str(e)[:500])


@router.post("/setup/sample")
def setup_sample(request: Request, background: BackgroundTasks, user=Depends(auth.require_editor)):
    """First run only: give an empty install the sample household (Rosie and Sam) to look around. Refused once any month is loaded."""
    if not SAMPLE_DIR.is_dir():
        flash(request, "The sample household is not included in this installation.", "bad"); return RedirectResponse("/", status_code=303)
    with db.pool.connection() as conn:
        if conn.execute("SELECT 1 FROM source_files LIMIT 1").fetchone():
            flash(request, "The sample is only for an empty install; this one already has months loaded.", "bad"); return RedirectResponse("/", status_code=303)
        mapping_store.clear(conn); conn.execute("DELETE FROM goals"); conn.execute("DELETE FROM life_events")
        conn.execute("DELETE FROM settings WHERE key IN ('rules', 'household')")
        seed_reference_tables(conn, SAMPLE_DIR)   # fills the tables it finds empty; the CPI table is kept
        db.put_setting(conn, "household", json.loads((SAMPLE_DIR / "household.json").read_text()))
        conn.commit()
    background.add_task(_load_sample)
    flash(request, "Loading the sample household (18 invented months); the dashboard fills in when the rebuild below finishes.")
    return RedirectResponse("/upload", status_code=303)
