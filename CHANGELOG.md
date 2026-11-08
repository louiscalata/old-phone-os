<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Changelog

Notable changes to Old Phone OS Interface (OPOS), in Keep a Changelog style.

## Unreleased

### Added

- Measured drain for a live battery (`--live-battery`): time left, the goal check, the reserve and the helper's hours left follow an exponentially weighted discharge rate taken from the phone's own level changes (and its charge counter when readable) instead of the simulated drain model, which stays the figure until 10 minutes of discharge have been observed. The state payload gains `battery.drain` (basis, rate, sample age, observation window). Tested on synthetic discharge traces only; not yet measured on the phone.
- `OPOS_SPAN` / `--span PANE,HINGE` (`src/opos/span.py`): for a browser window that covers both panels of a two-screen phone, the server serves the page as if the browser had reported two viewport segments, so the existing Foldable layout keeps content off the hinge. Chromium on Linux cannot report segments, so the Surface Duo OPOS OS image's server can be told where the hinge is: 675,42 from the port's 2784 x 1800 output at phoc scale 2, left commented out in `opos-server.service` because it was checked in headless Chromium at 1392 x 900 and not yet on the phone. Unset, the page is unchanged.
- `OPOS_PANES=1` (`src/opos/panes.py`, `docs/jaryo-panes.md`): on the window that spans both panels the thumb pane is black with light text and the list pane white with dark text (pressed, active and on controls stay the inverted fill of their own pane), and a Hand setting in Settings > Display (Right or Left) decides which physical pane is which and mirrors the layout. Served as separate `/panes.css` and `/panes.js` like the on-screen keyboard; `web.page()` does not change and, unset, the panes assets and tags are absent. The Network, first-run and time zone pages follow the Jaryo text-button rules (two colours, square corners, dashed unavailable buttons, 48 px targets, a drawn checkbox) with the flag on or off. Checked in headless Chromium at 1392 x 900, not yet on the phone.
- Surface Duo with OPOS as the only interface (`packaging/duo-os`): a build script, offline checks and boot-evidence tools for an image on the community Droidian port's rootfs, where `opos-server.service` runs the OPOS server on the phone's real battery and `opos-session` starts phoc (hwcomposer, no shell mode) with a full-screen Chromium kiosk. Ran on the owner's Surface Duo 1 on November 7, 2026, RAM-booted from a PC; the owner confirmed it worked and logs showed phoc on hwcomposer and the real battery from sysfs. It was seen on one phone in one session; no speed, battery-life or stability measurements; calls and texts are still simulated; RAM-boot only.
- `deploy/duo-opos-boot.ps1`: the install chain for that image from a Windows PC (misc brake, userdata format, TWRP, hash-verified push, one RAM-boot of the kernel), with the phone found by probing TCP 22 across the /24 of the USB network adapter instead of waiting for a gateway, an optional ssh key for diagnostics, no hard-coded paths and a dry run unless `-Yes`. Tests parse it, read its safety rules and run its probe against loopback; it has not been run in this form.
- Surface Duo on stock Android: `--live-battery` (`LiveBattery`), `deploy/duo-termux.ps1` and `deploy/termux-setup.sh`; one session on the owner's phone on November 7, 2026 (see `docs/duo-android.md`).

### Changed

- Documentation for the Surface Duo runs: `README.md` Limits, `ROADMAP.md` and the documentation index now separate what ran on the phone (one phone, one day) from what is simulated or unmeasured, and the Droidian USB notes are labelled historical.
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
