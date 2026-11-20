# Source profiles for other budget tools

A source profile tells Bearings how to read another budget tool's monthly pair of CSVs (a transactions file and a budget file):
which column is which, the date format, how amounts are signed, and, for a back catalogue, where each file's budget month comes
from (`month_from`). The format is documented in `pipeline/sources.py` and in the README ("Other budget tools").

**No preset ships yet.** Export columns change between versions of a tool and between countries, and a profile that has not been
checked against a real export would load a household's history wrongly, quietly. So a profile is added here only together with a
sample that a test reads.

## Contributing one

Send a pull request that adds, for one tool:

1. `<tool>.profile.json`: the profile, as **Settings → Sources** exports it after you have mapped one month (add a `month_from` if
   the tool's export names carry the month, e.g. `{"filename": "(?P<yyyy>\\d{4})-(?P<mm>\\d{2})"}`, or
   `{"column": "date", "rule": "mode"}` if they do not).
2. `<tool>/`: one real month's transactions and budget export from that tool, **anonymised**: replace every merchant, payee, note,
   account and person's name, and change the amounts, but keep the columns, their order, the delimiter, the date and number
   formats and the file names exactly as the tool writes them. Keep each line's transactions summing to its spent figure, so the
   month still reconciles.
3. A test in `tests/` that reads the pair through the profile (`pipeline.sources.apply`) and checks that it reconciles, as
   `tests/test_sources.py` does for its Bree Bank sample. Name the tool's version and the export settings you used in the profile's
   pull request.
