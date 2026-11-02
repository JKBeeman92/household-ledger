/* dash-shim.js — a small stand-in for the Dashboard artifact runtime, so the chart code runs unchanged.
   dash.data(id) -> {status, data}; dash.draw(name, fn); dash.calc(id, {inputs, fn}); dash.onData(fn);
   dash.params()/setParams()/resetParams(); dash.colors; dash.setLink(name). Data comes from /api/datasets. */
(() => {
  const draws = new Map();      // name -> fn
  const calcs = new Map();      // id -> {inputs, fn, value, dirty}
  const onData = [];
  const datasets = {};          // id -> rows
  let status = 'loading';
  let params = {};
  try { params = JSON.parse(localStorage.getItem('dash.params') || '{}'); } catch (e) { params = {}; }
  const defaults = { dollars: 'nominal' };

  const tokens = ['--series-1', '--series-2', '--series-3', '--series-4', '--series-5', '--series-6', '--series-7', '--series-8'];
  const readColors = () => { const cs = getComputedStyle(document.documentElement); return tokens.map((t) => cs.getPropertyValue(t).trim() || '#888'); };

  const calcValue = (id, seen = new Set()) => {
    const c = calcs.get(id); if (!c) return undefined;
    if (!c.dirty) return c.value;
    if (seen.has(id)) throw new Error('calc cycle at ' + id); seen.add(id);
    const args = c.inputs.map((i) => calcs.has(i) ? calcValue(i, seen) : datasets[i]);
    if (args.some((a) => a === undefined)) return undefined;
    c.value = c.fn(...args); c.dirty = false; return c.value;
  };
  const invalidate = () => { for (const c of calcs.values()) c.dirty = true; };

  const dash = {
    get colors() { return readColors(); },
    data(id) {
      if (status !== 'ok') return { status, data: [], columns: [], meta: [] };
      if (calcs.has(id)) { const v = calcValue(id); return v === undefined ? { status: 'loading', data: [] } : { status: 'ok', data: v, columns: v.length ? Object.keys(v[0]) : [], meta: [] }; }
      const d = datasets[id];
      return d ? { status: 'ok', data: d, columns: d.length ? Object.keys(d[0]) : [], meta: [] } : { status: 'missing', data: [] };
    },
    calc(id, spec) { calcs.set(id, { inputs: spec.inputs, fn: spec.fn, title: spec.title, description: spec.description, value: undefined, dirty: true }); },
    loader() {},
    draw(name, fn) {
      if (fn) draws.set(name, fn);
      for (const el of document.querySelectorAll(`[data-draw="${name}"]`)) {
        if (el.closest('[hidden]')) continue;
        try { (draws.get(name) || (() => {}))(el); } catch (e) { console.error('draw', name, e); el.textContent = '—'; }
      }
    },
    onData(fn) { onData.push(fn); if (status === 'ok') { try { fn(); } catch (e) { console.error(e); } } },
    params() { return { ...defaults, ...params }; },
    setParams(p) { for (const [k, v] of Object.entries(p)) { if (v == null) delete params[k]; else params[k] = v; } try { localStorage.setItem('dash.params', JSON.stringify(params)); } catch (e) {} redraw(); },
    resetParams() { params = {}; try { localStorage.removeItem('dash.params'); } catch (e) {} redraw(); },
    setLink(name) { if (name && location.hash !== '#' + name) history.replaceState(null, '', '#' + name); },
    refresh() { load(); },
    _redraw: null,
  };

  const redraw = () => { for (const fn of onData) { try { fn(); } catch (e) { console.error(e); } } for (const name of draws.keys()) dash.draw(name); };
  dash._redraw = redraw;

  const load = async () => {
    status = 'loading';
    try {
      const r = await fetch('/api/datasets', { credentials: 'same-origin' });
      if (r.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname + location.hash); return; }
      const j = await r.json();
      for (const [k, v] of Object.entries(j)) datasets[k] = v;
      invalidate(); status = 'ok';
    } catch (e) { console.error('load', e); status = 'error'; }
    redraw();
  };

  // redraw drawings whose element changed width (charts size from clientWidth)
  const ro = new ResizeObserver((entries) => { const names = new Set(); for (const en of entries) { const n = en.target.dataset.draw; if (n) names.add(n); } for (const n of names) dash.draw(n); });
  const observeAll = () => { for (const el of document.querySelectorAll('[data-draw]')) ro.observe(el); };

  window.dash = dash;
  window.addEventListener('DOMContentLoaded', () => {
    observeAll(); load();
    // when a section is un-hidden (tab switch), draw everything inside it — inline elements never trigger ResizeObserver
    new MutationObserver((muts) => {
      if (status !== 'ok') return;
      for (const m of muts) { if (!m.target.hidden) for (const el of m.target.querySelectorAll('[data-draw]')) if (!el.closest('[hidden]')) { const n = el.dataset.draw; try { (draws.get(n) || (() => {}))(el); } catch (e) { console.error('draw', n, e); } } }
    }).observe(document.body, { attributes: true, attributeFilter: ['hidden'], subtree: true });
  });
  // theme switch: redraw so chart colors follow the tokens
  new MutationObserver(() => { if (status === 'ok') redraw(); }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
})();
