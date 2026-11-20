"""Bulk import: `python -m pipeline.import_exports [--profile NAME] [--dry-run] <dir-or-files...>` stores every export found, then
rebuilds once. Use for the first load of a back catalogue; the web upload is for one month at a time (or a zip of many).

Files named like exports (the internal format, or EveryDollar's) are read as they are. With --profile NAME the other CSVs are read
through that saved source profile (#165): its headers say which file is which, and its month rule (month_from), or else the most
common month of a transactions file's dates, says the budget month; a budget file with no month in its name is paired with the
transactions file it reconciles with. Every file is printed with its month and how it was found. --dry-run prints that and stores
nothing. Exit 0 done (and reconciled); 1 a file was refused, or the build does not reconcile; 2 nothing to import or a bad argument."""
from __future__ import annotations
import argparse, os, sys
from pathlib import Path
import psycopg
from . import filenames
from .run_pipeline import ingest, rebuild, plan_files, store_planned, other_csv, FN_RX


def _collect(paths: list[str]) -> list[tuple[Path, Path]]:
    """(file, the directory it was found under) for every CSV given or found; a file given by name is its own root."""
    out = []
    for p in paths:
        P = Path(p)
        out += [(x, P if P.is_dir() else P.parent) for x in sorted(P.rglob("*.csv") if P.is_dir() else [P])]
    return out


def describe(plan: list[dict], verb: str = "would store") -> list[str]:
    """One line per planned file: what the dry run says about it, as the zip upload's table does (verb "store" in a real run,
    where the lines are printed before anything is stored, so a month guessed from the dates is shown before it is stored)."""
    lines = []
    for e in plan:
        head = f"{e['month'] or '-------'} {(e['kind'] or '?'):12s} {e['name']}"
        if e["state"] == "refused":
            lines.append(f"REFUSED {head}: {e['detail']}")
        else:
            lines.append(f"{verb if e['state'] == 'ready' else 'unchanged'} {head} (“{e['profile']}”, {e['rows']} rows; month {e['how']})"
                         + (f"; {e['detail']}" if e["detail"] and e["state"] == "ready" else ""))
    return lines


def run(conn, paths: list[str], *, profile: str | None = None, dry_run: bool = False, uploaded_by: str = "import", out=print, err=None) -> int:
    """The import on an open connection; app.admin's `import` calls it too. Returns the exit code."""
    err = err or (lambda s: print(s, file=sys.stderr))
    found = _collect(paths)
    named = [f for f, _ in found if FN_RX.match(f.name)]
    other = [f for f, root in found if other_csv(f, root)] if profile else []   # not seed files, nothing under a dot-folder
    if not named and not other:
        err("no monthly export files found (internal format or EveryDollar's names" + (", or CSVs to read with a profile)" if profile else
            "; pass --profile NAME to read another tool's files)")); return 2
    files = [(str(f), f.read_bytes()) for f in other]
    try:
        plan = plan_files(conn, files, default_mode=True, profile_name=profile, present={filenames.parse(f.name) for f in named}) if other else []
    except ValueError as e:
        err(str(e)); return 2
    refused = [e for e in plan if e["state"] == "refused"]
    if dry_run:
        for f in named:
            out(f"would store {f.name} (named like an export)")
        for line in describe(plan):
            out(line)
        out(f"dry run: nothing stored; {len(named) + sum(1 for e in plan if e['state'] == 'ready')} file(s) would be stored, {len(refused)} refused")
        return 1 if refused else 0
    for line in describe(plan, verb="store"):   # the whole table first: every month, and how it was found, before anything is stored
        out(line)
    for f in named:
        info = ingest(conn, f.name, f.read_bytes(), uploaded_by=uploaded_by)
        out(f"stored {info['budget_month']} {info['kind']:12s} {info['rows']:5d} rows")
    for r in store_planned(conn, plan, files, uploaded_by, atomic=True):
        out(f"stored {r['name']}: {r['detail']}")
    conn.commit()
    res = rebuild(conn)
    out(res["summary"])
    for p in res["reconciliation"].get("problems", []):
        out(f"PROBLEM {p}")
    return 0 if res["reconciliation"]["clean"] and not refused else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.import_exports", description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+", help="directories (searched for *.csv) or files")
    ap.add_argument("--profile", help="read the CSVs not named like exports through this saved source profile")
    ap.add_argument("--dry-run", action="store_true", help="print what would be stored, with each month and how it was found; store nothing")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        return run(conn, a.paths, profile=a.profile, dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
