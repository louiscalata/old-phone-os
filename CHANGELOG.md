<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Changelog

Notable changes to Old Phone OS Interface (OPOS), in Keep a Changelog style.

## Unreleased

No changes yet after 0.2.0.

## 0.2.0 - 2026-11-09

Release candidate. The Surface Duo OS interface is not in this repository. The Meta hackathon interface stays in place as a reference. This commit does not create a git tag or a GitHub Release.

### Added

- CI job `easy-setup`: ShellCheck 0.11.0 on `packaging/pmos/easy/*.sh` and `packaging/pmos/qemu-smoke.sh`, and a dry-run test that `easy-install.sh` without `--flash` does not call `image-prep.sh`, `confirm-reboot.sh`, or `pmbootstrap`, and does not modify the image or those scripts.
- CI job `qemu-guest-smoke`: boots a tiny initramfs under `qemu-system-x86_64` and uploads the serial log. The guest prints `OPOS_QEMU_GUEST_SMOKE_OK`. It is not a postmarketOS image and does not start the OPOS UI.
- `packaging/pmos/qemu-smoke.sh` (`--plan` by default, `--guest` for that tiny boot).
- `docs/release-notes/v0.2.0.md` for the GitHub Release page and the website.
- `reference/meta-hackathon/README.md` labels the in-place hackathon interface. The code was not moved; the package, tests, and runtime archive depend on the current paths.

### Changed

- Product version strings are 0.2.0: `pyproject.toml`, `src/opos/notices.py`, `docs/NOTICES.md`, `opos --version`, and `pkgver` in `packaging/pmos/opos/APKBUILD` (`pkgrel` 0). `postmarketos-ui-opos` stays at pkgver 1.
- The opos recipe names runtime archive `opos-runtime-source-20261109.tar.gz` on the future `v0.2.0` release asset. The sha512 in the recipe is the measured `make-runtime-source.py` output for this tree. The file is not uploaded in this commit, so a remote pmbootstrap fetch of that URL fails until the release owner publishes it. Local staging with `OPOS_LOCAL_SOURCE=1` still builds from the checkout.
- Easy setup docs now follow the scripts. `confirm-reboot.sh` no longer says the phone will reboot several times. Without `--flash`, `easy-install.sh` only prints a plan. With `--flash` it checks the image header, asks for confirmation, then runs `pmbootstrap flasher flash_rootfs` without passing the image path. Physical-device flashing is untested.
- README quick start commands are unchanged. The introduction points at the 0.2.0 release notes.
- README now includes an illustrative older-phone vignette, new final-tree Muse Glimmer Ask and Why? captures in light and dark, with all eight measured UI response times, the quoted first dark Ask answer, and current pause status. All eight requests returned Glimmer replies within the 115-second deadline; the new Why? measurements replace the earlier rules-fallback observations for this source. Simulation wording distinguishes the profile drain runs from the worn-battery burst-shaping evaluation; postmarketOS wording separates the recorded QEMU boot from outstanding integration acceptance and untested physical-phone behavior.

### Proven for this candidate

- Desktop quick start: `PYTHONPATH=src python3 -m opos.web --port 8765` serves the demo page.
- The Python unit tests on this tree, including the non-mutating easy-install dry run.
- ShellCheck 0.11.0 on the easy-setup scripts and `qemu-smoke.sh`.
- A QEMU x86_64 guest smoke that prints `OPOS_QEMU_GUEST_SMOKE_OK`. This does not boot postmarketOS.

### Not proven

- Physical phones, and `easy-install.sh --flash` / `pmbootstrap flasher flash_rootfs` on hardware.
- A new postmarketOS image boot in QEMU for this 0.2.0 tree. The November 3, 2026 aarch64 boot to the lock screen and Home remains the recorded image boot and was not repeated here.
- Modem use, a real battery reading on a phone, on-phone inference, the Windows runtime, and full integration acceptance.
- The GitHub Release download. Remote package builds do not work until that archive is uploaded.

## 0.1.0 - 2026-11-03

### Added

- Public snapshot (`bb9c846`): a prototype phone interface that keeps calls and messages on while pausing less essential work toward an owner's chosen time.
- A desktop demo with simulated battery, calls and messages, requiring Python 3.11 or newer; an optional local helper explains decisions and may add pauses.
- Follow-up (`857e235`): tests compare published simulation numbers with simulator results.
- Panel fixes (`bd0e465`): `pyproject.toml` and CI for Python 3.11 and 3.13.

### Changed

- Follow-up: renamed the displayed project name to Old Phone OS Interface; existing installer markers, service descriptions and package fields retain the old name.
- Panel fixes: clarified the simulated battery and modem in the postmarketOS kiosk, battery trade-offs and the meaning of a worn battery in the docs.

### Fixed

- Follow-up: handled incomplete helper replies, transport errors and deeply nested replies; limited claimed pauses to paused tasks and gave the last route the remaining time.
- Follow-up: made Wikipedia search ignore proxies and redirects.
- Follow-up: corrected demo-goal handling and wording, including empty batteries and goals set after their time.
- Follow-up: added one plain message for Python versions older than 3.11 on every platform.
- Panel fixes: used one final pause decision across screens, helper facts and simulated drain, with safety limits applied after owner actions.
- Panel fixes: shared the daily-goal budget across tasks in a fixed order and aligned simulated drain with the time-left estimate.
- Panel fixes: checked helper routes before sending Why and Ask questions, restricted the network route to private addresses and preserved route settings in installed services.
- Panel fixes: verified the optional AI-tools installer against a pinned SHA-256 digest and refused redirects to another host.

Source details and validation limits: [provenance](docs/provenance.md).
