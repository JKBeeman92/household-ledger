#!/usr/bin/env node
// The looking half of the ux-critique workflow, done once and in code so no agent drives a browser (AGENTS.md, "UX review").
// For each surface and URL: full-page screenshots at 390 and 1280 px, light and dark, with every <details> opened, and the checks a
// script can make better than a reader: console errors, sideways page scroll, touch targets under 44 px on a phone, and text below
// WCAG AA contrast. Writes <out>/manifest.json; the critics read it and the PNGs it names.
//
//   node .claude/workflows/ux-shots.js --url http://localhost:8010 --out /tmp/ux --surfaces '[{"key":"checkin","urls":["/checkin/2026-06"]}]'
//
// Read-only: it opens pages and toggles <details>; it never submits a form.
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d }
const BASE = arg('url', 'http://localhost:8010').replace(/\/$/, '')
const OUT = arg('out', '/tmp/ux-critique')
const SURFACES = JSON.parse(arg('surfaces', '[]'))
const VARIANTS = [[390, 'light'], [390, 'dark'], [1280, 'light'], [1280, 'dark']]
const LIMIT = 12   // per check and page: enough to show a pattern, small enough to read

// Runs in the page. Contrast walks up to the first opaque background; text over an image or gradient is not judged.
function checks(phone) {
  const lum = (c) => { const m = c.match(/[\d.]+/g); if (!m) return null; const [r, g, b] = m.slice(0, 3).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }); return 0.2126 * r + 0.7152 * g + 0.0722 * b }
  const alpha = (c) => { const m = c.match(/[\d.]+/g); return m && m.length > 3 ? +m[3] : 1 }
  const bgOf = (el) => { for (let e = el; e; e = e.parentElement) { const s = getComputedStyle(e); if (s.backgroundImage !== 'none') return null; if (alpha(s.backgroundColor) > 0.95) return s.backgroundColor } return 'rgb(255, 255, 255)' }
  const name = (el) => { const t = (el.getAttribute('aria-label') || el.textContent || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 50); return `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}${el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : ''} "${t}"` }
  const seen = (el) => { const r = el.getBoundingClientRect(), s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' }
  const de = document.documentElement
  const out = { sideways: de.scrollWidth > de.clientWidth ? `${de.scrollWidth}px wide in ${de.clientWidth}px` : null, small_targets: [], low_contrast: [] }
  if (phone) for (const el of document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab]')) {
    if (!seen(el) || el.closest('p, li, td, dd') && el.tagName === 'A' && getComputedStyle(el).display === 'inline') continue   // a link in running text is exempt (WCAG 2.5.5 inline)
    const r = el.getBoundingClientRect(); if (r.width < 44 || r.height < 44) out.small_targets.push(`${name(el)} ${Math.round(r.width)}x${Math.round(r.height)}`)
  }
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, { acceptNode: (n) => n.textContent.trim() ? 1 : 2 })
  const done = new Set()
  for (let n; (n = walk.nextNode());) {
    const el = n.parentElement; if (!el || done.has(el) || !seen(el)) continue; done.add(el)
    const s = getComputedStyle(el), bg = bgOf(el); if (!bg || alpha(s.color) < 0.1) continue
    const a = lum(s.color), b = lum(bg); if (a == null || b == null) continue
    const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05), px = parseFloat(s.fontSize), bold = +s.fontWeight >= 700
    const need = px >= 24 || (bold && px >= 18.66) ? 3 : 4.5
    if (ratio < need) out.low_contrast.push(`${name(el)} ${ratio.toFixed(2)}:1 (needs ${need}) ${s.color} on ${bg}`)
  }
  return out
}

;(async () => {
  fs.mkdirSync(OUT, { recursive: true })
  const browser = await chromium.launch(), manifest = { base: BASE, surfaces: {} }
  for (const s of SURFACES) {
    const pages = manifest.surfaces[s.key] = {}
    for (const u of s.urls || []) {
      const res = pages[u] = {}
      for (const [w, mode] of VARIANTS) {
        const ctx = await browser.newContext({ viewport: { width: w, height: 900 }, colorScheme: mode, deviceScaleFactor: 1 })
        const page = await ctx.newPage(), errors = []
        page.on('console', (m) => m.type() === 'error' && errors.push(m.text().slice(0, 200)))
        page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)))
        const v = `${w}-${mode}`
        try {
          await page.goto(BASE + u, { waitUntil: 'networkidle', timeout: 30000 })
          await page.evaluate(() => document.querySelectorAll('details').forEach((d) => { d.open = true }))
          await page.waitForTimeout(400)
          const png = path.join(OUT, `${s.key}--${u.replace(/[^a-z0-9]+/gi, '_').replace(/^_|_$/g, '') || 'root'}--${v}.png`)
          await page.screenshot({ path: png, fullPage: true })
          const c = await page.evaluate(checks, w < 600)
          res[v] = { png, console_errors: errors, sideways: c.sideways,
            small_targets: { count: c.small_targets.length, first: c.small_targets.slice(0, LIMIT) },
            low_contrast: { count: c.low_contrast.length, first: c.low_contrast.slice(0, LIMIT) } }
        } catch (e) { res[v] = { error: String(e).slice(0, 300), console_errors: errors } }
        await ctx.close()
      }
    }
  }
  await browser.close()
  fs.writeFileSync(path.join(OUT, 'manifest.json'), JSON.stringify(manifest, null, 1))
  const n = Object.values(manifest.surfaces).reduce((a, p) => a + Object.keys(p).length, 0)
  console.log(`${SURFACES.length} surface(s), ${n} page(s), ${n * VARIANTS.length} screenshot(s): ${path.join(OUT, 'manifest.json')}`)
})()
