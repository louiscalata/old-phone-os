<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# OPOS OS for the Surface Duo

An OPOS phone with no Android system: the Surface Duo boots a Linux rootfs whose only interface is OPOS.
Status, November 7, 2026: **image built and checked offline; not yet booted on a phone.** The owner's Duo
cannot take it yet: its USB-C port charges but carries no data, and every install route below needs fastboot
over USB. Android's wireless debugging cannot write partitions.

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
| `opos-server.service` | `python3 -m opos.web --live-battery` as user `droidian`: the rules on the phone's sysfs battery |
| `opos.service` | the shell: `phosh-session` with `PHOSH_SESSION_BIN=/usr/libexec/opos/opos-kiosk`, so `phoc` starts exactly as the port starts it (hwcomposer output, hinge seam, scale 2) with OPOS full-screen as its only client |
| `opos-kiosk` | waits for the server, then keeps Chromium in kiosk mode on the OPOS page, restarting it if it exits |
| `opos-fallback.service`, `opos-switch` | as the port's item: a failed OPOS shell starts phosh; `sudo opos-switch opos\|phosh\|status` changes shells |

<!-- track: network -->
The image also lets OPOS show the radios and join Wi-Fi through NetworkManager (Settings > Network, `/network`): see
[docs/network.md](../../docs/network.md). Its drop-in `opos-network.conf` sets `OPOS_NETWORK=1`; the polkit rule
`50-opos-network.rules` is installed only when building with `OPOS_NET_POLKIT=1`.
<!-- end track: network -->

phosh and item stay installed but are not started. Calls and texts still go through OPOS's simulated modem:
the base has ModemManager (via ofono2mm), but `src/opos/mm_modem.py` is not wired in yet.

## Build and check

On a Linux builder with `zstd`, `e2fsprogs` and `qemu-user-static`:

```sh
cd <release folder> && sha256sum -c SHA256SUMS
zstd -d --long=27 rootfs.img.zst -o base-rootfs.img        # must hash to the manifest's 9299b2c0…
sudo sh packaging/duo-os/build-image.sh base-rootfs.img opos-os-rootfs.img HEAD
sudo sh packaging/duo-os/verify-image.sh opos-os-rootfs.img
```

`verify-image.sh` checks the unit wiring and phoc's `-E` startup command, and runs the OPOS server from the image
with the image's own aarch64 python3 under qemu-user. It is not a boot, display or battery test.

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

## On-screen keyboard

The Duo has no physical keyboard and OPOS's kiosk is the only client of phoc, so nothing raised a keyboard for the helper's
Ask field, the Messages composer or the goal time in the shade. The image carries an **in-page keyboard**:
`opos-server.service` sets `OPOS_OSK=1`, and the server then adds `/osk.js` and `/osk.css` (`src/opos/osk.py`,
`src/opos/osk_static/`) to the page it serves; unset, the page and every route are exactly as before. It appears when a text
field is touched, with letters, numbers, symbols, a phone pad and a 24-hour time pad, and sends the field the key and input events
its handlers expect. On the spanned 1392 x 900 window the letters are **split, one half on each panel**, so no key sits on the
hinge; a Join key puts them on one panel. `/etc/default/opos-osk` overrides the setting (`OPOS_OSK=off`, or `stevia` for the
port's own `phosh-osk-stevia`, started by `opos-osk.service`: opt-in, written from sources, never started). Built and exercised in a desktop
browser only, not yet on the phone. [OSK.md](OSK.md) has the evidence for the choice, the behaviour in full and the live checks.

## Restore to Android

- Full stock 2022.902.48 (all 25 partitions, `surface/duo/duo:12/2022.902.48`, AT&T product config included) was
  extracted from the OTA payload and hash-checked; flash it from fastboot, logical partitions through fastbootd,
  then wipe.
- A partition backup of this phone from October 31, 2026 also exists on the owner's PC.
