# Example brand kit: Mallorn

Gold leaf and silver bark: warm umber in dark mode, parchment in light mode, Cormorant Garamond for headings, Source Sans 3 for
text and IBM Plex Mono for figures, with sharp corners and hairline rules. It exists to show how far a kit can move the app away
from the built-in Verdigris theme without touching code.

```sh
cp examples/brand-kit/theme.css examples/brand-kit/mark.svg examples/brand-kit/backdrop.svg "$LEDGER_DATA"/brand/
```

No restart is needed. The fonts come from Google Fonts, which the default Content-Security-Policy already allows. Delete the
three files from the brand directory to go back to Verdigris. `tests/test_contrast.py` checks this kit against the same WCAG
pairs as the built-in theme, so an edit that drops below 4.5:1 for text or 3:1 for graphics fails the tests.
