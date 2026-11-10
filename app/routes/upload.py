from fastapi import APIRouter, BackgroundTasks, Request, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import settings
from .. import db, auth, review
from ..web_helpers import flash, render, _months
from ..builds import build_status, run_background
from .findings import latest_findings
from pipeline.run_pipeline import ingest, build_running

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
    return render(request, "upload.html", user, active="upload", findings=findings, files=files, months=months, build=build, review_enabled=bool(settings.anthropic_api_key))


@router.post("/upload")
async def upload(request: Request, background: BackgroundTasks, files: list[UploadFile] = File(...), user=Depends(auth.require_editor)):
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
            if build_running(conn):
                flash(request, f"Stored {', '.join(stored)}. A rebuild is already running; press Rebuild when it finishes to include these files.", "bad")
            else:
                background.add_task(run_background)
                flash(request, f"Stored {', '.join(stored)}. Rebuilding in the background; progress is shown below.")
    for e in errors:
        flash(request, e, "bad")
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
def review_generate(request: Request, month: str = Form(...), user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        try:
            res = review.generate(conn, month)
            flash(request, f"Review written for {month} ({len(res['sections'])} sections). See the dashboard's Last month tab.")
        except Exception as e:  # noqa: BLE001
            conn.rollback(); flash(request, f"Review failed: {str(e)[:800]}", "bad")
    return RedirectResponse("/upload", status_code=303)
