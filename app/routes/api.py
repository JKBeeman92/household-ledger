from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from .. import db, auth
from ..web_helpers import _months

router = APIRouter()


@router.get("/api/datasets")
def api_datasets(user=Depends(auth.require_user_api)):
    with db.pool.connection() as conn:
        rows = conn.execute("SELECT name, rows FROM datasets").fetchall()
    return JSONResponse({r["name"]: r["rows"] for r in rows})


@router.get("/api/dataset/{name}")
def api_dataset(name: str, user=Depends(auth.require_user_api)):
    with db.pool.connection() as conn:
        r = conn.execute("SELECT rows FROM datasets WHERE name=%s", (name,)).fetchone()
    if not r:
        raise HTTPException(404)
    return JSONResponse(r["rows"])


@router.get("/api/status")
def api_status(user=Depends(auth.require_user_api)):
    with db.pool.connection() as conn:
        months = _months(conn)
        last = conn.execute("SELECT started_at, finished_at, ok, summary FROM build_log ORDER BY id DESC LIMIT 1").fetchone()
    return {"months": len(months), "first": months[-1] if months else None, "last": months[0] if months else None, "last_build": last}


@router.get("/healthz")
def healthz():
    with db.pool.connection() as conn:
        conn.execute("SELECT 1")
    return {"ok": True}
