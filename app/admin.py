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
EXPORTABLE = ("mapping", "rules", "events", "goals", "cpi", "household", "accounts", "balances", "decisions")


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
