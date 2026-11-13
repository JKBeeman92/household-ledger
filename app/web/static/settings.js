/* Settings page: inline row editors for mapping, goals, events and accounts. Plain JS; the tables are server-rendered and work read-only without it. */
(function () {
  var csrf = document.getElementById("csrf-form");
  function post(url, fields) {
    var fd = new FormData();
    if (csrf) new FormData(csrf).forEach(function (v, k) { fd.append(k, v); });
    Object.keys(fields).forEach(function (k) { fd.append(k, fields[k] == null ? "" : fields[k]); });
    return fetch(url, { method: "POST", body: fd, credentials: "same-origin" }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (r.status === 401) { location.href = "/login?next=" + encodeURIComponent("/settings"); return { ok: false, errors: { _: "Signed out: sending you to sign in." } }; }
        if (!r.ok && !body.errors && body.deleted === undefined) return { ok: false, errors: { _: body.detail || "server error " + r.status } };
        return body;
      });
    });
  }
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text != null) e.textContent = text;
    return e;
  }
  var flowHelp = {}; try { var fh = document.getElementById && document.getElementById("flow-help"); if (fh) flowHelp = JSON.parse(fh.textContent); } catch (e) {}
  var cats = {}; (function () { var dl = document.getElementById && document.getElementById("settings-categories"); if (dl) Array.prototype.forEach.call(dl.querySelectorAll("option"), function (o) { cats[o.value.toLowerCase()] = 1; }); })();
  function needRebuild(res) { var b = document.getElementById("rebuild-banner"); if (b && res.rebuild_needed) b.hidden = false; }
  function status(msg, bad) {
    var s = document.getElementById("rowstatus"); s.textContent = msg; s.className = "rowstatus " + (bad ? "bad" : "ok");
  }

  document.querySelectorAll("table.edit").forEach(function (table) {
    var kind = table.dataset.kind, fields = table.dataset.fields.split(","), flows = (table.dataset.flows || "").split(",");
    var choices = {}; try { choices = JSON.parse(table.dataset.choices || "{}"); } catch (e) { choices = {}; }   // fields picked from a fixed list (#109)
    var origKeys = kind === "events" ? ["original_m", "original_label"] : ["original"];
    var origFields = kind === "events" ? ["m", "label"] : [fields[0]];
    var tbody = table.tBodies[0];

    function heading(f) { var th = table.querySelector && table.querySelector('th[data-sort="' + f + '"]'); return th ? th.textContent.trim() : f; }
    function flash(tr, msg) {   // say it where the eye is: on the row that was saved
      tr.className = "saved"; var acts = tr.querySelector(".acts"); if (acts) acts.appendChild(el("span", { "class": "rowmsg" }, msg));
      setTimeout(function () { if (tr.className === "saved") tr.className = ""; var m = tr.querySelector(".rowmsg"); if (m && m.remove) m.remove(); }, 2600);
    }
    function origOf(tr) { var o = {}; origKeys.forEach(function (k, i) { o[k] = tr.dataset["o" + i] || ""; }); return o; }
    function setOrig(tr, row) { origFields.forEach(function (f, i) { tr.dataset["o" + i] = row[f] == null ? "" : row[f]; }); }
    function whatOf(row) {   // the name a row's buttons carry (DS-A11Y-2); the grid macro in settings.html says the same
      var v = function (f) { return row[f] == null ? "" : String(row[f]); };
      return kind === "events" ? (v(fields[0]) + " " + v(fields[1])).trim() || "this row" : v(fields[0]) || "this row";
    }
    function show(tr, row) {
      tr.className = ""; tr.textContent = "";
      fields.forEach(function (f) { var th = table.querySelector('th[data-sort="' + f + '"]'), at = { "data-f": f }, empty = row[f] == null || row[f] === ""; if (th && th.classList.contains("num")) at["class"] = "num"; if (empty) at["data-v"] = ""; tr.appendChild(el("td", at, empty ? "\u2014" : String(row[f]))); });   // a missing figure is a dash, the stored value kept in data-v (DS-WORD-6)   // a figure column stays right-aligned (DS-TYPE-3)
      var td = el("td", { "class": "acts" });
      var what = whatOf(row);   // a button names its row to a screen reader (DS-A11Y-2)
      var eb = el("button", { type: "button", "class": "btn", "data-act": "edit", "aria-label": "Edit " + what }, "Edit"), db = el("button", { type: "button", "class": "btn", "data-act": "del", "aria-label": "Delete " + what }, "Delete");
      td.appendChild(eb); td.appendChild(db); tr.appendChild(td);
      eb.onclick = function () { edit(tr); }; db.onclick = function () { del(tr); };
    }
    function rowOf(tr) { var r = {}; fields.forEach(function (f) { var td = tr.querySelector('td[data-f="' + f + '"]'); r[f] = td.hasAttribute("data-v") ? td.getAttribute("data-v") : td.textContent; }); return r; }   // data-v: the stored value behind a formatted figure
    function edit(tr, blank) {
      Array.prototype.forEach.call(document.querySelectorAll("tr.editing"), function (o) { if (o !== tr && o.cancelEdit) o.cancelEdit(); });   // one editor open at a time, so one primary Save (DS-BTN-1)
      var row = blank ? {} : rowOf(tr); tr.className = "editing"; tr.textContent = "";
      fields.forEach(function (f) {
        var td = el("td", { "data-f": f }), inp;
        if (f === "flow") {
          inp = el("select", { name: f, "aria-label": heading(f) });
          flows.forEach(function (o) { var op = el("option", { value: o }, o); if (o === row[f]) op.selected = true; inp.appendChild(op); });
          if (blank) inp.value = "expense";
          var hint = el("div", { "class": "hint" }, flowHelp[inp.value] || ""); inp.onchange = function () { hint.textContent = flowHelp[inp.value] || ""; };
          td.appendChild(inp); td.appendChild(hint); td.appendChild(el("div", { "class": "err", role: "alert" })); tr.appendChild(td); return;
        } else if (choices[f]) {
          inp = el("select", { name: f, "aria-label": heading(f) });
          var opts = choices[f].slice(); if (row[f] && opts.indexOf(row[f]) < 0) opts.push(row[f]);   // keep a value the list no longer has
          opts.forEach(function (o) { var op = el("option", { value: o }, o === "" ? "(none)" : o); if (o === (row[f] || "")) op.selected = true; inp.appendChild(op); });
        } else {
          inp = el("input", { type: "text", name: f, "aria-label": heading(f), value: row[f] == null ? "" : row[f] });
          if (f === "category" && kind === "mapping") inp.setAttribute("list", "settings-categories");
          if (f === "person" && (kind === "events" || kind === "accounts")) inp.setAttribute("list", "settings-people");   // suggested, not required: an unknown person is a warning (#109)
          if (f === "debt_item" && kind === "accounts") inp.setAttribute("list", "settings-debt-items");   // #143
          if (f === "apr" || f === "escrow") inp.setAttribute("inputmode", "decimal");
          if (f === "canonical_item" && kind === "mapping") inp.setAttribute("list", "settings-canonicals");
          if (f === "value") inp.setAttribute("inputmode", "decimal");
          if (f === "m") inp.setAttribute("placeholder", "YYYY-MM");
        }
        td.appendChild(inp); td.appendChild(el("div", { "class": "err", role: "alert" })); tr.appendChild(td);
      });
      var named = blank ? "the new row" : whatOf(row);
      var acts = el("td", { "class": "acts" }), sv = el("button", { type: "button", "class": "btn primary", "aria-label": "Save " + named }, "Save"), cn = el("button", { type: "button", "class": "btn", "aria-label": "Cancel " + named }, "Cancel");
      acts.appendChild(sv); acts.appendChild(cn); tr.appendChild(acts);
      if (kind === "mapping") {   // #133: a canonical item has one category, so picking a known one fills it in
        var canonIn = tr.querySelector('input[name="canonical_item"]'), catIn = tr.querySelector('input[name="category"]');
        var known = {}; try { known = JSON.parse((document.getElementById("canonical-category") || {}).textContent || "{}"); } catch (e) { known = {}; }
        var fill = function () { var k = canonIn.value.trim(), c = Object.prototype.hasOwnProperty.call(known, k) ? known[k] : null; if (typeof c === "string" && c) catIn.value = c; };
        if (canonIn && catIn) { canonIn.onchange = fill; canonIn.oninput = fill; tr.fillCategory = fill; }
      }
      if (blank && kind === "mapping") {   // #106: a new item whose name drifted from one already mapped ("X 2", "X Fund") gets that row's values
        var itemIn = tr.querySelector('input[name="item"]'), like = el("div", { "class": "hint", id: "suggest-hint" });
        itemIn.parentNode.appendChild(like);
        itemIn.onchange = function () {
          var v = itemIn.value.trim(); like.textContent = ""; if (!v) return;
          fetch("/settings/mapping/suggest?item=" + encodeURIComponent(v), { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : {}; }).then(function (res) {
            var s = res && res.suggestion; if (!s || itemIn.value.trim() !== v) return;
            [["canonical_item", s.canonical_item], ["category", s.category]].forEach(function (p) { var i = tr.querySelector('input[name="' + p[0] + '"]'); if (i && !i.value) i.value = p[1]; });
            var fl = tr.querySelector('select[name="flow"]'); if (fl) { fl.value = s.flow; if (fl.onchange) fl.onchange(); }
            like.textContent = "Filled in like " + s.like + ". Change anything that differs.";
          }).catch(function () {});
        };
      }
      tr.cancelEdit = function () { cn.click(); };
      sv.onclick = function () { save(tr); };
      cn.onclick = function () { if (tr.dataset.new) { var a = tr.closest("section, .panel, body").querySelector("[data-act=add]"); tr.remove(); if (a) a.focus(); } else { show(tr, row); back(tr); } };
      tr.onkeydown = function (e) { if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); save(tr); } else if (e.key === "Escape") cn.click(); };
      var first = tr.querySelector("input,select"); if (first) first.focus();
    }
    function back(tr) { var e = tr.querySelector("[data-act=edit]"); if (e) e.focus(); }   // focus returns to the row, not to the top of the page (DS-A11Y-1)
    function save(tr) {
      var body = origOf(tr);
      if (tr.fillCategory) tr.fillCategory();   // Enter saves before the canonical field's change event (#133)
      tr.querySelectorAll("input,select").forEach(function (i) { body[i.name] = i.value; });
      if (kind === "mapping" && body.category && !cats[body.category.toLowerCase()] &&
          !confirm('"' + body.category + '" is a new category. Charts and goals will show it on its own. Create it?')) return;
      tr.querySelectorAll(".err").forEach(function (e) { e.textContent = ""; });
      tr.querySelectorAll("[aria-invalid]").forEach(function (i) { i.removeAttribute("aria-invalid"); });
      post("/settings/" + kind + "/row", body).then(function (res) {
        if (res.ok) { delete tr.dataset.new; setOrig(tr, res.row); show(tr, res.row); back(tr); flash(tr, "Saved"); if (kind === "mapping" && res.row && res.row.category) cats[String(res.row.category).toLowerCase()] = 1; status("Saved" + (res.warning ? " (" + res.warning + ")" : "") + ". The dashboard shows it after a rebuild: press Rebuild now above.", false); needRebuild(res); return; }
        var errs = res.errors || {}, first = true, fieldErr = false;
        Object.keys(errs).forEach(function (f) {
          var td = tr.querySelector('td[data-f="' + f + '"]');
          if (!td) { status(errs[f], true); return; }
          fieldErr = true; td.querySelector(".err").textContent = errs[f]; var i = td.querySelector("input,select"); i.setAttribute("aria-invalid", "true");
          if (first) { i.focus(); first = false; }
        });
        if (fieldErr || !Object.keys(errs).length) status("Row not saved: fix the highlighted field.", true);
      }).catch(function () { status("Row not saved: could not reach the server.", true); });
    }
    function del(tr) {
      if (!confirm("Delete this row?")) return;
      post("/settings/" + kind + "/row/delete", origOf(tr)).then(function (res) {
        if (res.ok) { var nx = tr.nextElementSibling || tr.previousElementSibling; tr.remove(); if (nx) back(nx); status("Row deleted. The dashboard shows it after a rebuild.", false); needRebuild(res); } else status((res.errors && res.errors._) || "Row not deleted (already gone? reload the page).", true);
      }).catch(function () { status("Row not deleted: could not reach the server.", true); });
    }
    Array.prototype.forEach.call(tbody.rows, function (tr) {
      var r = rowOf(tr); setOrig(tr, r);
      tr.querySelector('[data-act="edit"]').onclick = function () { edit(tr); };
      tr.querySelector('[data-act="del"]').onclick = function () { del(tr); };
    });
    var add = document.querySelector('[data-add="' + kind + '"]');
    if (add) add.onclick = function () { var tr = el("tr"); tr.dataset.new = "1"; tbody.insertBefore(tr, tbody.firstChild); edit(tr, true); };

    // sorting: click a header; numeric when every non-empty cell parses as a number
    table.querySelectorAll("th[data-sort]").forEach(function (th) {
      var btn = th.querySelector && th.querySelector("button.sortbtn");   // a real button inside the header keeps its column-header role
      function go() {
        var idx = fields.indexOf(th.dataset.sort), dir = th.getAttribute("aria-sort") === "ascending" ? -1 : 1;
        table.querySelectorAll("th").forEach(function (o) { o.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", dir === 1 ? "ascending" : "descending");
        var trs = Array.prototype.slice.call(tbody.rows), val = function (tr) { var c = tr.cells[idx]; var i = c.querySelector("input,select"); return (i ? i.value : c.hasAttribute("data-v") ? c.getAttribute("data-v") : c.textContent).trim(); };   // data-v: the stored figure behind a formatted one
        var num = trs.every(function (tr) { var v = val(tr); return v === "" || !isNaN(parseFloat(v)) && isFinite(v); });
        trs.sort(function (a, b) {
          var x = val(a), y = val(b);
          if (num) return dir * ((x === "" ? Infinity : parseFloat(x)) - (y === "" ? Infinity : parseFloat(y)) || 0);
          return dir * x.localeCompare(y, undefined, { sensitivity: "base" });
        });
        trs.forEach(function (tr) { tbody.appendChild(tr); });
      }
      if (btn) btn.onclick = go; else th.onclick = go;
    });
    var filter = document.querySelector('[data-filter="' + kind + '"]');
    if (filter) filter.oninput = function () {
      var q = filter.value.toLowerCase();
      Array.prototype.forEach.call(tbody.rows, function (tr) { tr.hidden = !!q && tr.textContent.toLowerCase().indexOf(q) < 0 && !tr.classList.contains("editing"); });
    };
  });
})();
