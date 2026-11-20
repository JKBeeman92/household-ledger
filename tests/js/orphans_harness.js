#!/usr/bin/env node
// Settings → Chart appearance → Not in the data in a real browser (#235 UX review, DS-A11Y-5): deletes every orphan row from the
// keyboard, first row first, and prints as JSON where focus went after each, whether the page reloaded, and what #rowstatus said.
// With {"nojs": true} it instead presses the first Delete with JavaScript off and prints where the form post landed.
//
//   node tests/js/orphans_harness.js http://127.0.0.1:8235 '{"order": [0, -1, 0]}'
//
// order: which row to delete each time (0 the first, -1 the last), until the list is empty.
// slow: hold each delete this many ms and record where focus is while it is in flight ("during").
// fail: answer the first delete with a 403 whose detail ends in a period, and print what #rowstatus said and where focus went.
const { chromium } = require('playwright')

const [base, raw] = process.argv.slice(2)
const o = JSON.parse(raw || '{}')

;(async () => {
  const browser = await chromium.launch()
  const errors = []
  try {
    const ctx = await browser.newContext({ javaScriptEnabled: !o.nojs, viewport: { width: o.width || 1280, height: 900 } })
    const page = await ctx.newPage()
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
    page.on('pageerror', (e) => errors.push(String(e)))
    await page.goto(base + '/settings#appearance/orphans')
    if (o.nojs) {
      await Promise.all([page.waitForNavigation(), (async () => { await page.locator('#appearance-orphans-table button').first().focus(); await page.keyboard.press('Enter') })()])
      console.log(JSON.stringify({ errors, url: page.url(), flash: await page.locator('body').innerText(), rows: await page.locator('#appearance-orphans-table tbody tr').count() }))
      return
    }
    if (o.slow) await page.route('**/settings/appearance/delete', async (r) => { await new Promise((res) => setTimeout(res, o.slow)); await r.continue() })
    if (o.fail) {
      await page.route('**/settings/appearance/delete', (r) => r.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'Your form expired. Reload the page.' }) }))
      const btn = page.locator('#appearance-orphans-table tbody tr').first().locator('button')
      await btn.focus(); await page.keyboard.press('Enter')
      await page.waitForFunction(() => document.getElementById('rowstatus').textContent.startsWith('Not deleted'))
      const errs = errors.filter((e) => !e.includes('403'))   // the mocked 403 is logged by the browser
      console.log(JSON.stringify({ errors: errs, ...(await page.evaluate(() => ({ status: document.getElementById('rowstatus').textContent, cls: document.getElementById('rowstatus').className,
        focus: document.activeElement.getAttribute('aria-label'), ariaDisabled: document.activeElement.getAttribute('aria-disabled'),
        rows: document.querySelectorAll('#appearance-orphans-table tbody tr').length }))) }))
      return
    }
    await page.evaluate(() => { window.__same = true })   // gone if the page reloads
    const steps = []
    for (const which of o.order || []) {
      const rows = page.locator('#appearance-orphans-table tbody tr')
      const n = await rows.count()
      const btn = rows.nth(which < 0 ? n + which : which).locator('button')
      const label = await btn.getAttribute('aria-label')
      await btn.focus()
      await page.keyboard.press('Enter')
      let during = null
      if (o.slow) {
        await page.waitForTimeout(o.slow / 3)
        during = await page.evaluate(() => ({ tag: document.activeElement.tagName, ariaDisabled: document.activeElement.getAttribute('aria-disabled') }))
        await page.keyboard.press('Enter')   // a second press while in flight sends nothing more
      }
      await page.waitForFunction((k) => document.querySelectorAll('#appearance-orphans-table tbody tr').length === k, n - 1)
      await page.waitForFunction(() => document.activeElement && document.activeElement !== document.body)
      steps.push({ during, ...await page.evaluate((deleted) => {
        const a = document.activeElement, tab = document.getElementById('st-appearance-orphans')
        return { deleted, focus: a.getAttribute('aria-label') || a.id, focusTag: a.tagName, same: window.__same === true, status: document.getElementById('rowstatus').textContent,
                 count: tab ? tab.querySelector('.count').textContent : null, tab: !!tab, panel: !!document.getElementById('sp-appearance-orphans'), hash: location.hash,
                 selected: document.activeElement.getAttribute('aria-selected'), shown: !document.getElementById('sp-appearance-category').hidden }
      }, label) })
    }
    // the tab strip still moves with the arrow keys once a tab has gone
    await page.keyboard.press('End')
    const end = await page.evaluate(() => document.activeElement.id)
    console.log(JSON.stringify({ errors, steps, end }))
  } finally {
    await browser.close()
  }
})().catch((e) => { console.error(e); process.exit(1) })
