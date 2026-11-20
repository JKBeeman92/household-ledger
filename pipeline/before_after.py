"""Before and after a life event (issue #159). Standalone, like quarters.py and payoff.py: the standard library only, no database.
make_datasets.py emits the `before_after` dataset with it. The household's "new normal" (an event it marked) also moves the window
of the averages used elsewhere (the look ahead's pace, the emergency fund's outgo, the suggested plan): that rule is averaging.py
(#236), and this comparison's own 3/6/12 months are separate from it.

Comparison. For an event in budget month M and a window of n months (6 by default; 3 and 12 offered):
  before   the n budget months before M (M-n .. M-1)
  after    M and the n-1 months after it (M .. M+n-1): the event's own month is the first month of the new pattern
Each side counts only the months loaded (and, for the after side, up to `through`), so a side may hold fewer than n months: its
`months` says how many and `short` is true. Every figure is a month's average over that side's loaded months, so sides of unequal
length still compare fairly: income, living spend (`spend`), debt payments, net and the savings rate (net ÷ income), nominal and in
one month's prices (`*_real`, the CPI toggle). An event with no month loaded on one side has no row.
  movers          living-spend categories by the change in a month's spend (`by_dollars`), and by percent (`by_pct`, only where the
                  before side spent at least $25 a month, so a $3 line doubling is not news); the debt and savings groups are listed
                  apart (`set_apart`), as on the Quarter tab; `by_dollars_real` and `by_pct_real` rank the same in real dollars
  new_categories  categories with spending on the after side and none in any loaded month before M, whose first spending falls in
                  the event's first MIN_MONTHS months (M .. M+2, or the whole after side when n is shorter): "new since <event>". A
                  category that first appears late in a long after side began with something else, not with the event

The new normal. An event marked `new_normal` (the household ticks it; never automatic) is flagged in its rows (`new_normal`); the
windows it moves elsewhere are averaging.window's.
"""
from __future__ import annotations

NS = (3, 6, 12)
N_DEFAULT = 6
MIN_MONTHS = 3
FIELDS = ("income", "spend", "debt", "net")
PCT_FLOOR = 25   # a month's spend before, in dollars, below which a change in percent is not a mover


def _idx(m: str) -> int:
    return int(m[:4]) * 12 + int(m[5:]) - 1


def _mon(i: int) -> str:
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def add_months(m: str, k: int) -> str:
    return _mon(_idx(m) + k)


def is_new_normal(e: dict) -> bool:
    return e.get("new_normal") is True


def _pct(a: float | None, b: float | None) -> float | None:
    """The change in percent, or None across a change of sign or from zero (-358% says nothing)."""
    if a is None or b in (None, 0) or ((a < 0) != (b < 0) and a != 0):
        return None
    return round((a - b) / abs(b), 4)


def _side(months: list[str], monthly: dict, first: str, last: str) -> dict | None:
    if not months:
        return None
    n = len(months)
    s = {"from": first, "to": last, "months": n}
    for f in FIELDS:
        for k in (f, f + "_real"):
            s[k] = round(sum(float(monthly[m].get(k) or 0) for m in months) / n, 2)
    s["savings_rate"] = round(s["net"] / s["income"], 4) if s["income"] else None
    s["savings_rate_real"] = round(s["net_real"] / s["income_real"], 4) if s["income_real"] else None
    return s


def compare(event: dict, n: int, monthly: list[dict], cat_month: list[dict], through: str | None = None,
            set_apart: set[str] | None = None, top: int = 5) -> dict | None:
    """One `before_after` row: the event against its n months before and after (see the module docstring). None when either side
    has no month loaded."""
    by_m = {r["m"]: r for r in monthly if not through or r["m"] <= through}
    m0 = event["m"]
    b_first, b_last, a_first, a_last = add_months(m0, -n), add_months(m0, -1), m0, add_months(m0, n - 1)
    before = sorted(m for m in by_m if b_first <= m <= b_last)
    after = sorted(m for m in by_m if a_first <= m <= a_last)
    if not before or not after:
        return None
    b, a = _side(before, by_m, b_first, b_last), _side(after, by_m, a_first, a_last)
    change = {}
    for f in FIELDS:
        for k in (f, f + "_real"):
            change[k] = round(a[k] - b[k], 2); change[k + "_pct"] = _pct(a[k], b[k])
    for k in ("savings_rate", "savings_rate_real"):
        change[k] = round(a[k] - b[k], 4) if a[k] is not None and b[k] is not None else None   # in points of the rate
    cats: dict[str, dict] = {}
    seen_before: set[str] = set()
    new_by = add_months(m0, min(MIN_MONTHS, n) - 1)   # the last month a category can first appear in and be new since the event
    starts_early: set[str] = set()
    for r in cat_month:
        m = r["m"]
        if through and m > through:
            continue
        amt, amt_r = float(r.get("amount") or 0), float(r.get("amount_real") or 0)
        if m < m0 and amt:
            seen_before.add(r["category"])
        if m0 <= m <= new_by and amt and m in after:
            starts_early.add(r["category"])
        side = "b" if m in before else "a" if m in after else None
        if side:
            c = cats.setdefault(r["category"], {"b": 0.0, "a": 0.0, "b_real": 0.0, "a_real": 0.0})
            c[side] += amt; c[side + "_real"] += amt_r
    rows = []
    for name, c in sorted(cats.items()):
        pb, pa, pbr, par = c["b"] / len(before), c["a"] / len(after), c["b_real"] / len(before), c["a_real"] / len(after)
        rows.append({"category": name, "before": round(pb, 2), "after": round(pa, 2), "change": round(pa - pb, 2), "pct": _pct(pa, pb) if pb >= PCT_FLOOR else None,
                     "before_real": round(pbr, 2), "after_real": round(par, 2), "change_real": round(par - pbr, 2), "pct_real": _pct(par, pbr) if pbr >= PCT_FLOOR else None,
                     "new": name not in seen_before and name in starts_early and pa != 0})
    apart = set(set_apart or ())
    living = [r for r in rows if r["category"] not in apart and r["change"]]
    living_real = [r for r in rows if r["category"] not in apart and r["change_real"]]   # the Dollars switch ranks in real terms too
    return {"m": m0, "label": event["label"], "kind": event.get("kind"), "person": event.get("person"), "new_normal": is_new_normal(event), "n": n,
            "short": b["months"] < n or a["months"] < n, "before": b, "after": a, "change": change,
            "movers": {"by_dollars": sorted(living, key=lambda r: (-abs(r["change"]), r["category"]))[:top],
                       "by_pct": sorted([r for r in living if r["pct"] is not None], key=lambda r: (-abs(r["pct"]), r["category"]))[:top],
                       "by_dollars_real": sorted(living_real, key=lambda r: (-abs(r["change_real"]), r["category"]))[:top],
                       "by_pct_real": sorted([r for r in living_real if r["pct_real"] is not None], key=lambda r: (-abs(r["pct_real"]), r["category"]))[:top],
                       "set_apart": sorted([r for r in rows if r["category"] in apart and r["change"]], key=lambda r: (-abs(r["change"]), r["category"]))},
            "new_categories": [{"category": r["category"], "after": r["after"], "after_real": r["after_real"]} for r in rows if r["new"]]}


def before_after(events: list[dict], monthly: list[dict], cat_month: list[dict], through: str | None = None,
                 set_apart: set[str] | None = None, ns: tuple[int, ...] = NS) -> list[dict]:
    """The `before_after` dataset: a row per life event and window length with a loaded month on both sides, by month and label."""
    out = []
    for e in sorted(events, key=lambda e: (e["m"], e["label"])):
        for n in ns:
            r = compare(e, n, monthly, cat_month, through, set_apart)
            if r:
                out.append(r)
    return out
