"""Estimated tax at the check-in (issue #153, part 1). Standalone, like balances.py and payoff.py: the standard library only, no
database. make_budget_items.py calls `summary` with the built budget rows and emits the `tax` dataset, and `deadline_rows` adds
`tax_deadline` rows to `coming_up`.

Contract or self-employment income has no tax withheld, so a share of it is set aside for the quarterly estimated payments:

    contract income   the year-to-date actual of the canonical items in the `contract_income_items` rule (tax year: calendar)
    expected          contract income × the goal `tax_set_aside_pct` (confirmed at each month-close, as tax law and income change)
    set aside         Σ planned this year of the envelopes in `tax_set_aside_items` (what was budgeted into them, whatever has
                      since been paid out), or an account's latest balance plus this year's payments recorded at the check-in
    this quarter      the contract income of the three budget months to the closing month × the same share
    next deadline     the first `tax_deadlines` entry after the closing month: dates the household keeps current, because they move
                      with weekends and holidays and differ by state; the app never hard-codes them

With no `contract_income_items` there is no summary, and nothing is shown anywhere."""
from __future__ import annotations


def _add(m: str, k: int) -> str:
    i = int(m[:4]) * 12 + int(m[5:]) - 1 + k
    return f"{i // 12}-{i % 12 + 1:02d}"


def summary(rows: list[dict], items: list[str], aside_items: list[str], pct: float | None, deadlines: list[dict], payments: list[dict],
            closing: str | None, balances: dict[str, float] | None = None) -> dict | None:
    if not items or not closing:
        return None
    year = closing[:4]
    income = {r["m"]: 0.0 for r in rows}
    for r in rows:
        if r.get("canonical") in items and r.get("flow") == "income":
            income[r["m"]] += float(r.get("actual") or 0)
    ytd = round(sum(v for m, v in income.items() if m[:4] == year and m <= closing), 2)
    quarter = [_add(closing, -k) for k in range(3)]
    q_income = round(sum(income.get(m, 0.0) for m in quarter), 2)
    paid = round(sum(float(p.get("amount") or 0) for p in payments if str(p.get("month", ""))[:4] == year), 2)
    aside, kinds = 0.0, []
    names = {r.get("canonical") for r in rows}
    for it in aside_items or []:
        if it in names:
            aside += sum(float(r.get("planned") or 0) for r in rows if r.get("canonical") == it and r["m"][:4] == year and r["m"] <= closing); kinds.append("envelope")
        elif balances is not None and it in balances:
            aside += float(balances[it]) + paid; kinds.append("account")
    expected = round(ytd * pct, 2) if pct is not None else None
    later = sorted((d for d in deadlines or [] if str(d.get("m", "")) > closing), key=lambda d: d["m"])
    nxt = later[0] if later else None
    return {"m": closing, "year": year, "contract_income": ytd, "pct": pct, "expected": expected,
            "set_aside": round(aside, 2) if aside_items else None, "set_aside_kind": "account" if "account" in kinds else ("envelope" if kinds else None),
            "short": round(max(0.0, expected - aside), 2) if expected is not None and aside_items else None,
            "quarter_months": quarter[::-1], "quarter_income": q_income, "quarter_expected": round(q_income * pct, 2) if pct is not None else None,
            "paid_this_year": paid, "next_deadline": nxt["m"] if nxt else None, "next_label": (nxt or {}).get("label"),
            "needs_dates": not later}


def deadline_rows(deadlines: list[dict], closing: str | None, quarter_expected: float | None = None) -> list[dict]:
    """tax_deadline rows for coming_up (#144): every deadline after the closing month; the first carries the quarter's expected amount."""
    if not closing:
        return []
    later = sorted((d for d in deadlines or [] if str(d.get("m", "")) > closing), key=lambda d: d["m"])
    return [{"m": d["m"], "kind": "tax_deadline", "label": d.get("label") or "Estimated tax", "public_label": d.get("label") or "Estimated tax",
             "amount": quarter_expected if i == 0 else None, "source": "planned", "ref": d.get("label") or ""} for i, d in enumerate(later)]
