<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Alert tone

`src/opos/alert.py` plays a caller-supplied file with `paplay --volume` at 20 percent of that one stream. It does not set a media role. It does not change the sink volume, so other streams, including an alarm, keep their own level. Alarms do not call this helper.

No sound file is shipped here. Nothing has been played on a phone. **NOT_RUN.**
