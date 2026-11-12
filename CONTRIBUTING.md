# Contributing

Thanks for looking. This is a small app with strong opinions about correctness; `AGENTS.md` holds the invariants and is the
first thing to read. The short version:

- **Branch → PR → squash merge.** Nothing lands on `main` directly. The PR title is a Conventional Commit
  (`feat:`, `fix:`, `docs:`, `chore:`, `ci:`, `refactor:`, `perf:`, `test:`); two checks enforce it, on the title and on each commit.
- **Reconciliation must stay clean.** Every envelope's transactions sum to its Spent. A change that breaks that is wrong, however
  nice the chart.
- **Raw exports are facts.** Never edit `transactions`/`budget_items`; change the mapping or rules and rebuild.
- **A long list ends its panel** (or hides behind a count): nothing a household needs sits underneath one (`AGENTS.md`, "Long lists").
- **No colours outside the theme.** `app.css` and the page use contract tokens only (`docs/BRANDING.md`).
- **No household data in the repo.** Fixtures are synthetic. `.env` is ignored. If you paste a build log into an issue, scrub it.
- **New data is done when it reaches every exit.** A PR that adds a dataset or a table answers the checklist in `AGENTS.md`,
  "Done means" (review, MCP, exports, seeds, CLI, webhooks, sample household, upgrade note, docs). The PR template carries it.

## Is it in scope?

This app is a monthly check-in, not a full financial tracker ([README, "What this is, and isn't"](README.md#what-this-is-and-isnt)).
Before proposing a feature, ask: **would a household use it during its monthly (or quarterly) sit-down?** If it is only useful
daily, it belongs in the budgeting tool, not here. Out of scope, whatever the implementation: bank or account syncing, daily
budgeting or editing individual transactions, investment performance or allocation, reminders or notifications that want you in
the app more than once a month, and tax preparation.

## Running it locally

```sh
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
docker run -d --name ledger-pg -e POSTGRES_PASSWORD=ledger -e POSTGRES_USER=ledger -e POSTGRES_DB=ledger -p 5432:5432 postgres:16-alpine
export DATABASE_URL=postgresql://ledger:ledger@localhost:5432/ledger AUTH_DISABLED=true SESSION_SECRET=$(openssl rand -hex 32)
uvicorn app.main:app --reload --port 8010 --no-proxy-headers --no-access-log   # as the image runs it: the app writes the access line
```

Open <http://localhost:8010>, load exports on **Close a month** (or `python -m pipeline.import_exports <dir>`).

## Running the tests

```sh
pip install -r requirements-dev.txt
DATABASE_URL=postgresql://ledger:ledger@localhost:5432/ledger_test python -m pytest -q
```

`DATABASE_URL` must name a Postgres database you can throw away: every table the app uses is truncated in it before each test, and
nothing outside it is touched. Without it the database tests are skipped and only the pipeline golden test runs. CI runs the same
thing against a `postgres:16` service container, and the image is not built unless it passes.

The pipeline test runs `build_ledger`, `aggregate`, `make_datasets` and `make_budget_items` on `tests/fixtures/golden/` and diffs the
output against `tests/fixtures/golden/expected/`. When you change the arithmetic on purpose, regenerate with `UPDATE_GOLDENS=1 python
-m pytest tests/test_pipeline_golden.py`, read the `git diff`, and commit it with the change. Goldens are never rewritten otherwise.

## Releases

Automatic. Merging a `feat:` or `fix:` PR makes release-please open a `chore(main): release x.y.z` PR with the changelog;
merging *that* tags the version, publishes the GitHub Release and the `x.y.z` / `x.y` / `x` image tags. `latest` follows every
merge to `main`. You never write a tag or a changelog by hand.
