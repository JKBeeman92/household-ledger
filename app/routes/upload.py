from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import settings
from .. import db, auth, review
from ..web_helpers import flash, render, _months
from pipeline.run_pipeline import ingest, rebuild

router = APIRouter()


@router.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request, user=Depends(auth.require_user)):
    with db.pool.connection() as conn:
        files = conn.execute("SELECT budget_month, kind, filename, row_count, uploaded_at FROM source_files ORDER BY budget_month DESC, kind").fetchall()
        months = _months(conn)
        last = conn.execute("SELECT * FROM build_log ORDER BY id DESC LIMIT 1").fetchone()
    return render(request, "upload.html", user, active="upload", files=files, months=months, last_build=last, review_enabled=bool(settings.anthropic_api_key))


@router.post("/upload")
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


@router.post("/rebuild")
def rebuild_now(request: Request, user=Depends(auth.require_user)):
    with db.pool.connection() as conn:
        try:
            res = rebuild(conn)
            flash(request, f"Rebuilt: {res['summary']}.", "ok" if res["reconciliation"]["clean"] else "bad")
        except Exception as e:  # noqa: BLE001
            flash(request, f"Rebuild failed: {str(e)[:800]}", "bad")
    return RedirectResponse("/upload", status_code=303)


@router.post("/review/generate")
def review_generate(request: Request, month: str = Form(...), user=Depends(auth.require_user)):
    with db.pool.connection() as conn:
        try:
            res = review.generate(conn, month)
            flash(request, f"Review written for {month} ({len(res['sections'])} sections). See the dashboard's Last month tab.")
        except Exception as e:  # noqa: BLE001
            conn.rollback(); flash(request, f"Review failed: {str(e)[:800]}", "bad")
    return RedirectResponse("/upload", status_code=303)
