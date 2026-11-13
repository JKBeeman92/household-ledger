"""The year in review (issue #157). Standalone, like quarters.py: the standard library only, no database. make_budget_items.py emits
`year_review` with it, after the look ahead (#148) it quotes.

A budget year is twelve budget months from the `year_start_month` rule (default 1: January to December). Its label is the year it
starts in (with year_start_month 7, July 2025 to June 2026 is "2025"). The year reviewed is the budget year of the latest loaded month;
it is partial when fewer than twelve of its months are loaded ("6 of 12 months"), and then each comparison is a month at a time. Every
figure is the sum of its months (a total) with its per-month figure beside it; a prior year that is not loaded is left out, never zero.

  this, previous   income, living spend, debt payments, net (kept), and pre-tax saving: sums over the year's loaded months, with the
                   savings rate (net ÷ income) and, with pre-tax saving, (net + pre-tax) ÷ (income + pre-tax); and the real (CPI) sums.
  change           this − previous, per month (both years' totals ÷ their months), and in percent of previous where its sign holds.
  net_worth        at the end of the year's last loaded month, against the end of the month before the year (or the year's first month
                   when that one has no balances); the same for the previous year when its months are loaded.
  debt             the debts' total balance at the same two points, and the debts paid off in the year (a balance above zero at the
                   start, zero at the end), each named by its type, never its account.
  decisions        the year's checks: how many were confirmed and kept (partly counts half), and the decisions with the largest
                   dollar aims (a cap's limit, a saving's least, an extra payment), with how often each was kept.
  events           the life events dated in the year.
  movers           the categories whose spending changed most against the previous year, per month, by dollars, nominal and real;
                   the categories in `set_apart` (debt and savings groups) are left out, as on the Quarter tab.
  ahead            the look ahead's 12-month row (#148): "where next year leads".
  withholding      every recorded tax outcome (#153, a refund positive, a bill negative), oldest first, and `adjust`: true when the two
                   latest tax years are consecutive and each is a refund or a bill of at least `withholding_note_at` ("consider adjusting
                   withholding"); `adjust_kind` says which way the two went (refund, owed, or mixed)."""
from __future__ import annotations

FIELDS = ("income", "spend", "debt", "net", "pretax")


def _idx(m: str) -> int:
    return int(m[:4]) * 12 + int(m[5:]) - 1


def _mon(i: int) -> str:
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def year_of(m: str, ys: int = 1) -> tuple[str, list[str]]:
    """The budget year a month is in: (label, its twelve months)."""
    y, mm = int(m[:4]), int(m[5:])
    start = y if mm >= ys else y - 1
    first = _idx(f"{start}-{ys:02d}")
    return str(start), [_mon(first + k) for k in range(12)]


def _sums(monthly: dict[str, dict], months: list[str]) -> dict | None:
    have = [m for m in months if m in monthly]
    if not have:
        return None
    s = {f: round(sum(float(monthly[m].get(f) or 0) for m in have), 2) for f in FIELDS}
    s.update({f + "_real": round(sum(float(monthly[m].get(f + "_real") or 0) for m in have), 2) for f in FIELDS})
    s["months"], s["first"], s["last"] = len(have), have[0], have[-1]
    s["per_month"] = {f: round(s[f] / len(have), 2) for f in FIELDS}
    s["savings_rate"] = round(s["net"] / s["income"], 4) if s["income"] else None
    s["savings_rate_pretax"] = round((s["net"] + s["pretax"]) / (s["income"] + s["pretax"]), 4) if s["pretax"] and (s["income"] + s["pretax"]) else None
    return s


def _change(this: dict, prev: dict | None) -> dict | None:
    if not prev:
        return None
    out = {}
    for f in FIELDS:
        a, b = this["per_month"][f], prev["per_month"][f]
        out[f] = {"per_month": round(a - b, 2), "pct": round((a - b) / abs(b), 4) if b and (a >= 0) == (b >= 0) else None}
    for f in ("savings_rate", "savings_rate_pretax"):
        out[f] = round(this[f] - prev[f], 4) if this.get(f) is not None and prev.get(f) is not None else None
    return out


def _nw_at(nw: dict[str, dict], m: str) -> float | None:
    r = nw.get(m)
    return float(r["net_worth"]) if r and r.get("net_worth") is not None else None


def _span(nw: dict[str, dict], months: list[str]) -> dict | None:
    """Net worth at the end of the month before the year (else the year's first month) and at the end of its last loaded month."""
    have = [m for m in months if m in nw]
    if not have:
        return None
    before = _mon(_idx(months[0]) - 1)
    start_m = before if before in nw else have[0]
    start, end = _nw_at(nw, start_m), _nw_at(nw, have[-1])
    return {"from": start_m, "to": have[-1], "start": start, "end": end,
            "change": round(end - start, 2) if start is not None and end is not None else None}


def review(monthly: list[dict], cat_month: list[dict], net_worth: list[dict], balances: list[dict], decisions: list[dict], events: list[dict],
           look_ahead: list[dict], closing: str | None, ys: int = 1, set_apart: set[str] | None = None, top: int = 5,
           tax_outcomes: list[dict] | None = None, withholding_note_at: float = 1000) -> dict | None:
    """The `year_review` row for the budget year of `closing`, or None with nothing loaded."""
    if not closing:
        return None
    mon = {r["m"]: r for r in monthly}
    label, months = year_of(closing, ys)
    months = [m for m in months if m <= closing]
    this = _sums(mon, months)
    if not this:
        return None
    plabel, pmonths = year_of(_mon(_idx(months[0]) - 1), ys)
    prev = _sums(mon, pmonths)
    nw = {r["m"]: r for r in net_worth}
    # debts: total balance at the two points, and those paid off in the year (by type, never by account)
    by_acct: dict[str, dict[str, dict]] = {}
    for b in balances:
        if b.get("kind") == "debt":
            by_acct.setdefault(b["account"], {})[b["m"]] = b
    span = _span(nw, months)
    debt = None
    if span:
        def at(m):   # None when no debt has a balance for the month: unknown, never zero
            have = [float(s[m]["amount"]) for s in by_acct.values() if m in s]
            return round(sum(have), 2) if have else None
        paid_off = sorted({(s[span["from"]].get("type") or "debt") for s in by_acct.values()
                           if span["from"] in s and span["to"] in s and float(s[span["from"]]["amount"]) > 0 and float(s[span["to"]]["amount"]) <= 0})
        debt = {"start": at(span["from"]), "end": at(span["to"]), "paid_off": paid_off}
        debt["change"] = round(debt["end"] - debt["start"], 2) if debt["start"] is not None and debt["end"] is not None else None
    # decisions: the year's confirmed checks, and the decisions with the largest dollar aims
    mine = [d for d in decisions if months[0] <= d["m"] <= months[-1] and d.get("verdict")]
    kept = sum(1 for d in mine if d["verdict"] == "kept") + 0.5 * sum(1 for d in mine if d["verdict"] == "partly")
    aim = lambda d: max([float(v) for k, v in (d.get("target") or {}).items() if k in ("max", "min", "amount") and isinstance(v, (int, float))] or [0])
    by_id: dict[int, dict] = {}
    for d in mine:
        e = by_id.setdefault(d["id"], {"text": d.get("text") or "", "kind": d.get("kind"), "aim": aim(d), "checked": 0, "kept": 0.0})
        e["checked"] += 1; e["kept"] += 1 if d["verdict"] == "kept" else 0.5 if d["verdict"] == "partly" else 0
    biggest = sorted(by_id.values(), key=lambda e: (-e["aim"], e["text"]))[:3]
    # category movers, a month at a time
    movers = None
    if prev:
        def per(ms, field):
            out: dict[str, float] = {}
            for r in cat_month:
                if r["m"] in ms:
                    out[r["category"]] = out.get(r["category"], 0.0) + float(r.get(field) or 0)
            return out
        n, pn = this["months"], prev["months"]
        movers = {}
        for field, key in (("amount", "nominal"), ("amount_real", "real")):
            a, b = per(set(months), field), per(set(pmonths), field)
            rows = [{"category": c, "per_month": round(a.get(c, 0) / n, 2), "was": round(b.get(c, 0) / pn, 2),
                     "change": round(a.get(c, 0) / n - b.get(c, 0) / pn, 2)} for c in set(a) | set(b) if c not in (set_apart or set())]
            movers[key] = sorted(rows, key=lambda r: (-abs(r["change"]), r["category"]))[:top]
    ahead = next((r for r in look_ahead if r.get("h") == 12 and r.get("m") == closing), None)
    outs = sorted(({"year": str(o["year"]), "amount": round(float(o["amount"]), 2)} for o in tax_outcomes or []), key=lambda o: o["year"])
    last2 = outs[-2:]
    adjust = len(last2) == 2 and int(last2[1]["year"]) - int(last2[0]["year"]) == 1 and all(abs(o["amount"]) >= withholding_note_at for o in last2)
    kinds = {"refund" if o["amount"] >= 0 else "owed" for o in last2}
    withholding = {"outcomes": outs, "note_at": withholding_note_at, "adjust": adjust,
                   "adjust_kind": (kinds.pop() if len(kinds) == 1 else "mixed") if adjust else None} if outs else None
    return {"year": label, "first": months[0], "last": this["last"], "months": this["months"], "partial": this["months"] < 12,
            "this": this, "previous": ({**prev, "year": plabel} if prev else None), "change": _change(this, prev),
            "net_worth": span, "net_worth_previous": _span(nw, pmonths) if prev else None, "debt": debt,
            "decisions": {"checked": len(mine), "kept": kept, "rate": round(kept / len(mine), 3) if mine else None, "biggest": biggest},
            "events": [{k: e.get(k) for k in ("m", "label", "kind", "person")} for e in events if months[0] <= e["m"] <= months[-1]],
            "movers": movers,
            "ahead": {k: ahead[k] for k in ("until", "pace", "pace_3", "cash", "net_worth", "debt_free", "growth_pct")} if ahead else None,
            "withholding": withholding}
