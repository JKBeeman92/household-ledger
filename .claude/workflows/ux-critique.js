export const meta = {
  name: 'ux-critique',
  description: 'UX critique of the UI surfaces a change touches against docs/DESIGN.md: screenshots and checks in code, one critic and one verifier per surface',
  whenToUse: 'Before opening or merging any PR that changes a template, app.css, settings.js, dash-shim.js or the dashboard page (AGENTS.md, "UX review"). Start the app on the sample household first (AGENTS.md, "Checking a change", step 2).',
  phases: [
    { title: 'Scope', detail: 'find the UI surfaces the diff touches; stop above max_surfaces' },
    { title: 'Shoot', detail: 'ux-shots.js: screenshots and mechanical checks, in code' },
    { title: 'Critique', detail: 'one critic per surface, all five lenses, from the shots' },
    { title: 'Verify', detail: 'one verifier per surface checks its high and medium findings' },
    { title: 'Consistency', detail: 'release runs only: the same thing named or built differently across pages' },
  ],
}

// Cost is the design constraint (AGENTS.md, "UX review"): about two agents per surface, and no agent drives a browser to look.
// args (all optional):
//   base          git ref the change is measured against (default 'origin/main': this PR's change, not a whole release)
//   url           where the app is running (default 'http://localhost:8010'; AUTH_DISABLED=true, sample household loaded)
//   shots         a writable directory for screenshots and manifest.json (default '/tmp/ux-critique')
//   surfaces      [{key, name, where, urls}] to skip Scope and review exactly these; urls are paths at `url`, with #hash for tabs
//   max_surfaces  Scope stops and returns the list above this many (default 8), so a big run is the owner's decision
//   findings      [{surface: {key, name, where, urls}, findings: [...]}] to skip Critique and only verify (e.g. a stopped run's)
//   findings_file the same, as a path to a JSON file (with surfaces set to its surfaces): each verifier reads its own surface's
//                 high and medium findings from it, so a big list never passes through the script; returns raw verdicts by n
//   release       true: also run one cross-page consistency pass over everything confirmed
//   models        {scope, critic, verify} tier overrides
const A = args || {}
const M = { scope: 'haiku', critic: 'sonnet', verify: 'sonnet', ...(A.models || {}) }
const BASE = A.base || 'origin/main', URL = A.url || 'http://localhost:8010', SHOTS = A.shots || '/tmp/ux-critique', MAX = A.max_surfaces || 8
const MANIFEST = `${SHOTS}/manifest.json`

const CONTEXT = `You are reviewing the UX of Bearings, a self-hosted app a household uses ONCE A MONTH to check in on its money (README, "What this is, and isn't"). The repository is the current working directory. The app is running at ${URL} with AUTH_DISABLED (you are an editor), on the synthetic sample household.

STRICTLY READ-ONLY: never submit a form, never POST, never edit files, never change git state.

How to look, cheaply: the screenshots are already taken. ${MANIFEST} has, for each surface and URL, four variants (390-light, 390-dark, 1280-light, 1280-dark), each with the path of a full-page PNG (every <details> opened) and machine checks: console_errors, sideways (horizontal page scroll), small_targets (under 44px on the phone) and low_contrast (computed WCAG ratio). Read only your surface's entry (python3 -c or jq), and Read the PNGs you need: usually 390-light and 1280-dark, the others only to check something. The checks are measurements: trust them, and turn the ones that matter into findings with the rule they break. Do NOT take screenshots or open a browser to look. Only for keyboard and focus behaviour may you run a short Playwright script (require('playwright'), no screenshots). Read the templates, app/web/static/app.css and the JS behind the surface; quote file:line for every source-based claim.

THE LAW: docs/DESIGN.md is the design system the app is judged by, with numbered rules (DS-SHEET-1, DS-WORD-1, ...). Read the sections that bear on your surface. AGENTS.md ("Long lists") adds the house rules for growing lists.
- A finding that breaks a rule cites its id in "rule". A finding that breaks no rule is a judgement: set "rule" to "judgement" and hold it to a higher bar of evidence.
- Judge against the rules and the token contract, never against the shipped kit's values: a different colour, typeface, glyph or flourish word is a kit's right (DESIGN.md section 7).
- DESIGN.md section 8 ("Not yet in keeping") lists known debts. Report one only if this surface's change touched it or made it worse.

Report only concrete, observable problems a household member would hit, rule breaches, or WCAG 2.1 AA failures, each with evidence and a specific fix. No generic advice, no praise. Severity: high = blocks or misleads; medium = real friction; low = polish.`

const LENSES = `Look through all five lenses, in one pass:
1. FLOW: findability, what to do next, task completion, defaults, error recovery, empty states, where a save lands and what it says.
2. COPY & HIERARCHY: microcopy clarity and length, jargon, one name per concept, number formatting, what draws the eye, density at phone width.
3. ACCESSIBILITY (WCAG 2.1 AA): keyboard operation (ARIA tab pattern where tabs are used), focus visibility and order, accessible names, nothing by colour alone, chart alternatives, contrast in light and dark, 44px phone targets.
4. RESPONSIVE & BOTH MODES: overflow or clipping at 390px, sideways scroll, day and night legibility (DS-LAYOUT-*, DS-A11Y-3), the house rules for long lists.
5. DESIGN SYSTEM: the surface's source against the rules: tokens only (no typed colour, typeface or size), sheets, tabs, buttons, stamps, tables, tiles and charts built as their rules say, fixed words, glyphs beside labels; and anything that only works with the shipped kit's values. Every finding from this lens cites a rule id.`

const SURFACE = { type: 'object', properties: {
  key: { type: 'string' }, name: { type: 'string' }, where: { type: 'string', description: 'what to open and the source files behind it' },
  urls: { type: 'array', items: { type: 'string' }, description: 'paths at the app, e.g. "/checkin/2026-06", "/#quarter", "/settings#mapping/debts"' },
}, required: ['key', 'name', 'where', 'urls'] }
const SURFACES_SCHEMA = { type: 'object', properties: { surfaces: { type: 'array', items: SURFACE } }, required: ['surfaces'] }
const FINDINGS = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: {
  title: { type: 'string' }, severity: { type: 'string', enum: ['high', 'medium', 'low'] }, rule: { type: 'string', description: 'the docs/DESIGN.md rule id this breaks (DS-...), a WCAG criterion, or "judgement"' }, evidence: { type: 'string' }, fix: { type: 'string' }, lens: { type: 'string' },
}, required: ['title', 'severity', 'rule', 'evidence', 'fix'] } } }, required: ['findings'] }
const VERDICTS = { type: 'object', properties: { verdicts: { type: 'array', items: { type: 'object', properties: {
  n: { type: 'integer', description: 'the finding number as given' }, refuted: { type: 'boolean' }, reason: { type: 'string' }, severity: { type: 'string', enum: ['high', 'medium', 'low'] },
}, required: ['n', 'refuted', 'reason', 'severity'] } } }, required: ['verdicts'] }
const rank = { high: 3, medium: 2, low: 1 }

// ---- Scope ----
phase('Scope')
const FILE = A.findings_file
let items = FILE ? A.surfaces.map((s) => ({ surface: s, findings: 'file' })) : A.findings ? A.findings.map((x) => ({ surface: x.surface, findings: x.findings })) : null
let surfaces = items ? items.map((x) => x.surface) : A.surfaces
if (!surfaces) {
  const scoped = await agent(`Find the user-facing surfaces a change touches in this repository. Run \`git diff --stat ${BASE}...HEAD\` and \`git diff ${BASE}...HEAD -- app/web app/routes app/checkin\`, plus uncommitted changes (\`git diff\`, \`git status\`). For each page, tab, sub-tab, panel, card or chart whose look, copy or behaviour changed, return one surface: a short key, a name a reviewer understands, "where" (what to open and the source files behind it) and "urls" (paths at the app: dashboard tabs as "/#<tab>", settings sub-tabs as "/settings#<tab>/<sub>", the check-in as "/checkin/2026-06"). Group related changes on one page into ONE surface: fewer, larger surfaces are better. Return an empty list if nothing user-facing changed. Read-only.`,
    { label: 'scope', phase: 'Scope', schema: SURFACES_SCHEMA, model: M.scope, effort: 'low' })
  surfaces = scoped ? scoped.surfaces : []
  if (surfaces.length > MAX) {
    log(`${surfaces.length} surfaces is above max_surfaces (${MAX}): stopping before any critic runs`)
    return { stopped: `Scope found ${surfaces.length} surfaces, above max_surfaces (${MAX}). Re-run with surfaces set to the ones that matter, or max_surfaces raised, on the owner's say.`, surfaces }
  }
}
log(`${surfaces.length} surface(s): ${surfaces.map((s) => s.key).join(', ') || 'none'}`)
if (!surfaces.length) return { confirmed: [], low: [], dropped: [], plan: 'No user-facing surface changed.' }

// ---- Shoot: one cheap agent runs the script; the looking is code, not tokens ----
phase('Shoot')
const shot = await agent(`Run exactly this one command from the repository root and return its last line of output verbatim (or the error, if it fails). Do nothing else.\n\nnode .claude/workflows/ux-shots.js --url '${URL}' --out '${SHOTS}' --surfaces '${JSON.stringify(surfaces.map((s) => ({ key: s.key, urls: s.urls || [] }))).replace(/'/g, "'\\''")}'`,
  { label: 'shoot', phase: 'Shoot', model: 'haiku', effort: 'low' })
log(`shots: ${String(shot || 'no output').trim().split('\n').pop()}`)

// ---- Critique and verify, one agent each per surface, pipelined ----
const results = await pipeline(
  items || surfaces.map((s) => ({ surface: s, findings: null })),
  (it) => it.findings ? it : agent(`${CONTEXT}\n\nSURFACE: ${it.surface.name} (manifest key "${it.surface.key}")\nWHERE: ${it.surface.where}\n\n${LENSES}\n\nAn empty list is a valid answer. Merge duplicates yourself: one finding per underlying problem.`,
    { label: `critic:${it.surface.key}`, phase: 'Critique', schema: FINDINGS, model: M.critic }).then((r) => ({ surface: it.surface, findings: r ? r.findings : [] })),
  (it) => {
    const s = it.surface
    if (it.findings === 'file') return agent(`${CONTEXT}\n\nYou are the skeptic for ONE surface: ${s.name} (manifest key "${s.key}"; ${s.where}). Its findings are in ${FILE}: the entry whose surface.key is "${s.key}". Take its findings whose severity is high or medium, numbered n = 0, 1, 2 ... in file order counting only those. For EACH, try to REFUTE it: reproduce it from the source, the manifest's checks and the screenshots. Refute if it does not reproduce, if the cited rule does not say what the claim needs (read the rule in docs/DESIGN.md), if it faults a kit's value and not a rule, if it is documented intended behaviour that is still reasonable, if it is generic advice, or if the fix would break a rule. If it reproduces, keep it at the severity the evidence supports. When uncertain after checking, refute. Return one verdict per finding, by its n.`,
      { label: `verify:${s.key}`, phase: 'Verify', schema: VERDICTS, model: M.verify }).then((r) => ({ surface: s.key, verdicts: r ? r.verdicts : null, confirmed: [], low: [], dropped: [] }))
    const fs = it.findings || []
    const check = fs.map((f, i) => ({ ...f, n: i })).filter((f) => rank[f.severity] >= 2)
    const low = fs.filter((f) => rank[f.severity] < 2).map((f) => ({ ...f, surface: s.key, verified: false }))
    if (!check.length) return { confirmed: [], low, dropped: [] }
    return agent(`${CONTEXT}\n\nYou are the skeptic for ONE surface: ${s.name} (manifest key "${s.key}"; ${s.where}). Below are its high and medium findings. For EACH, try to REFUTE it: reproduce it from the source, the manifest's checks and the screenshots. Refute if it does not reproduce, if the cited rule does not say what the claim needs (read the rule in docs/DESIGN.md), if it faults a kit's value and not a rule, if it is documented intended behaviour that is still reasonable, if it is generic advice, or if the fix would break a rule. If it reproduces, keep it at the severity the evidence supports. When uncertain after checking, refute. Return one verdict per finding, by its n.\n\nFINDINGS:\n${JSON.stringify(check, null, 1)}`,
      { label: `verify:${s.key}`, phase: 'Verify', schema: VERDICTS, model: M.verify }).then((r) => {
      const by = new Map(((r && r.verdicts) || []).map((v) => [v.n, v]))
      const out = check.map((f) => { const v = by.get(f.n); return { ...f, surface: s.key, severity: v && !v.refuted ? v.severity : f.severity, survives: !!v && !v.refuted, note: v ? v.reason : 'no verdict returned' } })
      return { confirmed: out.filter((f) => f.survives), low, dropped: out.filter((f) => !f.survives) }
    })
  },
)

const ok = results.filter(Boolean)
const confirmed = ok.flatMap((r) => r.confirmed).sort((a, b) => rank[b.severity] - rank[a.severity])
const low = ok.flatMap((r) => r.low), dropped = ok.flatMap((r) => r.dropped)
if (ok.length < surfaces.length) log(`${surfaces.length - ok.length} surface(s) returned nothing (an agent failed): not covered`)
log(`${confirmed.length} high/medium confirmed, ${dropped.length} refuted, ${low.length} low passed through unverified`)

// ---- Consistency: release runs only, one agent ----
let consistency = null
if (A.release) {
  phase('Consistency')
  consistency = await agent(`${CONTEXT}\n\nThis is a release run. Per-surface findings are below. Look ACROSS the surfaces for what no single critic could see: the same concept named or styled differently on different pages, a flow that spans pages, first run against a returning household. Check each gap in the source and screenshots before listing it; do not repeat a finding already listed.\n\nSURFACES: ${JSON.stringify(surfaces.map((s) => s.key + ': ' + s.name))}\nFINDINGS: ${JSON.stringify(confirmed.concat(low).map((f) => `${f.surface} [${f.severity} ${f.rule}] ${f.title}`))}`,
    { label: 'consistency', phase: 'Consistency', schema: FINDINGS, model: M.critic })
}

const line = (f) => `- **${f.severity}** ${f.rule} · ${f.surface} · ${f.title}. Fix: ${f.fix}`
const plan = ['## Confirmed (high and medium, verified)', ...confirmed.map(line),
  ...(consistency && consistency.findings.length ? ['', '## Across pages (release pass, not separately verified)', ...consistency.findings.map((f) => line({ ...f, surface: 'across' }))] : []),
  '', '## Low (not verified: fix if small, else file an issue)', ...low.map(line)].join('\n')
if (FILE) return { verdicts: ok.map((r) => ({ surface: r.surface, verdicts: r.verdicts })) }
return { confirmed, low, across: consistency ? consistency.findings : [], dropped: dropped.map((f) => ({ surface: f.surface, title: f.title, note: f.note })), plan }
