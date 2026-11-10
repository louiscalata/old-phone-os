<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Airplane mode

`src/opos/airplane.py` plans an airplane change for `phy0` and `hci0` only. Other rfkill entries are left as they are. oFono `Online` is set false, and a later restore sets it true and unblocks only the radios this mode itself blocked.

Forget and Wi-Fi off return a confirmation sentence when Wi-Fi is connected and neither USB nor mobile data is up.

The network page does not call this yet. Not measured on a phone. **NOT_RUN.**
