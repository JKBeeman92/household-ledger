"""Categories and canonical items defined once (issue #133): the migration, the store's consistency rules, renames and moves with the
rules that name them, the five-column CSV everywhere, the Settings panels, the CLI and the review notes."""
from __future__ import annotations
import io, json, shutil, subprocess, sys, zipfile

import pytest

from conftest import GOLDEN, ROOT, upload_files
from test_migrations import scratch, q   # noqa: F401  (fixture)
from app import admin, db, renames
from pipeline import mapping_store

CONFLICT = [("Bag End Rent", "Rent", "Housing", "expense"), ("Bywater Rent", "Rent", "Housing", "expense"),
            ("Hill Rent", "Rent", "Housing", "expense"), ("Shire Rent", "Rent", "Housing", "expense"),
            ("Rent Late Fee", "Rent", "Bills", "expense"), ("Pipe-weed", "Pipe-weed", "Fun", "expense"),
            ("Old Leaf", "Pipe-weed", "Bills", "expense"), ("Toby Leaf", "Pipe-weed", "Fun", "expense"),
            ("Mystery", "Mystery", "UNMAPPED", "expense"), ("Paycheck", "Paycheck", "Income", "income")]


def mig_dir(tmp_path, upto: bool):
    d = tmp_path / ("before" if upto else "all"); d.mkdir()
    for p in sorted(db.MIGRATIONS.glob("*.sql")):
        if not upto or p.name < "0133":
            shutil.copy(p, d / p.name)
    return d


def test_migration_keeps_the_majority_category_and_notes_what_it_dropped(scratch, tmp_path):  # noqa: F811
    db.migrate(scratch, mig_dir(tmp_path, True))
    import psycopg
    with psycopg.connect(scratch, autocommit=True) as c:
        for r in CONFLICT:
            c.execute("INSERT INTO item_mapping (item, canonical_item, category, flow) VALUES (%s, %s, %s, %s)", r)
        preview = c.execute((ROOT / "db" / "preview" / "0133_mapping_preview.sql").read_text()).fetchall()
    assert preview == [("leaves the mapping", "Mystery", "its category is the placeholder UNMAPPED; it is listed as unmapped until you map it"),
                       ("category conflict", "Pipe-weed", "Fun (2 rows) kept; Bills (1 row) dropped"),
                       ("category conflict", "Rent", "Housing (4 rows) kept; Bills (1 row) dropped")]
    assert db.migrate(scratch) == [p.name for p in sorted(db.MIGRATIONS.glob("*.sql")) if p.name >= "0133"]
    assert q(scratch, "SELECT name, category FROM canonical_items ORDER BY name") == [("Paycheck", "Income"), ("Pipe-weed", "Fun"), ("Rent", "Housing")]
    assert q(scratch, "SELECT name FROM categories ORDER BY name") == [("Fun",), ("Housing",), ("Income",)]   # Bills: no canonical item kept it
    assert q(scratch, "SELECT category FROM mapping_rows WHERE item='Rent Late Fee'") == [("Housing",)]
    assert q(scratch, "SELECT count(*) FROM item_mapping WHERE item='Mystery'") == [(0,)]
    notes = [r[0] for r in q(scratch, "SELECT note FROM mapping_migration_notes ORDER BY id")]
    assert notes[0].startswith("Mystery: its category was the placeholder UNMAPPED")
    assert notes[1:] == ["Pipe-weed: Fun (2 rows) kept; Bills (1 row) dropped", "Rent: Housing (4 rows) kept; Bills (1 row) dropped"]
    assert "category" not in {r[0] for r in q(scratch, "SELECT column_name FROM information_schema.columns WHERE table_name='item_mapping'")}


LEGACY_ODD = [("Rent", "Rent", "Housing", "expense"), ("Hill Rent", "Rent ", "Housing", "expense"), ("Lamp Oil", "Lamp\u00a0Oil", "Home  Goods", "expense"),
              ("Blank Cat", "Blank Cat", "", "expense"), ("Shy", "Shy", "Unmapped", "expense"), ("Shyer", "Shyer", " unmapped ", "expense"),
              ("No Canon", "", "Food", "expense"), ("Paycheck", "Paycheck", "Income", "income")]


def test_migration_tidies_odd_0_4_data_instead_of_failing_and_the_preview_says_so(scratch, tmp_path):  # noqa: F811
    db.migrate(scratch, mig_dir(tmp_path, True))
    import psycopg
    with psycopg.connect(scratch, autocommit=True) as c:
        for r in LEGACY_ODD:
            c.execute("INSERT INTO item_mapping (item, canonical_item, category, flow) VALUES (%s, %s, %s, %s)", r)
        preview = c.execute((ROOT / "db" / "preview" / "0133_mapping_preview.sql").read_text()).fetchall()
    db.migrate(scratch)   # must not fail at start-up
    assert q(scratch, "SELECT item, canonical_item, category FROM mapping_rows ORDER BY item") == [
        ("Hill Rent", "Rent", "Housing"), ("Lamp Oil", "Lamp Oil", "Home Goods"), ("No Canon", "No Canon", "Food"),
        ("Paycheck", "Paycheck", "Income"), ("Rent", "Rent", "Housing")]
    notes = [r[0] for r in q(scratch, "SELECT note FROM mapping_migration_notes ORDER BY id")]
    assert [n.split(":")[0] for n in notes] == ["Hill Rent", "Lamp Oil", "Blank Cat", "Shy", "Shyer", "No Canon"]
    assert [(r[0], r[1]) for r in preview] == [("spacing tidied", "Hill Rent"), ("spacing tidied", "Lamp Oil"), ("leaves the mapping", "Blank Cat"),
                                               ("leaves the mapping", "Shy"), ("leaves the mapping", "Shyer"), ("canonical item filled in", "No Canon")]


def test_a_0_4_shaped_seed_still_starts_an_install_and_notes_what_changed(client, reset_db, tmp_path):
    from pipeline.run_pipeline import seed_reference_tables
    (tmp_path / "item_mapping.csv").write_text(csv_body([("A", "Rent", "Housing", "expense"), ("B", "Rent", "Housing", "expense"),
                                                        ("C", "Rent", "Bills", "expense"), ("D", "D", "UNMAPPED", "expense")]))
    with db.pool.connection() as conn:
        mapping_store.clear(conn); conn.execute("DELETE FROM mapping_migration_notes")
        seed_reference_tables(conn, tmp_path); conn.commit()
        assert [(r["item"], r["category"]) for r in mapping_store.rows(conn, "item")] == [("A", "Housing"), ("B", "Housing"), ("C", "Housing")]
        notes = [r["note"] for r in conn.execute("SELECT note FROM mapping_migration_notes ORDER BY id").fetchall()]
    assert notes[0].startswith("D: its category was the placeholder UNMAPPED") and notes[1] == "Rent: Housing (2 rows) kept; Bills (1 row) dropped"


def test_a_canonical_rename_says_when_a_prefix_rule_stops_matching():
    w = renames.prefix_warnings({"verify_transfer_prefixes": ["Slush"], "payer_prefix_by_person": {"Rosie": ["Rosie "]}}, "Slush Fund", "Rosie Spare")
    assert w == ['verify_transfer_prefixes "Slush" no longer matches Rosie Spare', 'payer_prefix_by_person Rosie "Rosie " now matches Rosie Spare']


def test_migration_of_a_clean_mapping_changes_nothing_but_the_shape(scratch, tmp_path):  # noqa: F811
    db.migrate(scratch, mig_dir(tmp_path, True))
    import psycopg, csv
    rows = list(csv.DictReader(open(GOLDEN / "item_mapping.csv")))
    with psycopg.connect(scratch, autocommit=True) as c:
        for r in rows:
            c.execute("INSERT INTO item_mapping (item, canonical_item, category, flow, note) VALUES (%s,%s,%s,%s,%s)", (r["Item"], r["CanonicalItem"], r["Category"], r["Flow"], r["Note"]))
    db.migrate(scratch)
    got = q(scratch, "SELECT item, canonical_item, category, flow, note FROM mapping_rows ORDER BY item")
    assert got == sorted((r["Item"], r["CanonicalItem"], r["Category"], r["Flow"], r["Note"]) for r in rows)
    assert q(scratch, "SELECT count(*) FROM mapping_migration_notes") == [(0,)]


def csv_body(rows):
    return "Item,CanonicalItem,Category,Flow,Note\n" + "".join(f"{i},{c},{g},{f},\n" for i, c, g, f in rows)


def test_a_csv_giving_one_canonical_two_categories_is_refused_by_line(client, reset_db):
    with db.pool.connection() as conn:
        before = mapping_store.rows(conn)
    r = client.post("/settings/mapping", data={"body": csv_body([("A", "Rent", "Housing", "expense"), ("B", "Rent", "Bills", "expense"), ("C", "X", "UNMAPPED", "expense")])})
    page = client.get(r.headers["location"]).text
    assert "Not saved" in page and "line 3: Rent is given category Bills, but line 2 gives it Housing" in page and "line 4: UNMAPPED is not a category" in page
    with db.pool.connection() as conn:
        assert mapping_store.rows(conn) == before


def test_row_editor_follows_and_guards_the_canonical_category(client, reset_db):
    r = client.post("/settings/mapping/row", data={"item": "Bag End Rent", "canonical_item": "Rent", "category": "", "flow": "expense", "note": ""}).json()
    assert r["ok"] and r["row"]["category"] == "Housing"   # blank: follows Rent
    r = client.post("/settings/mapping/row", data={"item": "Bag End Rent", "canonical_item": "Rent", "category": "Bills", "flow": "expense", "note": "", "original": "Bag End Rent"}).json()
    assert not r["ok"] and "Rent is in Housing, not Bills" in r["errors"]["category"]
    r = client.post("/settings/mapping/row", data={"item": "Pipe-weed", "canonical_item": "Pipe-weed", "category": "Fun", "flow": "expense", "note": ""}).json()
    assert r["ok"]
    with db.pool.connection() as conn:
        assert mapping_store.categories_of(conn)["Pipe-weed"] == "Fun"
    assert client.post("/settings/mapping/row/delete", data={"original": "Pipe-weed"}).json()["ok"]
    with db.pool.connection() as conn:   # its canonical item and category went with the last row
        assert "Pipe-weed" not in mapping_store.categories_of(conn)
        assert conn.execute("SELECT 1 FROM categories WHERE name='Fun'").fetchone() is None


def figures(client):
    with db.pool.connection() as conn:
        return {r["name"]: r["rows"] for r in conn.execute("SELECT name, rows FROM datasets WHERE name IN ('monthly','yearly','cat_year','recurring','goal_config')").fetchall()}


def test_category_and_canonical_renames_are_one_edit_with_identical_figures(client, reset_db):
    upload_files(client, GOLDEN / "exports")
    before = figures(client)
    assert client.post("/settings/mapping/category/rename", data={"old": "Utilities", "new": "Home Utilities"}).status_code == 303
    assert client.post("/settings/mapping/canonical/rename", data={"old": "Paycheck", "new": "Salary"}).status_code == 303
    with db.pool.connection() as conn:
        rules = db.get_setting(conn, "rules", {})
        assert rules["goal_housing_categories"] == ["Housing", "Home Utilities"] and rules["recurring_categories"] == ["Subscriptions", "Home Utilities", "Insurance"]
        assert rules["paycheck_items"] == ["Salary"] and "Salary" in rules["income_by_person"]["Rosie"]
        assert {r["dimension"]: (r["old"], r["new"]) for r in conn.execute("SELECT dimension, old, new FROM renames").fetchall()} == \
               {"category": ("Utilities", "Home Utilities"), "canonical_item": ("Paycheck", "Salary")}
    client.post("/rebuild")
    after = figures(client)
    swap = lambda o: json.loads(json.dumps(o).replace('"Home Utilities"', '"Utilities"'))
    assert swap(after["monthly"]) == before["monthly"] and swap(after["yearly"]) == before["yearly"]
    srt = lambda rows: sorted(rows, key=lambda r: json.dumps(r, sort_keys=True))   # categories sort by name, so the order moves
    assert srt(swap(after["cat_year"])) == srt(before["cat_year"]) and swap(after["recurring"]) == before["recurring"]
    assert any(r["category"] == "Home Utilities" for r in after["cat_year"])


def test_rename_into_an_existing_name_merges_and_move_changes_category(client, reset_db):
    with db.pool.connection() as conn:
        cats = mapping_store.categories_of(conn)
        a, b = sorted({c for c in cats.values()})[:2]
        renames.rename_category(conn, a, b, "test"); conn.commit()
        assert a not in set(mapping_store.categories_of(conn).values())
        name = next(iter(cats))
        assert renames.move_canonical(conn, name, "Second Breakfast").endswith("to Second Breakfast"); conn.commit()
        assert mapping_store.categories_of(conn)[name] == "Second Breakfast"
        with pytest.raises(ValueError, match="UNMAPPED is not a name"):
            renames.rename_category(conn, b, "unmapped")


def test_renaming_a_name_a_default_rule_holds_writes_the_rule_out():
    rules, changed = renames.rules_after_rename({}, "category", "Insurance", "Cover")
    assert rules == {"recurring_categories": ["Subscriptions", "Utilities", "Cover"], "plan_keep_categories": ["Housing", "Debt", "Cover"]}
    assert changed == ["recurring_categories", "plan_keep_categories"]
    rules, _ = renames.rules_after_rename({"catchall_items": ["Holiday", "Fund"], "payer_items_by_person": {"Sam": ["Holiday"]}}, "canonical_item", "Holiday", "Fund")
    assert rules["catchall_items"] == ["Fund"] and rules["payer_items_by_person"] == {"Sam": ["Fund"]}


def test_five_column_csv_round_trips_through_zip_cli_and_draft(client, reset_db, tmp_path, capsys):
    zf = zipfile.ZipFile(io.BytesIO(client.get("/export/settings.zip").content))
    assert zf.read("item_mapping.csv").decode().splitlines()[0] == "Item,CanonicalItem,Category,Flow,Note"
    assert admin.main(["export-mapping"]) == 0
    out = capsys.readouterr().out
    f = tmp_path / "m.csv"; f.write_text(out)
    assert admin.main(["import-mapping", str(f)]) == 0
    with db.pool.connection() as conn:
        assert "".join(l + "\n" for l in out.splitlines()) == "".join(l + "\n" for l in __import__("app.routes.settings", fromlist=["x"])._mapping_csv(conn).splitlines())


def test_cli_rename_move_and_preview(client, reset_db, capsys):
    assert admin.main(["rename-category", "Food", "Victuals"]) == 0
    assert "Food renamed Victuals" in capsys.readouterr().out
    assert admin.main(["move-canonical", "Groceries", "Pantry"]) == 0
    assert admin.main(["rename-canonical", "Nope", "X"]) == 1
    with db.pool.connection() as conn:
        assert mapping_store.categories_of(conn)["Groceries"] == "Pantry"
        assert conn.execute("SELECT by FROM renames").fetchone()["by"].endswith("(cli)")
    capsys.readouterr()
    assert admin.main(["preview-mapping-migration"]) == 0
    assert "already run" in capsys.readouterr().out


def test_settings_shows_panels_notes_and_mixed_flows(client, reset_db):
    with db.pool.connection() as conn:
        conn.execute("INSERT INTO mapping_migration_notes (note) VALUES ('Rent: Housing (4 rows) kept; Bills (1 row) dropped')")
        mapping_store.write_rows(conn, [{"item": "Rent Refund", "canonical_item": "Rent", "category": "", "flow": "income", "note": ""}]); conn.commit()
    page = client.get("/settings").text
    assert "Rent: Housing (4 rows) kept; Bills (1 row) dropped" in page and 'action="/settings/mapping/category/rename"' in page
    assert "mixes income with expense" in page and 'id="canonical-category"' in page
    client.post("/settings/mapping/notes/dismiss")
    assert "Bills (1 row) dropped" not in client.get("/settings").text


def test_reviews_note_renames_since_they_were_written(client, reset_db):
    with db.pool.connection() as conn:
        conn.execute("INSERT INTO reviews (month, model, sections, prompt, generated_at) VALUES ('2025-01','m',%s,'p','2025-02-01')",
                     (json.dumps([{"order": 1, "heading": "Food", "text": "Food was high."}]),))
        conn.commit()
    client.post("/settings/mapping/category/rename", data={"old": "Food", "new": "Food & Drink"})
    page = client.get("/reviews").text
    assert "Food was high." in page and "Food was renamed Food &amp; Drink on" in page


def test_draft_follows_existing_canonical_categories():
    from app import mapping_hints as mh
    mapping = [{"item": "Holiday", "canonical_item": "Holiday", "category": "Savings", "flow": "expense", "note": ""}]
    d = mh.draft([{"item": "Holiday Trip", "group": "Fun"}, {"item": "Pipe", "group": "Fun"}, {"item": "Pipe 2", "group": "Smoke"}], mapping)
    by = {r["item"]: r for r in d}
    assert by["Pipe"]["category"] == by["Pipe 2"]["category"] == "Fun"
