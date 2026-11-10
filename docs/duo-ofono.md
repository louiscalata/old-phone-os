<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Calls and messages through oFono

`src/opos/ofono_phone.py` can dial, hang up, and send a message through oFono. It does nothing unless `OPOS_CALLS=1`. With that flag on, `dial` still refuses a short number with no leading plus, including 911, 112 and 999, using the same rule as the practice phone. It is not installed as `Phone.modem`. The phone page still uses the simulated modem.

This is not switched on, and it has not been tried with a SIM. **NOT_RUN.**
