"""The item mapping in the database (issue #133): every write goes through here.

The mapping is three tables: `categories(name)`, `canonical_items(name, category)` and `item_mapping(item, canonical_item, flow, note)`;
the view `mapping_rows` joins them back into the five columns every reader, the CSV and the pipeline use (Item, CanonicalItem,
Category, Flow, Note). A canonical item has one category, so the category of a row follows from its canonical item.

`write_rows` keeps the three tables consistent: it creates categories and canonical items as needed, refuses rows that would put one
canonical item in two categories, and afterwards removes canonical items and categories no row uses any more (`prune`), so the
tables hold exactly what the five-column CSV can say and a settings.zip round trip is exact.

Not a standalone script: DB code for the app and pipeline/run_pipeline.py (the scripts beside it read item_mapping.csv only)."""
from __future__ import annotations
import psycopg
from psycopg.rows import dict_row

UNMAPPED = "UNMAPPED"   # a build finding (an item with no row), never a category anyone chose: refused as a category


class MappingError(ValueError):
    """A set of rows the mapping cannot take; the message names each problem (and its CSV line when there is one)."""


def rows(conn: psycopg.Connection, order: str = "category, canonical_item, item") -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT item, canonical_item, category, flow, note FROM mapping_rows ORDER BY {order}")
        return cur.fetchall()


def categories_of(conn: psycopg.Connection) -> dict[str, str]:
    """{canonical item: its category}."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT name, category FROM canonical_items")
        return {r["name"]: r["category"] for r in cur.fetchall()}


def conflicts(new: list[dict], current: dict[str, str] | None = None, lines: list[int] | None = None) -> list[str]:
    """Problems with giving these rows' canonical items their categories: one canonical item with two categories among the rows, a row
    whose category differs from the canonical item's category in `current` (when given), or the reserved UNMAPPED. A row with an empty
    category takes its canonical item's category from `current` and is a problem only when there is none."""
    seen: dict[str, tuple[str, int | None]] = {}
    out = []
    for i, r in enumerate(new):
        where = f"line {lines[i]}: " if lines else ""
        canon, cat = r["canonical_item"], r.get("category") or ""
        if cat.upper() == UNMAPPED:
            out.append(f"{where}{UNMAPPED} is not a category (an item with no row is listed as unmapped): choose a real one for {r['item']}")
            continue
        if not cat:
            if not current or canon not in current:
                out.append(f"{where}{canon} is a new canonical item, so it needs a category")
            continue
        if canon in seen and seen[canon][0] != cat:
            first = f"line {seen[canon][1]}" if seen[canon][1] else "another row"
            out.append(f"{where}{canon} is given category {cat}, but {first} gives it {seen[canon][0]}: a canonical item has one category")
        elif current and canon in current and current[canon] != cat:
            out.append(f"{where}{canon} is in {current[canon]}, not {cat}: leave its category as {current[canon]}, or move {canon} to {cat} "
                       "on Settings → Item mapping → Canonical items")
        seen.setdefault(canon, (cat, lines[i] if lines else None))
    return out


def write_rows(conn: psycopg.Connection, new: list[dict], *, replace: bool = False, lines: list[int] | None = None) -> None:
    """Upsert mapping rows ({item, canonical_item, category, flow, note}). With replace=True the rows are the whole mapping (the bulk CSV):
    items not among them are deleted and a canonical item takes the category the rows give it, which may move it. Otherwise an existing
    canonical item keeps its category and a row naming another one is refused (moving is the Canonical items panel's job).
    Raises MappingError, before writing anything, on any problem."""
    current = categories_of(conn)
    problems = conflicts(new, None if replace else current, lines)
    if replace:   # the CSV is the whole mapping: blank categories are not filled from what it replaces
        problems = [p for p in problems if "needs a category" not in p] + \
                   [(f"line {lines[i]}: " if lines else "") + f"category required on: {r['item']}" for i, r in enumerate(new) if not r.get("category")]
    if problems:
        raise MappingError(f"{len(problems)} problem{'s' * (len(problems) != 1)}: " + "; ".join(problems[:8])
                           + (f"; and {len(problems) - 8} more" if len(problems) > 8 else ""))
    want = {}
    for r in new:
        want.setdefault(r["canonical_item"], r.get("category") or current[r["canonical_item"]])
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO categories (name) VALUES (%s) ON CONFLICT DO NOTHING", [(c,) for c in sorted(set(want.values()))])
        cur.executemany("INSERT INTO canonical_items (name, category) VALUES (%s, %s) ON CONFLICT (name) DO UPDATE SET category = EXCLUDED.category",
                        sorted(want.items()))
        cur.executemany("INSERT INTO item_mapping (item, canonical_item, flow, note) VALUES (%s, %s, %s, %s) ON CONFLICT (item) DO UPDATE SET "
                        "canonical_item = EXCLUDED.canonical_item, flow = EXCLUDED.flow, note = EXCLUDED.note, updated_at = now()",
                        [(r["item"], r["canonical_item"], r["flow"], r.get("note") or "") for r in new])
        if replace:
            cur.execute("DELETE FROM item_mapping WHERE item <> ALL(%s)", ([r["item"] for r in new],))
    prune(conn)


def delete_items(conn: psycopg.Connection, items: list[str]) -> int:
    n = conn.execute("DELETE FROM item_mapping WHERE item = ANY(%s)", (items,)).rowcount
    prune(conn)
    return n


def clear(conn: psycopg.Connection) -> None:
    conn.execute("DELETE FROM item_mapping"); prune(conn)


def prune(conn: psycopg.Connection) -> None:
    """Canonical items no row uses, then categories no canonical item uses, leave: the tables say what the mapping says."""
    conn.execute("DELETE FROM canonical_items c WHERE NOT EXISTS (SELECT 1 FROM item_mapping m WHERE m.canonical_item = c.name)")
    conn.execute("DELETE FROM categories g WHERE NOT EXISTS (SELECT 1 FROM canonical_items c WHERE c.category = g.name)")
