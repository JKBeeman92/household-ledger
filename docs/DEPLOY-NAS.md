# Deploying on the Beeman NAS

Owner's deployment notes. Nothing here is required to run the app elsewhere; `README.md` covers the generic install.
Hostname `ledger.beeman.family`, host port `8010`, app directory `/volume2/ledger/`, Authentik group `admins` only.

> The Red Book of Westmarch was kept by the family, not lent to the library of Minas Tirith.

## Layout on the NAS

```
/volume2/ledger/
  docker-compose.yml     from this repo
  .env                   from .env.example, chmod 600 — never in any repo
  postgres/              Postgres data (bind mount)
  brand/                 theme.css, mark.svg, moonlit-valley.png  (examples/brand-beeman/)
  seed/                  item_mapping.csv, goals.json, rules.json, events.json, review.json, household.json — private household data
  import/                the raw EveryDollar export CSVs; new or changed files load on every container start
  backups/               only if the optional `backup` compose profile is used (step 7)
```

## Steps

1. **Image access.** The GHCR package is public, or `docker login ghcr.io` on the NAS with a PAT that has `read:packages`.
2. **Port check.** `ss -tlnp | grep 8010` prints nothing.
3. **Lay out the directory** as above. `.env`: `LEDGER_DATA=/volume2/ledger`, `POSTGRES_PASSWORD` and `SESSION_SECRET` from
   `openssl rand -hex 32`, `BASE_URL=https://ledger.beeman.family`. Leave `EDITOR_GROUPS` unset: everyone in `admins` edits.
   For the first run of a release, pin the image in `docker-compose.yml` to that version (`:0.3.0`) instead of `:latest`,
   so the thing tested is the thing that stays running.
4. **Authentik.** Providers → OAuth2/OpenID, confidential, authorization code, redirect URI
   `https://ledger.beeman.family/auth/callback`, signing key set, scopes `openid email profile` + the default groups mapping.
   Also scope `offline_access` (refresh token for the 5-minute group re-check) and redirect URI `https://ledger.beeman.family/signed-out`
   (where Sign out lands after Authentik's end-session).
   Applications → `ledger` → that provider → policy binding to `admins`. Issuer: `https://auth.beeman.family/application/o/ledger/`.
   Client ID/secret and issuer into `.env`. Leave `ANTHROPIC_API_KEY` empty until step 8.
5. **Deploy.** `docker compose config` (look for empty values), `docker compose up -d`.
   `docker compose logs ledger | grep "import dir"` → one line with `'loaded': 212` and a clean reconciliation. A line reading
   `import dir … failed` names what went wrong; the container still starts, and Close a month shows the same failure.
   `curl -s http://192.168.1.103:8010/healthz` → `{"ok":true,"last_build":"ok"}`.
   The container runs with a read-only root and a 256 MB `/tmp`; a start-up error about a read-only file system means
   something writes outside `/tmp` and is a bug to report, not a setting to loosen.
6. **Routes** (homelab conventions):
   - Traefik `dynamic.yml`: router `ledger` → `Host(\`ledger.beeman.family\`)`, entryPoint `websecure`, certResolver `cloudflare`;
     service `ledger` → `http://192.168.1.252:8010`. Restart Traefik if the file edit is not picked up.
   - Pi-hole: local record `ledger.beeman.family → 192.168.1.251` plus `local=/ledger.beeman.family/` in `misc.dnsmasq_lines`
     (read the list first; `pihole-FTL --config` replaces it whole).
   - Cloudflare Tunnel: published route `ledger.beeman.family → http://192.168.1.103:8010`.
   - Sign in through Authentik from off-LAN. Then, in the homelab repo, add the SSO row to `docs/ARCHITECTURE.md` and the
     service entry to `docs/SERVICES.md` (those docs are the homelab repo's inventory; this app's code and config stay here).
7. **Backup.** In the homelab `backup.sh`, *after* `/volume2/ledger` exists:
   `docker exec ledger-db pg_dump -U ledger ledger | gzip > "$DUMP_DIR/ledger.sql.gz.tmp" && mv … ledger.sql.gz`;
   add `/volume2/ledger` to the include list and `--exclude /volume2/ledger/postgres`. Run one backup by hand.
   (The compose file also has its own optional dump service, `docker compose --profile backup up -d`, writing to
   `/volume2/ledger/backups/`. One of the two is enough; the homelab script keeps every service's backup in one place.)
8. **Review.** Put an Anthropic API key in `.env`, `docker compose up -d`, press **Generate review** for the latest month.
   It now asks for confirmation first and shows an approximate input-token count. Another provider (OpenAI-compatible,
   Ollama, Gemini) can be chosen on the **Reviews** page instead.
9. **Retire the artifact.** Spot-check the four headline tiles and the Goals tab against the Claude artifact, then delete it and
   point the first-Sunday reminder at `https://ledger.beeman.family/upload`.

## Updating

`docker compose pull && docker compose up -d` (or the image updater). Pending database migrations apply themselves on start. To pin a major
version instead of following `main`, set the image tag to `:0` (or `:1` once released) in `docker-compose.yml`.

## First run of 0.3.0

0.3.0 is the first release with everything from the 33-issue pass. All of it passed the test suite, and none of it has run
outside one: no real container start, no real Authentik, no live review provider. This list is what to exercise on the NAS,
roughly in the order the steps above reach it. Report anything that fails as an issue with the container log lines.

- [ ] **Container starts hardened.** `docker compose up -d` with the current `docker-compose.yml` (read-only root, dropped
      capabilities, tmpfs `/tmp`); `docker compose ps` shows `ledger` healthy; ten migrations apply on a fresh database.
- [ ] **Sign-in and sign-out** through Authentik: the callback works, **Sign out** ends the Authentik session and lands on
      `/signed-out`. Needs the `offline_access` scope and the `/signed-out` redirect URI from step 4.
- [ ] **Group re-check.** Remove the test account from `admins` in Authentik; within five minutes (`GROUP_RECHECK_SECONDS`)
      its next page load is signed out.
- [ ] **Import and rebuild.** The start-up import loads the export files; Close a month shows the build as ok and the
      reconciliation clean. Upload one month again (as a `.zip` too): the page returns at once, shows the build running, then
      reloads with a "Last upload" panel.
- [ ] **Dashboard.** All eight tabs draw on a desktop and on a phone; the figures match the old artifact (step 9).
- [ ] **Settings.** Edit and save one mapping row, one goal and one event; a deliberate typo in the rules JSON is refused with
      the key named.
- [ ] **Review.** Generate one review (confirmation, token estimate), open the review history (`/reviews/history`) to see the prompt
      that was sent, hand-edit a section and save it.
- [ ] **CPI.** **Fetch latest CPI** on the CPI page reaches FRED and reports months added or replaced.
- [ ] **Exports.** Download `ledger.csv`, `datasets.zip` and `sources.zip` from Close a month.
- [ ] **API token and metrics.** Create a `read` token on Settings → API tokens;
      `curl -H "Authorization: Bearer hl_…" https://ledger.beeman.family/metrics` returns Prometheus text; revoke the token and
      the same call returns 401.
- [ ] **MCP.** `claude mcp add --transport http household-ledger https://ledger.beeman.family/mcp --header "Authorization: Bearer hl_…"`,
      then ask for the list of months. This is the proof on `mcp` 1.x that the 2.x migration (#88) waits for.
- [ ] **Webhook** (optional). Point the Webhooks page at ntfy or Home Assistant and press **Send test event**.
- [ ] **Backup.** One backup by hand (step 7), and one restore of that dump into a scratch database.

Not covered by this list: the arm64 image (it only gets a start if the NAS itself is arm64) and a screen reader pass over the
dashboard. The `:0.3.0` tag exists once the release pull request is merged; until then only `:latest` carries this code.

## Open items
- `investing_per_paycheck` goal has no value yet (Settings → Goals).
- The `Boa` $8,121 row in Jun 2024 is still flagged VERIFY on the Method tab.
- To let `family` see the dashboard later: add the group to the Authentik binding *and* to `ALLOWED_GROUPS`.
