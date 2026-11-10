<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Calls and messages through oFono

`src/opos/ofono_phone.py` can dial, hang up, and send a message through oFono. It does nothing unless `OPOS_CALLS=1`. With that flag on, 911, 112, 999, 000, 110 and 119 go straight to oFono. There is no PIN, lock or confirmation in `dial`. Another short number with no leading plus, such as 1234, is still refused. `08` is two digits, so the short-number rule does not cover it. With `OPOS_CALLS=1`, the phone page and a locked phone use this modem for a call. With calls off, the practice modem does not record an emergency number, and the notice says "Calls are off."

This is not switched on, and it has not been tried with a SIM. **NOT_RUN.**
