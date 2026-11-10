# Security

This app holds a household's financial history. Report vulnerabilities privately through
[GitHub security advisories](https://github.com/JKBeeman92/household-ledger/security/advisories/new), not as a public issue.

What the app assumes: it runs behind an OIDC provider (`ALLOWED_GROUPS` enforced on every page and API call), on a private
network or behind a tunnel, with the database reachable only from the app container. `AUTH_DISABLED` is for local development and
must never be set on a reachable host. It makes outbound calls only when a user asks for one: to the
configured review provider on **Generate review** (derived figures such as planned vs spent and category totals, never
transactions, merchants or notes), to the price-index source on **Fetch latest CPI**, and to a webhook URL if an editor has set one.

Supported: the latest release and `main`. Dependabot keeps dependencies current; the image is rebuilt on every merge.
