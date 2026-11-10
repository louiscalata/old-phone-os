<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Calls and messages through oFono

`src/opos/ofono_phone.py` can dial, hang up, and send a message through oFono. It does nothing unless `OPOS_CALLS=1`. The default is practice mode. Even with the flag on, a short number such as 911 is refused, because no emergency call has been tested on a phone. The phone page still uses the simulated modem. This module is not installed as `Phone.modem`.

This is not switched on, and it has not been tried with a SIM. **NOT_RUN.**
