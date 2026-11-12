<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# OPOS OS for the Surface Duo

An OPOS phone with no Android system: the Surface Duo boots a Linux rootfs whose only interface is OPOS.

Status, November 7, 2026: **ran on the owner's Surface Duo 1, RAM-booted from a PC; the owner looked at the
screens and said "it's working".** One phone, one day, one person watching. It is not an installed system: it
lives on userdata and starts only from a RAM-boot (see below). A power cycle can return the phone to Android, destroying
the OPOS install: read-only observations from the PC session on November 11, 2026 record that any stock Android boot on
OPOS userdata applies metadata encryption in place, leaving the OPOS rootfs unreadable to Linux.
The pieces added since (keyboard, Network page, first run and time zone, acceptance suite) are built and tested off the phone only.
[docs/duo-os-final.md](../../docs/duo-os-final.md) says what the OS does now, what is still simulated, what is not known, and
lists the live checks in order. The owner confirmed on November 11, 2026 that the Duo's USB-C port carries data; every install route below needs fastboot over USB. Android's wireless debugging cannot write partitions.

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
0.22.1, kernel `4.14-190-microsoft-surfaceduo`), whose hash the owner's PC recomputed on 2026-11-12 from the file its build uses
(`rootfs.img` SHA-256 `9299b2c0…e312`, equal to the publisher's saved, unsigned manifest; the publisher's release page was offline that day, so there is no independent comparison and no second copy). That base keeps what mainline Linux cannot yet do on this phone: the
vendor kernel and the Halium layer drive both panels, touch, GPU, Wi-Fi, audio and the modem. Mainline's
`sm8150-microsoft-surface-duo.dts` covers storage, USB, buttons, the two bq27742 fuel gauges and the modem but no
display, so a mainline-only OPOS would boot to a dark screen.

On top of the base, `build-image.sh` adds:

| Piece | Role |
|---|---|
| `/opt/opos` | OPOS runtime at an exact revision (`/opt/opos/REVISION`); stdlib Python on the image's python3. A symlink to `/opt/opos-releases/0-<rev12>`, so an update can switch releases atomically |
| `opos-server.service` | `python3 -m opos.web --live-battery` as user `droidian`, on `127.0.0.1:8765`: the rules on the phone's sysfs battery; sets the switches below; keeps the page token in `/var/lib/opos/token` (mode 0600, made on the first start), so a restart of the server leaves the open page working |
| `opos.service` | the shell, with the hardware setup the port gives phosh and item (hwcomposer backend, vendor composer, VT 7); it runs `opos-session` |
| `opos-session` | starts `phoc` as phosh-session does (the port's `phoc.ini`, hwcomposer output, hinge seam, scale) but **without `-S`**, with `opos-kiosk` as phoc's only client. phoc runs as its child, so that when the kiosk gives up the script ends phoc and exits 75 (below) |
| `opos-kiosk` | waits for the server before every start of the browser (so a kiosk relaunched after an update waits for the restarted server), then keeps Chromium in kiosk mode on the OPOS page. It clears a stale profile lock before each start; a Chromium that keeps dying within 20 s is restarted after 2, 2, 4, 8 ... 60 s and, from the third quick exit, without the GPU, and after the sixth in a row, or the tenth of any length within 10 minutes, the kiosk gives up (below). Every exit is logged: `journalctl -t opos-kiosk`; the owner's time zone is put in Chromium's environment at every start |
| `sfduo-composer-watchdog` | the port's script, edited at build time so that its bounce of a composer that is not DRM master restarts OPOS as well as phosh (`verify-image.sh` checks the edit; an upgrade of the port's package reverts it) |
| `opos-fallback.service`, `opos-switch` | as the port's item: a failed OPOS shell starts phosh; `sudo opos-switch opos\|phosh\|status` changes shells |
| `opos-updater`, `opos-update*.path/.service` | the root side of over-the-air updates (below): checks and applies signed bundles, rolls back a bad one |
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

**When Chromium never starts.** After 6 quick exits in a row (each under 20 s, about half a minute of back-off), or 10 exits
of any length within 10 minutes (a Chromium that crashes every 20 to 60 s), `opos-kiosk` leaves a marker file
(`opos-gave-up` in the runtime directory) and exits 1. `opos-session`, which runs phoc as its child, sees it, ends phoc
(TERM, five seconds, KILL) and exits 75. `opos.service` has `RestartPreventExitStatus=75`, so it fails at once instead of
restarting, and `OnFailure=` runs `opos-fallback.service`, which starts phosh. No start-limit arithmetic is involved; the
unit keeps its original `StartLimitIntervalSec=60`, so a manual restart or the composer watchdog's try-restart does not
count against a long window. The unit fails about a minute after it started when Chromium dies at once, and about three
minutes at the very worst (phosh needs the fallback's composer reset and its own start after that); a wait for an OPOS
server that does not answer comes first, before every start of Chromium and not only the first: up to 120 one-second
tries, so about two minutes when nothing listens on the port and up to six when something accepts and never answers (it
runs before the start time is taken, so it never counts as a quick exit, and not at all once the marker is written). A
phoc that itself keeps dying is not a give-up: it still depends on the start limit, 4 starts in 60 s, which the 20 to 40 s
of `item-composer-fresh` per start may keep from being reached (not measured). Four starts of `opos.service` within 60 s,
the first one and manual restarts included, fail the unit and start phosh unless `item-composer-fresh` makes each start
take 20 s or more, and `sudo opos-switch opos` brings OPOS back.

**Phosh after a failed OPOS** is the stock Droidian shell, started for this boot only: nothing is switched off, OPOS is not
running, and the next boot tries OPOS again. To get OPOS back without rebooting, run `sudo opos-switch opos` (over ssh or
from phosh's terminal); `sudo opos-switch phosh` makes phosh the default instead. The reason is in
`journalctl -t opos-kiosk` and `journalctl -u opos.service`.

Shown: the whole chain on real systemd 259 in a model (`OPOS_TEST_SYSTEMD=1`, root, `tests/test_duo_kiosk.py`) with the
shipped `opos-session`, `opos-kiosk` and fallback command, a stub phoc that ignores TERM, one that obeys it and one that
ends with its client, and a stub Chromium that dies at once: the unit fails with status 75, is not restarted, and the
stand-in phosh starts within seconds. Only on paper: how the real phoc reacts to TERM and KILL and shuts the hwcomposer
down, the real timings on the phone, and that phosh comes up afterwards. So the fallback is reachable end to end in the
model, not yet on the phone.

Over-the-air updates are described [below](#over-the-air-updates); the contract is [docs/ota-update.md](../../docs/ota-update.md).

## Switches

Every piece is off unless its switch is set, and then the page and every route are exactly what they were. The image sets them in
its units; a missing one is a FAIL in `opos-selftest` when the image carries the piece.

| Variable | Set by | Meaning |
|---|---|---|
| `OPOS_OSK` | `opos-server.service` (`1`), overridden by `/etc/default/opos-osk` | `1`/`web` in-page keyboard, `stevia` the port's keyboard, `off` none |
| `OPOS_OSK_HINGE` | not set (42) | CSS px between the keyboard's halves when the page does not say |
| `OPOS_NETWORK` | drop-in `opos-server.service.d/20-opos-network.conf` | the Settings link to `/network` and the actions that change Wi-Fi |
| `OPOS_NET_TTY` | not set | `1`: hand the Wi-Fi password to `nmcli` through a pseudo-terminal |
| `OPOS_NET_POLKIT` | build input | `1`: install `50-opos-network.rules`; unset, empty or `0`: not installed; anything else stops the build |
| `OPOS_FIRSTRUN` | `opos-server.service` (`1`) | first-run setup, `/timezone`, the saved zone |
| `OPOS_KIOSK_RESTART` | not set | `0`: do not restart Chromium after a zone change |
| `OPOS_CONFIG_DIR` | not set (`~/.config/opos`) | where the config, first-run flag and saved zone live; the server and `opos-kiosk` both read it |
| `OPOS_PANES` | `opos-server.service` (`1`) | the black thumb pane, the white list pane and the Hand setting on the spanned window (`src/opos/panes.py`, [docs/jaryo-panes.md](../../docs/jaryo-panes.md)); paints only while `OPOS_SPAN` is on; `1,pure` strict `#000`/`#fff`, `invert` color swap without the mirror, unset none |
| `OPOS_SPAN` | `opos-server.service` (`675,42`), or `--span` | pane and hinge widths in CSS px, so the page lays out for the window that covers both panels (see Spanning both panels); `off` disables. Not yet measured on the phone |
| `OPOS_DEV_SPAN` | build input, dev images only | `1`, together with `OPOS_DEV_SSH_PUBKEY` (the build refuses it alone): the image gets the drop-in `opos-server.service.d/30-opos-dev-span.conf`, which sets `OPOS_SPAN=675,42` for a checkpoint that is to be looked at on the phone; unset: no drop-in (see Spanning both panels) |
| `OPOS_DEV_SUDO` | build input, dev images only | `1`, together with `OPOS_DEV_SSH_PUBKEY` (the build refuses it alone): the image gets `/etc/sudoers.d/90-opos-dev` (0440 root:root, exactly `droidian ALL=(ALL) NOPASSWD:ALL`), so the dev login user is root; such an image must never be carried or shared; unset: no file (see Dev options: passwordless sudo) |
| `OPOS_MONITOR` | `opos-server.service` (`1`) | the Settings link to `/monitor` and the routes `/monitor` and `/api/monitor` (see Inference page) |
| `OPOS_AGIW_URL`, `OPOS_AGIW_HOST` | optional `/etc/opos/monitor.env`, absent from the image | where the Inference page looks for an AGIW monitor, and the `Host` header to send it (`OPOS_LOCAL_URL` is the Helper's, see Inference page) |
| `OPOS_OSK_BIN`, `OPOS_OSK_WAIT`, `OPOS_OSK_GSETTINGS` | not set | only `opos-osk` (the opt-in Wayland keyboard) reads them: the binary, seconds to wait for phoc's socket, `0` to leave gsettings alone |
| `OPOS_KIOSK_DEBUG_PORT` | `/etc/default/opos-kiosk`, written only by a dev image | the Chromium DevTools port on `127.0.0.1`; `opos-kiosk` reads it from that file alone, never from a unit or the caller's environment |
| `OPOS_DEV_SSH_PUBKEY` | build input | a public key file; the image then lets that key log in as `droidian` (a dev image) |

## RAM-boot only, and the misc brake

Every boot of this image is a RAM-boot from a PC: `fastboot boot boot.img`. Nothing is installed to the phone's
boot partitions, on purpose.

A gated, read-only-first experiment to find out whether the kernel can live in the phone's own storage is planned in [docs/duo-persistent-install.md](../../docs/duo-persistent-install.md); it has not been run.

- The image is RAM-booted from a PC; the phone's installed system is left unchanged. No tool here writes
  the boot image or changes the active slot. Making the image start without the PC would need a different,
  separately decided method.
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
service file sets `OPOS_SPAN=675,42`. A window under 1055 CSS px keeps the single-screen layout. Not yet measured on the phone.
A **dev checkpoint image** can also record the span in a separate drop-in: `OPOS_DEV_SPAN=1` together with `OPOS_DEV_SSH_PUBKEY` makes
`build-image.sh` write `/etc/systemd/system/opos-server.service.d/30-opos-dev-span.conf` (`[Service]`, `Environment=OPOS_SPAN=675,42`).
`verify-image.sh` checks the drop-in and builds the page for its value with the image's own python3. `OPOS_DEV_SPAN` without the dev key
stops the build before the output is touched, and an image built without it has no dev drop-in. Removing the drop-in restores the service
file's setting, which already enables the span. Not handled: a rotated window (the hinge would run across it), a phone folded with one panel dark, and two things that
still cross the hinge: the thin battery bar along the top edge and, while it plays, the zoom from a tile to its
app.

## Build and check

The image identity and release-folder procedure are in
[docs/duo-os-identity.md](../../docs/duo-os-identity.md). The version comes from
`pyproject.toml` at the source ref. `SOURCE_DATE_EPOCH` defaults to that ref's
commit timestamp; an explicit value must be valid nonnegative Unix seconds. No
builder wall-clock time is used in the identity manifest. These identity changes
have not been built into a real image or checked on a phone (**NOT_RUN**).

`build-image.sh` runs as root on a build machine its owner controls; it does not defend against another local user who can write to the output directory while it runs. It resolves OUT's parent once, refuses it unless owned by root or the invoking user (`SUDO_UID` when present) with no group or other write permission, and refuses any ancestor through `/` that is writable by others without the sticky bit. Staging stays inside that parent. Within this threat model, these checks close staging-path replacement; the owner must keep the directory under their control throughout the build.

On a Linux builder with Python 3.11+, `zstd`, `e2fsprogs` and `qemu-user-static`:

```sh
cd <release folder> && sha256sum -c SHA256SUMS
zstd -d --long=27 rootfs.img.zst -o base-rootfs.img        # must hash to the manifest's 9299b2c0…
sudo env SOURCE_DATE_EPOCH=<release-unix-seconds> sh packaging/duo-os/build-image.sh base-rootfs.img opos-os-rootfs.img HEAD
sudo sh packaging/duo-os/verify-image.sh opos-os-rootfs.img
```

`verify-image.sh` checks the unit wiring, that phoc is not started in shell mode, phoc's `-E` startup command and
the journal settings, runs the OPOS server from the image with the image's own aarch64 python3 under
qemu-user, starts a second one with the keyboard, the Network page and the first run all on to ask it for each page, and two
more, with the Inference page off (its two routes answer 404) and on (they answer 200).
All four servers listen on loopback ports it picks, and a port that already answers is a FAIL, so a stale or hostile listener
cannot stand in for the image's server; they get none of the caller's `OPOS_` variables, and are stopped by their own pid,
at the end or on INT, TERM or HUP, and waited for before unmounting. Unmount failure is verification failure, never PASS;
the verifier reports the attached mount point and backing file, which must be retained for recovery.
It is not a boot, display or battery test.
`tests/test_duo_kiosk.py` runs the kiosk script under `sh` with a fake `chromium` (locks, back-off, the no-GPU retry, the
give-up), `opos-session` with a stub phoc, and with `OPOS_TEST_SYSTEMD=1` as root the whole give-up chain on real systemd.

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
  building with `OPOS_NET_POLKIT=1`, and it grants only `opos-server.service` running as `droidian` (any other value but `0`, empty or unset stops the build, so a typo cannot build an image without it; `verify-image.sh`
  checks the rule). Without it the phone cannot join Wi-Fi: NetworkManager asks polkit for `wifi.scan`, `network-control` and `settings.modify.system`, and
  `droidian` has no session to answer an administrator prompt. See [docs/network.md](../../docs/network.md).
- **First run and time zone.** A fresh phone opens a skippable setup; Settings > Date and time changes the zone later. The
  server applies the zone; the kiosk exports it to Chromium on every start. See [docs/duo-firstrun.md](../../docs/duo-firstrun.md).
- **Acceptance.** `/usr/sbin/opos-selftest` on the phone, `pwsh -File deploy\duo-acceptance.ps1 -Host <phone> -Key <key>` from
  the PC, then the owner's checklist. See [docs/duo-acceptance.md](../../docs/duo-acceptance.md).

## Inference page

Settings > Inference opens `/monitor` (`src/opos/monitor.py`, `src/opos/monitor_web.py`, `src/opos/web_static/monitor*`), a read-only
page in two parts. **This Duo** says whether a model server on the phone's own loopback (`127.0.0.1:8080`) lists a model (it asks
`GET /models`; no prompt is sent) and shows the battery percent, health and source. Health is shown only when the battery's own gauge
reports it (sysfs full-charge over design capacity); the page cannot label an estimate, so a health that is estimated from the charge
counter, assumed, or typed in (`OPOS_BATTERY_HEALTH`) reads "Not reported". **AGIW monitor** is optional: what an AGIW Inference Monitor on a computer you own
says about its machines, fetched read-only. `opos-server.service` sets `OPOS_MONITOR=1`; unset (or anything but `1`) neither `/monitor`
nor `/api/monitor` answers. `/etc/opos/monitor.env` is optional and absent from the image; `/etc/opos/monitor.env.example` shows it, and
with no file no AGIW monitor is fetched (the page still asks the phone's own loopback model port):

| Variable | Meaning |
|---|---|
| `OPOS_AGIW_URL` | `http(s)://host[:port]` of the monitor; every address it resolves to must be loopback or on a private network; unset: NOT CONFIGURED |
| `OPOS_AGIW_HOST` | the `Host` header to send (a monitor behind a tunnel answers only for its own address) |
| `OPOS_LOCAL_URL` | NOT an Inference page setting, and not in the example file: it is the Helper's (`src/opos/assist.py`). The page's check of the phone's own model server follows it ONLY if you already set it for the Helper (http on `127.0.0.1` or `localhost`); otherwise it asks `http://127.0.0.1:8080/v1`. Setting it changes the Helper too, even to that default: the Helper then uses the address as set, stops checking `/models` for a known model first, and drops its alternate model ids |

Only loopback egress works on the hardened server units (`deploy/opos-server.service` and the postmarketOS unit set
`IPAddressDeny=any` and `IPAddressAllow=localhost`): there a LAN address reads TIMED OUT or UNREACHABLE, and a reverse tunnel to the
phone's loopback is the way. This image's `opos-server.service` has no such filter, so a private-network address works here too, but
over plain http the answer is not authenticated. The tunnel, from the computer that runs the monitor on its `127.0.0.1:8767`, over
the dev ssh key:

```sh
ssh -N -R 127.0.0.1:18767:127.0.0.1:8767 droidian@<phone>   # the phone's 127.0.0.1:18767 is now the computer's monitor
# on the phone, once:
sudo cp /etc/opos/monitor.env.example /etc/opos/monitor.env   # then remove the # from the OPOS_AGIW_URL and OPOS_AGIW_HOST lines
sudo systemctl restart opos-server                            # and tap Settings > Inference
```

Written from the sources and tried against fakes and loopback servers only; not seen on the phone.

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
  (`/run/systemd/timesync/synchronized`) or the time is set below. Readers ignore the guess once that boot's
  synchronized file exists, but a clock at or below the image floor is never OK. A clean shutdown keeps the guess for the next boot
  (`/var/lib/opos/clock-guess`), because `timekeeper` stores whatever the clock was.
- The OPOS server reads those files. While the clock is a guess, or before 2026, `/api/state` has
  `battery.clock_ok: false`, `clock` reads `--:--`, goals and `calls_until` are not computed against it, the governor
  gets no goal hours, and the goal note reads "Clock not set". The demo and the public demo do not change. The page
  draws the same fact: the status bar and the lock screen show `--:--`, the lock screen and the Home dateline say "Time not
  set", and the status bar's network slot says "Clock not set", until the clock is real (the page also treats a browser
  year before 2026 as unset, so a 1970 boot is never drawn as a time).

Set the time from the PC, dev images only (built with `OPOS_DEV_SSH_PUBKEY`; the units are in every image, enabled
only in those). The `droidian` user has no passwordless sudo and none is added by default (the dev-only `OPOS_DEV_SUDO=1` adds it, see
[Dev options: passwordless sudo](#dev-options-passwordless-sudo)). Instead a root path unit watches
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

The first command checks the four files against their SHA-256 (and that the rootfs is a raw ext4 image, not a `.zst` or sparse file) and prints the steps without touching the phone.
With `-Yes` it checks product, slot a, unlocked and battery; arms the brake; formats userdata; RAM-boots TWRP;
pushes the rootfs and compares its hash on the phone; returns to the bootloader; re-arms the brake; RAM-boots
the port's kernel once; then finds the phone on the USB network and, only if `-Key` is given, reads its state
and logs over ssh. The first failure stops it and nothing is retried; the one exception is a failed brake write, which is tried once more without another erase before it stops and says to keep the phone in Boot Select (an interrupt with Ctrl+C after the brake erase says the same). The safety rules are written at the top
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

Every build requires `OPOS_OWNER_SSH_PUBKEY=<owner.pub>`, independently of dev options. The builder validates each public key, replaces inherited
`~/.ssh/authorized_keys` with the supplied owner key and optional developer key, repairs the home directory to 0755, and installs `.ssh` at 0700 and
`authorized_keys` at 0600 with droidian ownership. The builder and verifier check the path through the home directory, the installed sshd and enabled
`ssh.service`, including an effective `AuthorizedKeysFile` that uses this key and traversable paths through the home directory. No package or service is added. The explicit `OPOS_NO_SSH=1` choice means no SSH access at all: it removes the
login keys, disables and masks SSH services and sockets, and records `/etc/opos/no-ssh`; it cannot be combined with a key. The older `OPOS_ALLOW_NO_SSH=1` spelling has the same meaning.

Every build copies BASE into one exclusively created file in a private 0700 staging directory next to OUT and hashes that staged file.
`OPOS_RELEASE=1` checks that hash against `packaging/duo-os/release-bases.sha256` beside the builder and inspects the staged file for
`/etc/opos/image.json` or the OPOS release layout, refusing either even if the hash is listed. The list cannot be replaced by an environment
variable or argument; tests put a fixture list in a fixture checkout. The seeded community-base hash is reported, not locally verified.
The builder modifies only the staged image, and a release must pass `verify-image.sh` on that same image before an atomic rename publishes OUT.
Existing OUT paths, including symlinks and hard links, and directories the builder cannot write are refused. The builder checks that no loop attachment remains before publishing;
failures remove staging only when its backing file is detached, otherwise they report and retain that file and exit nonzero.
Checksum reporting happens before publication. The exclusive rename is the commit point: INT, TERM and HUP are ignored during publication and cleanup,
and a successful rename returns success even if removing the empty staging directory fails. No fallible reporting follows the rename.
An uncatchable termination can leave OUT after that commit point without a successful process exit.
Every image records `release` or `dev` in `/etc/opos/build-mode`; `opos-selftest` reports the mode, and release verification refuses dev or unmarked images.
Dev builds still accept unpinned or reused bases and always record `dev`, even without optional dev features. They print verification `NOT_RUN`
unless `OPOS_VERIFY_IMAGE=1` is set. The existing policy checks remain as defence in depth.

`OPOS_DEV_SSH_PUBKEY=<file.pub>` adds a separate developer key and enables the clock drop and Chromium DevTools on loopback. Leave it unset for a
carried or release image; release mode rejects every nonempty value, including `0`. The owner recovery key alone enables none of those dev features. Keys grant a shell as droidian over every network where sshd
can be reached; SSH accepts no passwords. Removing a key from `~/.ssh/authorized_keys` revokes it. Rebuilding replaces the inherited key list.

## Debug port and USB shell

The image build binds the container debug port to `127.0.0.1` and installs a filter that drops new inbound packets on `wlan0`. A passwordless USB root shell (telnetd started as a shell) is closed. The legacy `OPOS_DEV_USB_SHELL` input cannot preserve a root shell; `/etc/opos/usb-shell.conf` always records `enabled=0`. The kiosk DevTools port, when a dev image opens one, is `127.0.0.1` only. None of this has been checked on a phone (**NOT_RUN**).

Both `OPOS_DEV_SPAN` and `OPOS_DEV_SUDO` default to off (unset, empty or `0`). Only `build-image.sh` writes their files, with an explicit `1` and a dev
ssh key at image build time. Runtime environment variables do not enable them. Rebuilding without the knobs removes their files even from an older dev
base. The files themselves record the choices; `opos-selftest` reports `dev.span` and `dev.sudo`, and `verify-image.sh` checks their contents, modes and
owners. Give the verifier the build's `OPOS_DEV_SPAN=0/1` and `OPOS_DEV_SUDO=0/1` to require absence or presence as well.
Release image builds leave both knobs unset or `0`. The OTA release builder and updater reject both the dev-span drop-in and the sudoers path; neither
knob is read by that release path.

## Dev options: passwordless sudo

The ordinary Duo interface displays a fixed `DEV: passwordless sudo for droidian` warning whenever `/etc/sudoers.d/90-opos-dev` is installed. The warning comes from the served pane stylesheet; HTML page bytes stay unchanged. Phone rendering remains **NOT_RUN**.

`OPOS_DEV_SUDO=1 OPOS_DEV_SSH_PUBKEY=<file.pub> sh packaging/duo-os/build-image.sh …` gives the dev login user, `droidian`, sudo without a password. In the
base, `droidian` is in the `sudo` group but sudo asks for a password that nobody has, and the root login is refused, so a developer who can ssh in cannot
install a file, restart a service or read a root-only log. The build writes `/etc/sudoers.d/90-opos-dev`, mode 0440, owner root:root, holding exactly the one line

```
droidian ALL=(ALL) NOPASSWD:ALL
```

checks it with `visudo -cf` when the builder has `visudo` (the build stops if `visudo` rejects it), and prints `DEV: passwordless sudo for droidian`. The knob works
only together with `OPOS_DEV_SSH_PUBKEY`: `OPOS_DEV_SUDO=1` without it, or any value other than `1`, `0`, empty or unset, stops the build before the output is touched
(exit 2), exactly as `OPOS_DEV_SPAN` does. Without the variable no file is written, and a file left by an earlier dev image in the base is removed.

**An image built with `OPOS_DEV_SUDO=1` must never be carried or shared.** Everything that runs as `droidian` is root on it: the OPOS server, the kiosk Chromium
and every ssh session, so the dev ssh key becomes a root key, and the over-the-air design ([docs/ota-update.md](../../docs/ota-update.md)), which assumes an
unprivileged `droidian`, protects nothing there. The acceptance suite warns (`dev.sudo`, with the other `dev.*` warnings) and `verify-image.sh` prints a `NOTE`.

`verify-image.sh` checks the file when it is there (a plain 0440 root:root file with exactly that line, in an image that has the dev ssh key, `visudo -cf` accepts
it, and no other sudoers line grants `NOPASSWD`) and that no sudoers line grants `NOPASSWD` when it is not. The image cannot say whether the build meant to
write the file, so give the verifier the variable the build had to hold the image to it both ways: `OPOS_DEV_SUDO=1` requires the file and `OPOS_DEV_SUDO=0`
requires its absence (unset requires nothing; under `sudo`, `sudo env OPOS_DEV_SUDO=1 OPOS_NET_POLKIT=1 sh packaging/duo-os/verify-image.sh …`; `OPOS_NET_POLKIT`
is held the same way: `1` requires the polkit rule, `0` requires its absence, and a rule that is there must be the very bytes of `50-opos-network.rules`).

Remove it from a running phone with `sudo rm /etc/sudoers.d/90-opos-dev`; the next sudo asks for a password again. Check it from the PC with
`ssh droidian@<phone> sudo -n true`.

## Removed on purpose: the USB root shell and the password login

Two doors that the community port leaves open need no key, and `build-image.sh` closes both in **every image** (no knob; the block is `usb hardening`):

- **A root shell on the USB cable.** The port's `/usr/local/sbin/sfduo-usb-gadget.sh`, which `sfduo-usb.service` runs at every boot, ends by starting
  busybox `telnetd` with `/bin/bash` as its login program on the USB link's address (port 23): a root shell with no login for anything on the cable.
  The build replaces that one line with a comment saying so. The rest of the script (the RNDIS gadget, the static address, the trailing `exit 0`)
  and the file's mode and owner stay as the port has them; the script is rewritten in place, and the build stops if the result does not parse.
- **A password from the whole USB subnet.** The port's `/etc/ssh/sshd_config.d/10-item-password-usb-only.conf` turned `PasswordAuthentication` off and
  then on again in a `Match Address` block for the USB link's /24 (`droidian` has a password, a four-digit PIN). sshd matches the client's *source
  address*, not the interface, so any client with an address in that /24 could try the PIN on any interface, Wi-Fi included once one is joined. The build rewrites that
  drop-in (same name, mode and owner) to say only `PasswordAuthentication no` and `KbdInteractiveAuthentication no`. sshd takes keys only, from every address.

ssh with a key (`OPOS_DEV_SSH_PUBKEY`, and `OPOS_DEV_SUDO` for root, see above) replaces both. `verify-image.sh` fails an image whose gadget script has a
non-comment `telnetd` line, whose other scripts, configs or units start `telnetd`, or whose sshd files set `PasswordAuthentication`,
`KbdInteractiveAuthentication` or `ChallengeResponseAuthentication` to `yes` anywhere (a `Match` block that takes no password is fine); a package upgrade of the
port that puts either file back is therefore reported. On the phone `opos-selftest` `ssh.auth` reads `key-only everywhere` for the result, and
`ss -ltn` as `droidian` must list nothing on port 23. The builder and verifier also run the host `sshd -T` on private copies of the image configuration, with Include paths confined to the image. This parses effective settings without starting a listener. Phone checks remain **NOT_RUN**.

**Separate exposure hardening.** The build still runs `close-exposure.sh`: it binds the container debug port to loopback, drops new inbound packets
on `wlan0`, and closes other root telnet units unconditionally. An inherited `OPOS_DEV_USB_SHELL=1` cannot keep a shell. These are source and fixture checks; the phone checks remain **NOT_RUN**.

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
- **A power cycle that reaches stock Android destroys the OPOS install.** The RAM-boot does not survive a restart.
  Read-only observations from the PC session on November 11, 2026 record that any stock Android boot on OPOS userdata
  applies metadata encryption in place, making the rootfs unreadable to Linux. To get the image back, run the install chain
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
`/var/lib/opos-update/inbox/` (or `/usr/libexec/opos-updater/opos-updater rollback` as root) returns to the previous release; the inbox
request is honoured once per applied update. OPOS also serves an update page at `http://127.0.0.1:8765/update` on the phone (check,
download, rollback); the kiosk reaches it by touch through Settings, About, Updates. With no update source set (`OPOS_UPDATE_URL`) the
page only shows the running version, the last result and the rollback button; the USB push needs no source.

Limits: nothing here has run on the phone, so expect to find problems at the first real push. A serial that failed on a phone is
never accepted again there (the fix goes out under a new serial). There is no way to revoke a release key over the air; a lost
or stolen key means a new image. A `boot`-scope bundle is staged and applied at the next boot, and rolls back after two failed
boots.

## Restore to Android

Nothing was written to boot, vbmeta, dtbo or recovery, and no slot was changed, so the stock Android
(2022.902.48) is still installed and is what the phone starts when the OS image is not RAM-booted.
Read-only observations from the PC session on November 11, 2026 record that any stock Android boot on OPOS userdata
applies metadata encryption in place and makes the OPOS rootfs unreadable to Linux. Returning to Android this way
destroys the OPOS install; it is not a harmless exit.
These restore steps assume the RAM-boot-only state above. If a slot holds persistent OPOS, do not select factory reset;
the separate persistent-install plan describes the unproven restore dependencies.

1. Power cycle the phone (Power held about 20 seconds). Android may start from the untouched boot partition and
   destroy the OPOS install as described above. If the brake is armed, the phone may instead reach Boot Select;
   its behavior is not guaranteed. Do not treat an Android start as preserving the OPOS image or its data.
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
