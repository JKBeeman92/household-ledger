"""API tokens page (#34): a signed-in editor creates and revokes their own tokens. Only the hash is stored; the token is shown once."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db, auth
from ..web_helpers import flash, render

router = APIRouter()


def _page(request: Request, user, new=None, status: int = 200):
    with db.pool.connection() as conn:
        rows = conn.execute("SELECT id, name, scope, created_at, last_used_at FROM api_tokens WHERE owner_email=%s AND revoked_at IS NULL ORDER BY created_at DESC, id DESC",
                            (user["email"],)).fetchall()
    resp = render(request, "tokens.html", user, active="settings", tokens=rows, scopes=list(auth.SCOPES), new=new)
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"   # the one response that carries a new token must not be cached
    return resp


@router.get("/settings/tokens", response_class=HTMLResponse)
def tokens_page(request: Request, user=Depends(auth.require_editor)):
    return _page(request, user)


@router.post("/settings/tokens/new", response_class=HTMLResponse)
def token_create(request: Request, name: str = Form(""), scope: str = Form("read"), user=Depends(auth.require_editor)):
    name = " ".join(name.split())[:60]
    if not name or scope not in auth.SCOPES:
        flash(request, "Not created — give the token a name and pick a scope.", "bad")
        return RedirectResponse("/settings/tokens", status_code=303)
    raw, digest = auth.new_token()
    with db.pool.connection() as conn:
        conn.execute("INSERT INTO api_tokens (token_hash, name, scope, owner_email) VALUES (%s, %s, %s, %s)", (digest, name, scope, user["email"]))
    return _page(request, user, new={"name": name, "scope": scope, "token": raw})   # rendered here, never put in the (signed, not encrypted) session cookie


@router.post("/settings/tokens/{token_id}/revoke")
def token_revoke(request: Request, token_id: int, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        row = conn.execute("UPDATE api_tokens SET revoked_at=now() WHERE id=%s AND owner_email=%s AND revoked_at IS NULL RETURNING name",
                           (token_id, user["email"])).fetchone()
    if not row:
        raise HTTPException(404, "No such token")
    flash(request, f"Revoked {row['name']}. Requests with it now get 401.")
    return RedirectResponse("/settings/tokens", status_code=303)
