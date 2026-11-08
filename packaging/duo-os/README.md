<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# OPOS OS for the Surface Duo

An OPOS phone with no Android system: the Surface Duo boots a Linux rootfs whose only interface is OPOS.

Status, November 7, 2026: **ran on the owner's Surface Duo 1, RAM-booted from a PC; the owner looked at the
screens and said "it's working".** One phone, one day, one person watching. It is not an installed system: it
lives on userdata and starts only from a RAM-boot (see below), and a power cycle returns the phone to Android.
The pieces added since (keyboard, Network page, first run and time zone, acceptance suite) are built and tested off the phone only.
[docs/duo-os-final.md](../../docs/duo-os-final.md) says what the OS does now, what is still simulated, what is not known, and
lists the live checks in order. The owner's Duo cannot take a fresh install yet: its USB-C port charges but carries no data, and
every install route below needs fastboot over USB. Android's wireless debugging cannot write partitions.

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
| `opos-server.service` | `python3 -m opos.web --live-battery` as user `droidian`, on `127.0.0.1:8765`: the rules on the phone's sysfs battery; sets the switches below |
| `opos.service` | the shell, with the hardware setup the port gives phosh and item (hwcomposer backend, vendor composer, VT 7); it runs `opos-session` |
| `opos-session` | starts `phoc` as phosh-session does (the port's `phoc.ini`, hwcomposer output, hinge seam, scale) but **without `-S`**, with `opos-kiosk` as phoc's only client |
| `opos-kiosk` | waits for the server, then keeps Chromium in kiosk mode on the OPOS page. It clears a stale profile lock before each start; a Chromium that keeps dying within 20 s is restarted after 2, 2, 4, 8 ... 60 s and, from the third quick exit, without the GPU, and after the tenth in a row the kiosk gives up (below). Every exit is logged: `journalctl -t opos-kiosk`; the owner's time zone is put in Chromium's environment at every start |
| `sfduo-composer-watchdog` | the port's script, edited at build time so that its bounce of a composer that is not DRM master restarts OPOS as well as phosh (`verify-image.sh` checks the edit; an upgrade of the port's package reverts it) |
| `opos-fallback.service`, `opos-switch` | as the port's item: a failed OPOS shell starts phosh; `sudo opos-switch opos\|phosh\|status` changes shells |
| `opos-diag`, journald drop-in | boot evidence that survives a failed boot (below) |
| `opos-clock.service` | raises a clock that reads before the image's floor (the OPOS commit's time, never before 2026) to it at boot, never lowers one; see [The clock](#the-clock) |
| `opos-clock-set.path` | dev images: sets the clock from Unix seconds that the developer's PC drops into `~/opos-set-time` over ssh, with no password |
| on-screen keyboard | in-page web keyboard served by `opos-server` (`src/opos/osk.py`); `opos-osk` and `opos-osk.service` are the opt-in Wayland one, enabled but idle |
| Network page | `/network` (`src/opos/net.py`, `net_page.py`); drop-in `opos-network.conf`; optional polkit rule `50-opos-network.rules` |
| first run, time zone | `/firstrun` and `/timezone` (`src/opos/firstrun.py`, `timezone.py`), the saved zone, the clock sentence |
| `opos-selftest` | read-only acceptance checks on the phone, `/usr/sbin/opos-selftest`; the PC side is `deploy/duo-acceptance.ps1` |

**Why no phoc shell mode.** `phosh-session` starts phoc with `-S`. In that shell mode phoc keeps an opaque black
shield over every output until a phosh client reports that the shell is up. OPOS's kiosk is not one, so on
November 7 both panels stayed black while every process ran. `opos-session` leaves `-S` out, and
`verify-image.sh` fails an image whose session script passes it. phosh and item stay installed but are not
started at boot.

**When Chromium never starts.** After 10 quick exits in a row (each under 20 s, with about 4 minutes of back-off
between them) `opos-kiosk` logs it, sends `phoc` SIGTERM (phoc may not end with its client) and exits 1.
That ends the session, so `opos.service` (`Restart=always`) starts a new one, and its start limit (3 starts in
`StartLimitIntervalSec=30min`; the old 60 s could not be reached, because one session takes minutes) refuses the
fourth: the unit fails and `OnFailure=` runs `opos-fallback.service`, which starts phosh. Expect about 15 minutes of
a dark or flickering screen first. Shown: the give-up path with a stub Chromium (`tests/test_duo_kiosk.py`), and the
restart, start-limit and `OnFailure=` chain on real systemd with a scaled model of the unit (`OPOS_TEST_SYSTEMD=1`,
root; the same test file). Not shown: that phoc ends on SIGTERM, how long a real session takes, and that
`opos-fallback` brings phosh up, since none of it has run on the phone. So the fallback is reachable in a model,
not yet end to end.

Over-the-air updates are a design ([docs/ota-update.md](../../docs/ota-update.md)), not part of this tree.

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
| `OPOS_SPAN` | not set; a commented line in `opos-server.service`, or `--span` | `675,42`: pane and hinge widths in CSS px, so the page lays out for the window that covers both panels (see Spanning both panels); `off` disables |
| `OPOS_OSK_BIN`, `OPOS_OSK_WAIT`, `OPOS_OSK_GSETTINGS` | not set | only `opos-osk` (the opt-in Wayland keyboard) reads them: the binary, seconds to wait for phoc's socket, `0` to leave gsettings alone |
| `OPOS_KIOSK_DEBUG_PORT` | `/etc/default/opos-kiosk`, written only by a dev image | the Chromium DevTools port on `127.0.0.1`; `opos-kiosk` reads it from that file alone, never from a unit or the caller's environment |
| `OPOS_DEV_SSH_PUBKEY` | build input | a public key file; the image then lets that key log in as `droidian` (a dev image) |

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
on one panel (675 px) keeps the phone layout. A Device type chosen in Settings is still honoured. Settings reads
"Auto looks like: two screens" while the window spans both panels, and whatever the window's width it adds the window's
size, the output scale and the span it was given ("Window 1392 x 900 CSS px at 1x, span 675 + 42 + 675."), again after
every resize, so the numbers can be checked on the phone. Unset, the served page is byte-for-byte what it was. The
service file carries the setting commented out, to be enabled once the layout has been checked on the phone. Not
handled: a rotated window (the hinge would run across it), a phone folded with one panel dark, and two things that
still cross the hinge: the thin battery bar along the top edge and, while it plays, the zoom from a tile to its
app.

## Build and check

On a Linux builder with `zstd`, `e2fsprogs` and `qemu-user-static`:

```sh
cd <release folder> && sha256sum -c SHA256SUMS
zstd -d --long=27 rootfs.img.zst -o base-rootfs.img        # must hash to the manifest's 9299b2c0…
sudo sh packaging/duo-os/build-image.sh base-rootfs.img opos-os-rootfs.img HEAD
sudo sh packaging/duo-os/verify-image.sh opos-os-rootfs.img
```

`verify-image.sh` checks the unit wiring, that phoc is not started in shell mode, phoc's `-E` startup command and
the journal settings, runs the OPOS server from the image with the image's own aarch64 python3 under
qemu-user, and starts a second one with the keyboard, the Network page and the first run all on to ask it for each page.
It is not a boot, display or battery test.
`tests/test_duo_kiosk.py` runs the kiosk script under `sh` with a fake `chromium` (locks, back-off, the no-GPU retry).

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
- A clean shutdown can make a wrong guess stick. This is a reviewer's reading of the two unit files, not measured on the
  phone. `timekeeper` treats a stored value under about 31 years as "no offset", and at a clean shutdown it stores
  whatever the clock reads. After a shutdown from a clock that `opos-clock` raised, `/data/time/timekeep` holds a time far
  above that, and `item-clock-seed` leaves a `timekeep` that already holds a time alone. If the unexplained 1970 was
  only a race with the persist mount (`ats_1` present, the seed skipped), the first clean shutdown would turn a retryable
  state into a stuck one: Android's offset in `ats_1` could never replace the guess on a later boot. `opos-clock`
  cannot undo that, because it has to stop before `timekeeper` stores. To let the seed try again, on the phone, with the
  owner's own password: `sudo sh -c 'echo -1 > /data/time/timekeep'`. A clean shutdown stores the clock again, so as read
  the next boot has to follow a power-off that skips the store (hold the power key), not a Reboot.
  So the first device check records the `ats_1` line before any shutdown:
  `journalctl -b -u opos-clock | grep 'evidence: persist'` ("present, N bytes" or "absent").
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
Not yet checked on a phone: the boot ordering, the path unit's trigger on the `mv`, whether timesyncd steps a guessed
clock, and the `timekeep` trap and its recovery above.

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
of the script. If Windows refuses to run the script under its default execution policy, put
`-ExecutionPolicy Bypass` before `-File` (the repository's `install.cmd` does the same).

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
- **The search finds the phone only if the PC's USB adapter holds an address in the phone's /24.** The script
  takes the /24 from the RNDIS adapter's IPv4 address. If the adapter shows only a 169.254.x.x address, the PC
  has no lease from the phone (seen on October 31, 2026): the sweep covers the wrong network and the script
  times out after the kernel boot. Do not run the script again to retry: that repeats the whole chain, including
  the userdata format and a second kernel boot, and it has no attach-only mode. Find the phone and ssh to it by
  hand.

## Restore to Android

Nothing was written to boot, vbmeta, dtbo or recovery, and no slot was changed, so the stock Android
(2022.902.48) is still installed and is what the phone starts when the OS image is not RAM-booted.

1. Power cycle the phone (Power held about 20 seconds). Android starts from the untouched boot partition. The
   port's notes expect it not to accept the OS image's userdata and to set itself up from scratch, which leaves
   the OS image and everything on userdata gone; that sequence was not re-measured here. (If a failed install
   left the brake armed, the first start goes to Boot Select once; power cycle again.)
2. If Android does not come up (a boot loop, or a recovery that offers to wipe), use the stock recovery's factory
   reset; that cured an earlier boot loop on this phone on November 7. The "never pick Factory data reset" rule
   in the Button steps of [docs/duo-usb-install.md](../../docs/duo-usb-install.md) applies while the OS image is
   being installed; when you are restoring Android, the reset is the intended step. If `misc` holds a wipe
   request that the bootloader keeps rejecting boots against, zero the first 2 KB of `misc` from a RAM-booted
   TWRP.
3. For a full stock reflash there is the extracted set for 2022.902.48 (all 25 partitions,
   `surface/duo/duo:12/2022.902.48`, AT&T product config included), checked against the OTA payload's own
   hashes: flash it from fastboot, logical partitions through fastbootd, then wipe. No restore from it is
   recorded in this repository, and the OTA's publisher hash was not independently confirmed.
4. A partition backup of this phone from October 31, 2026 also exists on the owner's PC.

The Android route that keeps Android and flashes nothing is in [docs/duo-android.md](../../docs/duo-android.md).
