"""Yearly bills (issue #151), the household's side: confirm a detected bill (and correct its month, amount, cadence or funding),
add one the detector missed, dismiss one for good, or put one back to the detector. Detection itself is pipeline/yearly_bills.py,
run at every build; this table is what the household said, and it wins."""
from __future__ import annotations
import csv, io, re

from psycopg.rows import dict_row

from . import db

COLUMNS = ["item", "month", "amount", "every_years", "funded_by", "source"]


class BillError(ValueError):
    """A bill that cannot be saved, with the form field it is about, so the page can mark that field (DS-FORM-1)."""
    def __init__(self, message: str, field: str = "item"):
        super().__init__(message); self.field = field


def _x(conn, sql, params=()):
    cur = conn.cursor(row_factory=dict_row); cur.execute(sql, params); return cur


def stored(conn) -> list[dict]:
    return _x(conn, "SELECT item, month, amount, every_years, funded_by, source FROM yearly_bills ORDER BY item").fetchall()


def built(conn) -> list[dict]:
    r = _x(conn, "SELECT rows FROM datasets WHERE name='yearly_bills'").fetchone()
    return r["rows"] if r else []


def for_page(conn) -> list[dict]:
    """The last build's bills with the household's latest word laid over them, so a save shows before the rebuild that applies it."""
    mine = {s["item"]: s for s in stored(conn)}
    out = []
    for b in built(conn):
        s = mine.pop(b["item"], None)
        out.append({**b, **({"source": s["source"], "month": s["month"], "amount": float(s["amount"]) if s["amount"] is not None else b["amount"],
                             "every_years": s["every_years"], "funded_by": s["funded_by"] or b.get("funded_by")} if s else {})})
    for s in mine.values():
        out.append({"item": s["item"], "source": s["source"], "month": s["month"], "amount": float(s["amount"]) if s["amount"] is not None else None,
                    "every_years": s["every_years"], "funded_by": s["funded_by"], "next_due": None, "set_aside": None, "years_seen": 0, "evidence": []})
    return sorted(out, key=lambda b: (b["source"] == "dismissed", b["month"], b["item"]))


def parse_amount(v) -> float | None:
    """A figure as a person types it, in any locale: currency signs and spaces dropped; with both separators the last one is the
    decimal ("1.234,50" and "1,234.50" are 1234.5); a lone comma with one or two digits after it is a decimal ("45,5"), else grouping."""
    a = re.sub(r"[^\d.,-]", "", str(v or "")).strip()
    if not a:
        return None
    if "," in a and "." in a:
        dec = "," if a.rfind(",") > a.rfind(".") else "."
        a = a.replace("." if dec == "," else ",", "").replace(",", ".")
    elif "," in a:
        a = a.replace(",", ".") if a.count(",") == 1 and len(a.split(",")[1]) <= 2 else a.replace(",", "")
    elif a.count(".") > 1:
        a = a.replace(".", "")
    return round(float(a), 2)


def save(conn, item: str, month, amount, every_years=1, funded_by: str | None = None, source: str = "confirmed", by: str = "") -> None:
    item = (item or "").strip()
    if not item:
        raise BillError("say which budget item the bill is. Pick one from the list.")
    if not _x(conn, "SELECT 1 FROM canonical_items WHERE name=%s", (item,)).fetchone():
        raise BillError(f"{item} is not one of your budget items. Pick one from the list, or add it on Settings → Item mapping.")
    if source not in ("confirmed", "added", "dismissed"):
        raise BillError("a bill is confirmed, added or dismissed.", "source")
    try:
        month = int(month)
    except (TypeError, ValueError):
        raise BillError("pick the month it usually falls in.", "month") from None
    try:
        every_years = int(every_years or 1)
    except ValueError:
        raise BillError("every is a whole number of years, from 1 to 10.", "every_years") from None
    try:   # a figure as a person types it: any currency sign or grouping is dropped
        amount = parse_amount(amount)
    except ValueError:
        raise BillError("the amount is a number, such as 960 or 960.00.", "amount") from None
    if not 1 <= month <= 12:
        raise BillError("pick the month it usually falls in.", "month")
    if not 1 <= every_years <= 10:
        raise BillError("every is a whole number of years, from 1 to 10.", "every_years")
    if amount is not None and amount < 0:
        raise BillError("the amount is not negative.", "amount")
    _x(conn, "INSERT INTO yearly_bills (item, month, amount, every_years, funded_by, source, updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s) "
             "ON CONFLICT (item) DO UPDATE SET month=EXCLUDED.month, amount=EXCLUDED.amount, every_years=EXCLUDED.every_years, "
             "funded_by=EXCLUDED.funded_by, source=EXCLUDED.source, updated_at=now(), updated_by=EXCLUDED.updated_by",
       (item, month, amount, every_years, (funded_by or "").strip() or None, source, by or ""))
    db.mark_inputs_changed(conn)


def dismiss(conn, item: str, by: str = "") -> None:
    cur = next((b for b in for_page(conn) if b["item"] == item), None)
    save(conn, item, (cur or {}).get("month") or 1, None, 1, None, "dismissed", by)


def restore(conn, item: str) -> None:
    """Back to the detector: the household's word on this item is forgotten."""
    _x(conn, "DELETE FROM yearly_bills WHERE item=%s", (item,))
    db.mark_inputs_changed(conn)


def rename_fund(conn, old: str, new: str) -> int:
    """A canonical item or account rename reaches a bill's funding, which names either (#151); the item itself cascades."""
    return _x(conn, "UPDATE yearly_bills SET funded_by=%s WHERE funded_by=%s", (new, old)).rowcount


def export_csv(conn) -> str:
    out = io.StringIO(); w = csv.writer(out, lineterminator="\n"); w.writerow(COLUMNS)
    for r in stored(conn):
        w.writerow([r["item"], r["month"], "" if r["amount"] is None else f"{r['amount']:.2f}", r["every_years"], r["funded_by"] or "", r["source"]])
    return out.getvalue()
