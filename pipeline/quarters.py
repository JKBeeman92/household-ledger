"""The quarter (issue #147). Standalone, like balances.py and payoff.py: the standard library only, no database. make_datasets.py emits
`quarterly`, `cat_quarter` and `quarter_review` with it; `python -m app.admin quarter` runs it against an install's built datasets.

A quarter is three budget months, counted from the `year_start_month` rule (default 1: Q1 is the January, February and March budget
months). Its label is the year its budget year starts in and the quarter: with year_start_month 7, July to September 2026 is
"2026-Q1". A quarter's figures are the sums of its months; a quarter with fewer than three months loaded is partial ("2 of 3 months")
and every comparison is per month, never a total of unequal length. A quarter that is not loaded at all is left out of a comparison,
never shown as zero.

Movers are living spend: the categories in `set_apart` (the debt and savings groups) are left out of the top five, since the debt line
already shows them, and listed on their own. A mover against the quarter before is marked `also_last_year` when the same quarter a
year before moved the same way by at least half as much: a yearly bill or a season, not a change in habit. A change in percent across
a change of sign (net turning negative) is not given; `turned` says which way it went.
"""
from __future__ import annotations

FIELDS = ("income", "spend", "debt", "net")


def _idx(m: str) -> int:
    return int(m[:4]) * 12 + int(m[5:]) - 1


def _mon(i: int) -> str:
    return f"{i // 12}-{i % 12 + 1:02d}"


def quarter_of(m: str, ys: int = 1) -> tuple[str, list[str]]:
    """('2026-Q3', its three budget months) for the budget month m, with the year starting in month ys."""
    pos = (int(m[5:]) - ys) % 12
    first = _idx(m) - pos % 3
    fy = int(m[:4]) - (1 if int(m[5:]) < ys else 0)
    return f"{fy}-Q{pos // 3 + 1}", [_mon(first + k) for k in range(3)]


def shift(label: str, k: int) -> str:
    """The quarter k quarters after `label` (negative: before)."""
    y, q = int(label[:4]), int(label[-1]) - 1 + k
    return f"{y + q // 4}-Q{q % 4 + 1}"


def quarterly(monthly: list[dict], ys: int = 1) -> list[dict]:
    """One row per budget quarter with any month loaded: the sums of its months, nominal and real, and how many months it has."""
    out: dict[str, dict] = {}
    for r in sorted(monthly, key=lambda r: r["m"]):
        q, months = quarter_of(r["m"], ys)
        o = out.setdefault(q, {"quarter": q, "first": months[0], "last": months[-1], "months": 0, "loaded": [],
                               **{f: 0.0 for f in FIELDS}, **{f + "_real": 0.0 for f in FIELDS}, "pretax": None})
        o["months"] += 1; o["loaded"].append(r["m"])
        for f in FIELDS:
            o[f] += float(r.get(f) or 0); o[f + "_real"] += float(r.get(f + "_real") or 0)
        if r.get("pretax") is not None:
            o["pretax"] = (o["pretax"] or 0.0) + float(r["pretax"])
    rows = []
    for o in out.values():
        for f in FIELDS:
            o[f] = round(o[f], 2); o[f + "_real"] = round(o[f + "_real"], 2)
        o["savings_rate"] = round(o["net"] / o["income"], 4) if o["income"] else None
        o["partial"] = o["months"] < 3
        rows.append(o)
    return sorted(rows, key=lambda o: o["first"])


def cat_quarter(cat_month: list[dict], ys: int = 1) -> list[dict]:
    out: dict[tuple, dict] = {}
    for r in cat_month:
        q, _ = quarter_of(r["m"], ys)
        o = out.setdefault((q, r["category"]), {"quarter": q, "category": r["category"], "amount": 0.0, "amount_real": 0.0})
        o["amount"] += float(r.get("amount") or 0); o["amount_real"] += float(r.get("amount_real") or 0)
    return [{**o, "amount": round(o["amount"], 2), "amount_real": round(o["amount_real"], 2)} for o in sorted(out.values(), key=lambda o: (o["quarter"], o["category"]))]


def _per_month(row: dict | None, f: str) -> float | None:
    return round(row[f] / row["months"], 2) if row and row["months"] and row.get(f) is not None else None


def _pct(a: float | None, b: float | None) -> tuple[float | None, str | None]:
    """(change in percent, None), or (None, 'negative'/'positive') when the sign flipped: -358% says nothing."""
    if a is None or b in (None, 0):
        return None, None
    if (a < 0) != (b < 0) and a != 0:
        return None, "negative" if a < 0 else "positive"
    return round((a - b) / abs(b), 4), None


def compare(q: str, rows: list[dict], cats: list[dict], net_worth: list[dict] | None = None, totals: list[dict] | None = None,
            coming_up: list[dict] | None = None, monthly: list[dict] | None = None, ys: int = 1, top: int = 5,
            set_apart: set[str] | None = None) -> dict | None:
    """The quarter q against the one before and the same one a year before, per month; the top movers; net worth and debt balance
    change over the quarter; and a look at the next quarter. None when q is not loaded."""
    by = {r["quarter"]: r for r in rows}
    this = by.get(q)
    if not this:
        return None
    prev, ago = by.get(shift(q, -1)), by.get(shift(q, -4))
    real = lambda f: f + "_real"

    def vs(other):
        if not other:
            return None   # not loaded: hidden, never zero
        d = {"quarter": other["quarter"], "months": other["months"], "partial": other["partial"]}
        for f in FIELDS:
            for k in (f, real(f)):
                a, b = _per_month(this, k), _per_month(other, k)
                d[k] = b; d[k + "_change"] = round(a - b, 2) if a is not None and b is not None else None
                d[k + "_pct"], d[k + "_turned"] = _pct(a, b)
        d["savings_rate"] = other["savings_rate"]
        d["savings_rate_change"] = round(this["savings_rate"] - other["savings_rate"], 4) if this["savings_rate"] is not None and other["savings_rate"] is not None else None
        return d

    by_q = {r["quarter"]: r for r in rows}
    cq = {(c["quarter"], c["category"]): c for c in cats}
    apart = set(set_apart or ())

    def pm(quarter, n, field="amount"):
        r = by_q.get(quarter)
        return (cq.get((quarter, n)) or {}).get(field, 0.0) / r["months"] if r and r["months"] else None

    def movers(other, seasonal=False):
        if not other:
            return None
        names = sorted({c["category"] for c in cats if c["quarter"] in (q, other["quarter"])})
        out = []
        for n in names:
            a, b = pm(q, n), pm(other["quarter"], n)
            pct = _pct(a, b)[0]
            ar, br = pm(q, n, "amount_real"), pm(other["quarter"], n, "amount_real")   # the same in one month's prices: the Real toggle
            x = {"category": n, "per_month": round(a, 2), "was": round(b, 2), "change": round(a - b, 2), "pct": pct, "new": not b,
                 "per_month_real": round(ar, 2), "was_real": round(br, 2), "change_real": round(ar - br, 2)}
            if seasonal:   # last year's same step: the same quarter a year before against the one before it
                la, lb = pm(shift(q, -4), n), pm(shift(other["quarter"], -4), n)
                x["also_last_year"] = la is not None and lb is not None and (la - lb) * (a - b) > 0 and abs(la - lb) >= abs(a - b) / 2
            out.append(x)
        living, kept_apart = [r for r in out if r["category"] not in apart], [r for r in out if r["category"] in apart]
        by_dollars = sorted(living, key=lambda r: (-abs(r["change"]), r["category"]))[:top]   # ties by name: the same on every run
        by_pct = sorted([r for r in living if r["pct"] is not None and abs(r["was"]) >= 25], key=lambda r: (-abs(r["pct"]), r["category"]))[:top]   # not a $3 line doubling
        return {"by_dollars": by_dollars, "by_pct": by_pct,
                "set_apart": sorted([r for r in kept_apart if r["change"]], key=lambda r: (-abs(r["change"]), r["category"]))}

    def change_over(rows_by_m: dict, value) -> float | None:
        start, end = _mon(_idx(this["first"]) - 1), this["last"] if this["last"] in rows_by_m else (this["loaded"][-1] if this["loaded"] else None)
        if end not in rows_by_m or start not in rows_by_m:
            return None
        return round(value(rows_by_m[end]) - value(rows_by_m[start]), 2)

    nw = {r["m"]: r for r in net_worth or []}
    debt = {}
    for t in totals or []:
        if t.get("kind") == "debt":
            debt[t["m"]] = debt.get(t["m"], 0.0) + float(t["amount"])
    nxt, months_next = shift(q, 1), quarter_of(_mon(_idx(this["first"]) + 3), ys)[1]
    by_m = {r["m"]: r for r in monthly or []}
    ahead = {}
    for f in ("income", "spend", "debt"):   # the same calendar months a year before, so a season keeps its shape
        vals = [by_m[_mon(_idx(m) - 12)][f] for m in months_next if _mon(_idx(m) - 12) in by_m]
        ahead[f] = round(sum(vals) / len(vals), 2) if vals else None   # a month, like every other figure here
    return {"quarter": q, "first": this["first"], "last": this["last"], "months": this["months"], "partial": this["partial"],
            "this": {**{k: _per_month(this, k) for f in FIELDS for k in (f, real(f))}, "savings_rate": this["savings_rate"],
                     **{"total_" + f: this[f] for f in FIELDS}},
            "previous": vs(prev), "year_ago": vs(ago),
            "movers_previous": movers(prev, seasonal=True), "movers_year_ago": movers(ago),
            "net_worth_change": change_over(nw, lambda r: float(r["net_worth"])),
            "debt_change": change_over({m: {"v": v} for m, v in debt.items()}, lambda r: r["v"]),
            "next": {"quarter": nxt, "months": months_next, "expected": ahead, "basis": "the same months a year before",
                     "coming_up": [c for c in coming_up or [] if c["m"] in months_next]}}
