# Branding and themes

The app ships with its own brand, "Homestead": money green and warm terracotta on cream by day and on warm charcoal by night,
Plus Jakarta Sans with JetBrains Mono for figures. Its mark is a house whose walls are a ledger sheet with a dollar sign on it.
Everything visual
is replaceable without rebuilding the image: mount a directory at `BRAND_DIR` (default `/data/brand`) and any file in it
shadows the shipped default of the same name under `/brand/…`.

| File in `BRAND_DIR` | Replaces | Notes |
|---|---|---|
| `theme.css` | the whole colour/type/spacing contract | must define every token below for `:root` (dark) and `:root[data-theme="light"]` |
| `custom.css` | nothing (loaded last, empty by default) | small overrides on top of a theme |
| `mark.svg` | the mark in the header, masthead, favicon and login card | square, any size |
| `backdrop.svg` or any image | the login backdrop | reference it from `theme.css` via `--login-backdrop` |
| `household.json` | first-boot household name/title/currency | only read when the `household` setting does not exist yet; edit later on the Settings page |

## The token contract

Surfaces and text: `--surface-000 --surface-100 --surface-200 --surface-300 --line --line-strong --ink --ink-muted --ink-faint`
Brand: `--accent --accent-hover --accent-soft --on-accent --accent-2 --accent-2-deep --on-accent-2 --accent-2-text --focus` (`--accent-2-text` is the ochre for small text; it must reach 4.5:1 on the panel surfaces, `--accent-2` itself only 3:1; if missing, `--accent-2` is used)
Status: `--success --warning --danger --info --on-status`
Charts: `--series-1 … --series-8` (categorical order used by every chart), `--chart-grid --chart-axis --chart-reference`
Login: `--login-backdrop` (an `url()`), `--login-overlay` (a gradient laid over it)
Type and rhythm: `--font-display --font-sans --font-mono`, `--space-1 2 3 4 6 8 12 16`, `--radius-sm md lg full`, `--shadow-sm md glow`

Fonts: `@import` them at the top of `theme.css` (Google Fonts or self-hosted files placed in `BRAND_DIR`). d3 is vendored, so the
page itself needs no CDN; a theme that imports no web fonts makes the whole app work offline.
The Content-Security-Policy allows Google Fonts by default; fonts, stylesheets or images from any other host must be listed in
`CSP_FONT_SRC` / `CSP_STYLE_SRC` / `CSP_IMG_SRC` (`.env.example`), or the browser blocks them.

Light/dark: the header button toggles `data-theme="light"` on `<html>` and remembers the choice in the browser; with no stored
choice the OS preference is used. A theme must therefore define both blocks.

## Installing a kit

Put your files (a `theme.css`, and optionally a mark and images) in the brand directory:

```sh
cp my-brand/* "$LEDGER_DATA"/brand/
```

and restart nothing — the files are read on each request. The built-in theme, `app/web/static/brand/theme.css`, is the reference
for every token a kit has to define.

## The logo

`mark.svg` is the logo everywhere: the header, the dashboard masthead, the login card and the browser tab (favicon). Drop your
own `mark.svg` into `BRAND_DIR` to replace it. Keep it square (any `viewBox`); it is drawn from 16 px (favicon) to about 64 px,
so it should read at 16 px, and it sits on both the light and the dark surface, so give it its own background shape, as the
shipped mark does, rather than relying on the page behind it.

## Example kit: The Red Book

`examples/brand-kits/red-book/` is a complete alternative kit after the Red Book of Westmarch, the ledger of the Shire: red
leather and gold leaf by night, parchment and red ink by day, Cinzel / EB Garamond / Courier Prime, a gold-tooled book for the
mark and a hand-drawn map for the login backdrop. Its `README.md` has the copy command, and `tests/test_contrast.py` checks it
alongside the built-in theme.
