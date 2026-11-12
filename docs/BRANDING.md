# Brand kits

The app ships with its own brand kit, **Bearings**: a navigator's chart table, navy ink and brass on chart paper by day and on
deep ink by night; Libre Caslon Text for headings, Source Sans 3 for text, IBM Plex Mono for figures, all served from the app
itself. Its mark is a round seal cut from a portolan chart.

A kit supplies values; it does not change how the app is laid out or what its controls say. [`DESIGN.md`](DESIGN.md) holds the
rules every page keeps and the token contract a kit fills; this page is how to make and mount a kit.

Everything a kit supplies is replaceable without rebuilding the image: mount a directory at `BRAND_DIR` (default `/data/brand`)
and a file in it shadows the shipped one of the same name under `/brand/…`. (The household's name, title and currency are not
brand files: they come from `household.json` in the seed directory on first boot, and from the Settings page after that.)

| File in `BRAND_DIR` | Supplies | Notes |
|---|---|---|
| `theme.css` | every colour, typeface, size, radius and timing | must define the contract tokens for `:root` (night) and `:root[data-theme="light"]` (day). A directory with a `theme.css` is its own kit |
| `words.json` | the flourish words | any of `name`, `tagline`, `dashboard_eyebrow`, `checkin_eyebrow`, `login_line`, `footer`; the rest stay plain. Control labels cannot be changed |
| `glyphs.svg` | glyphs of its own | `<symbol id="…">` elements with the ids in `DESIGN.md`; each replaces the default glyph of that id, the rest fall back |
| `mark.svg` | the full mark, 48 px and up | square, any `viewBox`; give it its own ground |
| `mark-small.svg` | the mark under 48 px: header, browser tab | falls back to `mark.svg` |
| `mark-dark.svg`, `mark-small-dark.svg` | the night cuts of each | fall back to the day cuts |
| `backdrop.svg` or any image | the sign-in backdrop | reference it from `theme.css` via `--login-backdrop` |
| `custom.css` | nothing (loaded last, empty by default) | small overrides on top of a theme |

A kit that leaves out `words.json` or `glyphs.svg` gets the plain defaults, not Bearings' chart symbols and wording. A directory
with no `theme.css` (only a `custom.css`, say) is dressing on Bearings and keeps both.

## The token contract

[`DESIGN.md`](DESIGN.md), "Foundations", names every token and its role. In short:

Surfaces and text: `--surface-000 --surface-100 --surface-200 --surface-300 --line --line-strong --ink --ink-muted --ink-faint`
Brand: `--accent --accent-hover --accent-soft --on-accent --accent-2 --accent-2-deep --on-accent-2 --accent-2-text --focus` (`--accent-2-text` is the secondary colour for small text; it must reach 4.5:1 on the panel surfaces, `--accent-2` itself only 3:1; if missing, `--accent-2` is used)
Status: `--success --warning --danger --info --on-status`
Charts: `--series-1 … --series-8` (categorical order used by every chart), `--chart-grid --chart-axis --chart-reference`
Sign-in: `--login-backdrop` (an `url()`), `--login-overlay` (a gradient laid over it)
Type: `--font-display --font-sans --font-mono`, `--text-caption --text-small --text-body --text-heading --text-title`, `--weight-heading --weight-strong --tracking-caps`
Rhythm, shape, motion: `--space-1 2 3 4 6 8 12 16`, `--radius-sm md lg full`, `--shadow-sm md glow`, `--dur-fast --dur --dur-slow --ease`
Page and glyphs: `--graticule` (the page's grid; `transparent` for plain paper), `--glyph-weight`

The type, motion, `--graticule` and `--glyph-weight` tokens have defaults in `app.css`, so a kit written before them still works.

Fonts: the shipped kit serves its own font files from the brand directory, so the app calls no outside host and works offline.
A kit may do the same (put the files in `BRAND_DIR` and name them in `@font-face`), or `@import` a font service at the top of
`theme.css`.
The Content-Security-Policy allows Google Fonts by default; fonts, stylesheets or images from any other host must be listed in
`CSP_FONT_SRC` / `CSP_STYLE_SRC` / `CSP_IMG_SRC` (`.env.example`), or the browser blocks them.

Day and night: the header's switch toggles `data-theme="light"` on `<html>` and remembers the choice in the browser; with no
stored choice the OS preference is used. A theme must therefore define both blocks.

## Installing a kit

Put your files (a `theme.css`, and optionally a mark and images) in the brand directory:

```sh
cp my-brand/* "$LEDGER_DATA"/brand/
```

and restart nothing — the files are read on each request. The built-in theme, `app/web/static/brand/theme.css`, is the reference
for every token a kit has to define.

## Building a kit from an existing design system

A design system's palette was not chosen for this app's surfaces, so mapping its tokens straight onto the contract often fails a
few of the pairs the app draws. The ones that fail most often:

- `--line-strong` on `--surface-300`: input borders (3:1);
- a dark or slate series colour (`--series-N`) on the dark panel surface (`--surface-200`, 3:1);
- a pale series colour on the light panel surface (3:1).

Keep the design system's file as it is and do the fixing in the kit's own `theme.css`: define each contract token from the design
system (`--accent: var(--brand-green-600);`), then, for each failing pair, override that contract token in the mode that fails,
with a darker or lighter step of the same hue from the design system. A series colour that cannot reach 3:1 in one mode can be
swapped for another step in that mode only; keep the order of hues so a series keeps its colour family across modes.

**The app checks a mounted kit for you.** At start, and whenever `theme.css` in `BRAND_DIR` changes (no restart needed), it logs
one `WARNING` per problem and lists them on the Settings page for editors ("Your theme fails N contrast checks"). It never refuses
to start. A line names the mode, the pair, its ratio and where the pair is drawn:

```
brand: theme.css light: line-strong on surface-300 = 2.41 (< 3.0) for input borders
brand: theme.css dark: --accent-2-text is not defined
brand: theme.css dark: --series-3: could not evaluate color-mix(in srgb, var(--a), var(--b))
```

Values may be `#rgb`, `#rrggbb`, `rgb()`, `hsl()`, `oklab()`, `oklch()`, or `var()` chains of them. `color-mix()`, keywords and
translucent colours cannot be judged on their own and are reported as such: give those tokens a final opaque value. Local
`@import`s inside `BRAND_DIR` are followed, so a design system's own token file can sit beside `theme.css`; remote imports (fonts)
are not. The same check (`app/brand_check.py`) runs in the test suite for the shipped theme and the example kits; to run it on a
kit locally, add it to `THEMES` in `tests/test_contrast.py`.

## The marks

`mark.svg` is the full mark, drawn at 48 px and up (the sign-in card, the dashboard masthead); `mark-small.svg` is the cut for
the header and the browser tab, where fine detail would be lost. Each may have a night cut (`mark-dark.svg`,
`mark-small-dark.svg`). A kit with only a `mark.svg` is complete: the others fall back to it. Keep a mark square (any `viewBox`)
and give it its own ground, as the shipped seal has, so it reads on both the day and the night surface.

## Example kit: The Red Book

`examples/brand-kits/red-book/` is a complete alternative kit (a theme, a mark, a backdrop, its own words and five glyphs) after the Red Book of Westmarch, the ledger of the Shire: red
leather and gold leaf by night, parchment and red ink by day, Cinzel / EB Garamond / Courier Prime, a gold-tooled book for the
mark and a hand-drawn map for the login backdrop. Its `README.md` has the copy command, and `tests/test_contrast.py` checks it
alongside the built-in theme.

## Example kit: War of the Ring

`examples/brand-kits/war-of-the-ring/` uses the two modes for two sides: day is the West (Gondor's white stone, Rohan's
green and gold), night the Shadow (Mordor's soot and fire, Isengard's iron). It shows that `--login-backdrop` may differ per
mode (`backdrop-light.svg`, `backdrop-dark.svg`), while `mark.svg` is shared by both, so its mark, the Ring, is drawn to read on
either surface.
