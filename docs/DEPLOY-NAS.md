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
```

## Steps

1. **Image access.** The GHCR package is public, or `docker login ghcr.io` on the NAS with a PAT that has `read:packages`.
2. **Port check.** `ss -tlnp | grep 8010` prints nothing.
3. **Lay out the directory** as above. `.env`: `LEDGER_DATA=/volume2/ledger`, `POSTGRES_PASSWORD` and `SESSION_SECRET` from
   `openssl rand -hex 32`, `BASE_URL=https://ledger.beeman.family`.
4. **Authentik.** Providers → OAuth2/OpenID, confidential, authorization code, redirect URI
   `https://ledger.beeman.family/auth/callback`, signing key set, scopes `openid email profile` + the default groups mapping.
   Also scope `offline_access` (refresh token for the 5-minute group re-check) and redirect URI `https://ledger.beeman.family/signed-out`
   (where Sign out lands after Authentik's end-session).
   Applications → `ledger` → that provider → policy binding to `admins`. Issuer: `https://auth.beeman.family/application/o/ledger/`.
   Client ID/secret and issuer into `.env`. Leave `ANTHROPIC_API_KEY` empty until step 8.
5. **Deploy.** `docker compose config` (look for empty values), `docker compose up -d`.
   `docker compose logs ledger | grep "import dir"` → `loaded: 212` and a clean reconciliation.
   `curl -s http://192.168.1.103:8010/healthz` → `{"ok":true}`.
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
8. **Review.** Put an Anthropic API key in `.env`, `docker compose up -d`, press **Generate review** for the latest month.
9. **Retire the artifact.** Spot-check the four headline tiles and the Goals tab against the Claude artifact, then delete it and
   point the first-Sunday reminder at `https://ledger.beeman.family/upload`.

## Updating

`docker compose pull && docker compose up -d` (or the image updater). Pending database migrations apply themselves on start. To pin a major
version instead of following `main`, set the image tag to `:0` (or `:1` once released) in `docker-compose.yml`.

## Open items
- `investing_per_paycheck` goal has no value yet (Settings → Goals).
- The `Boa` $8,121 row in Jun 2024 is still flagged VERIFY on the Method tab.
- To let `family` see the dashboard later: add the group to the Authentik binding *and* to `ALLOWED_GROUPS`.
