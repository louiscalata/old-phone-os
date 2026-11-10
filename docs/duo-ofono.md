<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Calls and messages through oFono

`src/opos/ofono_phone.py` can dial, hang up, and send a message through oFono. It does nothing unless `OPOS_CALLS=1`. With that flag on, 911, 112, 999, 000, 110 and 119 go straight to oFono. There is no PIN, lock or confirmation in `dial`. Another short number with no leading plus, such as 1234, is still refused. `08` is two digits, so the short-number rule does not cover it. It is not installed as `Phone.modem`. The phone page still uses the simulated modem. An emergency number on that modem is not placed: the notice says "Calls are off."

This is not switched on, and it has not been tried with a SIM. **NOT_RUN.**
