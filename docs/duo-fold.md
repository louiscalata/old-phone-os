<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Fold

`src/opos/fold.py` reads an `SW_LID` report. Value 1 means folded. The plan is a black page, backlight floor 3, and a poll of at least 10 seconds. Unfolding restores the previous level and poll.

The compositor output stays on. This does not turn a panel off.

Not measured on a phone. **NOT_RUN.**
