<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Documentation

Old Phone OS Interface is a prototype. The desktop demo simulates the battery, calls and texts; recorded QEMU
results and model experiments have their own limits. The postmarketOS kiosk uses the same simulated battery
and modem as the desktop demo. Documents marked **historical** preserve
earlier work and are not current instructions or evidence about the current source.

## Use OPOS

- [Project README](../README.md): quickstart, demo walkthrough, per-user installation, tests and limits.
- [Why Old Phone OS Interface](WHY.md): the problem OPOS takes on, its essential-task policy and what the prototype shows.
- [AI coding tools](ai-tools.md): optional coding-tool installation, storage, Lite/Full power behavior and uninstall.

## How it works

- [Architecture](architecture.md): module map, how a request flows from the page to the governor and to Muse Glimmer, page assembly and tests.
- [Rules remain the safety floor](design-safety-floor.md): how the governor validates model advice so it can never override the rules, which limits are hard floors, what Undo can override and how the goal budget is shared.

## Meta AI and evaluation

- [OPOS and Meta models](META.md): runtime routes, optional inference, deterministic safeguards and simulation results.
- [Meta model field report](meta-model-field-report.md): field observations from one project, eight findings and five requests for Meta's model teams.
- [Worn-battery results](worn-battery-results.md): simulated service durations, resistance assumptions and brownout/shutdown accounting.

## Install and packaging

- [OPOS on postmarketOS](pmos.md): package inputs, local-source staging, QEMU development and validation limits.
- [Easy setup on postmarketOS](pmos-easy-setup.md): what the setup scripts do and how to turn the kiosk off.
<!-- track: qa -->
- [Duo OS acceptance](duo-acceptance.md): the automated suite (`opos-selftest` on the phone, `deploy/duo-acceptance.ps1` from the PC) and the owner's eyes-on-the-glass checklist; written and tested against fakes, not yet run on a phone.
<!-- end track: qa -->
- [Lean by design](lean.md): direct package and service inputs, and the measurements still needed.
- [Over-the-air updates](ota-update.md): design for signed, rollback-safe updates of the OPOS app and its own files on the Duo OS image; design only, not yet implemented.
- [OPOS on a stock-Android Surface Duo](duo-android.md): Termux deploy without flashing, real battery and health, two screens; prepared, not yet run on the phone.
- [Surface Duo USB notes](duo-usb-install.md): **historical** Droidian development and recovery notes; Duo work is paused.

## Security and privacy

- [Device hardening](device-hardening.md): owner-run checks, SSH and firewall configuration, backups and rollback.
- [Security threat model](security.md): **historical** threat assessment from an earlier security branch.
- [About and notices](NOTICES.md): the legal, privacy and tested-device text the app shows, generated from `src/opos/notices.py`.
- [Notices verification](notices-verification.md): the source evidence behind the privacy and device statements.
- [Third-party notices](../THIRD_PARTY.md): dependencies, optional model terms and development tools.

## Design

- [Jaryo design specification](jaryo.md): **historical** interface proposal followed by implementation notes.
- [Demo scene plan](storyboard.md): a proposed demo sequence that keeps simulation, model answers and recordings apart.

## Project records

- [Changelog](../CHANGELOG.md): public snapshot, follow-up fixes and unreleased work.
- [Roadmap](../ROADMAP.md): what works today and what comes next.
- [New work and source scope](../NEW-WORK.md): hackathon source scope and contributions.
- [Development contributions](../WORK-BY-MODEL.md): who and which models contributed what.
- [Provenance](provenance.md): public source attribution and what the unpublished development records do and do not cover.
- [Repository checks](../guard/RULES.md): what the optional static guard examines and what it cannot certify.
- [Web page maintenance notes](web-implementation-notes.md): CSS comments and headers moved out of the page to keep it under its size budget.
