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
| `opos-clock.service` | raises a clock that reads before the image's floor (the OPOS commit's time, never before 2026) to it at boot, never lowers one; see [The clock](#the-clock) |
| `opos-clock-set.path` | dev images: sets the clock from Unix seconds that the developer's PC drops into `~/opos-set-time` over ssh, with no password |

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

## The clock

The first boot of this image read 1970-01-01, so the lock screen and every time-of-day goal were wrong. What follows
about the port's own clock services comes from reading its unit files in the base and OPOS images (the same file in
both); none of it was measured on the phone.

- `timekeeper.service` (sysinit) sets the clock from the hardware clock plus an offset it keeps in `/data/time/timekeep`
  and stores at a clean shutdown. A fresh userdata has no offset and a power cycle skips the store, so it sets 1970.
- The base's `item-clock-seed.service` is enabled in the OPOS image exactly as in the base and is not tied to item's
  shell. It acts only when `/mnt/vendor/persist/time/ats_1` exists, `/data/time/timekeep` holds no time yet and the
  offset in `ats_1` is positive; otherwise it exits without a word.
- Why it did nothing on the first boot is not established: no journal from that boot is in this repository.
  `opos-clock` now writes what it finds (does `ats_1` exist, what `timekeep` holds, the hardware clock's value, the two
  services' results) to the persistent journal at every boot: `journalctl -b -u opos-clock`.
- `systemd-timesyncd` is the only network time service in the base (no chrony), enabled and unmasked. It needs a
  network that reaches the Debian NTP pool. The hardware clock is never written (`hwclock.service` is masked in the base).

What the image does now:

- `opos-clock.service` runs after `timekeeper` and `item-clock-seed`. A clock earlier than the floor is set to the
  floor (also to the last time timesyncd saved, when that is later and within 20 years); a later clock is never touched.
  `build-image.sh` records the floor as the commit time of the OPOS revision in `/opt/opos/CLOCK_FLOOR`.
  The unit comes after those two and before only `opos-server`; it has no `Before=` on sysinit, the time targets or the
  network, because such an ordering on a clock unit made a cycle on the port once (NetworkManager never started).
- A raised clock is a guess, not the time. `/run/opos/clock-floor` says so until `systemd-timesyncd` reports network time
  (`/run/systemd/timesync/synchronized`) or the time is set below. A clean shutdown keeps the guess for the next boot
  (`/var/lib/opos/clock-guess`), because `timekeeper` stores whatever the clock was.
- The OPOS server reads those files. While the clock is a guess, or before 2026, `/api/state` has
  `battery.clock_ok: false`, `clock` reads `--:--`, goals and `calls_until` are not computed against it, the governor
  gets no goal hours, and the goal note reads "Clock not set". The demo and the public demo do not change. The page's
  lock-screen and status-bar times are Chromium's own `Date`, so they show the guess until it is replaced.

Set the time from the PC, dev images only (built with `OPOS_DEV_SSH_PUBKEY`; the units are in every image, enabled
only in those). The `droidian` user has no passwordless sudo and none is added. Instead a root path unit watches
`~/opos-set-time`; it takes Unix seconds only, between the floor and 20 years past it, refuses symlinks and
anything else, deletes the file and sets the clock. From a Unix shell:

```sh
ssh droidian@<phone-address> "echo $(date -u +%s) > ~/.opos-time && mv ~/.opos-time ~/opos-set-time"
```

From PowerShell:

```powershell
ssh droidian@<phone-address> "echo $([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) > ~/.opos-time && mv ~/.opos-time ~/opos-set-time"
```

The `$(...)` is expanded on the PC inside the double quotes, so the phone gets the PC's time; the `mv` makes the file
appear whole. Check with `ssh droidian@<phone-address> date -u`, which should match the PC's UTC time within a few seconds.
Anything running as `droidian` can use the same file, which is why the path unit is not enabled in other images.
Not yet checked on a phone: the boot ordering, the path unit's trigger on the `mv`, and whether timesyncd steps a guessed clock.

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
