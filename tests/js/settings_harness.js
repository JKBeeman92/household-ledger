// Runs app/web/static/settings.js against a tiny fake DOM with a stubbed fetch; prints {status, href, rowstatus} as JSON.
// usage: node settings_harness.js <settings.js> <http status> <json body>
const fs = require("fs");
const [src, httpStatus, body] = process.argv.slice(2);
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.attrs = {}; this.kids = []; this.dataset = {}; this.className = ""; this._text = ""; this.value = ""; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "value") this.value = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  getAttribute(k) { return this.attrs[k] === undefined ? null : this.attrs[k]; }
  appendChild(c) { this.kids.push(c); return c; }
  insertBefore(c) { this.kids.unshift(c); return c; }
  remove() { this.removed = true; }
  focus() {}
  get classList() { return { contains: () => false }; }
  set textContent(t) { this._text = String(t); this.kids = []; }
  get textContent() { return this._text + this.kids.map(k => k.textContent).join(""); }
  all() { return this.kids.flatMap(k => [k, ...k.all()]); }
  match(sel) {
    return sel.split(",").some(s => {
      s = s.trim(); let m;
      if ((m = s.match(/^(\w+)\[(.+?)="(.+?)"\]$/))) return this.tagName === m[1].toUpperCase() && this.attrs[m[2]] === m[3];
      if ((m = s.match(/^\[(.+?)\]$/))) return m[1] in this.attrs;
      if (s[0] === ".") return this.className.split(" ").includes(s.slice(1)) || this.attrs["class"] === s.slice(1);
      return this.tagName === s.toUpperCase();
    });
  }
  querySelectorAll(sel) { return this.all().filter(e => e.match(sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
const status = new El("div"), tbody = new El("tbody"), table = new El("table"), add = new El("button");
tbody.rows = []; table.tBodies = [tbody]; table.dataset = { kind: "goals", fields: "key,label", flows: "" };
table.querySelectorAll = (sel) => sel === "table.edit" ? [] : sel.startsWith("th") ? [] : El.prototype.querySelectorAll.call(table, sel);
const doc = {
  getElementById: (id) => id === "rowstatus" ? status : null,
  createElement: (t) => new El(t),
  querySelectorAll: (sel) => sel === "table.edit" ? [table] : [],
  querySelector: (sel) => sel.startsWith("[data-add") ? add : null,
};
const loc = { href: "" };
global.document = doc; global.location = loc; global.confirm = () => true; global.FormData = class { forEach() {} append() {} };
global.fetch = () => Promise.resolve({ status: +httpStatus, ok: +httpStatus < 400, json: () => body === "HTML" ? Promise.reject(new Error("x")) : Promise.resolve(JSON.parse(body)) });
eval(fs.readFileSync(src, "utf8"));
add.onclick();
const tr = tbody.kids[0];
tr.querySelectorAll("button").find(b => b.textContent === "Save").onclick();
setTimeout(() => console.log(JSON.stringify({ href: loc.href, rowstatus: status.textContent, cls: status.className })), 20);
