"""AGENTS.md, "Long lists": a list that grows with the household's data (marked data-list="unbounded") is the last thing in its panel,
or sits behind a count in a <details>. Checked on the rendered pages, with enough data that every marked list is drawn."""
from __future__ import annotations
from html.parser import HTMLParser

import pytest

from conftest import GOLDEN, upload_files

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
CONTENT = {"table", "form", "section", "details", "textarea", "ul", "ol", "h2", "h3", "select", "fieldset"}   # what must not follow a list
PANEL = lambda n: n.attrs.get("role") == "tabpanel" or "panel" in (n.attrs.get("class") or "").split()


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children, self.text = tag, dict(attrs), parent, [], ""


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(); self.root = Node("root", {}, None); self.cur = self.root
    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur); self.cur.children.append(n)
        if tag not in VOID: self.cur = n
    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag: n = n.parent
        if n is not self.root: self.cur = n.parent
    def handle_data(self, data):
        self.cur.text += data


def walk(n):
    yield n
    for c in n.children: yield from walk(c)


def text(n):
    return n.text + "".join(text(c) for c in n.children)


def problems(html: str) -> list[str]:
    t = Tree(); t.feed(html); out = []
    for n in walk(t.root):
        if n.attrs.get("data-list") != "unbounded":
            continue
        name = n.attrs.get("id") or n.attrs.get("data-kind") or n.tag
        a, collapsed = n.parent, None
        while a is not None and not PANEL(a):
            if a.tag == "details": collapsed = a
            a = a.parent
        if collapsed is not None:
            summary = next((c for c in collapsed.children if c.tag == "summary"), None)
            if summary is None or not any(ch.isdigit() for ch in text(summary)):
                out.append(f"{name}: collapsed, but its summary does not say how many")
            continue
        if a is None:
            out.append(f"{name}: in no panel"); continue
        # everything after the list, in document order, up to the end of its panel
        seen, after = False, []
        for m in walk(a):
            if m is n: seen = True; continue
            if seen and not any(p is n for p in _ancestors(m)) and m.tag in CONTENT:
                after.append(m.tag + (f"#{m.attrs['id']}" if m.attrs.get("id") else ""))
        if after:
            out.append(f"{name}: followed in its panel by {', '.join(after[:4])}")
    return out


def _ancestors(m):
    while m.parent is not None:
        m = m.parent; yield m


def test_the_checker_catches_a_list_with_something_under_it():
    bad = '<div class="panel"><table data-list="unbounded" id="x"><tr><td>1</td></tr></table><form id="f"></form></div>'
    assert problems(bad) == ["x: followed in its panel by form#f"]
    ok = '<div class="panel"><form id="f"></form><table data-list="unbounded"></table><p><button>Save</button></p></div>'
    assert problems(ok) == []
    assert problems('<div class="panel"><details><summary>Show</summary><ul data-list="unbounded" id="u"></ul></details><form></form></div>') \
        == ["u: collapsed, but its summary does not say how many"]


@pytest.mark.parametrize("path", ["/settings", "/upload", "/upload?balances_month=2025-04", "/checkin/2025-04"])
def test_long_lists_end_their_panel(client, reset_db, path):
    upload_files(client, GOLDEN / "exports")
    html = client.get(path).text
    assert 'data-list="unbounded"' in html
    assert problems(html) == []


def test_debt_types_have_a_sub_tab_of_their_own_and_saves_land_on_it(client, reset_db):
    """The case that prompted the rule: a debt type was set at the bottom of a page under every mapping row and every canonical item."""
    upload_files(client, GOLDEN / "exports")
    html = client.get("/settings").text
    t = Tree(); t.feed(html)
    debts = next(n for n in walk(t.root) if n.attrs.get("id") == "sp-mapping-debts")
    names = [text(r.children[0]).strip() for r in walk(debts) if r.tag == "tr" and r.children and r.children[0].tag == "td"]
    assert names == ["Student Loan"]   # only canonical items with debt rows (or a type)
    r = client.post("/settings/mapping/canonical/debt-type", data={"name": "Student Loan", "debt_type": "student"})
    assert r.status_code == 303 and r.headers["location"] == "/settings#mapping/debts"
    r = client.post("/settings/mapping/category/rename", data={"old": "Debt", "new": "Debts"})
    assert r.headers["location"] == "/settings#mapping/categories"


def test_the_mapping_draft_list_ends_its_panel(client, reset_db):
    upload_files(client, GOLDEN / "exports")
    from app import db
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM item_mapping WHERE item IN (SELECT item FROM item_mapping ORDER BY item LIMIT 2)"); conn.commit()
    html = client.get("/settings/mapping/draft").text
    assert 'data-list="unbounded"' in html and problems(html) == []
