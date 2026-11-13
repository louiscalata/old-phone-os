<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- Generated from src/opos/notices.py by `python -m opos.notices --markdown`; do not edit by hand. Shared with the installer workstream: the .exe/.dmg installers import opos.notices or run `python -m opos.notices` (JSON). Build shows "unpublished" here; a release build fills it in. Evidence for each privacy and tested-device line: docs/notices-verification.md. -->
About Old Phone OS Interface

Old Phone OS Interface is Louis Calata's hackathon demo. It is experimental software, provided as-is with no warranty (GPL-3.0). Installing it replaces your phone's operating system and may erase data; back up first and use at your own risk.

The replacement warning is for OS flashing; per-user install.sh installs OPOS within the existing OS.

Version
0.1.0

Build
unpublished

License
GNU GPL-3.0-or-later

Source
github.com/louiscalata/old-phone-os

Third-party notices
- Packages the postmarketOS build depends on:
- Linux kernel (GPL-2.0)
- Python 3 standard library (PSF License); no other Python packages are used
- Chromium (BSD-3-Clause and others), shows the phone screen
- cage (MIT), the kiosk session
- ModemManager (GPL-2.0-or-later), packaged for later modem use
- NetworkManager (GPL-2.0-or-later)
- polkit (LGPL-2.0-or-later) and D-Bus (AFL-2.1 or GPL-2.0-or-later)
- OpenRC (BSD-2-Clause) and psmisc (GPL-2.0-or-later)
- These postmarketOS repository packages retain their licenses and copyrights.
- The Duo image uses Droidian, phoc and systemd. This is not a complete component or license inventory for redistributing it.

Affiliation
Old Phone OS Interface is not affiliated with or endorsed by postmarketOS, Microsoft, Google or any phone maker. All trademarks belong to their owners.

Tested devices
- Physical phones, including Surface Duo, are untested for this corrected source export.
- A November 3 build booted to Home in postmarketOS QEMU with software rendering; that does not validate physical devices.

Risks
- Unlocking the bootloader or replacing the operating system may void your warranty.
- Unlocking the bootloader usually erases all data on the phone.
- A failed or interrupted install can leave the phone unable to start (bricked).
- Tasks, calls and texts are simulated. OPOS cannot place an emergency call; keep another way to call for help.

Restore the original system
[restore guide: unpublished]

Privacy
- Old Phone OS Interface sends no telemetry or analytics.
- The phone and setup pages are loopback-only; the public demo server listens on the network.
- By default the Helper uses its built-in rules and programs on this phone only.
- Local phone and setup code sends nothing off the phone unless you opt in to Wikipedia search, a Helper or Inference route to your computer on a private network, paid cloud use with your key, or coding tool installation.
- Hosted demo: entered numbers, messages and questions go to its server. Use fictional information.
- System software, including postmarketOS and Chromium, may use the network independently of OPOS.

Contact
See the project repository for support.

Copyright
© 2026 Louis Calata
