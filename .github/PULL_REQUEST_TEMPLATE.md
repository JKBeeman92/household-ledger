<!-- Title must be a Conventional Commit: feat: / fix: / docs: / chore: / ci: / refactor: / perf: / test: — it becomes the squash commit and drives the release. -->

## What

<!-- One or two sentences. Link the issue: Closes #NN -->

## Why

<!-- The problem, or the behaviour the user sees today. -->

## How I checked it

- [ ] `python -m pytest -q` passes (goldens regenerated only on purpose, and the diff reviewed)
- [ ] Ran locally against Postgres (`AUTH_DISABLED=true`) and the page draws in day and night with no console errors
- [ ] UI change: the `ux-critique` workflow ran (AGENTS.md, "UX review"); its surviving findings are fixed or answered here, each with its `docs/DESIGN.md` rule id
- [ ] Pipeline change: `python -m pipeline.import_exports <fixtures>` exits 0 (reconciliation clean) and the ledger diff is the one I expected
- [ ] Schema change: a new `db/migrations/NNNN_name.sql` (NNNN = issue number), never an edit to an applied one; applies on an existing database with data, not just a fresh one
- [ ] No household data, secrets or `.env` contents in the diff

## Done means

<!-- Only for a change that adds a dataset, or a reference or raw table (AGENTS.md, "Done means"). For each line write "done" or
"not, because …". Delete this section for any other change. -->

- [ ] Dataset: emitted by `make_datasets.py`, read through `dash.data()`
- [ ] Review: in `build_context`
- [ ] MCP: in `DATASETS`, `tests/test_mcp.py` updated
- [ ] Exports: `datasets.zip` / `sources.zip`; a reference table in `settings.zip` and the seed loader
- [ ] CLI: `python -m app.admin` can set it
- [ ] Webhooks: `figures` in `build.completed`
- [ ] Sample household: `generate.py` exercises it
- [ ] Upgrade note in `docs/UPGRADING.md`
- [ ] Docs: README

## Notes for the release

<!-- Anything a user upgrading the image should know: new env var, changed default, data migration. Leave blank if none. -->
