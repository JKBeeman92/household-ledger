"""Estimated payoff per debt (issue #144): at last month's payment, when is each debt gone? Standalone, like balances.py: the standard
library only, no database. make_datasets.py calls `estimate` for each debt account and emits the `payoff` and `coming_up` datasets.

    P  the payment: last month's total paid to the account's linked canonical item, less escrow (no payment: no escrow either)
    r  APR / 12
    n  months left = -ln(1 - r·B/P) / ln(1 + r), or B/P with no APR (then the date is "at least": it ignores interest)

The payoff month is the closing month plus ceil(n - TOLERANCE). Payments are typed to the cent, so a loan whose textbook payment
rounds to the cent ($200,000 at 6% for 30 years, $1,199.10) comes out a hair over 360 months (360.0009); a final month of under 1%
of a payment is not counted as a month. A genuine part month still counts.

What an estimate can say, in order (the first that applies decides; the qualifiers after it add notes, never hide each other):
    no linked item              no estimate: "link the debt's budget item to see a payoff date"
    balance is 0                paid off
    no payment last month       the 3-month average, if there is one ("no payment in <month>: from the 3-month average"),
                                else no date ("no payment in <month>")
    payment <= a month's interest   no date: "at this payment the balance does not go down"
    more than 100 years         no date: "more than 100 years at this payment"
  then the date, with:
    no APR                      "at least": the date ignores interest
    an estimated balance        "from an estimated balance" (a month with no balance typed in; an accepted suggestion is not one)
    a card                      "at this payment, with no new charges"
    a mortgage with no escrow   "set the escrow part of the payment for an honest date"
"""
from __future__ import annotations
import math

TOLERANCE = 0.01      # of a month: see above
MAX_MONTHS = 1200     # 100 years; beyond it a date says nothing
AVG_MONTHS = 3        # the window for "the 3-month average" when last month had no payment



def _add(m: str, k: int) -> str:
    i = int(m[:4]) * 12 + int(m[5:]) - 1 + k
    return f"{i // 12}-{i % 12 + 1:02d}"


def _index(m: str) -> int:
    return int(m[:4]) * 12 + int(m[5:]) - 1


def months_left(balance: float, payment: float, apr: float | None) -> float | None:
    """Months to clear `balance` at `payment` a month; None when the payment never gets there (it does not cover the interest)."""
    if balance <= 0:
        return 0.0
    if payment <= 0:
        return None
    if not apr:
        return balance / payment
    r = apr / 12
    if payment <= r * balance:
        return None
    return -math.log(1 - r * balance / payment) / math.log(1 + r)


def whole_months(n: float) -> int:
    return max(1, math.ceil(n - TOLERANCE))


def _pay(paid: dict, item: str | None, m: str, escrow: float | None) -> float:
    p = paid.get((item, m), 0.0) if item else 0.0
    return max(0.0, p - (escrow or 0.0)) if p > 0 else 0.0


def estimate(account: dict, balances: dict[str, dict], paid: dict, closing: str, loaded: set[str] | None = None) -> dict | None:
    """The payoff line for one debt account as of the closing month, or None for an account with no balance at `closing`.

    account: as balances.read_accounts gives it, with `type` resolved (the linked item's debt type when the account has none).
    balances: {month: the account's `balances` dataset row}. paid: {(canonical item, month): dollars paid}. loaded: the budget
    months loaded, for the 3-month average (months not loaded are left out of it; a loaded month with no payment counts as 0)."""
    row = balances.get(closing)
    if account.get("kind") != "debt" or not row:
        return None
    B, apr, escrow, item, typ = float(row["amount"]), account.get("apr"), account.get("escrow"), account.get("debt_item"), account.get("type")
    prev = balances.get(_add(closing, -1))
    out = {"m": closing, "account": account["name"], "type": typ or None, "person": account.get("person") or None, "balance": round(B, 2),
           "change": round(B - float(prev["amount"]), 2) if prev else None, "balance_source": row.get("source"), "payment": None, "basis": None,
           "months": None, "payoff": None, "at_least": False, "status": None, "notes": [], "moved": None, "paid_more": None}

    def note(code: str, text: str, m: str | None = None):
        # text may hold {m}: the page puts the month in, formatted for the household's locale (DS-WORD-6)
        out["notes"].append({"code": code, "text": text, "m": m})

    if not item:
        out["status"] = "no_link"; note("no_link", "Link the debt's budget item to see a payoff date.")
        return out
    if B <= 0:
        out.update(status="paid_off", months=0); return out
    P, basis = _pay(paid, item, closing, escrow), "last_month"
    if P <= 0:
        window = [m for m in (_add(closing, -k) for k in range(AVG_MONTHS)) if loaded is None or m in loaded]
        avg = sum(_pay(paid, item, m, escrow) for m in window) / len(window) if window else 0.0
        if avg > 0:
            P, basis = round(avg, 2), "avg3"
            note("avg3", f"No payment in {{m}}: from the {AVG_MONTHS}-month average.", closing)
        else:
            out["status"] = "no_payment"; note("no_payment", "No payment in {m}.", closing)
            return out
    out.update(payment=round(P, 2), basis=basis)
    n = months_left(B, P, apr)
    if n is None:
        out["status"] = "not_down"; note("not_down", "At this payment the balance does not go down: the interest is as much as the payment.")
        return out
    k = whole_months(n)
    if k > MAX_MONTHS:
        out["status"] = "too_long"; note("too_long", "More than 100 years at this payment.")
        return out
    out.update(status="date", months=k, payoff=_add(closing, k), at_least=apr is None)
    if apr is None:
        note("no_apr", "At least: with no APR entered, the date ignores interest.")
    if row.get("source") == "estimated":
        note("estimated", "From an estimated balance: no balance was entered for the month.")
    if typ == "card":
        note("card", "At this payment, with no new charges.")
    if typ == "mortgage" and escrow is None:
        note("escrow", "Set the escrow part of the payment for an honest date.")
    return out


def with_movement(now: dict | None, before: dict | None) -> dict | None:
    """Add how the payoff month moved since last month's estimate: `moved` in months (negative: sooner), and `paid_more`."""
    if not now or now.get("status") != "date" or not before or before.get("status") != "date":
        return now
    now["moved"] = _index(now["payoff"]) - _index(before["payoff"])
    now["paid_more"] = (now["payment"] or 0) > (before["payment"] or 0)
    return now


def moved_text(r: dict) -> str | None:
    """'3 months sooner: you paid extra', 'the same month as last month's estimate', '2 months later'."""
    d = r.get("moved")
    if d is None:
        return None
    if d == 0:
        return "the same month as last month's estimate"
    unit = "month" if abs(d) == 1 else "months"
    if d < 0:
        return f"{-d} {unit} sooner" + (": you paid extra" if r.get("paid_more") else "")
    return f"{d} {unit} later"


def estimates(accounts: list[dict], balance_rows: list[dict], paid: dict, closing: str | None, loaded: set[str]) -> list[dict]:
    """The `payoff` dataset: one row per active debt account with a balance at the closing month, with last month's movement."""
    if not closing:
        return []
    by = {}
    for r in balance_rows:
        by.setdefault(r["account"], {})[r["m"]] = r
    out = []
    for a in accounts:
        if a.get("kind") != "debt" or not a.get("active", True):
            continue
        mine = by.get(a["name"], {})
        now = estimate(a, mine, paid, closing, loaded)
        if now is None:
            continue
        before = estimate(a, mine, paid, _add(closing, -1), loaded)
        r = with_movement(now, before)
        r["moved_text"] = moved_text(r)   # the page and the check-in say it in these words
        out.append(r)
    return out


TYPE_WORDS = {"mortgage": "Mortgage", "student": "Student loan", "auto": "Car loan", "card": "Card", "medical": "Medical debt", "personal": "Personal loan"}


def coming_up(payoffs: list[dict], events: list[dict], closing: str | None) -> list[dict]:
    """The `coming_up` dataset (#144): one row per future item. kind: payoff (here), event (life events dated after the last loaded
    month), and later yearly_bill (#151), tax_deadline (#153), goal (#158). Regenerated at every build; never written to life_events.

    label names the account; public_label names only its type, for the review and MCP, where account names are not sent."""
    rows = []
    for p in payoffs:
        if p.get("status") != "date":
            continue
        what = TYPE_WORDS.get(p.get("type") or "", "A debt")
        suffix = " (est., at least)" if p.get("at_least") else " (est.)"
        rows.append({"m": p["payoff"], "kind": "payoff", "label": f"{p['account']} paid off{suffix}", "public_label": f"{what} paid off{suffix}",
                     "amount": None, "source": "estimated", "ref": p["account"]})
    for e in events or []:
        if closing and str(e.get("m") or "") > closing:
            rows.append({"m": e["m"], "kind": "event", "label": e.get("label") or "", "public_label": e.get("label") or "",
                         "amount": None, "source": "planned", "ref": e.get("label") or ""})
    rows.sort(key=lambda r: (r["m"], r["kind"], r["label"]))
    return rows
