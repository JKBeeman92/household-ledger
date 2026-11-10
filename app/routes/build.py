from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

from .. import db, auth
from ..builds import build_status

router = APIRouter()


@router.get("/api/build")
def api_build(user=Depends(auth.require_user_api)):
    """Live state of the latest rebuild, polled by the Close-a-month page."""
    with db.pool.connection() as conn:
        st = build_status(conn)
    return JSONResponse(jsonable_encoder(st))
