"""Operational endpoints: /metrics (Prometheus text format). /healthz stays in api.py: unauthenticated, one cheap query."""
from fastapi import APIRouter, Depends, Response

from .. import db, auth
from ..web_helpers import _months
from pipeline.run_pipeline import reconcile

router = APIRouter()


def render_metrics(conn) -> str:
    out: list[str] = []

    def metric(name: str, help_: str, value, kind: str = "gauge") -> None:
        out.append(f"# HELP {name} {help_}\n# TYPE {name} {kind}")
        if value is not None:
            out.append(f"{name} {round(float(value), 3)!r}")

    metric("ledger_months_loaded", "Budget months with uploaded exports.", len(_months(conn)))
    b = conn.execute("SELECT ok, EXTRACT(EPOCH FROM finished_at - started_at) AS dur, EXTRACT(EPOCH FROM finished_at) AS ts "
                     "FROM build_log WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    metric("ledger_last_build_ok", "1 if the last finished build succeeded, 0 if it failed; absent before any build.", None if not b else int(bool(b["ok"])))
    metric("ledger_last_build_duration_seconds", "Duration of the last finished build.", b and b["dur"])
    metric("ledger_last_build_timestamp_seconds", "Unix time the last finished build ended.", b and b["ts"])
    metric("ledger_reconciliation_problems", "Envelope-months where transactions do not match BudgetItems Spent.", len(reconcile(conn)["problems"]))
    r = conn.execute("SELECT EXTRACT(EPOCH FROM max(generated_at)) AS ts FROM reviews").fetchone()
    metric("ledger_last_review_timestamp_seconds", "Unix time the latest month-close review was generated; absent if none.", r["ts"])
    return "\n".join(out) + "\n"


@router.get("/metrics")
def metrics(user=Depends(auth.require_user_api)):
    with db.pool.connection() as conn:
        body = render_metrics(conn)
    return Response(body, media_type="text/plain; version=0.0.4; charset=utf-8")
