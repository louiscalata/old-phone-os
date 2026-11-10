<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Alert tone

`src/opos/alert.py` plays a caller-supplied file with `paplay --volume` at 20 percent of one stream. It does not set `media.role` and it does not change the sink volume. An alarm uses its own stream volume, from 20 percent to 70 percent, so this cap does not scale the alarm.

No sound file is shipped here. Nothing has been played on a phone. **NOT_RUN.**
