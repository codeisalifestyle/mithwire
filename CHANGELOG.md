# Changelog

All notable changes to `mithwire` are documented here. This file is maintained
automatically by [release-please](https://github.com/googleapis/release-please);
entries below are generated from [Conventional Commit](https://www.conventionalcommits.org/)
messages. Do not edit released sections by hand.

## [0.50.9](https://github.com/codeisalifestyle/mithwire/compare/v0.50.8...v0.50.9) (2026-07-18)


### Features

* add engine-aware stealth controller for CloakBrowser integration ([a7bac5d](https://github.com/codeisalifestyle/mithwire/commit/a7bac5d225b6e65e77192ef09e4f9ddb866a3cc2))
* add environment spoofs for headless detection reduction ([7d01000](https://github.com/codeisalifestyle/mithwire/commit/7d010003b367764f099778128f4c77c5f1eae577))
* auto-align navigator.userAgent OS token to match fp.platform ([ce240c3](https://github.com/codeisalifestyle/mithwire/commit/ce240c3c3a6bf1ed5f64dccddda26056c7dc5420))
* auto-align navigator.userAgent OS token to match fp.platform ([0a650f6](https://github.com/codeisalifestyle/mithwire/commit/0a650f671df156fecb4dd9aec6f2feab9402c20a))


### Bug Fixes

* headless launch flags — window/screen mismatch, Worker UA leak, font hinting ([1fbfd39](https://github.com/codeisalifestyle/mithwire/commit/1fbfd397de63c358b4b9570e20dd2d8f27895e4b))
* simulate browser chrome in viewport metrics for headless detection evasion ([8986749](https://github.com/codeisalifestyle/mithwire/commit/8986749c67acde573eaa1a871e594ed547b1edce))
* **stealth:** universal navigator.platform spoofing across all contexts ([f2688ac](https://github.com/codeisalifestyle/mithwire/commit/f2688ac73dd20b625e0d4d4ee1f2f6f1d76fcf35))
* **stealth:** universal navigator.platform spoofing across all contexts ([60e05d5](https://github.com/codeisalifestyle/mithwire/commit/60e05d53b63207bccf11061af6b1d2f5c36b654b))
* use Emulation domain for headless UA cleanup (not Network) ([94dce9b](https://github.com/codeisalifestyle/mithwire/commit/94dce9b4b8fa7bf5c8364c7dd1950c0c8e8b67ea))
* use Emulation domain for headless UA cleanup + inject Chrome brand ([c403b0e](https://github.com/codeisalifestyle/mithwire/commit/c403b0ee6323f6540d515bf00125fccc73a1ebca))


### Refactors

* remove auto WebGL spoof — isAntiDetect tradeoff is net negative ([00394c6](https://github.com/codeisalifestyle/mithwire/commit/00394c62cc597de9b1772caa35ca2043f26b4ed3))

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
