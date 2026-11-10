"""Remove a loaded month. Raw rows go only by deleting their source file (never edited); the ledger is then rebuilt in the background."""
import re

from fastapi import APIRouter, BackgroundTasks, Request, Depends
from fastapi.responses import RedirectResponse

from .. import db, auth
from ..web_helpers import flash
from ..builds import run_background
from pipeline.run_pipeline import build_running, record_history

router = APIRouter()


@router.post("/months/{month}/delete")
def delete_month(month: str, request: Request, background: BackgroundTasks, user=Depends(auth.require_editor)):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        flash(request, f"{month!r} is not a budget month.", "bad")
        return RedirectResponse("/upload", status_code=303)
    with db.pool.connection() as conn:
        record_history(conn.cursor(), "deleted", user.get("email", ""), budget_month=month)
        n = conn.execute("DELETE FROM source_files WHERE budget_month=%s", (month,)).rowcount
        conn.commit()
        busy = build_running(conn) if n else False
    if not n:
        flash(request, f"No files are loaded for {month}.", "bad")
    elif busy:
        flash(request, f"Deleted both files for {month}. A rebuild is already running; it will run again to drop the month from the ledger.", "bad")
    else:
        background.add_task(run_background)
        flash(request, f"Deleted both files for {month}. Rebuilding in the background; progress is shown below.")
    return RedirectResponse("/upload", status_code=303)
