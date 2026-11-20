#!/usr/bin/env node
// The dashboard in a real browser (#132): opens the running app, draws every tab with a chart coloured by a name, and prints what was drawn
// as JSON, so tests/test_appearance.py can check colours and hidden names against what the household set. Read-only, apart from the
// "show" press it is asked to make.
//
//   node tests/js/dashboard_harness.js http://127.0.0.1:8132 '{"theme": "light", "params": {"range": "12m"}, "press": "slush", "reload": true}'
//
// theme: light or dark (stored as the header's switch stores it); params: dash.params as stored in this browser (the period filter);
// press: a chart whose "N hidden · show" button to press; reload: open the page again afterwards (what the viewer remembers);
// fresh: then open it in a second browser context (another viewer); width: the viewport width (1280 unless given, 390 for a phone).
const { chromium } = require('playwright')

const [base, raw] = process.argv.slice(2)
const o = JSON.parse(raw || '{}')
const TABS = ['overview', 'lastmonth', 'where', 'leaks', 'position', 'people', 'method']   // the last two for its tables: reconciliation is on Method

// Runs in the page: for each drawn chart its series (name, fill) in drawing order, its bars and markers, its legend and its "hidden"
// button; the tables, tiles and tooltips' totals, which hiding must never change.
function probe() {
  const out = { charts: {}, legends: {}, tables: {}, tips: {} }
  const key = (d) => d && (d.key ?? d.item ?? d.kind ?? d.text)
  for (const el of document.querySelectorAll('.chart[data-draw]')) {
    if (el.closest('[hidden]') || !el.querySelector('svg, .hidden-note')) continue   // a chart with every name hidden still has its button
    const series = [...el.querySelectorAll('svg g[fill]')].filter((g) => g.__data__ && g.__data__.key != null).map((g) => [g.__data__.key, g.getAttribute('fill')])
    const bars = [...el.querySelectorAll('rect.b.h')].map((r) => [r.__data__.item, r.getAttribute('fill')])
    // life-event markers (#140: one per group of events within a month; a merged one is a ring, so its colour is the group's, not the fill)
    const markers = [...el.querySelectorAll('circle.ev:not(.ev-ahead)')].map((m) => [key(m.__data__), m.__data__.kind ?? null, m.__data__.color ?? m.getAttribute('fill')])
    const lines = [...el.querySelectorAll('svg > g > line[stroke-dasharray="2 3"]')].map((l) => l.getAttribute('stroke'))
    const note = el.querySelector('.hidden-note button')
    const ahead = [...el.querySelectorAll('circle.ev-ahead')].map((m) => [key(m.__data__), m.__data__.kind ?? null, m.__data__.color ?? null])
    // the buttons over the markers (#140): what each says to the keyboard and a screen reader, and whether it can take focus
    const marks = [...el.querySelectorAll('.ev-mark')].map((b) => ({ label: b.getAttribute('aria-label'), focusable: b.tagName === 'BUTTON' && b.tabIndex === 0 && !b.disabled, ahead: !!b.closest('.ahead'), box: (({ left, right, height }) => [left, right, height])(b.getBoundingClientRect()) }))
    out.charts[el.dataset.draw] = { series, bars, markers, ahead, marks, lines, note: note ? note.textContent : null }
    // the tooltip over the first bar of each series: its swatch and its total
    const tips = {}
    for (const g of el.querySelectorAll('svg g[fill]')) {
      if (!g.__data__ || g.__data__.key == null) continue
      const r = [...g.querySelectorAll('rect.b')].find((x) => Number(x.getAttribute('height')) > 0); if (!r) continue
      const b = r.getBoundingClientRect()
      r.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: b.x + b.width / 2, clientY: b.y + b.height / 2 }))
      const tip = el.querySelector('.tip'), sw = tip && tip.querySelector('.sw')
      tips[g.__data__.key] = { sw: sw ? sw.style.getPropertyValue('--sw').trim() : null, total: tip ? [...tip.querySelectorAll('.t')].map((t) => t.textContent).pop() : null, x: r.__data__.data.x }
      r.dispatchEvent(new PointerEvent('pointerleave', { bubbles: true }))
    }
    if (Object.keys(tips).length) out.tips[el.dataset.draw] = tips
  }
  for (const el of document.querySelectorAll('.legend[data-draw]')) if (!el.closest('[hidden]')) out.legends[el.dataset.draw] = [...el.children].map((s) => [s.textContent, s.style.getPropertyValue('--sw').trim()])
  out.lists = {}   // the lists under a chart (the life events, coming up): each item's words, and the summary that counts them
  for (const ul of document.querySelectorAll('ul[data-draw]')) if (!ul.closest('[hidden]')) { const d = ul.closest('details'); out.lists[ul.dataset.draw] = { shown: !d || d.style.display !== 'none', summary: d ? d.querySelector('summary').textContent : null, items: [...ul.children].map((li) => li.textContent.replace('┊', '').trim()) } }
  out.chips = [...document.querySelectorAll('.ev-chips button')].map((b) => [b.textContent, b.getAttribute('aria-pressed')])
  for (const tb of document.querySelectorAll('tbody[data-draw]')) if (!tb.closest('[hidden]')) out.tables[tb.dataset.draw] = tb.textContent.replace(/\s+/g, ' ').trim()
  out.tiles = [...document.querySelectorAll('.tile .v, .tile .d, [data-draw^="lede-"]')].map((e) => e.textContent.trim())
  // each chart's one-line finding (DS-CHART-5) and the chart's accessible name, which a11y.js builds with it
  out.findings = {}
  for (const f of document.querySelectorAll('.finding[data-draw]')) if (!f.closest('[hidden]')) {
    const svg = f.closest('.card').querySelector('.chart svg'); out.findings[f.dataset.draw] = { text: f.textContent, label: svg ? svg.getAttribute('aria-label') : null }
  }
  // every scrolling table box a keyboard can reach and a screen reader can name (DS-A11Y-1)
  out.unreachable = [...document.querySelectorAll('.tbl')].filter((e) => e.getAttribute('role') !== 'region' || e.tabIndex !== 0 ||
    !((e.getAttribute('aria-label') || '').trim() || ((e.getAttribute('aria-labelledby') || '').split(' ').filter(Boolean).every((i) => document.getElementById(i)) && e.getAttribute('aria-labelledby')))).map((e) => e.outerHTML.slice(0, 80))
  out.sideways = document.documentElement.scrollWidth > document.documentElement.clientWidth   // DS-LAYOUT-1
  out.colors = dash.colors
  out.params = dash.params()
  return out
}

const snap = async (page) => {
  const all = { charts: {}, legends: {}, tables: {}, tips: {}, findings: {}, lists: {}, tiles: null }
  for (const t of TABS) {
    await page.click(`#tab-${t}`)
    await page.waitForTimeout(120)
    const p = await page.evaluate(probe)
    for (const k of ['charts', 'legends', 'tables', 'tips', 'findings', 'lists']) Object.assign(all[k], p[k])
    all.chips = all.chips || p.chips; all.sideways = all.sideways || p.sideways
    all.unreachable = p.unreachable
    all.tiles = all.tiles || p.tiles; all.colors = p.colors; all.params = p.params
  }
  return all
}

const open = async (ctx, errors) => {
  const page = await ctx.newPage()
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  page.on('pageerror', (e) => errors.push(String(e)))
  await page.goto(base + '/', { waitUntil: 'load' })
  await page.waitForFunction(() => window.dash && dash.data('monthly').status === 'ok', null, { timeout: 20000 })
  return page
}

;(async () => {
  const browser = await chromium.launch()
  const errors = [], out = {}
  try {
    const ctx = await browser.newContext({ viewport: { width: o.width || 1280, height: 900 } })
    const first = await ctx.newPage()
    await first.goto(base + '/api/status')   // same origin, so the stored theme and period filter are in place before the page draws
    await first.evaluate((o) => { localStorage.setItem('theme', o.theme || 'light'); if (o.params) localStorage.setItem('dash.params', JSON.stringify(o.params)) }, o)
    await first.close()
    let page = await open(ctx, errors)
    out.before = await snap(page)
    if (o.press) {
      const tab = await page.evaluate((c) => document.querySelector(`[data-draw="${c}"]`).closest('[role=tabpanel]').id, o.press)
      await page.click(`#tab-${tab}`)
      await page.click(`[data-draw="${o.press}"] .hidden-note button`)
      await page.waitForTimeout(150)
      out.focused = await page.evaluate(() => document.activeElement && document.activeElement.textContent)
      out.after = await snap(page)
      if (o.reload) { await page.close(); page = await open(ctx, errors); out.reloaded = await snap(page) }
      if (o.fresh) {
        const other = await browser.newContext({ viewport: { width: 1280, height: 900 } })
        const p2 = await open(other, errors); out.other = await snap(p2); await other.close()
      }
    }
    await ctx.close()
  } catch (e) {
    errors.push('harness: ' + String(e && e.stack || e))
  }
  await browser.close()
  out.errors = errors
  process.stdout.write(JSON.stringify(out))
})()
