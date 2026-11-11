/* Settings page: inline row editors for mapping, goals and events. Plain JS; the tables are server-rendered and work read-only without it. */
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
  function needRebuild(res) { var b = document.getElementById("rebuild-banner"); if (b && res.rebuild_needed) b.hidden = false; }
  function status(msg, bad) {
    var s = document.getElementById("rowstatus"); s.textContent = msg; s.className = "rowstatus " + (bad ? "bad" : "ok");
  }

  document.querySelectorAll("table.edit").forEach(function (table) {
    var kind = table.dataset.kind, fields = table.dataset.fields.split(","), flows = (table.dataset.flows || "").split(",");
    var origKeys = kind === "events" ? ["original_m", "original_label"] : ["original"];
    var origFields = kind === "events" ? ["m", "label"] : [fields[0]];
    var tbody = table.tBodies[0];

    function origOf(tr) { var o = {}; origKeys.forEach(function (k, i) { o[k] = tr.dataset["o" + i] || ""; }); return o; }
    function setOrig(tr, row) { origFields.forEach(function (f, i) { tr.dataset["o" + i] = row[f] == null ? "" : row[f]; }); }
    function show(tr, row) {
      tr.className = ""; tr.textContent = "";
      fields.forEach(function (f) { tr.appendChild(el("td", { "data-f": f }, row[f] == null ? "" : String(row[f]))); });
      var td = el("td", { "class": "acts" });
      var eb = el("button", { type: "button", "class": "btn", "data-act": "edit" }, "Edit"), db = el("button", { type: "button", "class": "btn", "data-act": "del" }, "Delete");
      td.appendChild(eb); td.appendChild(db); tr.appendChild(td);
      eb.onclick = function () { edit(tr); }; db.onclick = function () { del(tr); };
    }
    function rowOf(tr) { var r = {}; fields.forEach(function (f) { r[f] = tr.querySelector('td[data-f="' + f + '"]').textContent; }); return r; }
    function edit(tr, blank) {
      var row = blank ? {} : rowOf(tr); tr.className = "editing"; tr.textContent = "";
      fields.forEach(function (f) {
        var td = el("td", { "data-f": f }), inp;
        if (f === "flow") {
          inp = el("select", { name: f, "aria-label": f });
          flows.forEach(function (o) { var op = el("option", { value: o }, o); if (o === row[f]) op.selected = true; inp.appendChild(op); });
          if (blank) inp.value = "expense";
        } else {
          inp = el("input", { type: "text", name: f, "aria-label": f, value: row[f] == null ? "" : row[f] });
          if (f === "value") inp.setAttribute("inputmode", "decimal");
          if (f === "m") inp.setAttribute("placeholder", "YYYY-MM");
        }
        td.appendChild(inp); td.appendChild(el("div", { "class": "err", role: "alert" })); tr.appendChild(td);
      });
      var acts = el("td", { "class": "acts" }), sv = el("button", { type: "button", "class": "btn primary" }, "Save"), cn = el("button", { type: "button", "class": "btn" }, "Cancel");
      acts.appendChild(sv); acts.appendChild(cn); tr.appendChild(acts);
      sv.onclick = function () { save(tr); };
      cn.onclick = function () { if (tr.dataset.new) tr.remove(); else show(tr, row); };
      tr.onkeydown = function (e) { if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); save(tr); } else if (e.key === "Escape") cn.click(); };
      var first = tr.querySelector("input,select"); if (first) first.focus();
    }
    function save(tr) {
      var body = origOf(tr);
      tr.querySelectorAll("input,select").forEach(function (i) { body[i.name] = i.value; });
      tr.querySelectorAll(".err").forEach(function (e) { e.textContent = ""; });
      tr.querySelectorAll("[aria-invalid]").forEach(function (i) { i.removeAttribute("aria-invalid"); });
      post("/settings/" + kind + "/row", body).then(function (res) {
        if (res.ok) { delete tr.dataset.new; setOrig(tr, res.row); show(tr, res.row); status("Saved. The dashboard shows it after a rebuild: press Rebuild now above.", false); needRebuild(res); return; }
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
        if (res.ok) { tr.remove(); status("Row deleted. The dashboard shows it after a rebuild.", false); needRebuild(res); } else status((res.errors && res.errors._) || "Row not deleted (already gone? reload the page).", true);
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
      th.tabIndex = 0; th.setAttribute("role", "button");
      function go() {
        var idx = fields.indexOf(th.dataset.sort), dir = th.getAttribute("aria-sort") === "ascending" ? -1 : 1;
        table.querySelectorAll("th").forEach(function (o) { o.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", dir === 1 ? "ascending" : "descending");
        var trs = Array.prototype.slice.call(tbody.rows), val = function (tr) { var c = tr.cells[idx]; var i = c.querySelector("input,select"); return (i ? i.value : c.textContent).trim(); };
        var num = trs.every(function (tr) { var v = val(tr); return v === "" || !isNaN(parseFloat(v)) && isFinite(v); });
        trs.sort(function (a, b) {
          var x = val(a), y = val(b);
          if (num) return dir * ((x === "" ? Infinity : parseFloat(x)) - (y === "" ? Infinity : parseFloat(y)) || 0);
          return dir * x.localeCompare(y, undefined, { sensitivity: "base" });
        });
        trs.forEach(function (tr) { tbody.appendChild(tr); });
      }
      th.onclick = go; th.onkeydown = function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } };
    });
    var filter = document.querySelector('[data-filter="' + kind + '"]');
    if (filter) filter.oninput = function () {
      var q = filter.value.toLowerCase();
      Array.prototype.forEach.call(tbody.rows, function (tr) { tr.hidden = !!q && tr.textContent.toLowerCase().indexOf(q) < 0 && !tr.classList.contains("editing"); });
    };
  });
})();
