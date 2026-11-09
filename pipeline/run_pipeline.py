"""Run the file-based pipeline against the database.

The database is the source of truth. For a rebuild we:
  1. export raw exports (transactions, budget_items) from Postgres into EveryDollar-shaped CSVs,
  2. export the reference tables (item_mapping, cpi, goals, life_events, rules) into the files the scripts read,
  3. run the unchanged pipeline scripts in a scratch working directory,
  4. load ledger.csv and dash_data/*.json back into Postgres (tables `ledger`, `datasets`).

Everything here is callable from the web app; `python -m pipeline.run_pipeline` runs a rebuild from the CLI.
"""
from __future__ import annotations
import csv, json, os, re, shutil, subprocess, sys, tempfile, hashlib
from pathlib import Path
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row, tuple_row

HERE = Path(__file__).resolve().parent
SCRIPTS = ["build_ledger.py", "aggregate.py", "make_datasets.py", "make_budget_items.py"]
FN_RX = re.compile(r"^(?P<hex>[0-9a-f]+)-(?P<mm>\d\d)-(?P<yyyy>\d{4})-EveryDollar-(?P<kind>Transactions|BudgetItems)\.csv$")


def norm(x: str) -> str:
    return re.sub(r"\s+", " ", (x or "").replace(" ", " ")).strip()


# ---------------------------------------------------------------- intake
def parse_export(filename: str, data: bytes):
    """Return (kind, budget_month, rows) for an EveryDollar export, or raise ValueError."""
    m = FN_RX.match(os.path.basename(filename))
    if not m:
        raise ValueError(f"{filename}: not an EveryDollar export filename (<hex>-MM-YYYY-EveryDollar-Transactions|BudgetItems.csv)")
    kind = "transactions" if m["kind"] == "Transactions" else "budget_items"
    bm = f"{m['yyyy']}-{m['mm']}"
    text = data.decode("utf-8-sig")
    rows = list(csv.DictReader(text.splitlines(keepends=True)))
    need = {"transactions": {"Group", "Item", "Type", "Date", "Merchant", "Amount"}, "budget_items": {"Group", "Item", "Planned", "Spent", "Remaining"}}[kind]
    if rows and not need.issubset(rows[0].keys()):
        raise ValueError(f"{filename}: missing columns {sorted(need - set(rows[0].keys()))}")
    return kind, bm, rows


def record_history(cur, event: str, by: str = "", **where) -> None:
    """Keep what is about to be replaced or deleted (name, sha256, row count, upload time) in source_file_history. Never the rows."""
    cond = " AND ".join(f"{k}=%s" for k in where)
    cur.execute("INSERT INTO source_file_history (kind, budget_month, filename, sha256, row_count, uploaded_at, uploaded_by, event, event_by) "
                f"SELECT kind, budget_month, filename, sha256, row_count, uploaded_at, uploaded_by, %s, %s FROM source_files WHERE {cond}", (event, by, *where.values()))


def ingest(conn: psycopg.Connection, filename: str, data: bytes, uploaded_by: str = "") -> dict:
    """Store one export file. Replaces an existing file of the same kind+month."""
    kind, bm, rows = parse_export(filename, data)
    sha = hashlib.sha256(data).hexdigest()
    with conn.cursor(row_factory=tuple_row) as cur:
        record_history(cur, "replaced", uploaded_by, kind=kind, budget_month=bm)
        cur.execute("DELETE FROM source_files WHERE kind=%s AND budget_month=%s", (kind, bm))
        cur.execute(
            "INSERT INTO source_files (kind, budget_month, filename, sha256, row_count, uploaded_by) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (kind, bm, os.path.basename(filename), sha, len(rows), uploaded_by),
        )
        sid = cur.fetchone()[0]
        if kind == "transactions":
            cur.executemany(
                "INSERT INTO transactions (source_file_id,budget_month,row_no,grp,item,type,date,merchant,account,amount,note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(sid, bm, i + 2, r["Group"], r["Item"], r["Type"], _date(r["Date"]), r.get("Merchant", ""), r.get("Account", ""), r["Amount"], r.get("Note", "")) for i, r in enumerate(rows)],
            )
        else:
            cur.executemany(
                "INSERT INTO budget_items (source_file_id,budget_month,grp,item,planned,spent,remaining) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                [(sid, bm, r["Group"], r["Item"], r["Planned"], r["Spent"], r["Remaining"]) for r in rows],
            )
    conn.commit()
    return {"kind": kind, "budget_month": bm, "rows": len(rows), "sha256": sha}


def _date(mmddyyyy: str) -> str:
    mm, dd, yy = mmddyyyy.split("/")
    return f"{yy}-{mm}-{dd}"


# ---------------------------------------------------------------- reconcile (in SQL, same rule as reconcile.py)
def reconcile(conn: psycopg.Connection, budget_month: str | None = None) -> dict:
    """Sum of signed Amount per (month, item) must equal the summary's Spent. Returns counts + mismatches."""
    where = "WHERE t.budget_month = %s" if budget_month else ""
    args = (budget_month,) if budget_month else ()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"""
          WITH tx AS (SELECT budget_month, item, SUM(amount) AS s, COUNT(*) AS n FROM transactions t {where} GROUP BY 1,2),
               bi AS (SELECT budget_month, item, spent FROM budget_items b {where.replace('t.', 'b.')})
          SELECT COALESCE(bi.budget_month, tx.budget_month) AS budget_month, COALESCE(bi.item, tx.item) AS item,
                 bi.spent, tx.s AS txn_sum, tx.n AS txn_count,
                 CASE WHEN bi.spent IS NULL THEN 'ITEM_NOT_IN_SUMMARY'
                      WHEN tx.s IS NULL AND bi.spent = 0 THEN 'OK_NO_ACTIVITY'
                      WHEN tx.s IS NULL THEN 'NO_TXNS_BUT_SPENT'
                      WHEN ABS(tx.s - bi.spent) < 0.005 THEN 'OK' ELSE 'MISMATCH' END AS status
          FROM bi FULL OUTER JOIN tx ON bi.budget_month = tx.budget_month AND bi.item = tx.item
        """, args + args if budget_month else ())
        rows = cur.fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    bad = [dict(r, spent=float(r["spent"]) if r["spent"] is not None else None, txn_sum=float(r["txn_sum"]) if r["txn_sum"] is not None else None)
           for r in rows if r["status"] not in ("OK", "OK_NO_ACTIVITY")]
    return {"counts": counts, "problems": bad, "clean": not bad}


def unmapped_items(conn: psycopg.Connection) -> list[dict]:
    """Items in the raw exports that have no mapping row, or only the placeholder category UNMAPPED, worst (most rows) first."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT item, category FROM item_mapping")
        mapped = {r["item"]: r["category"] for r in cur.fetchall()}
        cur.execute("""SELECT item, MIN(grp) AS grp, array_agg(DISTINCT budget_month ORDER BY budget_month) AS months, SUM(n)::int AS n, SUM(amt) AS amount FROM (
                         SELECT item, grp, budget_month, COUNT(*) AS n, SUM(amount) AS amt FROM transactions GROUP BY 1,2,3
                         UNION ALL SELECT item, grp, budget_month, 0, 0 FROM budget_items) u GROUP BY item""")
        found: dict[str, dict] = {}
        for r in cur.fetchall():
            k = norm(r["item"])
            if mapped.get(k, "UNMAPPED") != "UNMAPPED":
                continue
            f = found.setdefault(k, {"item": k, "group": r["grp"], "months": set(), "rows": 0, "amount": 0.0})
            f["months"].update(r["months"]); f["rows"] += r["n"]; f["amount"] += float(r["amount"])
    out = [dict(f, months=sorted(f["months"]), amount=round(f["amount"], 2)) for f in found.values()]
    return sorted(out, key=lambda f: (-f["rows"], f["item"]))


def save_findings(conn: psycopg.Connection, build_id: int, findings: dict) -> None:
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("UPDATE build_log SET findings=%s::jsonb WHERE id=%s", (json.dumps(findings), build_id))
    conn.commit()


# ---------------------------------------------------------------- rebuild
def export_for_pipeline(conn: psycopg.Connection, wd: Path) -> None:
    (wd / "txns").mkdir(parents=True, exist_ok=True)
    (wd / "summaries").mkdir(parents=True, exist_ok=True)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id, kind, budget_month, filename FROM source_files ORDER BY budget_month")
        files = cur.fetchall()
        for f in files:
            bm = f["budget_month"]; yyyy, mm = bm.split("-")
            if f["kind"] == "transactions":
                cur.execute("SELECT grp,item,type,to_char(date,'MM/DD/YYYY') d,merchant,account,amount,note FROM transactions WHERE source_file_id=%s ORDER BY row_no", (f["id"],))
                path = wd / "txns" / f"db-{mm}-{yyyy}-EveryDollar-Transactions.csv"
                with open(path, "w", newline="", encoding="utf-8") as out:
                    w = csv.writer(out, quoting=csv.QUOTE_ALL); w.writerow(["Group", "Item", "Type", "Date", "Merchant", "Account", "Amount", "Note"])
                    for r in cur: w.writerow([r["grp"], r["item"], r["type"], r["d"], r["merchant"], r["account"], f"{r['amount']:.2f}", r["note"]])
            else:
                cur.execute("SELECT grp,item,planned,spent,remaining FROM budget_items WHERE source_file_id=%s ORDER BY id", (f["id"],))
                path = wd / "summaries" / f"db-{mm}-{yyyy}-EveryDollar-BudgetItems.csv"
                with open(path, "w", newline="", encoding="utf-8") as out:
                    w = csv.writer(out, quoting=csv.QUOTE_ALL); w.writerow(["Group", "Item", "Planned", "Spent", "Remaining"])
                    for r in cur: w.writerow([r["grp"], r["item"], f"{r['planned']:.2f}", f"{r['spent']:.2f}", f"{r['remaining']:.2f}"])
        # reference tables
        cur.execute("SELECT item, canonical_item, category, flow, note FROM item_mapping ORDER BY item")
        with open(wd / "item_mapping.csv", "w", newline="") as out:
            w = csv.writer(out); w.writerow(["Item", "CanonicalItem", "Category", "Flow", "Note"])
            for r in cur: w.writerow([r["item"], r["canonical_item"], r["category"], r["flow"], r["note"]])
        cur.execute("SELECT month, cpi, source FROM cpi ORDER BY month")
        with open(wd / "cpi_u.csv", "w", newline="") as out:
            w = csv.writer(out); w.writerow(["month", "cpi", "source"])
            for r in cur: w.writerow([r["month"], f"{r['cpi']:.3f}", r["source"]])
        cur.execute("SELECT key, value, unit, label, note, confirmed_at FROM goals ORDER BY key")
        goals = [dict(r, value=float(r["value"]) if r["value"] is not None else None, confirmed_at=r["confirmed_at"].isoformat() if r["confirmed_at"] else None) for r in cur]
        cur.execute("SELECT month m, label FROM life_events ORDER BY month, label")
        with open(wd / "events.json", "w") as out:
            json.dump([dict(r) for r in cur], out)
        cur.execute("SELECT value FROM settings WHERE key='rules'")
        row = cur.fetchone()
    with open(wd / "rules.json", "w") as out:
        json.dump(row["value"] if row else {}, out)
    (wd / "dash_data").mkdir(exist_ok=True)
    with open(wd / "dash_data" / "goals.json", "w") as out:
        json.dump(goals, out)


def run_scripts(wd: Path) -> str:
    log = []
    for s in SCRIPTS:
        p = subprocess.run([sys.executable, "-I", str(HERE / s)], cwd=wd, capture_output=True, text=True, timeout=600)
        log.append(f"$ {s}\n{p.stdout}{p.stderr}")
        if p.returncode != 0:
            raise RuntimeError("\n".join(log))
    return "\n".join(log)


def _read(path: Path) -> str:
    with open(path) as f:
        return f.read()


def load_outputs(conn: psycopg.Connection, wd: Path) -> dict:
    counts = {}
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("TRUNCATE ledger")
        with open(wd / "ledger.csv", newline="") as f:
            rows = list(csv.DictReader(f))
        cur.executemany(
            """INSERT INTO ledger (budget_month,calendar_month,date,grp,item,type,canonical_item,category,flow,merchant,merchant_norm,amount,amount_real,
               earner,payer,employer,is_transfer,is_plug,transfer_pair,in_totals,has_note,note,source_file,source_row)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            [(r["BudgetMonth"], r["CalendarMonth"], r["Date"], r["Group"], r["Item"], r["Type"], r["CanonicalItem"], r["Category"], r["Flow"],
              r["Merchant"], r["MerchantNorm"], r["Amount"], r["AmountReal"], r["Earner"], r["Payer"], r["Employer"],
              r["IsTransfer"] == "1", r["IsPlug"] == "1", int(r["TransferPair"]) if r["TransferPair"] else None, r["InTotals"] == "1",
              r["HasNote"] == "1", r["Note"], r["SourceFile"], int(r["SourceRow"])) for r in rows],
        )
        counts["ledger"] = len(rows)
        for p in sorted((wd / "dash_data").glob("*.json")):
            with open(p) as f:
                data = json.load(f)
            cur.execute("INSERT INTO datasets (name, rows, row_count) VALUES (%s,%s,%s) ON CONFLICT (name) DO UPDATE SET rows=EXCLUDED.rows, row_count=EXCLUDED.row_count, built_at=now()",
                        (p.stem, json.dumps(data), len(data)))
            counts[p.stem] = len(data)
        # review dataset comes from the reviews table
        cur.execute("SELECT month, sections FROM reviews ORDER BY month")
        review_rows = [dict(m=m, **sec) for m, secs in cur.fetchall() for sec in secs]
        cur.execute("INSERT INTO datasets (name, rows, row_count) VALUES ('review',%s,%s) ON CONFLICT (name) DO UPDATE SET rows=EXCLUDED.rows, row_count=EXCLUDED.row_count, built_at=now()",
                    (json.dumps(review_rows), len(review_rows)))
    conn.commit()
    return counts


BUILD_LOCK = (0x6C65, 0x6264)  # advisory lock (two int4 keys) held, on the building connection, for the whole of a rebuild


class BuildBusy(RuntimeError):
    """Another rebuild holds the lock (this process or another one)."""


def build_running(conn: psycopg.Connection) -> bool:
    """True while some session holds the build lock. A crashed process drops its session, and with it the lock."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=%s AND objid=%s AND objsubid=2 AND granted "
                    "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())", BUILD_LOCK)   # advisory locks are per database
        busy = cur.fetchone() is not None
    conn.commit()
    return busy


def rebuild(conn: psycopg.Connection) -> dict:
    """Rebuild ledger and datasets. Only one rebuild runs at a time across all processes (BuildBusy otherwise)."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s, %s)", BUILD_LOCK)
        if not cur.fetchone()[0]:
            conn.commit()
            raise BuildBusy("a rebuild is already running")
    try:
        return _rebuild(conn)
    finally:
        conn.rollback()
        with conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT pg_advisory_unlock(%s, %s)", BUILD_LOCK)
        conn.commit()


def _rebuild(conn: psycopg.Connection) -> dict:
    with conn.cursor(row_factory=tuple_row) as cur:
        # holding the lock, so any row still unfinished belongs to a process that died mid-build
        cur.execute("UPDATE build_log SET finished_at=now(), ok=false, summary='interrupted: the process stopped before this build finished' WHERE finished_at IS NULL")
        cur.execute("INSERT INTO build_log DEFAULT VALUES RETURNING id"); bid = cur.fetchone()[0]
    conn.commit()
    wd = Path(tempfile.mkdtemp(prefix="ledger-build-"))
    try:
        rec = reconcile(conn)
        # recorded before the scripts run: an unmapped item makes build_ledger.py exit, and that is when the list matters most
        save_findings(conn, bid, {"unmapped": unmapped_items(conn), "reconciliation": dict(rec, problems=rec["problems"][:200])})
        if conn.execute("SELECT 1 FROM source_files LIMIT 1").fetchone() is None:   # every month deleted: nothing to build, derived tables empty
            conn.execute("TRUNCATE ledger, datasets"); log = "no source files loaded; ledger and datasets emptied"; counts = {"ledger": 0}
        else:
            export_for_pipeline(conn, wd)
            log = run_scripts(wd)
            counts = load_outputs(conn, wd)
        summary = f"ledger rows {counts.get('ledger')}; reconciliation {rec['counts']}; {'clean' if rec['clean'] else str(len(rec['problems'])) + ' problems'}"
        detail = log + "".join(f"\nReconciliation: {p}" for p in rec.get("problems", []))
        with conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("UPDATE build_log SET finished_at=now(), ok=true, summary=%s, detail=%s WHERE id=%s", (summary, detail, bid))
        conn.commit()
        return {"ok": True, "summary": summary, "reconciliation": rec, "counts": counts}
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        with conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("UPDATE build_log SET finished_at=now(), ok=false, summary=%s, detail=%s WHERE id=%s", (str(e)[:500], str(e), bid))
        conn.commit()
        raise
    finally:
        shutil.rmtree(wd, ignore_errors=True)


# ---------------------------------------------------------------- bulk import
def import_dir(conn: psycopg.Connection, import_dir: Path, uploaded_by: str = "import") -> dict:
    """Load every EveryDollar export under import_dir that is new or changed (by sha256), then rebuild once if anything loaded.
    Safe to run at every start: already-loaded files are skipped, so the directory can stay in place."""
    import_dir = Path(import_dir)
    if not import_dir.is_dir():
        return {"loaded": 0, "skipped": 0}
    known = {}
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT kind, budget_month, sha256 FROM source_files")
        known = {(k, m): sha for k, m, sha in cur.fetchall()}
    loaded = skipped = 0
    failed: list[dict] = []
    for f in sorted(x for x in import_dir.rglob("*.csv") if FN_RX.match(x.name)):
        data = f.read_bytes()
        m = FN_RX.match(f.name)
        kind = "transactions" if m["kind"] == "Transactions" else "budget_items"
        if known.get((kind, f"{m['yyyy']}-{m['mm']}")) == hashlib.sha256(data).hexdigest():
            skipped += 1; continue
        try:
            ingest(conn, f.name, data, uploaded_by=uploaded_by); loaded += 1
        except Exception as e:  # noqa: BLE001  one bad file must not stop the others
            conn.rollback()
            failed.append({"file": f.name, "error": str(e)[:300]})
    conn.commit()
    try:
        res = rebuild(conn) if loaded else None
    finally:
        if failed:   # after the rebuild, so this is the newest build_log row and the failure shows on the dashboard and the build log
            lines = "".join(f"\n{x['file']}: {x['error']}" for x in failed)
            conn.execute("INSERT INTO build_log (finished_at, ok, summary, detail) VALUES (now(), false, %s, %s)",
                         (f"import: {len(failed)} file(s) could not be loaded ({failed[0]['file']}: {failed[0]['error']})"[:500], f"{len(failed)} import file(s) failed:{lines}"))
            conn.commit()
    return {"loaded": loaded, "skipped": skipped, "failed": failed, "rebuild": res and res["summary"]}


# ---------------------------------------------------------------- seed
def seed_reference_tables(conn: psycopg.Connection, seed_dir: Path = HERE) -> None:
    """First boot: load the mapping, CPI, goals, rules, events and past reviews, only into empty tables.
    Each file is taken from seed_dir when present there, else from the generic files shipped next to this module."""
    _given = Path(seed_dir)

    class _Dir:
        def __truediv__(self, name):
            p = _given / name
            return p if p.exists() else HERE / name
    seed_dir = _Dir()
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT count(*) FROM item_mapping")
        if cur.fetchone()[0] == 0 and (seed_dir / "item_mapping.csv").exists():
            with open(seed_dir / "item_mapping.csv") as f:
                for r in csv.DictReader(f):
                    cur.execute("INSERT INTO item_mapping (item,canonical_item,category,flow,note) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                                (norm(r["Item"]), r["CanonicalItem"], r["Category"], r["Flow"], r.get("Note", "")))
        cur.execute("SELECT count(*) FROM cpi")
        if cur.fetchone()[0] == 0 and (seed_dir / "cpi_u.csv").exists():
            with open(seed_dir / "cpi_u.csv") as f:
                for r in csv.DictReader(f):
                    cur.execute("INSERT INTO cpi VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (r["month"], r["cpi"], r["source"]))
        cur.execute("SELECT count(*) FROM goals")
        if cur.fetchone()[0] == 0 and (seed_dir / "goals.json").exists():
            with open(seed_dir / "goals.json") as f:
                goals_seed = json.load(f)
            for g in goals_seed:
                cur.execute("INSERT INTO goals (key,value,unit,label,note) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", (g["key"], g["value"], g.get("unit", ""), g["label"], g.get("note", "")))
        cur.execute("SELECT count(*) FROM life_events")
        if cur.fetchone()[0] == 0 and (seed_dir / "events.json").exists():
            with open(seed_dir / "events.json") as f:
                events_seed = json.load(f)
            for e in events_seed:
                cur.execute("INSERT INTO life_events VALUES (%s,%s) ON CONFLICT DO NOTHING", (e["m"], e["label"]))
        cur.execute("SELECT count(*) FROM settings WHERE key='rules'")
        if cur.fetchone()[0] == 0 and (seed_dir / "rules.json").exists():
            cur.execute("INSERT INTO settings (key, value) VALUES ('rules', %s)", (_read(seed_dir / "rules.json"),))
        cur.execute("SELECT count(*) FROM reviews")
        if cur.fetchone()[0] == 0 and (seed_dir / "review.json").exists():
            with open(seed_dir / "review.json") as f:
                rows = json.load(f)
            by: dict[str, list] = {}
            for r in rows: by.setdefault(r["m"], []).append({"order": r["order"], "heading": r["heading"], "text": r["text"]})
            for m, secs in by.items():
                cur.execute("INSERT INTO reviews (month, model, sections, prompt) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (m, "seed", json.dumps(secs), "seeded from the original build"))
    conn.commit()


if __name__ == "__main__":
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url) as c:
        print(json.dumps(rebuild(c)["summary"]))
