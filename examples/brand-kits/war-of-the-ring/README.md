# Example brand kit: War of the Ring

The light/dark button changes sides. **Light mode is the West**: the white stone of Minas Tirith, the green and gold of Rohan,
and a beacon lit on the mountains. **Dark mode is the Shadow**: the soot of Mordor, the fire of Orodruin and the iron of Isengard.
Each side has its own login backdrop: the theme sets `--login-backdrop` separately for each mode. The mark is the Ring, plain
gold on a dark disc. Both sides contend for it, and one image has to serve both modes, because `mark.svg` is a plain image the
toggle cannot swap. Marcellus SC is used for headings, Alegreya for text and Fira Mono for figures.

```sh
cp examples/brand-kits/war-of-the-ring/*.css examples/brand-kits/war-of-the-ring/*.svg examples/brand-kits/war-of-the-ring/words.json "$LEDGER_DATA"/brand/
```

No restart is needed. The fonts come from Google Fonts, which the default Content-Security-Policy already allows. Delete the
copied files from the brand directory to go back to the built-in Bearings kit. It brings its own flourish words
(`words.json`: "Tidings at month's end") and no glyphs of its own, so it shows the default glyph set. All the art is original SVG; nothing is taken
from the films or from any edition of the books. `tests/test_contrast.py` checks this kit against the same WCAG pairs as the
built-in theme, in both modes.

The Red Book (`../red-book/`) is the quieter Middle-earth kit; this one is the dramatic one.
