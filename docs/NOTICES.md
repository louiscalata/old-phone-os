<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- Generated from src/opos/notices.py by `python -m opos.notices --markdown`; do not edit by hand. Shared with the installer workstream: the .exe/.dmg installers import opos.notices or run `python -m opos.notices` (JSON). Build shows "unpublished" here; a release build fills it in. Evidence for each privacy and tested-device line: docs/notices-verification.md. -->
About Old Phone OS Interface

Old Phone OS Interface is Louis Calata's hackathon demo. It is experimental software, provided as-is with no warranty (GPL-3.0). Installing it replaces your phone's operating system and may erase data; back up first and use at your own risk.

The replacement warning is for OS flashing; per-user install.sh installs OPOS within the existing OS.

Version
0.1.0

Build
unpublished

Licence
GNU GPL-3.0-or-later

Source
github.com/louiscalata/old-phone-os

Third-party notices
- postmarketOS, the base system the packages are built for
- Linux kernel (GPL-2.0)
- Python 3 standard library (PSF License); no other Python packages are used
- Chromium (BSD-3-Clause and others), shows the phone screen
- cage (MIT), the kiosk session
- ModemManager (GPL-2.0-or-later), packaged for later modem use
- NetworkManager (GPL-2.0-or-later)
- polkit (LGPL-2.0-or-later) and D-Bus (AFL-2.1 or GPL-2.0-or-later)
- OpenRC (BSD-2-Clause) and psmisc (GPL-2.0-or-later)
- Each of these keeps its own licence and copyright; they are installed from the postmarketOS package repositories, not changed by Old Phone OS Interface.

Affiliation
Old Phone OS Interface is not affiliated with or endorsed by postmarketOS, Microsoft, Google or any phone maker. All trademarks belong to their owners.

Tested devices
- Physical phones, including Surface Duo, are untested for this corrected source export.
- A November 3 build booted to Home in postmarketOS QEMU with software rendering; that does not validate physical devices.

Risks
- Unlocking the bootloader or replacing the operating system may void your warranty.
- Unlocking the bootloader usually erases all data on the phone.
- A failed or interrupted install can leave the phone unable to start (bricked).
- Emergency calling is not guaranteed. Keep another way to call for help.

Restore the original system
[restore guide: unpublished]

Privacy
- Old Phone OS Interface sends no telemetry or analytics.
- The phone and setup pages are loopback-only; the public demo server listens on the network.
- By default the Helper uses its built-in rules and programs on this phone only.
- Old Phone OS Interface's own code sends nothing off the phone unless you opt in: ticking Search Wikipedia for a question, pointing the Helper at a computer you own on your private network, turning on the paid cloud option with your own key, or choosing coding tools to install during setup.
- postmarketOS, Chromium and other system software may use the network on their own; that is outside Old Phone OS Interface's control.

Contact
See the project repository for support.

Copyright
© 2026 Louis Calata
