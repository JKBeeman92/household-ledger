# The Bearings design system

Bearings is a monthly check-in for household money. A household sits down once a month, sees where it stands, decides what to
change, and keeps in view where it is heading. The brand says the same thing in the language of old navigation: a fix on a
chart, and a course drawn from it.

This file is the law the app's pages are judged by. It has three layers, and it matters which layer a rule belongs to:

| Layer | What it holds | Who may change it |
|---|---|---|
| **The rules** | Layout, components, patterns, plain control words, accessibility | This file, by a pull request |
| **The kit contract** | The tokens and files a brand kit must or may supply | This file; every kit must satisfy it |
| **A kit** | Values: colours, typefaces, marks, glyphs, flourish words | Whoever mounts a `BRAND_DIR` (`docs/BRANDING.md`) |

Bearings is the kit that ships. A page is never judged against Bearings' colours or its chart symbols, only against the rules
and the contract, so that The Red Book or any household's own kit is as lawful as the default.

Every rule has an id (`DS-SHEET-1`). The UX critique (`AGENTS.md`, "UX review") cites the id of the rule a finding breaks; a
finding that breaks no rule is a judgement, and is marked as one. Examples use the Gamgees of Bagshot Row.

## 1. The brand

- **DS-BRAND-1** The product is **Bearings**. The household's own name is shown beside it, never in place of it.
- **DS-BRAND-2** One idea runs through everything: *a fix and a course*. Where the household stands is a fix; where it is
  heading is a course; a goal is a waypoint. Nothing is added to a page that this idea cannot explain.
- **DS-BRAND-3** The feeling is a navigator's chart table: paper, ruled lines, measured marks. It is calm and exact. It is not
  playful, and it is not a pirate's map: no rope, no anchors as ornament, no parchment stains.

### The mark

The mark is a round seal cut from a portolan chart: a fix, rhumb lines fanning from it, the chosen bearing as a brass needle
reaching a waypoint, and a chart border.

| File | What | Use |
|---|---|---|
| `mark.svg`, `mark-dark.svg` | The Portolan seal, day and night | 48 px and up: sign-in card, dashboard masthead |
| `mark-small.svg`, `mark-small-dark.svg` | The small cut: ring, fix, needle, waypoint | Under 48 px: header, browser tab |
| `backdrop.svg` | The wind lattice | Backgrounds only |

- **DS-MARK-1** The full seal is never drawn under 48 px; below that the small cut is used.
- **DS-MARK-2** The lattice is a background and never stands as a logo.
- **DS-MARK-3** A mark carries its own ground, so it reads on any surface. Nothing is laid over it and it is not recoloured.

## 2. Words

- **DS-WORD-1** A control says plainly what it does: *Check-in*, *Close a month*, *Rebuild*, *Reconciled*. No kit may change
  the words a person acts on, and no nautical term replaces a plain one on a control, a heading or an error.
- **DS-WORD-2** Flourish belongs to the kit and lives in `words.json`. The keys are `name`, `tagline`, `dashboard_eyebrow`,
  `checkin_eyebrow`, `login_line` and `footer`, and there are no others: the eyebrows of working pages (Close a month, Settings,
  Reviews) are plain and typed. A page reads the six from `words`; it never types them.
- **DS-WORD-3** The same thing has the same name on every page. *Budget month*, *check-in*, *close a month*, *review*,
  *rebuild* and *reconciled* are fixed terms. The act of having the model read a month back is **Write review** (a second
  time, **Write review again**), and the page that chooses the model is **Review provider**, everywhere: never "generate",
  "provider settings" or "write one".
- **DS-WORD-4** Sentences are short and say what happened and what to do next. An error names the cause and the remedy; it
  does not apologise. No exclamation marks.
- **DS-WORD-5** Sentence case everywhere. Capitals with letter-spacing are for eyebrows and stamps only.
- **DS-WORD-6** Money is written with the household's currency and locale (`fmt`); dates as `1 Nov 2026`; a month as
  `November 2026` in a heading and `Nov 2026` in a table or on a chart. A missing figure is an em dash with the reason nearby,
  never a zero. What a person types into a field stays a plain number; what the page shows them is formatted.
  On the server the filters do it (`app/formatting.py`): `|money` (`money(2)` for cents, `money(signed=True)` for a change),
  `|date`, `|datetime`, `|month`, `|month_short`, `|share`, `|number`, and `|plain` for a field's value; a flash uses
  `month_text()`. In script, `dash.fmt` and `HL.mo`. Both read the same CLDR data, so a figure reads alike on either side.

## 3. Foundations (the kit contract)

A kit's `theme.css` defines every token below for night (`:root`) and day (`:root[data-theme="light"]`). `app.css` and the
pages use tokens only.

- **DS-TOKEN-1** No page, stylesheet or script names a colour, a typeface or a font size of its own. (`tests/test_design_system.py`.)
- **DS-TOKEN-2** One vocabulary. The names below are the only token names; no page keeps aliases for them.

### Colour roles

| Token | Role |
|---|---|
| `--surface-100` | Chart paper: the page |
| `--surface-200` | A sheet: panels, cards |
| `--surface-300` | Shoal: inputs, resting tabs, hover, popovers |
| `--surface-000` | The deepest ground: print |
| `--line`, `--line-strong` | A ruled line inside a sheet; a border that must be seen (3:1) |
| `--ink`, `--ink-muted`, `--ink-faint` | Text, secondary text, footer text; sheet borders are `--ink` |
| `--accent`, `--accent-hover`, `--accent-soft`, `--on-accent` | Actions, links, the current page, plotted data |
| `--accent-2`, `--accent-2-deep`, `--accent-2-text`, `--on-accent-2` | Destinations only: goals, waypoints, reference lines, eyebrows |
| `--success`, `--warning`, `--danger`, `--info`, `--on-status` | Verdicts |
| `--series-1` … `--series-8` | Chart series, in this order on every chart |
| `--chart-grid`, `--chart-axis`, `--chart-reference`, `--graticule` | Chart furniture, and the page's grid |
| `--focus` | The focus ring |

- **DS-COLOR-1** The accent is never a verdict. A kit's `--accent` and `--accent-2` should stand clear of its `--success`,
  `--warning` and `--danger`, so that a selected tab does not read as good news.
- **DS-COLOR-2** `--accent-2` marks a destination and nothing else. It is not used for emphasis.
- **DS-COLOR-3** Every pair the app draws meets WCAG 2.1 AA: 4.5:1 for text, 3:1 for borders, focus rings and chart marks.
  The pairs are listed once, in `app/brand_check.py`; a new combination is added there.
- **DS-COLOR-4** Nothing is told by colour alone. A verdict has a word or a sign beside its colour.

### Type

Three roles: `--font-display` (headings, large figures), `--font-sans` (text and controls), `--font-mono` (dates, amounts in
tables, code). One scale: `--text-caption` 13, `--text-small` 14, `--text-body` 16, `--text-heading` 19, `--text-title` 28.
Weights: `--weight-heading`, `--weight-strong`. Capitals: `--tracking-caps`.

- **DS-TYPE-1** Sizes come from the scale. Nothing is set below `--text-caption`.
- **DS-TYPE-2** Headings and headline figures are in the display face; nothing else is.
- **DS-TYPE-3** Figures that are compared down a column are tabular and right-aligned.
- **DS-TYPE-4** Running text is no wider than 75 characters.
- **DS-TYPE-5** The shipped kit serves its own font files. The app calls no outside host for type.

### Space, shape, motion

Spacing `--space-1 2 3 4 6 8 12 16` (4 to 64 px). Radius `--radius-sm md lg full`. Motion `--dur-fast` 120 ms, `--dur` 220 ms,
`--dur-slow` 300 ms, `--ease`. Breakpoints: phone at 600 px, small at 820 px.

- **DS-SPACE-1** Gaps and padding come from the spacing scale.
- **DS-SHAPE-1** Sheets and controls are square-cornered, within what the kit's radius allows; only a count or a person is round.
- **DS-MOTION-1** Motion shows where something came from, and lasts no longer than `--dur-slow`. Nothing moves under
  `prefers-reduced-motion`.
- **DS-LAYOUT-1** Every page works at 390 px with no sideways scroll of the page; a wide table scrolls inside its own box.
- **DS-LAYOUT-2** Every control is at least 44 px tall on a phone. A link inside a sentence is exempt; a link that stands alone is not.

### Glyphs

`/static/glyphs.svg` is the default set: plain, with no theme. A kit's `glyphs.svg` replaces any of them by id; what it leaves
out falls back. Ids: `dashboard check-in close-month reviews settings day night sign-out savings-rate net-worth emergency-fund`.

- **DS-GLYPH-1** A glyph is drawn on a 24 px grid in one line weight (`--glyph-weight`), in the colour of the text beside it.
- **DS-GLYPH-2** A glyph always sits beside its label and is hidden from assistive technology. It is never the only name a
  control has.
- **DS-GLYPH-3** A kit replaces glyphs; it does not add destinations. A new glyph id is added here first.

## 4. Components

### Sheets

- **DS-SHEET-1** A page's content lies on sheets (`.panel`): the sheet surface, an ink border, and a second rule just inside
  it, as a chart has.
- **DS-SHEET-2** A card inside a sheet or a grid of figures (`.card`) has one rule, not two. Rules are not nested three deep.
- **DS-SHEET-3** A set piece may lie on a stack of earlier sheets (`.sheet-stack`): the sign-in card, and the one sheet a
  page is about. Working pages (Settings, the editors, long tables) stay flat.
- **DS-SHEET-4** A month is a page. Where a page shows one month, the way to its neighbours is at the foot of the sheet,
  earlier at the left and later at the right.

### Header and navigation

- **DS-NAV-1** The monthly pages are index tabs at the left, standing on a ruled line: Dashboard, Check-in, Close a month,
  Reviews. The current tab is cut from the page and joins it.
- **DS-NAV-2** Administration sits apart at the right: Settings, the day and night switch, Sign out. Each is a bordered
  button, so that it does not melt into the paper.
- **DS-NAV-3** The day and night switch names the mode it will change to.
- **DS-NAV-4** Tabs within a page keep their place in the URL hash, and follow the ARIA tab pattern.
- **DS-NAV-5** A selected tab looks the same wherever tabs appear: the accent ground with `--on-accent` text for tabs inside
  a page; the cut tab for the header.

### Controls

- **DS-BTN-1** A button has a visible border and a ground. One primary button (`.btn.primary`, the accent) to a view; the
  rest are plain.
- **DS-BTN-2** A button's label is a verb and its object: *Close the month*, not *Submit*. In a table, where the row is the
  object, a bare verb (*Edit*, *Delete*) is allowed if the button's accessible name adds the row (*Edit Groceries*). A button
  never shares its word with a status: the stamp says *Done*, the button says *Finish the check-in*.
- **DS-FORM-1** A field has a visible label above it, a shoal ground and a strong border. An error sits under its field in
  words, and the field is marked `aria-invalid`.
- **DS-FOCUS-1** Keyboard focus is a 2 px `--focus` ring, 2 px off the control, on every control without exception: buttons,
  fields, links, the wordmark and disclosure summaries. A box that scrolls keeps room for the ring of what is inside it.

### Figures, tables, status

- **DS-TILE-1** A headline figure is a label, the figure in the display face, and one line saying what it is compared with.
- **DS-TABLE-1** A table has a strong rule under its head and light rules between rows; numbers right, words left.
- **DS-STATUS-1** A status is a stamp (`.status-chip`): a word in spaced capitals inside a double rule, in the verdict's colour.
  The word is fixed (DS-WORD-1).
- **DS-FLASH-1** After a save, a flash at the top of the page says what was saved, in the past tense, and the page returns to
  where the person was.

### Charts

- **DS-CHART-1** Months that have happened are fixes: a ring with a dot, joined by a solid track.
- **DS-CHART-2** What has not happened is a dotted course.
- **DS-CHART-3** A goal is a filled waypoint in `--accent-2`, with its name and amount beside it.
- **DS-CHART-4** Series take `--series-1` to `--series-8` in order, on every chart, so a category keeps its colour.
- **DS-CHART-5** Every chart can be read without seeing it: a caption that states the finding, and its figures as a table.

## 5. Patterns

- **DS-LIST-1** A list that grows with the household's data is the last thing in its panel, or sits behind a count
  (`AGENTS.md`, "Long lists").
- **DS-EMPTY-1** An empty state says what will appear and what to do first, with the button that does it.
- **DS-FIRST-1** The first view of every page answers its question before it explains itself: the figure, then the method.
- **DS-MONTH-1** Budget month, not calendar month, everywhere; the term carries its definition.

## 6. Accessibility

WCAG 2.1 AA is the floor (DS-COLOR-3, DS-COLOR-4, DS-FOCUS-1, DS-LAYOUT-2, DS-GLYPH-2, DS-CHART-5, DS-MOTION-1), and:

- **DS-A11Y-1** Everything works from the keyboard, in an order that follows the page.
- **DS-A11Y-2** Every control has a name that says what it does, without its neighbours.
- **DS-A11Y-3** Both modes are designed. Night is not day inverted.
- **DS-A11Y-4** Every page has one `<main>`, and a skip link to it is the first thing the keyboard reaches.
- **DS-A11Y-5** After an action in a list (save, cancel, delete), focus stays with the row or its neighbour; it never falls
  to the top of the page.
- **DS-ERROR-1** A page that cannot be shown (not found, not allowed, an expired form) keeps the header, says what happened
  in plain words, and offers the way back.

## 7. What a kit supplies

| File | Required | Falls back to |
|---|---|---|
| `theme.css` | yes, to be a kit | The contract tokens for both modes |
| `words.json` | no | The plain words in `app/brand_kit.py` |
| `glyphs.svg` | no | The default set, glyph by glyph |
| `mark.svg` | no | Bearings' seal |
| `mark-small.svg`, `mark-dark.svg`, `mark-small-dark.svg` | no | The kit's nearest mark |
| `backdrop.svg`, `custom.css`, font files | no | None needed |

A directory with a `theme.css` is its own kit: what it leaves out falls back to the plain defaults, not to Bearings' chart
symbols or wording. `docs/BRANDING.md` has the steps; `examples/brand-kits/` has two kits that follow them.

## 8. Not yet in keeping

The system was written after the app. These parts of the app break a rule today; a change that touches one of them mends it,
and the critique reports them only on the surfaces a change touches. Each row names the issue that will mend it.

| Where | Rule | What is wrong | Issue |
|---|---|---|---|
| Dashboard charts (`dashboard.html`) | DS-CHART-1, 2, 3 | Lines and areas, not fixes, courses and waypoints | #191 |
| Dashboard page | DS-TOKEN-1 | A 200-line `<style>` block of its own, with raw `em` sizes | #191 |
| Templates | DS-SPACE-1 | About 135 inline `style` attributes, most of them margins | #193 |
| Check-in, dashboard | DS-SHEET-4 | No page turn between months | #191 |
| Dashboard tiles | DS-GLYPH-1 | The figure glyphs (`savings-rate`, `net-worth`, `emergency-fund`) are drawn but not yet placed | #191 |
| Dashboard (catch-all envelopes, radar charts) | DS-CHART-5 | No table of their figures | #191 |
| Dashboard | DS-COLOR-2 | `--accent-2` marks card headings, summaries and definitions, which are not destinations | #191 |
| Dashboard gauges | DS-STATUS-1 | Verdicts are round pills with a dot, not stamps | #191 |
| Dashboard, Settings | DS-SHEET-1, 2 | Cards and lists lie straight on the paper; one sheet is nested in another | #191 |
| Settings, Reviews | DS-FORM-1, DS-EMPTY-1 | Filter boxes have no visible label; empty states link in prose where a button should be | #193 |
| Settings tables | DS-TYPE-3 | Number columns are left-aligned (`.num` exists; the columns do not yet use it) | #193 |
| Close a month, Settings | DS-BTN-1 | More than one primary button in a view; which is primary is the owner's choice | #193 |
