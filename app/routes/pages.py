from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from .. import db, auth, checkin
from ..web_helpers import render, _span, _months
from .. import stale

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user=Depends(auth.require_user)):
    with db.pool.connection() as conn:
        span = _span(conn)
        freshness = stale.stale_status(_months(conn))
        ci = checkin.line(conn) if user and user.get("is_editor") else None   # the Last month tab's check-in line (#164)
    return render(request, "dashboard.html", user, active="dashboard", span=span, freshness=freshness, checkin=ci)
