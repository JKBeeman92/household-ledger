"""Which files are monthly exports, and which budget month each is (issue #161). Standalone: no database, no app imports.

The pipeline's own format, the **internal format**, is a pair of files per budget month:

    YYYY-MM-transactions.csv   Group, Item, Type, Date (YYYY-MM-DD), Merchant, Account, Amount (income +, spending -), Note
    YYYY-MM-budget.csv         Group, Item, Planned, Spent, Remaining

EveryDollar's own export names (<hex>-MM-YYYY-EveryDollar-Transactions.csv / -BudgetItems.csv, dates MM/DD/YYYY) are read too, and
always will be: EveryDollar is the first built-in source profile, and its files need no mapping. The budget month comes from the
file name, never from the dates inside.

The scripts beside this file import it; when they are run by hand (`python -I build_ledger.py` in a folder of CSVs), keep this file
next to them."""
from __future__ import annotations
import os, re

NEUTRAL = re.compile(r"^(?P<yyyy>\d{4})-(?P<mm>0[1-9]|1[0-2])-(?P<kind>transactions|budget)\.csv$")
EVERYDOLLAR = re.compile(r"^(?P<hex>[0-9a-f]+)-(?P<mm>\d\d)-(?P<yyyy>\d{4})-EveryDollar-(?P<kind>Transactions|BudgetItems)\.csv$")


def parse(name: str) -> tuple[str, str] | None:
    """(kind, budget month) for an export's file name: kind is "transactions" or "budget_items", the month YYYY-MM. None otherwise."""
    name = os.path.basename(name)
    m = NEUTRAL.match(name)
    if m:
        return ("transactions" if m["kind"] == "transactions" else "budget_items"), f"{m['yyyy']}-{m['mm']}"
    m = EVERYDOLLAR.match(name)
    if m:
        return ("transactions" if m["kind"] == "Transactions" else "budget_items"), f"{m['yyyy']}-{m['mm']}"
    return None


def is_export(name: str) -> bool:
    return parse(name) is not None


def budget_month(name: str) -> str | None:
    p = parse(name)
    return p[1] if p else None


def neutral_name(kind: str, month: str) -> str:
    """The internal-format file name for a kind ("transactions" or "budget_items") and a budget month (YYYY-MM)."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        raise ValueError(f"the budget month must be YYYY-MM, not {month!r}")
    return f"{month}-{'transactions' if kind == 'transactions' else 'budget'}.csv"


def exports(folder: str, kind: str) -> list[tuple[str, str]]:
    """[(budget month, file name)] of one kind in a folder, in budget-month order (then by name), whatever the naming scheme."""
    if not os.path.isdir(folder):
        return []
    found = [(p[1], fn) for fn in os.listdir(folder) if (p := parse(fn)) and p[0] == kind]
    return sorted(found)


def iso_date(value: str) -> tuple[str, str, str]:
    """(yyyy, mm, dd) from an export's date: YYYY-MM-DD (internal format) or MM/DD/YYYY (EveryDollar)."""
    v = (value or "").strip()
    if "-" in v:
        yy, mm, dd = v.split("-")
    else:
        mm, dd, yy = v.split("/")
    return yy, mm.zfill(2), dd.zfill(2)
