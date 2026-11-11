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


def _store_rows(cur, sid: int, kind: str, bm: str, rows: list[dict]) -> None:
    """Insert one file's rows, already in the internal (EveryDollar) shape."""
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


def ingest(conn: psycopg.Connection, filename: str, data: bytes, uploaded_by: str = "", *,
           profile: dict | None = None, kind: str | None = None, budget_month: str | None = None) -> dict:
    """Store one export file. Replaces an existing file of the same kind+month.

    Without `profile` the file must be an EveryDollar export, named as EveryDollar names it. With a profile ({"id", "spec"} from
    source_profiles) the caller says which kind of file it is and for which budget month; the file is kept as uploaded and its
    rows are read through the profile's mapping."""
    if profile is None:
        kind, bm, rows = parse_export(filename, data)
        content, profile_id = None, None
    else:
        from .sources import apply
        if kind not in ("transactions", "budget_items"):
            raise ValueError(f"{filename}: say whether this is the transactions file or the budget file")
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", budget_month or ""):
            raise ValueError(f"{filename}: the budget month must be YYYY-MM")
        bm, rows = budget_month, apply(kind, data, profile["spec"])
        content, profile_id = data, profile["id"]
    sha = hashlib.sha256(data).hexdigest()
    with conn.cursor(row_factory=tuple_row) as cur:
        record_history(cur, "replaced", uploaded_by, kind=kind, budget_month=bm)
        cur.execute("DELETE FROM source_files WHERE kind=%s AND budget_month=%s", (kind, bm))
        cur.execute(
            "INSERT INTO source_files (kind, budget_month, filename, sha256, row_count, uploaded_by, profile_id, content) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (kind, bm, os.path.basename(filename), sha, len(rows), uploaded_by, profile_id, content),
        )
        _store_rows(cur, cur.fetchone()[0], kind, bm, rows)
    conn.commit()
    return {"kind": kind, "budget_month": bm, "rows": len(rows), "sha256": sha}


def reapply_profile(conn: psycopg.Connection, profile_id: int) -> int:
    """Re-read every stored file of a profile through its current spec, replacing the rows derived from them, in one transaction.
    The files themselves are not touched. Returns the number of files; raises ValueError naming the file that no longer reads."""
    from .sources import apply, ProfileError
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT spec FROM source_profiles WHERE id=%s", (profile_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError("no such profile")
        cur.execute("SELECT id, kind, budget_month, filename, content FROM source_files WHERE profile_id=%s ORDER BY budget_month, kind", (profile_id,))
        files = cur.fetchall()
        try:
            for f in files:
                rows = apply(f["kind"], bytes(f["content"]), row["spec"])
                cur.execute(f"DELETE FROM {'transactions' if f['kind'] == 'transactions' else 'budget_items'} WHERE source_file_id=%s", (f["id"],))
                _store_rows(cur, f["id"], f["kind"], f["budget_month"], rows)
                cur.execute("UPDATE source_files SET row_count=%s WHERE id=%s", (len(rows), f["id"]))
        except ProfileError as e:
            conn.rollback()
            raise ValueError(f"{f['filename']} ({f['budget_month']}): {e}") from None
    conn.commit()
    return len(files)


def save_profile(conn: psycopg.Connection, spec: dict, commit: bool = True) -> int:
    """Insert or update a profile by name (validated first); returns its id. The caller re-applies it to stored files if it changed:
    pass commit=False and let reapply_profile commit, so a profile that no longer reads its files is never saved."""
    from .sources import validate_profile
    validate_profile(spec)
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("INSERT INTO source_profiles (name, spec) VALUES (%s, %s) ON CONFLICT (name) DO UPDATE SET spec=EXCLUDED.spec, updated_at=now() RETURNING id",
                    (spec["name"].strip(), json.dumps(spec)))
        pid = cur.fetchone()[0]
    if commit:
        conn.commit()
    return pid


def load_profiles(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT p.id, p.name, p.spec, p.updated_at, count(f.id)::int AS files FROM source_profiles p LEFT JOIN source_files f ON f.profile_id=p.id "
                    "GROUP BY p.id ORDER BY p.name")
        return cur.fetchall()


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
        cur.execute("SELECT month m, label, kind, person FROM life_events ORDER BY month, label")
        with open(wd / "events.json", "w") as out:   # kind and person only when set (#109), so events without them read as before
            json.dump([{k: v for k, v in r.items() if v is not None} for r in cur], out)
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


class ImportRebuildFailed(RuntimeError):
    """import_dir stored its files, then the rebuild after them failed. The message says both, and what to do about it."""
    def __init__(self, message: str, loaded: int, skipped: int):
        super().__init__(message)
        self.loaded, self.skipped = loaded, skipped


def _rebuild_failure(conn: psycopg.Connection, loaded: int, skipped: int, e: Exception) -> str:
    head = f"{loaded} file(s) stored, {skipped} unchanged; the rebuild after them failed"
    try:
        um = unmapped_items(conn)
    except Exception:  # noqa: BLE001  the message must not hide the original failure
        conn.rollback(); um = []
    if um:
        names = ", ".join(repr(x["item"]) for x in um[:3]) + (f" and {len(um) - 3} more" if len(um) > 3 else "")
        return (f"{head}: {len(um)} item(s) have no mapping ({names}). The files are kept; add the items on Settings → Item mapping, "
                "then press Rebuild without uploading on Close a month.")
    last = next((ln.strip() for ln in reversed(str(e).splitlines()) if ln.strip()), type(e).__name__)
    return f"{head}: {last[:500]}. The files are kept; fix the cause, then press Rebuild without uploading on Close a month."


def build_running(conn: psycopg.Connection) -> bool:
    """True while some session holds the build lock. A crashed process drops its session, and with it the lock."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=%s AND objid=%s AND objsubid=2 AND granted "
                    "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())", BUILD_LOCK)   # advisory locks are per database
        busy = cur.fetchone() is not None
    conn.commit()
    return busy


MAX_PASSES = 3   # a rebuild runs again while the source files keep changing underneath it, at most this many times


def source_fingerprint(conn: psycopg.Connection) -> str:
    """Identity of the stored export files: changes on every upload, replacement and delete (a replaced file gets a new id)."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT coalesce(md5(string_agg(id::text || ':' || sha256, ',' ORDER BY id)), '') FROM source_files")
        fp = cur.fetchone()[0]
    conn.commit()
    return fp


def rebuild(conn: psycopg.Connection) -> dict:
    """Rebuild ledger and datasets. Only one rebuild runs at a time across all processes (BuildBusy otherwise).
    If the source files changed while a pass was running (an upload or delete arrived mid-build), build again, at most MAX_PASSES times.
    The check is made after the lock is released: a request that saw the lock held and so queued nothing has committed its change before that,
    and one that comes later finds no build running and starts its own."""
    res = None
    for attempt in range(MAX_PASSES):
        with conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s, %s)", BUILD_LOCK)
            got = cur.fetchone()[0]
        if not got:
            conn.commit()
            if attempt == 0:
                raise BuildBusy("a rebuild is already running")
            return res   # someone else started a build after our last pass; it reads the newest files
        try:
            res = _rebuild(conn)
        finally:
            conn.rollback()
            with conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SELECT pg_advisory_unlock(%s, %s)", BUILD_LOCK)
            conn.commit()
        if source_fingerprint(conn) == res["sources"]:
            return res
    conn.execute("UPDATE build_log SET summary = summary || %s WHERE id=%s", ("; the source files changed again during the build: press Rebuild", res["build_id"]))
    conn.commit()
    res["summary"] += "; the source files changed again during the build: press Rebuild"
    return res


def describe_problems(problems: list[dict], conn: psycopg.Connection) -> list[str]:
    """Reconciliation problems as sentences, status code last (the Method tab and the findings use the code). A month whose
    BudgetItems file is missing makes every envelope "not in summary": that is said once, not once per envelope."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT budget_month FROM source_files GROUP BY 1 HAVING bool_and(kind = 'transactions')")
        no_budget = {r["budget_month"] for r in cur.fetchall()}
    money = lambda v: f"{v:,.2f}" if v is not None else "—"
    out, collapsed = [], {}
    for p in problems:
        m, item, st = p.get("budget_month"), p.get("item"), p.get("status")
        if st == "ITEM_NOT_IN_SUMMARY" and m in no_budget:
            collapsed[m] = collapsed.get(m, 0) + 1; continue
        if st == "ITEM_NOT_IN_SUMMARY":
            out.append(f"{m} {item}: {p.get('txn_count') or 0} transaction(s) totalling {money(p.get('txn_sum'))}, but no line for it in the budget file ({st})")
        elif st == "NO_TXNS_BUT_SPENT":
            out.append(f"{m} {item}: the budget file says {money(p.get('spent'))} was spent, but there are no transactions ({st})")
        else:
            out.append(f"{m} {item}: transactions total {money(p.get('txn_sum'))}, the budget file says {money(p.get('spent'))} ({st})")
    return [f"{m}: {n} envelope(s) have transactions but no budget line, because the BudgetItems file is missing (ITEM_NOT_IN_SUMMARY)"
            for m, n in sorted(collapsed.items())] + out


def incomplete_months_note(conn: psycopg.Connection) -> str:
    """'2026-06: the BudgetItems file is missing (upload it to finish this month). ' for every month with only one of its two files."""
    with conn.cursor(row_factory=dict_row) as cur:   # the app's pool and the CLI's plain connection return different row shapes
        cur.execute("""SELECT budget_month, bool_or(kind = 'transactions') AS has_txns FROM source_files
                       GROUP BY 1 HAVING count(DISTINCT kind) < 2 ORDER BY 1""")
        rows = cur.fetchall()
    return "".join(f"{r['budget_month']}: the {'BudgetItems' if r['has_txns'] else 'Transactions'} file is missing "
                   "(upload it to finish this month). " for r in rows)


def _rebuild(conn: psycopg.Connection) -> dict:
    with conn.cursor(row_factory=tuple_row) as cur:
        # holding the lock, so any row still unfinished belongs to a process that died mid-build
        cur.execute("UPDATE build_log SET finished_at=now(), ok=false, summary='interrupted: the process stopped before this build finished' WHERE finished_at IS NULL")
        cur.execute("INSERT INTO build_log DEFAULT VALUES RETURNING id"); bid = cur.fetchone()[0]
    conn.commit()
    note = incomplete_months_note(conn)   # half a month reconciles as every envelope wrong: name the missing file first
    sources = source_fingerprint(conn)   # what this pass builds from; rebuild() compares it with the files once the pass is over
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
        n_ok = sum(v for k, v in rec["counts"].items() if k.startswith("OK"))
        summary = note + (f"{counts.get('ledger')} ledger rows; " + (f"all {n_ok} envelope-months reconcile" if rec["clean"]
                   else f"{len(rec['problems'])} envelope-month(s) do not reconcile (listed below)"))
        detail = log + "".join(f"\nReconciliation: {line}" for line in describe_problems(rec.get("problems", []), conn))
        with conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("UPDATE build_log SET finished_at=now(), ok=true, summary=%s, detail=%s WHERE id=%s", (summary, detail, bid))
        conn.commit()
        return {"ok": True, "summary": summary, "reconciliation": rec, "counts": counts, "sources": sources, "build_id": bid}
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        with conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("UPDATE build_log SET finished_at=now(), ok=false, summary=%s, detail=%s WHERE id=%s", ((note + str(e))[:500], note + str(e), bid))
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
    except BuildBusy:
        raise
    except Exception as e:  # noqa: BLE001  the files are committed above: say so, rather than "import failed"
        raise ImportRebuildFailed(_rebuild_failure(conn, loaded, skipped, e), loaded, skipped) from e
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
                cur.execute("INSERT INTO life_events (month,label,kind,person) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                            (e["m"], e["label"], (e.get("kind") or "").strip().lower() or None, (e.get("person") or "").strip() or None))
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
