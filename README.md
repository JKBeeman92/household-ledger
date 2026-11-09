# Household Ledger

A self-hosted dashboard for a household budget kept in EveryDollar. Upload the two monthly exports, get a reconciled ledger,
nine years of charts, goals tracking and a model-written month-close review — behind your own identity provider, on your own box.

Everything the app decides from is data you can edit on its Settings page: the item → category mapping, the CPI table for real
dollars, goals, life-event markers, the classification rules (who earns what, which items are transfers), and the household name.
The look is a mounted directory of CSS and images ([docs/BRANDING.md](docs/BRANDING.md)).

## How it works

```
EveryDollar exports ──upload──▶ Postgres (source_files, transactions, budget_items)      ← immutable facts
                                   │ rebuild (on every upload, or on demand)
                                   ▼
                      pipeline/ (reconcile · classify · CPI-adjust · aggregate)        ← pure functions of the facts + settings
                                   │
                                   ▼
                       Postgres (ledger, datasets)  ──/api/datasets──▶  the dashboard page (d3, no build step)
```

- A **budget month** is read from the export filename (`<id>-MM-YYYY-EveryDollar-Transactions.csv`), never from transaction dates.
- Reconciliation: for every envelope, Σ transactions must equal the BudgetItems "Spent". A month that fails still loads, and the
  page says so.
- The pipeline scripts are plain Python with no database knowledge; `pipeline/run_pipeline.py` feeds them from Postgres and loads
  their outputs back. They can also be run by hand on a folder of CSVs.

## Run it

```sh
mkdir -p /volume2/ledger/{postgres,brand}
cp docker-compose.yml .env.example /volume2/ledger/ && cd /volume2/ledger && mv .env.example .env
$EDITOR .env            # passwords, BASE_URL, OIDC settings
docker compose up -d
```

First boot applies the schema and seeds the reference tables. Seeds come from `SEED_DIR` (`/volume2/ledger/seed/` in the compose file)
when a file is present there, else from the generic files in `pipeline/` (CPI table, empty mapping, goal keys with no values).
A seed directory holds any of `item_mapping.csv`, `goals.json`, `events.json`, `rules.json`, `review.json`, `household.json`;
after the first boot the database owns them and the Settings page is the editor. Keep your seed directory out of public repos —
it is your household's data.
Then open the site, go to **Close a month**, and upload exports (the form takes any number of files at once).

**Bulk load at start.** Anything dropped in `/volume2/ledger/import/` (mounted as `IMPORT_DIR`) is loaded when the container
starts: every EveryDollar export whose month is not yet in the database, or whose contents changed, is stored, then one rebuild
runs. Files already loaded are skipped by checksum, so the directory can stay as a permanent archive of the raw exports.
The same import is available by hand:

```sh
docker compose exec ledger python -m pipeline.import_exports /data/import
```

Updates: the image is published to `ghcr.io/jkbeeman92/household-ledger` on every push to `main` (and `vX.Y.Z` tags).
`docker compose pull && docker compose up -d`, or any image-watching updater, is the whole upgrade.

## Try it with the sample household

`examples/sample-household/` is a synthetic household (two earners, 18 budget months, a student loan, a transfer pair, three-paycheck
months, life events) that reconciles clean, so you can see every tab before loading your own exports. The seed files go in `SEED_DIR`
(first boot only) and the exports in `IMPORT_DIR`.

Compose, from a checkout of this repo (`LEDGER_DATA` defaults to `./data`, which git ignores):

```sh
mkdir -p data/seed data/import
cp examples/sample-household/*.json examples/sample-household/item_mapping.csv data/seed/
cp examples/sample-household/*-EveryDollar-*.csv data/import/
docker compose up -d
```

Local, against a Postgres of your own:

```sh
export DATABASE_URL=postgresql://ledger:ledger@localhost:5432/ledger AUTH_DISABLED=true SESSION_SECRET=$(openssl rand -hex 32)
export SEED_DIR=$PWD/examples/sample-household IMPORT_DIR=$PWD/examples/sample-household
uvicorn app.main:app --port 8010
```

Seeding only happens into empty tables, so start from an empty database. `python -I examples/sample-household/generate.py` rebuilds
the files byte for byte (fixed seed, no clock); everything in them is invented.

## Sign-in (Authentik)

The app is its own OIDC client — no proxy outpost in front of it.

1. Authentik → Applications → Providers → **Create** → OAuth2/OpenID Provider. Confidential client, authorization-code flow,
   redirect URI `https://ledger.example.com/auth/callback`, signing key selected, scopes `openid email profile` plus the
   default `groups` mapping (the app reads the `groups` claim).
2. Applications → **Create** `ledger`, bound to that provider. Restrict with a policy binding on the `admins` group (or set
   `ALLOWED_GROUPS` to whichever group you use — the app enforces this too).
3. Put the provider's client ID/secret and the issuer URL (`https://auth.example.com/application/o/ledger/`) in `.env`.

`AUTH_DISABLED=true` skips all of this for local development and signs you in as `dev@localhost`. Never set it in production.

## Month-close review

With `ANTHROPIC_API_KEY` set, the Close-a-month page can write a review of any loaded month. It sends derived figures only
(planned vs spent per envelope, category totals, monthly totals, trailing-twelve averages, goals) — never transactions, merchants
or notes — and stores both the prompt and the result so it can be audited. The review appears on the dashboard's **Last month** tab.

## Local development

```sh
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
export DATABASE_URL=postgresql://ledger:ledger@localhost:5432/ledger AUTH_DISABLED=true SESSION_SECRET=$(openssl rand -hex 32)
uvicorn app.main:app --reload --port 8010
```

Layout: `app/` (FastAPI, templates, static), `pipeline/` (the scripts and the seed data), `db/schema.sql`, `examples/brand-beeman/`.

## Privacy

This is financial data. Keep it behind SSO, keep the database on an encrypted volume, and back it up (`pg_dump` of the `ledger`
database is the whole state; the brand directory is the only other thing worth keeping). The container runs as a non-root user and
makes exactly one outbound call, to the Anthropic API, and only when you press Generate review.

## License

MIT.
