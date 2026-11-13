# Changelog

## [0.7.0](https://github.com/JKBeeman92/household-ledger/compare/v0.6.0...v0.7.0) (2026-11-13)


### Features

* a decisions log: what we decided at the check-in, and whether it happened ([#198](https://github.com/JKBeeman92/household-ledger/issues/198)) ([b08147f](https://github.com/JKBeeman92/household-ledger/commit/b08147f93e8008351129e302baaea0a7396ed8dd))
* dashboard charts as fixes, courses and waypoints, with a table for every chart ([#201](https://github.com/JKBeeman92/household-ledger/issues/201)) ([a2908df](https://github.com/JKBeeman92/household-ledger/commit/a2908df669da5fa9e4e28f4d186f76b8f84f57cf))
* estimated payoff per debt at last month's payment, and a coming_up dataset ([#196](https://github.com/JKBeeman92/household-ledger/issues/196)) ([f035de3](https://github.com/JKBeeman92/household-ledger/commit/f035de3ac5efab0e904ef43edff0c041062df657))
* estimated tax set-aside at the check-in ([#202](https://github.com/JKBeeman92/household-ledger/issues/202)) ([d5e80d5](https://github.com/JKBeeman92/household-ledger/commit/d5e80d52d8c08098ce1b2deb422ad10e51ca8762))
* one formatter for money, dates and months on every server-rendered page ([#195](https://github.com/JKBeeman92/household-ledger/issues/195)) ([bfc931f](https://github.com/JKBeeman92/household-ledger/commit/bfc931fc6d2c62c9e50807af8fc71686fbcb451e))
* the dashboard in keeping with the design system: stamps, sheets, glyphs, page turns ([#205](https://github.com/JKBeeman92/household-ledger/issues/205)) ([1a9e1ab](https://github.com/JKBeeman92/household-ledger/commit/1a9e1abb3882b811cc491444c3f504d93264b9e2))
* the quarter at the check-in, on the dashboard and in the review ([#203](https://github.com/JKBeeman92/household-ledger/issues/203)) ([6160499](https://github.com/JKBeeman92/household-ledger/commit/61604995dededa75dbfa793d907737c074198b83))
* yearly bills: what's due in the next three months, and whether the sinking funds hold it ([#200](https://github.com/JKBeeman92/household-ledger/issues/200)) ([95742c3](https://github.com/JKBeeman92/household-ledger/commit/95742c317af6721b67295ba8fe7844dbc2094798))


### Bug Fixes

* one name for each act on Reviews and the check-in, and a way forward from every dead end ([#199](https://github.com/JKBeeman92/household-ledger/issues/199)) ([0f95a50](https://github.com/JKBeeman92/household-ledger/commit/0f95a50fb96728f042c1c63806743a6c86c8eb48))
* Settings and Close a month in keeping with the design system ([#204](https://github.com/JKBeeman92/household-ledger/issues/204)) ([d8a7daa](https://github.com/JKBeeman92/household-ledger/commit/d8a7daa475935a4d5aa010cff944ad2b2ccfe6b1))
* the 0.7.0 UX critique's findings, focus after check-in actions, and a cheaper ux-critique ([#208](https://github.com/JKBeeman92/household-ledger/issues/208)) ([09518d0](https://github.com/JKBeeman92/household-ledger/commit/09518d0cebad0e51a6de83b9505c316bf2b4a60a))

## [0.6.0](https://github.com/JKBeeman92/household-ledger/compare/v0.5.0...v0.6.0) (2026-11-12)


### ⚠ BREAKING CHANGES

* the container image moves from ghcr.io/jkbeeman92/household-ledger to ghcr.io/jkbeeman92/bearings. Change the image in docker-compose.yml (docs/UPGRADING.md).

### Features

* Bearings, a design system and a new default brand kit ([#194](https://github.com/JKBeeman92/household-ledger/issues/194)) ([5e379bf](https://github.com/JKBeeman92/household-ledger/commit/5e379bf042dc406d716ce9b9e72f6551238d2534))
* count pre-tax saving in a second savings rate and in net worth's split ([#188](https://github.com/JKBeeman92/household-ledger/issues/188)) ([2bde60a](https://github.com/JKBeeman92/household-ledger/commit/2bde60ad938f89fe2121e7a352053f70be24e84b))
* debt-to-income on Last month and Goals, with optional caps ([#185](https://github.com/JKBeeman92/household-ledger/issues/185)) ([ed04925](https://github.com/JKBeeman92/household-ledger/commit/ed04925210497c5e5a088e4c680c9f5b48cc9917))
* emergency-fund coverage on Last month and Goals ([#187](https://github.com/JKBeeman92/household-ledger/issues/187)) ([28ba28e](https://github.com/JKBeeman92/household-ledger/commit/28ba28e84087442caba3d234cdc1a00aa707e96c))
* long lists end their panel; Settings gets sub-tabs, debt types their own ([#184](https://github.com/JKBeeman92/household-ledger/issues/184)) ([bc10c9a](https://github.com/JKBeeman92/household-ledger/commit/bc10c9acb9e8db497a6098340263f31830ab9751))
* net worth on Last month and Position, split into what you did and what happened ([#186](https://github.com/JKBeeman92/household-ledger/issues/186)) ([6c72ee0](https://github.com/JKBeeman92/household-ledger/commit/6c72ee04ffc58d4ba8aaba56e9d8e9c78607d56f))
* record month-end balances for debts and assets ([#182](https://github.com/JKBeeman92/household-ledger/issues/182)) ([1b2d18b](https://github.com/JKBeeman92/household-ledger/commit/1b2d18b2c42d11c238ce912170de3a5af22251cd))
* the monthly check-in, one guided, resumable page per month ([#189](https://github.com/JKBeeman92/household-ledger/issues/189)) ([a4ae05d](https://github.com/JKBeeman92/household-ledger/commit/a4ae05d5220dfe20807b8d54bbf9f955afe83293))

## [0.5.0](https://github.com/JKBeeman92/household-ledger/compare/v0.4.0...v0.5.0) (2026-11-12)


### Features

* a tool-neutral internal format, with EveryDollar as one built-in source profile ([#180](https://github.com/JKBeeman92/household-ledger/issues/180)) ([d270011](https://github.com/JKBeeman92/household-ledger/commit/d2700112838fa11816ccb53265e73ecf0e46c49d))
* category and item roles in rules, so no chart depends on a name typed in the code ([#177](https://github.com/JKBeeman92/household-ledger/issues/177)) ([f42d94d](https://github.com/JKBeeman92/household-ledger/commit/f42d94da6386c85b424890259abd79f40cc48edf))
* debt types, and a Debts tab you can order, group and hide ([#181](https://github.com/JKBeeman92/household-ledger/issues/181)) ([5c35846](https://github.com/JKBeeman92/household-ledger/commit/5c35846e1638dc246ee554ab2b8e6e9be544b921))
* define categories and canonical items once, so a rename is one edit ([#179](https://github.com/JKBeeman92/household-ledger/issues/179)) ([81d1c82](https://github.com/JKBeeman92/household-ledger/commit/81d1c82f7b36e1138acd772af71b640d48f96efc))
* optional kind and person on life events, so the timeline can group or filter them ([#176](https://github.com/JKBeeman92/household-ledger/issues/176)) ([275d05e](https://github.com/JKBeeman92/household-ledger/commit/275d05e808f3fa55b3aa8c70e399492c0e2f257a))

## [0.4.0](https://github.com/JKBeeman92/household-ledger/compare/v0.3.0...v0.4.0) (2026-11-11)


### Features

* a headless admin command line for mapping, rules, events, goals and rebuild ([#135](https://github.com/JKBeeman92/household-ledger/issues/135)) ([f8349bf](https://github.com/JKBeeman92/household-ledger/commit/f8349bf2c57a960193c31e36103af4a76106b343))
* draft an item mapping from the budget groups, and flag names that drift between years ([#134](https://github.com/JKBeeman92/household-ledger/issues/134)) ([770def3](https://github.com/JKBeeman92/household-ledger/commit/770def31cf3427fed7b4ef0fa981947ea49fdfc0))
* export every setting as one zip in the seed-directory shape, and start a new install from it ([#173](https://github.com/JKBeeman92/household-ledger/issues/173)) ([c38af64](https://github.com/JKBeeman92/household-ledger/commit/c38af64490a483f13f45264cacb684bace3c7404)), closes [#160](https://github.com/JKBeeman92/household-ledger/issues/160)
* warn when a BRAND_DIR theme lacks a contract token or fails a contrast pair ([#137](https://github.com/JKBeeman92/household-ledger/issues/137)) ([8324fa3](https://github.com/JKBeeman92/household-ledger/commit/8324fa33bf96931ebd6057efa42acb92acf128c6))


### Bug Fixes

* collapse the life-event list under the timeline behind a count ([#172](https://github.com/JKBeeman92/household-ledger/issues/172)) ([82e911f](https://github.com/JKBeeman92/household-ledger/commit/82e911f200dca81cc0270f5625a5a026d65cef92)), closes [#139](https://github.com/JKBeeman92/household-ledger/issues/139)
* log the client without ":0" and the proxy it came through, in text and JSON alike ([#136](https://github.com/JKBeeman92/household-ledger/issues/136)) ([ebfdc4b](https://github.com/JKBeeman92/household-ledger/commit/ebfdc4bf37fdfb543e808912b3b5d83fc153b8aa))

## [0.3.0](https://github.com/JKBeeman92/household-ledger/compare/v0.2.0...v0.3.0) (2026-11-11)


### Features

* a first-run dashboard with the sample household, a Reviews page of reviews, one tab style ([#130](https://github.com/JKBeeman92/household-ledger/issues/130)) ([a5cbff4](https://github.com/JKBeeman92/household-ledger/commit/a5cbff4fa793a949e801f761805e7f3a97aac107))
* a period filter for the dashboard's timelines ([#117](https://github.com/JKBeeman92/household-ledger/issues/117)) ([ada1479](https://github.com/JKBeeman92/household-ledger/commit/ada1479a1e8285ccb4757eba5b03b4f7a7085e77))
* every chart's figures as a table, and labels that state the data's span ([#128](https://github.com/JKBeeman92/household-ledger/issues/128)) ([7b8933f](https://github.com/JKBeeman92/household-ledger/commit/7b8933fdc46c077a188a7cd6504ec9df44a8b163))
* headline summaries, and colours that know savings and debt want more ([#126](https://github.com/JKBeeman92/household-ledger/issues/126)) ([54bb8e9](https://github.com/JKBeeman92/household-ledger/commit/54bb8e918af38db3204e742220b20db4ef425c46))
* layout pass on every page (text beside charts, tabs, ≤2.5 screens) with chart motion and hover highlighting ([#115](https://github.com/JKBeeman92/household-ledger/issues/115)) ([47c6308](https://github.com/JKBeeman92/household-ledger/commit/47c6308f196a3cb68afc9809f4478c0fa819aa1b))
* say when settings changed since the last rebuild, with a Rebuild now button ([#114](https://github.com/JKBeeman92/household-ledger/issues/114)) ([b87225b](https://github.com/JKBeeman92/household-ledger/commit/b87225b9e93b1573d9adefd87bcbe5876b693878))
* Settings and Close a month for editors who do not read JSON ([#127](https://github.com/JKBeeman92/household-ledger/issues/127)) ([2c74332](https://github.com/JKBeeman92/household-ledger/commit/2c74332df3e2dc2d70e9c60cc8e06a0c4c3ab4eb))
* write month-close reviews in the background, like rebuilds ([#129](https://github.com/JKBeeman92/household-ledger/issues/129)) ([adbd7fa](https://github.com/JKBeeman92/household-ledger/commit/adbd7fa5cc06a6b3fa9d7a0cca7d135f5c0113a9))


### Bug Fixes

* act on the UX review's verified findings ([#119](https://github.com/JKBeeman92/household-ledger/issues/119)) ([4865cef](https://github.com/JKBeeman92/household-ledger/commit/4865cef1162db8453ec4ec7f098418dc5bac3b54))
* install httpx2 so Authlib stops warning that httpx is deprecated at every start ([#104](https://github.com/JKBeeman92/household-ledger/issues/104)) ([55567ff](https://github.com/JKBeeman92/household-ledger/commit/55567ff690c95c5515f49dc64ca9de8e4c5efe71))
* phone first screen, radar labels, plain CPI and review wording ([#131](https://github.com/JKBeeman92/household-ledger/issues/131)) ([c6d0bae](https://github.com/JKBeeman92/household-ledger/commit/c6d0bae2ab49cfc132e6e68de5f0fc0b19d65484))
* raise the review token limit, and say why a review reply could not be read ([#112](https://github.com/JKBeeman92/household-ledger/issues/112)) ([b8f74cb](https://github.com/JKBeeman92/household-ledger/commit/b8f74cb05ef95551acc11aecd1c50c29907abbe3))
* say that a start-up import stored its files when only the rebuild failed ([#103](https://github.com/JKBeeman92/household-ledger/issues/103)) ([9c29c3f](https://github.com/JKBeeman92/household-ledger/commit/9c29c3fdf60cfc708e12acac7dcbf12b484ad9ff))
* show that a review is being generated, and send it only once ([#113](https://github.com/JKBeeman92/household-ledger/issues/113)) ([9ba396b](https://github.com/JKBeeman92/household-ledger/commit/9ba396b16e8112b8f6e21541ba9a03c2dadd9d41))

## [0.2.0](https://github.com/JKBeeman92/household-ledger/compare/v0.1.0...v0.2.0) (2026-11-10)


### ⚠ BREAKING CHANGES

* make the remaining household-specific defaults configurable ([#97](https://github.com/JKBeeman92/household-ledger/issues/97))

### Features

* accept any budget tool's transactions and budget files through a column mapping ([#100](https://github.com/JKBeeman92/household-ledger/issues/100)) ([51eac50](https://github.com/JKBeeman92/household-ledger/commit/51eac506d5663d9ad9fc237c6d91de4e44dcbf0c))
* Homestead default brand with a house-ledger-dollar mark; Red Book example kit ([#99](https://github.com/JKBeeman92/household-ledger/issues/99)) ([830b8a3](https://github.com/JKBeeman92/household-ledger/commit/830b8a391f1f4acd519197f6d5dec6e98a093d85))
* make the remaining household-specific defaults configurable ([#97](https://github.com/JKBeeman92/household-ledger/issues/97)) ([7dd3bcd](https://github.com/JKBeeman92/household-ledger/commit/7dd3bcd1ff7766db57eead932d121e07cff58227))
* War of the Ring example kit; deployment docs and 0.2.0 upgrade notes ([#101](https://github.com/JKBeeman92/household-ledger/issues/101)) ([24de1be](https://github.com/JKBeeman92/household-ledger/commit/24de1bedca8d0964b38eb1da5916ef6df6bc223d))

## 0.1.0 (2026-11-10)


### Features

* Household Ledger, a self-hosted household budget dashboard ([63dbec2](https://github.com/JKBeeman92/household-ledger/commit/63dbec25ea1254a014ef8f8e35908220aa9f0ac4))


### Miscellaneous Chores

* restart versioning at 0.1.0 ([#94](https://github.com/JKBeeman92/household-ledger/issues/94)) ([e408982](https://github.com/JKBeeman92/household-ledger/commit/e4089827f77db0d2010b35f7f87e2b1df079d62c))

## Changelog
