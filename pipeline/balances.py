"""Month-end balances (issue #143): the arithmetic for a month that has no balance typed in, shared by the app (the suggestion at
Close a month) and make_datasets.py (the `balances` dataset). Standalone, like filenames.py: the standard library only, no database.

A month-end balance is a fact a person types. When a month has none:
  - an asset is *carried*: last month's balance, unchanged;
  - a debt is *estimated*: last month's balance, plus a month's interest at its APR when one is given, less the month's payments
    to its linked canonical item, less escrow (the part of a mortgage payment that pays taxes and insurance), never below zero.
The same formula pre-fills the Close a month form, where accepting it stores the value as `carried`."""
from __future__ import annotations
import csv, os

ASSET_TYPES = ("cash", "savings", "retirement", "brokerage", "home", "vehicle", "other")
DEBT_TYPES = ("mortgage", "student", "auto", "card", "medical", "personal", "other")   # mapping_store.DEBT_TYPES (#141); a test keeps them equal
EMERGENCY_TYPES = ("cash", "savings")   # an asset of these types counts toward emergency-fund coverage (#150) unless switched off
ACCOUNT_COLUMNS = ["name", "kind", "type", "person", "debt_item", "apr", "escrow", "emergency", "in_net_worth", "active"]
BALANCE_COLUMNS = ["account", "month", "amount", "source"]


def next_month(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:])
    return f"{y + 1}-01" if mo == 12 else f"{y}-{mo + 1:02d}"


def prev_month(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:])
    return f"{y - 1}-12" if mo == 1 else f"{y}-{mo - 1:02d}"


def months(first: str, last: str) -> list[str]:
    out, m = [], first
    while m <= last:
        out.append(m); m = next_month(m)
    return out


def suggest(kind: str, prev: float, payment: float = 0.0, apr: float | None = None, escrow: float | None = None) -> float:
    """The balance at the end of a month, from the one before it and the month's payments (debts only)."""
    if kind != "debt":
        return round(prev, 2)
    paid = max(0.0, (payment or 0.0) - (escrow or 0.0)) if payment else 0.0   # no payment: no escrow either
    return max(0.0, round(prev + prev * (apr or 0.0) / 12 - paid, 2))


def truthy(v, default: bool) -> bool:
    s = str(v if v is not None else "").strip().lower()
    return default if s == "" else s in ("1", "true", "yes", "y", "on")


def _num(v) -> float | None:
    s = str(v if v is not None else "").strip()
    return (float(s[:-1]) / 100 if s.endswith("%") else float(s)) if s else None   # an APR may be written 6.5% or 0.065


def read_accounts(path: str = "accounts.csv") -> list[dict]:
    """accounts.csv as dicts with typed fields; [] when the file is absent (a household that keeps no balances)."""
    if not os.path.exists(path):
        return []
    out = []
    for r in csv.DictReader(open(path, newline="", encoding="utf-8-sig")):
        kind = (r.get("kind") or "").strip()
        typ = (r.get("type") or "").strip() or None
        out.append({"name": r["name"].strip(), "kind": kind, "type": typ, "person": (r.get("person") or "").strip() or None,
                    "debt_item": (r.get("debt_item") or "").strip() or None, "apr": _num(r.get("apr")), "escrow": _num(r.get("escrow")),
                    "emergency": truthy(r.get("emergency"), kind == "asset" and typ in EMERGENCY_TYPES),
                    "in_net_worth": truthy(r.get("in_net_worth"), True), "active": truthy(r.get("active"), True)})
    return out


def read_balances(path: str = "balances.csv") -> dict[tuple[str, str], tuple[float, str]]:
    """{(account, month): (amount, source)}; {} when the file is absent."""
    if not os.path.exists(path):
        return {}
    return {(r["account"].strip(), r["month"].strip()): (float(r["amount"]), (r.get("source") or "entered").strip() or "entered")
            for r in csv.DictReader(open(path, newline="", encoding="utf-8-sig"))}


def series(accounts: list[dict], stored: dict[tuple[str, str], tuple[float, str]], payments: dict[tuple[str, str], float],
           last: str | None) -> list[dict]:
    """One row per account per budget month, from the account's first stored balance to `last` (a retired account stops at its last
    stored one). Stored rows are confirmed; gaps are estimated (a debt) or carried (an asset) by `suggest`, and are not confirmed.
    payments: {(canonical item, month): dollars paid}."""
    rows = []
    for a in accounts:
        mine = sorted(m for (n, m) in stored if n == a["name"])
        if not mine:
            continue
        end = (last if a["active"] and last and last > mine[-1] else mine[-1])
        prev = None
        for m in months(mine[0], end):
            if (a["name"], m) in stored:
                amount, source = stored[(a["name"], m)]; confirmed = True
            else:
                pay = payments.get((a["debt_item"], m), 0.0) if a["debt_item"] else 0.0
                amount = suggest(a["kind"], prev, pay, a["apr"], a["escrow"])
                source, confirmed = ("estimated" if a["kind"] == "debt" else "carried"), False
            rows.append({"m": m, "account": a["name"], "kind": a["kind"], "type": a["type"], "person": a["person"], "amount": round(amount, 2),
                         "source": source, "confirmed": confirmed, "in_net_worth": a["in_net_worth"], "emergency": a["emergency"]})
            prev = amount
    rows.sort(key=lambda r: (r["m"], r["kind"], r["account"]))
    return rows


def totals(rows: list[dict]) -> list[dict]:
    """balance_totals: per month, kind and type, over accounts in net worth; no account names (review and MCP read this one)."""
    acc: dict[tuple, dict] = {}
    for r in rows:
        if not r["in_net_worth"]:
            continue
        t = acc.setdefault((r["m"], r["kind"], r["type"] or ""), {"m": r["m"], "kind": r["kind"], "type": r["type"] or "", "amount": 0.0, "accounts": 0, "estimated": 0})
        t["amount"] = round(t["amount"] + r["amount"], 2); t["accounts"] += 1; t["estimated"] += r["source"] == "estimated"
    return [acc[k] for k in sorted(acc)]


def net_worth(rows: list[dict], did: dict[str, float], cpi: dict[str, float] | None = None, cpi_base: float | None = None) -> list[dict]:
    """net_worth (#146): per budget month, assets − debts over the accounts in net worth, and the month's change split in two:
      you_did   what the household did: `did[m]`, the month's income less living spend (net plus debt payments; plus pre-tax saving
                where #152 states it). None for a month the exports do not cover.
      happened  the rest: asset values moving, interest charged, money in or out of accounts not tracked here.
    An account's first month (`added`) and the month after its last (`closed`, a retired account) are left out of the split and given
    on their own, so a new account is not read as progress. change == you_did + happened + added − closed, to the cent.
    *_ex_home: the same without `home` assets (a typed estimate that moves the total more than a month's decisions)."""
    by: dict[str, dict[str, dict]] = {}
    for r in rows:
        if r["in_net_worth"]:
            by.setdefault(r["m"], {})[r["account"]] = r
    signed = lambda r: r["amount"] if r["kind"] == "asset" else -r["amount"]
    out, prev = [], None
    for m in sorted(by):
        cur = by[m]
        nw = round(sum(signed(r) for r in cur.values()), 2)
        home = round(sum(r["amount"] for r in cur.values() if r["kind"] == "asset" and r["type"] == "home"), 2)
        row = {"m": m, "assets": round(sum(r["amount"] for r in cur.values() if r["kind"] == "asset"), 2),
               "debts": round(sum(r["amount"] for r in cur.values() if r["kind"] == "debt"), 2), "net_worth": nw, "home": home,
               "net_worth_ex_home": round(nw - home, 2), "accounts": len(cur), "estimated": sum(r["source"] == "estimated" for r in cur.values()),
               "confirmed": all(r["confirmed"] for r in cur.values()),
               "net_worth_real": round(nw * cpi_base / cpi[m], 2) if cpi and cpi_base and cpi.get(m) else None,
               "change": None, "added": None, "added_accounts": 0, "closed": None, "you_did": None, "happened": None, "home_change": None}
        if prev is not None and prev["m"] == prev_month(m):
            p = by[prev["m"]]
            both = [a for a in cur if a in p]
            added = round(sum(signed(cur[a]) for a in cur if a not in p), 2)
            closed = round(sum(signed(p[a]) for a in p if a not in cur), 2)
            moved = round(sum(signed(cur[a]) - signed(p[a]) for a in both), 2)
            row.update(change=round(nw - prev["net_worth"], 2), added=added, added_accounts=sum(a not in p for a in cur), closed=closed,
                       home_change=round(sum(cur[a]["amount"] - p[a]["amount"] for a in both if cur[a]["kind"] == "asset" and cur[a]["type"] == "home"), 2))
            if did.get(m) is not None:
                row["you_did"] = round(did[m], 2); row["happened"] = round(moved - row["you_did"], 2)
        out.append(row); prev = row
    for i, r in enumerate(out):   # the twelve months to this one, when the series reaches back that far without a gap
        base = out[i - 12] if i >= 12 else None
        r["change_12"] = round(r["net_worth"] - base["net_worth"], 2) if base and base["m"] == bal_months_back(r["m"], 12) else None
        span = out[i - 11:i + 1] if r["change_12"] is not None else []
        r["you_did_12"] = round(sum(x["you_did"] for x in span), 2) if span and all(x["you_did"] is not None for x in span) else None
        r["happened_12"] = round(sum(x["happened"] for x in span), 2) if r["you_did_12"] is not None else None
        r["added_12"] = round(sum((x["added"] or 0) - (x["closed"] or 0) for x in span), 2) if span else None
    return out


def bal_months_back(m: str, n: int) -> str:
    for _ in range(n):
        m = prev_month(m)
    return m
