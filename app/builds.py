"""Background rebuilds: start one (refused if another is running), run it on its own connection, report its status."""
from __future__ import annotations
import logging

from . import db, hooks
from pipeline.run_pipeline import rebuild, build_running, BuildBusy

log = logging.getLogger("ledger")


def build_status(conn) -> dict:
    """The newest build_log row plus whether it is really running. A row with no finish time but no lock holder was left
    by a process that died; it reads as interrupted and never blocks a new rebuild."""
    row = conn.execute("SELECT id, started_at, finished_at, ok, summary, detail FROM build_log ORDER BY id DESC LIMIT 1").fetchone()
    running = build_running(conn)
    if row is None:
        return {"running": running, "id": None, "state": "running" if running else "never", "started_at": None, "finished_at": None,
                "ok": None, "summary": None, "problems": [], "problem_count": 0}
    state = "running" if running and row["finished_at"] is None else "interrupted" if row["finished_at"] is None else "ok" if row["ok"] else "failed"
    problems = [l[len("Reconciliation: "):] for l in (row["detail"] or "").splitlines() if l.startswith("Reconciliation: ")]
    summary = "the process stopped before this build finished" if state == "interrupted" or (row["summary"] or "").startswith("interrupted:") else row["summary"]
    return {"running": running, "id": row["id"], "state": state, "started_at": row["started_at"], "finished_at": row["finished_at"],
            "ok": row["ok"], "summary": summary, "problems": problems[:10], "problem_count": len(problems)}


def run_background() -> None:
    """Body of the background task: rebuild on a connection of its own; failures land in build_log, never propagate."""
    try:
        with db.pool.connection() as conn:
            rebuild(conn)
        hooks.notify("build.completed")   # after the build is recorded and its connection released; logs its own failures, never raises
    except BuildBusy:
        log.info("background rebuild skipped: another rebuild is running")
    except Exception as e:  # noqa: BLE001
        log.error("background rebuild failed: %s", str(e)[:500])

