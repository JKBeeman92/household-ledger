"""A look ahead (issue #148): if the household keeps the pace it has, where is it in 3, 12 and 36 months? Plain arithmetic, three
horizons, one pace; every figure is an estimate at the current pace and each is reproducible by hand from the inputs in its row. No
investment returns unless the `assumed_growth_pct` rule says so, no planned changes to income or spending (a planner, not this).
Standalone like payoff.py: no database. make_budget_items.py calls it last, once coming_up and planned_purchases are final.

As of the closing (last loaded) budget month c, for each horizon h (months after c):
  pace        monthly net (income − living spend − debt payments) averaged over the trailing 12 budget months (the default; fewer
              when fewer are loaded: `pace_months` says how many), with the trailing 3-month average (`pace_3`) beside it.
  freed       each debt with a payoff month p (#144) frees its payment P from the month after p: Σ P × max(0, h − (p − c)). It is
              added to cash, and listed on its own (`freed`), so the sum can be checked.
  cash        pace × h + freed (and `cash_3` = pace_3 × h + freed): what the household keeps over the h months.
  paid_down   what the debts' payments take off their balances over the h months: each debt amortized month by month at its payment
              and APR (balance × (1 + APR/12) − payment, never below zero; with no APR, the payment alone). Its interest is not in it.
  growth      only with `assumed_growth_pct` (a yearly %, default none): today's asset balances × ((1 + g)^(h/12) − 1). Labelled.
  net_worth   current net worth (#146) + cash + paid_down + growth (and `net_worth_3` with cash_3).
  debt_free   the latest payoff month among the debts that are not a mortgage, when every one of them has a date; None otherwise,
              with `debt_free_open` naming how many have none. Already none: `debt_free` is the closing month.
  goals       each open planned purchase (#158) with its reach month at its own pace, or "not at this pace" (reach_m None);
              those reached within h are marked.
  coming_up   the `coming_up` rows dated within the horizon.
  baseline    `pace_from`, the first month averaged. A later "new normal" (#159) may move it; until then it is the trailing window."""
from __future__ import annotations

HORIZONS = (3, 12, 36)


def _idx(m: str) -> int:
    return int(m[:4]) * 12 + int(m[5:]) - 1


def _mon(i: int) -> str:
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def _avg(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 2) if vals else None


def amortize(balance: float, payment: float, apr: float | None, months: int) -> float:
    """The balance after `months` payments at `payment`, with interest at apr/12 a month first; never below zero."""
    r = (apr or 0.0) / 12
    for _ in range(max(0, months)):
        if balance <= 0:
            return 0.0
        balance = balance * (1 + r) - payment
    return round(max(0.0, balance), 2)


def look_ahead(monthly: list[dict], payoffs: list[dict], net_worth: list[dict], balances: list[dict], goals: list[dict],
               coming_up: list[dict], closing: str | None, growth_pct: float | None = None, baseline_from: str | None = None,
               horizons: tuple[int, ...] = HORIZONS) -> list[dict]:
    """The `look_ahead` dataset: one row per horizon (see the module docstring for each figure)."""
    if not closing:
        return []
    c = _idx(closing)
    months = sorted(r["m"] for r in monthly if r["m"] <= closing and (not baseline_from or r["m"] >= baseline_from))
    net = {r["m"]: float(r["net"]) for r in monthly}
    t12, t3 = months[-12:], months[-3:]
    pace, pace_3 = _avg([net[m] for m in t12]), _avg([net[m] for m in t3])
    nw_row = next((r for r in net_worth if r["m"] == closing), None)
    nw_now = float(nw_row["net_worth"]) if nw_row and nw_row.get("net_worth") is not None else None
    assets = round(sum(float(b["amount"]) for b in balances if b["m"] == closing and b.get("kind") == "asset" and b.get("in_net_worth", True) is not False), 2)
    debts = [p for p in payoffs if p.get("m") == closing and (p.get("balance") or 0) > 0]
    dated = [p for p in debts if p.get("status") == "date" and p.get("payoff")]
    not_mortgage = [p for p in debts if p.get("type") != "mortgage"]
    open_ = [p for p in not_mortgage if not (p.get("status") == "date" and p.get("payoff"))]
    debt_free = closing if not not_mortgage else (max(p["payoff"] for p in not_mortgage) if not open_ else None)
    out = []
    for h in horizons:
        until = _mon(c + h)
        freed_by = [{"account": p["account"], "type": p.get("type"), "payoff": p["payoff"], "payment": p["payment"],
                     "months": max(0, h - (_idx(p["payoff"]) - c)), "freed": round(p["payment"] * max(0, h - (_idx(p["payoff"]) - c)), 2)}
                    for p in dated if p.get("payment")]
        freed = round(sum(f["freed"] for f in freed_by), 2)
        paid_down = round(sum(float(p["balance"]) - amortize(float(p["balance"]), float(p["payment"]), p.get("apr"), h)
                              for p in debts if p.get("payment")), 2)
        growth = round(assets * ((1 + growth_pct / 100) ** (h / 12) - 1), 2) if growth_pct and assets else 0.0
        cash = round(pace * h + freed, 2) if pace is not None else None
        cash_3 = round(pace_3 * h + freed, 2) if pace_3 is not None else None
        nw = round(nw_now + cash + paid_down + growth, 2) if nw_now is not None and cash is not None else None
        nw_3 = round(nw_now + cash_3 + paid_down + growth, 2) if nw_now is not None and cash_3 is not None else None
        gl = [{"key": g["key"], "label": g["label"], "target_amount": g["target_amount"], "target_month": g["target_month"], "reach_m": g.get("reach_m"),
               "by_target": bool(g.get("reach_m")) and g["reach_m"] <= g["target_month"] or g.get("verdict") == "reached",
               "within": g.get("verdict") == "reached" or bool(g.get("reach_m") and g["reach_m"] <= until)}
              for g in goals if not g.get("done") and g.get("target_amount")]
        out.append({"m": closing, "h": h, "until": until, "pace": pace, "pace_3": pace_3, "pace_months": len(t12), "pace_from": t12[0] if t12 else None,
                    "cash": cash, "cash_3": cash_3, "freed": freed, "freed_by": freed_by, "paid_down": paid_down,
                    "growth_pct": growth_pct or None, "assets": assets, "growth": growth, "net_worth_now": nw_now, "net_worth": nw, "net_worth_3": nw_3,
                    "debt_free": debt_free, "debt_free_open": len(open_), "debt_free_within": bool(debt_free and debt_free <= until),
                    "goals": gl, "coming_up": [{k: r.get(k) for k in ("m", "kind", "label", "public_label", "amount")} for r in coming_up if closing < r["m"] <= until]})
    return out
