<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Changelog

What changed for the athlete, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the detail
behind each line is in the git history.

## [Unreleased]

### Added

- Connect intervals.icu: sign in at intervals.icu and approve what Endury may
  do, instead of pasting an API key. The access covers your activities,
  wellness, calendar and settings only, and removing Endury from your apps at
  intervals.icu ends it. An API key still works.

### Fixed

- An account used with an API key on one device and Connect intervals.icu on
  another no longer keeps two copies of its data. Once a device connects, the
  account belongs to that sign-in: the others open read-only until they
  connect too.

## [0.1.0]

The first public release: a free, open-source endurance coach that runs
entirely in your browser. Pick a race and it builds the whole preparation
from the training you already do, puts every session on your intervals.icu
calendar, and adapts when life gets in the way.
