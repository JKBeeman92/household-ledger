# Upgrading

Every upgrade, whatever the version:

1. Read the section for each release between yours and the new one, below.
2. Take a backup: `docker compose --profile backup run --rm -e BACKUP_ONCE=1 backup` (or the manual dump in the README, "Backups").
3. Change the image tag in `docker-compose.yml` to the new release, then `docker compose pull && docker compose up -d`, then
   `docker compose logs -f ledger`. Pending migrations apply at start, in
   order, each in its own transaction; a failed one stops the start-up and is named in the log.
4. Run the checklist in [`FIRST-RUN.md`](FIRST-RUN.md).

Migrations are not reversible. To roll back, pin the previous image tag in `docker-compose.yml` and restore the backup from step 2.

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
