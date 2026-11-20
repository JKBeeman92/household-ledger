"""Chart appearance (issue #132): a colour and a hide switch per name, in one table for every chart coloured by a name. A life-event
kind has a colour only (#235): an event leaves the charts by its own On the chart switch (#140), and the chips above the timeline are
each viewer's filter, so a kind is never hidden here (db/migrations/0235_event_kind_hide_to_on_chart.sql turned the old flags into that).

A dimension is the kind of name a chart is coloured by: a category (the category stack), a catch-all item ("Where it went"), an
employer (Paycheck source), a person (the earner and payer stacks), a debt (the Debts bars), an account type (net worth's stacks)
and a life-event kind (the markers). A name with no row draws exactly as before: its place in the palette. A colour is a palette
slot, `series-1` … `series-8`, which follows the kit in each mode, or a CSS colour, fixed in both. Display only: the pipeline never
reads this table, so a change needs no rebuild, and "hidden" takes a name out of a chart's bars, lines and legend, never out of a
total, a net line, a table or the reconciliation.

The dashboard gets the whole table once, as a JSON block (`for_page`), and dash-shim.js looks names up in it (`dash.colorFor`,
`dash.hidden`). Settings → Chart appearance lists only the names the current data has (`present`); rows whose name is gone are
orphans, listed with Delete. A fixed colour is checked against the kit in force (app/brand_check.py): 3:1 on --surface-200, and
not within ΔE 10 (OKLab) of --success or --danger, which already mean kept and drew down. Either warns, naming the mode; neither
refuses (the household may know its screen better than the check does)."""
from __future__ import annotations
import json, logging, re

from . import brand_check, brand_kit

DIMENSIONS = ("category", "item", "employer", "person", "debt", "account_type", "event_kind")   # db/migrations/0132_appearance.sql
# for Settings and messages: (sub-tab label, singular noun, which chart it colours)
LABELS = {
    "category": ("Categories", "category", "Where it went: the category stack"),
    "item": ("Catch-all items", "catch-all item", "Leaks: the catch-all envelopes"),
    "employer": ("Employers", "employer", "Overview: Paycheck source"),
    "person": ("People", "person", "People: the earner and payer stacks"),
    "debt": ("Debts", "debt", "Position: the Debts timeline"),
    "account_type": ("Account types", "account type", "Position: the stacks under net worth"),
    "event_kind": ("Life-event kinds", "event kind", "the life-event markers on the timelines"),
}
COLOUR_ONLY = ("event_kind",)   # dimensions with a colour and no hide switch (#235); the table's CHECK says the same
HIDE_REFUSED = ("{name}: a life-event kind has no hide switch; switch its events off the chart with On the chart in "
                "Settings → Life events (or \"on_chart\": false in events.json)")
FILTER_FROM = 8   # Settings shows a filter box over a list longer than this (AGENTS.md, "Long lists": one long enough to scan)
SLOTS = tuple(f"series-{i}" for i in range(1, 9))
CONTRAST = brand_check.GRAPHIC   # a chart mark against the sheet it sits on (DS-COLOR-3)
NEAR = 10.0                      # ΔE (OKLab ×100) under which a colour reads as a verdict
MODE_WORDS = {"light": "by day", "dark": "by night"}   # the header's switch calls the modes day and night
_CSS_CHARS = re.compile(r"^[#\w(),.%\s/+-]{1,64}$")   # the value lands in a style property and an SVG fill: nothing that could end one


def check_color(raw) -> tuple[str | None, str | None]:
    """(colour as stored, problem). Blank is "the palette" (None). A slot is `series-1` … `series-8` (`3` and `Series 3` are read
    as `series-3`); anything else must be an opaque colour brand_check can evaluate, so it can also be checked for contrast."""
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, "must be text"
    v = raw.strip()
    if not v:
        return None, None
    if (m := re.fullmatch(r"(?:series[-\s]?)?([1-8])", v, re.I)):
        return f"series-{m.group(1)}", None
    bad = "a theme colour (series-1 to series-8) or a colour code such as #2b6cb0, rgb(43 108 176), hsl(210 60% 43%) or oklch(0.5 0.12 250)"
    if not _CSS_CHARS.match(v):
        return None, bad
    try:
        brand_check.rgb(v)
    except brand_check.Unreadable as e:
        return None, ("translucent: give an opaque colour" if "translucent" in str(e) else bad)
    return v.lower(), None


def _theme_tokens() -> dict[str, dict[str, str]]:
    """The kit in force (BRAND_DIR's theme.css, else the shipped one), as {mode: tokens}; {} when it cannot be read."""
    p = brand_kit.find("theme.css", brand_kit.BRAND_DIR)
    try:
        css = brand_check.read(p, p.parent) if p else ""
    except OSError:
        return {}
    return {mode: brand_check.tokens(css, mode) for mode in brand_check.MODES}


def warnings(color: str | None, toks: dict | None = None) -> list[str]:
    """What a fixed colour may do to the charts, one line each, naming the mode. A palette slot has none: the kit's own check
    (Settings, "Your theme fails …") covers its series tokens."""
    if not color or color in SLOTS:
        return []
    toks = _theme_tokens() if toks is None else toks
    out = []
    for mode in ("light", "dark"):
        t = toks.get(mode) or {}
        try:
            r = brand_check.ratio(color, brand_check.resolve("surface-200", t))
            if r < CONTRAST:
                out.append(f"{MODE_WORDS[mode]}, {color} is faint on a chart: {r:.1f}:1 against the panel, where {CONTRAST:g}:1 is wanted")
            for tok, means in (("success", "kept"), ("danger", "drew down")):
                d = brand_check.delta_e(color, brand_check.resolve(tok, t))
                if d < NEAR:
                    out.append(f"{MODE_WORDS[mode]}, {color} looks like --{tok} (ΔE {d:.1f}), which means {means} on the charts")
        except (KeyError, brand_check.Unreadable):
            continue   # a kit without the token, or with one this check cannot evaluate: nothing to compare with
    return out


# ------------------------------------------------------------------ the table

# An empty table is this feature's normal state (a cleared name loses its row), so "seed only into an empty table" alone would bring a
# seed dir's appearance.json back on every start once a household had cleared its colours (#132 review). The first write by the
# household or by a seed records this settings key, and the seed never runs again after it: the database owns the table from then on.
WRITTEN = "appearance_written"


def _written(conn) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (%s, 'true') ON CONFLICT (key) DO NOTHING", (WRITTEN,))


def rows(conn) -> list[dict]:
    return conn.execute("SELECT dimension, name, color, hidden FROM appearance ORDER BY dimension, name").fetchall()


def for_page(conn) -> dict:
    """The table as the dashboard's JSON block: {dimension: {name: {"color": …, "hidden": …}}}; {} when there are no rows, which draws
    every chart exactly as before."""
    out: dict = {}
    for r in rows(conn):
        out.setdefault(r["dimension"], {})[r["name"]] = {"color": r["color"], "hidden": bool(r["hidden"])}
    return out


def set_one(conn, dimension: str, name: str, color, hidden: bool) -> str | None:
    """Write one name's appearance; a name back at the palette and shown loses its row (no row is the default). The colour is
    returned as stored. ValueError names the problem."""
    if dimension not in DIMENSIONS:
        raise ValueError(f"unknown dimension {dimension!r}: one of {', '.join(DIMENSIONS)}")
    name = (name or "").strip()
    if not name:
        raise ValueError("give the name")
    c, problem = check_color(color)
    if problem:
        raise ValueError(f"{name}: {problem}")
    if hidden and dimension in COLOUR_ONLY:
        raise ValueError(HIDE_REFUSED.format(name=name))
    _written(conn)
    if c is None and not hidden:
        conn.execute("DELETE FROM appearance WHERE dimension=%s AND name=%s", (dimension, name))
    else:
        conn.execute("INSERT INTO appearance (dimension, name, color, hidden) VALUES (%s,%s,%s,%s) ON CONFLICT (dimension, name) "
                     "DO UPDATE SET color=EXCLUDED.color, hidden=EXCLUDED.hidden, updated_at=now()", (dimension, name, c, bool(hidden)))
    return c


def delete(conn, dimension: str, name: str) -> int:
    _written(conn)
    return conn.execute("DELETE FROM appearance WHERE dimension=%s AND name=%s", (dimension, name)).rowcount


def rename(conn, dimensions: tuple[str, ...], old: str, new: str) -> None:
    """A category or canonical rename (#133) carries its colour and hidden switch, in the caller's transaction. Renaming into a name
    that already has a row is a merge: the name kept keeps its own appearance, and the old row goes."""
    for d in dimensions:
        conn.execute("UPDATE appearance SET name=%s, updated_at=now() WHERE dimension=%s AND name=%s "
                     "AND NOT EXISTS (SELECT 1 FROM appearance WHERE dimension=%s AND name=%s)", (new, d, old, d, new))
        conn.execute("DELETE FROM appearance WHERE dimension=%s AND name=%s", (d, old))


def parse(data, converted: list | None = None) -> list[dict]:
    """appearance.json (settings.zip, a seed directory, `set-appearance`): a list of {dimension, name, color?, hidden?}, checked as the
    Settings page checks a row. ValueError lists the first problems by item number. `hidden: true` on an event kind (a file from before
    0.9.0, #235) is refused, unless the caller passes `converted`: the seed loader, which collects those kinds there and switches their
    events off the chart, as the migration did, keeping the row's colour."""
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        raise ValueError("appearance must be a JSON list of objects: {\"dimension\", \"name\", \"color\", \"hidden\"}")
    out, problems, seen = [], [], set()
    for i, x in enumerate(data, 1):
        d, n = x.get("dimension"), x.get("name")
        if d not in DIMENSIONS:
            problems.append(f"item {i}: dimension must be one of {', '.join(DIMENSIONS)}"); continue
        if not isinstance(n, str) or not n.strip():
            problems.append(f"item {i}: name is required"); continue
        c, problem = check_color(x.get("color"))
        if problem:
            problems.append(f"item {i}: color {problem}"); continue
        h = x.get("hidden", False)
        if not isinstance(h, bool):
            problems.append(f"item {i}: hidden must be true or false"); continue
        if h and d in COLOUR_ONLY:
            if converted is None:
                problems.append(f"item {i}: " + HIDE_REFUSED.format(name=f"{d} {n.strip()}")); continue
            converted.append(n.strip()); h = False
        if (d, n.strip()) in seen:
            problems.append(f"item {i}: {d} {n.strip()} is listed twice"); continue
        seen.add((d, n.strip()))
        if c is not None or h:   # a row that says "as before" is no row
            out.append({"dimension": d, "name": n.strip(), "color": c, "hidden": h})
    if problems:
        raise ValueError("; ".join(problems[:8]) + (f"; and {len(problems) - 8} more" if len(problems) > 8 else ""))
    return out


def import_json(conn, body: str) -> str:
    """Replace the table from appearance.json (the CLI's set-appearance); nothing is written if any item is bad."""
    items = parse(json.loads(body))
    _written(conn)
    conn.execute("DELETE FROM appearance")
    conn.cursor().executemany("INSERT INTO appearance (dimension, name, color, hidden) VALUES (%s,%s,%s,%s)",
                              [(x["dimension"], x["name"], x["color"], x["hidden"]) for x in items])
    warn = [f"{x['name']}: {w}" for x in items for w in warnings(x["color"])]
    return f"{len(items)} appearance row{'' if len(items) == 1 else 's'}" + (f" (warnings: {'; '.join(warn[:4])})" if warn else "")


def export_json(conn) -> str:
    return json.dumps([{"dimension": r["dimension"], "name": r["name"], **({"color": r["color"]} if r["color"] else {}),
                        **({"hidden": True} if r["hidden"] else {})} for r in rows(conn)], indent=1, ensure_ascii=False)


def seed(conn, path) -> None:
    """First boot: appearance.json from a seed directory, only into an empty table the household has never written (WRITTEN)."""
    if not path.exists() or conn.execute("SELECT 1 FROM settings WHERE key=%s", (WRITTEN,)).fetchone() \
            or conn.execute("SELECT 1 FROM appearance LIMIT 1").fetchone():
        return
    kinds: list[str] = []   # event kinds an older file hid: their events go off the chart instead (#235, as 0235_… did)
    try:
        items = parse(json.loads(path.read_text(encoding="utf-8-sig")), converted=kinds)
    except ValueError as e:
        raise ValueError(f"seed {path}: {e}") from None
    conn.cursor().executemany("INSERT INTO appearance (dimension, name, color, hidden) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                              [(x["dimension"], x["name"], x["color"], x["hidden"]) for x in items])
    if kinds and conn.execute("UPDATE life_events SET on_chart = false WHERE kind = ANY(%s) AND on_chart", (kinds,)).rowcount:
        from . import db
        db.mark_inputs_changed(conn)   # events.json changed: the next build carries the switch into the events dataset
    if kinds:
        logging.getLogger("ledger").info("seed %s: event kinds %s were hidden; their events are now off the chart (On the chart, #235)",
                                         path, ", ".join(kinds))
    _written(conn)


# ------------------------------------------------------------------ what the data names

def account_type_label(kind: str, typ: str | None) -> str:
    """An account type as net worth's legend names it (dashboard.html, nw-legend): a debt's type is "owed: …"."""
    return ("owed: " if kind == "debt" else "") + (typ or "other")


def present(conn) -> dict[str, list[str]]:
    """The names the current data has, per dimension: from the last build's datasets (so what the charts draw) and, for event kinds,
    the life events."""
    def ds(name: str, field: str) -> list[str]:
        return [r["v"] for r in conn.execute("SELECT DISTINCT e->>%s AS v FROM datasets, jsonb_array_elements(rows) e WHERE name=%s "
                                             "AND e->>%s IS NOT NULL AND e->>%s <> '' ORDER BY 1", (field, name, field, field)).fetchall()]
    bal = conn.execute("SELECT DISTINCT e->>'kind' AS kind, e->>'type' AS type FROM datasets, jsonb_array_elements(rows) e "
                       "WHERE name='balances' AND (e->>'in_net_worth')::boolean").fetchall()
    return {"category": ds("cat_year", "category"), "item": ds("slush", "item"), "employer": ds("employer", "employer"),
            "person": ds("people", "who"), "debt": ds("debts", "item"),
            "account_type": sorted({account_type_label(r["kind"], r["type"]) for r in bal}),
            "event_kind": [r["kind"] for r in conn.execute("SELECT DISTINCT kind FROM life_events WHERE kind IS NOT NULL ORDER BY kind").fetchall()]}


def page(conn) -> dict:
    """Settings → Chart appearance: per dimension, each name the data has with its colour, hidden switch (none for COLOUR_ONLY) and
    warnings; and the orphans (rows whose name the data no longer has)."""
    names, table, toks = present(conn), for_page(conn), _theme_tokens()
    out = {}
    for d in DIMENSIONS:
        have = table.get(d, {})
        out[d] = [{"name": n, "color": (have.get(n) or {}).get("color"), "hidden": bool((have.get(n) or {}).get("hidden")),
                   "warnings": warnings((have.get(n) or {}).get("color"), toks)} for n in names[d]]
    orphans = [{**r, "noun": LABELS[r["dimension"]][1]} for r in rows(conn) if r["name"] not in set(names[r["dimension"]])]
    return {"dimensions": out, "orphans": orphans}
