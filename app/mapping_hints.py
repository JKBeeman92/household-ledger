"""Hints for the item mapping (issues #105 and #106). Pure functions over mapping rows and rules; nothing here writes.

- base_name: an item's name with the suffixes budget tools add from year to year stripped ("Second Breakfast Fund 2" -> "Second Breakfast").
- suggest: the CanonicalItem (and its category and flow) an existing mapping row already gives an item's base name.
- families: mapped items that share a base name but map to different CanonicalItems, i.e. one envelope split across charts.
- draft: a mapping row for each unmapped item, from its budget group; never a row for an item that already has one.

Both patterns are rules (item_name_suffixes, transfer_item_names), so a household whose tool names things differently can change them."""
from __future__ import annotations
import re
from collections import Counter

from pipeline.run_pipeline import norm

# EveryDollar's habits: a year's copy of an envelope gets " 2", " 3" ..., and savings envelopes drift between "X", "X Fund" and "X Goal".
DEFAULT_SUFFIXES = [r"\s+\d+", r"\s+fund", r"\s+goal"]
# Whole item names that are moves between your own envelopes. An income-group item named "Fund Transfer" otherwise counts as income.
DEFAULT_TRANSFER_ITEMS = r"(?:fund transfers?|transfer funds?|.*\brebalanc(?:e|ing)|transfer(?:ring)? (?:to|from)\b.*)"


def _suffix_rx(rules: dict | None) -> re.Pattern:
    pats = (rules or {}).get("item_name_suffixes") or DEFAULT_SUFFIXES
    return re.compile("(?:" + "|".join(f"(?:{p})" for p in pats) + r")\s*$", re.I)


def _transfer_rx(rules: dict | None) -> re.Pattern:
    return re.compile(r"^(?:" + ((rules or {}).get("transfer_item_names") or DEFAULT_TRANSFER_ITEMS) + r")$", re.I)


def base_name(item: str, rules: dict | None = None) -> str:
    """Strip trailing suffixes until none is left; never to an empty name."""
    rx, name = _suffix_rx(rules), norm(item)
    while True:
        cut = rx.sub("", name).strip()
        if not cut or cut == name:
            return name
        name = cut


def suggest(item: str, mapping: list[dict], rules: dict | None = None) -> dict | None:
    """The mapping an item would most likely share, judged by its base name. `mapping` rows have item, canonical_item, category, flow.
    Prefers a row whose CanonicalItem is the base name, then one whose Item is, then any row with the same base name.
    None when nothing matches, or when the item already has a row."""
    item = norm(item)
    if not item or any(r["item"] == item for r in mapping):
        return None
    base = base_name(item, rules).lower()
    tiers = ([r for r in mapping if r["canonical_item"].lower() == base], [r for r in mapping if r["item"].lower() == base],
             [r for r in mapping if base_name(r["item"], rules).lower() == base])
    for rows in tiers:
        if rows:
            # the canonical name most of the matching rows use; ties go to the earliest row
            top, _ = Counter(r["canonical_item"] for r in rows).most_common(1)[0]
            r = next(r for r in rows if r["canonical_item"] == top)
            return {"canonical_item": r["canonical_item"], "category": r["category"], "flow": r["flow"], "like": r["item"]}
    return None


def families(mapping: list[dict], rules: dict | None = None) -> list[dict]:
    """Groups of mapped items whose names are equal once the suffixes are stripped but which map to more than one CanonicalItem.
    Each group: base, items [{item, canonical_item}], canonicals (sorted), and the suggested single name: the base name when a row
    already uses it as its CanonicalItem, otherwise the CanonicalItem most of the items use (ties: the shortest, then alphabetical)."""
    by: dict[str, list[dict]] = {}
    for r in mapping:
        if r.get("flow") == "exclude":   # pass-through money is left out on purpose; folding it into an envelope would undo that
            continue
        by.setdefault(base_name(r["item"], rules).lower(), []).append(r)
    out = []
    for rows in by.values():
        canon = Counter(r["canonical_item"] for r in rows)
        if len(canon) < 2:
            continue
        base = base_name(rows[0]["item"], rules)
        exact = next((c for c in canon if c.lower() == base.lower()), None)
        pick = exact or sorted(canon, key=lambda c: (-canon[c], len(c), c))[0]
        out.append({"base": base, "suggested": pick, "canonicals": sorted(canon),
                    "items": sorted(({"item": r["item"], "canonical_item": r["canonical_item"]} for r in rows), key=lambda r: r["item"])})
    return sorted(out, key=lambda f: f["base"].lower())


def flow_for(item: str, group: str, rules: dict | None = None) -> str:
    """The draft Flow: a fund move by name wins over its group (Fund Transfer sits in Income), then income, debt, else expense."""
    if _transfer_rx(rules).match(norm(item)):
        return "transfer"
    g = norm(group).lower()
    if g == "income" or g.startswith("income "):
        return "income"
    if g == "debt":
        return "debt"
    return "expense"


def draft(unmapped: list[dict], mapping: list[dict], rules: dict | None = None) -> list[dict]:
    """A mapping row for each unmapped item ({item, group}). An item whose base name an existing row already maps copies that row's
    CanonicalItem, Category and Flow (the household has decided it once); otherwise CanonicalItem is the base name, Category the budget
    group and Flow from flow_for. Items drafted together share a base name's CanonicalItem. Note marks every row as drafted."""
    have = {r["item"] for r in mapping}
    out, seen, canon_of = [], set(), {}
    for u in unmapped:
        item = norm(u["item"])
        if not item or item in have or item in seen:
            continue
        seen.add(item)
        group = norm(u.get("group") or "") or "Uncategorised"
        like = suggest(item, mapping, rules)
        if like:
            out.append({"item": item, "canonical_item": like["canonical_item"], "category": like["category"], "flow": like["flow"],
                        "note": f"drafted: like {like['like']}"})
            continue
        flow = flow_for(item, group, rules)
        base = base_name(item, rules)
        # a fund move keeps its own name; otherwise the base name, spelled as the first drafted item that had it
        canon = item if flow == "transfer" else canon_of.setdefault(base.lower(), base)
        out.append({"item": item, "canonical_item": canon, "category": group, "flow": flow, "note": f"drafted from group {group}"})
    return out


def guess(item: str, group: str, rules: dict | None = None) -> dict:
    """The one-item form of draft, for the build log's UNMAPPED form."""
    flow = flow_for(item, group, rules)
    return {"canonical_guess": norm(item) if flow == "transfer" else base_name(item, rules), "flow_guess": flow}
