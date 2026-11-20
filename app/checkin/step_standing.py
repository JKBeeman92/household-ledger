"""Step 6, Where we stand: a read-only summary of the month (the Last month tab's figures); reading it marks it done."""
from . import Step, register, _x


def _key(name) -> str:
    """A person's name as the rows are matched on: case and spacing ignored, so "rosie " is Rosie."""
    return " ".join(str(name or "").split()).casefold()


def by_person(ds: dict, month: str) -> list[dict]:
    """#156: each person's month (by_person, which is empty with fewer than two people), with their accounts and the decisions in force
    that name them; the shared part last as "Joint", with the accounts and decisions that name no one or a name the People rule does not
    have (a removed person, say), so none drops out. Names match ignoring case and spacing. Figures only, no verdict."""
    rows = [r for r in ds.get("by_person", []) if r["m"] == month]
    if not rows:
        return []
    named = {_key(r["who"]) for r in rows if r["who"] != "Household"}
    owner = lambda name: _key(name) if _key(name) in named else None   # None: under Joint
    accts = [r for r in ds.get("balances", []) if r["m"] == month]
    decs = [r for r in ds.get("decisions", []) if r["m"] == month and _key(r.get("person"))]
    out = []
    for r in rows:
        joint = r["who"] == "Household"
        me = None if joint else _key(r["who"])
        out.append({**r, "label": "Joint" if joint else r["who"], "joint": joint,
                    "accounts": sorted(a["account"] for a in accts if owner(a.get("person")) == me),
                    "decisions": [d["text"] for d in decs if owner(d["person"]) == me]})
    return out


def _status(conn, month, state):
    return ("done", "read") if "standing" in (state.get("viewed") or {}) else ("todo", "open it to read the month's figures")


def _context(conn, month, state):
    ds = {r["name"]: r["rows"] for r in _x(conn, "SELECT name, rows FROM datasets WHERE name IN "
                                                 "('monthly','net_worth','debt_to_income','emergency_fund','by_person','balances','decisions')").fetchall()}
    pick = lambda name: next((r for r in ds.get(name, []) if r["m"] == month), None)
    t = _x(conn, "SELECT rows FROM datasets WHERE name='tax'").fetchone()   # #153: every check-in shows the year's set-aside
    tx = next((r for r in (t["rows"] if t else []) if r["m"] == month), None)
    from app import purchases   # #158: each open planned purchase's progress, as of the month the last build closed on
    pp = [p for p in purchases.for_page(conn) if not p["done"] and p["progress"] and p["progress"]["m"] == month]
    return {"people": by_person(ds, month), "monthly": pick("monthly"), "nw": pick("net_worth"), "dti": pick("debt_to_income"), "ef": pick("emergency_fund"), "tax": tx, "purchases": pp}


register(Step("standing", 60, "Where we stand", _status, context=_context, actions=("view",)))
