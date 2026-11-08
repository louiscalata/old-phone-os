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
| `opos.service` | the shell: `opos-session`, which starts `phoc` as `phosh-session` does (hwcomposer output, hinge seam, scale 2) but without `-S`, with the kiosk as phoc's only client |
| `opos-kiosk` | waits for the server, then keeps Chromium in kiosk mode on the OPOS page. It clears a stale profile lock before each start; a Chromium that keeps dying within 20 s is restarted after 2, 2, 4, 8 ... 60 s and, from the third quick exit, without the GPU. Every exit is logged: `journalctl -t opos-kiosk` |
| `sfduo-composer-watchdog` | the port's script, edited at build time so that its bounce of a composer that is not DRM master restarts OPOS as well as phosh (`verify-image.sh` checks the edit; an upgrade of the port's package reverts it) |
| `opos-fallback.service`, `opos-switch` | as the port's item: a failed OPOS shell starts phosh; `sudo opos-switch opos\|phosh\|status` changes shells |

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
`tests/test_duo_kiosk.py` runs the kiosk script under `sh` with a fake `chromium` (locks, back-off, the no-GPU retry).

## Looking at the page on a dev image

`OPOS_DEV_SSH_PUBKEY=<file.pub> sudo -E sh packaging/duo-os/build-image.sh ...` also writes `/etc/default/opos-kiosk`
with `OPOS_KIOSK_DEBUG_PORT=9222`; without that variable the file is not in the image (`verify-image.sh` fails an image
that has one without the key). The kiosk then starts Chromium with `--remote-debugging-port=9222
--remote-debugging-address=127.0.0.1`, reachable only over an ssh port-forward, which also carries page screenshots
(DevTools `Page.captureScreenshot`):

```sh
ssh -L 9222:127.0.0.1:9222 droidian@<phone>
curl -s http://127.0.0.1:9222/json        # lists the OPOS page and its webSocketDebuggerUrl
```

Clients that open the DevTools websocket without an `Origin` header (puppeteer, chrome-remote-interface, a plain
websocket library) should connect as they are; a browser-based client sends one, and Chromium 111 and later refuse it
unless `--remote-allow-origins` names it. That is expected behaviour, not tried on the phone.
The port gives full control of the browser to whoever can reach it; that is the same trust as the dev ssh key. Delete
the file on the phone (`sudo rm /etc/default/opos-kiosk`, then restart `opos.service`) to close it. Screen blanking and
the power key are not handled by OPOS yet: see [POWER.md](POWER.md).

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
