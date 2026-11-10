from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .. import db, auth
from ..web_helpers import _months

router = APIRouter()


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    if header.strip() == "*":
        return True
    return any(t.strip().removeprefix("W/") == etag.removeprefix("W/") for t in header.split(","))


@router.get("/api/datasets")
def api_datasets(request: Request, user=Depends(auth.require_user_api)):
    """All datasets as one JSON object. Revalidated with an ETag from the build time, row count and sizes of `datasets`."""
    with db.pool.connection() as conn:
        v = conn.execute("SELECT max(built_at) AS t, count(*) AS n, coalesce(sum(row_count), 0) AS rc FROM datasets").fetchone()
        etag = 'W/"%s-%s-%s"' % (v["t"].timestamp() if v["t"] else 0, v["n"], v["rc"])
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        rows = conn.execute("SELECT name, rows FROM datasets").fetchall()
    return JSONResponse({r["name"]: r["rows"] for r in rows}, headers=headers)


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
