<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# OPOS OS for the Surface Duo

An OPOS phone with no Android system: the Surface Duo boots a Linux rootfs whose only interface is OPOS.
Status, November 7, 2026: **the owner reported that the OPOS-only image works on the phone (RAM-booted from the PC).** The
pieces added since (keyboard, Network page, first run and time zone, acceptance suite) are built and tested off the phone only.
[docs/duo-os-final.md](../../docs/duo-os-final.md) says what the OS does now, what is still simulated, what is not known, and
lists the live checks in order. The owner's Duo cannot take a fresh install yet: its USB-C port charges but carries no data, and
every install route below needs fastboot over USB. Android's wireless debugging cannot write partitions.

## What is in the image

The base is the community Droidian port's release `item-duo1-20261025-port0.22.1` (Droidian next, adaptation
0.22.1, kernel `4.14-190-microsoft-surfaceduo`), checked against its manifest
(`rootfs.img` SHA-256 `9299b2c0…e312`). That base keeps what mainline Linux cannot yet do on this phone: the
vendor kernel and the Halium layer drive both panels, touch, GPU, Wi-Fi, audio and the modem. Mainline's
`sm8150-microsoft-surface-duo.dts` covers storage, USB, buttons, the two bq27742 fuel gauges and the modem but no
display, so a mainline-only OPOS would boot to a dark screen.

On top of the base, `build-image.sh` adds:

| Piece | Role |
|---|---|
| `/opt/opos` | OPOS runtime at an exact revision (`/opt/opos/REVISION`); stdlib Python on the image's python3 |
| `opos-server.service` | `python3 -m opos.web --live-battery` as user `droidian`: the rules on the phone's sysfs battery; sets the switches below |
| `opos.service`, `opos-session` | the shell: the unit takes phosh's hardware setup (hwcomposer output, vendor composer, VT switch), and `opos-session` starts `phoc` as `phosh-session` does but **not** in shell mode (`-S` keeps a black shield over both panels), with the kiosk as its only client |
| `opos-kiosk` | waits for the server, then keeps Chromium in kiosk mode on the OPOS page, restarting it if it exits, with the owner's time zone in its environment |
| `opos-fallback.service`, `opos-switch` | as the port's item: a failed OPOS shell starts phosh; `sudo opos-switch opos\|phosh\|status` changes shells |
| `opos-diag` | 90 s after boot, one read-only dump to `/var/log/opos-boot-diag.txt` (processes, graphics, input, radios, batteries, units); the journal is persistent |
| on-screen keyboard | in-page web keyboard served by `opos-server` (`src/opos/osk.py`); `opos-osk` and `opos-osk.service` are the opt-in Wayland one, enabled but idle |
| Network page | `/network` (`src/opos/net.py`, `net_page.py`); drop-in `opos-network.conf`; optional polkit rule `50-opos-network.rules` |
| first run, time zone | `/firstrun` and `/timezone` (`src/opos/firstrun.py`, `timezone.py`), the saved zone, the clock sentence |
| `opos-selftest` | read-only acceptance checks on the phone, `/usr/sbin/opos-selftest`; the PC side is `deploy/duo-acceptance.ps1` |

phosh and item stay installed but are not started. Calls and texts still go through OPOS's simulated modem:
the base has ModemManager (via ofono2mm), but `src/opos/mm_modem.py` is not wired in yet. Over-the-air updates are a design
([docs/ota-update.md](../../docs/ota-update.md)), not part of this tree.

## Switches

Every piece is off unless its switch is set, and then the page and every route are exactly what they were. The image sets them in
its units; a missing one is a FAIL in `opos-selftest` when the image carries the piece.

| Variable | Set by | Meaning |
|---|---|---|
| `OPOS_OSK` | `opos-server.service` (`1`), overridden by `/etc/default/opos-osk` | `1`/`web` in-page keyboard, `stevia` the port's keyboard, `off` none |
| `OPOS_OSK_HINGE` | not set (42) | CSS px between the keyboard's halves when the page does not say |
| `OPOS_NETWORK` | drop-in `opos-server.service.d/20-opos-network.conf` | the Settings link to `/network` and the actions that change Wi-Fi |
| `OPOS_NET_TTY` | not set | `1`: hand the Wi-Fi password to `nmcli` through a pseudo-terminal |
| `OPOS_NET_POLKIT` | build input | `1`: install `50-opos-network.rules` |
| `OPOS_FIRSTRUN` | `opos-server.service` (`1`) | first-run setup, `/timezone`, the saved zone |
| `OPOS_KIOSK_RESTART` | not set | `0`: do not restart Chromium after a zone change |
| `OPOS_CONFIG_DIR` | not set (`~/.config/opos`) | where the config, first-run flag and saved zone live; the server and `opos-kiosk` both read it |
| `OPOS_OSK_BIN`, `OPOS_OSK_WAIT`, `OPOS_OSK_GSETTINGS` | not set | only `opos-osk` (the opt-in Wayland keyboard) reads them: the binary, seconds to wait for phoc's socket, `0` to leave gsettings alone |
| `OPOS_DEV_SSH_PUBKEY` | build input | a public key file; the image then lets that key log in as `droidian` (a dev image) |

## Build and check

On a Linux builder with `zstd`, `e2fsprogs` and `qemu-user-static`:

```sh
cd <release folder> && sha256sum -c SHA256SUMS
zstd -d --long=27 rootfs.img.zst -o base-rootfs.img        # must hash to the manifest's 9299b2c0…
sudo sh packaging/duo-os/build-image.sh base-rootfs.img opos-os-rootfs.img HEAD
sudo sh packaging/duo-os/verify-image.sh opos-os-rootfs.img
```

`verify-image.sh` checks the unit wiring and phoc's `-E` startup command, runs the OPOS server from the image
with the image's own aarch64 python3 under qemu-user, and starts a second one with the keyboard, the Network page and the first run
all on to ask it for each page. It is not a boot, display or battery test.

## On-screen keyboard

The Duo has no physical keyboard and OPOS's kiosk is the only client of phoc, so nothing raised a keyboard for the helper's
Ask field, the Messages composer, the goal time in the shade or the Wi-Fi password. The image carries an **in-page keyboard**:
`opos-server.service` sets `OPOS_OSK=1`, and the server then adds `/osk.js` and `/osk.css` (`src/opos/osk.py`,
`src/opos/osk_static/`) to the pages it serves; unset, the page and every route are exactly as before. It appears when a text
field is touched, with letters, numbers, symbols, a phone pad and a 24-hour time pad, and sends the field the key and input events
its handlers expect. On the spanned 1392 x 900 window the letters are **split, one half on each panel**, so no key sits on the
hinge; a Join key puts them on one panel. `/etc/default/opos-osk` overrides the setting (`OPOS_OSK=off`, or `stevia` for the
port's own `phosh-osk-stevia`, started by `opos-osk.service`: opt-in, written from sources, never started). Built and exercised in a desktop
browser only, not yet on the phone. [OSK.md](OSK.md) has the evidence for the choice, the behaviour in full and the live checks.

## Network, first run and acceptance

- **Network.** Settings > Network opens `/network`: radio status, and scan, join, forget, disconnect and Wi-Fi on or off through
  NetworkManager as `droidian`. The `opos-network.conf` drop-in sets `OPOS_NETWORK=1`; the polkit rule is installed only when
  building with `OPOS_NET_POLKIT=1`. See [docs/network.md](../../docs/network.md).
- **First run and time zone.** A fresh phone opens a skippable setup; Settings > Date and time changes the zone later. The
  server applies the zone; the kiosk exports it to Chromium on every start. See [docs/duo-firstrun.md](../../docs/duo-firstrun.md).
- **Acceptance.** `/usr/sbin/opos-selftest` on the phone, `pwsh -File deploy\duo-acceptance.ps1 -Host <phone> -Key <key>` from
  the PC, then the owner's checklist. See [docs/duo-acceptance.md](../../docs/duo-acceptance.md).

## Install, once USB data works

This erases Android and its data on the phone. Back up first; a verified restore set exists (below).
With the bootloader unlocked, battery at 20 % or more, and the phone in Boot Select:

```sh
sh deploy/duo-rootfs-install.sh <release>/surfaceduo1-twrp.img <release>/boot.img opos-os-rootfs.img          # dry run
sh deploy/duo-rootfs-install.sh --yes <release>/surfaceduo1-twrp.img <release>/boot.img opos-os-rootfs.img    # do it
```

That formats userdata as ext4, RAM-boots TWRP, pushes the image to `/data/rootfs.img` and checks its hash and size,
returns to the bootloader and RAM-boots the port's kernel once. A RAM-boot does not survive a power cycle; see
[docs/duo-usb-install.md](../../docs/duo-usb-install.md) for restarting safely. Making it permanent means flashing
the port's `boot.img` and `vbmeta.img` to the active slot, which is the owner's separate decision.

## Restore to Android

- Full stock 2022.902.48 (all 25 partitions, `surface/duo/duo:12/2022.902.48`, AT&T product config included) was
  extracted from the OTA payload and hash-checked; flash it from fastboot, logical partitions through fastbootd,
  then wipe.
- A partition backup of this phone from October 31, 2026 also exists on the owner's PC.
