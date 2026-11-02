"""Bulk import: `python -m pipeline.import_exports <dir-or-files...>` stores every EveryDollar export found, then rebuilds once.
Use for the first load of a back catalogue; the web upload is for one month at a time."""
from __future__ import annotations
import os, sys
from pathlib import Path
import psycopg
from .run_pipeline import ingest, rebuild, import_dir, FN_RX


def main(paths: list[str]) -> int:
    files = []
    for p in paths:
        P = Path(p)
        files += sorted(x for x in (P.rglob("*.csv") if P.is_dir() else [P]) if FN_RX.match(x.name))
    if not files:
        print("no EveryDollar export files found", file=sys.stderr); return 2
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for f in files:
            info = ingest(conn, f.name, f.read_bytes(), uploaded_by="import")
            print(f"stored {info['budget_month']} {info['kind']:12s} {info['rows']:5d} rows")
        conn.commit()
        res = rebuild(conn)
    print(res["summary"])
    for p in res["reconciliation"].get("problems", []):
        print("PROBLEM", p)
    return 0 if res["reconciliation"]["clean"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
