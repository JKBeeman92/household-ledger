"""Step 7, Coming up (#144): what falls in the next three budget months, from the `coming_up` dataset: estimated payoffs and life
events already dated (yearly bills, #151, and tax deadlines, #153, join it). Read-only; opening it marks it read."""
from . import Step, register, _x

AHEAD = 3


def _until(month: str, k: int = AHEAD) -> str:
    i = int(month[:4]) * 12 + int(month[5:]) - 1 + k
    return f"{i // 12}-{i % 12 + 1:02d}"


def items(conn, month: str) -> list[dict]:
    r = _x(conn, "SELECT rows FROM datasets WHERE name='coming_up'").fetchone()
    return [x for x in (r["rows"] if r else []) if month < x["m"] <= _until(month)]


def _status(conn, month, state):
    if "coming_up" in (state.get("viewed") or {}):
        return "done", "read"
    n = len(items(conn, month))
    return "todo", (f"{n} item{'s' if n != 1 else ''} in the next {AHEAD} months: open it to read them" if n else f"nothing dated in the next {AHEAD} months: open it to confirm")


def _context(conn, month, state):
    r = _x(conn, "SELECT rows FROM datasets WHERE name='yearly_bills'").fetchone()   # set aside for each bill due (#151)
    return {"rows": items(conn, month), "ahead": AHEAD, "until": _until(month), "bills": {b["item"]: b for b in (r["rows"] if r else [])}}


register(Step("coming_up", 70, "Coming up", _status, context=_context, actions=("view",), issue="#144"))
