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
| `/opt/opos` | OPOS runtime at an exact revision (`/opt/opos/REVISION`); stdlib Python on the image's python3. A symlink to `/opt/opos-releases/0-<rev12>`, so an update can switch releases atomically |
| `opos-server.service` | `python3 -m opos.web --live-battery` as user `droidian`: the rules on the phone's sysfs battery |
| `opos.service` | the shell: `phosh-session` with `PHOSH_SESSION_BIN=/usr/libexec/opos/opos-kiosk`, so `phoc` starts exactly as the port starts it (hwcomposer output, hinge seam, scale 2) with OPOS full-screen as its only client |
| `opos-kiosk` | waits for the server, then keeps Chromium in kiosk mode on the OPOS page, restarting it if it exits |
| `opos-fallback.service`, `opos-switch` | as the port's item: a failed OPOS shell starts phosh; `sudo opos-switch opos\|phosh\|status` changes shells |
| `opos-updater`, `opos-update*.path/.service` | the root side of over-the-air updates (below): checks and applies signed bundles, rolls back a bad one |

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

## Over-the-air updates

Status: implemented and tested on a PC (`tests/test_ota_roundtrip.py` builds, delivers, applies, refuses and rolls back real
signed bundles with stand-ins for systemd, runuser and the browser); **not tested on the phone**, which has not booted
this image. The contract is [docs/ota-update.md](../../docs/ota-update.md); the owner's steps are in
[docs/ota-release-howto.md](../../docs/ota-release-howto.md).

What it does: the running phone can take a new OPOS app tree (`/opt/opos`) and new OPOS scripts and units without a new
image. A bundle is signed on your PC (`tools/opos-release.py`, an ed25519 key and `ssh-keygen -Y sign`). The phone's root
updater verifies the signature, serial and every file hash, starts the new tree once as a smoke test, switches
`/opt/opos`, restarts `opos-server`, checks that it answers, and goes back by itself if it does not. It never restarts
`opos.service` (the compositor session; restarting it has reset this phone). The kernel, initramfs, vendor layer, Debian
packages, the updater and its trust keys change only with a new image.

Build options, as environment variables of `build-image.sh`:

- `OPOS_UPDATE_SIGNERS=<file>`: public keys the image trusts, one `ssh-ed25519 AAAA...` or `opos-release ssh-ed25519 AAAA...`
  line each (`python tools/opos-release.py keygen-help --pub <key>.pub` prints one). **Without it the image accepts no update
  at all**, and the build says so. A private key or any other line stops the build.
- `OPOS_UPDATE_URL=https://<host>/<folder>/latest.json`: where the phone may look for updates, checked only when asked. Without
  it the phone fetches nothing.
- `OPOS_DEV_SSH_PUBKEY=<login key>.pub`: needed for the USB push below.

Push a release over the USB network (no Wi-Fi, no host), then follow the phone's answer:

```sh
python tools/opos-release.py build --serial 1 --version 0.2.0 --key <release key> --out dist --overlay opos-kiosk
powershell -File deploy\duo-push-update.ps1 -Bundle dist\opos-1.opos-update -Host <phone address> -Key <login key>
```

The phone's side, for checking and going back: `/usr/libexec/opos-updater/opos-updater status` prints the running release
and the last result; `/var/lib/opos-update/status.json` is the same for programs; an empty file `rollback.request` in
`/var/lib/opos-update/inbox/` (or `/usr/libexec/opos-updater/opos-updater rollback` as root) returns to the previous release. OPOS also serves an
update page at `http://127.0.0.1:8765/update` on the phone (check, download, rollback). The main page does not link to it yet,
because that page is at its size budget, so the kiosk cannot reach it by touch; use the USB push until a link is added.

Limits: nothing here has run on the phone, so expect to find problems at the first real push. A serial that failed on a phone is
never accepted again there (the fix goes out under a new serial). There is no way to revoke a release key over the air; a lost
or stolen key means a new image. A `boot`-scope bundle is staged and applied at the next boot, and rolls back after two failed
boots.

## Restore to Android

- Full stock 2022.902.48 (all 25 partitions, `surface/duo/duo:12/2022.902.48`, AT&T product config included) was
  extracted from the OTA payload and hash-checked; flash it from fastboot, logical partitions through fastbootd,
  then wipe.
- A partition backup of this phone from October 31, 2026 also exists on the owner's PC.
