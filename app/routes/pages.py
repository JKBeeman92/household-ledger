from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from .. import db, auth
from ..web_helpers import render, _span

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user=Depends(auth.require_user)):
    with db.pool.connection() as conn:
        span = _span(conn)
    return render(request, "dashboard.html", user, active="dashboard", span=span)
