/* a11y.js — dashboard accessibility helpers (issue #15). Runs after the page's own scripts; touches attributes and adds one popover only.
   1. Chart text alternatives: every .chart svg gets role="img" and an aria-label generated from the card heading, the legend and the axis labels.
   2. <dfn title> definitions: focusable, shown on hover, focus, click or tap (Esc closes) in a popover linked with aria-describedby.
   3. Print: the page prints in the light theme whatever the screen uses, then goes back. */
(() => {
  const root = document.getElementById('dash-root'); if (!root) return;

  // ---- 1. chart summaries ----
  const words = (n, one, many) => n + ' ' + (n === 1 ? one : many);
  const label = (chart) => {
    const svg = chart.querySelector('svg'); if (!svg) return null;
    const card = chart.closest('.card'), sub = chart.parentElement.querySelector(':scope > .sub');
    const title = [card && card.querySelector('h2'), sub].filter(Boolean).map((e) => e.textContent.trim()).join(', ') || 'Chart';
    const parts = ['Chart: ' + title + '.'];
    const legend = [...(card || document).querySelectorAll('.legend span')].map((s) => s.textContent.trim()).filter(Boolean);
    if (legend.length) parts.push((chart.classList.contains('radar') ? 'Years: ' : 'Series: ') + legend.join(', ') + '.');
    const finding = card && card.querySelector('.finding'); if (finding && finding.textContent.trim()) parts.push(finding.textContent.trim());   // the one-line finding the page draws from the data
    if (chart.dataset.span) parts.push('Figures from ' + chart.dataset.span + '.');   // the data's own range, not the first and last tick
    else for (const g of svg.querySelectorAll(':scope > g')) {
      const t = [...g.querySelectorAll('.tick text')].map((x) => x.textContent.trim()).filter(Boolean);
      if (t.length > 1) parts.push('Axis labels run from ' + t[0] + ' to ' + t[t.length - 1] + ' (' + words(t.length, 'label', 'labels') + ').');
    }
    if (card && card.querySelector('details.datatable')) parts.push('The same figures are in a table after the chart.');
    if (chart.classList.contains('radar')) { const c = [...svg.querySelectorAll(':scope > g.labels text')].map((x) => (x.querySelector('title') || x).textContent.trim()); if (c.length) parts.push('Categories: ' + c.join(', ') + '.'); }
    return parts.join(' ');
  };
  let queued = false;
  const annotate = () => {
    queued = false;
    for (const chart of root.querySelectorAll('.chart')) {
      const svg = chart.querySelector('svg'); if (!svg) continue;
      const text = label(chart); if (!text) continue;
      svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', text); svg.removeAttribute('aria-hidden');
    }
  };
  const queue = () => { if (!queued) { queued = true; requestAnimationFrame(annotate); } };
  new MutationObserver(queue).observe(root, { childList: true, subtree: true });
  queue();

  // ---- 2. definitions ----
  let pop = null, openFor = null, uid = 0;
  const hide = () => { if (pop) pop.hidden = true; if (openFor) openFor.removeAttribute('aria-expanded'); openFor = null; };
  const show = (dfn) => {
    if (!pop) { pop = document.createElement('div'); pop.className = 'dfn-pop'; pop.id = 'dfn-pop'; pop.setAttribute('role', 'tooltip'); pop.hidden = true; document.body.appendChild(pop); }
    if (openFor && openFor !== dfn) openFor.removeAttribute('aria-expanded');
    pop.textContent = dfn.dataset.def; pop.hidden = false; openFor = dfn; dfn.setAttribute('aria-expanded', 'true'); dfn.setAttribute('aria-describedby', 'dfn-pop');
    const r = dfn.getBoundingClientRect(), w = pop.offsetWidth;
    pop.style.left = Math.max(8, Math.min(r.left + scrollX, scrollX + document.documentElement.clientWidth - w - 8)) + 'px';
    pop.style.top = (r.bottom + scrollY + 6) + 'px';
  };
  for (const d of root.querySelectorAll('dfn[title]')) {
    d.dataset.def = d.getAttribute('title'); d.removeAttribute('title'); d.tabIndex = 0; d.setAttribute('role', 'button'); d.id = d.id || 'dfn-' + (++uid);
  }
  const dfnOf = (e) => e.target.closest && e.target.closest('dfn[data-def]');
  root.addEventListener('click', (e) => { const d = dfnOf(e); if (d) show(d); });   // focus shows it too, so a tap or click must not toggle it back off
  root.addEventListener('keydown', (e) => { const d = dfnOf(e); if (d && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); show(d); } });   // Esc, blur or a tap elsewhere closes
  root.addEventListener('pointerover', (e) => { if (e.pointerType === 'mouse') { const d = dfnOf(e); if (d) show(d); } });
  root.addEventListener('pointerout', (e) => { if (e.pointerType === 'mouse' && dfnOf(e) && !(e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest('dfn[data-def]'))) hide(); });
  root.addEventListener('focusin', (e) => { const d = dfnOf(e); if (d) show(d); });
  root.addEventListener('focusout', (e) => { if (dfnOf(e)) hide(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
  document.addEventListener('click', (e) => { if (openFor && !dfnOf(e)) hide(); });

  // ---- 3. print ----
  // beforeprint/afterprint where the browser fires them, and the print media query where it does not; both paths are idempotent
  let printing = false, restore = null;
  const before = () => {
    if (printing) return; printing = true;
    const h = document.documentElement; restore = h.getAttribute('data-theme');
    h.setAttribute('data-theme', 'light'); document.querySelectorAll('.hl details.how').forEach((d) => { if (!d.open) { d.open = true; d.dataset.printOpened = '1'; } });
  };
  const after = () => {
    if (!printing) return; printing = false;
    const h = document.documentElement; if (restore) h.setAttribute('data-theme', restore); else h.removeAttribute('data-theme'); restore = null;
    document.querySelectorAll('.hl details.how[data-print-opened]').forEach((d) => { d.open = false; delete d.dataset.printOpened; });
  };
  window.addEventListener('beforeprint', before); window.addEventListener('afterprint', after);
  matchMedia('print').addEventListener('change', (e) => (e.matches ? before() : after()));
})();
