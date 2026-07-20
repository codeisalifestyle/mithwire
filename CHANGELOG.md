# Changelog

All notable changes to `mithwire` are documented here. This file is maintained
automatically by [release-please](https://github.com/googleapis/release-please);
entries below are generated from [Conventional Commit](https://www.conventionalcommits.org/)
messages. Do not edit released sections by hand.

## [0.51.2](https://github.com/codeisalifestyle/mithwire/compare/v0.51.1...v0.51.2) (2026-07-20)


### Features

* add proxy, fingerprint_gen, cloakbrowser, virtual_display modules ([86ff6a7](https://github.com/codeisalifestyle/mithwire/commit/86ff6a794af03473f93b76c1bc1413e0490774ed))
* add proxy, fingerprint_gen, cloakbrowser, virtual_display to engine ([2b29dfb](https://github.com/codeisalifestyle/mithwire/commit/2b29dfb1fb0b29f347a5043da549210edb5c7d54))
* guard stealth launch args to prevent CDP/binary worker inconsistency ([892fd32](https://github.com/codeisalifestyle/mithwire/commit/892fd32b45ad38f1330ab76385c67af6e2515766))
* rename engine mode stock → cdp ([29dbab6](https://github.com/codeisalifestyle/mithwire/commit/29dbab620f7eb22edfe42b381fedbeea5c9b0c92))
* rename engine mode stock → cdp with deprecation alias ([addc2ab](https://github.com/codeisalifestyle/mithwire/commit/addc2ab5601ca25bd27de14d5a0e3f0ab26b16cc))
* stealth engine guard — prevent CDP/binary worker inconsistency ([92c03dc](https://github.com/codeisalifestyle/mithwire/commit/92c03dc4f96688ab12793306497d6276f0ab5faa))
* stealth engine guard + macOS stealth support ([b41aed0](https://github.com/codeisalifestyle/mithwire/commit/b41aed070f6d8008e29d140ad8fa79827ae2c616))

## [0.51.1](https://github.com/codeisalifestyle/mithwire/compare/v0.51.0...v0.51.1) (2026-07-19)


### Bug Fixes

* **ci:** add timeout-minutes to all release jobs ([9e7cffc](https://github.com/codeisalifestyle/mithwire/commit/9e7cffca0a392f895c0aa205d0c73a9474f2e7d4))
* **ci:** add timeout-minutes to all release jobs ([392a80b](https://github.com/codeisalifestyle/mithwire/commit/392a80bcf4d0add82b877289d70716a3b7f17b81))

## [0.51.0](https://github.com/codeisalifestyle/mithwire/compare/v0.50.8...v0.51.0) (2026-07-19)


### Features

* **stealth:** dual-mode stealth engine — stock (CDP-only) and stealth (CloakBrowser binary) ([a7bac5d](https://github.com/codeisalifestyle/mithwire/commit/a7bac5d))
* **stealth:** add environment spoofs for headless detection reduction (media devices, battery, speech voices) ([7d01000](https://github.com/codeisalifestyle/mithwire/commit/7d01000))


### Bug Fixes

* **stealth:** force 4G effective connection type in headless mode to fix connectionRTT detection ([a71b841](https://github.com/codeisalifestyle/mithwire/commit/a71b841))
* **stealth:** headless launch flags — window/screen mismatch, Worker UA leak, font hinting ([1fbfd39](https://github.com/codeisalifestyle/mithwire/commit/1fbfd39))
* **stealth:** simulate browser chrome in viewport metrics for headless detection evasion ([8986749](https://github.com/codeisalifestyle/mithwire/commit/8986749))


### Refactors

* **stealth:** remove auto WebGL spoof — isAntiDetect tradeoff is net negative ([00394c6](https://github.com/codeisalifestyle/mithwire/commit/00394c6))

## [0.50.8](https://github.com/codeisalifestyle/mithwire/compare/v0.50.7...v0.50.8) (2026-07-14)


### Bug Fixes

* **stealth:** hide headless Chrome blank window on Windows ([66734cb](https://github.com/codeisalifestyle/mithwire/commit/66734cb050f487c72429833673bb78438ae8e7d2))

## [0.50.7](https://github.com/codeisalifestyle/mithwire/compare/v0.50.6...v0.50.7) (2026-06-20)


### Bug Fixes

* **stealth-diagnostic:** stop sync wrapper from shadowing the subpackage ([#10](https://github.com/codeisalifestyle/mithwire/issues/10)) ([5d2ae72](https://github.com/codeisalifestyle/mithwire/commit/5d2ae72be82c4c9986c75b4829526b139c89045c))

## [0.50.6](https://github.com/codeisalifestyle/mithwire/compare/v0.50.5...v0.50.6) (2026-06-20)


### Features

* **stealth-diagnostic:** bundle a stealth diagnostic with the engine ([#7](https://github.com/codeisalifestyle/mithwire/issues/7)) ([2070b44](https://github.com/codeisalifestyle/mithwire/commit/2070b44c8bd770c45e18fe870b7e386878898c07))

## [0.50.5](https://github.com/codeisalifestyle/mithwire/compare/v0.50.4...v0.50.5) (2026-06-13)


### Bug Fixes

* ship the mithwire.stealth subpackage in the wheel ([#5](https://github.com/codeisalifestyle/mithwire/issues/5)) ([1b83bbc](https://github.com/codeisalifestyle/mithwire/commit/1b83bbc499de58355754907ba4c2c830aee1e7f7))

## [0.50.4](https://github.com/codeisalifestyle/mithwire/compare/v0.50.3...v0.50.4) (2026-06-13)


### Features

* **stealth:** engine owns all anti-detect stealth ([#2](https://github.com/codeisalifestyle/mithwire/issues/2)) ([cda7806](https://github.com/codeisalifestyle/mithwire/commit/cda7806112d7a7a6b05647fc47aea27f0e8ea8bb))

## [0.50.3] - 2026-06-04

Rebrand to `mithwire`: a maintained, stealth-focused fork of nodriver that
talks directly to Chrome over CDP. Merges upstream nodriver 0.50.3 (flat mode),
plus reforged fixes — wider DevTools-connect backoff window, always-emitted
DevTools bind address for Chrome 136+, and DOM-geometry Turnstile solving.
