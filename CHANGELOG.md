# Changelog

## [0.3.0](https://github.com/JKBeeman92/household-ledger/compare/v0.2.0...v0.3.0) (2026-11-10)


### Features

* /metrics endpoint, opt-in JSON logs with request id, build status in /healthz ([#65](https://github.com/JKBeeman92/household-ledger/issues/65)) ([5c11b44](https://github.com/JKBeeman92/household-ledger/commit/5c11b4496588641bd220432e13006e1ab81833ab))
* delete a loaded month and keep a sha256 history of replaced files ([#56](https://github.com/JKBeeman92/household-ledger/issues/56)) ([089ed25](https://github.com/JKBeeman92/household-ledger/commit/089ed2592af05168c57e0a7ccecad56c2a6a5420))
* editable review instructions with context preview ([#63](https://github.com/JKBeeman92/household-ledger/issues/63)) ([57d8d9e](https://github.com/JKBeeman92/household-ledger/commit/57d8d9e86113fed11836335110589db065ba6c10))
* export ledger, datasets and raw sources, and scan the import directory on demand ([#66](https://github.com/JKBeeman92/household-ledger/issues/66)) ([6054817](https://github.com/JKBeeman92/household-ledger/commit/6054817fd151687134b823aa12165ef2aa0a8705))
* fetch the latest CPI and list estimated months on the Method tab ([#60](https://github.com/JKBeeman92/household-ledger/issues/60)) ([0bcc7f0](https://github.com/JKBeeman92/household-ledger/commit/0bcc7f09d7a751c5e9f61218f80432a2298b792a))
* format amounts from the household currency and locale ([#68](https://github.com/JKBeeman92/household-ledger/issues/68)) ([26a1df4](https://github.com/JKBeeman92/household-ledger/commit/26a1df41f21b9a21417e794744408fc86e98110e))
* keyboard-operable dashboard, chart text alternatives, measured contrast, print styles ([#84](https://github.com/JKBeeman92/household-ledger/issues/84)) ([2ff5c46](https://github.com/JKBeeman92/household-ledger/commit/2ff5c46a3ae8a75ca8cb78a5aee23aad9f5af37b))
* MCP server for agents, authenticated by API tokens ([#74](https://github.com/JKBeeman92/household-ledger/issues/74)) ([a156894](https://github.com/JKBeeman92/household-ledger/commit/a15689407b4eb0cf8079546ade5f6204265447cd))
* mobile layout pass for phones and small tablets ([#69](https://github.com/JKBeeman92/household-ledger/issues/69)) ([636dd23](https://github.com/JKBeeman92/household-ledger/commit/636dd236a9745e0dc864f5a94f833a243d158a4b))
* month-close checklist with goal confirmation prompts ([#57](https://github.com/JKBeeman92/household-ledger/issues/57)) ([8a0b82b](https://github.com/JKBeeman92/household-ledger/commit/8a0b82b6275ae4d2660a56f7d8b8bb1ee674a8fa))
* personal API tokens for scripts, monitoring and agents ([#73](https://github.com/JKBeeman92/household-ledger/issues/73)) ([ec0eab5](https://github.com/JKBeeman92/household-ledger/commit/ec0eab5f529177a6633414bdc0cfca09f7f97f24))
* pluggable month-close review provider (anthropic, openai-compatible, ollama, gemini) ([#61](https://github.com/JKBeeman92/household-ledger/issues/61)) ([8c20e8c](https://github.com/JKBeeman92/household-ledger/commit/8c20e8c0ef9200a649393945fd5df5539a5939c0))
* review continuity from last month's review, life events and goal confirmations ([#72](https://github.com/JKBeeman92/household-ledger/issues/72)) ([079d40d](https://github.com/JKBeeman92/household-ledger/commit/079d40d01796420a5d4d01e329b5436034307b71))
* review cost guard, past-review audit page and hand-editing ([#62](https://github.com/JKBeeman92/household-ledger/issues/62)) ([6ac6dfe](https://github.com/JKBeeman92/household-ledger/commit/6ac6dfe0b155e776a769203caa34c7b0ea9da3fd))
* run rebuilds in the background with a cross-process lock ([#53](https://github.com/JKBeeman92/household-ledger/issues/53)) ([cbb7081](https://github.com/JKBeeman92/household-ledger/commit/cbb708168d3764633113fcff29c34190b34b8f1b))
* signed outbound webhooks when a month closes ([#75](https://github.com/JKBeeman92/household-ledger/issues/75)) ([39a8ae1](https://github.com/JKBeeman92/household-ledger/commit/39a8ae13004569e4f6c8e74e6e8596168219e4a3))
* stale-data banner, /api/status fields and a months-missing gauge ([#70](https://github.com/JKBeeman92/household-ledger/issues/70)) ([c012b07](https://github.com/JKBeeman92/household-ledger/commit/c012b070a04adc35adb81f5e18c11628fc704f11))
* structured Settings editors with row-level saves ([#59](https://github.com/JKBeeman92/household-ledger/issues/59)) ([a5049d8](https://github.com/JKBeeman92/household-ledger/commit/a5049d8e60e35789db7950f647e3699be4d99b97))
* surface unmapped items and reconciliation problems in the UI ([#55](https://github.com/JKBeeman92/household-ledger/issues/55)) ([7c2e650](https://github.com/JKBeeman92/household-ledger/commit/7c2e650b390db8266f7c6249e25cf32f0f1a121b))
* validate rules and household against published JSON Schemas on save ([#58](https://github.com/JKBeeman92/household-ledger/issues/58)) ([42dfadd](https://github.com/JKBeeman92/household-ledger/commit/42dfaddb78fe3c2725a471fdb76c19fa9d64585c))
* zip upload, drag-and-drop and per-file results on Close a month ([#71](https://github.com/JKBeeman92/household-ledger/issues/71)) ([c8eb126](https://github.com/JKBeeman92/household-ledger/commit/c8eb1266f01bd2ed57a5ce4f0e856908fc81642f))


### Bug Fixes

* dashboard findings from the review (goal captions, radar categories, month axis labels) ([#87](https://github.com/JKBeeman92/household-ledger/issues/87)) ([a24afab](https://github.com/JKBeeman92/household-ledger/commit/a24afabfcd2b13c738b45b5f7c251d718ad75a1b))
* data-path findings from the review (imports, rebuilds, CPI and Settings saves) ([#86](https://github.com/JKBeeman92/household-ledger/issues/86)) ([e8adb2e](https://github.com/JKBeeman92/household-ledger/commit/e8adb2e78ca7d2c9fe2354840b34ed584c2e82ff))
* generate the dashboard's household-specific copy from the data ([#67](https://github.com/JKBeeman92/household-ledger/issues/67)) ([2aa8edd](https://github.com/JKBeeman92/household-ledger/commit/2aa8edd6c640cf75bbfd4b5112f34f945e191425))
* guard AUTH_DISABLED in the container and send expired-session posts to sign-in ([#85](https://github.com/JKBeeman92/household-ledger/issues/85)) ([023ccf8](https://github.com/JKBeeman92/household-ledger/commit/023ccf8298577f366f8bc8b6749e1ff99130da82))


### Performance Improvements

* gzip and ETag-revalidate /api/datasets ([#64](https://github.com/JKBeeman92/household-ledger/issues/64)) ([6a5e9f9](https://github.com/JKBeeman92/household-ledger/commit/6a5e9f983eddaf2cac05f6bc9e8097106135c550))

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
