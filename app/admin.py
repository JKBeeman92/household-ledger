"""Headless administration (issue #108): `python -m app.admin <command> …`, run where DATABASE_URL is set, e.g.
`docker compose exec ledger python -m app.admin import-mapping /data/seed/item_mapping.csv`.

Each command calls the function the Settings page calls, in one transaction, so validation and error messages are the same. Shell access
to the container already means full control (it holds DATABASE_URL), so this adds no remote surface; API tokens stay read-only.

Exit codes: 0 done; 1 refused (the validation message is printed) or a rebuild that failed or does not reconcile; 2 usage; 75 a rebuild is
already running (EX_TEMPFAIL: try again). Every write is logged as one `ledger.admin` line naming the command and the OS user; the
reference tables have no "changed by" column, so the log is the record."""
from __future__ import annotations
import argparse, getpass, logging, os, sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

EX_TEMPFAIL = 75
log = logging.getLogger("ledger.admin")
SETTERS = {"import-mapping": "mapping", "set-rules": "rules", "set-events": "events", "set-goals": "goals", "set-cpi": "cpi", "set-household": "household",
           "set-accounts": "accounts", "set-balances": "balances"}   # set-balances adds or corrects month-ends (account,YYYY-MM,amount); it never deletes
EXPORTABLE = ("mapping", "rules", "events", "goals", "cpi", "household", "accounts", "balances", "decisions", "yearly_bills", "tax_payments", "yearly_checklist")


def _read(path: str) -> str:
    return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8-sig")


def _who() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001  (no passwd entry for the container's uid)
        return f"uid {os.getuid()}"


def _connect():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set: run this where the app runs, e.g. docker compose exec ledger python -m app.admin …")
    return psycopg.connect(url, row_factory=dict_row)


def _write(cmd: str, fn, *args) -> int:
    from app import db
    from app.routes.settings import PIPELINE_INPUTS
    key = SETTERS.get(cmd, "mapping")
    with _connect() as conn:
        try:
            what = fn(conn, *args)
            if key in PIPELINE_INPUTS:
                db.mark_inputs_changed(conn)
            conn.commit()
        except Exception as e:  # noqa: BLE001  (ValueError from validation, JSONDecodeError, a constraint): say it, change nothing
            conn.rollback()
            print(f"Not saved — {e}", file=sys.stderr)
            return 1
    log.info("%s by %s (cli): saved %s", cmd, _who(), what)
    print(f"Saved {what}." + (" Run `python -m app.admin rebuild` (or press Rebuild now) to update the dashboard." if key in PIPELINE_INPUTS else ""))
    return 0


PREVIEW_0133 = Path(__file__).resolve().parents[1] / "db" / "preview" / "0133_mapping_preview.sql"


def preview_mapping_migration() -> int:
    """What 0133 would do to this database's mapping, read-only. Exit 0 either way; it says when the migration has already run."""
    with _connect() as conn:
        cols = {r["column_name"] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='item_mapping'").fetchall()}
        if "category" not in cols:
            print("Migration 0133 has already run on this database: nothing to preview. Its notes are on Settings → Item mapping.")
            return 0
        rows = conn.execute(PREVIEW_0133.read_text()).fetchall()
        conn.rollback()
    if not rows:
        print("Migration 0133 keeps the item mapping as it is: no canonical item has two categories, and no row is UNMAPPED.")
    for r in rows:
        print(f"{r['change']}: {r['name']}: {r['detail']}")
    return 0


def checkin_status(month: str | None) -> int:
    """`checkin status [YYYY-MM]`: each step with its status; exit 0 when done, 1 while steps are still to do (for a script)."""
    from app import checkin
    with _connect() as conn:
        month = month or checkin.latest(conn)
        if not month:
            print("No month is loaded yet: the first check-in starts with its upload.", file=sys.stderr); return 1
        try:
            ov = checkin.overview(conn, month)
        except ValueError as e:
            print(e, file=sys.stderr); return 2
        conn.rollback()
    head = f"{month} check-in ({ov['mode']}{', catching up' if ov['short'] else ''}): "
    r = ov["row"]
    print(head + (f"done {r['completed_at']:%Y-%m-%d}" + (f" by {r['completed_by']}" if r["completed_by"] else "") if ov["done"] else f"{ov['to_do']} step(s) to do"))
    for s in ov["steps"]:
        print(f"  {s['n']}. {s['title']:<18} {s['label']:<16} {s['summary']}")
    if ov["missing"]:
        print("Earlier months without a check-in: " + ", ".join(ov["missing"]))
    return 0 if ov["done"] else 1


def rebuild_cmd() -> int:
    from pipeline.run_pipeline import rebuild, BuildBusy
    with _connect() as conn:
        try:
            res = rebuild(conn)
        except BuildBusy as e:
            print(f"Not started — {e}; try again when it finishes.", file=sys.stderr)
            return EX_TEMPFAIL
        except Exception as e:  # noqa: BLE001  (an unmapped item, a script error): the build log has the detail too
            print(f"Rebuild failed — {e}", file=sys.stderr)
            return 1
    log.info("rebuild by %s (cli): %s", _who(), res["summary"])
    print(res["summary"])
    for p in res["reconciliation"].get("problems", []):
        print("PROBLEM", p)
    return 0 if res["reconciliation"]["clean"] else 1


def decisions_cmd(a) -> int:
    """`python -m app.admin decisions ...`: what the check-in's two decision steps do, from the command line (#155)."""
    from app import checkin, decisions
    with _connect() as conn:
        try:
            if a.dcmd == "list":
                month = a.month or checkin.latest(conn)
                for d in decisions.in_force(conn, month):
                    v = (d["check"] or {}).get("verdict") or "-"
                    print(f"{d['id']}\tchecked {month}\t{d['kind']}\tapp: {d['auto_verdict']}\tyou: {v}\t{d['text']}")
                for d in decisions.made_in(conn, month):
                    print(f"{d['id']}\tmade {month}\t{d['kind']}\tuntil {d['until'] or decisions.rules.add_months(month, 1)}\t{d['text']}")
                return 0
            if a.dcmd == "add":
                t = {k: v for k, v in (("category", a.category), ("item", a.item), ("debt", a.debt)) if v}
                key = {"spend_cap": "max", "save": "min", "extra_payment": "extra", "plan_change": "planned"}.get(a.kind)
                if key and a.amount is not None:
                    t[key] = a.amount
                if a.until_payoff:
                    t["until_payoff"] = True
                i, n = decisions.add(conn, a.month, a.text, a.kind, t or None, a.until, a.person, a.note, f"{_who()} (cli)")
                print(f"Added decision {i} at the {a.month} check-in" + (f" ({n} that month: three is usually enough)" if n > decisions.rules.SOFT_CAP else "") + ".")
            elif a.dcmd == "check":
                decisions.confirm(conn, a.id, a.month, a.verdict, f"{_who()} (cli)"); print(f"Decision {a.id}: {a.verdict} in {a.month}.")
            elif a.dcmd == "carry":
                print(f"Decision {a.id} carries on to {decisions.carry_on(conn, a.id, a.month)}.")
            elif a.dcmd == "remove":
                decisions.delete(conn, a.id); print(f"Removed decision {a.id}.")
            conn.commit()
            return 0
        except ValueError as e:
            conn.rollback(); print(f"decisions {a.dcmd}: {e}", file=sys.stderr); return 2


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


def purchases_cmd(a) -> int:
    """`python -m app.admin purchases [list|add|change|bought|reopen|delete]` (#158): what Settings → Goals → Planned purchases
    and the check-in's "bought it" form can do. list reads the last build and writes nothing."""
    from app import purchases
    with _connect() as conn:
        try:
            if (a.pcmd or "list") == "list":
                conn.execute("SET TRANSACTION READ ONLY")
                rows = purchases.for_page(conn)
                for p in rows:
                    g = p["progress"] or {}
                    state = "bought" + (f" in {p['done_month']}" if p["done_month"] else "") if p["done"] else (g.get("says") or "rebuild to see the progress")
                    saved = f", {g['saved']:,.2f} saved" if g.get("saved") is not None else ""
                    print(f"{p['key']}\t{p['label']}: {p['target_amount']:,.2f} by {p['target_month']}, kept in {p['funded_by']} ({p['funded_kind']}){saved}: {state}")
                if not rows:
                    print("No planned purchases.", file=sys.stderr)
                return 0
            if a.pcmd in ("add", "change"):
                key = purchases.save(conn, a.label, a.amount, a.by, a.fund, a.since, a.key if a.pcmd == "change" else "")
                conn.commit(); print(f"{key}: saved. Rebuild to see its progress."); return 0
            if a.pcmd == "bought":
                loan = {"item": a.loan_item, "category": a.loan_category or purchases.debt_category(conn), "debt_type": a.loan_type, "account": a.loan_account or a.loan_item,
                        "balance": a.borrowed, "apr": a.apr} if a.loan_item else None
                done = purchases.mark_bought(conn, a.key, a.month, a.event, loan, f"{_who()} (cli)")
                conn.commit(); print(f"{a.key}: bought in {a.month}" + (f"; life event {done['event']}" if done["event"] else "") +
                                     (f"; debt {done['debt']} with account {done['account']}" if done["debt"] else "") + ". Rebuild to apply it."); return 0
            n = (purchases.reopen if a.pcmd == "reopen" else purchases.delete)(conn, a.key)
            conn.commit(); print(f"{a.key}: {'open again' if a.pcmd == 'reopen' else 'deleted'}." if n else f"{a.key}: no such planned purchase.")
            return 0 if n else 1
        except purchases.PurchaseError as e:
            conn.rollback(); print(f"purchases {a.pcmd}: {e}", file=sys.stderr); return 2


def yearly_checklist_cmd(a) -> int:
    """`python -m app.admin yearly-checklist [list|add|rename|remove|tick|untick]` (#157): what Settings → Yearly checklist and the
    check-in's year mode do. list (the default) shows the list and, with --year, that budget year's ticks; it writes nothing."""
    from app import yearly_checklist as yc
    with _connect() as conn:
        try:
            cmd = a.ccmd or "list"
            if cmd == "list":
                conn.execute("SET TRANSACTION READ ONLY")
                have = yc.ticks(conn, a.year) if getattr(a, "year", None) else {}
                for r in yc.items(conn):
                    t = have.get(r["key"])
                    print(f"{r['key']}\t{r['label']}" + (f"\tdone {t['done_at']:%Y-%m-%d}" + (f": {t['note']}" if t["note"] else "") if t else ""))
                return 0
            if cmd == "add":
                key = yc.add(conn, a.label); conn.commit(); print(f"{key}: added."); return 0
            if cmd == "rename":
                yc.rename(conn, a.key, a.label); conn.commit(); print(f"{a.key}: saved."); return 0
            if cmd == "remove":
                n = yc.remove(conn, a.key); conn.commit(); print(f"{a.key}: removed." if n else f"{a.key}: not on the list."); return 0 if n else 1
            if cmd == "tick":
                yc.tick(conn, a.year, a.key, a.note or "", f"{_who()} (cli)"); conn.commit(); print(f"{a.key}: ticked for {a.year}."); return 0
            n = yc.untick(conn, a.year, a.key); conn.commit(); print(f"{a.key}: unticked for {a.year}." if n else f"{a.key}: was not ticked for {a.year}."); return 0
        except yc.ChecklistError as e:
            conn.rollback(); print(f"yearly-checklist {a.ccmd}: {e}", file=sys.stderr); return 2


def yearly_bills_cmd(a) -> int:
    """`python -m app.admin yearly-bills [report|confirm|add|dismiss|restore]` (#151). The report reads the last build in a
    read-only transaction, so it is safe against a live install: it is the check a household runs on its own back catalogue."""
    from app import yearly_bills
    with _connect() as conn:
        if (a.ycmd or "report") == "report":
            conn.execute("SET TRANSACTION READ ONLY")   # nothing below writes; on an install not yet upgraded it changes nothing either
            has_table = conn.execute("SELECT to_regclass('public.yearly_bills') AS t").fetchone()["t"] is not None
            built = conn.execute("SELECT 1 FROM datasets WHERE name='yearly_bills'").fetchone()
            if built and has_table:
                rows, how = yearly_bills.for_page(conn), "from the last build"
            else:   # not built with this version yet (e.g. a 0.6 install checked from this branch): work it out from the last build's budget rows
                from pipeline import yearly_bills as detect
                ds = {r["name"]: r["rows"] for r in conn.execute("SELECT name, rows FROM datasets WHERE name IN ('budget_items', 'balances')").fetchall()}
                budget = ds.get("budget_items") or []
                if not budget:
                    conn.rollback(); print("No build to read: the install has no budget_items dataset yet.", file=sys.stderr); return 1
                stored = [dict(r) for r in conn.execute("SELECT item, month, amount, every_years, funded_by, source FROM yearly_bills").fetchall()] if has_table else []
                last = {b["account"]: b["amount"] for b in ds.get("balances") or []}
                months_loaded = {r["m"] for r in budget}
                rows, how = detect.bills(budget, stored, max(months_loaded), months_loaded, sorted({r["canonical"] for r in budget}), last), "worked out now from the last build's budget rows; nothing saved"
            conn.rollback()
            print(f"# yearly bills, {how}", file=sys.stderr)
            print("item\tsource\tmonth\tabout\tyears seen\tpaid in\tsaved for in\tset aside\tnext due\tnot paid in")
            for b in rows:
                print("\t".join(str(x) for x in (b["item"], b["source"], MONTHS[int(b["month"]) - 1] + (f" (every {b['every_years']} years)" if (b.get("every_years") or 1) > 1 else ""),
                      "" if b.get("amount") is None else f"{b['amount']:.2f}", b.get("years_seen", 0), ",".join(b.get("evidence") or []),
                      (b.get("funded_by") or "") + (" (guess)" if b.get("funded_guess") else ""), "" if b.get("set_aside") is None else f"{b['set_aside']:.2f}", b.get("next_due") or "", b.get("missed") or "")))
            found = [b for b in rows if b["source"] == "detected"]
            print(f"\n{len(found)} found and not yet confirmed, {sum(1 for b in rows if b['source'] in ('confirmed', 'added'))} confirmed or added, "
                  f"{sum(1 for b in rows if b['source'] == 'dismissed')} dismissed.", file=sys.stderr)
            return 0
        try:
            if a.ycmd in ("confirm", "add"):
                cur = next((b for b in yearly_bills.for_page(conn) if b["item"] == a.item), {})
                yearly_bills.save(conn, a.item, a.month or cur.get("month") or 1, a.amount if a.amount is not None else cur.get("amount"), a.every,
                                  a.fund or cur.get("funded_by"), "added" if a.ycmd == "add" else "confirmed", f"{_who()} (cli)")
            elif a.ycmd == "dismiss":
                yearly_bills.dismiss(conn, a.item, f"{_who()} (cli)")
            elif a.ycmd == "restore":
                was = yearly_bills.restore(conn, a.item)
                conn.commit(); print(f"{a.item}: " + (f"restored as {was}, with its values" if was != "detector" else "handed back to the detector") + ". Rebuild to apply it."); return 0
            conn.commit(); print(f"{a.item}: {dict(confirm='confirmed', add='added', dismiss='dismissed')[a.ycmd]}. Rebuild to apply it."); return 0
        except ValueError as e:
            conn.rollback(); print(f"yearly-bills {a.ycmd}: {e}", file=sys.stderr); return 2


def quarter_cmd(which: str | None) -> int:
    """`python -m app.admin quarter [YYYY-Qn]` (#147): read only, safe against a live install. On an install not yet built with
    quarters it works them out from the last build's monthly datasets; nothing is saved."""
    import re
    from pipeline import quarters
    with _connect() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        ds = {r["name"]: r["rows"] for r in conn.execute("SELECT name, rows FROM datasets WHERE name IN "
              "('monthly','cat_month','quarterly','cat_quarter','net_worth','balance_totals','coming_up','budget_items')").fetchall()}
        stored = [dict(r) for r in conn.execute("SELECT item, month, amount, every_years, funded_by, source FROM yearly_bills").fetchall()] \
            if conn.execute("SELECT to_regclass('yearly_bills') IS NOT NULL AS t").fetchone()["t"] else []
        rr = conn.execute("SELECT value FROM settings WHERE key='rules'").fetchone()
        conn.rollback()
    rules = (rr or {}).get("value") or {}
    ys = int(rules.get("year_start_month") or 1)
    apart = set(rules.get("debt_groups") if rules.get("debt_groups") is not None else ["Debt"]) | set(rules.get("goal_savings_groups") or [])   # role default
    monthly = ds.get("monthly") or []
    if not monthly:
        print("No build to read: the install has no monthly dataset yet.", file=sys.stderr); return 1
    how = "from the last build" if ds.get("quarterly") else "worked out now from the last build's monthly figures; nothing saved"
    rows = ds.get("quarterly") or quarters.quarterly(monthly, ys)
    cats = ds.get("cat_quarter") or quarters.cat_quarter(ds.get("cat_month") or [], ys)
    which = which or quarters.quarter_of(max(r["m"] for r in monthly), ys)[0]
    if not re.match(r"^\d{4}-Q[1-4]$", which):
        print("quarter: give it as YYYY-Qn, such as 2026-Q3", file=sys.stderr); return 2
    coming = ds.get("coming_up")
    if coming is None and ds.get("budget_items"):   # an install built before coming_up: its yearly bills, worked out as `yearly-bills` does
        from pipeline import yearly_bills as yb
        b = ds["budget_items"]; loaded = {r["m"] for r in b}
        coming = yb.coming_up_rows(yb.bills(b, stored, max(loaded), loaded, sorted({r["canonical"] for r in b})))
        how += "; coming up: yearly bills only"
    c = quarters.compare(which, rows, cats, ds.get("net_worth"), ds.get("balance_totals"), coming, monthly, ys, set_apart=apart)
    print(f"# the quarter, {how}; year_start_month {ys}", file=sys.stderr)
    if not c:
        print(f"{which} is not loaded.", file=sys.stderr); return 1
    by_m = {r["m"]: r for r in monthly}
    ok = all(abs(sum(float(by_m[m][f]) for m in r["loaded"]) - r[f]) < 0.01 for r in rows for f in quarters.FIELDS)
    pc = lambda v: "" if v is None else f"{v * 100:+.1f}%"
    def chg(o, f):   # the change in percent; a change of sign in words, since -358% says nothing
        if not o:
            return "not loaded"
        if o.get(f + "_turned"):
            return f"{o[f + '_change']:+.2f}/month, turned {o[f + '_turned']}"
        return pc(o.get(f + "_pct"))
    mv = lambda x: "new" if x.get("new") else pc(x["pct"])
    print(f"quarter\t{c['quarter']}\t{c['first']} to {c['last']}\t{c['months']} of 3 months" + (" (partial: compared per month)" if c["partial"] else ""))
    print(f"check\tquarters equal the sum of their months\t{'pass' if ok else 'FAIL'}")
    t = c["this"]
    print("figure\tthis, a month\tvs previous\tvs a year before")
    for f in ("income", "spend", "debt", "net"):
        p, y = c["previous"] or {}, c["year_ago"] or {}
        print(f"{f}\t{t[f]:.2f}\t{chg(c['previous'], f)}\t{chg(c['year_ago'], f)}")
    sr = lambda v: "" if v is None else f"{v * 100:.1f}%"
    print(f"savings rate\t{sr(t['savings_rate'])}\t{sr((c['previous'] or {}).get('savings_rate')) or 'not loaded'}\t{sr((c['year_ago'] or {}).get('savings_rate')) or 'not loaded'}")
    for key, title in (("movers_previous", "vs previous"), ("movers_year_ago", "vs a year before")):
        m = c[key]
        if m:
            print(f"movers {title}, by dollars\t" + "; ".join(f"{x['category']} {x['change']:+.0f}/month ({mv(x)})" + (", also last year" if x.get("also_last_year") else "") for x in m["by_dollars"]))
            print(f"movers {title}, by percent\t" + "; ".join(f"{x['category']} {pc(x['pct'])}" + (", also last year" if x.get("also_last_year") else "") for x in m["by_pct"]))
            if m.get("set_apart"):
                print(f"set apart {title} (debt and savings)\t" + "; ".join(f"{x['category']} {x['change']:+.0f}/month" for x in m["set_apart"]))
    print(f"net worth change\t{'' if c['net_worth_change'] is None else format(c['net_worth_change'], '+.2f')}")
    print(f"debt balance change\t{'' if c['debt_change'] is None else format(c['debt_change'], '+.2f')}")
    n = c["next"]
    print(f"next quarter\t{n['quarter']}\t{n['months'][0]} to {n['months'][-1]}\texpected a month: income {n['expected']['income']}, spend {n['expected']['spend']}, debt {n['expected']['debt']} ({n['basis']})")
    for x in n["coming_up"]:
        print(f"coming up\t{x['m']}\t{x['kind']}\t{x.get('public_label') or x['label']}\t{'' if x.get('amount') is None else x['amount']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.admin", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")
    for cmd, key in SETTERS.items():
        sub.add_parser(cmd, help=(f"replace the {key} from FILE (- for stdin), checked as on the Settings page" if key != "balances" else
                                  "add or correct month-end balances from a CSV FILE (account,YYYY-MM,amount; - for stdin); old values are kept as history")).add_argument("file")
    sub.add_parser("export", help="print an editor's current contents").add_argument("what", choices=EXPORTABLE)
    sub.add_parser("export-mapping", help="print the mapping CSV the Settings page exports")
    sub.add_parser("draft-mapping", help="print a draft mapping CSV for the unmapped items (nothing is saved)")
    sub.add_parser("add-mapping", help="add the rows of a mapping CSV (e.g. a reviewed draft); refuses items already mapped").add_argument("file")
    sub.add_parser("export-settings", help="write every setting into DIR in the seed-directory shape (as /export/settings.zip)").add_argument("dir")
    sub.add_parser("rebuild", help="rebuild the ledger and datasets (exit 75 if one is already running, 1 if it does not reconcile)")
    for cmd, what in (("rename-category", "a category"), ("rename-canonical", "a canonical item")):
        sp = sub.add_parser(cmd, help=f"rename {what} everywhere, rules included (renaming to an existing name merges them)")
        sp.add_argument("old"); sp.add_argument("new")
    sp = sub.add_parser("move-canonical", help="move a canonical item, and so all its rows, to another category")
    sp.add_argument("name"); sp.add_argument("category")
    sp = sub.add_parser("set-debt-type", help="set a canonical item's debt type (mortgage, student, auto, card, medical, personal, other; '' clears it)")
    sp.add_argument("name"); sp.add_argument("debt_type")
    sp = sub.add_parser("checkin", help="print where a month's check-in stands (completing it stays a browser action: the household's sign-off)")
    sp.add_argument("what", choices=["status"]); sp.add_argument("month", nargs="?", help="YYYY-MM (default: the latest loaded month)")
    sp = sub.add_parser("decisions", help="the decisions log (#155): list a month's, add one, confirm a month, carry one on, remove one")
    dsub = sp.add_subparsers(dest="dcmd", required=True, metavar="action")
    x = dsub.add_parser("list", help="the decisions in force at MONTH's check-in, and those made at it"); x.add_argument("month", nargs="?")
    x = dsub.add_parser("add", help="a decision made at MONTH's check-in, in force from the month after")
    x.add_argument("month"); x.add_argument("text")
    x.add_argument("--kind", default="other", choices=["spend_cap", "extra_payment", "save", "plan_change", "other"])
    g = x.add_mutually_exclusive_group(); g.add_argument("--category"); g.add_argument("--item"); g.add_argument("--debt", help="a debt account (extra_payment)")
    x.add_argument("--amount", type=float, help="the cap, the least to put in, the extra to pay or the new plan, by kind")
    x.add_argument("--until", help="the last month in force (YYYY-MM); default one month"); x.add_argument("--until-payoff", action="store_true")
    x.add_argument("--person"); x.add_argument("--note", default="")
    x = dsub.add_parser("check", help="the household's verdict on decision ID for MONTH"); x.add_argument("id", type=int); x.add_argument("month"); x.add_argument("verdict", choices=["kept", "partly", "dropped"])
    x = dsub.add_parser("carry", help="keep decision ID in force one month after MONTH"); x.add_argument("id", type=int); x.add_argument("month")
    x = dsub.add_parser("remove", help="delete decision ID and its checks"); x.add_argument("id", type=int)
    sp = sub.add_parser("yearly-bills", help="yearly bills (#151): a read-only report of what the last build found, or confirm/add/dismiss/restore one")
    ysub = sp.add_subparsers(dest="ycmd", metavar="action")
    ysub.add_parser("report", help="(the default) every bill the last build found or you listed, with the months it was seen in; read only")
    for name, what in (("confirm", "confirm a found bill, correcting it if need be"), ("add", "add a bill the detector missed")):
        x = ysub.add_parser(name, help=what); x.add_argument("item"); x.add_argument("--month", type=int, required=(name == "add"))
        x.add_argument("--amount", type=float); x.add_argument("--every", type=int, default=1); x.add_argument("--fund", help="the envelope or account that saves for it")
    x = ysub.add_parser("dismiss", help="not a yearly bill: stays dismissed across rebuilds"); x.add_argument("item")
    x = ysub.add_parser("restore", help="undo a dismissal: a bill you added or confirmed comes back with its values; a detected one goes back to the detector"); x.add_argument("item")
    sp = sub.add_parser("purchases", help="planned purchases (#158): list them with their progress, or add, change, mark bought, reopen or delete one")
    psub = sp.add_subparsers(dest="pcmd", metavar="action")
    psub.add_parser("list", help="(the default) each planned purchase and the last build's verdict; read only")
    for name in ("add", "change"):
        x = psub.add_parser(name, help="a new planned purchase" if name == "add" else "change planned purchase KEY")
        if name == "change": x.add_argument("key")
        x.add_argument("label"); x.add_argument("--amount", required=True); x.add_argument("--by", required=True, help="the budget month to have it by (YYYY-MM)")
        x.add_argument("--fund", required=True, help="the budget item (envelope) or account it is saved in"); x.add_argument("--since", help="the first month whose envelope saving counts")
    x = psub.add_parser("bought", help="mark KEY bought in MONTH; --event adds the life event, --loan-item sets up the debt")
    x.add_argument("key"); x.add_argument("month"); x.add_argument("--event", action="store_true")
    x.add_argument("--loan-item"); x.add_argument("--loan-category", help="default: the first of the debt_groups rule"); x.add_argument("--loan-type", default="auto")
    x.add_argument("--loan-account"); x.add_argument("--borrowed"); x.add_argument("--apr")
    for name, what in (("reopen", "not bought after all"), ("delete", "delete planned purchase KEY")):
        x = psub.add_parser(name, help=what); x.add_argument("key")
    sp = sub.add_parser("yearly-checklist", help="the yearly checklist (#157): list it (and a year's ticks), add, rename or remove an item, or tick one for a year")
    csub = sp.add_subparsers(dest="ccmd", metavar="action")
    x = csub.add_parser("list", help="(the default) the list; with --year, that budget year's ticks; read only"); x.add_argument("--year")
    x = csub.add_parser("add", help="add an item"); x.add_argument("label")
    x = csub.add_parser("rename", help="change an item's wording"); x.add_argument("key"); x.add_argument("label")
    x = csub.add_parser("remove", help="take an item off the list (past ticks stay)"); x.add_argument("key")
    x = csub.add_parser("tick", help="tick KEY for budget year YEAR (the year it starts in)"); x.add_argument("year"); x.add_argument("key"); x.add_argument("--note")
    x = csub.add_parser("untick", help="undo a tick"); x.add_argument("year"); x.add_argument("key")
    sp = sub.add_parser("quarter", help="the quarter (#147): a read-only report of a quarter against the one before and a year before, and the next")
    sp.add_argument("which", nargs="?", help="YYYY-Qn (default: the quarter of the latest loaded month)")
    sp = sub.add_parser("tax", help="estimated tax (#153): mark a quarter's estimate paid at its check-in month, or unmark it")
    tsub = sp.add_subparsers(dest="tcmd", required=True, metavar="action")
    x = tsub.add_parser("paid", help="the quarter ending MONTH: its estimated tax is paid"); x.add_argument("month"); x.add_argument("--amount")
    x = tsub.add_parser("unpaid", help="undo marking it paid"); x.add_argument("month")
    sub.add_parser("preview-mapping-migration", help="before upgrading to 0.5.0: print what migration 0133 would change in the item mapping "
                                                     "(nothing is changed; run it against a copy of the 0.4.x database)")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)

    from app.routes import settings as s
    if a.cmd in SETTERS:
        return _write(a.cmd, s._save_editor, SETTERS[a.cmd], _read(a.file), f"{_who()} (cli)")
    if a.cmd == "add-mapping":
        return _write(a.cmd, s._mapping_add, _read(a.file))
    if a.cmd == "rebuild":
        return rebuild_cmd()
    if a.cmd == "set-debt-type":
        from pipeline import mapping_store
        return _write(a.cmd, lambda conn: (mapping_store.set_debt_type(conn, a.name, a.debt_type), f"{a.name}'s debt type ({a.debt_type or 'cleared'})")[1])
    if a.cmd in ("rename-category", "rename-canonical", "move-canonical"):
        from app import renames
        if a.cmd == "move-canonical":
            return _write(a.cmd, renames.move_canonical, a.name, a.category)
        fn = renames.rename_category if a.cmd == "rename-category" else renames.rename_canonical
        return _write(a.cmd, fn, a.old, a.new, f"{_who()} (cli)")
    if a.cmd == "preview-mapping-migration":
        return preview_mapping_migration()
    if a.cmd == "checkin":
        return checkin_status(a.month)
    if a.cmd == "decisions":
        return decisions_cmd(a)
    if a.cmd == "yearly-bills":
        return yearly_bills_cmd(a)
    if a.cmd == "purchases":
        return purchases_cmd(a)
    if a.cmd == "yearly-checklist":
        return yearly_checklist_cmd(a)
    if a.cmd == "quarter":
        return quarter_cmd(a.which)
    if a.cmd == "tax":
        from app import tax
        with _connect() as conn:
            try:
                (tax.mark_paid(conn, a.month, a.amount, f"{_who()} (cli)") if a.tcmd == "paid" else tax.unmark(conn, a.month)); conn.commit()
                print(f"{a.month}: estimated tax {'marked paid' if a.tcmd == 'paid' else 'no longer marked paid'}. Rebuild to apply it."); return 0
            except ValueError as e:
                conn.rollback(); print(f"tax {a.tcmd}: {e}", file=sys.stderr); return 2
    with _connect() as conn:
        if a.cmd == "export-settings":
            from app.settings_export import write_dir
            names = write_dir(conn, Path(a.dir))
            print(f"Wrote {len(names)} files to {a.dir}: {', '.join(names)}. Put them in an empty SEED_DIR to start a new install with these settings.")
            return 0
        if a.cmd == "draft-mapping":
            rows = s.draft_mapping(conn)
            if not rows:
                print("Every item in the stored exports already has a mapping row.", file=sys.stderr)
                return 0
            sys.stdout.write(s.draft_csv(rows))
            print(f"{len(rows)} drafted rows; review them, then: python -m app.admin add-mapping FILE", file=sys.stderr)
            return 0
        body = s._editor_body(conn, "mapping" if a.cmd == "export-mapping" else a.what)
    sys.stdout.write(body if body.endswith("\n") else body + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
