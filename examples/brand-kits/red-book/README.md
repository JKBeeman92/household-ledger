# Example brand kit: The Red Book

A ledger kept the way the Shire kept its records: red leather and gold leaf in dark mode, parchment and red ink in light mode,
Cinzel capitals for headings, EB Garamond for text and Courier Prime for figures. The mark is a gold-tooled red book with a ring
and a lone mountain on its cover; the login backdrop is a hand-drawn map with hills, a wood, a river and a road that goes ever on.
All the art is original SVG; nothing is taken from the films or from any edition of the books.

```sh
cp examples/brand-kits/red-book/theme.css examples/brand-kits/red-book/mark.svg examples/brand-kits/red-book/backdrop.svg "$LEDGER_DATA"/brand/
```

No restart is needed. The fonts come from Google Fonts, which the default Content-Security-Policy already allows. Delete the
three files from the brand directory to go back to the built-in Homestead theme. `tests/test_contrast.py` checks this kit
against the same WCAG pairs as the built-in theme.
