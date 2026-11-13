"""Irregular yearly bills (issue #151): insurance premiums, property tax, vehicle registration, annual subscriptions. Standalone, like
balances.py and payoff.py: the standard library only, no database. make_budget_items.py calls it with the built budget rows and
the household's own list (yearly_bills.csv) and emits the `yearly_bills` dataset and `yearly_bill` rows for `coming_up`.

Detection. For each expense canonical item, look at the last three budget years ending at the closing month (each a window of 12
months; a window counts only if some of it is loaded). A window is a *hit* when the item was paid in one or two months of it, all
within `yearly_bill_tolerance` months (default 1) of each other. An item with at least `yearly_bill_min_years` hits (default 2) and
no window where it was paid in three months or more is a yearly bill: due in the calendar month it is usually paid in, for about
last year's amount. A monthly or quarterly bill never qualifies.

The household's own list wins: `confirmed` (with its month, amount, cadence and funding), `added` (a bill the detector missed, such
as one paid every two years), `dismissed` (stays dismissed across rebuilds).

Sinking funds. `funded_by` names the budget envelope (a canonical item) that saves for the bill, defaulting to an item whose name
contains the bill's and says Fund (`Car Insurance` -> `Car Insurance Fund`). Set aside = Σ (planned - spent) of that envelope since
the bill last fell due. A household that keeps sinking funds as accounts names the account instead: set aside = its latest balance.
"""
from __future__ import annotations
import statistics

TOLERANCE, MIN_YEARS = 1, 2
SOURCES = ("detected", "confirmed", "added", "dismissed")


def _idx(m: str) -> int:
    return int(m[:4]) * 12 + int(m[5:]) - 1


def _mon(i: int) -> str:
    return f"{i // 12}-{i % 12 + 1:02d}"


def _near(a: int, b: int, tol: int) -> bool:
    d = abs(a - b) % 12
    return min(d, 12 - d) <= tol


def paid_by_item(rows: list[dict]) -> dict[str, dict[str, float]]:
    """{canonical item: {month: dollars paid}} over expense rows (the budget export's Spent, which reconciles with the ledger)."""
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        if r.get("flow") == "expense" and float(r.get("actual") or 0) > 0.005:
            out.setdefault(r["canonical"], {})
            out[r["canonical"]][r["m"]] = round(out[r["canonical"]].get(r["m"], 0.0) + float(r["actual"]), 2)
    return out


def detect(paid: dict[str, dict[str, float]], closing: str, loaded: set[str], tol: int = TOLERANCE, min_years: int = MIN_YEARS) -> list[dict]:
    """Candidate yearly bills: [{item, month (1-12), amount, years_seen, evidence: [months paid]}]."""
    end = _idx(closing)
    windows = [(end - 12 * (k + 1) + 1, end - 12 * k) for k in range(3)]
    out = []
    for item, by in sorted(paid.items()):
        per, bad = [], False
        for lo, hi in windows:
            if not any(lo <= _idx(m) <= hi for m in loaded):
                continue
            ms = sorted(m for m in by if lo <= _idx(m) <= hi)
            if len(ms) >= 3:
                bad = True; break
            if ms and all(_near(int(a[5:]), int(ms[0][5:]), tol) for a in ms):
                per.append(ms)
        if bad or len(per) < min_years:
            continue
        # the years must agree with each other too: the month most of them fall near, and only the years near it count
        cands = sorted({int(m[5:]) for ms in per for m in ms})
        month = max(cands, key=lambda c: (sum(1 for ms in per if any(_near(int(m[5:]), c, tol) for m in ms)), -abs(c - statistics.median_low(cands))))
        per = [ms for ms in per if any(_near(int(m[5:]), month, tol) for m in ms)]
        if len(per) < min_years:
            continue
        evidence = sorted(m for ms in per for m in ms)
        month = int(statistics.median_low(int(m[5:]) for m in evidence))
        last_year = [m for m in evidence if _idx(m) > end - 12] or evidence[-1:]
        out.append({"item": item, "month": month, "amount": round(sum(by[m] for m in last_year), 2), "years_seen": len(per), "evidence": evidence})
    return out


def guess_fund(item: str, names: list[str]) -> str | None:
    """The envelope that likely saves for the bill: an item whose name contains the bill's and says fund, else one that contains it."""
    low = item.lower()
    pool = [n for n in names if n != item and low in n.lower()]
    funds = [n for n in pool if "fund" in n.lower() or "sinking" in n.lower()]
    return sorted(funds or pool, key=len)[0] if (funds or pool) else None


def next_due(month: int, every: int, last_paid: str | None, closing: str) -> str:
    """The first budget month after `closing` the bill falls in: a cadence from its last payment, or its calendar month next."""
    end = _idx(closing)
    if last_paid:
        i = _idx(last_paid)
        while i <= end:
            i += 12 * max(1, every)
        return _mon(i)
    i = end + 1
    while (i % 12) + 1 != month:
        i += 1
    return _mon(i)


def bills(rows: list[dict], stored: list[dict], closing: str | None, loaded: set[str], items: list[str], account_balances: dict[str, float] | None = None,
          tol: int = TOLERANCE, min_years: int = MIN_YEARS) -> list[dict]:
    """The `yearly_bills` dataset: detected bills merged with the household's list, each with its next due month and its set-aside."""
    if not closing:
        return []
    paid = paid_by_item(rows)
    found = {b["item"]: b for b in detect(paid, closing, loaded, tol, min_years)}
    mine = {s["item"]: s for s in stored}
    planned = {}
    for r in rows:
        planned.setdefault(r["canonical"], {})
        planned[r["canonical"]][r["m"]] = planned[r["canonical"]].get(r["m"], 0.0) + float(r.get("planned") or 0) - float(r.get("actual") or 0)
    out = []
    for item in sorted(set(found) | set(mine)):
        d, s = found.get(item), mine.get(item)
        src = s["source"] if s else "detected"
        b = {"item": item, "source": src, "month": int((s or {}).get("month") or (d or {}).get("month") or 1),
             "amount": float((s or {}).get("amount") or 0) or (d or {}).get("amount") or 0.0, "every_years": int((s or {}).get("every_years") or 1),
             "years_seen": (d or {}).get("years_seen", 0), "evidence": (d or {}).get("evidence", []), "detected": d is not None}
        if src == "dismissed":
            out.append({**b, "funded_by": None, "funded_kind": None, "last_paid": None, "next_due": None, "set_aside": None, "per_month": None, "short": None, "missed": None})
            continue
        by = paid.get(item, {})
        last = max(by) if by else None   # the bill last fell due when it was last paid, whatever month the household now says it falls in
        fund = (s or {}).get("funded_by") or guess_fund(item, items)
        kind = "account" if fund and account_balances is not None and fund in account_balances and fund not in planned else ("envelope" if fund else None)
        if kind == "envelope":
            since = [m for m in planned.get(fund, {}) if (not last or m > last) and m <= closing]
            aside = round(sum(planned[fund][m] for m in since), 2) if fund in planned else None
        elif kind == "account":
            aside = round(float(account_balances[fund]), 2)
        else:
            aside = None
        due = next_due(b["month"], b["every_years"], last if last and _near(int(last[5:]), b["month"], tol) else None, closing)
        left = max(1, _idx(due) - _idx(closing))
        # missed: its month came round in the loaded months (and its tolerance has passed) with no payment near it. Still listed,
        # since a bill can be paid late or skipped a year, but a cancelled subscription should be dismissed, so the page says so.
        i = _idx(closing) - tol
        while i % 12 + 1 != b["month"]:
            i -= 1
        missed = None
        if b["every_years"] == 1 and _mon(i) in loaded and not any(abs(_idx(m) - i) <= tol for m in by):
            missed = _mon(i)
        short = round(max(0.0, b["amount"] - (aside or 0.0)), 2)
        out.append({**b, "funded_by": fund, "funded_kind": kind, "funded_guess": not (s or {}).get("funded_by") and bool(fund), "last_paid": last,
                    "next_due": due, "months_left": left, "missed": missed, "set_aside": aside, "short": short, "per_month": round(short / left, 2) if short else 0.0})
    return out


def coming_up_rows(bill_rows: list[dict]) -> list[dict]:
    """yearly_bill rows for `coming_up` (#144): each bill's next due month and amount; the label names the item, never an account."""
    rows = []
    for b in bill_rows:
        if b["source"] == "dismissed" or not b.get("next_due"):
            continue
        label = f"{b['item']} due" + (" (est.)" if b["source"] == "detected" else "")
        rows.append({"m": b["next_due"], "kind": "yearly_bill", "label": label, "public_label": label, "amount": b["amount"] or None,
                     "source": "estimated" if b["source"] == "detected" else "planned", "ref": b["item"]})
    return rows
