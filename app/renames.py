"""Rename a category or a canonical item, or move a canonical item to another category, in one edit (issue #133).

A rename is one UPDATE: item_mapping and canonical_items reference names with ON UPDATE CASCADE. Renaming to a name that already
exists merges the two. The rules that name what was renamed are updated too (#162), including a rule still at its default, which is
written out with the new name so it keeps meaning the same thing. Every rename is logged in `renames`; stored reviews keep their
text, and the Reviews page notes later renames on them. A move is not a rename and is not logged."""
from __future__ import annotations
import json

from pipeline import mapping_store
from . import db
from .roles import ROLE_DEFAULTS
DEBT_TYPES = mapping_store.DEBT_TYPES

# rules that hold category names, and rules that hold canonical item names (lists, or person -> list)
CATEGORY_RULES = ("goal_giving_categories", "goal_housing_categories", "recurring_categories", "plan_keep_categories", "loan_categories")
CANONICAL_RULES = ("catchall_items", "paycheck_items")
CANONICAL_BY_PERSON = ("income_by_person", "payer_items_by_person")


def _swap(names: list, old: str, new: str) -> list:
    out = []
    for n in names:
        n = new if n == old else n
        if n not in out:
            out.append(n)
    return out


def rules_after_rename(rules: dict, dimension: str, old: str, new: str) -> tuple[dict, list[str]]:
    """The rules with `old` replaced by `new` wherever a rule of that dimension names it, and the keys changed."""
    out, changed = json.loads(json.dumps(rules or {})), []
    for key in (CATEGORY_RULES if dimension == "category" else CANONICAL_RULES):
        cur = out.get(key)
        cur = ROLE_DEFAULTS.get(key, []) if cur is None else cur   # a rule at its default names what the default names
        if old in cur:
            out[key] = _swap(cur, old, new); changed.append(key)
    if dimension == "canonical_item":
        for key in CANONICAL_BY_PERSON:
            per = out.get(key) or {}
            for person, names in per.items():
                if old in names:
                    per[person] = _swap(names, old, new); changed.append(f"{key}.{person}")
    return out, changed


def _check(new: str) -> str:
    from pipeline.run_pipeline import norm
    new = norm(new)
    if not new:
        raise ValueError("give the new name")
    if new.upper() == mapping_store.UNMAPPED:
        raise ValueError(f"{mapping_store.UNMAPPED} is not a name you can use: it marks an item with no row")
    return new


def _log_and_rules(conn, dimension: str, old: str, new: str, by: str) -> list[str]:
    rules, changed = rules_after_rename(db.get_setting(conn, "rules", {}) or {}, dimension, old, new)
    if changed:
        db.put_setting(conn, "rules", rules)
    from . import decisions   # a decision's target names a category or item in JSON no foreign key follows (#155)
    decisions.rewrite_targets(conn, dimension, old, new)
    conn.execute("INSERT INTO renames (dimension, old, new, by) VALUES (%s, %s, %s, %s)", (dimension, old, new, by or ""))
    db.mark_inputs_changed(conn)
    return changed


def rename_category(conn, old: str, new: str, by: str = "") -> str:
    new = _check(new)
    if not conn.execute("SELECT 1 FROM categories WHERE name=%s", (old,)).fetchone():
        raise ValueError(f"there is no category {old}")
    if new == old:
        return f"{old} is already called that"
    merge = conn.execute("SELECT 1 FROM categories WHERE name=%s", (new,)).fetchone() is not None
    if merge:   # into an existing category: its canonical items join it
        conn.execute("UPDATE canonical_items SET category=%s WHERE category=%s", (new, old))
        mapping_store.prune(conn)
    else:
        conn.execute("UPDATE categories SET name=%s WHERE name=%s", (new, old))
    changed = _log_and_rules(conn, "category", old, new, by)
    return (f"{old} {'merged into' if merge else 'renamed'} {new}" + (f"; rules updated: {', '.join(changed)}" if changed else ""))


def rename_canonical(conn, old: str, new: str, by: str = "") -> str:
    new = _check(new)
    cats = mapping_store.categories_of(conn)
    if old not in cats:
        raise ValueError(f"there is no canonical item {old}")
    if new == old:
        return f"{old} is already called that"
    merge = new in cats
    types = mapping_store.debt_types_of(conn)
    clash = merge and types.get(old) and types.get(new) and types[old] != types[new]
    if merge:   # into an existing canonical item: its rows join it, and take its category; its debt type unless it has none (#141)
        conn.execute("UPDATE canonical_items SET debt_type = COALESCE(debt_type, %s) WHERE name=%s", (types.get(old), new))
        conn.execute("UPDATE item_mapping SET canonical_item=%s, updated_at=now() WHERE canonical_item=%s", (new, old))
        mapping_store.prune(conn)
    else:
        conn.execute("UPDATE canonical_items SET name=%s WHERE name=%s", (new, old))
    changed = _log_and_rules(conn, "canonical_item", old, new, by)
    note = f" (now in {cats[new]}, not {cats[old]})" if merge and cats[new] != cats[old] else ""
    note += f" (debt type {types[new]} kept; {old} was {types[old]})" if clash else ""
    warn = prefix_warnings(db.get_setting(conn, "rules", {}) or {}, old, new)
    return (f"{old} {'merged into' if merge else 'renamed'} {new}{note}" + (f"; rules updated: {', '.join(changed)}" if changed else "")
            + (f". Check the rules: {'; '.join(warn)}" if warn else ""))


def prefix_warnings(rules: dict, old: str, new: str) -> list[str]:
    """Rules that match canonical items by prefix (verify_transfer_prefixes, payer_prefix_by_person) are not rewritten by a rename,
    because a prefix names many items. Say where the rename changes what a prefix matches."""
    out = []
    for p in rules.get("verify_transfer_prefixes") or []:
        if old.startswith(p) != new.startswith(p):
            out.append(f"verify_transfer_prefixes \"{p}\" {'no longer matches' if old.startswith(p) else 'now matches'} {new}")
    for person, prefixes in (rules.get("payer_prefix_by_person") or {}).items():
        for p in prefixes:
            if old.startswith(p) != new.startswith(p):
                out.append(f"payer_prefix_by_person {person} \"{p}\" {'no longer matches' if old.startswith(p) else 'now matches'} {new}")
    return out


def move_canonical(conn, name: str, category: str) -> str:
    category = _check(category)
    cats = mapping_store.categories_of(conn)
    if name not in cats:
        raise ValueError(f"there is no canonical item {name}")
    if cats[name] == category:
        return f"{name} is already in {category}"
    conn.execute("INSERT INTO categories (name) VALUES (%s) ON CONFLICT DO NOTHING", (category,))
    conn.execute("UPDATE canonical_items SET category=%s WHERE name=%s", (category, name))
    mapping_store.prune(conn)
    db.mark_inputs_changed(conn)
    return f"{name} moved from {cats[name]} to {category}"


def overview(conn) -> dict:
    """For the Settings panels: each category with its canonical items and row counts, and each canonical item with its category, row
    count and flows; `mixed` flags a canonical item whose rows mix income with expense or debt (almost always a mistake)."""
    rows = mapping_store.rows(conn)
    canon: dict[str, dict] = {}
    for r in rows:
        c = canon.setdefault(r["canonical_item"], {"name": r["canonical_item"], "category": r["category"], "rows": 0, "flows": set(),
                                                    "debt_type": r.get("debt_type")})
        c["rows"] += 1; c["flows"].add(r["flow"])
    for c in canon.values():
        c["mixed"] = "income" in c["flows"] and bool(c["flows"] & {"expense", "debt"})
        c["debt_or_typed"] = "debt" in c["flows"] or bool(c["debt_type"])   # listed under Settings → Item mapping → Debt types
        c["flows"] = sorted(c["flows"])
    cats: dict[str, dict] = {}
    for c in canon.values():
        g = cats.setdefault(c["category"], {"name": c["category"], "canonical": 0, "rows": 0})
        g["canonical"] += 1; g["rows"] += c["rows"]
    return {"categories": sorted(cats.values(), key=lambda g: g["name"].lower()),
            "canonical": sorted(canon.values(), key=lambda c: (c["category"].lower(), c["name"].lower()))}


def notes(conn) -> list[dict]:
    return conn.execute("SELECT id, note FROM mapping_migration_notes WHERE dismissed_at IS NULL ORDER BY id").fetchall()


def renames_after(conn) -> list[dict]:
    return conn.execute("SELECT at, dimension, old, new FROM renames ORDER BY at").fetchall()
