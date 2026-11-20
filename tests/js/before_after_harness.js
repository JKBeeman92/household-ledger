#!/usr/bin/env node
// Before and after a life event, and the new normal (#159), in a real browser: what a phone and a desk show of the comparison card,
// the Last month tab's emergency fund, Detail key and Average note, and the Settings offer on a life event's row. Prints JSON for
// tests/test_before_after.py. Read-only: the Settings row is opened for editing and cancelled, never saved.
//
//   node tests/js/before_after_harness.js http://127.0.0.1:8159 '{"width": 390, "theme": "light"}'
const { chromium } = require('playwright')

const [base, raw] = process.argv.slice(2)
const o = JSON.parse(raw || '{}')

function overview() {
  const W = document.documentElement.clientWidth
  const tables = [...document.querySelectorAll('#ba-card .tbl')].filter((t) => !t.closest('[hidden]')).map((t) => {
    const box = t.getBoundingClientRect()
    return {
      heads: [...t.querySelectorAll('thead th')].map((th) => th.textContent),
      overflow: t.scrollWidth - t.clientWidth,   // a sideways swipe inside the box
      changeOut: [...t.querySelectorAll('tbody tr')].map((tr) => tr.lastElementChild.getBoundingClientRect().right - box.right).filter((d) => d > 1).length,
      rows: [...t.querySelectorAll('tbody tr')].map((tr) => [...tr.children].map((c) => c.textContent)),
    }
  })
  const seg = [...document.querySelectorAll('#ba-n button')].map((b) => ({ n: b.dataset.n, disabled: b.disabled, ariaDisabled: b.getAttribute('aria-disabled') }))
  const sel = document.querySelector('#ba-event')
  return { pageOverflow: document.documentElement.scrollWidth - W, tables, seg, options: [...sel.options].map((x) => x.text), span: document.querySelector('[data-draw="ba-span"]').textContent }
}

function lastmonth() {
  const ef = document.querySelector('[data-draw="lm-ef"]')
  const keyEl = document.querySelector('[data-draw="lm-cats-key"]')
  const cats = [...document.querySelectorAll('[data-draw="lm-cats"] tr')].map((tr) => {
    const td = tr.cells[6]
    return { category: tr.cells[0].textContent, suggest: td.textContent, mark: !!td.querySelector('.key-mark[aria-hidden="true"]'), hidden: (td.querySelector('.visually-hidden') || {}).textContent || null }
  })
  return {
    pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ef: { dts: [...ef.querySelectorAll('dt')].map((d) => d.textContent), stamps: [...ef.querySelectorAll('.status-chip')].map((s) => s.textContent),
      downs: ef.querySelectorAll('.down').length, text: ef.textContent, links: [...ef.querySelectorAll('a')].map((a) => [a.textContent, a.getAttribute('href')]) },
    key: [...keyEl.children].map((li) => ({ mark: (li.querySelector('.key-mark') || {}).textContent || null, text: li.textContent })),
    cats,
    avgSince: document.querySelector('[data-draw="lm-avg-since"]').textContent,
    detailSince: document.querySelector('#lm-t-cats [data-draw="lm-since"]').textContent,
  }
}

;(async () => {
  const browser = await chromium.launch()
  const errors = [], out = {}
  try {
    const ctx = await browser.newContext({ viewport: { width: o.width || 1280, height: 900 } })
    const page = await ctx.newPage()
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
    page.on('pageerror', (e) => errors.push(String(e)))
    await page.goto(base + '/api/status')
    await page.evaluate((t) => { localStorage.setItem('theme', t); localStorage.removeItem('dash.params') }, o.theme || 'light')
    await page.goto(base + '/', { waitUntil: 'load' })
    await page.waitForFunction(() => window.dash && dash.data('monthly').status === 'ok', null, { timeout: 20000 })
    await page.click('#tab-overview'); await page.waitForTimeout(150)
    out.overview = await page.evaluate(overview)
    await page.click('#ba-card [data-show="ba-t-pct"]'); await page.waitForTimeout(100)
    out.overviewPct = await page.evaluate(overview)
    await page.click('#tab-lastmonth'); await page.waitForTimeout(150)
    out.lastmonth = await page.evaluate(lastmonth)
    if (o.settings) {   // the offer on an existing move row, opened for editing (#159 review)
      await page.goto(base + '/settings#events', { waitUntil: 'load' })
      const row = page.locator('#sp-events tr', { hasText: o.settings })
      await row.locator('button', { hasText: 'Edit' }).click()
      const read = () => page.evaluate(() => { const h = document.getElementById('new-normal-hint'), s = document.querySelector('select[name="new_normal"]');
        return h && { text: h.textContent, role: h.getAttribute('role'), live: h.getAttribute('aria-live'), describedby: s.getAttribute('aria-describedby'), value: s.value } })
      out.settings = { opened: await read() }
      await page.selectOption('select[name="new_normal"]', 'yes'); out.settings.yes = await read()
      await page.selectOption('select[name="new_normal"]', 'no'); await page.selectOption('select[name="kind"]', 'job'); out.settings.job = await read()
      await page.locator('#sp-events button', { hasText: 'Cancel' }).first().click()
    }
    await ctx.close()
  } catch (e) {
    errors.push('harness: ' + String(e && e.stack || e))
  }
  await browser.close()
  out.errors = errors
  process.stdout.write(JSON.stringify(out))
})()
