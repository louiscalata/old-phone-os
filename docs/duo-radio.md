<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Radio recovery

The network page has two more actions when network control is on.

- Restart Wi-Fi turns the Wi-Fi radio off and then on through NetworkManager. It needs the `enable-disable-wifi` permission.
- Restart modem asks oFono to set `Online` false and then true on `/ril_0` (or `OPOS_OFONO_MODEM`). The reply text is not kept.

`opos-diag` prints three watch lines after the read-only radio block: whether `wlan0` exists, whether `/ril_0` exists, and whether every remoteproc state file says `running` or `online`. A missing file is "missing". Anything else is "not ONLINE". The raw state text is not printed.

None of this has been run on a phone. **NOT_RUN.**
