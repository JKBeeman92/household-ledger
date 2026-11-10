import logging
from urllib.parse import quote

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth
from ..web_helpers import render

log = logging.getLogger("ledger")
router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login(request: Request, next: str = "/", error: str = ""):
    if auth.current_user(request):
        return RedirectResponse(auth._safe_next(next))
    return render(request, "login.html", None, next=next, error=error)


@router.get("/auth/start")
async def auth_start(request: Request, next: str = "/"):
    return await auth.start_login(request, next)


@router.get("/auth/callback")
async def auth_callback(request: Request):
    try:
        return await auth.finish_login(request)
    except HTTPException as e:
        return RedirectResponse(f"/login?error={quote(str(e.detail), safe='')}", status_code=303)
    except Exception as e:  # noqa: BLE001
        log.exception("oidc callback failed")
        return RedirectResponse(f"/login?error={quote('sign-in failed: ' + type(e).__name__, safe='')}", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    return RedirectResponse(await auth.logout(request), status_code=303)


@router.get("/signed-out", response_class=HTMLResponse)
def signed_out(request: Request):
    """Where the IdP sends the browser after end-session (`post_logout_redirect_uri`)."""
    if auth.current_user(request):
        return RedirectResponse("/")
    return render(request, "login.html", None, next="/", error="", signed_out=True)
