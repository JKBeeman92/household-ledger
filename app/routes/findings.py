"""What the last build found (unmapped items, reconciliation) and the inline fix: append to the mapping, then rebuild."""
from __future__ import annotations
import json, re

from fastapi import APIRouter, BackgroundTasks, Request, Depends, Form
from fastapi.responses import RedirectResponse

from .. import db, auth
from ..web_helpers import flash
from ..builds import run_background
from pipeline.run_pipeline import norm, build_running

router = APIRouter()
FLOWS = ["expense", "income", "debt", "transfer", "plug", "zero", "exclude", "verify"]


DRIFT = re.compile(r"\s+(?:\d+|fund|goal)$", re.I)          # "Groceries 2", "Vacation Fund", "Car Goal": the same envelope, renamed
FUND_MOVE = re.compile(r"\b(?:transfer|rebalance|redistribution)\b", re.I)


def guess(item: str, group: str) -> dict:
    """A first draft for an unmapped item: its canonical name without year-drift suffixes, and a flow from its group or name."""
    g = group.strip().lower()
    flow = "transfer" if FUND_MOVE.search(item) else "income" if g == "income" else "debt" if g == "debt" else "expense"
    return {"canonical_guess": DRIFT.sub("", item.strip()) or item, "flow_guess": flow}


def latest_findings(conn) -> dict:
    """Findings of the newest finished build, plus the known categories for the form's suggestions."""
    row = conn.execute("SELECT id, finished_at, findings FROM build_log WHERE finished_at IS NOT NULL AND findings IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    f = (row and row["findings"]) or {}
    cats = [r["category"] for r in conn.execute("SELECT DISTINCT category FROM item_mapping WHERE category <> 'UNMAPPED' ORDER BY 1").fetchall()]
    unmapped = [{**u, **guess(u.get("item", ""), u.get("group") or "")} for u in f.get("unmapped", [])]
    return {"build_id": row and row["id"], "finished_at": row and row["finished_at"], "unmapped": unmapped,
            "reconciliation": f.get("reconciliation") or {"counts": {}, "problems": [], "clean": True}, "categories": cats, "flows": FLOWS}


@router.post("/upload/map")
def map_items(request: Request, background: BackgroundTasks, item: list[str] = Form(default=[]), canonical: list[str] = Form(default=[]),
              category: list[str] = Form(default=[]), flow: list[str] = Form(default=[]), user=Depends(auth.require_editor)):
    """Rows with a category filled in are added to (or corrected in) item_mapping; rows left blank are skipped. Then rebuild."""
    rows = [(norm(i), norm(c) or norm(i), norm(g), f) for i, c, g, f in zip(item, canonical, category, flow) if norm(g)]
    bad = [r[0] for r in rows if r[3] not in FLOWS or r[2].upper() == "UNMAPPED"]
    if bad:
        flash(request, f"Not saved: choose a real category and flow for {', '.join(bad[:5])}.", "bad")
        return RedirectResponse("/upload", status_code=303)
    if not rows:
        flash(request, "Nothing to add: fill in a category for at least one item.", "bad")
        return RedirectResponse("/upload", status_code=303)
    with db.pool.connection() as conn:
        conn.cursor().executemany(
            "INSERT INTO item_mapping (item,canonical_item,category,flow) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (item) DO UPDATE SET canonical_item=EXCLUDED.canonical_item, category=EXCLUDED.category, flow=EXCLUDED.flow, updated_at=now()", rows)
        conn.commit()
        busy = build_running(conn)
    if busy:
        flash(request, f"Mapped {len(rows)} item(s). A rebuild is already running; press Rebuild when it finishes.", "bad")
    else:
        background.add_task(run_background)
        flash(request, f"Mapped {len(rows)} item(s). Rebuilding in the background; progress is shown below.")
    return RedirectResponse("/upload", status_code=303)
