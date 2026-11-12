# First-run checklist

For a new install, or the first start of a new release (read that release's notes in [`UPGRADING.md`](UPGRADING.md) first). `README.md` covers the install itself; this is what to try once the
container is up. Replace `https://ledger.example.com` with your `BASE_URL`. If a step fails, open an issue with the relevant lines
of `docker compose logs ledger` (check them for anything private first).

- [ ] **The container starts.** `docker compose ps` shows `ledger` healthy. The compose file runs it with a read-only root, no
      capabilities and a 256 MB `/tmp`; an error about a read-only file system means something writes outside `/tmp`, which is a
      bug to report, not a setting to loosen.
- [ ] **Allowed groups.** `ALLOWED_GROUPS` is set in `.env`. Since 0.2.0 it has no default: unset or empty, the container
      refuses to start and `docker compose logs ledger` says so (it used to fall back to `admins`).
- [ ] **Health.** `curl -s http://localhost:8010/healthz` returns `{"ok":true,"last_build":"none"}` on an empty database, and
      `"last_build":"ok"` after the first successful rebuild.
- [ ] **Through the proxy.** `https://ledger.example.com/healthz` answers through the reverse proxy, and the certificate is valid.
      Test from a client (a phone and a laptop), not only from the server: a hostname that exists only in local DNS fails on a
      client that resolves through another server (an ISP's IPv6 DNS, or the browser's secure DNS), and the app logs nothing
      (README, "A hostname that exists only in local DNS"). Then set `FORWARDED_ALLOW_IPS` to the address after `via` in a
      `ledger.access` line of `docker compose logs ledger` (README, "Finding the value for `FORWARDED_ALLOW_IPS`"), restart, and
      check that a request through the proxy still shows `<browser address> via <that address>`.
- [ ] **Sign-in and sign-out.** Sign in through your OIDC provider; **Sign out** also ends the provider session and lands on
      `/signed-out`. This needs the `offline_access` scope and the `/signed-out` redirect URI (README, "Sign-in").
- [ ] **Group re-check.** Remove a test account from the allowed group at the provider; within `GROUP_RECHECK_SECONDS` (default
      five minutes) its next page load is signed out.
- [ ] **Import and rebuild.** With `IMPORT_DIR` set, `docker compose logs ledger | grep "import dir"` shows one line with the
      number of files loaded, and the container starts whatever happens. `import dir … failed` means the import stopped before any
      rebuild (a single unreadable file does not do that: it is skipped and named in the build log); `… N file(s) stored, … the rebuild after them failed` means the files are in and the rebuild stopped. On a
      first start with an empty mapping that is expected: it names the unmapped items. On Settings → Item mapping press **Draft a
      mapping from my budget groups**, check the draft (pass-through money needs `exclude`), add it, then press **Rebuild without
      uploading**. Without a browser: `docker compose exec ledger python -m app.admin draft-mapping > draft.csv`, review it, then
      `add-mapping` and `rebuild` (README, "Without a browser").
      Upload one month on **Close a month** (a `.zip` works too): the page returns at once, shows the build running, then a
      "Last upload" panel and a clean reconciliation. Upload a file of a few MB through the proxy too: a 413 from the proxy (not the
      app) means its body-size limit is below `MAX_UPLOAD_MB`.
- [ ] **Another budget tool** (optional). Upload a transactions CSV and a budget CSV that are not named like exports (the internal format, or EveryDollar's): the mapping page
      opens, and confirming it stores the month (README, "Other budget tools").
- [ ] **Dashboard.** All eight tabs draw on a desktop and on a phone.
- [ ] **Settings.** Edit and save one mapping row, one goal and one event; a deliberate typo in the rules JSON is refused with the
      key named. If Item mapping lists "names that look like one envelope", decide for each: one canonical name, or apart on purpose.
- [ ] **Own brand kit** (optional). With a `theme.css` in `BRAND_DIR`, `docker compose logs ledger | grep "brand:"` shows either
      "passes every contrast check" or one line per failing pair, and Settings shows the same count (docs/BRANDING.md).
- [ ] **Check-in.** **Check-in** in the top bar opens the latest month's check-in; each step opens in place, and `python -m app.admin
      checkin status` prints the same steps.
- [ ] **Command line.** `docker compose exec ledger python -m app.admin export rules` prints the rules JSON.
- [ ] **Review** (optional). With a provider configured, generate one review (it asks for confirmation and shows an approximate
      input-token count), open `/reviews/history` to see the prompt that was sent, then hand-edit a section and save it.
- [ ] **Price index.** **Fetch latest CPI** on the CPI page reaches the source and reports months added or replaced.
- [ ] **Exports.** Download `ledger.csv`, `datasets.zip`, `sources.zip` and `settings.zip` from **Close a month**. Keep the
      `settings.zip`: it is your mapping, goals, events and rules as seed files, the way back if a later upgrade's migration goes wrong.
- [ ] **API token and metrics.** Create a `read` token on Settings → API tokens;
      `curl -H "Authorization: Bearer hl_…" https://ledger.example.com/metrics` returns Prometheus text; revoke the token and the
      same call returns 401.
- [ ] **MCP** (optional). Add the endpoint to an MCP client (README, "Connect an agent") and ask it for the list of months.
- [ ] **Webhook** (optional). Set a URL and secret on the Webhooks page and press **Send test event**.
- [ ] **Backup and restore.** Take one dump (README, "Backups") and restore it into a scratch database.
