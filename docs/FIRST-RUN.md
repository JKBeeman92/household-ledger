# First-run checklist

For a new install, or the first start of a new release. `README.md` covers the install itself; this is what to try once the
container is up. Replace `https://ledger.example.com` with your `BASE_URL`. If a step fails, open an issue with the relevant lines
of `docker compose logs ledger` (check them for anything private first).

- [ ] **The container starts.** `docker compose ps` shows `ledger` healthy. The compose file runs it with a read-only root, no
      capabilities and a 256 MB `/tmp`; an error about a read-only file system means something writes outside `/tmp`, which is a
      bug to report, not a setting to loosen.
- [ ] **Health.** `curl -s http://localhost:8010/healthz` returns `{"ok":true,"last_build":"none"}` on an empty database, and
      `"last_build":"ok"` after the first successful rebuild.
- [ ] **Sign-in and sign-out.** Sign in through your OIDC provider; **Sign out** also ends the provider session and lands on
      `/signed-out`. This needs the `offline_access` scope and the `/signed-out` redirect URI (README, "Sign-in").
- [ ] **Group re-check.** Remove a test account from the allowed group at the provider; within `GROUP_RECHECK_SECONDS` (default
      five minutes) its next page load is signed out.
- [ ] **Import and rebuild.** With `IMPORT_DIR` set, `docker compose logs ledger | grep "import dir"` shows one line with the
      number of files loaded; a line reading `import dir … failed` names what went wrong and the container still starts.
      Upload one month on **Close a month** (a `.zip` works too): the page returns at once, shows the build running, then a
      "Last upload" panel and a clean reconciliation.
- [ ] **Dashboard.** All eight tabs draw on a desktop and on a phone.
- [ ] **Settings.** Edit and save one mapping row, one goal and one event; a deliberate typo in the rules JSON is refused with the
      key named.
- [ ] **Review** (optional). With a provider configured, generate one review (it asks for confirmation and shows an approximate
      input-token count), open `/reviews/history` to see the prompt that was sent, then hand-edit a section and save it.
- [ ] **Price index.** **Fetch latest CPI** on the CPI page reaches the source and reports months added or replaced.
- [ ] **Exports.** Download `ledger.csv`, `datasets.zip` and `sources.zip` from **Close a month**.
- [ ] **API token and metrics.** Create a `read` token on Settings → API tokens;
      `curl -H "Authorization: Bearer hl_…" https://ledger.example.com/metrics` returns Prometheus text; revoke the token and the
      same call returns 401.
- [ ] **MCP** (optional). Add the endpoint to an MCP client (README, "Connect an agent") and ask it for the list of months.
- [ ] **Webhook** (optional). Set a URL and secret on the Webhooks page and press **Send test event**.
- [ ] **Backup and restore.** Take one dump (README, "Backups") and restore it into a scratch database.
