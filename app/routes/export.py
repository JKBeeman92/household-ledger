"""Take your data out: /export/ledger.csv, /export/datasets.zip, /export/sources.zip (read-only, any signed-in user), plus the
editor's "Scan import directory now". Downloads are streamed: the CSV from a server-side cursor, the zips written through a
non-seekable ZIP writer one file at a time, so memory stays bounded by the biggest single chunk, not the whole export."""
from __future__ import annotations
import csv, io, json, zipfile
from pathlib import Path

from psycopg.rows import tuple_row
from fastapi import APIRouter, Request, Depends
from fastapi.responses import StreamingResponse, RedirectResponse

from ..config import settings
from .. import db, auth
from ..web_helpers import flash
from pipeline.run_pipeline import import_dir, BuildBusy, ImportRebuildFailed

router = APIRouter()

LEDGER_COLS = [("BudgetMonth", "budget_month"), ("CalendarMonth", "calendar_month"), ("Date", "date"), ("Group", "grp"), ("Item", "item"), ("Type", "type"),
               ("CanonicalItem", "canonical_item"), ("Category", "category"), ("Flow", "flow"), ("Merchant", "merchant"), ("MerchantNorm", "merchant_norm"),
               ("Amount", "amount"), ("AmountReal", "amount_real"), ("Earner", "earner"), ("Payer", "payer"), ("Employer", "employer"),
               ("IsTransfer", "is_transfer"), ("IsPlug", "is_plug"), ("TransferPair", "transfer_pair"), ("InTotals", "in_totals"), ("HasNote", "has_note"),
               ("Note", "note"), ("SourceFile", "source_file"), ("SourceRow", "source_row")]
BOOLS = {"is_transfer", "is_plug", "in_totals", "has_note"}


def _csv_line(row) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerow(row)
    return buf.getvalue()


def _headers(name: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"}


class _Pipe:
    """Write-only sink for ZipFile (no seek/tell, so it emits streaming entries); drain() hands over what was written so far."""
    def __init__(self): self.parts: list[bytes] = []
    def write(self, b): self.parts.append(bytes(b)); return len(b)
    def flush(self): pass
    def drain(self) -> bytes:
        out, self.parts = b"".join(self.parts), []
        return out


def _zip_stream(entries):
    """entries yields (name, iterator of bytes chunks); yields the zip's bytes incrementally."""
    pipe = _Pipe()
    with zipfile.ZipFile(pipe, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, chunks in entries:
            with zf.open(zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0)), "w") as w:
                for c in chunks:
                    w.write(c)
                    if (d := pipe.drain()): yield d
            if (d := pipe.drain()): yield d
    if (d := pipe.drain()): yield d


def _ledger_rows():
    yield _csv_line([h for h, _ in LEDGER_COLS])
    cols = ", ".join(c for _, c in LEDGER_COLS)
    with db.pool.connection() as conn, conn.cursor(name="export_ledger", row_factory=tuple_row) as cur:
        cur.itersize = 2000
        cur.execute(f"SELECT {cols} FROM ledger ORDER BY budget_month, date, source_file, source_row")
        for r in cur:
            yield _csv_line(["" if v is None else ("1" if v is True else "0" if v is False else v) for v in r])


@router.get("/export/ledger.csv")
def export_ledger(user=Depends(auth.require_user)):
    return StreamingResponse(_ledger_rows(), media_type="text/csv; charset=utf-8", headers=_headers("ledger.csv"))


def _dataset_entries():
    with db.pool.connection() as conn:
        names = [r["name"] for r in conn.execute("SELECT name FROM datasets ORDER BY name").fetchall()]
        for n in names:
            row = conn.execute("SELECT rows FROM datasets WHERE name=%s", (n,)).fetchone()
            if row: yield f"{n}.json", iter([json.dumps(row["rows"], indent=1).encode()])


@router.get("/export/datasets.zip")
def export_datasets(user=Depends(auth.require_user)):
    return StreamingResponse(_zip_stream(_dataset_entries()), media_type="application/zip", headers=_headers("datasets.zip"))


def _source_chunks(conn, f):
    """The raw export rebuilt in EveryDollar's shape (same columns and quoting the pipeline reads), a batch of rows at a time."""
    if f["kind"] == "transactions":
        yield _csv_line(["Group", "Item", "Type", "Date", "Merchant", "Account", "Amount", "Note"]).encode()
        sql = "SELECT grp,item,type,to_char(date,'MM/DD/YYYY') d,merchant,account,amount,note FROM transactions WHERE source_file_id=%s ORDER BY row_no"
        fmt = lambda r: [r["grp"], r["item"], r["type"], r["d"], r["merchant"], r["account"], f"{r['amount']:.2f}", r["note"]]
    else:
        yield _csv_line(["Group", "Item", "Planned", "Spent", "Remaining"]).encode()
        sql = "SELECT grp,item,planned,spent,remaining FROM budget_items WHERE source_file_id=%s ORDER BY id"
        fmt = lambda r: [r["grp"], r["item"], f"{r['planned']:.2f}", f"{r['spent']:.2f}", f"{r['remaining']:.2f}"]
    with conn.cursor(name=f"export_src_{f['id']}") as cur:
        cur.itersize = 2000
        cur.execute(sql, (f["id"],))
        batch = []
        for r in cur:
            batch.append(_csv_line(fmt(r)))
            if len(batch) >= 500: yield "".join(batch).encode(); batch = []
        if batch: yield "".join(batch).encode()


def _source_entries():
    with db.pool.connection() as conn:
        files = conn.execute("SELECT id, kind, budget_month, filename, content IS NOT NULL AS mapped FROM source_files ORDER BY budget_month, kind").fetchall()
        for f in files:
            if f["mapped"]:   # a mapped source (#96): the file exactly as uploaded, under its month (its name need not carry one)
                row = conn.execute("SELECT content FROM source_files WHERE id=%s", (f["id"],)).fetchone()
                yield f"{f['budget_month']}/{f['filename']}", iter([bytes(row["content"])])
            else:
                yield f["filename"], _source_chunks(conn, f)


@router.get("/export/sources.zip")
def export_sources(user=Depends(auth.require_user)):
    return StreamingResponse(_zip_stream(_source_entries()), media_type="application/zip", headers=_headers("sources.zip"))


@router.post("/import/scan")
def scan_import_dir(request: Request, user=Depends(auth.require_editor)):
    if not settings.import_dir:
        flash(request, "IMPORT_DIR is not set, so there is no import directory to scan.", "bad")
    elif not Path(settings.import_dir).is_dir():
        flash(request, f"Import directory {settings.import_dir} does not exist or is not readable.", "bad")
    else:
        with db.pool.connection() as conn:
            try:
                res = import_dir(conn, Path(settings.import_dir), uploaded_by=user.get("email", ""))
                flash(request, f"Scanned the import directory: {res['loaded']} new or changed file(s) loaded, {res['skipped']} unchanged." + (f" Rebuilt: {res['rebuild']}." if res.get("rebuild") else ""))
                for x in res.get("failed", []):
                    flash(request, f"Not loaded: {x['file']}: {x['error']}", "bad")
            except BuildBusy:
                conn.rollback(); flash(request, "New files were stored; another rebuild is running and rebuilds again when it sees them.")
            except ImportRebuildFailed as e:
                conn.rollback(); flash(request, str(e), "bad")
            except Exception as e:  # noqa: BLE001
                conn.rollback(); flash(request, f"Scan failed: {str(e)[:800]}", "bad")
    return RedirectResponse("/upload", status_code=303)
