<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# The Duo OS as it stands

Status, November 7, 2026: **the pieces below are merged and tested together on a computer; none of the pieces added since the
owner's first boot has been run on the phone.** The owner reported on November 7, 2026 that the OPOS-only image works on the
Surface Duo. The on-screen keyboard, the Network page, the first run, the time zone, the honest clock sentence and the acceptance
suite came after that. Everything in "What is not known" is open until someone runs the live checks at the end of this page.

## What it is

The image identity implementation is documented in [duo-os-identity.md](duo-os-identity.md):
Old Phone OS, derived from Droidian, with the upstream identity preserved, a
hashed installed-file manifest, `opos-version` and About on the update page.
A real image build and every phone check for this change remain **NOT_RUN**.

An experimental OPOS interface on a Droidian Linux rootfs for the Surface Duo 1. Tasks, calls and texts are simulated. The community Droidian/Halium port supplies the kernel and the vendor
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
| `OPOS_NET_POLKIT` | unset (a build input; the rule is not installed) | `1` at build time installs the polkit rule that lets `opos-server.service`, running as `droidian`, use the four NetworkManager actions (any value but `1`, `0`, empty or unset stops the build; `verify-image.sh` checks the rule, and with `OPOS_NET_POLKIT` in its environment requires or forbids it). Without it joining Wi-Fi is refused on this image |
| `OPOS_FIRSTRUN` | `1` (`opos-server.service`) | the first-run setup, `/timezone`, and applying the saved zone at start |
| `OPOS_MONITOR` | `1` (`opos-server.service`) | the Inference page: the Settings link to `/monitor`, and the routes `/monitor` and `/api/monitor`; `/monitor` and `/api/monitor` answer 404 without it |
| `OPOS_VOLUME_KEYS` | `1` (`opos-server.service`) | the authenticated state API reports simulated volume from the key reader; `/volume.js` makes the Settings volume read-only and labels it and the top-bar meter simulated. Unset: no script or API field, and the existing practice slider and page bytes stay unchanged. No media, ringer, call or alarm volume changes; see [VOLUME.md](../packaging/duo-os/VOLUME.md) |
| `OPOS_AGIW_URL`, `OPOS_AGIW_HOST` | unset (optional `/etc/opos/monitor.env`; the image ships only `monitor.env.example`) | where the Inference page looks for an AGIW monitor on a computer you own (loopback or private network; unset: no monitor is fetched), and the `Host` header to send it; see the Inference page section of the image README |
| `OPOS_LOCAL_URL` | unset (the image does not set it and `monitor.env.example` does not list it) | the Helper's setting, not an Inference page setting: the page's check of the phone's own model server follows it only if you already set it for the Helper, and setting it changes the Helper too (it stops checking `/models` for a known model and drops its alternates); see the Inference page section of the image README |
| `OPOS_KIOSK_RESTART` | unset (on) | `0` stops the server restarting Chromium after a zone change |
| `OPOS_CONFIG_DIR` | unset (`~/.config/opos`) | where the config, first-run flag and saved zone live; the server and `opos-kiosk` both read it |
| `OPOS_SPAN` | `675,42` (`opos-server.service`) | pane and hinge widths in CSS pixels for the window that covers both panels, so the page lays out for two screens; `off` disables it; the same as `--span`. Not yet measured on the phone |
| `OPOS_PANES` | `1` (`opos-server.service`; it paints only while `OPOS_SPAN` is on) | the black and white pane surfaces and the Hand setting on the window that covers both panels (`/panes.css`, `/panes.js`, [jaryo-panes.md](jaryo-panes.md)); `1,pure` is the strict `#000`/`#fff` pair, `invert` swaps the colors for the left hand without mirroring; unset or `off`: no panes assets or tags and `page()` is unchanged (the Network, first-run and time zone pages carry the Jaryo conformance fixes either way). Not yet seen on the phone |
| `OPOS_RELEASE` | `0` (build and verification input) | `1` refuses development inputs, requires a pinned community base without previous OPOS identity or release layout, records release mode, and requires verification before publication |
| `OPOS_VERIFY_IMAGE` | `0` (dev build input) | `1` verifies a dev staging image before publication; release builds always verify |
| `OPOS_NO_SSH`, `OPOS_ALLOW_NO_SSH` | `0` (build input; the latter is a compatibility alias) | `1` explicitly chooses no SSH access: no authorized keys, SSH services and sockets disabled and masked; otherwise an owner recovery key is required |
| `OPOS_DEV_SPAN` | unset; a build input of `build-image.sh`, dev images only | `1`, with `OPOS_DEV_SSH_PUBKEY` (the build refuses it alone): writes the drop-in `opos-server.service.d/30-opos-dev-span.conf`, which sets `OPOS_SPAN=675,42` recording the dev choice separately from the normal service span; no dev drop-in without it. `opos-selftest` WARNs `dev.span`. Not yet seen on the phone |
| `OPOS_DEV_SUDO` | unset; a build input of `build-image.sh`, dev images only | `1`, with `OPOS_DEV_SSH_PUBKEY` (the build refuses it alone, before the output is touched): writes `/etc/sudoers.d/90-opos-dev` (0440 root:root, exactly `droidian ALL=(ALL) NOPASSWD:ALL`, checked with `visudo -cf` where the builder has it), so the dev login user is root and the checkpoint can be fixed over ssh; no file without it. An image built this way must never be carried or shared: everything that runs as `droidian` is root on it. `opos-selftest` WARNs `dev.sudo`. Not yet seen on the phone |
| `OPOS_KIOSK_DEBUG_PORT` | unset; a dev image writes it into `/etc/default/opos-kiosk` | the kiosk Chromium's DevTools port on `127.0.0.1`; `opos-kiosk` reads it from that file alone, never from a unit or the caller's environment |
| `OPOS_DEV_USB_SHELL` | ignored | Root telnet shells are always closed; `/etc/opos/usb-shell.conf` records `enabled=0`. Phone checks remain **NOT_RUN** |
| `OPOS_NATIVE`, `OPOS_NATIVE_OFF`, `OPOS_NATIVE_TRIED`, `OPOS_NATIVE_RESTORE_FAILED` | unset (`/usr/libexec/opos/opos-native`, `/etc/opos/native-off`) | the native shell's launcher that `opos-kiosk` hands its process to, the file whose presence keeps the browser as the interface from the session's next normal start, the flag the launcher sets when it hands the process back so the kiosk never hands it over again, and the flag `opos-native` sets when its restore of the two screens gave up before the handover, which `opos-kiosk` reads (and unsets) to keep trying in the background with `python3 -m opos.native.blank --restore --for 120`; hooks for the tests. Not yet seen on the phone; see [NATIVE.md](../packaging/duo-os/NATIVE.md) |
| `OPOS_NATIVE_BROWSER`, `OPOS_NATIVE_DEFAULTS`, `OPOS_NATIVE_APPS_CONF`, `OPOS_NATIVE_FIRSTRUN_FLAG`, `OPOS_NATIVE_SRC`, `OPOS_NATIVE_START_S` | unset | only for `opos-native`: the browser kiosk it hands over to, the file it reads `OPOS_SPAN` from (`/etc/default/opos-native`), the server drop-in it reads `OPOS_APPS` from (`/etc/systemd/system/opos-server.service.d/20-opos-apps.conf`), the first-run flag file it looks for, where the shell's source is, and how many seconds the shell has to put its window up (30) |
| `OPOS_KIOSK_DEFAULTS` | unset (`/etc/default/opos-kiosk`) | which file `opos-kiosk` reads the DevTools port from; a hook for tests |
| `OPOS_OSK_BIN`, `OPOS_OSK_WAIT`, `OPOS_OSK_GSETTINGS` | unset | only for `opos-osk` (the opt-in Wayland keyboard): the binary, seconds to wait for phoc's socket (120), and `0` to leave gsettings alone |
| `OPOS_BATTERY`, `OPOS_ON_PHONE` | `live`, `1` | the sysfs battery source and the phone-only behaviour |
| `OPOS_BRIGHTNESS` | `128` (`opos-brightness.service`) | one OS level, 1-255, mapped through the vendor curve and written to both panel backlights. Not measured on a phone |
| `OPOS_BRIGHTNESS_DIR` | `/run/user/32011` | directory where the brightness unit writes `sfduo-brightness` for the lid helper |
| `OPOS_UPDATE_SIGNERS` | unset (a build input) | a file of public keys (`ssh-ed25519 ...`) that `build-image.sh` installs as the image's only trust for over-the-air updates; without it the image accepts no update |
| `OPOS_UPDATE_URL` | unset (a build input) | `https://host/folder/latest.json`, written to `/etc/opos/update.json` so the Updates page may look there, only when asked; without it the phone fetches nothing |

## Removed on purpose

Two doors the port leaves open, closed by `build-image.sh` in every image with no switch (the `usb hardening` block, checked by `verify-image.sh`; details in the
image README under "Removed on purpose: the USB root shell and the password login"):

- the root telnet shell on the USB link: the port's `sfduo-usb-gadget.sh` ended by starting busybox `telnetd` with `/bin/bash` as its login program, so anything on
  the cable had a root shell with no login. The line is now a comment.
- the sshd password login for the USB subnet: the port's `10-item-password-usb-only.conf` let a password in for every client whose source address was in that
  /24, on any interface, Wi-Fi included once joined. The drop-in is key-only now, so ssh takes a key from every address and `opos-selftest` `ssh.auth` says
  `key-only everywhere`. Every accessible image requires `OPOS_OWNER_SSH_PUBKEY`; `OPOS_NO_SSH=1` (or the compatibility alias `OPOS_ALLOW_NO_SSH=1`) explicitly disables and masks SSH services and sockets. A dev image may add `OPOS_DEV_SSH_PUBKEY` and, if built with `OPOS_DEV_SUDO=1`, root through sudo.

The separate exposure hardening binds the container debug port to loopback, drops new inbound packets on `wlan0`, and closes other root telnet units
unconditionally. An inherited `OPOS_DEV_USB_SHELL=1` cannot preserve a root shell. Not yet seen on the phone.

## What is still simulated

- **Volume keys report simulated state only.** The reader publishes a bounded percentage for Settings and the top bar; no audio mixer is connected and sound is unchanged. Missing or invalid state is shown as unavailable. Image build and verification, native systemd lifecycle, browser, phone and audible checks for this consumer remain **NOT_RUN**; local fake-input, HTTP and Node VM checks do not establish them.
- **Calls and texts are simulated.** The base has ModemManager (through ofono2mm) and `mmcli` can read the modem and SIM, so the
  Network page shows the real radio's state, but `src/opos/mm_modem.py` is not wired to the phone page. Nothing is dialled, sent
  or received; the page says so.

## What is not known

- **Nothing is measured about battery life or stability.** No drain curve, heat, standby or multi-day run exists for this image.
  The Duo gauge reader reads available sysfs temperature values. Missing readings can leave the runtime at its default
  temperature, so source support does not establish that heat protection works on the phone. Device temperature verification
  remains required; `battery.temp` warns when the default appears without a sysfs reading or when the values disagree.
- **RAM-boot only.** The bootloader refuses boot-partition writes, so every boot comes from the PC. Nothing here makes it permanent.
- **Never seen on the phone:** the keyboard under a finger and at the real 1392 x 900 window; the Network page against the phone's
  real NetworkManager, ModemManager and polkit (the password handshake is the main unknown, with `OPOS_NET_TTY=1` as the fallback);
  that Chromium on the phone honours `TZ`; that killing only Chromium after a zone change leaves phoc and the kiosk loop to bring
  the screen back; that the clock corrects itself once online; every output format the selftest reads (`wlr-randr`, `nmcli -t`,
  `journalctl`, `timedatectl`).
- **Page size depends on the source revision and enabled features.** The November 12 development record in
  [ROADMAP.md](../ROADMAP.md) reports 101,895 bytes with volume and the existing switches enabled; this is a recorded local
  result, not a phone-performance measurement. Older byte counts apply only to their named revisions.
  [ui-byte-ledger.md](ui-byte-ledger.md) gives the current measurement command and the 102,000-byte gate.
- **Over-the-air updates are built but not seen on the phone** ([ota-update.md](ota-update.md), [ota-release-howto.md](ota-release-howto.md)):
  the root updater, the `/update` page, the release tool and the USB push script are tested on a computer against stand-ins for systemd,
  runuser and the browser. An image built without `OPOS_UPDATE_SIGNERS` accepts no update, and the selftest's `ota.updater` says so (WARN).
  The clock floor, measured drain, the hinge layout (`OPOS_SPAN=675,42` in the service file, not yet measured on the phone), the kiosk units with their restart loop and
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

For whoever holds the phone. Read-only unless a step says otherwise; replace `<phone-usb-address>` and `<key>`.
Find the current USB value using [USB-link addresses](../packaging/duo-os/README.md#usb-link-addresses).
Keep the output private: names, addresses and passwords appear in some of it.

1. **Build and verify** (Linux builder, root). Run from the OPOS checkout after replacing the quoted placeholders with absolute paths:

   ```sh
   sudo env OPOS_OWNER_SSH_PUBKEY="/absolute/path/to/owner.pub" \
     sh packaging/duo-os/build-image.sh \
     "/absolute/path/to/base-rootfs.img" \
     "/absolute/path/to/new-opos-os-rootfs.img" HEAD

   sudo sh packaging/duo-os/verify-image.sh \
     "/absolute/path/to/new-opos-os-rootfs.img"
   ```

   A developer key is optional and does not replace the required owner recovery key. The output image must not already exist.
   `OPOS_DEV_SSH_PUBKEY=<file.pub>` adds a developer key. With that key, `OPOS_DEV_SPAN=1` records a dev-span drop-in and
   `OPOS_DEV_SUDO=1` gives `droidian` passwordless sudo for an image that is never carried or shared. `OPOS_NET_POLKIT=1`
   installs the NetworkManager rule described above. Expect `VERIFY: PASS` and, among the lines,
   "the keyboard, the Network page and the first run answer together in one server from the image".
2. **Boot** from the PC as before, wait five minutes (the boot dump is due 90 s after multi-user.target).
3. **The suite, from the PC:** `pwsh -File deploy\duo-acceptance.ps1 -Host <phone-usb-address> -Key <key> -OutDir <folder>`; on the phone,
   `/usr/sbin/opos-selftest ; echo rc=$?`. Expect no FAIL. Expected WARNs on a first boot of a dev image: `battery.temp`, `clock.year`
   (1970, or the image's floor until network time or a set time), `clock.timezone` (UTC), `dev.*` (`dev.ssh-key`, `dev.devtools`, `dev.time-drop`, `dev.span` with `OPOS_DEV_SPAN=1`, and `dev.sudo` on an image built with `OPOS_DEV_SUDO=1`), possibly `svc.system`. Expected WARN: `ota.updater` on an image built without `OPOS_UPDATE_SIGNERS` (no update is accepted). Expected SKIP: `clock.kiosk-tz` until a zone is chosen; `kiosk.restarts` reports the exits `opos-kiosk` logged this boot.
   Expected PASS: `osk.keyboard`, `net.page`, `net.permissions` (WARN means NetworkManager will refuse; see step 5),
   `firstrun.flag`, `firstrun.routes` (it says first run PENDING). Any FAIL or surprise in a format is a finding: send the report.
   `page.png` exists only if the kiosk has a DevTools port, which comes from another branch.
4. **Formats the suite assumes** (ssh as `droidian`; compare each with the matching selftest line and report differences):
   `systemctl show --property=LoadState,ActiveState,SubState,Result opos.service`; `pgrep -af phoc` and `pgrep -af 'composer@2.4'`
   (phoc has `-E /usr/libexec/opos/opos-kiosk` and no `-S`); `tr '\0' '\n' < /proc/$(pgrep -f -- --kiosk | head -1)/environ | grep -E '^(WAYLAND_DISPLAY|XDG_RUNTIME_DIR)='`;
   `XDG_RUNTIME_DIR=/run/user/32011 WAYLAND_DISPLAY=wayland-0 wlr-randr` (HWCOMPOSER-1, Enabled: yes, 2784x1800 px, Scale 2);
   `cat /sys/class/backlight/panel*-backlight/bl_power /sys/class/backlight/panel*-backlight/brightness /sys/class/drm/card0-DSI-*/dpms` (`bl_power` 0, a level above 0, `On` twice; `actual_brightness` reads 0 on this kernel while the panels are lit, so `gfx.backlight` does not use it);
   `for s in /sys/class/power_supply/*; do echo $s $(cat $s/type) $(cat $s/capacity 2>&1) $(cat $s/status 2>&1) $(cat $s/temp 2>&1); done`;
   `timedatectl show -p Timezone -p NTPSynchronized`; `nmcli -t -f DEVICE,TYPE,STATE device`; `ip -4 -o addr show dev usb0`;
   `ls /var/log/journal/*/`; `head -3 /var/log/opos-boot-diag.txt`.
5. **Network layer, before touching the screen:** `export PYTHONPATH=/opt/opos/src; cd /opt/opos/src; id` (groups include `netdev`);
   `systemctl show opos-server -p Environment` (shows `OPOS_NETWORK=1`, `OPOS_FIRSTRUN=1`, `OPOS_OSK=1`, `OPOS_MONITOR=1`);
   `python3 -m opos.net probe | tee /tmp/net-probe.txt` (every block `rc=0`; an `mmcli` "couldn't find modem" is a finding, not an error);
   `nmcli -t general permissions | grep -E 'enable-disable-wifi|network-control|settings.modify.system|wifi.scan'` (four lines ending `:yes`;
   `:auth` or `:no` means the default rule is not in effect; `OPOS_NET_POLKIT=0` omits it); `python3 -m opos.net status | python3 -c 'import json,sys; s=json.load(sys.stdin); [print(s[k]["summary"]) for k in ("wifi","bluetooth","modem","sim","data","airplane","usb")]; print(s["control"]); print(s["notes"])'`
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
11. **RAM-boot again** from the PC (reboot bootloader path; never plain reboot); after 90 s `sed -n '/== radios/,/== power supplies/p' /var/log/opos-boot-diag.txt` (six `radios:` lines, no
    SSID, address or IMEI). The setup must not come back, the zone file is unchanged, the status bar keeps the zone, and
    `/usr/sbin/opos-selftest` has no FAIL. Note whether a saved Wi-Fi network survives the reboot (NetworkManager keeps it under
    `/etc/NetworkManager/system-connections` on the rootfs file, which lives on userdata).
12. **Optional, the port's keyboard:** the recipe in [packaging/duo-os/OSK.md](../packaging/duo-os/OSK.md) (`OPOS_OSK=stevia`).
13. **The owner's part:** the ten checks of part 2 of [duo-acceptance.md](duo-acceptance.md), written down with dates.
