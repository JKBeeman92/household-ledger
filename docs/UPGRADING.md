# Upgrading

Every upgrade, whatever the version:

1. Read the section for each release between yours and the new one, below.
2. Take a backup: `docker compose --profile backup run --rm -e BACKUP_ONCE=1 backup` (or the manual dump in the README, "Backups").
3. Change the image tag in `docker-compose.yml` to the new release, then `docker compose pull && docker compose up -d`, then
   `docker compose logs -f ledger`. Pending migrations apply at start, in
   order, each in its own transaction; a failed one stops the start-up and is named in the log.
4. Run the checklist in [`FIRST-RUN.md`](FIRST-RUN.md).

Migrations are not reversible. To roll back, pin the previous image tag in `docker-compose.yml` and restore the backup from step 2.

## 0.8.0

- **Planned purchases** (#158, `0158_dated_goals.sql`): seven nullable columns on `goals`; existing goals are unchanged and read as
  before. Rebuild once after upgrading to emit `planned_purchases` (in `datasets.zip`, the review and MCP). `goals.json` in
  `settings.zip` carries a planned purchase's columns only for a goal that has them, so an older file still loads.

- **Restoring a dismissed yearly bill** (#206, `0206_yearly_bills_dismissed_from.sql`): one nullable column on `yearly_bills`. A bill
  you added or confirmed and then dismiss now keeps its values, and Restore brings it back as it was. Bills dismissed before this
  upgrade have no record of what they were, so Restore still hands them back to the detector; re-add a hand-added one if you need it.
  `yearly_bills.csv` in `settings.zip` gains a `dismissed_from` column; an older file without it still loads.

## 0.7.0

- **The quarter** (#147): no migration. Rebuild once after upgrading to emit `quarterly`, `cat_quarter` and `quarter_review` (in
  `datasets.zip` and MCP); until then `python -m app.admin quarter` works them out from the last build without saving. Quarters follow
  the existing `year_start_month` rule. The check-in at a quarter's end gains The quarter step, the dashboard a Quarter tab, and that
  month's review one more section.

- **Estimated tax** (#153, `0153_tax_payments.sql`): a new table only. Nothing shows until contract income items are set on Settings →
  Goals → Estimated tax. Three new rules (`contract_income_items`, `tax_set_aside_items`, `tax_deadlines`) default to empty;
  `settings.zip` gains `tax_payments.json`; `datasets.zip` and MCP gain `tax`; `coming_up` gains `tax_deadline` rows.

- **Yearly bills** (#151, `0151_yearly_bills.sql`): a new table only, so nothing existing changes. After upgrading, rebuild once and
  look at Settings → Yearly bills (or `python -m app.admin yearly-bills`): confirm what was found, dismiss what is not a yearly bill,
  add what was missed. `settings.zip` gains `yearly_bills.csv`; `datasets.zip` and MCP gain `yearly_bills`; `coming_up` gains
  `yearly_bill` rows. Two new rules, `yearly_bill_tolerance` and `yearly_bill_min_years`, have defaults.

- **A decisions log** (#155, `0155_decisions.sql`): new tables only (`decisions`, `decision_checks`), so nothing existing changes and
  there is nothing to do. The `renames` log also accepts account renames. The check-in gains two steps, Last month's decisions (4) and
  Next month (9); `settings.zip` gains `decisions.json` (read back by the seed loader); `datasets.zip` and MCP gain `decisions`,
  `decision_summary` and `plan_suggestions`; the review's data gains the household's decisions and verdicts, in place of last
  month's own suggestion. Two new rules, `decision_partly_over` and `decision_partly_share`, have defaults.

- **Payoff estimates and coming up** (#144): no migration, nothing to do. `datasets.zip` gains `payoff` and `coming_up`. For agents:
  MCP's `get_dataset` adds `coming_up` (labels name a debt by its type, never an account), and the month-close review's data gains
  `coming_up` for the next three months.

## 0.6.0

Migrations run at start; read this list before upgrading.

- **The app is now called Bearings, and its image has moved.** Change the image in your `docker-compose.yml` from
  `ghcr.io/jkbeeman92/household-ledger` to `ghcr.io/jkbeeman92/bearings`. The old path keeps its existing tags but receives no new
  ones. Nothing else moves: the repository, the database, the environment variables, the MCP server name and your API tokens are
  unchanged. Your household's own name and title are untouched; only an install that never set a title shows "Bearings" there.
- **A new look and a new default brand kit** (`docs/DESIGN.md`). If you mount your own kit in `BRAND_DIR`, it keeps working: the
  tokens added to the contract (type sizes, motion, `--graticule`, `--glyph-weight`) have defaults. Two things to look at:
  `--surface-300` now also grounds the resting header tabs, and the header draws `mark-small.svg`, which falls back to your
  `mark.svg`. A kit may now also supply `words.json` and `glyphs.svg` (`docs/BRANDING.md`).

- **Accounts and month-end balances** (#143, `0143_accounts_and_balances.sql`): new tables only, so nothing existing changes and there is
  nothing to do. To use them, add what you owe and own on Settings → Accounts, enter last month's balances at Close a month, and paste
  any statement balances you already have under Balances history. `settings.zip` gains `accounts.csv` and `sources.zip` gains
  `balances.csv` (the seed loader reads both from `SEED_DIR`); `datasets.zip` gains `balances` and `balance_totals`. For agents: MCP's
  `get_dataset` adds `balance_totals` (totals by account type, no account names), and the month-close review's data gains `balances`.

- **Debt-to-income** (#145): no migration and nothing to do. To draw a cap on it, add `dti_cap` and/or `housing_dti_cap` (a share of
  gross) on Settings → Goals. Housing counts the `goal_housing_categories` rule, so Utilities are in it unless you change that rule.
  For agents: MCP gains the `debt_to_income` dataset, and the review's data gains `debt_to_income`.
- **Net worth** (#146): no migration. It appears once accounts have balances (above). MCP and the review's data gain `net_worth`.
- **Emergency-fund coverage** (#150): no migration. Cash and savings accounts count by default; switch one off (Emergency no) on
  Settings → Accounts. Add `emergency_months` on Settings → Goals for a target. MCP and the review's data gain `emergency_fund`.
- **Pre-tax saving** (#152): no migration. State each person's contribution on Settings → Goals → Pre-tax saving. The `monthly`
  dataset gains `pretax` and `pretax_real`, `yearly` gains `pretax` and `savings_rate_pretax` (all null until stated); the review's
  data gains `savings_rate`. The Overview's savings-rate line now says "of take-home kept".
- **The monthly check-in** (#164, `0164_checkins.sql`): a new table only. **Check-in** joins the top bar; the first visit says which
  loaded months have no check-in and offers to catch up, which you can also ignore. `settings.zip` gains `checkins.json` (the seed
  loader reads it). A new webhook event, `checkin.completed`, is sent when a check-in is marked Done: a receiver that rejects unknown
  events should allow it. The rules gain `year_start_month` (default 1). See [CHECK-IN.md](CHECK-IN.md).

## 0.5.0

Migrations run at start; read this list before upgrading.

- **A tool-neutral internal format** (#161). Nothing to do: EveryDollar's exports are read as before, and files named
  `YYYY-MM-transactions.csv` / `YYYY-MM-budget.csv` (ISO dates) are now read as they are too (README, "The internal format").
  Rebuilds stage files under those names, so the ledger's `SourceFile` column now shows `2026-03-transactions.csv` where it
  showed `db-03-2026-EveryDollar-Transactions.csv`, and its rows come in budget-month order; no figure changes. Anything that
  ran the standalone scripts by hand needs `pipeline/filenames.py` beside them.
- **Life events gain an optional kind and person** (#109, `0109_life_event_kind.sql`). Existing events keep both empty and draw
  as before; nothing is guessed from their labels. Set them on Settings → Life events. The Employer chart now marks job events,
  and events with no kind, so existing events appear there too until you give them a kind. For agents: MCP's `month_summary`
  adds `event_details` (label, kind, person) beside `events`, and the month-close review's `continuity.life_events` lists
  objects (`{"label", "kind"?, "person"?}`) instead of bare labels.
- **The item mapping is reshaped** (#133, `0133_categories_and_canonical_items.sql`): categories and canonical items become
  tables of their own, and a canonical item has exactly one category. **Before upgrading, download `settings.zip`** (Close a month,
  or `python -m app.admin export-settings DIR`): it holds the old mapping, so nothing is lost if you disagree with what the
  migration chose.
  - Names are tidied as the app tidies typed input (odd and doubled spaces, spaces at the ends), so `Rent ` and `Rent` become one.
    A row whose category is blank or the placeholder `UNMAPPED` (any capitalisation) leaves the mapping and is listed as unmapped; a
    blank canonical item takes the item's name. A canonical item mapped under more than one category keeps the one most of its
    rows use (ties: alphabetical, so check these). Settings → Item mapping lists each change once, until you dismiss it.
  - A `settings.zip` or seed directory written by 0.4 still starts a new install: the same rules apply, with the same notes.
  - To see what it will do first, run the preview against your 0.4 database, or a copy of it. It only reads:
    `docker compose exec -T db psql -U ledger ledger < db/preview/0133_mapping_preview.sql` (from a checkout of 0.5.0), or, with the
    0.5.0 image and `DATABASE_URL` pointing at a copy that has not been started on 0.5.0:
    `docker compose run --rm ledger python -m app.admin preview-mapping-migration`. No output rows means nothing changes.
  - The mapping CSV keeps its five columns everywhere; a CSV that gives one canonical item two categories is now refused, by line.
- **Debt types** (#141, `0141_debt_type.sql`): canonical items gain an optional debt type, empty for every existing one. Exports of the
  mapping (`settings.zip`, the CSV download, `export-mapping`) gain a sixth column, `DebtType`; five-column files still import and
  leave types alone. Once you give any debt a type, the Debts tab groups by it and folds a debt with no payment in the latest 3
  budget months under **Paid off** (the `debt_paid_off_months` rule; 0 turns the fold off). Until then the tab looks as it did.

## 0.4.0

No action is required, and no migration runs. Two things look different:

- **The access log is the app's own line** (#111). uvicorn's `INFO:     203.0.113.7:0 - "GET / HTTP/1.1" 200` is replaced by
  `INFO     ledger.access: 203.0.113.7 via 172.18.0.1 "GET /" 200 4.1ms`: the client without uvicorn's `:0`, and after `via` the
  reverse proxy it came through, which is the value `FORWARDED_ALLOW_IPS` wants. With `LOG_FORMAT=json` the existing fields are
  unchanged and two are added, `client` and `peer`. Anything that parses the text log with a pattern for uvicorn's line needs the new
  shape. `X-Forwarded-*` are now applied inside the app, from the same `FORWARDED_ALLOW_IPS`; the image starts uvicorn with
  `--no-proxy-headers --no-access-log`. **If your compose file overrides the ledger service's `command:`** with the old
  `--proxy-headers --forwarded-allow-ips …` flags, change them to those two: otherwise uvicorn rewrites the address first, the log
  can no longer show the proxy, and every request is logged twice.
- **Other log lines in the readable format** now carry their level and logger (`INFO     mcp.server…: …`) instead of the bare message.

New, optional: **`settings.zip`** on Close a month, every setting as seed files (README, "Backups"). Download one before each later
upgrade: 0.5.0 reshapes the item mapping (#133), and the zip makes the old one recoverable without a database restore.
`python -m app.admin` (README, "Without a browser"); the mapping draft and drift hints on Settings → Item mapping, with
the rules `item_name_suffixes` and `transfer_item_names` (both empty by default, meaning the built-in patterns); start-up warnings for a
`BRAND_DIR` theme (docs/BRANDING.md).

## 0.2.0

**Action required before starting it:**

- **`ALLOWED_GROUPS` has no default any more.** It used to fall back to `admins`. Set it in `.env` to the group(s) in your identity
  provider's `groups` claim that may use the ledger, or the container refuses to start and logs
  `ALLOWED_GROUPS must be set to at least one group`.

**Action required after starting it, if your rules relied on the old built-ins.** Several behaviours that used to be hard-coded
are now rules (Settings, the **Pipeline rules** editor), empty or generic by default:

| Behaviour | Rule | Old built-in value |
|---|---|---|
| Catch-all envelopes chart (Leaks tab) | `catchall_items` | `["Long Term Savings (slush)", "Taxes", "Generosity", "Emergency Fund"]` |
| Savings ratio on the Goals tab | `goal_savings_groups` | `["Joy Saving"]` |
| Verify rows that are envelope moves | `verify_transfer_prefixes` | `["Slush"]` |
| Merchant names that mark a transfer | `transfer_merchants` | see below |
| Item words in "Transfer to …" merchants | `transfer_item_words` | see below |
| Giving and housing categories (Goals tab) | `goal_giving_categories`, `goal_housing_categories` | unchanged: `["Giving"]`, `["Housing", "Utilities"]` |
| Paycheck deposit threshold | `paycheck_min` | unchanged: `500` |

An install that wants exactly the old behaviour adds these keys to its Pipeline rules JSON, saves, then presses **Rebuild without
uploading** on Close a month:

```json
"catchall_items": ["Long Term Savings (slush)", "Taxes", "Generosity", "Emergency Fund"],
"goal_savings_groups": ["Joy Saving"],
"verify_transfer_prefixes": ["Slush"],
"transfer_merchants": "find transfer|office redistribution|end of year rebalance|long term transfer|xbox transfer|house misc transfer|from vacation|from long ?term|holiday|technology|car repair|long term|maintenance|tax slush",
"transfer_item_words": "long ?term(?: savings)?|vacation|holiday|hygiene|grocery|date|health|gifts?|house misc|utilities|mobile|technology|emergency|renters?(?: insurance)?|life insurance|every ?dollar(?: plus)?|meds?|xbox|tax slush|disability|clothing|car repair|office|audible|gym|slush|savings|checking|budget"
```

Keep only the names your own budget uses. To check the result, compare the Method tab's "Inter-item transfers tagged" count and the
Goals tab before and after the upgrade.

**Changes you will notice:**

- **New default look, "Homestead"** (money green and terracotta, a house-ledger-dollar mark). The previous default theme is not
  shipped any more; a `theme.css` / `mark.svg` in `BRAND_DIR` still overrides everything. Two example kits are in
  `examples/brand-kits/`.
- **Empty goal targets show "—"** instead of being read as 0. New installs ship goals with no values; existing goals are kept.
- **Uploads:** a CSV that is not named like an EveryDollar export now opens the column-mapping page (for other budget tools)
  instead of being refused. Nothing is stored until you confirm it. Zips and the import directory still take EveryDollar exports
  only.
- **Sign-in:** an identity provider the container cannot reach now gives a message on the login page instead of a server error.

**Migration:** `0096_source_profiles.sql` adds `source_profiles` and `upload_pending` and two nullable columns on `source_files`.
Existing rows are untouched.
