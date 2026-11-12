"""Decisions (issue #155): what the household decided at a check-in, checked against the months it was in force. Standalone, like
balances.py and payoff.py: the standard library only, no database. The app (app/decisions.py, the check-in steps) and
make_budget_items.py (the `decisions` and `plan_suggestions` datasets) both judge with these functions, so the page, the check-in and
the review cannot disagree.

A decision made at the check-in of month M is in force from M+1 through `until` (one month when `until` is empty). A decision with
`target.until_payoff` stays in force until its debt's balance is 0, and is then done. For each month X in force the app works out its
own verdict from X's budget figures:

    spend_cap      {"category" | "item": name, "max": $}       kept if spent <= max; partly if within decision_partly_over (10%)
    extra_payment  {"item": the debt's item, "extra": $, "base": $}  kept if paid - base >= extra; partly if >= decision_partly_share
                   (half) of it. `base` is the usual payment when the decision was made (the median of the three months before)
    save           {"category" | "item": name, "min": $}       kept if put in >= min; partly if >= half of it
    plan_change    {"category" | "item": name, "planned": $, "from": $}   checked against X's *planned* figure: kept if within $1 of
                   planned; partly if it moved at least halfway from `from` towards it
    other, or no target                                         unknown: the household says

and two verdicts that are never the household's: `pending` while month X's budget file is not loaded and built (never "dropped" for
want of data), and `unknown` when the target names nothing in the budget (renamed away, or never there).
"""
from __future__ import annotations
import statistics

KINDS = ("spend_cap", "extra_payment", "save", "plan_change", "other")
VERDICTS = ("kept", "partly", "dropped")
AUTO = VERDICTS + ("pending", "unknown")
STATUSES = ("open", "kept", "partly", "dropped", "done")
PARTLY_OVER, PARTLY_SHARE = 0.10, 0.5   # rules decision_partly_over and decision_partly_share (build_ledger.RULES)
SOFT_CAP = 3                             # new decisions a month before the page says three is usually enough


def add_months(m: str, k: int) -> str:
    i = int(m[:4]) * 12 + int(m[5:]) - 1 + k
    return f"{i // 12}-{i % 12 + 1:02d}"


def months_in_force(d: dict, through: str) -> list[str]:
    """The months a decision is checked in, up to `through` (the latest loaded month): made_in+1 .. until, or open-ended for a
    payoff decision until it is done."""
    first = add_months(d["made_in"], 1)
    last = d.get("until") or first
    if (d.get("target") or {}).get("until_payoff") and d.get("status") in (None, "open"):
        last = through
    out, m = [], first
    while m <= min(last, through):
        out.append(m); m = add_months(m, 1)
    return out


def _field(target: dict) -> tuple[str, str] | None:
    for key, col in (("item", "canonical"), ("category", "category")):
        if target.get(key):
            return col, target[key]
    return None


def figure(target: dict, rows: list[dict], which: str = "actual") -> float | None:
    """The month's figure the target names: the sum of `actual` (or `planned`) over its budget rows; None when it names no row."""
    f = _field(target or {})
    if not f:
        return None
    mine = [r for r in rows if r.get(f[0]) == f[1]]
    return round(sum(float(r.get(which) or 0) for r in mine), 2) if mine else None


def auto(kind: str, target: dict | None, rows: list[dict] | None, over: float = PARTLY_OVER, share: float = PARTLY_SHARE) -> tuple[str, float | None]:
    """(verdict, the figure it rests on) for one decision in one month. rows: that month's budget_items rows, or None when the month
    is not built yet (pending)."""
    t = target or {}
    if kind == "other" or not t:
        return "unknown", None
    if rows is None:
        return "pending", None
    if kind == "plan_change":
        x = figure(t, rows, "planned")
        if x is None or t.get("planned") is None:
            return "unknown", x
        want, was = float(t["planned"]), t.get("from")
        if abs(x - want) < 1:
            return "kept", x
        if was is not None and float(was) != want and (x - float(was)) / (want - float(was)) >= 0.5:
            return "partly", x
        return "dropped", x
    x = figure(t, rows, "actual")
    if x is None:
        return "unknown", None
    if kind == "spend_cap" and t.get("max") is not None:
        cap = float(t["max"])
        return ("kept" if x <= cap else "partly" if x <= cap * (1 + over) else "dropped"), x
    if kind == "save" and t.get("min") is not None:
        need = float(t["min"])
        return ("kept" if x >= need else "partly" if x >= need * share else "dropped"), x
    if kind == "extra_payment" and t.get("extra") is not None:
        more, extra = x - float(t.get("base") or 0), float(t["extra"])
        return ("kept" if more >= extra else "partly" if more >= extra * share else "dropped"), x
    return "unknown", x


def usual_payment(rows: list[dict], item: str, before: str, n: int = 3) -> float:
    """The baseline an extra payment is measured from: the median paid to `item` over the n budget months before `before`."""
    months = sorted({r["m"] for r in rows if r["m"] < before})[-n:]
    paid = [sum(float(r["actual"]) for r in rows if r["m"] == m and r.get("canonical") == item) for m in months]
    return round(statistics.median(paid), 2) if paid else 0.0


def plan_suggestions(budget_rows: list[dict], keep: list[str]) -> list[dict]:
    """The Last month tab's suggested plan, per category, for the latest budget month: the average of the (up to) twelve months
    before it, rounded up to $10, or the current plan for the plan_keep_categories. The tab and the check-in's Next month step both
    read this, so they cannot disagree."""
    months = sorted({r["m"] for r in budget_rows})
    if not months:
        return []
    last, prev = months[-1], months[-13:-1]
    n = len(prev) or 1
    out = []
    for c in sorted({r["category"] for r in budget_rows}):
        cur = [r for r in budget_rows if r["m"] == last and r["category"] == c]
        planned = round(sum(float(r["planned"]) for r in cur), 2)
        avg12 = sum(float(r["actual"]) for r in budget_rows if r["m"] in prev and r["category"] == c) / n
        suggest = planned if c in keep else float(-(-avg12 // 10) * 10)   # ceil to the next $10
        flow = cur[0]["flow"] if cur else next((r["flow"] for r in budget_rows if r["category"] == c), None)
        out.append({"m": last, "category": c, "flow": flow, "planned": planned, "avg12": round(avg12, 2), "suggest": round(suggest, 2)})
    return out


def judge(decisions: list[dict], checks: dict, budget_rows: list[dict], built: set[str], through: str | None,
          over: float = PARTLY_OVER, share: float = PARTLY_SHARE) -> list[dict]:
    """The `decisions` dataset: one row per decision per month in force up to `through`, with the app's verdict and the household's.
    checks: {(decision id, month): {"verdict", "auto_verdict", "auto_figure"}} as confirmed. built: the months whose budget is built."""
    if not through:
        return []
    by_month: dict[str, list] = {}
    for r in budget_rows:
        by_month.setdefault(r["m"], []).append(r)
    out = []
    for d in decisions:
        for m in months_in_force(d, through):
            c = checks.get((d["id"], m))
            if c:   # a confirmed check keeps what it was judged on, whatever later rebuilds say
                v, x = c["auto_verdict"], c.get("auto_figure")
            else:
                v, x = auto(d["kind"], d.get("target"), by_month.get(m) if m in built else None, over, share)
            out.append({"id": d["id"], "made_in": d["made_in"], "m": m, "text": d["text"], "kind": d["kind"], "target": d.get("target"),
                        "until": d.get("until"), "person": d.get("person"), "status": d.get("status") or "open",
                        "auto_verdict": v, "auto_figure": x, "verdict": c.get("verdict") if c else None})
    out.sort(key=lambda r: (r["m"], r["id"]))
    return out


def keep_rate(rows: list[dict], through: str, months: int = 6) -> dict:
    """Over the last `months` budget months: how many checks the household confirmed, and how many of them were kept (partly
    counts half). No judgement in the words: the page says "kept 5 of 7"."""
    since = add_months(through, -(months - 1))
    mine = [r for r in rows if since <= r["m"] <= through and r.get("verdict")]
    kept = sum(1 for r in mine if r["verdict"] == "kept") + 0.5 * sum(1 for r in mine if r["verdict"] == "partly")
    return {"since": since, "through": through, "checked": len(mine), "kept": kept, "rate": round(kept / len(mine), 3) if mine else None}
