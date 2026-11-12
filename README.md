# Household Ledger

A self-hosted dashboard for a household budget kept in any budget tool that exports its transactions and its budget each month
(EveryDollar is the worked example, and needs no setup). Upload the two monthly files, get a reconciled ledger, charts across every year you load, goals tracking and a
model-written month-close review — behind your own identity provider, on your own box.

Everything the app decides from is data you can edit on its Settings page: the item → category mapping, the CPI table for real
dollars, goals, life-event markers, the classification rules (who earns what, which items are transfers), and the household name.
The look is a mounted directory of CSS and images ([docs/BRANDING.md](docs/BRANDING.md)).

## What this is, and isn't

A **monthly check-in and refocus**: once a month (and once a quarter, and once a year), sit down, see where the household
stands, decide what to adjust for the coming month, and keep in view where it is heading. It is not a full financial tracker.

| It is | It isn't |
|---|---|
| A monthly close: upload, reconcile, review | Bank or account syncing |
| Where we stand: income, spend, debts and, from 0.6.0, balances and net worth | Daily budgeting or editing individual transactions: your budgeting tool (EveryDollar or another) does that, and this app reads its exports |
| What to change next month | Investment performance or allocation |
| Quarterly and yearly comparisons | Bill reminders, or notifications that want you in the app more than once a month |
| A simple look ahead: payoff dates, goals | Tax preparation |

**The test for a feature:** would a household use it during its monthly (or quarterly) sit-down? If it is only useful daily,
it belongs in the budgeting tool, not here.

## How it works

```
monthly exports    ──upload──▶ Postgres (source_files, transactions, budget_items)      ← immutable facts
                                   │ rebuild (on every upload, or on demand)
                                   ▼
                      pipeline/ (reconcile · classify · CPI-adjust · aggregate)        ← pure functions of the facts + settings
                                   │
                                   ▼
                       Postgres (ledger, datasets)  ──/api/datasets──▶  the dashboard page (d3, no build step)
```

- A **budget month** is read from the export's file name (`2026-03-transactions.csv`, or EveryDollar's `<id>-03-2026-EveryDollar-Transactions.csv`), never from transaction dates ([The internal format](#the-internal-format)).
  Another tool's files are read through a source profile, and their month is confirmed on upload ([Other budget tools](#other-budget-tools)).
- Reconciliation: for every envelope, Σ transactions must equal the BudgetItems "Spent". A month that fails still loads, and the
  page says so.
- The pipeline scripts are plain Python with no database knowledge; `pipeline/run_pipeline.py` feeds them from Postgres and loads
  their outputs back. They can also be run by hand on a folder of CSVs.

## Run it

You need: a host with Docker Compose (amd64 or arm64), a hostname with HTTPS in front of the app (see
[Reverse proxy and HTTPS](#reverse-proxy-and-https)), and an OpenID Connect provider with a client created for the ledger (see
[Sign-in](#sign-in-any-oidc-provider)). Create the client first: its ID, secret and issuer URL go in `.env`.

```sh
mkdir -p /srv/ledger/{postgres,brand,seed,import,backups}
cp docker-compose.yml .env.example /srv/ledger/ && cd /srv/ledger && mv .env.example .env
openssl rand -hex 24    # POSTGRES_PASSWORD
openssl rand -hex 32    # SESSION_SECRET
$EDITOR .env            # the two secrets, BASE_URL, OIDC_ISSUER / _CLIENT_ID / _CLIENT_SECRET, ALLOWED_GROUPS
docker compose up -d
docker compose logs -f ledger   # a configuration problem is listed here; the app refuses to start (and restarts) until it is fixed
```

The settings the app refuses to start without: `SESSION_SECRET` (32+ characters), `OIDC_ISSUER`, `OIDC_CLIENT_ID`,
`OIDC_CLIENT_SECRET` and `ALLOWED_GROUPS`. `BASE_URL` must be the exact public URL people type, with `https://` and no trailing
slash: sign-in redirects are built from it, and an `https` `BASE_URL` makes the session cookie HTTPS-only.

**File ownership.** The app runs as uid 10001 and mounts `brand/`, `seed/` and `import/` read-only, so the files in them must be
readable by that user (world-readable is enough: `chmod -R a+rX /srv/ledger/{brand,seed,import}`). `postgres/` belongs to the
database container, which sets its own ownership on first start; leave it alone. `backups/` is written as root.

First boot applies the migrations and seeds the reference tables. Seeds come from `SEED_DIR` (`/srv/ledger/seed/` in the compose file)
when a file is present there, else from the generic files in `pipeline/` (CPI table, empty mapping, goal keys with no values).
A seed directory holds any of `item_mapping.csv`, `cpi_u.csv`, `goals.json`, `events.json`, `rules.json`, `review.json`,
`household.json`; after the first boot the database owns them and the Settings page is the editor. A running install writes all of them
at once as `settings.zip` ([Backups](#backups)), so moving to a new box is: unzip into `seed/`, start. Their shapes:

| File | Shape |
|---|---|
| `item_mapping.csv` | columns `Item,CanonicalItem,Category,Flow,Note`, and optionally `DebtType` ([Roles](#roles)) |
| `cpi_u.csv` | columns `month,cpi,source` (`month` is `YYYY-MM`) |
| `goals.json` | list of `{"key", "label", "value", "unit", "note"}`; `value` may be `null` |
| `events.json` | list of `{"m": "YYYY-MM", "label": "…", "kind": "…", "person": "…"}` (the key is `m`, not `month`); `kind` (one of `job`, `move`, `family`, `health`, `purchase`, `debt`, `other`) and `person` (best one of the `people` rule) are optional |
| `rules.json` | object; keys and types in [`app/schemas/rules.schema.json`](app/schemas/rules.schema.json) |
| `household.json` | object; [`app/schemas/household.schema.json`](app/schemas/household.schema.json) |
| `review.json` | list of `{"m", "order", "heading", "text"}`, one row per review section |

[`examples/sample-household/`](examples/sample-household/) has a working copy of every file except `cpi_u.csv` (the shipped
`pipeline/cpi_u.csv` is used when none is given) and `review.json`. Keep your seed directory out of public repos —
it is your household's data.
Then open the site, go to **Close a month**, and upload exports (the form takes any number of files at once, or a `.zip` of a back catalogue; drop them on the form or choose them; request bodies are capped by `MAX_UPLOAD_MB`, default 50). The rebuild runs in the
background and the page shows its progress; only one rebuild runs at a time, even across containers.

**Bulk load at start.** Anything dropped in `/srv/ledger/import/` (mounted as `IMPORT_DIR`) is loaded when the container
starts: every export named in the internal format or as EveryDollar names them, whose month is not yet in the database, or whose contents changed, is stored, then one rebuild
runs. Files already loaded are skipped by checksum, so the directory can stay as a permanent archive of the raw exports.
A failed start-up import is logged and recorded in the build log; it never stops the container from starting.
**Close a month** also has a *Scan import directory now* button (no restart needed), and download links for `ledger.csv`, `datasets.zip` and
`sources.zip` (internal-format and EveryDollar exports rebuilt in their own shape under their original filenames; other tools' files exactly as uploaded, under `YYYY-MM/`), also at `/export/...` for any signed-in user.
The same import is available by hand:

```sh
docker compose exec ledger python -m pipeline.import_exports /data/import
```

**Without a browser.** The Settings editors and the rebuild have a command-line twin, run inside the container. Each command calls the
function the Settings page calls, so it checks its input the same way, prints the same error, and exits non-zero without saving anything
when the input is refused:

```sh
docker compose exec -T ledger python -m app.admin import-mapping - < item_mapping.csv
```

Commands: `import-mapping`, `set-rules`, `set-events`, `set-goals`, `set-cpi` and `set-household` replace one editor's contents from a file
(`-` reads standard input); `export <mapping|rules|events|goals|cpi|household>` and `export-mapping` print them; `draft-mapping` prints the
[drafted mapping](#roles) for unmapped items and `add-mapping FILE` adds a reviewed draft without touching existing
rows; `rename-category OLD NEW`, `rename-canonical OLD NEW` and `move-canonical NAME CATEGORY` (and `set-debt-type NAME TYPE`) do what the Categories and canonical
items panel does ([Roles](#roles)); `preview-mapping-migration` prints what the 0.5.0 upgrade will change in a 0.4 mapping
([docs/UPGRADING.md](docs/UPGRADING.md)); `rebuild` exits 0 when the build reconciles, 1 when it fails or does not reconcile, and 75 when another rebuild is running. Every
write is logged as a `ledger.admin` line naming the command. API tokens stay read-only: shell access to the container already means
full control, so the command line adds no new way in.

**Updates.** The image is `ghcr.io/jkbeeman92/household-ledger`, for `linux/amd64` and `linux/arm64`. Every push to `main`
publishes `:latest` and `:sha-<short>`; every release (git tag `vX.Y.Z`) also publishes `:X.Y.Z`, `:X.Y` and `:X`. The shipped
`docker-compose.yml` is pinned to the release it came with (`:X.Y.Z`; each release bumps it). Keep it pinned: `:0.2` takes 0.2.x
fixes only, `:0` takes every 0.x release (while the version is below 1.0 a minor release may change behaviour), and `:latest`
follows `main`, including changes not yet released. Do not hand the container to an auto-updater such as Watchtower: an upgrade
applies migrations that cannot be undone, so it should be a step you take after a backup.

To upgrade: read the release's notes in [`docs/UPGRADING.md`](docs/UPGRADING.md), take a backup (`docker compose --profile backup
run --rm -e BACKUP_ONCE=1 backup`, or the manual dump under [Backups](#backups)), change the tag in `docker-compose.yml`, then
`docker compose pull && docker compose up -d`.
Pending migrations apply themselves at start and are not reversible: to roll back, pin the previous tag and restore the dump.
A checklist for the first run of a new install or release is in [`docs/FIRST-RUN.md`](docs/FIRST-RUN.md).

## Reverse proxy and HTTPS

Run the app behind a reverse proxy that terminates TLS (Caddy, nginx, Traefik, a Cloudflare tunnel, …). It must:

- serve the app at the root of its hostname (`https://ledger.example.com/`, not a sub-path), the same URL as `BASE_URL`;
- pass the `Host` header through and set `X-Forwarded-Proto` and `X-Forwarded-For`;
- accept request bodies at least as large as `MAX_UPLOAD_MB` (50 MB by default). nginx's default is 1 MB, so set
  `client_max_body_size 50m;`; Caddy and Traefik have no limit by default;
- allow responses that take a while: rebuilds and month-close reviews run in the background (the page shows their progress),
  but a large upload can take tens of seconds; raise nginx's `proxy_read_timeout` if you see 504s on upload.

Caddy, on the same host:

```
ledger.example.com {
    reverse_proxy 127.0.0.1:8010
}
```

nginx:

```
server {
    listen 443 ssl;
    server_name ledger.example.com;
    # ssl_certificate / ssl_certificate_key ...
    client_max_body_size 50m;
    location / {
        proxy_pass http://127.0.0.1:8010;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }
}
```

When the proxy runs on the same host, publish the port on loopback only (`"127.0.0.1:8010:8010"` in `docker-compose.yml`) so
nothing reaches the app around it, and set `FORWARDED_ALLOW_IPS` to the proxy's address (see Hardening below). Opening
`http://<host>:8010` directly is not a supported way in: with an `https` `BASE_URL` the browser drops the session cookie and sign-in
loops back to the login page.

**A proxy that is not on this host's loopback.** If the proxy runs on another machine, or on this one but with its own LAN address
(for example a macvlan container, which reaches the host through a shim address rather than `127.0.0.1`), a loopback-only port
cannot reach it: keep the port published (`"8010:8010"`, or bound to the one host address the proxy connects to) and firewall it so
only the proxy's address may connect, then set `FORWARDED_ALLOW_IPS` to that address. Point the proxy at the host's address, e.g.
`reverse_proxy 192.168.1.20:8010`. Docker publishes ports through its own iptables rules, which a host firewall such as `ufw` does
not see; filter in the `DOCKER-USER` chain or on the network instead.

**Finding the value for `FORWARDED_ALLOW_IPS`.** Use the address the container actually sees, which is not always the proxy's own:
Docker can hand over a connection from its network gateway (`172.x.0.1`) instead. Open the site through the proxy and read
`docker compose logs ledger`: each request line names the browser and the peer it came through, e.g.
`INFO     ledger.access: 203.0.113.7 via 172.18.0.1 "GET /" 200 4.1ms` (the `client` and `peer` fields with `LOG_FORMAT=json`).
The address after `via` is the value; a network such as `172.18.0.0/16` also works. A line with no `via` came from a peer that is not
trusted, so its `X-Forwarded-For` was ignored and the peer is shown as the client.

**A hostname that exists only in local DNS.** If `ledger.example.com` resolves only on your LAN DNS server, a client that asks
another resolver gets "can't reach this page" and the app logs no request at all, because nothing reached it. The usual causes are
a router that hands out its ISP's IPv6 DNS servers alongside your own (clients prefer whichever answers), and browsers with DNS over
HTTPS ("secure DNS") turned on, which skip the LAN resolver. Fix the DHCP/RA DNS settings, or turn secure DNS off or point it at
your resolver, and test from a client (`nslookup ledger.example.com` there), not only from the server.

**The container must reach the identity provider.** Sign-in exchanges the code for tokens, and the group re-check refreshes them,
from inside the container, not from the browser. `OIDC_ISSUER` must therefore resolve and be reachable from the `ledger` container,
with a certificate it trusts. A homelab where the provider's hostname only resolves on the LAN, or where the router does not
hairpin traffic to its own public address, fails here: point the container at a resolvable address (for example `extra_hosts:`
in the compose file) or put the ledger and the provider on a shared Docker network. The symptom is the login page saying
"cannot reach the identity provider" (or "sign-in failed" after the provider's own page), and `docker compose logs ledger` shows
the connection that failed.

**Hardening.** The compose file runs the app with a read-only root filesystem, all Linux capabilities dropped and
`no-new-privileges`; its only writable path is a 256 MB tmpfs on `/tmp` (rebuild working folder, upload spool). If you run the
image another way, give it a writable `/tmp` or set `TMPDIR`. The app trusts `X-Forwarded-*` headers from any address by default
(`FORWARDED_ALLOW_IPS=*`); once the port is reachable only from your reverse proxy, set it to the proxy's IP or network in `.env`
so a client that reaches the port directly cannot spoof its address (how to find it: above).

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

**Settings alone.** **Close a month** → `settings.zip` (or `/export/settings.zip`, or `docker compose exec ledger python -m app.admin
export-settings /tmp/settings`) holds the mapping, CPI, goals, life events, rules and household as the seed files below. Unzip it into an
empty `seed/` and a new install starts with the same settings, no `pg_dump` needed. It carries no secrets (API tokens, webhook
destination and secret, sessions) and no reviews or build log. Take one before an upgrade that reshapes the mapping.

The dump is the database only, and it includes the files uploaded from other budget tools (they are stored in the database).
To rebuild a household from nothing you also need the directories you maintain by hand: `seed/`, `brand/` and `import/` (the raw
exports) complete the state. A one-off dump through the backup service: `docker compose --profile backup run --rm -e BACKUP_ONCE=1 backup`.

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

## The internal format

The pipeline reads one format of its own, a pair of CSV files per budget month:

| File | Columns |
|---|---|
| `YYYY-MM-transactions.csv` | `Group, Item, Type, Date, Merchant, Account, Amount, Note`: `Date` is `YYYY-MM-DD`; `Amount` is signed, income positive and spending negative |
| `YYYY-MM-budget.csv` | `Group, Item, Planned, Spent, Remaining` |

The month in the file name is the budget month every row counts in. Files named this way are read as they are, on upload, from a
`.zip`, from the import directory and by the standalone scripts. The app stages every rebuild in this format, and
`python -m pipeline.sources normalize` writes it. **EveryDollar's** exports are this format with `MM/DD/YYYY` dates and their own
names (`<id>-MM-YYYY-EveryDollar-Transactions.csv`, `…-BudgetItems.csv`); they are the built-in profile, read without a mapping
step, and always will be. `pipeline/filenames.py` recognises both.

## Other budget tools

A file in the internal format, or EveryDollar's, needs no setup. Any other tool works too, as long as it exports the same
pair each month: **a transactions CSV and a budget CSV**. Upload both on **Close a month**. Files not named like exports go to
a mapping page that shows their first rows, guesses which column is which, which file is which and the budget month, and asks
you to confirm. The confirmed mapping is saved as a *source profile* under a name you choose and is offered again automatically
for next month's files with the same columns. Profiles can be edited, exported and imported as JSON on **Settings → Sources**.

| File | Required columns | Optional |
|---|---|---|
| Transactions | date; group; item; and either one signed amount column, or separate debit and credit columns, or an amount plus a type column that says income or expense | merchant / payee, account, note / memo |
| Budget | group; item; planned; spent | — |

- **Group and item** must name the same budget lines in both files: each line's transactions are reconciled against that line's
  spent figure, as they are in the internal format. A tool with no budget lines (a plain bank statement) cannot be reconciled, so it is
  not supported.
- **Dates**: `MM/DD/YYYY`, `DD/MM/YYYY`, `YYYY-MM-DD`, `DD.MM.YYYY`, `YYYY/MM/DD`, `DD-MM-YYYY` or `MM-DD-YYYY`. **Numbers**: either
  decimal mark, any thousands separator, currency symbols and `(1.00)` negatives. Comma, semicolon or tab delimited; UTF-8 or Windows-1252.
- **Signs**: say whether spending is negative or positive in each file; lines in the income groups (default `Income`) are income.

The files are stored exactly as uploaded, and their rows are read through the profile, so correcting a profile re-reads every
month stored with it; nothing needs uploading again. The import directory and `.zip` uploads take named exports only (the
internal format, or EveryDollar's). The built-in EveryDollar profile is shown, read-only and exportable, on **Settings → Sources**.
Outside the app, `python -m pipeline.sources propose …` prints a starting profile for a pair of files and
`python -m pipeline.sources normalize --profile p.json --month YYYY-MM …` writes them in the internal format for the standalone
pipeline scripts.

## Roles

The item mapping turns each raw item name from the exports (`Item`) into a `CanonicalItem`, a `Category` and a `Flow`. Name them as
you like: nothing in the code reads a category, canonical item or budget group by its name. Where a chart needs to know *which* of
your names plays a part (which items are paychecks, which categories are recurring bills), a rule says so. Each has a default that
matches the sample household; set it on Settings → Pipeline rules → **Roles**, or in the rules JSON.

**A canonical item has one category.** The mapping is stored as categories, canonical items (each in one category) and item rows
(each naming a canonical item), so a row's category follows from its canonical item. Settings → Item mapping has a sub-tab for each list (Items,
Categories, Canonical items, Debt types). **Categories** and **Canonical items** rename either in one edit, and moves a canonical item to another category; renaming to a name that already
exists merges the two. A rename also rewrites every rule that names what was renamed (the `*_categories` rules, `loan_categories` among them, `paycheck_items`,
`catchall_items`, `income_by_person`, `payer_items_by_person`), including a rule still at its default, and is logged: stored reviews
keep their wording, and the Reviews page notes the renames made since each was written. The mapping CSV keeps its five columns
everywhere (the editor, the bulk import, `settings.zip`, seeds, the command line); a CSV that gives one canonical item two categories
is refused, naming the lines. The Canonical items sub-tab also flags a canonical item whose rows mix `income` with `expense` or `debt`.

**Debt types.** A canonical item with `debt` rows can be given a type on the **Debt types** sub-tab (`/settings#mapping/debts`), which lists only those: `mortgage`, `student`, `auto`, `card`, `medical`,
`personal` or `other`. The mapping CSV carries it as an optional sixth column, `DebtType`: a five-column CSV leaves types as they are,
and with the column an empty value clears one. The Debts tab groups typed debts with each type's total, orders them by first payment
or by total paid, and, once any debt has a type, folds a debt with no payment in the latest `debt_paid_off_months` budget months (a
rule, default 3; 0 never folds) under **Paid off**. A household that types nothing sees the Debts timeline as before.
`UNMAPPED` is not a category: an item with no row is what the build calls unmapped.

| Rule | Default | Names | What reads it |
|---|---|---|---|
| `paycheck_items` | `["Paycheck"]` | CanonicalItem | Rows of these items get an employer (the `employers` rule) and count as deposits (`paycheck_min`). The Employer chart, the Goals tab's take-home and paycheck count, and the month-close review's paycheck count are built from them. A list, so `Salary` and `Contract Income` can stay apart; tell earners apart with `income_by_person`. |
| `recurring_categories` | `["Subscriptions", "Utilities", "Insurance"]` | Category | The Recurring tab lists `expense` rows in these categories. |
| `plan_keep_categories` | `["Housing", "Debt", "Insurance"]` | Category | The Last month tab's suggested plan keeps the current plan for these instead of the twelve-month average. |
| `income_groups` | `["Income"]` | Budget group | An inflow whose merchant equals its item name is read as a carry-forward into a fund (a transfer), except in these groups; income-typed transfers are counted from them. New source profiles for other tools, and the mapping draft, start from these. |
| `debt_groups` | `["Debt"]` | Budget group | The `debt_plug_until` rule looks at these groups; the off-budget outflow count leaves them out; the mapping draft gives their items Flow `debt`. |
| `loan_categories` | `["Family Loan"]` | Category | The Method tab's two family-loan lines: inflows kept out of income, repayments kept out of spend. |
| `goal_housing_categories` | `["Housing", "Utilities"]` | Category | The housing share on the Goals tab. |
| `goal_giving_categories` | `["Giving"]` | Category | The giving share on the Goals tab. |

Renaming one of these names in the mapping without changing its rule empties what reads it, so change both together. A test
(`tests/test_role_literals.py`) fails if any of the default names appears as a string in the code outside the rule defaults.

**Rules take canonical names**, not the raw names in the exports: `income_by_person`, `payer_items_by_person`,
`payer_prefix_by_person`, `catchall_items` and `verify_transfer_prefixes` all compare against `CanonicalItem`. With
`Rosie Paycheck` and `Sam Paycheck` both mapped to `Paycheck`, `"income_by_person": {"Rosie": ["Paycheck"]}` works and
`{"Rosie": ["Rosie Paycheck"]}` matches nothing. The `*_categories` rules take `Category` names; `goal_savings_groups` takes budget
group names as they appear in the export. `employers[].match` is a regular expression searched in the **lower-cased** merchant, so
write it in lower case (`"hobbiton mill"`, not `"Hobbiton Mill"`).

**Fund moves recorded as income.** An item such as `Fund Transfer`, `Transfer Funds` or `End of Year Rebalance` that sits in the
income group counts as income, and inflates every income chart, unless its Flow is `transfer`. The pipeline also treats a row as a
transfer when its *merchant* is one of a few built-in names (`Transfer`, `Rebalance`, `Fund Transfer`, …) or matches the
`transfer_merchants` rule, but it does not look at the item name. Set Flow `transfer` on these items.

**Money you hold for someone else.** Collecting money on someone's behalf and paying it out later (donations gathered for a relative,
a group gift, a shared bill paid back by friends) shows as one month of large negative spending and later months of inflated
spending. Give those items Flow `exclude`: their rows stay in the stored exports and in reconciliation, but leave the ledger and
every chart. The sample household's `Venmo Pass-Through` item shows the pattern.

**A first mapping for a back catalogue.** Settings → Item mapping offers **Draft a mapping from my budget groups** whenever the stored
exports carry items with no mapping row. The draft fills only those items and never changes an existing row: Category is the budget
group; CanonicalItem is the item name without a year's suffix (` 2`, ` Fund`, ` Goal`); Flow is `income` for the `income_groups` (default `Income`), `debt`
for the `debt_groups` (default `Debt`), `transfer` for names such as `Fund Transfer`, `Transfer Funds`, `… Rebalance` or `Transfer to …`, and `expense` otherwise.
An item whose name matches one already mapped (`Second Breakfast 3` beside `Second Breakfast`) copies that row instead. Every drafted
row's Note starts with `drafted`, so the guesses can be found later. You review the draft as CSV before it is added; check pass-through
money (`exclude`) and fund moves the names do not reveal.

**One envelope, many names.** Budget tools rename an envelope from year to year (`Second Breakfast`, `Second Breakfast 2`,
`Second Breakfast Fund`). If those map to different CanonicalItems, the per-item charts split one line into several. The Item
mapping tab lists such groups under "names that look like one envelope", with a one-click fix that changes only the canonical name;
adding a row for a new item fills it in from the matching one. The suffixes are the `item_name_suffixes` rule and the fund-move names
the `transfer_item_names` rule, for tools that name things differently.

## Accounts and balances

Budget exports carry payments, not balances: the app knows what was paid toward a debt, not what is left, and nothing about what the
household holds. So once a month it asks for a few month-end balances, one per account (four to eight is the design case).

- **Settings → Accounts** lists them. Kind is `debt` or `asset`. A debt's type is a debt type (empty: its linked canonical item's); an
  asset's is `cash`, `savings`, `retirement`, `brokerage`, `home`, `vehicle` or `other`. A debt's **linked item** is the canonical item its
  payments go to; its optional **APR** and **escrow** (the part of each payment that pays taxes and insurance, not the loan) refine the
  suggestion below. **Person** is one of the people rule, or empty for joint. **Emergency** marks an asset that counts toward the
  emergency fund (on by default for cash and savings); **In net worth** can leave an account out of totals. An account with balances is
  retired (Active no), never deleted.
- **Close a month → Balances at the end of `<month>`** has one field per active account, filled in with a suggestion: for a debt, the
  balance before plus a month's interest at its APR, less the month's payments to its linked item and less escrow; for an asset, the
  balance before. Saving a suggestion unchanged confirms it (stored as `carried`); any other figure is `entered`.
- **A month with no balance** is never stored: the build *estimates* a debt by the same formula and *carries* an asset unchanged, and says
  so (`source` in the `balances` dataset, and the Position tab's table). A typed balance always wins.
- **Balances history** (Settings → Accounts) takes past month-ends, typed or pasted as `account,YYYY-MM,amount`, so a household can
  enter last December's statements and see a year's change at its first annual check-in. A correction replaces the month's figure and
  keeps the old one in `balance_history`.
- From the command line: `python -m app.admin set-accounts FILE` (replaces them, as the CSV on the Settings page),
  `set-balances FILE` (adds or corrects month-ends, never deletes), `export accounts`, `export balances`.

Balances are typed facts, so they travel with the raw exports: `sources.zip` holds `balances.csv`, `settings.zip` holds `accounts.csv`,
and a `SEED_DIR` with both starts a new install with them. The review and MCP see totals by account type (`balance_totals`), never an
account's name.

## Try it with the sample household

`examples/sample-household/` is a synthetic household (two earners, 18 budget months, a student loan, a transfer pair, three-paycheck
months, life events, five accounts with two years of month-end balances) that reconciles clean, so you can see every tab before loading your own exports. The seed files go in `SEED_DIR`
(first boot only) and the exports in `IMPORT_DIR`.

Compose, from a checkout of this repo (`LEDGER_DATA` defaults to `./data`, which git ignores):

```sh
mkdir -p data/seed data/import
cp examples/sample-household/*.json examples/sample-household/item_mapping.csv examples/sample-household/accounts.csv examples/sample-household/balances.csv data/seed/
cp examples/sample-household/*-EveryDollar-*.csv examples/sample-household/2026-06-*.csv data/import/
docker compose up -d
```

Local, against a Postgres of your own:

```sh
export DATABASE_URL=postgresql://ledger:ledger@localhost:5432/ledger AUTH_DISABLED=true SESSION_SECRET=$(openssl rand -hex 32)
export SEED_DIR=$PWD/examples/sample-household IMPORT_DIR=$PWD/examples/sample-household
uvicorn app.main:app --port 8010 --no-proxy-headers --no-access-log
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

1. Authentik → Applications → Providers → **Create** → OAuth2/OpenID Provider. Confidential client, any authorization flow,
   signing key selected. Redirect URIs, both with matching mode **Strict**:
   `https://ledger.example.com/auth/callback`, and `https://ledger.example.com/signed-out` (the `post_logout_redirect_uri` that
   **Sign out** sends).
   Scopes: the default `openid`, `email` and `profile` mappings, plus `offline_access` (the app needs a refresh token for the group
   re-check below). There is no separate groups mapping to add: Authentik's default `profile` mapping is what puts the `groups`
   claim in the token and userinfo. The app also asks for a `groups` scope, for providers that use one; Authentik ignores a scope
   it has no mapping for, so that is harmless. To check, open the provider's **Preview** tab, pick a user, and look for `groups`.
2. Applications → **Create** an application with slug `ledger` (the name can be anything), bound to that provider. Restrict it with
   a policy binding on the group you will put in `ALLOWED_GROUPS`, for example `ledger-users` (the app enforces it too).
3. Put the provider's client ID/secret and the issuer URL in `.env`. The path segment is the application's **slug**, not its name:
   `https://auth.example.com/application/o/ledger/`.

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
household and has a Test connection button. The stored review records `provider:model`.
`REVIEW_MAX_TOKENS` (default 16000) is the output limit, thinking included: a model that thinks before answering can use most of
it (one real review used about 7,900), and a reply that stops at the limit is reported as such. An OpenAI-compatible server that
checks the limit against the model's context length (vLLM does) may need a lower value. Replies wrapped in markdown fences or prose are tolerated.

## Local development

```sh
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
export DATABASE_URL=postgresql://ledger:ledger@localhost:5432/ledger AUTH_DISABLED=true SESSION_SECRET=$(openssl rand -hex 32)
uvicorn app.main:app --reload --port 8010 --no-proxy-headers --no-access-log
```

Layout: `app/` (FastAPI, templates, static), `pipeline/` (the scripts and the seed data), `db/migrations/` (numbered SQL, applied at start), `examples/sample-household/` (synthetic data for a first run).

## Privacy

This is financial data. Keep it behind SSO, keep the database on an encrypted volume, and back it up (see [Backups](#backups): the `ledger`
database plus the `seed/`, `brand/` and `import/` directories are the whole state). The container runs as a non-root user and
makes outbound calls only to the identity provider (sign-in and the group re-check) and when you ask for them: to the review
provider you configured when you press Generate review, to the price-index source when you press Fetch latest on the CPI page, and
to the webhook URL an editor set on the Webhooks page ([docs/WEBHOOKS.md](docs/WEBHOOKS.md)). Only derived figures are sent, never
transactions.

## License

MIT.
