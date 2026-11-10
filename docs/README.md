<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Documentation

Old Phone OS Interface is a prototype. The desktop demo simulates the battery, calls and texts; recorded QEMU
results and model experiments have their own limits. The postmarketOS kiosk uses the same simulated battery
and modem as the desktop demo. The Surface Duo routes read the phone's real battery and ran on one owner's
phone on November 7, 2026, with calls and texts still simulated and no speed, battery-life or stability
measurements. Documents marked **historical** preserve earlier work and are not current instructions or
evidence about the current source.

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
<!-- track: integrate -->
- [The Duo OS as it stands](duo-os-final.md): what the Surface Duo OS does now, the switches, what is still simulated or not known (calls and texts, battery life, stability, RAM-boot only), and the live checks in order; none of it measured on a phone yet.
<!-- end track: integrate -->
<!-- track: qa -->
- [Duo OS acceptance](duo-acceptance.md): the automated suite (`opos-selftest` on the phone, `deploy/duo-acceptance.ps1` from the PC) and the owner's eyes-on-the-glass checklist; written and tested against fakes, not yet run on a phone.
<!-- end track: qa -->
- [Lean by design](lean.md): direct package and service inputs, and the measurements still needed.
- [Over-the-air updates](ota-update.md): the contract for signed, rollback-safe updates of the OPOS app and its own files on the Duo OS image, and what was built to it; implemented and tested on Linux with stand-ins for systemd, runuser and the browser, not tested on the phone.
- [Releasing over-the-air updates](ota-release-howto.md): the owner's how-to for the release key, `tools/opos-release.py` (build, verify, index), hosting, the USB push script, key rotation and recovery; tested on a PC with throw-away keys against the real updater code; none of it has run on a phone.
- [OPOS OS for the Surface Duo](../packaging/duo-os/README.md): OPOS as the only interface of a Linux image, RAM-booted on the owner's Duo on November 7, 2026 (it worked, per the owner); what was and was not observed, the RAM-boot-only rule and the misc brake, the dev ssh key, boot logs, known issues and restoring Android. Installed by `deploy/duo-opos-boot.ps1`.
- [Persistent kernel on the Duo](duo-persistent-install.md): a gated runbook for the first device session that tests whether the OPOS kernel can live in the phone's own storage so the power button starts OPOS; read-only probes and backups first, every write needs the owner's word; a plan, not run, with `deploy/duo-persist-probe.ps1` tested against stand-in tools only.
<!-- track: network -->
- [Network on the Duo OS](network.md): Wi-Fi and radio status, joining Wi-Fi through NetworkManager as an unprivileged user, and what is still simulated; tested against fake tools only, not on the phone.
<!-- end track: network -->
<!-- track: monitor -->
- [Inference page on the Duo OS](../packaging/duo-os/README.md#inference-page): Settings > Inference (`/monitor`): whether a model server on the phone answers, the battery numbers it was given, and an optional AGIW monitor on a computer you own; the three variables, why only loopback egress works on the hardened units, and a reverse-tunnel recipe; tested against fakes and loopback servers, not on the phone.
<!-- end track: monitor -->
<!-- >>> track firstboot -->
- [First run and time zone on the Duo OS](duo-firstrun.md): the setup shown on the first start, the owner's time zone applied without root, the clock sentence and the switch that turns it on; tested off the phone.
<!-- <<< track firstboot -->
- [OPOS on a stock-Android Surface Duo](duo-android.md): Termux deploy without flashing, real battery and health, two screens; ran on the owner's Duo on November 7, 2026, one session.
- [Surface Duo USB notes](duo-usb-install.md): **historical** Droidian development and recovery notes; the current Duo routes are the two above.
- [On-device checklist](duo-on-device-checklist.md): every check that needs the physical phone. Each one is **NOT_RUN**.

## Security and privacy

- [Device hardening](device-hardening.md): owner-run checks, SSH and firewall configuration, backups and rollback.
- [Security threat model](security.md): **historical** threat assessment from an earlier security branch.
- [About and notices](NOTICES.md): the legal, privacy and tested-device text the app shows, generated from `src/opos/notices.py`.
- [Notices verification](notices-verification.md): the source evidence behind the privacy and device statements.
- [Third-party notices](../THIRD_PARTY.md): dependencies, optional model terms and development tools.

## Design

- [Jaryo design specification](jaryo.md): **historical** interface proposal followed by implementation notes.
- [Jaryo panes](jaryo-panes.md): a black screen and a white screen on the window that spans both panels of the Duo, the Hand setting, and the text-button rules for the Duo OS pages (`OPOS_PANES=1`); checked in headless Chromium at 1392 x 900, not yet on the phone.
- [Demo scene plan](storyboard.md): a proposed demo sequence that keeps simulation, model answers and recordings apart.

## Project records

- [Changelog](../CHANGELOG.md): public snapshot, follow-up fixes and unreleased work.
- [Roadmap](../ROADMAP.md): what works today and what comes next.
- [New work and source scope](../NEW-WORK.md): hackathon source scope and contributions.
- [Development contributions](../WORK-BY-MODEL.md): who and which models contributed what.
- [Provenance](provenance.md): public source attribution and what the unpublished development records do and do not cover.
- [Repository checks](../guard/RULES.md): what the optional static guard examines and what it cannot certify.
- [Web page maintenance notes](web-implementation-notes.md): CSS comments and headers moved out of the page to keep it under its size budget.
- [UI byte ledger](ui-byte-ledger.md): what the page weighs now (`page()` and the served page with every switch on), where the bytes are, the 102,000-byte wave gate and how to measure it again.
