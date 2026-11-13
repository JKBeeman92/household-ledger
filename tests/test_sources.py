"""Other budget tools' files through a column mapping (issue #96). The second sample source, "Bree Bank", is the golden January
export rewritten the way a European bank would: semicolons, day-first dates, 1.234,56 amounts, separate debit and credit
columns, and spending shown as positive in the budget file. Read through its profile it must give exactly the EveryDollar rows."""
from __future__ import annotations
import csv, io, json, zipfile
from decimal import Decimal
from pathlib import Path

from conftest import GOLDEN, ROOT
from test_pipeline_golden import run_scripts

from pipeline import sources as src, filenames

BREE = ROOT / "tests" / "fixtures" / "bree"
TXN, BUD = BREE / "bree-bank-2025-01-transactions.csv", BREE / "bree-bank-2025-01-budget.csv"
ED = GOLDEN / "exports"


def ed_rows(month_mm: str, kind: str) -> list[dict]:
    """An EveryDollar export's rows in the internal format: the same rows, dates as YYYY-MM-DD (#161)."""
    name = f"c0ffee-{month_mm}-2025-EveryDollar-{'Transactions' if kind == 'transactions' else 'BudgetItems'}.csv"
    rows = list(csv.DictReader(open(ED / name, encoding="utf-8-sig")))
    return [{**r, "Date": "-".join(filenames.iso_date(r["Date"]))} if "Date" in r else r for r in rows]


def _eu(d: Decimal) -> str:
    return f"{abs(d):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def to_bree(month_mm: str) -> tuple[bytes, bytes]:
    """Any golden month in Bree Bank's layout (how tests/fixtures/bree was made)."""
    t, b = io.StringIO(), io.StringIO()
    w = csv.writer(t, delimiter=";", lineterminator="\n"); w.writerow(["Posted", "Description", "Category", "Subcategory", "Debit", "Credit", "Memo"])
    for r in ed_rows(month_mm, "transactions"):
        a = Decimal(r["Amount"]); yy, mm, dd = filenames.iso_date(r["Date"])
        w.writerow([f"{dd}/{mm}/{yy}", r["Merchant"], r["Group"], r["Item"], _eu(a) if a < 0 else "", _eu(a) if a > 0 else "", r.get("Note", "")])
    w = csv.writer(b, delimiter=";", lineterminator="\n"); w.writerow(["Category", "Subcategory", "Budgeted", "Actual"])
    for r in ed_rows(month_mm, "budget_items"):
        s, inc = Decimal(r["Spent"]), r["Group"] == "Income"
        w.writerow([r["Group"], r["Item"], _eu(Decimal(r["Planned"])), ("-" if (s < 0 if inc else s > 0) else "") + _eu(s)])
    return t.getvalue().encode(), b.getvalue().encode()


def bree_profile() -> dict:
    return {"name": "Bree Bank", "transactions": src.propose("transactions", TXN.read_bytes()), "budget": src.propose("budget_items", BUD.read_bytes())}


# ---------------------------------------------------------------- the engine, no database
def test_fixture_is_the_generated_january():
    t, b = to_bree("01")
    assert TXN.read_bytes() == t and BUD.read_bytes() == b


def test_proposal_reads_the_layout_from_headers_and_values():
    p = bree_profile()
    t, b = p["transactions"], p["budget"]
    assert t["columns"] == {"date": "Posted", "amount": None, "debit": "Debit", "credit": "Credit", "type": None, "merchant": "Description",
                            "account": None, "note": "Memo", "group": "Category", "item": "Subcategory"}
    assert (t["date_format"], t["decimal"], t["thousands"], t["amount_mode"]) == ("DD/MM/YYYY", ",", ".", "split")
    assert b["columns"] == {"group": "Category", "item": "Subcategory", "planned": "Budgeted", "spent": "Actual"}
    assert (b["spent_sign"], b["decimal"]) == ("positive", ",")


def test_applied_profile_gives_exactly_the_everydollar_rows():
    p = bree_profile()
    keys = ["Group", "Item", "Type", "Date", "Merchant", "Amount", "Note"]
    assert [{k: r[k] for k in keys} for r in src.apply("transactions", TXN.read_bytes(), p)] == [{k: r[k] for k in keys} for r in ed_rows("01", "transactions")]
    assert src.apply("budget_items", BUD.read_bytes(), p) == [{k: r[k] for k in src.ED_BUDGET} for r in ed_rows("01", "budget_items")]


def test_profile_errors_name_what_is_wrong():
    p = bree_profile()
    bad = json.loads(json.dumps(p)); bad["transactions"]["date_format"] = "MM/DD/YYYY"
    try:
        src.apply("transactions", TXN.read_bytes(), bad); raise AssertionError("expected a ProfileError")
    except src.ProfileError as e:
        assert "row 3" in str(e) and "17/01/2025" in str(e)
    missing = json.loads(json.dumps(p)); missing["budget"]["columns"]["spent"] = None
    try:
        src.validate_profile(missing); raise AssertionError("expected a ProfileError")
    except src.ProfileError as e:
        assert "spent" in str(e)


def test_signed_and_type_layouts():
    signed = b"Date,Payee,Group,Item,Amount\n2025-03-02,Bywater Market,Food,Groceries,-12.50\n2025-03-05,Hobbiton Mill,Income,Paycheck,100.00\n"
    p = {"transactions": src.propose("transactions", signed)}
    assert p["transactions"]["amount_mode"] == "signed" and p["transactions"]["date_format"] == "YYYY-MM-DD"
    rows = src.apply("transactions", signed, p)
    assert [(r["Date"], r["Amount"], r["Type"]) for r in rows] == [("2025-03-02", "-12.50", "Expense"), ("2025-03-05", "100.00", "Income")]
    typed = b"Date,Payee,Group,Item,Amount,Kind\n03/02/2025,Bywater Market,Food,Groceries,12.50,debit\n03/05/2025,Hobbiton Mill,Income,Paycheck,100,credit\n"
    t = src.propose("transactions", typed)
    assert t["amount_mode"] == "type"
    assert [r["Amount"] for r in src.apply("transactions", typed, {"transactions": t})] == ["-12.50", "100.00"]


def test_standalone_normalize_feeds_the_unchanged_pipeline_scripts(tmp_path):
    (tmp_path / "p.json").write_text(json.dumps(bree_profile()))
    out = tmp_path / "out"
    assert src.main(["normalize", "--profile", str(tmp_path / "p.json"), "--month", "2025-01", "--transactions", str(TXN), "--budget", str(BUD), "--out", str(out)]) == 0
    wd = tmp_path / "wd"; (wd / "txns").mkdir(parents=True); (wd / "summaries").mkdir()
    for f in out.iterdir():
        assert filenames.parse(f.name)[1] == "2025-01" and not f.name.startswith(("c0ffee", "EveryDollar"))   # internal-format names
        (wd / ("txns" if filenames.parse(f.name)[0] == "transactions" else "summaries") / f.name).write_bytes(f.read_bytes())
    for n in ("item_mapping.csv", "cpi_u.csv", "rules.json", "events.json"):
        (wd / n).write_bytes((GOLDEN / n).read_bytes())
    run_scripts(wd)
    assert json.loads((wd / "dash_data" / "reconciliation.json").read_text()) == []
    assert len(list(csv.DictReader(open(wd / "ledger.csv")))) > 0


# ---------------------------------------------------------------- through the app
def form_for(profile: dict, pending: list[dict], month: str = "2025-01", name: str = "Bree Bank") -> dict:
    f = {"month": month, "profile_name": name}
    for p in pending:
        f[f"kind_{p['id']}"] = "budget_items" if "budget" in p["filename"] else "transactions"
    t, b = profile["transactions"], profile["budget"]
    for k, v in t["columns"].items(): f[f"t_col_{k}"] = v or ""
    for k, v in b["columns"].items(): f[f"b_col_{k}"] = v or ""
    f.update(t_date_format=t["date_format"], t_amount_mode=t["amount_mode"], t_expense_sign=t["expense_sign"], t_decimal=t["decimal"],
             t_thousands=t["thousands"], t_income_values=", ".join(t["income_values"]), t_income_groups="Income",
             b_spent_sign=b["spent_sign"], b_decimal=b["decimal"], b_thousands=b["thousands"], b_income_groups="Income")
    return f


def pending_of(batch: str) -> list[dict]:
    from app import db
    with db.pool.connection() as conn:
        return conn.execute("SELECT id, filename FROM upload_pending WHERE batch=%s ORDER BY id", (batch,)).fetchall()


def upload_pair(client, t: bytes, b: bytes, tname="bree-bank-2025-01-transactions.csv", bname="bree-bank-2025-01-budget.csv") -> str:
    r = client.post("/upload", files=[("files", (tname, t, "text/csv")), ("files", (bname, b, "text/csv"))])
    assert r.status_code == 303 and r.headers["location"].startswith("/upload/map/"), r.headers.get("location")
    return r.headers["location"].rsplit("/", 1)[1]


def ledger_rows():
    from app import db
    with db.pool.connection() as conn:
        return conn.execute("SELECT budget_month, date, grp, item, canonical_item, flow, merchant, amount, in_totals FROM ledger "
                            "ORDER BY budget_month, date, item, merchant, amount").fetchall()


def test_mapped_pair_builds_the_same_ledger_as_the_everydollar_pair(client, reset_db):
    from conftest import upload_files
    import shutil, tempfile
    with tempfile.TemporaryDirectory() as d:
        for p in ED.glob("c0ffee-01-2025-*.csv"): shutil.copy(p, d)
        upload_files(client, Path(d))
    want = ledger_rows()
    assert want
    reset_db()
    batch = upload_pair(client, TXN.read_bytes(), BUD.read_bytes())
    page = client.get(f"/upload/map/{batch}").text
    assert "Map your columns" in page and 'value="2025-01"' in page and "<option value=\"Posted\" selected>" in page
    r = client.post(f"/upload/map/{batch}", data=form_for(bree_profile(), pending_of(batch)))
    assert r.status_code == 303 and r.headers["location"] == "/upload", r.text[:500]
    assert ledger_rows() == want
    assert client.get("/api/status").json()["months"] == 1
    from app import db
    with db.pool.connection() as conn:
        assert conn.execute("SELECT ok FROM build_log ORDER BY id DESC LIMIT 1").fetchone()["ok"] is True
        files = conn.execute("SELECT kind, filename, profile_id IS NOT NULL m, content FROM source_files ORDER BY kind").fetchall()
        assert all(f["m"] for f in files) and bytes(files[1]["content"]) == TXN.read_bytes()
        assert conn.execute("SELECT count(*) n FROM upload_pending").fetchone()["n"] == 0
    z = zipfile.ZipFile(io.BytesIO(client.get("/export/sources.zip").content))
    assert sorted(z.namelist()) == ["2025-01/bree-bank-2025-01-budget.csv", "2025-01/bree-bank-2025-01-transactions.csv", "balances.csv"]
    assert z.read("2025-01/bree-bank-2025-01-transactions.csv") == TXN.read_bytes()


def test_next_month_is_recognised_by_its_headers(client, reset_db):
    batch = upload_pair(client, TXN.read_bytes(), BUD.read_bytes())
    client.post(f"/upload/map/{batch}", data=form_for(bree_profile(), pending_of(batch)))
    t, b = to_bree("02")
    batch = upload_pair(client, t, b, "feb-txns.csv", "feb-budget.csv")
    page = client.get(f"/upload/map/{batch}").text
    assert "which recognises these headers" in page and 'value="Bree Bank"' in page and 'value="2025-02"' in page
    r = client.post(f"/upload/map/{batch}", data=form_for(bree_profile(), pending_of(batch), month="2025-02"))
    assert r.status_code == 303
    assert client.get("/api/status").json()["months"] == 2
    from app import db
    with db.pool.connection() as conn:
        assert conn.execute("SELECT count(*) n FROM source_profiles").fetchone()["n"] == 1


def test_a_bad_mapping_stores_nothing_and_says_why(client, reset_db):
    batch = upload_pair(client, TXN.read_bytes(), BUD.read_bytes())
    f = form_for(bree_profile(), pending_of(batch)); f["t_date_format"] = "MM/DD/YYYY"
    r = client.post(f"/upload/map/{batch}", data=f)
    assert r.status_code == 200 and "17/01/2025" in r.text and "is not a date" in r.text
    assert 'value="MM/DD/YYYY" selected' in r.text or "<option selected>MM/DD/YYYY</option>" in r.text   # the user's choice is kept
    from app import db
    with db.pool.connection() as conn:
        assert conn.execute("SELECT count(*) n FROM source_files").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) n FROM source_profiles").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) n FROM upload_pending").fetchone()["n"] == 2


def test_editing_a_profile_rereads_stored_files_and_a_broken_edit_changes_nothing(client, reset_db):
    from app import db
    batch = upload_pair(client, TXN.read_bytes(), BUD.read_bytes())
    client.post(f"/upload/map/{batch}", data=form_for(bree_profile(), pending_of(batch)))
    with db.pool.connection() as conn:
        pid = conn.execute("SELECT id FROM source_profiles").fetchone()["id"]
    spec = client.get(f"/settings/sources/{pid}.json").json()
    assert spec["name"] == "Bree Bank"
    broken = json.loads(json.dumps(spec)); broken["transactions"]["date_format"] = "MM/DD/YYYY"
    client.post("/settings/sources", data={"body": json.dumps(broken)})
    page = client.get("/settings/sources").text   # the typed profile comes back, with its error under it (DS-FORM-1)
    assert "Not saved: see the profile marked below." in page and 'aria-invalid="true"' in page and 'class="err" id="src-' in page
    with db.pool.connection() as conn:
        assert conn.execute("SELECT spec FROM source_profiles").fetchone()["spec"] == spec
        assert conn.execute("SELECT count(*) n FROM transactions WHERE merchant <> ''").fetchone()["n"] > 0
    fixed = json.loads(json.dumps(spec)); fixed["transactions"]["columns"]["merchant"] = None
    client.post("/settings/sources", data={"body": json.dumps(fixed)})
    assert "Re-read 2 stored files" in client.get("/settings/sources").text
    with db.pool.connection() as conn:
        assert conn.execute("SELECT count(*) n FROM transactions WHERE merchant <> ''").fetchone()["n"] == 0


def test_everydollar_names_still_skip_the_mapping_step(client, reset_db):
    from conftest import upload_files
    r = upload_files(client, ED)
    assert r.headers["location"] == "/upload"
    assert client.get("/api/status").json()["months"] == 4


def test_more_than_one_pair_of_other_files_is_refused(client, reset_db):
    r = client.post("/upload", files=[("files", (f"x{i}.csv", b"a,b\n1,2\n", "text/csv")) for i in range(3)])
    assert r.headers["location"] == "/upload"
    assert "upload one month" in client.get("/upload").text
