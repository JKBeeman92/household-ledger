"""The monthly check-in (issue #164): one guided, resumable page per budget month, /checkin/YYYY-MM.

Steps are a registry, not a template. Each is a small module in app/checkin/ that registers a `Step`: its key, title and order,
`applies(conn, month, mode, short)`, `status(conn, month, state)` and its partial template (templates/checkin/_<key>.html). A step
whose feature has not shipped does not register; a later issue adds its module, and its step appears on every check-in not yet done
(a done one keeps the steps it had: `done_steps`). Status is one of todo, done, skipped, attention; Done is allowed when no step that
applies is todo or attention.

Modes: the last budget month of a quarter runs in quarter mode and the last of the year in year mode, the year starting at the
`year_start_month` rule (default 1, January). Steps for those modes arrive with their issues (#147, #153, #157); one check-in per month.

Catching up: months before the latest loaded month with no completed check-in get the short form (`short=True`: files, clean books,
balances); only the latest month gets the full flow and the review. Completing stays a browser action (a household's sign-off);
`python -m app.admin checkin status YYYY-MM` prints where a check-in stands."""
from __future__ import annotations
import datetime as dt
import importlib
import json
import pkgutil
import re
from dataclasses import dataclass, field
from typing import Callable

from psycopg.rows import dict_row

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
STATUSES = ("todo", "done", "skipped", "attention")
LABELS = {"todo": "to do", "done": "done", "skipped": "skipped", "attention": "needs attention"}


@dataclass
class Step:
    key: str
    order: int
    title: str
    status: Callable                        # (conn, month, state) -> (status, one-line summary)
    applies: Callable = lambda conn, month, mode, short: True
    context: Callable = lambda conn, month, state: {}   # extra template variables for the partial
    in_short: bool = False                  # part of the catch-up form for earlier months
    actions: tuple = ()                     # recorded actions the page may post: ack, skip, view
    issue: str = ""


REGISTRY: dict[str, Step] = {}


def register(step: Step) -> Step:
    REGISTRY[step.key] = step
    return step


def _x(conn, sql, params=()):
    cur = conn.cursor(row_factory=dict_row); cur.execute(sql, params); return cur


def year_start_month(conn) -> int:
    row = _x(conn, "SELECT value FROM settings WHERE key='rules'").fetchone()
    v = (row["value"] if row else {}) or {}
    v = v.get("year_start_month") if isinstance(v, dict) else None
    return v if isinstance(v, int) and 1 <= v <= 12 else 1


def mode_of(conn, month: str) -> str:
    """year on the year's last budget month, quarter on a quarter's, else month."""
    mm, ys = int(month[5:]), year_start_month(conn)
    pos = (mm - ys) % 12          # 0 = the year's first month
    return "year" if pos == 11 else "quarter" if pos % 3 == 2 else "month"


def loaded_months(conn) -> list[str]:
    return [r["budget_month"] for r in _x(conn, "SELECT DISTINCT budget_month FROM source_files ORDER BY 1").fetchall()]


def row(conn, month: str) -> dict | None:
    return _x(conn, "SELECT month, mode, steps, completed_at, completed_by, reopened_at FROM checkins WHERE month=%s", (month,)).fetchone()


def is_done(r: dict | None) -> bool:
    return bool(r and r["completed_at"] and not r["reopened_at"])


def latest(conn) -> str | None:
    ms = loaded_months(conn)
    return ms[-1] if ms else None


def neighbours(conn, month: str) -> tuple[str | None, str | None]:
    """The loaded months either side of `month`: the page turn at the foot of a month's sheet (DS-SHEET-4). None at an end."""
    ms = loaded_months(conn)
    return (max((m for m in ms if m < month), default=None), min((m for m in ms if m > month), default=None))


def missing(conn, before: str | None = None) -> list[str]:
    """Loaded months before `before` (default: the latest loaded) with no completed check-in, oldest first."""
    ms = loaded_months(conn)
    if not ms:
        return []
    before = before or ms[-1]
    done = {r["month"] for r in _x(conn, "SELECT month FROM checkins WHERE completed_at IS NOT NULL AND reopened_at IS NULL").fetchall()}
    return [m for m in ms if m < before and m not in done]


def short_form(conn, month: str) -> bool:
    """Earlier months get the catch-up form; the latest loaded month (or a month not loaded yet) the full flow."""
    last = latest(conn)
    return bool(last and month < last)


def overview(conn, month: str) -> dict:
    """Everything the page, the CLI and the Last month line need: mode, steps with status, and whether Done is allowed."""
    if not MONTH_RE.match(month or ""):
        raise ValueError("a month looks like 2026-10 (YYYY-MM)")
    r = row(conn, month)
    state = (r["steps"] if r else {}) or {}
    mode = r["mode"] if r else mode_of(conn, month)
    short = short_form(conn, month)
    if is_done(r) and state.get("done_steps"):
        keys = [k for k in state["done_steps"] if k in REGISTRY]
    else:
        keys = [k for k, s in REGISTRY.items() if s.applies(conn, month, mode, short) and (s.in_short or not short)]
    steps = []
    for k in sorted(keys, key=lambda k: REGISTRY[k].order):
        s = REGISTRY[k]
        st, summary = s.status(conn, month, state)
        if st in ("todo", "attention") and k in state.get("skipped", {}) and "skip" in s.actions:
            st, summary = "skipped", "skipped"
        steps.append({"key": k, "n": len(steps) + 1, "title": s.title, "status": st, "label": LABELS[st], "summary": summary,
                      "actions": s.actions, "template": f"checkin/_{k}.html", "context": s.context(conn, month, state)})
    blocking = [s for s in steps if s["status"] in ("todo", "attention")]
    return {"month": month, "mode": mode, "short": short, "steps": steps, "done": is_done(r), "row": r, "can_complete": not blocking,
            "to_do": len(blocking), "missing": missing(conn, month) if not short else []}


def _save_state(conn, month: str, state: dict) -> None:
    _x(conn, "INSERT INTO checkins (month, mode, steps) VALUES (%s, %s, %s::jsonb) ON CONFLICT (month) DO UPDATE SET steps=EXCLUDED.steps, updated_at=now()",
       (month, mode_of(conn, month), json.dumps(state)))


def record(conn, month: str, step: str, action: str) -> None:
    """A recorded action on a step: ack (problems acknowledged), skip, view (a read-only step was read), or undo (clears all three)."""
    s = REGISTRY.get(step)
    if not s or (action != "undo" and action not in s.actions):
        raise ValueError(f"step {step!r} takes no {action!r}")
    r = row(conn, month)
    state = dict((r["steps"] if r else {}) or {})
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for bucket, act in (("ack", "ack"), ("skipped", "skip"), ("viewed", "view")):
        b = dict(state.get(bucket) or {})
        if action == act:
            b.setdefault(step, now)
        elif action == "undo":
            b.pop(step, None)
        state[bucket] = b
    _save_state(conn, month, state)


def complete(conn, month: str, by: str) -> dict:
    """Done: refused while a step is to do or needs attention. Keeps the first completion time on a reopened check-in."""
    ov = overview(conn, month)
    if ov["done"]:
        return ov
    if not ov["can_complete"]:
        raise ValueError(f"{ov['to_do']} step{'s' * (ov['to_do'] != 1)} still to do: " + ", ".join(s["title"] for s in ov["steps"] if s["status"] in ("todo", "attention")))
    r = row(conn, month)
    state = dict((r["steps"] if r else {}) or {})
    state["done_steps"] = [s["key"] for s in ov["steps"]]
    state["done_status"] = {s["key"]: s["status"] for s in ov["steps"]}
    _x(conn, "INSERT INTO checkins (month, mode, steps, completed_at, completed_by) VALUES (%s,%s,%s::jsonb, now(), %s) "
             "ON CONFLICT (month) DO UPDATE SET steps=EXCLUDED.steps, completed_at=COALESCE(checkins.completed_at, now()), "
             "completed_by=COALESCE(checkins.completed_by, EXCLUDED.completed_by), reopened_at=NULL, updated_at=now()",
       (month, ov["mode"], json.dumps(state), by))
    return overview(conn, month)


def reopen(conn, month: str) -> None:
    _x(conn, "UPDATE checkins SET reopened_at=now(), updated_at=now() WHERE month=%s AND completed_at IS NOT NULL", (month,))


def line(conn) -> dict | None:
    """The Last month tab's line: the latest loaded month's check-in, done (with date) or the steps still to do."""
    m = latest(conn)
    if not m:
        return None
    ov = overview(conn, m)
    return {"month": m, "prev": neighbours(conn, m)[0], "done": ov["done"], "completed_at": ov["row"]["completed_at"] if ov["row"] else None, "to_do": ov["to_do"],
            "missing": len(ov["missing"])}


def _load() -> None:
    """Import every step_*.py beside this file: each registers its step."""
    for m in pkgutil.iter_modules(__path__):
        if m.name.startswith("step_"):
            importlib.import_module(f"{__name__}.{m.name}")


_load()
