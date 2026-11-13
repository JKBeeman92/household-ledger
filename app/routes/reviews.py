"""Reviews page: pick the month-close review provider/model and test the connection. Keys stay in the environment."""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db, auth, review, review_providers as rp
from ..web_helpers import flash, render

router = APIRouter()


@router.get("/reviews", response_class=HTMLResponse)
def reviews_list(request: Request, user=Depends(auth.require_editor)):
    """The reviews themselves, newest first; provider settings are one link away."""
    with db.pool.connection() as conn:
        rows = conn.execute("SELECT month, model, generated_at, hand_edited_at, sections FROM reviews ORDER BY month DESC").fetchall()
        prov = rp.resolve(conn)
        log = conn.execute("SELECT at, old, new FROM renames ORDER BY at").fetchall()
    # a stored review keeps its text (the record of what was said); renames since it was written are noted beside it (#133)
    later = {r["month"]: [x for x in log if x["at"] > r["generated_at"]] for r in rows}   # a hand edit keeps the generated names
    with db.pool.connection() as conn:
        job = review.latest_job(conn)   # a review being written, or one that failed, shows here too, not only on Close a month
    job = job if job and job["state"] != "stored" else None
    return render(request, "reviews_list.html", user, active="reviews", reviews=rows, prov=prov, problem=prov.problem(), later=later, job=job)


@router.get("/reviews/provider", response_class=HTMLResponse)
def reviews_page(request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        saved = db.get_setting(conn, rp.SETTING, {}) or {}
        prov = rp.resolve(conn)
        recent = conn.execute("SELECT month, model, generated_at FROM reviews ORDER BY month DESC LIMIT 6").fetchall()
    return render(request, "reviews.html", user, active="reviews", providers=rp.PROVIDERS, saved=saved, prov=prov, problem=prov.problem(), recent=recent)


@router.post("/reviews")
def reviews_save(request: Request, provider: str = Form(""), model: str = Form(""), base_url: str = Form(""), user=Depends(auth.require_editor)):
    provider, model, base_url = provider.strip().lower(), model.strip(), base_url.strip().rstrip("/")
    if provider and provider not in rp.PROVIDERS:
        flash(request, f"Not saved: unknown provider {provider}.", "bad")
    elif base_url and not base_url.startswith(("http://", "https://")):
        flash(request, "Not saved: the base URL must start with http:// or https://.", "bad")
    else:
        with db.pool.connection() as conn:
            db.put_setting(conn, rp.SETTING, {"provider": provider, "model": model, "base_url": base_url}); conn.commit()
        flash(request, "Saved. Blank fields fall back to the REVIEW_* environment.")
    return RedirectResponse("/reviews/provider", status_code=303)


@router.post("/reviews/test")
def reviews_test(request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        prov = rp.resolve(conn)
    if (why := prov.problem()):
        flash(request, f"Not configured — {why}.", "bad")
    else:
        try:
            reply = prov.complete("Reply with the single word: ok", "ping").strip()
            flash(request, f"Connected to {prov.label}. Reply: {reply[:80] or '(empty)'}")
        except Exception as e:  # noqa: BLE001
            flash(request, f"Test failed for {prov.label} — {str(e)[:400]}", "bad")
    return RedirectResponse("/reviews/provider", status_code=303)
