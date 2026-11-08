<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# OPOS OS for the Surface Duo

An OPOS phone with no Android system: the Surface Duo boots a Linux rootfs whose only interface is OPOS.

Status, November 7, 2026: **ran on the owner's Surface Duo 1, RAM-booted from a PC; the owner looked at the
screens and said "it's working".** One phone, one day, one person watching. It is not an installed system: it
lives on userdata and starts only from a RAM-boot (see below), and a power cycle returns the phone to Android.

## What was seen, and what was not

Seen on November 7, 2026, in logs read off the phone over the USB network, and by the owner looking at the panels:

- the system reached `running`, and both backlights were on;
- `phoc` ran on the `hwcomposer` backend, with GLES on the Adreno 640;
- OPOS read the battery from the phone's sysfs (`--live-battery`), not from the simulator;
- an earlier build left both panels black with every process running; the cause was phoc's shell mode (below),
  and the panels lit once `opos-session` started phoc without it.

Not measured, and not claimed: speed or smoothness, battery life or drain under OPOS, stability over hours or
across many boots, suspend and resume, Wi-Fi, audio, and the failure path (`opos-fallback` starting phosh,
`opos-switch`). Calls and texts are still OPOS's simulated modem: the base has ModemManager (through ofono2mm),
but `src/opos/mm_modem.py` is not wired in. The raw logs from that day are not part of this repository.

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
| `opos-server.service` | `python3 -m opos.web --live-battery` as user `droidian`, on `127.0.0.1:8765`: the rules on the phone's sysfs battery |
| `opos.service` | the shell, with the hardware setup the port gives phosh and item (hwcomposer backend, vendor composer, VT 7); it runs `opos-session` |
| `opos-session` | starts `phoc` as phosh-session does (the port's `phoc.ini`, hwcomposer output, hinge seam, scale) but **without `-S`**, with `opos-kiosk` as phoc's only client |
| `opos-kiosk` | waits for the server, then keeps Chromium in kiosk mode on the OPOS page, restarting it if it exits |
| `opos-fallback.service`, `opos-switch` | as the port's item: a failed OPOS shell starts phosh; `sudo opos-switch opos\|phosh\|status` changes shells |
| `opos-diag`, journald drop-in | boot evidence that survives a failed boot (below) |

**Why no phoc shell mode.** `phosh-session` starts phoc with `-S`. In that shell mode phoc keeps an opaque black
shield over every output until a phosh client reports that the shell is up. OPOS's kiosk is not one, so on
November 7 both panels stayed black while every process ran. `opos-session` leaves `-S` out, and
`verify-image.sh` fails an image whose session script passes it. phosh and item stay installed but are not
started at boot.

## RAM-boot only, and the misc brake

Every boot of this image is a RAM-boot from a PC: `fastboot boot boot.img`. Nothing is installed to the phone's
boot partitions, on purpose.

- This phone's bootloader refuses flashes of the boot partition with `Device Error` even when unlocked
  (`boot_a`, recorded October 31, 2026; vbmeta is reported to be refused the same way), and the port's own
  notes forbid flashing boot, vbmeta, dtbo or recovery in any case. `fastboot boot` works. No tool here writes
  those partitions or changes the active slot; making the image permanent would need a different, separately
  decided method.
- The **misc brake** is `misc-brake.img`, 2,048 bytes for the `misc` partition: a one-shot
  `bootonce-bootloader` flag, so the next boot lands in Boot Select instead of in stock Android. If stock
  Android boots while this rootfs sits on userdata, it can write a wipe request into `misc`, after which the
  bootloader rejects everything on that slot until `misc` is zeroed from TWRP (the port's "BCB poison"). The
  brake is armed before TWRP and again before the kernel. It is a precaution, not a guarantee: on October 31
  a reboot from an initramfs with the flag armed reached stock recovery, not fastboot.
- **One RAM-boot, never retried.** A kernel that crashed can leave the slot rejecting further RAM-boots with
  `Device Error`. If the boot fails, stop.
- **Never run `reboot` or `systemctl reboot` on the RAM-booted phone**: it would start the stock kernel on a
  phone holding a foreign rootfs. Use `reboot bootloader`, re-arm the brake, and RAM-boot once from the PC
  ([docs/duo-usb-install.md](../../docs/duo-usb-install.md), "Rebooting safely / recovering").

## Build and check

On a Linux builder with `zstd`, `e2fsprogs` and `qemu-user-static`:

```sh
cd <release folder> && sha256sum -c SHA256SUMS
zstd -d --long=27 rootfs.img.zst -o base-rootfs.img        # must hash to the manifest's 9299b2c0…
sudo sh packaging/duo-os/build-image.sh base-rootfs.img opos-os-rootfs.img HEAD
sudo sh packaging/duo-os/verify-image.sh opos-os-rootfs.img
```

`verify-image.sh` checks the unit wiring, that phoc is not started in shell mode, phoc's `-E` startup command and
the journal settings, and runs the OPOS server from the image with the image's own aarch64 python3 under
qemu-user. It is not a boot, display or battery test.

## Install: RAM-boot from a Windows PC

This erases Android and everything it stored on the phone. Back up first; a restore set exists (below). Needs
the bootloader unlocked, the battery at 20 % or more, the phone in Boot Select (fastboot), `fastboot.exe` and
`adb.exe` on PATH. The image folder holds `boot.img` and `surfaceduo1-twrp.img` from the port release,
`misc-brake.img` (the port's `tools/make-misc-brake.sh` makes it) and `opos-os-rootfs.img`:

```powershell
powershell -File deploy\duo-opos-boot.ps1 -ImageDir <folder> -RootfsSha256 <sha256 of opos-os-rootfs.img>
powershell -File deploy\duo-opos-boot.ps1 -ImageDir <folder> -RootfsSha256 <sha256> [-Key <private key>] -Yes
```

The first command checks the four files against their SHA-256 and prints the steps without touching the phone.
With `-Yes` it checks product, slot a, unlocked and battery; arms the brake; formats userdata; RAM-boots TWRP;
pushes the rootfs and compares its hash on the phone; returns to the bootloader; re-arms the brake; RAM-boots
the port's kernel once; then finds the phone on the USB network and, only if `-Key` is given, reads its state
and logs over ssh. The first failure stops it and nothing is retried. The safety rules are written at the top
of the script.

The phone sets no default gateway and is not in the PC's neighbour table until something contacts it, so the
script derives the /24 from the address of the PC's RNDIS adapter and probes TCP port 22 across it, trying host
82 first (`-PreferHost`; on November 7 the phone was host 82 and the PC's adapter host 147 of a 10.15.19.x
network). `-PhoneIp` skips the search. The scratch version of this script ran the chain on November 7; this version
changes its network discovery and options and has been parsed and had its probe exercised against loopback,
not run on a phone.

`deploy/duo-rootfs-install.sh` (any host with fastboot and adb; a dry run unless `--yes`) does the formatting,
push, verification and one kernel boot too, but not the brake, the preflight or the network search: it is the
less guarded route.

## Dev options: an ssh key

`OPOS_DEV_SSH_PUBKEY=<file.pub> sh packaging/duo-os/build-image.sh …` appends that public key to
`~/.ssh/authorized_keys` of the `droidian` user in the image (the directory is made 0700, the file 0600) and prints a
`DEV IMAGE` line; `verify-image.sh` notes how many keys the image holds. It grants **a shell as `droidian` to
whoever holds the matching private key**, over the USB network and over any other network where the phone's
sshd can be reached (the port's sshd is not limited to the USB link; keys are accepted from any address,
passwords only from the port's own USB subnet, 172.16.42.x). It is meant for reading logs and testing live;
leave it unset for an image that will be carried. With the variable unset the image adds no login of its own,
but the port's password rule stays as it is.

Remove it from a running phone with `rm ~/.ssh/authorized_keys` as `droidian` (this deletes every key in the
file, not only the one the build added) or by deleting that one line. Rebuilding without the variable gives an
image without it. The key lives in userdata until the next format.

## Boot evidence: persistent journal and opos-diag

The rootfs lives on userdata, so a failed boot would otherwise leave nothing behind. The image sets
`Storage=persistent` with `SystemMaxUse=200M` (`/etc/systemd/journald.conf.d/50-opos-persistent.conf`), so
`journalctl -b -1` can show the previous boot. `opos-diag.service` runs once per boot, 90 seconds after it
starts, and writes `/var/log/opos-boot-diag.txt` (overwritten each boot): system state, failed units, the
OPOS, composer and phoc processes, `/dev/dri`, network addresses, power supplies, the OPOS and phoc journals,
DRM clients, backlight values and kernel warnings. Read it over ssh. From TWRP the same files can be read by
mounting userdata and loop-mounting `/data/rootfs.img`; that has not been rehearsed. A format erases both.

## Known issues

- **The clock reads 1970 until it is set.** Nothing sets it at boot. OPOS takes its time from the phone's clock,
  so goal times and "time left" are not meaningful until it is set (for example `sudo date -s …` over ssh), and
  journal timestamps before that are wrong. No automatic fix has been tried.
- **A power cycle boots stock Android, which wipes the OS image.** The RAM-boot does not survive a restart, and
  Android does not accept this userdata and sets itself up on it (the port's notes expect that; the sequence was
  not re-measured here). To get the image back, run the install chain
  again with the phone in Boot Select (Power held about 20 seconds, then Volume-down and Power;
  [docs/duo-usb-install.md](../../docs/duo-usb-install.md), "Button steps").
- Calls and texts are simulated (see above); the real modem is not used.
- `opos-fallback` and `opos-switch` have not been exercised on the phone.
- One address, one run: host 82 of a 10.15.19.x network is where the phone answered once, not a documented constant.

## Restore to Android

Nothing was written to boot, vbmeta, dtbo or recovery, and no slot was changed, so the stock Android
(2022.902.48) is still installed and is what the phone starts when the OS image is not RAM-booted.

1. Power cycle the phone (Power held about 20 seconds). Android starts from the untouched boot partition,
   does not accept the OS image's userdata and sets itself up from scratch; the OS image and everything on
   userdata are gone. (If a failed install left the brake armed, the first start goes to Boot Select once;
   power cycle again.)
2. If Android does not come up (a boot loop, or a recovery that offers to wipe), use the stock recovery's factory
   reset; that cured an earlier boot loop on this phone on November 7. If `misc` holds a wipe request that the
   bootloader keeps rejecting boots against, zero the first 2 KB of `misc` from a RAM-booted TWRP.
3. For a full stock reflash there is the extracted set for 2022.902.48 (all 25 partitions,
   `surface/duo/duo:12/2022.902.48`, AT&T product config included), checked against the OTA payload's own
   hashes: flash it from fastboot, logical partitions through fastbootd, then wipe. No restore from it is
   recorded in this repository, and the OTA's publisher hash was not independently confirmed.
4. A partition backup of this phone from October 31, 2026 also exists on the owner's PC.

The Android route that keeps Android and flashes nothing is in [docs/duo-android.md](../../docs/duo-android.md).
