# AGENTS.md — working in this repo

Household Ledger is a FastAPI + Postgres app around a small, file-based budget pipeline. Read this before changing anything.

## Invariants
- **Raw exports are facts; everything else is derived.** Never edit rows in `transactions`/`budget_items`; fix the mapping or
  rules and rebuild. For a source read through a profile (#96) the fact is the uploaded file in `source_files.content`; its rows
  are re-derived from it only by `reapply_profile`, never edited. `ledger` and `datasets` are truncated and rewritten on every rebuild.
- **Budget month, not calendar month**, everywhere.
- **Reconciliation must stay clean**: Σ transactions per envelope == BudgetItems Spent. A change that breaks this is wrong.
- `pipeline/*.py` must keep working as standalone scripts on a folder of CSVs (`python -I build_ledger.py` etc.). They read
  `item_mapping.csv`, `cpi_u.csv`, `events.json`, `rules.json` from the working directory and know nothing about the DB.
- The dashboard page (`app/web/templates/dashboard.html`) talks to data only through `dash.data()/calc()/draw()` from
  `dash-shim.js`. Add a dataset by emitting it from `make_datasets.py`; the shim exposes every row of `datasets` automatically.
- Colours come from the brand contract tokens (`docs/BRANDING.md`). Never type a hex colour into `app.css` or the page.
- No secrets in the repo. `.env` is ignored; `.env.example` holds placeholders only.

## Schema changes
Never edit an applied file in `db/migrations/`; add `NNNN_name.sql`, where NNNN is the 4-digit GitHub issue number (`0022_source_file_history.sql`).
`app/db.py` applies, at start and under an advisory lock, every file not yet in `schema_migrations`, in filename order, each in its own
transaction; a failure aborts start-up and leaves that file unrecorded. Out-of-order numbers from parallel branches still apply.
Derived tables (`ledger`, `datasets`) may be dropped and rebuilt by a migration; raw tables (`source_files`, `transactions`,
`budget_items`) and reference tables (`item_mapping`, `cpi`, `goals`, `life_events`, `settings`, `reviews`) must migrate their data.

## Done means
A change that adds a dataset, or a reference or raw table, is done only when its PR answers each line below with *done* or
*not, because …*. The data leaves the app through each of these places; the list is how none of them gets forgotten.

1. **Dataset**: emitted by `pipeline/make_datasets.py`, so the page reads it through `dash.data()`, never a route of its own.
   From 1.0.0 it is also in the dataset-schema snapshot test.
2. **Review**: in `build_context` (`app/review.py`), or a stated reason why not (e.g. balances are sent, account names are not).
3. **MCP**: in the `DATASETS` allow-list (`app/mcp_endpoint.py`) with `tests/test_mcp.py` updated, or a stated reason why not.
4. **Exports**: a dataset is in `datasets.zip`, a raw table in `sources.zip` (`app/routes/export.py`); a reference table is in
   `settings.zip` (`app/settings_export.py`) and read back by the seed loader (`seed_reference_tables`, `pipeline/run_pipeline.py`).
5. **CLI**: anything an editor can set on a page can be set with `python -m app.admin`.
6. **Webhooks**: whether `figures` in `build.completed` should carry it (`docs/WEBHOOKS.md`); usually not.
7. **Sample household**: `examples/sample-household/generate.py` produces data that exercises it, so the first-run dashboard shows
   it and the goldens and `tests/test_sample_household.py` test it.
8. **Upgrade note**: a line in `docs/UPGRADING.md` under the coming release if a migration touches existing data.
9. **Docs**: the README section that covers the feature (and, from 0.6.0, the check-in guide).

**The privacy line**, which every line above respects: only derived figures leave the box (review provider, MCP, webhooks).
Transactions, merchants and notes never do. Exports are the one exception, and they go only to a signed-in user of this install.

## Adding a household-specific rule
Put it in `rules.json` (and the `RULES` dict defaults in `build_ledger.py`) rather than in code, so other households can change
it on the Settings page.

## Checking a change
1. `DATABASE_URL=<throwaway database> python -m pytest -q` (`requirements-dev.txt`; see CONTRIBUTING.md). Goldens change only deliberately (`UPDATE_GOLDENS=1`).
2. `uvicorn app.main:app --reload --no-proxy-headers --no-access-log` with `AUTH_DISABLED=true` against a local Postgres.
3. `python -m pipeline.import_exports <dir>` on a set of exports; it exits non-zero if reconciliation fails.
4. Open the page in light and dark; no console errors; every tab draws.

## Month-to-month operation (what a session is usually for)
A new month arrives as two CSVs. Upload them on **Close a month**. If the build log says UNMAPPED, add the item on the Settings
page (reuse existing canonical names), rebuild, then press **Generate review**. The pipeline's standing rules and targets live in
the database, not in prompts — read them there.

## Commits, releases, images
- **Conventional Commits**, enforced on PR commits and the PR title: `feat:`, `fix:`, `perf:`, `refactor:`, `docs:`, `chore:`, `ci:`,
  `build:`, `test:`. A breaking change is `feat!:` or a `BREAKING CHANGE:` footer. PRs are squash-merged, so the PR title is what
  release-please reads.
- **Releases are automatic.** release-please keeps a `chore(main): release x.y.z` PR open on `main`; merging it tags `vx.y.z`, writes
  `CHANGELOG.md`, bumps `version.txt`. `feat` bumps minor, `fix` bumps patch (while < 1.0, a breaking change bumps minor).
- **Images**: every push to `main` publishes (amd64 + arm64) `ghcr.io/jkbeeman92/household-ledger:latest` and `:sha-<short>`; a release additionally
  publishes `:x.y.z`, `:x.y`, `:x`. Deployments that want stability pin `:x`; `:latest` follows `main`.
