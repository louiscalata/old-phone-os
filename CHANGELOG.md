<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Changelog

Notable changes to Old Phone OS Interface (OPOS), in Keep a Changelog style.

## Unreleased

### Added

- Inference page (`/monitor`, behind `OPOS_MONITOR=1`; `src/opos/monitor.py`, `src/opos/monitor_web.py`, `src/opos/web_static/monitor*`): a read-only page that says whether a model server answers on the phone's own loopback, shows the battery percent, health (only when the battery's own gauge reports it, never an estimated, assumed or typed-in value) and source, and optionally shows an AGIW Inference Monitor on a computer you own (`OPOS_AGIW_URL`, `OPOS_AGIW_HOST`; nothing leaves the phone unless `OPOS_AGIW_URL` is set). The page's check of the phone's own model server follows `OPOS_LOCAL_URL` only if you already set it for the Helper, and setting it changes the Helper too. Settings gets a link to it when the switch is on, added at serve time so the main page does not grow; `/api/monitor` needs the session token, is rate limited, and asks the monitor outside the request lock. The Duo OS image's `opos-server.service` turns it on and reads an optional `/etc/opos/monitor.env` (only a commented example ships); the privacy notice now names the Inference page among the opt-ins. Tested against fakes and loopback servers only; not seen on the phone. On the hardened units only loopback egress works, so a reverse tunnel is the way there (`packaging/duo-os/README.md`).
- Measured drain for a live battery (`--live-battery`): time left, the goal check, the reserve and the helper's hours left follow an exponentially weighted discharge rate taken from the phone's own level changes (and its charge counter when readable) instead of the simulated drain model, which stays the figure until 10 minutes of discharge have been observed. The state payload gains `battery.drain` (basis, rate, sample age, observation window). Tested on synthetic discharge traces only; not yet measured on the phone.
- `OPOS_SPAN` / `--span PANE,HINGE` (`src/opos/span.py`): for a browser window that covers both panels of a two-screen phone, the server serves the page as if the browser had reported two viewport segments, so the existing Foldable layout keeps content off the hinge. Chromium on Linux cannot report segments, so the Surface Duo OPOS OS image's server can be told where the hinge is: 675,42 from the port's 2784 x 1800 output at phoc scale 2, left commented out in `opos-server.service` because it was checked in headless Chromium at 1392 x 900 and not yet on the phone. Unset, the page is unchanged.
- `OPOS_DEV_SPAN=1` (build input of `packaging/duo-os/build-image.sh`, only together with `OPOS_DEV_SSH_PUBKEY`): a dev checkpoint image gets the drop-in `opos-server.service.d/30-opos-dev-span.conf` that sets `OPOS_SPAN=675,42`, so the two-screen page and the pane surfaces paint on the phone without editing the unit (which still ships the span commented out); the build refuses it without the dev key, and `verify-image.sh` checks the drop-in and builds the page for its value with the image's python3.
- `OPOS_DEV_SUDO=1` (build input of `packaging/duo-os/build-image.sh`, only together with `OPOS_DEV_SSH_PUBKEY`): a dev image gets `/etc/sudoers.d/90-opos-dev` (0440 root:root, exactly `droidian ALL=(ALL) NOPASSWD:ALL`, checked with `visudo -cf` where the builder has it), so a developer who can ssh in can become root; such an image must never be carried or shared. The build refuses the knob without the dev key or with any value but 1/0/unset before the output is touched; `verify-image.sh` checks the file (and, given the build's `OPOS_DEV_SUDO`, holds the image to it both ways) and that no other sudoers line grants `NOPASSWD`; `opos-selftest` WARNs `dev.sudo` when the file exists.
- Duo OS image hardening, in every image and with no switch (`build-image.sh`, block `usb hardening`; `verify-image.sh` checks it): the port's root telnet shell on the USB link (busybox `telnetd` with `/bin/bash`, no login, started by `sfduo-usb-gadget.sh` at every boot) is replaced by a comment, and the port's sshd drop-in `10-item-password-usb-only.conf` becomes key-only (it let a password in for every client in the USB link's /24, on any interface). `opos-selftest` `ssh.auth` reads `key-only everywhere`. `adbd` (TCP 5555, authorised keys only) is not touched. Tested against a scratch root file system and, where the machine has `sshd`, with `sshd -T`; not seen on the phone.
- `OPOS_PANES=1` (`src/opos/panes.py`, `docs/jaryo-panes.md`): on the window that spans both panels the thumb pane is black with light text and the list pane white with dark text (pressed, active and on controls stay the inverted fill of their own pane), and a Hand setting in Settings > Display (Right or Left) decides which physical pane is which and mirrors the layout. Served as separate `/panes.css` and `/panes.js` like the on-screen keyboard; `web.page()` does not change and, unset, the panes assets and tags are absent. The Network, first-run and time zone pages follow the Jaryo text-button rules (two colours, square corners, dashed unavailable buttons, 48 px targets, a drawn checkbox) with the flag on or off. Checked in headless Chromium at 1392 x 900, not yet on the phone.
- Surface Duo with OPOS as the only interface (`packaging/duo-os`): a build script, offline checks and boot-evidence tools for an image on the community Droidian port's rootfs, where `opos-server.service` runs the OPOS server on the phone's real battery and `opos-session` starts phoc (hwcomposer, no shell mode) with a full-screen Chromium kiosk. Ran on the owner's Surface Duo 1 on November 7, 2026, RAM-booted from a PC; the owner confirmed it worked and logs showed phoc on hwcomposer and the real battery from sysfs. It was seen on one phone in one session; no speed, battery-life or stability measurements; calls and texts are still simulated; RAM-boot only.
- `deploy/duo-opos-boot.ps1`: the install chain for that image from a Windows PC (misc brake, userdata format, TWRP, hash-verified push, one RAM-boot of the kernel), with the phone found by probing TCP 22 across the /24 of the USB network adapter instead of waiting for a gateway, an optional ssh key for diagnostics, no hard-coded paths and a dry run unless `-Yes`. Tests parse it, read its safety rules and run its probe against loopback; it has not been run in this form.
- Surface Duo on stock Android: `--live-battery` (`LiveBattery`), `deploy/duo-termux.ps1` and `deploy/termux-setup.sh`; one session on the owner's phone on November 7, 2026 (see `docs/duo-android.md`).
- Over-the-air updates for the Surface Duo OS image ([design and as-built notes](docs/ota-update.md)): a root updater with its systemd units, installed by `packaging/duo-os/build-image.sh` (`OPOS_UPDATE_SIGNERS`, `OPOS_UPDATE_URL`); an opt-in update client and `/update` page in OPOS; `tools/opos-release.py` and `deploy/duo-push-update.ps1` to build, sign and push a release ([how-to](docs/ota-release-howto.md)). Settings, About has an Updates link to the page. Review fixes: the update service has no start limit that a writer of the inbox could trip; the smoke test uses a port the kernel picks; a rollback requested through the inbox is limited to one step back per applied update and does not burn the serial. Implemented and tested on Linux with stand-ins for systemd, runuser and the browser; not tested on the phone.

### Changed

- Documentation for the Surface Duo runs: `README.md` Limits, `ROADMAP.md` and the documentation index now separate what ran on the phone (one phone, one day) from what is simulated or unmeasured, and the Droidian USB notes are labelled historical.
- `opos-kiosk` waits for the OPOS server before every start of the browser, so the browser comes back only after a restarted server answers.
- README now includes an illustrative older-phone vignette, new final-tree Muse Glimmer Ask and Why? captures in light and dark, with all eight measured UI response times, the quoted first dark Ask answer, and current pause status. All eight requests returned Glimmer replies within the 115-second deadline; the new Why? measurements replace the earlier rules-fallback observations for this source. Simulation wording distinguishes the profile drain runs from the worn-battery burst-shaping evaluation; postmarketOS wording separates the recorded QEMU boot from outstanding integration acceptance and untested physical-phone behavior.

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
