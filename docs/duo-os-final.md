<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# The Duo OS as it stands

Status, November 7, 2026: **the pieces below are merged and tested together on a computer; none of the pieces added since the
owner's first boot has been run on the phone.** The owner reported on November 7, 2026 that the OPOS-only image works on the
Surface Duo. The on-screen keyboard, the Network page, the first run, the time zone, the honest clock sentence and the acceptance
suite came after that. Everything in "What is not known" is open until someone runs the live checks at the end of this page.

## What it is

A Linux OS whose only interface is OPOS, on a Surface Duo 1. The community Droidian/Halium port supplies the kernel and the vendor
layer (panels, touch, GPU, Wi-Fi, audio, modem). `phoc` (wlroots, hwcomposer backend, **not** in shell mode) runs one Chromium
kiosk on the OPOS server (`python3 -m opos.web --live-battery`, user `droidian`, from `/opt/opos/src`). The window spans both
panels: 2784 x 1800 device pixels at scale 2, which is 1392 x 900 CSS pixels with a 42 CSS pixel hinge. Touch only.
[packaging/duo-os/README.md](../packaging/duo-os/README.md) has the pieces, the switches, the build and the install.

## What it does now

- **OPOS on the phone's own battery.** The rules and the governor run on the two bq27742 fuel gauges in sysfs
  (`--live-battery`); the lock screen, the daily goal, the essentials and the helper are OPOS's own.
- **Typing without a keyboard.** An in-page keyboard (`OPOS_OSK=1`, [packaging/duo-os/OSK.md](../packaging/duo-os/OSK.md)): letters
  split one half on each panel so nothing sits on the hinge, symbols, a phone pad, a digits pad and a 24-hour time pad. It is
  served to the home page, the Network page, and the first-run and time zone pages. The port's own keyboard is an opt-in
  (`OPOS_OSK=stevia`), written from sources and never started.
- **Radios and Wi-Fi** ([network.md](network.md)). Settings > Network opens `/network`: the state of Wi-Fi, Bluetooth, modem, SIM,
  mobile data, airplane mode and the USB network, and scan, join (password on standard input, never in a command line), connect
  to a saved network, forget, disconnect, and Wi-Fi on or off, all through NetworkManager as the unprivileged user.
- **First run, time zone and an honest clock** ([duo-firstrun.md](duo-firstrun.md)). A fresh phone opens a skippable setup
  (zone, daily goal, helper, a hand-off to the Network page). The owner's zone is kept in `~/.config/opos/timezone` and applied
  without root; the kiosk exports it to Chromium. While the clock reads before 2026 the setup says so in plain words and the
  zone chooser does not show a time it cannot know.
- **Evidence** ([duo-acceptance.md](duo-acceptance.md)). `opos-selftest` on the phone and `deploy/duo-acceptance.ps1` from the PC
  run read-only checks, keep an evidence folder with hashes, and expect each piece above to be present: an image that ships the
  keyboard, the Network page or the first run but starts the server without its switch FAILs. The boot dump
  `/var/log/opos-boot-diag.txt` and a persistent journal stay on the phone for a dark boot.

## The switches

Set by the image's units; unset means the page and every route are byte for byte what they were.

| Switch | Image default | Effect |
|---|---|---|
| `OPOS_OSK` | `1` (`opos-server.service`; `/etc/default/opos-osk` overrides) | `1` or `web`: the in-page keyboard on every full page; `stevia`: the port's keyboard instead; `off` or anything else: none |
| `OPOS_OSK_HINGE` | unset (42) | CSS pixels between the two halves of a split keyboard when the page does not say; `0` never splits |
| `OPOS_NETWORK` | `1` (drop-in `opos-network.conf`) | the Settings link to `/network` and the actions that change things; without it the page only reads |
| `OPOS_NET_TTY` | unset | `1` hands the Wi-Fi password to `nmcli` through a pseudo-terminal, if a pipe is not accepted on the phone |
| `OPOS_NET_POLKIT` | unset (a build input) | `1` at build time installs the polkit rule that lets `droidian` change Wi-Fi without being asked |
| `OPOS_FIRSTRUN` | `1` (`opos-server.service`) | the first-run setup, `/timezone`, and applying the saved zone at start |
| `OPOS_KIOSK_RESTART` | unset (on) | `0` stops the server restarting Chromium after a zone change |
| `OPOS_CONFIG_DIR` | unset (`~/.config/opos`) | where the config, first-run flag and saved zone live; the server and `opos-kiosk` both read it |
| `OPOS_SPAN` | unset (a commented line in `opos-server.service`) | `675,42`: pane and hinge widths in CSS pixels for the window that covers both panels, so the page lays out for two screens; `off` disables it; the same as `--span`. Not yet measured on the phone |
| `OPOS_KIOSK_DEBUG_PORT` | unset; a dev image writes it into `/etc/default/opos-kiosk` | the kiosk Chromium's DevTools port on `127.0.0.1`; `opos-kiosk` reads it from that file alone, never from a unit or the caller's environment |
| `OPOS_KIOSK_DEFAULTS` | unset (`/etc/default/opos-kiosk`) | which file `opos-kiosk` reads the DevTools port from; a hook for tests |
| `OPOS_OSK_BIN`, `OPOS_OSK_WAIT`, `OPOS_OSK_GSETTINGS` | unset | only for `opos-osk` (the opt-in Wayland keyboard): the binary, seconds to wait for phoc's socket (120), and `0` to leave gsettings alone |
| `OPOS_BATTERY`, `OPOS_ON_PHONE` | `live`, `1` | the sysfs battery source and the phone-only behaviour |

## What is still simulated

- **Calls and texts are simulated.** The base has ModemManager (through ofono2mm) and `mmcli` can read the modem and SIM, so the
  Network page shows the real radio's state, but `src/opos/mm_modem.py` is not wired to the phone page. Nothing is dialled, sent
  or received; the page says so.

## What is not known

- **Nothing is measured about battery life or stability.** No drain curve, heat, standby or multi-day run exists for this image.
  One finding to act on: the sysfs battery path never reads a temperature, so `temp_c` stays at OPOS's default 30.0 and the heat
  rules do not see the phone's real heat. `battery.temp` in the selftest WARNs on exactly that.
- **RAM-boot only.** The bootloader refuses boot-partition writes, so every boot comes from the PC. Nothing here makes it permanent.
- **Never seen on the phone:** the keyboard under a finger and at the real 1392 x 900 window; the Network page against the phone's
  real NetworkManager, ModemManager and polkit (the password handshake is the main unknown, with `OPOS_NET_TTY=1` as the fallback);
  that Chromium on the phone honours `TZ`; that killing only Chromium after a zone change leaves phoc and the kiosk loop to bring
  the screen back; that the clock corrects itself once online; every output format the selftest reads (`wlr-randr`, `nmcli -t`,
  `journalctl`, `timedatectl`).
- **The home page is larger on the Duo OS.** `web.page()` stays within the 100 KiB cap that `tests/test_uxp30_budget.py` holds it
  to. With all three switches on the server also injects a Settings link for each of the Network page and the time zone, and the
  keyboard's two tags: about 590 bytes, so the page the Duo is sent is about 102,900 bytes, roughly 500 bytes over 102,400.
  `tests/test_duo_final.py` pins the injection at 650 bytes. Whether that is acceptable, or one link should go, is the owner's call.
- **Over-the-air updates are not here** ([ota-update.md](ota-update.md) is a design only); the selftest SKIPs `ota.updater`. The clock
  floor, measured drain, the hinge layout (`OPOS_SPAN`, off until checked on the phone), the kiosk units with their restart loop and
  debug port, and the install docs are merged with the pieces above and tested with them on a computer, not yet on the phone. The
  first run's clock sentence still tests only for a year before 2026 (`clockcheck.py`); the server's own `clock_ok` also treats the
  image's floor as a guess, so on a first boot the setup does not yet say so.
- Rotation, fold-to-sleep (nothing blanks the panels today) and the battery readout against Android's own are in the
  owner's part of [duo-acceptance.md](duo-acceptance.md).

## What was checked, and how

On a computer only: unit tests for each piece (fake `nmcli`, `mmcli`, `rfkill`, `ip` and `bluetoothctl` scripts; fake `systemctl`,
`journalctl` and a fake phone tree for the selftest; the real server over HTTP), the keyboard's logic under Node, the PC script
under PowerShell against a stand-in `ssh`, and `tests/test_duo_final.py`, which starts the real server with all three switches on
and checks that each page carries what another track adds, that the selftest accepts that server, that the image probe in
`verify-image.sh` passes against it, and that the units, scripts, inventory and docs name the same things. The keyboard was also
driven with a mouse in a desktop browser; the pages were looked at in a desktop browser at 1392 x 900. `verify-image.sh` additionally
runs the image's own Python under qemu-user on a builder; that run needs root, a loop mount and the base image, and has not been
done for this merge.

## Live checks, in order

For whoever holds the phone. Read-only unless a step says otherwise; replace `<phone>` and `<key>`. Keep the output: names,
addresses and passwords appear in some of it.

1. **Build and verify** (builder, root): `sudo sh packaging/duo-os/build-image.sh base-rootfs.img opos-os-rootfs.img HEAD`, with
   `OPOS_DEV_SSH_PUBKEY=<file.pub>` for a dev image you can ssh into (and `OPOS_NET_POLKIT=1` only if step 5 shows NetworkManager
   refusing); then `sudo sh packaging/duo-os/verify-image.sh opos-os-rootfs.img`. Expect `VERIFY: PASS` and, among the lines,
   "the keyboard, the Network page and the first run answer together in one server from the image".
2. **Boot** from the PC as before, wait five minutes (the boot dump is due 90 s after multi-user.target).
3. **The suite, from the PC:** `pwsh -File deploy\duo-acceptance.ps1 -Host <phone> -Key <key> -OutDir <folder>`; on the phone,
   `/usr/sbin/opos-selftest ; echo rc=$?`. Expect no FAIL. Expected WARNs on a first boot of a dev image: `battery.temp`, `clock.year`
   (1970, or the image's floor until network time or a set time), `clock.timezone` (UTC), `dev.*`, possibly `svc.system`. Expected SKIPs: `ota.updater`, and `clock.kiosk-tz` until a zone is chosen; `kiosk.restarts` reports the exits `opos-kiosk` logged this boot.
   Expected PASS: `osk.keyboard`, `net.page`, `net.permissions` (WARN means NetworkManager will refuse; see step 5),
   `firstrun.flag`, `firstrun.routes` (it says first run PENDING). Any FAIL or surprise in a format is a finding: send the report.
   `page.png` exists only if the kiosk has a DevTools port, which comes from another branch.
4. **Formats the suite assumes** (ssh as `droidian`; compare each with the matching selftest line and report differences):
   `systemctl show --property=LoadState,ActiveState,SubState,Result opos.service`; `pgrep -af phoc` and `pgrep -af 'composer@2.4'`
   (phoc has `-E /usr/libexec/opos/opos-kiosk` and no `-S`); `tr '\0' '\n' < /proc/$(pgrep -f -- --kiosk | head -1)/environ | grep -E '^(WAYLAND_DISPLAY|XDG_RUNTIME_DIR)='`;
   `XDG_RUNTIME_DIR=/run/user/32011 WAYLAND_DISPLAY=wayland-0 wlr-randr` (HWCOMPOSER-1, Enabled: yes, 2784x1800 px, Scale 2);
   `cat /sys/class/backlight/panel*-backlight/bl_power /sys/class/backlight/panel*-backlight/actual_brightness`;
   `for s in /sys/class/power_supply/*; do echo $s $(cat $s/type) $(cat $s/capacity 2>&1) $(cat $s/status 2>&1) $(cat $s/temp 2>&1); done`;
   `timedatectl show -p Timezone -p NTPSynchronized`; `nmcli -t -f DEVICE,TYPE,STATE device`; `ip -4 -o addr show dev usb0`;
   `ls /var/log/journal/*/`; `head -3 /var/log/opos-boot-diag.txt`.
5. **Network layer, before touching the screen:** `export PYTHONPATH=/opt/opos/src; cd /opt/opos/src; id` (groups include `netdev`);
   `systemctl show opos-server -p Environment` (shows `OPOS_NETWORK=1`, `OPOS_FIRSTRUN=1`, `OPOS_OSK=1`);
   `python3 -m opos.net probe | tee /tmp/net-probe.txt` (every block `rc=0`; an `mmcli` "couldn't find modem" is a finding, not an error);
   `nmcli -t general permissions | grep -E 'enable-disable-wifi|network-control|settings.modify.system|wifi.scan'` (four lines ending `:yes`;
   `:auth` or `:no` means rebuild with `OPOS_NET_POLKIT=1`, or as root install `packaging/duo-os/50-opos-network.rules` into
   `/etc/polkit-1/rules.d/`); `python3 -m opos.net status | python3 -c 'import json,sys; s=json.load(sys.stdin); [print(s[k]["summary"]) for k in ("wifi","bluetooth","modem","sim","data","airplane","usb")]; print(s["control"]); print(s["notes"])'`
   (seven plain lines that agree with `nmcli`, `mmcli -m any`, `rfkill list`, `ip -br addr`).
6. **First run, on the glass.** The kiosk opens "Set up this phone": text on the left panel, controls on the right, nothing in the
   42 px hinge strip, every control reachable by touch, the 1970 sentence on the left. Tap Start, Europe, Berlin, Next, adjust the
   goal with the buttons, Next, the helper choice, Next, Wi-Fi, Next, Finish. **Expect the screen to go dark for about 4 to 8 seconds**
   (Chromium restarts because the zone changed) and come back on the OPOS home. If it stays dark, that is the main risk: over ssh
   `pgrep -u droidian -af 'opos-kiosk|chromium'` and `journalctl -t phoc -b | tail`; set `OPOS_KIOSK_RESTART=0` to stop the restart.
   Probes first, to see the redirect rule: `python3 -c "import urllib.request as u;print((r:=u.urlopen('http://127.0.0.1:8765/')).status,r.url);print((r:=u.urlopen(u.Request('http://127.0.0.1:8765/',headers={'Accept':'text/html'}))).status,r.url)"`
   prints `200 .../` then `200 .../firstrun`.
7. **The zone took:** `ls ~/.config/opos` (`config.json firstrun.json prefs.json timezone`); `cat ~/.config/opos/timezone`;
   `pid=$(pgrep -u droidian -f 'user-data-dir=.*opos-kiosk' | head -1); tr '\0' '\n' < /proc/$pid/environ | grep '^TZ='` (`TZ=Europe/Berlin`);
   the status-bar time equals `TZ=Europe/Berlin date +%H:%M`; `/usr/sbin/opos-selftest --only clock --only firstrun` (`clock.kiosk-tz` PASS).
8. **Keyboard, on the glass.** Phone: tap the number field, a pad appears at the outer edge of that pane with the field above it;
   `5 5 5` types 555; Enter moves to the message field and letters appear: one half on each pane, the hinge empty between, first letter
   capital, Hide at the top left, Join at the top right; hold Delete and it repeats; `123`, `#+=` and `ABC` work; Join makes one block,
   Split restores it; type a message and tap Send. Helper: the keyboard does not appear on opening, tapping Ask shows it, Enter asks.
   Shade: tap Last until, tap `1 7 3 0`, the goal reads 17:30. Tapping the background or Hide removes it; a key from a USB keyboard
   hides it and a touch brings it back. Watch for a key under the hinge, a field hidden behind the keyboard, missed taps, doubled
   letters, lag. If `innerWidth` is not 1392 (DevTools port, if the image has one: `JSON.stringify({w:innerWidth,h:innerHeight,plan:oposOsk.st.plan})`),
   fix `OPOS_SPAN` and `OPOS_OSK_HINGE`.
9. **Wi-Fi, on the glass** (uses the keyboard): Settings > Network. Status on the left panel, networks on the right, nothing under the
   hinge. Tap a network; the password field appears and the keyboard comes up. A wrong password gives "The password was not accepted.
   Check it and try again. Nothing was saved." (for a network that was already saved, it says the old entry was removed). The right one
   connects. Meanwhile over ssh, the password must never be in a command line:
   `for i in $(seq 1 200); do ps -eo args | grep '[n]mcli'; sleep 0.2; done | sort -u` shows `nmcli ... --ask device wifi connect <SSID> ifname wlan0` and no password.
   If a correct password is rejected, retry with `OPOS_NET_TTY=1 python3 -m opos.net join '<SSID>'` and, if that works, add
   `Environment=OPOS_NET_TTY=1` to `packaging/duo-os/opos-network.conf`; report which. Then `nmcli -t -f DEVICE,STATE,CONNECTION device status`,
   `ip -br addr show wlan0`, `python3 -c 'import urllib.request as u;print(u.urlopen("http://example.com",timeout=15).status)'` (200),
   `python3 -m opos.net forget '<SSID>'`, `python3 -m opos.net wifi off` then `on`. With Wi-Fi connected the data line says "off while
   Wi-Fi is connected". Home returns to the main page.
10. **Time zone later and the first-run edges:** Settings > Date and time > Time zone, pick Asia/Tokyo, Save: "Saved. The screen restarts
    in a few seconds", a dark gap, home in Tokyo time, `cat ~/.config/opos/timezone`. Skip path: `rm ~/.config/opos/firstrun.json;
    pkill -TERM -u droidian -f 'user-data-dir=.*opos-kiosk'`; within about 5 s the setup is back; Skip setup returns home with
    `"skipped": true`. Never blocks home: `rm ~/.config/opos/firstrun.json; chmod 500 ~/.config/opos`, restart Chromium as above, the
    home appears directly; then `chmod 700 ~/.config/opos`.
11. **Reboot** from the PC; after 90 s `sed -n '/== radios/,/== power supplies/p' /var/log/opos-boot-diag.txt` (six `radios:` lines, no
    SSID, address or IMEI). The setup must not come back, the zone file is unchanged, the status bar keeps the zone, and
    `/usr/sbin/opos-selftest` has no FAIL. Note whether a saved Wi-Fi network survives the reboot (NetworkManager keeps it under
    `/etc/NetworkManager/system-connections` on the rootfs file, which lives on userdata).
12. **Optional, the port's keyboard:** the recipe in [packaging/duo-os/OSK.md](../packaging/duo-os/OSK.md) (`OPOS_OSK=stevia`).
13. **The owner's part:** the ten checks of part 2 of [duo-acceptance.md](duo-acceptance.md), written down with dates.
