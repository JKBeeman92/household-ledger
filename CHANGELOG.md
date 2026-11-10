# Changelog

## [0.3.0](https://github.com/JKBeeman92/household-ledger/compare/v0.2.0...v0.3.0) (2026-11-10)


### Features

* Household Ledger, a self-hosted household budget dashboard ([63dbec2](https://github.com/JKBeeman92/household-ledger/commit/63dbec25ea1254a014ef8f8e35908220aa9f0ac4))

## [0.2.0](https://github.com/JKBeeman92/household-ledger/compare/v0.1.0...v0.2.0) (2026-11-09)


### Features

* enforce per-session CSRF tokens on every state-changing request ([#50](https://github.com/JKBeeman92/household-ledger/issues/50)) ([bbd1e5c](https://github.com/JKBeeman92/household-ledger/commit/bbd1e5c494a0b5fdb187755ad4e7d0b9309ed7fb))
* numbered SQL migrations with an applied-set table ([#43](https://github.com/JKBeeman92/household-ledger/issues/43)) ([2676b3e](https://github.com/JKBeeman92/household-ledger/commit/2676b3ea260ff655c869ee5d4387b2bf88621abb))
* OIDC logout with id_token_hint, group re-check and security headers ([#51](https://github.com/JKBeeman92/household-ledger/issues/51)) ([763ad5a](https://github.com/JKBeeman92/household-ledger/commit/763ad5a077208d01afb7be88b798a90fc3d78f20))
* optional backup service and a README Backups section ([#48](https://github.com/JKBeeman92/household-ledger/issues/48)) ([fe8da7d](https://github.com/JKBeeman92/household-ledger/commit/fe8da7d4824db0bb19e21530c50738f57b361616))
* synthetic sample household for first run and demos ([#41](https://github.com/JKBeeman92/household-ledger/issues/41)) ([92f1670](https://github.com/JKBeeman92/household-ledger/commit/92f16700e054ff1ae8369a4314532bc260164b12))
* viewer and editor roles via EDITOR_GROUPS ([#52](https://github.com/JKBeeman92/household-ledger/issues/52)) ([24da9c3](https://github.com/JKBeeman92/household-ledger/commit/24da9c38aa71d15b6456ad131cbd8abc1630d3d7))


### Bug Fixes

* keep a flash message added while another is still unread ([#45](https://github.com/JKBeeman92/household-ledger/issues/45)) ([dcafdbf](https://github.com/JKBeeman92/household-ledger/commit/dcafdbfb0f1d6c72d2824aa549a5c50b272214f7))
* make pipeline/reconcile.py run as a standalone script again ([#47](https://github.com/JKBeeman92/household-ledger/issues/47)) ([de5976e](https://github.com/JKBeeman92/household-ledger/commit/de5976e0d983cae3de089bdd9e6b661b4a4fdcd1))
* refuse to start on invalid production config and harden login redirects ([#49](https://github.com/JKBeeman92/household-ledger/issues/49)) ([fd68d7d](https://github.com/JKBeeman92/household-ledger/commit/fd68d7d9356ccae65a5f18d785cc23be8b9a7ab0))
* reject protocol-relative next on /login ([#39](https://github.com/JKBeeman92/household-ledger/issues/39)) ([f82c4c3](https://github.com/JKBeeman92/household-ledger/commit/f82c4c302e2dffd0adfb28107f7d675c38c269f2))
