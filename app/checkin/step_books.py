"""Step 2, Clean books: the month reconciles and has no unmapped item, or each problem is acknowledged (#164)."""
from . import Step, register


def _problems(conn, month):
    from pipeline.run_pipeline import reconcile, unmapped_items
    rec = reconcile(conn, month)
    un = [u for u in unmapped_items(conn) if month in (u.get("months") or [])]
    return rec, un


def _status(conn, month, state):
    rec, un = _problems(conn, month)
    bits = ([f"{len(rec['problems'])} envelope-month{'s' * (len(rec['problems']) != 1)} do not reconcile"] if not rec["clean"] else []) + \
           ([f"{len(un)} unmapped item{'s' * (len(un) != 1)}"] if un else [])
    if not bits:
        n = sum(v for k, v in (rec.get("counts") or {}).items() if k.startswith("OK"))   # as Close a month counts them
        return "done", f"all {n} envelope-months reconcile, and every item is mapped" if n else "reconciles, every item mapped"
    if "books" in (state.get("ack") or {}):
        return "done", "; ".join(bits) + " (acknowledged)"
    return "attention", "; ".join(bits)


def _context(conn, month, state):
    rec, un = _problems(conn, month)
    return {"rec": rec, "unmapped": un, "acked": "books" in (state.get("ack") or {})}


register(Step("books", 20, "Clean books", _status, context=_context, in_short=True, actions=("ack",)))
