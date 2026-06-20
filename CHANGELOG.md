# Changelog

All notable changes to `mithwire` are documented here. This file is maintained
automatically by [release-please](https://github.com/googleapis/release-please);
entries below are generated from [Conventional Commit](https://www.conventionalcommits.org/)
messages. Do not edit released sections by hand.

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
