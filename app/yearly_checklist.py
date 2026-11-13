"""The yearly checklist (issue #157): the household's once-a-year housekeeping, asked in the check-in's year mode. The list is
`yearly_checklist_items` (seven defaults ship, each editable or removable: Settings → Yearly checklist, `settings.zip`, the CLI); a
tick is a row of `yearly_checklist` per budget year and item, with a note. db/migrations/0157_yearly_checklist.sql."""
from __future__ import annotations
import json
import re

from psycopg.rows import dict_row


class ChecklistError(ValueError):
    def __init__(self, message: str, field: str = "label"):
        super().__init__(message); self.field = field


def _x(conn, sql, params=()):
    cur = conn.cursor(row_factory=dict_row); cur.execute(sql, params); return cur


def items(conn) -> list[dict]:
    return _x(conn, "SELECT key, label, position, takes_amount FROM yearly_checklist_items ORDER BY position, key").fetchall()


def ticks(conn, year: str) -> dict[str, dict]:
    return {r["item"]: r for r in _x(conn, "SELECT item, label, note, done_at, done_by FROM yearly_checklist WHERE year=%s", (year,)).fetchall()}


def key_for(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40] or "item"


def add(conn, label: str) -> str:
    label = (label or "").strip()
    if not label:
        raise ChecklistError("say what to do once a year, such as Review the will.")
    if len(label) > 200:
        raise ChecklistError("keep it under 200 characters.")
    key, n = key_for(label), 2
    while _x(conn, "SELECT 1 FROM yearly_checklist_items WHERE key=%s", (key,)).fetchone():
        key, n = f"{key_for(label)}_{n}", n + 1
    pos = (_x(conn, "SELECT coalesce(max(position), 0) AS p FROM yearly_checklist_items").fetchone()["p"] or 0) + 10
    _x(conn, "INSERT INTO yearly_checklist_items (key, label, position) VALUES (%s, %s, %s)", (key, label, pos))
    return key


def rename(conn, key: str, label: str) -> None:
    label = (label or "").strip()
    if not label:
        raise ChecklistError("an item needs words. To drop it, press Remove.")
    if _x(conn, "UPDATE yearly_checklist_items SET label=%s, updated_at=now() WHERE key=%s", (label[:200], key)).rowcount == 0:
        raise ChecklistError("that item is gone. Reload the page.")


def move(conn, key: str, step: int) -> None:
    """Up (-1) or down (+1) one place: swaps positions with its neighbour."""
    rows = items(conn)
    keys = [r["key"] for r in rows]
    if key not in keys:
        return
    i = keys.index(key); j = i + step
    if not 0 <= j < len(rows):
        return
    for k, r in enumerate(rows):   # renumber so equal positions cannot stall a move
        _x(conn, "UPDATE yearly_checklist_items SET position=%s WHERE key=%s", ((k + 1) * 10, r["key"]))
    a, b = rows[i]["key"], rows[j]["key"]
    _x(conn, "UPDATE yearly_checklist_items SET position=CASE key WHEN %s THEN %s ELSE %s END WHERE key IN (%s, %s)", (a, (j + 1) * 10, (i + 1) * 10, a, b))


def remove(conn, key: str) -> int:
    """Off the list; past years' ticks keep the label they had."""
    return _x(conn, "DELETE FROM yearly_checklist_items WHERE key=%s", (key,)).rowcount


def tick(conn, year: str, key: str, note: str = "", by: str = "") -> None:
    it = _x(conn, "SELECT label FROM yearly_checklist_items WHERE key=%s", (key,)).fetchone()
    if not it:
        raise ChecklistError("that item is no longer on the list. Reload the page.")
    _x(conn, "INSERT INTO yearly_checklist (year, item, label, note, done_by) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (year, item) DO UPDATE "
             "SET label=EXCLUDED.label, note=EXCLUDED.note, done_at=now(), done_by=EXCLUDED.done_by", (year, key, it["label"], (note or "").strip()[:1000], by))


def untick(conn, year: str, key: str) -> int:
    return _x(conn, "DELETE FROM yearly_checklist WHERE year=%s AND item=%s", (year, key)).rowcount


def status(conn, year: str) -> tuple[int, int]:
    """(ticked, on the list) for the year."""
    have = ticks(conn, year)
    keys = [r["key"] for r in items(conn)]
    return sum(1 for k in keys if k in have), len(keys)


def export_json(conn) -> str:
    out = {"items": [dict(r) for r in items(conn)],
           "ticks": [{**r, "done_at": r["done_at"].isoformat()} for r in _x(conn, "SELECT year, item, label, note, done_at, done_by FROM yearly_checklist ORDER BY year, item").fetchall()]}
    return json.dumps(out, indent=1)
