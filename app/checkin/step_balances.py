"""Step 3, Balances (#143): every active account's month-end balance confirmed; a carried value accepted counts. Applies once the
household has accounts."""
from . import Step, register, _x


def _applies(conn, month, mode, short):
    return _x(conn, "SELECT 1 FROM accounts LIMIT 1").fetchone() is not None


def _status(conn, month, state):
    from pipeline import accounts_store
    from . import short_form
    s = accounts_store.month_status(conn, month, strict=not short_form(conn, month))
    opt = f"; {', '.join(s['optional'])} had no balance yet (optional)" if s["optional"] else ""
    if not s["accounts"]:
        return "done", "no account had a balance yet" + opt if s["optional"] else "no active accounts"
    if s["to_do"]:
        return "todo", f"{s['confirmed']} of {s['accounts']} confirmed; still: {', '.join(s['to_do'])}" + opt
    return "done", f"all {s['accounts']} confirmed" + opt


def _context(conn, month, state):
    from app.routes.balances import form_context
    return {"bal": form_context(conn, month)}


register(Step("balances", 30, "Balances", _status, applies=_applies, context=_context, in_short=True, issue="#143"))
