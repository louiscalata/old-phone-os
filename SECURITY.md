<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Security

OPOS 0.2.0 in this repository is the Meta hackathon reference. The Surface Duo OS interface is not in this tree. Report a vulnerability in the reference code; do not send a hardware dossier.

## Reporting

Open a private GitHub security advisory on [louiscalata/old-phone-os](https://github.com/louiscalata/old-phone-os/security/advisories/new). If private advisories are unavailable, open a public issue that names the affected file and the impact, without an exploit script or a secret.

## Scope

In scope: `src/opos`, the installers, and the postmarketOS package scripts in this repository.

Out of scope: physical phones, flashing, the unpublished Surface Duo OS, and third-party software the kiosk does not patch (postmarketOS, Chromium, the kernel).

[docs/security.md](docs/security.md) is a historical threat-model note. Its "fixed" rows describe an earlier branch. They are not a current audit of this tree.

## Supported versions

| Version | Status |
|---|---|
| 0.2.0 (this tree, not yet tagged) | The reference under review |
| 0.1.0 | Hackathon snapshot, superseded by this reference labeling |
