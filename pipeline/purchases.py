"""Planned purchases (issue #158): a goal with an amount and a month, saved for in a budget envelope or an account, and whether the
pace gets there. Plain arithmetic a person can redo by hand; no DB (make_budget_items.py calls it beside the scripts, the app reads
the dataset it writes).

For each dated goal, as of the closing (last loaded) budget month:
  saved       envelope: Σ (planned − spent) of the envelope over the budget months from `since_month` to the closing month, both
              included (the same envelope arithmetic as a yearly bill's set-aside, #151); account: its balance at the closing month,
              else its latest balance before it.
  months_left the budget months after the closing month up to and including the target month (March 2028 from June 2026 is 21).
  needed      (target − saved) ÷ months_left, per month. None once the target month has come with the goal not reached.
  pace        what was actually put aside a month, on average over the last three budget months: envelope, Σ (planned − spent) over
              the closing month and the two before it ÷ 3; account, (balance now − balance three months before) ÷ 3. None without
              the months to measure it.
  verdict     reached (saved ≥ target), past (the target month has come, not reached), ahead (pace ≥ needed × 1.1), on_pace
              (pace ≥ needed), short (pace < needed; short_by = needed − pace a month), or no_pace (no pace to judge by).
  reach_m     at the current pace, the budget month the target is reached (the closing month plus ⌈(target − saved) ÷ pace⌉);
              None with no positive pace. The look ahead (#148) reads it.
A goal marked bought (`done_at`) has verdict done and no figures beyond what it had; it leaves coming_up."""
from __future__ import annotations
import math

COLUMNS = ("target_amount", "target_month", "funded_by", "funded_kind", "since_month", "done_at", "done_month")   # 0158_dated_goals.sql
AHEAD = 1.1   # a pace at least this many times what is needed reads "ahead", not just "on pace"


def _idx(m: str) -> int:
    return int(m[:4]) * 12 + int(m[5:]) - 1


def _mon(i: int) -> str:
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def envelope_net(rows: list[dict]) -> dict[str, dict[str, float]]:
    """{canonical item: {budget month: planned − spent}} from the budget rows ({canonical, m, planned, actual})."""
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        by = out.setdefault(r["canonical"], {})
        by[r["m"]] = by.get(r["m"], 0.0) + float(r.get("planned") or 0) - float(r.get("actual") or 0)
    return out


def _balance_at(series: dict[str, float], m: str) -> float | None:
    """An account's balance at month m, else its latest before m."""
    before = [x for x in series if x <= m]
    return series[max(before)] if before else None


def is_dated(g: dict) -> bool:
    return g.get("target_amount") is not None and bool(g.get("target_month"))


def progress(goals: list[dict], rows: list[dict], closing: str | None, balances: list[dict] | None = None) -> list[dict]:
    """The `planned_purchases` dataset: one row per dated goal (see the module docstring for each figure).
    goals: the goals table's rows; rows: the budget rows; balances: the `balances` dataset ({account, m, amount})."""
    if not closing:
        return []
    net = envelope_net(rows)
    acct: dict[str, dict[str, float]] = {}
    for b in balances or []:
        if b.get("amount") is not None:
            acct.setdefault(b["account"], {})[b["m"]] = float(b["amount"])
    c = _idx(closing)
    out = []
    for g in sorted((g for g in goals if is_dated(g)), key=lambda g: (g["target_month"], g["key"])):
        target, tm = round(float(g["target_amount"]), 2), g["target_month"]
        fund, kind = g.get("funded_by") or None, g.get("funded_kind") or None
        since = g.get("since_month") or closing
        r = {"key": g["key"], "label": g.get("label") or g["key"], "target_amount": target, "target_month": tm, "funded_by": fund,
             "funded_kind": kind, "since_month": since, "m": closing, "done": bool(g.get("done_at")), "done_month": g.get("done_month"),
             "saved": None, "months_left": _idx(tm) - c, "needed": None, "pace": None, "verdict": "done" if g.get("done_at") else None,
             "short_by": None, "remaining": None, "reach_m": None}
        if r["done"]:
            out.append(r)
            continue
        if kind == "account" and fund in acct:
            s = acct[fund]
            saved = _balance_at(s, closing)
            then = _balance_at(s, _mon(c - 3))
            pace = round((saved - then) / 3, 2) if saved is not None and then is not None and _mon(c - 3) >= min(s) else None
        elif kind == "envelope" and fund:
            by = net.get(fund, {})
            saved = round(sum(v for m, v in by.items() if since <= m <= closing), 2)
            last3 = [_mon(i) for i in range(c - 2, c + 1)]
            pace = round(sum(by.get(m, 0.0) for m in last3) / 3, 2) if all(m in by for m in last3) else None
        else:
            saved, pace = None, None
        r["saved"], r["pace"] = (round(saved, 2) if saved is not None else None), pace
        if saved is None:
            r["verdict"] = "no_pace"
            out.append(r)
            continue
        remaining = round(max(0.0, target - saved), 2)
        r["remaining"] = remaining
        if remaining == 0:
            r["verdict"] = "reached"
        elif r["months_left"] <= 0:
            r["verdict"] = "past"
        else:
            need = round(remaining / r["months_left"], 2)
            r["needed"] = need
            if pace is None:
                r["verdict"] = "no_pace"
            elif pace >= need * AHEAD:
                r["verdict"] = "ahead"
            elif pace >= need:
                r["verdict"] = "on_pace"
            else:
                r["verdict"], r["short_by"] = "short", round(need - pace, 2)
        if remaining and pace and pace > 0:
            r["reach_m"] = _mon(c + math.ceil(remaining / pace))
        out.append(r)
    for r in out:
        r["says"] = words(r)
    return out


def words(r: dict) -> str:
    """The verdict as the check-in and Settings say it: "on pace", "$120 a month short", "past its date, $400 short"."""
    v = r.get("verdict")
    money = lambda x: f"${x:,.0f}"
    return {"reached": "reached", "done": "bought", "ahead": "ahead of pace", "on_pace": "on pace",
            "short": f"{money(r['short_by'] or 0)} a month short", "past": f"past its date, {money(r['remaining'] or 0)} short",
            "no_pace": "no pace yet: three months of saving tell"}.get(v, "")


def coming_up_rows(prog: list[dict]) -> list[dict]:
    """goal rows for `coming_up` (#144): an open dated goal at its target month. The label is the household's own goal label."""
    return [{"m": r["target_month"], "kind": "goal", "label": f"{r['label']} target", "public_label": f"{r['label']} target",
             "amount": r["target_amount"], "source": "planned", "ref": r["key"]}
            for r in prog if not r["done"] and r["target_month"] > r["m"]]
