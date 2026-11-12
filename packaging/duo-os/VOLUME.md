<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Volume keys: simulated state

November 12, 2026: implemented in source, **not run on a phone (NOT_RUN)**.
The owner reported working volume-down events (114) on `qpnp_pon` and volume-up
events (115) on `gpio-keys`. That measurement does not verify this new consumer.

There is no connected OPOS media/ringer mixer path. Settings previously stored a
browser-local practice volume; `alarm_sound.py` takes an injected player and its
default refuses playback. This change therefore implements the requested fallback:
**volume keys change simulated state only. No speaker, media, ringer, call or alarm
volume is changed.** It does not configure PulseAudio, set an alarm media role or
invoke an audio command. Connecting a real audio path remains future work.

`opos.volumekey` discovers both exact sysfs names on every open, without assuming
an event number. It opens nonblocking, read-only evdev streams and never grabs
either device, so the existing power reader can continue reading `qpnp_pon`.
Only 114 on `qpnp_pon` and 115 on `gpio-keys` are accepted. Each fresh press changes
the simulated value by five percentage points, clamped to 0–100. Release re-arms
that key; duplicate presses, autorepeats, unknown values, power and unrelated keys
do nothing. Holding a key does not repeat volume changes.

Each device has its own partial-record buffer and retry deadline. Input loss drops
that device's pending bytes and held state, and retries discovery after one second;
the other device remains usable. `SYN_DROPPED` clears the affected held state and
suppresses its events through the next `SYN_REPORT`. No held key is reconstructed
or replayed after an overrun or reconnect. Operational journal messages contain
fixed statuses only, without raw input, volume values, paths or exception content.

`opos-volumekey.service` runs as root, bound to the OPOS shell's lifetime, with
`Restart=always`, `RestartSec=3` and `StartLimitIntervalSec=0`. Its root-owned
0755 RuntimeDirectory is `/run/opos-volume`; the only published data is
`{"simulated": true, "percent": 60}` (with the current simulated percentage).
The file is atomically replaced with mode 0644, readable but not writable by the
web user. The simulated value starts at 60 on each reader start. The runtime
directory is removed by systemd when the unit stops. A publication failure exits
nonzero for systemd to retry; it never reports an uncommitted percentage as success.
No hardware or application audio state is restored or persisted.

The Duo server enables `OPOS_VOLUME_KEYS=1`. Its authenticated existing state API
adds `volume_keys`, and a nonce-authorized `/volume.js` script reuses the existing
Settings slider, output and top-bar volume meter. The slider becomes read-only and
is explicitly labeled simulated, with sound unchanged. Missing or invalid state
is shown as unavailable; the last valid report is shown between state polls. This
is a simulated value, not input-device health or proof the service is still alive.
There is no new poll timer or volume mutation endpoint. With the switch absent,
the page, API state and practice slider keep their existing behavior.

The build installs and enables the unit. The image verifier checks module/asset
presence, unit wiring and restart/runtime-directory settings; it does not prove
effective systemd drop-ins, runtime-source revision binding or device behavior.
No image build, image verification, native systemd lifecycle, browser, phone or
sound check was run for this change (**NOT_RUN**). Local unit tests use fake
input events, filesystem fixtures, HTTP requests and a Node VM, without a browser.

The next phone gate is to verify both keys while the power reader is active,
unplug/reopen and restart behavior, Settings/top-bar refresh, service permissions
and fallback stop/start ordering. Actual audio-volume acceptance requires a
connected media/ringer audio path and separate audible checks.
