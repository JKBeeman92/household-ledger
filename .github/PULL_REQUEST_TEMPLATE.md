<!-- Title must be a Conventional Commit: feat: / fix: / docs: / chore: / ci: / refactor: / perf: / test: — it becomes the squash commit and drives the release. -->

## What

<!-- One or two sentences. Link the issue: Closes #NN -->

## Why

<!-- The problem, or the behaviour the user sees today. -->

## How I checked it

- [ ] `python -m pytest -q` passes (goldens regenerated only on purpose, and the diff reviewed)
- [ ] Ran locally against Postgres (`AUTH_DISABLED=true`) and the page draws in light and dark with no console errors
- [ ] Pipeline change: `python -m pipeline.import_exports <fixtures>` exits 0 (reconciliation clean) and the ledger diff is the one I expected
- [ ] Schema change: a new `db/migrations/NNNN_name.sql` (NNNN = issue number), never an edit to an applied one; applies on an existing database with data, not just a fresh one
- [ ] No household data, secrets or `.env` contents in the diff

## Notes for the release

<!-- Anything a user upgrading the image should know: new env var, changed default, data migration. Leave blank if none. -->
