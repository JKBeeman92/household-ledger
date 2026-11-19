from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from .. import db, auth, checkin, appearance
from ..web_helpers import render, _span, _months
from .. import stale

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user=Depends(auth.require_user)):
    with db.pool.connection() as conn:
        span = _span(conn)
        freshness = stale.stale_status(_months(conn))
        ci = checkin.line(conn) if user and user.get("is_editor") else None   # the Last month tab's check-in line (#164)
        # chart colours and hidden names (#132): handed to the page once, like currency and locale, not through a dataset, because the
        # pipeline never reads them and a change needs no rebuild
        look = appearance.for_page(conn)
    return render(request, "dashboard.html", user, active="dashboard", span=span, freshness=freshness, checkin=ci, appearance=look)
