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
mkdir -p /srv/ledger/{postgres,brand}
cp docker-compose.yml .env.example /srv/ledger/ && cd /srv/ledger && mv .env.example .env
$EDITOR .env            # passwords, BASE_URL, OIDC settings
docker compose up -d
```

First boot applies the migrations and seeds the reference tables. Seeds come from `SEED_DIR` (`/srv/ledger/seed/` in the compose file)
when a file is present there, else from the generic files in `pipeline/` (CPI table, empty mapping, goal keys with no values).
A seed directory holds any of `item_mapping.csv`, `goals.json`, `events.json`, `rules.json`, `review.json`, `household.json`;
after the first boot the database owns them and the Settings page is the editor. Keep your seed directory out of public repos —
it is your household's data.
Then open the site, go to **Close a month**, and upload exports (the form takes any number of files at once, or a `.zip` of a back catalogue; drop them on the form or choose them; request bodies are capped by `MAX_UPLOAD_MB`, default 50). The rebuild runs in the
background and the page shows its progress; only one rebuild runs at a time, even across containers.

**Bulk load at start.** Anything dropped in `/srv/ledger/import/` (mounted as `IMPORT_DIR`) is loaded when the container
starts: every EveryDollar export whose month is not yet in the database, or whose contents changed, is stored, then one rebuild
runs. Files already loaded are skipped by checksum, so the directory can stay as a permanent archive of the raw exports.
A failed start-up import is logged and recorded in the build log; it never stops the container from starting.
**Close a month** also has a *Scan import directory now* button (no restart needed), and download links for `ledger.csv`, `datasets.zip` and
`sources.zip` (EveryDollar exports rebuilt in their own shape under their original filenames; other tools' files exactly as uploaded, under `YYYY-MM/`), also at `/export/...` for any signed-in user.
The same import is available by hand:

```sh
docker compose exec ledger python -m pipeline.import_exports /data/import
```

Updates: the image is published to `ghcr.io/jkbeeman92/household-ledger` on every push to `main` (and `vX.Y.Z` tags), for both `linux/amd64` and `linux/arm64`.
`docker compose pull && docker compose up -d`, or any image-watching updater, is the whole upgrade. For anything you rely on, pin the
image in `docker-compose.yml` to a release (`:X.Y.Z`, or `:0` for the latest 0.x) instead of `:latest`. A checklist for the first
run of a new install or release is in [`docs/FIRST-RUN.md`](docs/FIRST-RUN.md).

**Hardening.** The compose file runs the app with a read-only root filesystem, all Linux capabilities dropped and
`no-new-privileges`; its only writable path is a 256 MB tmpfs on `/tmp` (rebuild working folder, upload spool). If you run the
image another way, give it a writable `/tmp` or set `TMPDIR`. The app trusts `X-Forwarded-*` headers from any address by default
(`FORWARDED_ALLOW_IPS=*`); once the port is reachable only from your reverse proxy, set it to the proxy's IP or network in `.env`
so a client that reaches the port directly cannot spoof its address.

## Backups

The database holds all the state that matters; `docker-compose.yml` has an optional `backup` service (a compose profile, so a plain
`up` does not start it) that dumps it on a schedule:

```sh
mkdir -p /srv/ledger/backups
docker compose --profile backup up -d      # one dump now, then every BACKUP_INTERVAL_HOURS (24)
```

Dumps land in `/srv/ledger/backups/ledger-<UTC time>.dump` (`pg_dump -Fc`, readable only by root) and dumps older than
`BACKUP_KEEP_DAYS` (14) are deleted. Both settings are optional entries in `.env`. A failed dump is logged (`docker compose logs backup`) and
never replaces an earlier good one. Copy that directory off the machine; a backup on the same disk is not a backup.

The dump is the database only. To rebuild a household from nothing you also need the directories you maintain by hand:
`seed/`, `brand/` and `import/` (the raw EveryDollar exports) complete the state.

**Restore** into an empty database, with the app stopped:

```sh
docker compose stop ledger
docker compose exec -T ledger-db dropdb -U ledger --force ledger
docker compose exec -T ledger-db createdb -U ledger ledger
docker compose exec -T ledger-db pg_restore -U ledger -d ledger --no-owner < /srv/ledger/backups/ledger-YYYYMMDDTHHMMSSZ.dump
docker compose start ledger
```

Without the `backup` service, the same dump by hand is
`docker compose exec -T ledger-db pg_dump -U ledger -Fc ledger > ledger.dump`.

## Other budget tools

EveryDollar's two exports are the built-in format and need no setup. Any other tool works too, as long as it exports the same
pair each month: **a transactions CSV and a budget CSV**. Upload both on **Close a month**. Files not named like EveryDollar's go to
a mapping page that shows their first rows, guesses which column is which, which file is which and the budget month, and asks
you to confirm. The confirmed mapping is saved as a *source profile* under a name you choose and is offered again automatically
for next month's files with the same columns. Profiles can be edited, exported and imported as JSON on **Settings → Sources**.

| File | Required columns | Optional |
|---|---|---|
| Transactions | date; group; item; and either one signed amount column, or separate debit and credit columns, or an amount plus a type column that says income or expense | merchant / payee, account, note / memo |
| Budget | group; item; planned; spent | — |

- **Group and item** must name the same budget lines in both files: each line's transactions are reconciled against that line's
  spent figure, as they are for EveryDollar. A tool with no budget lines (a plain bank statement) cannot be reconciled, so it is
  not supported.
- **Dates**: `MM/DD/YYYY`, `DD/MM/YYYY`, `YYYY-MM-DD`, `DD.MM.YYYY`, `YYYY/MM/DD`, `DD-MM-YYYY` or `MM-DD-YYYY`. **Numbers**: either
  decimal mark, any thousands separator, currency symbols and `(1.00)` negatives. Comma, semicolon or tab delimited; UTF-8 or Windows-1252.
- **Signs**: say whether spending is negative or positive in each file; lines in the income groups (default `Income`) are income.

The files are stored exactly as uploaded, and their rows are read through the profile, so correcting a profile re-reads every
month stored with it; nothing needs uploading again. The import directory and `.zip` uploads take EveryDollar exports only.
Outside the app, `python -m pipeline.sources propose …` prints a starting profile for a pair of files and
`python -m pipeline.sources normalize --profile p.json --month YYYY-MM …` writes them as EveryDollar-named files for the standalone
pipeline scripts.

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

## Sign-in (any OIDC provider)

The app is its own OIDC client — no authenticating proxy in front of it. Any OpenID Connect provider with a discovery document
works (Authentik, Keycloak, Authelia, Zitadel, Entra ID, …) as long as it can:

- register a confidential client using the authorization-code flow, with redirect URIs `https://ledger.example.com/auth/callback`
  and `https://ledger.example.com/signed-out` (the `post_logout_redirect_uri` that **Sign out** sends);
- put the user's groups in a `groups` claim, in the ID token and from userinfo;
- issue a refresh token (the app asks for `offline_access`), which the group re-check below uses.

Put the issuer URL (the one with `/.well-known/openid-configuration` under it) and the client ID/secret in `.env`, and set
`ALLOWED_GROUPS` to the group(s) that may use the ledger. **`ALLOWED_GROUPS` is required**: unset or empty, the app refuses to
start, rather than admitting everyone the provider authenticates.

**Example: Authentik.**

1. Authentik → Applications → Providers → **Create** → OAuth2/OpenID Provider. Confidential client, authorization-code flow,
   redirect URI `https://ledger.example.com/auth/callback`, signing key selected, scopes `openid email profile` plus the
   default `groups` mapping (the app reads the `groups` claim).
   Also select the `offline_access` scope (the app needs a refresh token for the group re-check below) and add
   `https://ledger.example.com/signed-out` as a redirect URI: it is the `post_logout_redirect_uri` that **Sign out** sends.
2. Applications → **Create** `ledger`, bound to that provider. Restrict it with a policy binding on the group you will put in
   `ALLOWED_GROUPS`, for example `ledger-users` (the app enforces it too).
3. Put the provider's client ID/secret and the issuer URL (`https://auth.example.com/application/o/ledger/`) in `.env`.

**Viewers and editors.** Everyone admitted by `ALLOWED_GROUPS` can see the dashboard and `/api/*`. Set `EDITOR_GROUPS` (default: same as `ALLOWED_GROUPS`) to the subset
that may also upload, rebuild, edit settings and generate reviews; other members get no nav links for those and a 403 page if they open them. Role changes at the IdP apply at the next group re-check.

Sign-in lasts `SESSION_MAX_AGE` (12 h). Every `GROUP_RECHECK_SECONDS` (5 min) the app refreshes the token and reads `groups`
again, so removing someone from the group, or disabling them at the provider, signs them out within that interval; if the provider
cannot be reached for the re-check, the session also ends. **Sign out** ends the provider session too (`id_token_hint`), where the provider supports RP-initiated logout.
The tokens stay in the database (`auth_sessions`); the cookie holds only a random id. Upgrading signs everyone out once.
A theme in `BRAND_DIR` that loads fonts or images from a host other than Google Fonts needs that host in `CSP_STYLE_SRC` /
`CSP_FONT_SRC` / `CSP_IMG_SRC` (see `.env.example`); otherwise the browser's Content-Security-Policy blocks it.

**API tokens** (scripts, monitoring, agents). An editor creates personal tokens on **Settings → API tokens**; each is shown once
(only its hash is stored), scoped `read` or `read+review`, and revocable there, with its last-used time. Send it as
`Authorization: Bearer hl_…` to `/api/*` and `/metrics` (e.g. a Prometheus `authorization` block). Tokens never open a page and
never upload, rebuild or change settings; a revoked or unknown token gets 401. When the group re-check finds the owner is no longer
an editor, their tokens are revoked; revoke a token by hand when someone leaves and never signs in again.

`AUTH_DISABLED=true` skips all of this for local development and signs you in as `dev@localhost`. Never set it in production.
With auth on, the app refuses to start (and prints why) on a missing or short `SESSION_SECRET`, missing OIDC settings, or unset or empty `ALLOWED_GROUPS`; `AUTH_DISABLED=true` in the container image (which listens on all interfaces) or with a non-localhost `BASE_URL` also needs `I_KNOW_AUTH_IS_OFF=1`.

## Connect an agent

The app serves an [MCP](https://modelcontextprotocol.io) endpoint at `/mcp` (streamable HTTP) so Claude Code, Claude Desktop or any
MCP client can read the month-close figures. Create a token on **Settings → API tokens** (`read`, or `read+review` if the agent
should write reviews), then:

```
claude mcp add --transport http household-ledger https://ledger.example.com/mcp --header "Authorization: Bearer hl_YOUR_TOKEN"
```

Tools: `list_months`, `get_dataset(name)`, `month_summary(m)`, `category_trend(category, months)`, `goals()`, `review(m)`,
`review_instructions()`, and `save_review(m, sections)`, which needs the `read+review` scope, replaces that month's review and records
the token that wrote it. The endpoint accepts API tokens only (no browser session, not even with `AUTH_DISABLED=true`). It serves
derived datasets only: there is no `transactions` tool, and merchants, notes and raw rows never leave through MCP.

## Month-close review

With a review provider configured, the Close-a-month page can write a review of any loaded month. It sends derived figures only
(planned vs spent per envelope, category totals, monthly totals, trailing-twelve averages, goals) — never transactions, merchants
or notes — and stores both the prompt and the result so it can be audited. The review appears on the dashboard's **Last month** tab.

The provider is chosen by `REVIEW_PROVIDER` (default `anthropic`, which uses `ANTHROPIC_API_KEY`): `openai` (any OpenAI-compatible
endpoint, via `REVIEW_BASE_URL` such as `https://host/v1`, and `REVIEW_API_KEY` where the server wants one), `ollama`
(`REVIEW_BASE_URL=http://ollama:11434`, no key, so a homelab can run it fully offline) or `gemini` (`REVIEW_API_KEY`). Every
provider except Anthropic needs `REVIEW_MODEL`; none is guessed. The **Reviews** page overrides provider, model and base URL per
household and has a Test connection button. The stored review records `provider:model`. Local models may need a larger
`REVIEW_MAX_TOKENS` (default 2500); replies wrapped in markdown fences or prose are tolerated.

## Local development

```sh
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
export DATABASE_URL=postgresql://ledger:ledger@localhost:5432/ledger AUTH_DISABLED=true SESSION_SECRET=$(openssl rand -hex 32)
uvicorn app.main:app --reload --port 8010
```

Layout: `app/` (FastAPI, templates, static), `pipeline/` (the scripts and the seed data), `db/migrations/` (numbered SQL, applied at start), `examples/sample-household/` (synthetic data for a first run).

## Privacy

This is financial data. Keep it behind SSO, keep the database on an encrypted volume, and back it up (see [Backups](#backups): the `ledger`
database plus the `seed/`, `brand/` and `import/` directories are the whole state). The container runs as a non-root user and
makes outbound calls only when you ask for them: to the review provider you configured when you press Generate review, and to the
price-index source when you press Fetch latest on the CPI page. Only derived figures are sent, never transactions.

## License

MIT.
