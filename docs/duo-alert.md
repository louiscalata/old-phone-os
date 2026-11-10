<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Alert tone

`src/opos/alert.py` plays a caller-supplied file with `paplay`. It does not set `media.role`. If the default sink is above 20 percent, it is lowered to 20 percent for the alert and put back afterwards, including when playback fails.

No sound file is shipped here. Nothing has been played on a phone. **NOT_RUN.**
