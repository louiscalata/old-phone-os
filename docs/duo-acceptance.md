<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Duo OS acceptance

Status, November 7, 2026: **the suite is written and tested against fake phones; it has not been run on a Duo.** Part 1 was
checked against temporary directories shaped like `/proc`, `/sys` and `/etc`, fake `systemctl`, `journalctl`, `wlr-randr`, `nmcli`
and the like, the real OPOS web server on a fake battery, and (where PowerShell 7 is installed) the PC script against a stand-in
`ssh` and a stand-in Chromium DevTools endpoint. None of that shows that the real tools print what the fakes print; the first run
on the phone is the first real test of the suite itself, so read its first FAIL or WARN as a possible bug in the suite as well.
Part 2 is a checklist for the owner and has no results yet.

This is the acceptance for the OPOS OS image ([packaging/duo-os/README.md](../packaging/duo-os/README.md)): what to run, what it
proves, and what only a person looking at the phone can decide.

## Part 1: the automated suite

Two pieces. `opos-selftest` runs on the phone and checks what a program can read there. `deploy/duo-acceptance.ps1` runs on the PC,
starts `opos-selftest` over ssh, fetches the boot diagnostics, takes a picture of what the page really renders, and writes an
evidence folder. Neither changes anything on the phone.

### Run it

From the PC (PowerShell 7, the Windows OpenSSH client, and a dev image that accepts your key: build it with `OPOS_DEV_SSH_PUBKEY`):

```
pwsh -File deploy\duo-acceptance.ps1 -Host <the phone's address> -Key <private key file> -OutDir <folder for evidence>
```

Add `-RequireScreenshot` to make a missing screenshot a failure, `-NoScreenshot` to skip it, `-User`, `-Port` or `-DebugPort` if the
phone differs from the defaults (`droidian`, 22, 9222), `-SshPath` for an ssh client that is not on PATH, and `-KnownHosts` for
your own host-key file (the default is a file in `-OutDir`; a reflashed image has a new host key, so delete that file's line for
the phone first).

The evidence folder `<OutDir>\duo-acceptance-<UTC time>\` holds `report.md` (the checks, start and finish times, the phone's own
clock, the image revision from `/opt/opos/REVISION`, and a table of every file with its size, time and SHA-256), `SHA256SUMS` (the
same hashes in `sha256sum -c` form), `selftest.txt` (the phone's output as received), `revision.txt`, `opos-boot-diag.txt` (a copy of
`/var/log/opos-boot-diag.txt` when the phone has one), and, when the page could be photographed, `page.png` and `viewport.json`.
The exit status is 0 when no check failed, 1 when at least one is FAIL, and 2 when the phone could not be asked at all (no ssh
login, or an image built before the suite).

On the phone itself, over ssh, as the same user (no root, nothing is written):

```
/usr/sbin/opos-selftest                  # all checks
/usr/sbin/opos-selftest --only battery   # only checks whose id starts with this; repeat --only for more
```

### How to read the lines

One line per check: `PASS`, `FAIL`, `WARN` or `SKIP`, the check's id, and the evidence in the same line. FAIL means the image or
the phone is not as the image promises and the suite exits 1. WARN is something to look at that does not stop the run: a wrong
clock, a dev key left in the image, an error line in the journal. SKIP means the piece is absent (no updater, no keyboard) or the
check cannot run yet (the diagnostics dump is only written 90 seconds after boot); it is not a pass.

| Check | What it reads | FAIL when | WARN or SKIP |
|---|---|---|---|
| `svc.lxc-android`, `svc.hwcomposer`, `svc.opos-server`, `svc.opos` | `systemctl show` of `lxc@android`, `android-service@hwcomposer`, `opos-server`, `opos` | not active (an `activating` restart loop counts) | |
| `svc.phosh-off`, `svc.item-off` | the same for `phosh` and `item`, which must not run next to OPOS | active | WARN if failed |
| `svc.fallback` | whether `opos-fallback.service` ran this boot | | WARN if it did: OPOS failed once and phosh was started |
| `svc.system` | `systemctl is-system-running` | any state other than running, degraded or starting | WARN: degraded (lists the failed units), starting |
| `phoc.process`, `phoc.no-shell-mode` | `/proc/<pid>/cmdline` of phoc | no phoc, two phoc, or `-S`/`--shell` in its command line (a black shield over both panels) | |
| `gfx.composer` | a `*composer*` binary among the processes | none | |
| `gfx.output` | `wlr-randr` with the XDG_RUNTIME_DIR and WAYLAND_DISPLAY the kiosk Chromium itself uses | no output, or none enabled | WARN: not 2784x1800 at scale 2, or the compositor cannot be queried; SKIP: `wlr-randr` missing |
| `gfx.backlight` | `bl_power`, brightness of `panel*-backlight` | a panel's `bl_power` is not 0 | WARN: brightness 0, or fewer than two panels |
| `kiosk.chromium`, `kiosk.gpu` | Chromium processes: one with `--kiosk`, and a `--type=gpu-process` | no kiosk Chromium, or no GPU process | WARN: `--disable-gpu` (the kiosk's fallback after repeated exits) |
| `kiosk.restarts` | the journal lines of the kiosk script | | WARN: Chromium exited this boot; SKIP: this kiosk script does not log |
| `server.http`, `server.state`, `server.token-gate` | the real server on loopback: the page and its token, `/api/state` with the token (battery, charging, `temp_c`, `battery_source`), `/api/state` without it | no answer, no token, a field missing or of the wrong type, a source other than `sysfs`, or the state open without the token | WARN: battery health not measured from sysfs |
| `battery.sysfs`, `battery.api-match`, `battery.temp` | every battery in `/sys/class/power_supply`, compared with what OPOS shows | no battery, or a capacity outside 0 to 100 | WARN: OPOS differs from sysfs by more than 10 points, the charging flag disagrees, or `temp_c` is not the battery's (it is exactly the default 30.0, or off by more than 3 degrees) |
| `clock.year`, `clock.timezone` | the clock, `timedatectl`, OPOS's own zone file | | WARN: the year is before 2026, the clock is the image's floor (a guess), or the zone is UTC |
| `clock.kiosk-tz` | with the first run on: the kiosk script reads the saved zone and exports `TZ`; and, once a zone is chosen, the `TZ` in the kiosk Chromium's environment against it | the kiosk script does not export the saved zone | WARN: the two differ (the screen shows the wrong time until Chromium restarts); SKIP: no zone chosen yet |
| `log.journal`, `log.diag`, `log.opos-errors` | the persistent journal, `/var/log/opos-boot-diag.txt`, error-level lines of the OPOS units this boot | journal not persistent, no dump after 300 seconds of uptime | WARN: error lines, with counts; SKIP: the dump is not due yet |
| `res.disk`, `res.mem` | free space on `/`, `MemAvailable` | under 64 MiB of disk, under 150 MiB of memory | WARN: under 512 MiB, under 500 MiB |
| `net.usb` | `sfduo-usb.service` if the image has it, `usb0` or `rndis0` with an IPv4 address | gadget service installed but not active, no interface while the service is installed, or no address | WARN: no interface and no gadget service (the phone may be reached over Wi-Fi) |
| `ssh.auth` | `sshd_config` and its drop-ins, as configured (not probed: `sshd -T` needs root) | global password or keyboard-interactive login on, empty passwords, root login `yes`, public keys off | WARN: password logins allowed for a non-private network |
| `net.manager`, `net.wifi` | `NetworkManager.service`, `nmcli device` | NetworkManager installed but not active | WARN: no Wi-Fi device, or it is unavailable; SKIP: not installed |
| `net.page`, `net.permissions` | with `OPOS_NETWORK=1`: `GET /network`, Settings linking to it, the keyboard loaded on it (when the web keyboard is on), and `nmcli general permissions` for the four actions joining Wi-Fi needs | the image ships the page (`src/opos/net_page.py`) but the server runs without `OPOS_NETWORK=1`; the page does not answer; Settings has no link; no keyboard on the page | WARN: a permission is not `yes`, or `OPOS_NETWORK` has a value other than 1; SKIP: the image has no Network page and it is not on |
| `ota.updater` | only if `/var/lib/opos-update` exists: the release link, `status.json`, the trusted signers, `opos-update.path` | the path unit is not active | WARN: the last update failed or rolled back, one is in progress, or no signer is installed; SKIP: no updater |
| `osk.keyboard` | with `OPOS_OSK` on: the page loads `/osk.js` and the server serves it (mode `1`/`web`), or the port's keyboard process runs (`stevia`) | the image ships the keyboard (`src/opos/osk.py`) but the server runs without `OPOS_OSK`; the web keyboard is on but not served | WARN: switched off in an image that has one, a unit edited but not restarted, or no keyboard process; SKIP: the image has none |
| `firstrun.flag`, `firstrun.routes` | `OPOS_FIRSTRUN=1` in the server's environment; `GET /firstrun`, `GET /timezone`, `/api/firstrun` with and without the token, Settings linking to `/timezone`, whether the first run is still pending, the saved zone against the zone the server uses | the image ships the first run (`src/opos/firstrun.py`) but the server runs without `OPOS_FIRSTRUN=1`; a route does not answer or the token gate is open | WARN: the server has not applied the saved zone; SKIP: the image has no first run |
| `dev.ssh-key`, `dev.devtools`, `dev.time-drop` | an authorized ssh key for droidian, a Chromium DevTools port, the clock-setting file drop | | WARN each: this is a dev image |

`OPOS_OSK` and `OPOS_NETWORK` are read from the running server's own environment, so a unit file edited without a restart shows up as
a WARN rather than as a pass.

### What Part 1 cannot show

A PASS is evidence from the phone, not a look at the glass. It does not show that the panels display the lock screen, that touch
lands where the finger is, that anything sits outside the hinge column, that the keyboard types the right characters, that Wi-Fi
joins, that an update applies, or that the battery percentage matches another reading. `page.png` is Chromium's own rendering through
DevTools, not a photograph of the panels, and it exists only on a dev image whose kiosk has a DevTools port. `ssh.auth` reads
configuration; it does not try to log in with a password.

The suite changes nothing on the phone, and the code is built that way: `opos-selftest` runs only status queries from a fixed list
(`systemctl show`, `is-active`, `is-system-running`, `list-units`; `journalctl` reads; `timedatectl show`; `ip ... show`;
`nmcli` device and permission tables; `wlr-randr` with no arguments) and refuses anything else, and the PC script's only remote
commands are `opos-selftest`, `cat /opt/opos/REVISION`, `test -r` and `cat` of the diagnostics file, plus one ssh port forward to the
phone's loopback DevTools port. The phone does log the ssh logins, and the server does its usual work to answer `/api/state` (the same
computation the page's own polling triggers); no setting, file or unit is changed.

## Part 2: eyes on the glass

Run Part 1 first and keep its `report.md`. Then work through these on the phone, and write what you saw and the date. A line with no
observation has not been checked. "Fail" here means the expected result did not happen; some failures are known gaps, written in
the "Expected" text, and are findings to keep rather than surprises.

Image revision under test: ______________________  Part 1 evidence folder: ______________________  Date: ____-__-__

### 1. Lock screen on both panels

- How: power the phone on from the PC (a fresh RAM-boot) and wait for the interface.
- Expected: the OPOS lock screen shows on both panels at once: clock and date, and the one-line battery story. No panel stays black,
  no phosh or other desktop appears, the screen does not flicker or restart on its own for two minutes. Part 1's `phoc.*`,
  `gfx.backlight`, `kiosk.*` agree.
- Observed: ____________________________________________  Date: ____-__-__  [ ] as expected  [ ] not

### 2. Touch on both panels

- How: tap a tile, drag and scroll a list, and type nothing; do each on the left panel, then on the right panel, then near the
  hinge edge of each.
- Expected: every touch lands under the finger on both panels: no offset, no mirrored or swapped panel, no dead area except the
  hinge itself; a tap opens the item it covers.
- Observed: ____________________________________________  Date: ____-__-__  [ ] as expected  [ ] not

### 3. Hinge column clear

- How: open several screens (lock, home, Settings, a conversation). Look at the strip over the hinge (42 CSS px, 84 device px). Note
  whether the image runs with the two-screen span setting (`OPOS_SPAN`) or without it; the `pc.viewport` line of the report gives
  the window's size.
- Expected: no text, button or status item sits under the hinge or is cut by it. With the span setting on, the two halves look
  like two screens with an empty gap. With it off, the page may cross the hinge: record where.
- Observed: ____________________________________________  Date: ____-__-__  [ ] as expected  [ ] not

### 4. Rotation behaviour

- How: hold the phone upright, on its side, and upside down, a few seconds each.
- Expected: not designed yet, so this records what happens. Nothing may be cut off, drawn over the wrong panel or crash. Write down
  which it is: no change, rotates correctly, rotates wrongly. Then run `opos-selftest` again: it must still pass.
- Observed: ____________________________________________  Date: ____-__-__  [ ] as expected  [ ] not

### 5. Fold-to-sleep

- How: with the screens lit, fold the phone shut for ten seconds and open it again. If you can ssh in meanwhile (over Wi-Fi, or
  over the cable if its data lines work), read `cat /sys/class/backlight/panel0-backlight/bl_power /sys/class/backlight/panel1-backlight/bl_power`
  while it is folded (4 is off, 0 is lit), and `actual_brightness` of both after opening.
- Expected: both panels go dark while folded and light when opened, at the brightness they had (not full), the page as you left it.
  Known gap: the port's phosh does that blanking and the OPOS image does not replace it yet (a design exists, nothing is
  implemented), so panels that stay lit while folded are the likely result today; record the `bl_power` values and keep it as a
  finding. After opening, `opos-selftest --only gfx` must pass.
- Observed: ____________________________________________  Date: ____-__-__  [ ] as expected  [ ] not

### 6. Typing with the on-screen keyboard

- How: open a place that takes text (the helper's Ask box, or a message). Tap the field. Type `Hello, 123.` with a capital, a
  digit, a comma and a full stop, delete one character, then dismiss the keyboard. Do it once on each panel.
- Expected: a keyboard appears when a field is touched, each key types its own character, backspace deletes, the keyboard goes away
  when dismissed and does not hide the field being typed in. If `opos-selftest --only osk` says SKIP, this image has no keyboard:
  write that down; it is not a pass.
- Observed: ____________________________________________  Date: ____-__-__  [ ] as expected  [ ] not

### 7. Joining Wi-Fi

- How: open Settings, then Network (when the image has it), pick a network, enter its password with the keyboard, join. Then run
  `opos-selftest --only net --only clock`. Restart the phone and see whether it rejoins on its own.
- Expected: it connects within about half a minute and says so; `net.wifi` shows the device connected; with internet, `clock.year`
  stops warning once network time arrives; a wrong password is reported, not retried forever. If `net.permissions` warned, the
  page is expected to say it may not change Wi-Fi: record the exact words.
- Observed: ____________________________________________  Date: ____-__-__  [ ] as expected  [ ] not

### 8. A push of an over-the-air update

- How: follow the developer path in [ota-update.md](ota-update.md) to build a signed bundle of a newer revision and push it to the
  phone's inbox. Before and after, note `cat /opt/opos/REVISION` and the pid in `opos-selftest --only phoc`. Then push a bundle with
  a broken signature.
- Expected: the status moves from verifying to applying to checking to done; the page comes back on the new revision within a
  minute and the lock screen looks the same; `/opt/opos` points at the new release; phoc's pid did not change (an update never
  restarts the compositor); `opos-selftest` passes. The bundle with the broken signature is refused, and the old revision keeps
  running.
- Observed: ____________________________________________  Date: ____-__-__  [ ] as expected  [ ] not

### 9. Battery readout against the Android reading

- How: the reference is the percentage stock Android showed for this phone at about the same charge: write it down before installing
  (Settings, Battery), or read it from the Android restore set or the Termux deployment (`termux-battery-status`, field
  `percentage`). Then read OPOS's percentage on the screen and the `battery.sysfs` line (it lists each gauge).
- Expected: within 3 points of the reference when both are read within a few minutes at the same plug state (a tolerance chosen for
  this checklist, not measured: two gauges and rounding); `battery.api-match` and `battery.temp` pass; the charging state agrees.
  Write down both gauges and the reference.
- Observed: ____________________________________________  Date: ____-__-__  [ ] as expected  [ ] not

### 10. Unplug test

- How: charge to somewhere below 90 percent, check the screen says it is charging, then unplug the cable and do not touch the
  phone. Count the seconds until the screen changes. Plug it in again and count again. If the cable is also your ssh link, you
  cannot run the suite meanwhile: watch the screen, then run `opos-selftest --only battery` afterwards (or use ssh over Wi-Fi).
- Expected: the screen changes from charging to on battery within about 30 seconds (the server re-reads the battery at most every
  15 seconds, and the page polls the server more often than that), and back again after plugging in. Afterwards
  `battery.api-match` passes with the charging flag agreeing.
- Observed: ____________________________________________  Date: ____-__-__  [ ] as expected  [ ] not

### Sign-off

Part 1: PASS / FAIL, with ____ WARN and ____ SKIP explained in the evidence folder.  Part 2: ____ of 10 as expected.
Findings to keep (each with the check number): ______________________________________________________________

## Limits

The checks and their thresholds are this checklist's own choices. Nothing here certifies battery life, call quality or safety.
Calls and texts are still simulated in OPOS, so they are not part of this acceptance.
