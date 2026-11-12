export const meta = {
  name: 'ux-critique',
  description: 'UX critique of every UI surface a change touches against docs/DESIGN.md: five lenses per surface, 3-skeptic verification, a fix plan',
  whenToUse: 'Before opening or merging any PR that changes a template, app.css, settings.js, dash-shim.js or the dashboard page (AGENTS.md, "UX review"). Start the app on the sample household first (AGENTS.md, "Checking a change", step 2).',
  phases: [
    { title: 'Scope', detail: 'find the UI surfaces the diff touches' },
    { title: 'Critique', detail: 'each surface through five lenses, read-only, Playwright + source' },
    { title: 'Merge', detail: 'one merger per surface collapses duplicates' },
    { title: 'Verify', detail: '3 skeptics per finding try to refute it' },
    { title: 'Synthesize', detail: 'completeness critic + prioritized fix plan' },
  ],
}

// args (all optional):
//   base      git ref the change is measured against (default 'origin/main')
//   url       where the app is running (default 'http://localhost:8010'; AUTH_DISABLED=true, sample household loaded)
//   shots     a writable directory for screenshots (default '/tmp/ux-critique')
//   surfaces  [{key, name, where}] to skip Scope and review exactly these
//   skeptics  verifiers per finding (default 3; a finding survives with a majority)
const A = args || {}
const BASE = A.base || 'origin/main', URL = A.url || 'http://localhost:8010', SHOTS = A.shots || '/tmp/ux-critique', K = A.skeptics || 3

const CONTEXT = `You are reviewing the UX of Bearings, a self-hosted app a household uses ONCE A MONTH to check in on its money (README, "What this is, and isn't"). The repository is the current working directory. The app is running at ${URL} with AUTH_DISABLED (you are an editor), normally on the synthetic sample household.

STRICTLY READ-ONLY: never submit a form, never POST, never edit files, never change git state. Opening <details>, switching tabs and toggling client-side controls is fine.

How to look: Chromium + Playwright are installed (require('playwright')). Write a small node script under ${SHOTS}/<your-label>/ (mkdir -p) that opens the page at 390x900 (phone) and 1280x900 (desktop), colorScheme 'light' and 'dark', records console errors and screenshots full pages at deviceScaleFactor 1; Read the PNGs. Read the templates, app/web/static/app.css, the dashboard's inline <style> and the JS behind the surface; use the keyboard (Tab, arrows) and page.evaluate to test focus and operation. Quote file:line for every source-based claim.

THE LAW: read docs/DESIGN.md first, all of it. It is the design system the app is judged by: numbered rules (DS-SHEET-1, DS-WORD-1, ...) for the brand, words, tokens, components, patterns and accessibility. AGENTS.md ("Long lists") adds the house rules for growing lists.
- A finding that breaks a rule cites its id in "rule". A finding that breaks no rule is a judgement: set "rule" to "judgement" and hold it to a higher bar of evidence.
- Judge against the rules and the token contract, never against the shipped kit's values: a different colour, typeface, glyph or flourish word is a kit's right (DESIGN.md section 7), not a defect.
- DESIGN.md section 8 ("Not yet in keeping") lists known debts. Report one only if this surface's change touched it or made it worse.

Report only concrete, observable problems a household member would hit, rule breaches, or WCAG 2.1 AA failures, each with evidence and a specific fix. No generic advice, no praise. Severity: high = blocks or misleads; medium = real friction; low = polish.`

const LENSES = [
  { key: 'flow', text: 'USABILITY & FLOW: findability, what to do next, task completion, defaults, error recovery, empty states, where a save lands and what it says.' },
  { key: 'copy', text: 'COPY & VISUAL HIERARCHY: microcopy clarity and length, jargon, the same concept named the same way everywhere, number formatting, what draws the eye, density at phone width.' },
  { key: 'a11y', text: 'ACCESSIBILITY (WCAG 2.1 AA): keyboard operation (ARIA tab pattern where tabs are used), focus visibility and order, accessible names, nothing conveyed by colour alone, chart alternatives, contrast in light AND dark (compute from getComputedStyle), 44px touch targets on phone.' },
  { key: 'layout', text: 'RESPONSIVE & BOTH MODES: overflow or clipping at 390px, horizontal page scroll (scrollWidth vs clientWidth), day and night legibility (DS-LAYOUT-*, DS-A11Y-3), and the house rules for long lists.' },
  { key: 'system', text: 'DESIGN-SYSTEM CONFORMANCE: go through docs/DESIGN.md rule by rule against this surface. Tokens only, no typed colour, typeface or size (grep the diff and the surface\'s source); sheets, tabs, buttons, stamps, tables, tiles and charts built as their rules say; fixed words used and flourish read from `words`; glyphs beside labels. Then swap the kit: restart is not needed, so compare the surface under the shipped kit and under examples/brand-kits/red-book mounted as BRAND_DIR if the app was started that way, or reason from the source, and report anything that only works with the shipped kit\'s values. Every finding from this lens cites a rule id.' },
]

const SURFACES_SCHEMA = { type: 'object', properties: { surfaces: { type: 'array', items: { type: 'object', properties: {
  key: { type: 'string' }, name: { type: 'string' }, where: { type: 'string', description: 'URLs (with hashes) to open and the source files behind it' },
}, required: ['key', 'name', 'where'] } } }, required: ['surfaces'] }
const FINDINGS = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: {
  title: { type: 'string' }, severity: { type: 'string', enum: ['high', 'medium', 'low'] }, rule: { type: 'string', description: 'the docs/DESIGN.md rule id this breaks (DS-...), a WCAG criterion, or "judgement"' }, evidence: { type: 'string' }, fix: { type: 'string' }, lens: { type: 'string' },
}, required: ['title', 'severity', 'rule', 'evidence', 'fix', 'lens'] } } }, required: ['findings'] }
const VERDICT = { type: 'object', properties: {
  refuted: { type: 'boolean' }, reason: { type: 'string' }, severity: { type: 'string', enum: ['high', 'medium', 'low'] },
}, required: ['refuted', 'reason', 'severity'] }

phase('Scope')
let surfaces = A.surfaces
if (!surfaces) {
  const scoped = await agent(`Find the user-facing surfaces a change touches in this repository. Run \`git diff --stat ${BASE}...HEAD\` and \`git diff ${BASE}...HEAD -- app/web app/routes app/checkin\`, plus uncommitted changes (\`git diff\`, \`git status\`). For each page, tab, sub-tab, panel, card or chart whose look, copy or behaviour changed, return one surface: a short key, a name a reviewer understands, and "where": the URL(s) at ${URL} (with #hash for dashboard tabs and settings sub-tabs) and the source files behind it. Group small related changes on one page into one surface; return an empty list if nothing user-facing changed. Read-only.`,
    { label: 'scope', phase: 'Scope', schema: SURFACES_SCHEMA })
  surfaces = scoped ? scoped.surfaces : []
}
log(`${surfaces.length} surface(s): ${surfaces.map((s) => s.key).join(', ') || 'none'}`)
if (!surfaces.length) return { confirmed: [], dropped: [], plan: 'No user-facing surface changed.' }

const rank = { high: 3, medium: 2, low: 1 }
const results = await pipeline(
  surfaces,
  (s) => parallel(LENSES.map((l) => () => agent(
    `${CONTEXT}\n\nSURFACE: ${s.name}\nWHERE: ${s.where}\n\nYOUR LENS: ${l.text}\n\nReview only this surface through only this lens; an empty list is a valid answer. Screenshot folder label: "${s.key}-${l.key}".`,
    { label: `${s.key}:${l.key}`, phase: 'Critique', schema: FINDINGS }))),
  (lensResults, s) => {
    const all = lensResults.filter(Boolean).flatMap((r) => r.findings)
    if (!all.length) return { findings: [] }
    return agent(`Merge UX findings for one surface (${s.name}) from five critics:\n${JSON.stringify(all, null, 1)}\n\nCollapse findings about the same underlying problem (strongest evidence, most specific fix, highest severity the evidence justifies, lens names joined, and the rule id kept: a rule id beats "judgement"). Keep distinct problems; invent none. Do not browse.`,
      { label: `merge:${s.key}`, phase: 'Merge', schema: FINDINGS, effort: 'low' })
  },
  (merged, s) => parallel((merged ? merged.findings : []).map((f, i) => () =>
    parallel(Array.from({ length: K }, (_, k) => () => agent(
      `${CONTEXT}\n\nSkeptic #${k + 1} on ONE claimed problem on ${s.name} (${s.where}).\nCLAIM: ${f.title}\nRULE: ${f.rule}\nSEVERITY: ${f.severity}\nEVIDENCE: ${f.evidence}\nFIX: ${f.fix}\n\nTry to REFUTE it by reproducing it yourself. Refute if it does not reproduce, if the cited rule does not say what the claim needs it to say (read the rule in docs/DESIGN.md), if it faults a kit's value and not a rule, if it is documented intended behaviour that is still reasonable, if it is generic advice, or if the fix breaks a rule. If it reproduces, keep it and set the severity the evidence supports. When uncertain after checking, refute. Screenshot label: "${s.key}-v${i}-${k}".`,
      { label: `verify:${s.key}#${i}.${k}`, phase: 'Verify', schema: VERDICT }))).then((vs) => {
      const ok = vs.filter(Boolean), kept = ok.filter((v) => !v.refuted)
      const sev = kept.length ? kept.map((v) => v.severity).sort((a, b) => rank[b] - rank[a])[Math.floor((kept.length - 1) / 2)] : f.severity
      return { ...f, surface: s.key, survives: kept.length * 2 > ok.length && ok.length > 0, severity: sev, skeptic_notes: ok.map((v) => (v.refuted ? 'REFUTED: ' : 'kept: ') + v.reason) }
    }))),
)

const verified = results.filter(Boolean).flat().filter(Boolean)
const confirmed = verified.filter((f) => f.survives), dropped = verified.filter((f) => !f.survives)
log(`${verified.length} findings after merging; ${confirmed.length} survived verification, ${dropped.length} refuted`)

phase('Synthesize')
const plan = await agent(`${CONTEXT}\n\nYou are the UX lead. CONFIRMED findings (survived ${K}-skeptic verification):\n${JSON.stringify(confirmed, null, 1)}\n\nREFUTED (context only, do not resurrect):\n${JSON.stringify(dropped.map((f) => f.surface + ': ' + f.title))}\n\n1. COMPLETENESS: what was missed across the surfaces together: the same concept named or styled differently on different pages, a flow that spans pages, first run vs a returning household. Verify each gap in the browser (read-only) before listing it.\n2. PLAN: a prioritized, de-duplicated fix plan in markdown, grouped by file: severity, rule id, surface, the problem in one sentence, the exact change. Mark items that need the owner's product decision.\n3. THE LAW ITSELF: where a confirmed judgement shows docs/DESIGN.md is silent or wrong, propose the rule to add or amend, with its id. End with what was checked and found fine.`,
  { label: 'synthesize', phase: 'Synthesize' })

return { confirmed, dropped: dropped.map((f) => ({ surface: f.surface, title: f.title, notes: f.skeptic_notes })), plan }
