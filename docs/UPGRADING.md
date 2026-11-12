# Upgrading

Every upgrade, whatever the version:

1. Read the section for each release between yours and the new one, below.
2. Take a backup: `docker compose --profile backup run --rm -e BACKUP_ONCE=1 backup` (or the manual dump in the README, "Backups").
3. Change the image tag in `docker-compose.yml` to the new release, then `docker compose pull && docker compose up -d`, then
   `docker compose logs -f ledger`. Pending migrations apply at start, in
   order, each in its own transaction; a failed one stops the start-up and is named in the log.
4. Run the checklist in [`FIRST-RUN.md`](FIRST-RUN.md).

Migrations are not reversible. To roll back, pin the previous image tag in `docker-compose.yml` and restore the backup from step 2.

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
