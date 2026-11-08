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

phosh and item stay installed but are not started. Calls and texts still go through OPOS's simulated modem:
the base has ModemManager (via ofono2mm), but `src/opos/mm_modem.py` is not wired in yet.

## Spanning both panels

The kiosk window covers both panels and the hinge, and Chromium on Linux cannot report viewport segments, so the
page's two-screen layout never applies and text or buttons can land on the hinge. `opos-server` can be told where
the hinge is: `OPOS_SPAN=675,42` or `--span 675,42`, the pane width and the hinge width in CSS px (`--span off`
turns it off). Those numbers come from the port's files, not from a measurement on the phone: an output of 2784 x
1800 device px, two 1350 px panels and an 84 px hinge (`display-panels/qcom,sm8150-mtp.json`), at the output scale 2
that `phoc.ini` sets, so the window should be 1392 x 900 CSS px with panes 675 px wide and a 42 px hinge (to be
confirmed on the phone).

With a span set, the server serves the page as if the browser had reported two viewport segments: the existing
Foldable rules apply with the first pane 675 px wide, the hinge as the gap and the second pane taking the rest;
Auto picks Foldable; and the status line, the shade's handle and the lock screen are moved off the hinge. All of it
applies only while the window is at least 1055 px wide (the first pane, the hinge and half a second pane), so a window
on one panel (675 px) keeps the phone layout. A Device type chosen in Settings is still honoured, and Settings reads
"Auto looks like: two screens" and then the window's size while the window spans both panels. Unset, the served page is
byte-for-byte what it was. The service file carries the setting commented out, to be enabled once the layout has been
checked on the phone. Not handled: a rotated window (the hinge would run across it), a phone folded with one panel
dark, and two things that still cross the hinge: the thin battery bar along the top edge and, while it plays, the
240 ms zoom from a tile to its app.

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

## Restore to Android

- Full stock 2022.902.48 (all 25 partitions, `surface/duo/duo:12/2022.902.48`, AT&T product config included) was
  extracted from the OTA payload and hash-checked; flash it from fastboot, logical partitions through fastbootd,
  then wipe.
- A partition backup of this phone from October 31, 2026 also exists on the owner's PC.
