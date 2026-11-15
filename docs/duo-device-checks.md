<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Duo device checks: the runbook

This page is for the person at the phone: the PC session or the owner. It lists, step by step, every phone check that
the docs still mark **NOT_RUN**, and for each one it says what must be true first, what to do, what to look at, what
counts as a pass, how to stop and undo, and what to write down.

Nothing on this page has been run. Writing it changed nothing on a phone. Every row of the results table at the end
stays NOT_RUN until someone runs that check on the phone and records the run with a date and an evidence link.

## Scope and sources

The list comes only from the NOT_RUN statements in [NATIVE.md](../packaging/duo-os/NATIVE.md), [ROADMAP.md](../ROADMAP.md),
[duo-os-final.md](duo-os-final.md), [duo-usb-install.md](duo-usb-install.md) and the bodies of the merged pull requests.
Each check names its sources as `file:line` with a quoted phrase. The line numbers are as of source revision `5b5cd46`
and can drift; the quoted phrase is what `tests/test_duo_device_checks.py` looks for, and that test fails when a NOT_RUN
statement in those four files has no check quoting it.

The roadmap's device table is the same list seen from the features side: `ROADMAP.md:130` "Date stays empty and Result
stays NOT_RUN until a phone run fills both", and `ROADMAP.md:161` "the device checks above remain". The results table at
the end follows that rule.

Some NOT_RUN statements in merged pull requests are not phone checks (CI runs, the optional guard, browser tests on the
Mac, review pipeline steps) or are already covered by their own runbook. They are listed under
[Covered elsewhere, and not phone checks](#covered-elsewhere-and-not-phone-checks).

## Markers

Each step that does more than read carries one or more markers, written in the step itself:

| Marker | Meaning |
|---|---|
| [READ-ONLY] | reads state; changes nothing on the phone |
| [OWNER ONLY] | the owner runs it, or says go for it in this session before it runs |
| [ENDS WI-FI] | can end or change the phone's own Wi-Fi connection |
| [REBOOTS] | leaves the running system; the phone must be RAM-booted again from the PC |
| [ERASES] | erases data on the phone; nothing on it can be brought back |

## Ground rules

- No step wipes, flashes or changes Wi-Fi unless it is marked and the owner says go. Read-only steps may run at any time.
- Keep the USB cable in for every check, unless a step says to take it out. The USB link (`usb0`) does not depend on
  Wi-Fi, so ssh over it stays up while Wi-Fi is joined, forgotten or turned off.
- Never run `reboot` or `systemctl reboot` on the phone. A RAM-booted kernel does not survive it, and a start without the
  PC boots Android, which destroys the OPOS installation. To leave the running system, use `reboot bootloader` and
  RAM-boot once from the PC (see [Recovery paths](#recovery-paths)).
- Battery at 30 percent or more before a check that reboots or erases. The button steps in
  [duo-usb-install.md](duo-usb-install.md#button-steps) need at least 20 percent; this page keeps the higher floor of
  [duo-persistent-install.md](duo-persistent-install.md).
- One check at a time. If a step does not do what the page says, stop there, write down what you saw, and use the
  check's Stop and undo. Do not retry a step that erases or reboots.
- Use test values: a test PIN that is not a real one, and a test Wi-Fi network (for example a hotspot on another phone)
  that the owner may lose for a minute. Never write a real password, a network name, an address, a serial, an IMEI or a
  hardware address into the repository or the results table. Keep raw output private.

Replace `<phone-usb-address>` and `<key>` in the commands; [USB-link addresses](../packaging/duo-os/README.md#usb-link-addresses)
says how to find the address. "On the phone" means an ssh session as `droidian` over the USB link:
`ssh -i <key> droidian@<phone-usb-address>`. Most phone commands need `export PYTHONPATH=/opt/opos/src` first.

## Before the checks: getting the image on the phone

This is not a check, and this page adds nothing to it. The image is built and RAM-booted by the owner or by the PC
session with the owner's go, through the guarded route in
[Install: RAM-boot from a Windows PC](../packaging/duo-os/README.md#install-ram-boot-from-a-windows-pc). That route
erases userdata and boots the phone from the PC, so it is [OWNER ONLY] [ERASES] [REBOOTS]. Every check below assumes a
phone already running an image built from the revision under test, with first-run setup finished (except where a check
says otherwise), and the USB link up.

## Image and services

### C01. Image build and verification

Sources: `docs/duo-os-final.md:14` "A real image build and every phone check for this change remain";
`ROADMAP.md:76` "Image build/verification"; `ROADMAP.md:90` "image execution remain"; `docs/duo-os-final.md:96`
"Image build and verification, native systemd lifecycle, browser, phone and audible checks for this consumer remain".

**Preconditions.** A Linux builder with root, a loop mount and the base image; the owner's recovery public key; a
checkout of the revision under test. Nothing touches the phone.

**Steps.**
1. [OWNER ONLY] Build and verify exactly as step 1 of [Live checks, in order](duo-os-final.md#live-checks-in-order) says (`build-image.sh`, then `verify-image.sh`).
2. [READ-ONLY] Keep the full verifier output and the SHA-256 of the new image file (`sha256sum <image>`).

**Observe.** The verifier's last line, the lines for the native launcher (`/usr/libexec/opos/opos-native`,
`/etc/default/opos-native`), the power-key and volume-key units, and the identity manifest.

**Pass or fail.** Pass: `VERIFY: PASS`, and none of the units or files above is reported missing. Fail: any other
result, or a missing file.

**Stop and undo.** Nothing to undo: the build writes a new file only and refuses an output that already exists. Do not
carry or share a dev image built with `OPOS_DEV_SUDO=1`.

**Record.** The source revision, the build flags used (names only, no key contents), the image hash, and the verifier
result. Keep the hash with the PC session's notes; it is the hash the boot route checks.

### C02. Image identity on the phone

Sources: `docs/duo-os-final.md:14` "A real image build and every phone check for this change remain".

**Preconditions.** The image from C01 is running on the phone.

**Steps.**
1. [READ-ONLY] On the phone, run checks 1 to 7 of [duo-os-identity.md](duo-os-identity.md) in order (`/etc/os-release`, `opos-version`, `opos-selftest --only identity`, the update page read).
2. [READ-ONLY] On the glass, open Settings, About, Updates, and compare the version, revision and base with `opos-version`.

**Observe.** Each command's output against the image manifest, and the visible About facts.

**Pass or fail.** Pass when each of the seven checks passes as that page defines it and the glass shows the same facts.
Fail on any mismatch.

**Stop and undo.** Read-only; nothing to undo.

**Record.** Pass or fail for each of the seven checks, and the version line (it holds no identifier).

### C03. USB root shell closed, ssh key-only, debug port on loopback

Sources: `docs/duo-os-final.md:68` "Root telnet shells are always closed"; PR #13 "Each one is NOT_RUN"; PR #117
"Device checks are NOT_RUN".

**Preconditions.** A normal image (not one built with a dev ssh key, unless the check says so). The USB link is up.

**Steps.**
1. [READ-ONLY] On the phone: `cat /etc/opos/usb-shell.conf; pgrep -af telnetd; echo rc=$?`.
2. [READ-ONLY] On the phone: `ss -ltn` and look at the listening addresses.
3. [READ-ONLY] From the PC: `ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password droidian@<phone-usb-address>`; do not type any password, only read the answer.
4. [READ-ONLY] On the phone: `/usr/sbin/opos-selftest --only ssh; echo rc=$?`.

**Observe.** The config line, the process list, the listening sockets, the ssh answer, and the `ssh.auth` line.

**Pass or fail.** Pass: `enabled=0`; no `telnetd` process (`pgrep` exits 1); nothing listens on port 23; the container
debug port, if listed, is bound to `127.0.0.1` only; the password-only ssh attempt is refused with `Permission denied
(publickey)` without a password prompt; `ssh.auth` says `key-only everywhere`. Fail: any of these differs.

**Stop and undo.** Read-only; nothing to undo. Close the refused ssh attempt with Ctrl-C if it waits.

**Record.** Each of the four answers as pass or fail, with the selftest line. Do not record addresses or port lists
that include Wi-Fi addresses.

### C04. Native shell starts, answers taps and hands over to the browser

Sources: PR #130 "the device checks stay NOT_RUN"; `ROADMAP.md:161` "the device checks above remain".

**Preconditions.** First-run setup finished, `OPOS_LOCK` not set, `/etc/opos/native-off` absent.

**Steps.**
1. [READ-ONLY] On the phone: `pgrep -af 'opos.native.shell'; /usr/sbin/opos-selftest --only kiosk; echo rc=$?`.
2. [READ-ONLY] On the phone: `journalctl -b -t opos-native --no-pager | tail -n 40` (if the tag differs, `journalctl -b --no-pager | grep -i 'opos-native' | tail -n 40`).
3. On the glass: open Home, Phone, Messages, Clock, Contacts, Notes, Calculator, Call log and Settings, and go back to Home from each.
4. On the glass: drag the battery sheet up and down, and drag the bottom edge of an app page; lift the finger half way.
5. [OWNER ONLY] On the glass: Settings, "Use the old interface". The browser kiosk takes over.

**Observe.** One window across both screens, nothing under the hinge; each tap answered in one frame, nothing sliding
or fading; the sheet following the finger exactly and showing the final state when it lifts; the practice and
emergency sentences on Phone; the launcher's log lines; after step 5, the browser home page.

**Pass or fail.** Pass: `kiosk.native` names the native shell, every screen opens and returns, drags follow the finger
1:1 and land complete, and step 5 hands over to the browser with both screens lit. Fail: a screen that does not open,
a frame that shows a wrong or half state, a dark screen at any time, or a hand-over that does not come.

**Stop and undo.** After step 5 the browser stays until the session starts again, or see
[R2](#r2-the-native-shell-is-wrong-or-stuck). If a screen goes dark, use [R1](#r1-a-screen-is-dark).

**Record.** Which screens passed, the launcher lines (no addresses), the result of step 5, and the interface that ran.

### C05. Lid close blanks both screens and lid open restores them

Sources: `packaging/duo-os/NATIVE.md:205` "no lid close, blank or restore on the Duo is recorded here"; PR #135 "any lid
close, blank or restore on a Duo"; `ROADMAP.md:143` "Folded posture, and bedside on the real hinge."

**Preconditions.** C04 passed; the native shell is the interface; the USB link is up and an ssh session is open; the
screens are lit.

**Steps.**
1. [READ-ONLY] On the phone, before folding: `cat /sys/class/backlight/panel*-backlight/brightness /sys/class/backlight/panel*-backlight/max_brightness; ls -l /run/user/32011/opos-saved-brightness`.
2. Close the lid fully and keep it closed for 10 seconds.
3. [READ-ONLY] On the phone, with the lid still closed: `cat /sys/class/backlight/panel*-backlight/brightness; cat /run/user/32011/opos-saved-brightness`.
4. Open the lid.
5. [READ-ONLY] On the phone: `cat /sys/class/backlight/panel*-backlight/brightness; ls -l /run/user/32011/opos-saved-brightness`.
6. Repeat steps 2 to 5 three times, then once more with the lid closed for 2 minutes.

**Observe.** Before: both brightness values above 0 and no saved file. Closed: the shell locks, then about 350 ms later
both panels read 0 and the saved file holds the earlier values. Open: both panels are lit again at exactly the earlier
values.

**Pass or fail.** Pass when every round blanks both panels with the lid closed and restores both to their exact earlier
values on opening, and the lock screen shows after opening. Fail: a panel stays lit with the lid closed, a panel stays
dark after opening for more than 10 seconds, or a restored value differs.

**Stop and undo.** Open the lid. If a panel stays dark after 10 seconds, use [R1](#r1-a-screen-is-dark) at once. Do not
leave the phone closed and unattended during this check.

**Record.** For each round: the values before, closed and after (numbers only), and how long the restore took by eye.
Bedside on the real hinge stays NOT_RUN: see C29.

### C06. Restore retries and the power-save mode (observe only)

Sources: `packaging/duo-os/NATIVE.md:205` "a lid watcher that stops is not started again"; PR #135 "any lid close, blank
or restore on a Duo".

**Preconditions.** C05 passed. This check only watches what happens; it never makes a panel fail on purpose, because
that needs root writes to sysfs and could leave a screen dark.

**Steps.**
1. [READ-ONLY] On the phone, with the lid open: `gdbus call --session --dest org.gnome.Mutter.DisplayConfig --object-path /org/gnome/Mutter/DisplayConfig --method org.freedesktop.DBus.Properties.Get org.gnome.Mutter.DisplayConfig PowerSaveMode`.
2. Close the lid for 10 seconds, then run the step 1 command again with the lid closed.
3. Open the lid, wait 10 seconds and run the step 1 command again.
4. [READ-ONLY] On the phone: `journalctl -b --no-pager | grep -iE 'opos-native|blank|restore|lid' | tail -n 60`.

**Observe.** The mode in force at each point (0 lit; 1 to 3 dark), and any journal line naming a failed panel, a retry,
a lost lid signal or a refused blank.

**Pass or fail.** Pass when the mode reads 0 with the lid open and the screens lit, the value with the lid closed matches
what the screens show, and the journal shows no failed restore. If the journal shows a failed panel, pass only when a
later line shows it lit within the retries (1 s, 2 s, 4 s, then every 5 s). Fail: a mode that disagrees with the
screens, or a failed restore with no later success. The induced-failure retry paths stay NOT_RUN by design.

**Stop and undo.** Open the lid; for a dark screen, [R1](#r1-a-screen-is-dark).

**Record.** The three mode values, and the journal lines about blank and restore with times (no addresses).

### C07. Current drawn with the lid closed, and drain with the panels blanked

Sources: `packaging/duo-os/NATIVE.md:205` "the current drawn with the lid closed has not been measured"; PR #135 "the
current drawn with the lid closed"; `ROADMAP.md:140` "Battery drain with the panels soft-blanked."

**Preconditions.** C05 passed. Battery between 50 and 90 percent. The USB cable must be out for the measurement,
because charging hides the drain; so the logger runs on the phone by itself.

**Steps.**
1. [READ-ONLY] On the phone: `ls /sys/class/power_supply/; cat /sys/class/power_supply/*/current_now`, to see which supplies report a current.
2. [READ-ONLY] On the phone, start a 30-minute logger that outlives the ssh session: `systemd-run --user --unit=opos-lid-current sh -c 'for i in $(seq 1 180); do echo "$(date +%s) $(cat /sys/class/power_supply/*/capacity | tr "\n" " ") $(cat /sys/class/power_supply/*/current_now | tr "\n" " ")"; sleep 10; done > $HOME/lid-current.txt'`. If it says there is no user bus, stop here and record the check as not run.
3. Take the USB cable out. Leave the lid open, screens lit and the phone untouched for 10 minutes.
4. Close the lid for 10 minutes. Then open it and leave it open for 10 minutes.
5. Put the USB cable back. [READ-ONLY] On the phone: `cat $HOME/lid-current.txt`.

**Observe.** The current and capacity lines for the three 10-minute windows, and that the screens were lit after step 4.

**Pass or fail.** This is a measurement, not a pass or fail. It is complete when all three windows have readings and the
screens came back lit. Write the average current of each window; do not turn it into a battery-life figure.

**Stop and undo.** `systemctl --user stop opos-lid-current` ends the logger early. If the screens are dark after
opening, [R1](#r1-a-screen-is-dark). The log file holds no identifiers; remove it from the phone when the numbers are
copied, if the owner wants.

**Record.** The three averages with their units as the kernel reports them, the start and end capacity, the date, and
that it was one run on one phone.

### C08. Power key blanks and lights the screens

Sources: `ROADMAP.md:90` "phone behavior, native systemd lifecycle and image execution remain".

**Preconditions.** The image from C01 is running; [POWER.md](../packaging/duo-os/POWER.md) read. Lid open.

**Steps.**
1. [READ-ONLY] On the phone: `systemctl show -p ActiveState,SubState,Result,NRestarts opos-powerkey.service`.
2. Press the power key briefly (under 1.5 seconds) once. Wait 3 seconds.
3. [READ-ONLY] On the phone: `cat /sys/class/backlight/panel*-backlight/bl_power`.
4. Touch the glass where a button would be, then press the power key briefly once more.
5. [READ-ONLY] Run the step 3 command again; then hold the power key for about 3 seconds and release it, and run it once more.

**Observe.** The unit active and running; after step 2 both panels dark and `bl_power` reads 4 on both; whether the
touch in step 4 did anything on the page (it may, see POWER.md); after the second press both lit (`bl_power` 0); a long
hold does not toggle.

**Pass or fail.** Pass: short presses blank and light both panels together, a long hold changes nothing, and the unit
stays active with no restarts. Fail: one panel only, a screen left dark, or a restart counted. Write down what the
touch while dark did; that behaviour is not known yet.

**Stop and undo.** A further short press lights the screens. If they stay dark, [R1](#r1-a-screen-is-dark).

**Record.** The unit state before and after, the `bl_power` values, and what the touch while dark did.

### C09. Volume keys change only the simulated volume

Sources: `ROADMAP.md:76` "native systemd lifecycle, browser, phone and audible checks remain"; `docs/duo-os-final.md:96`
"Volume keys report simulated state only."

**Preconditions.** The image from C01 is running; [VOLUME.md](../packaging/duo-os/VOLUME.md) read.

**Steps.**
1. [READ-ONLY] On the phone: `systemctl show -p ActiveState,SubState,NRestarts opos-volumekey.service; cat /run/opos-volume/*`.
2. Press volume up once, then volume down twice; then hold volume up for 3 seconds.
3. [READ-ONLY] Run the step 1 command again, and look at Settings and the top bar on the glass.
4. Play nothing: listen only for whether any sound changed.

**Observe.** The published state file (`"simulated": true`), the percent before and after, and what the glass shows.

**Pass or fail.** Pass: from the value read in step 1, the presses give +5, then -5 twice (60, 65, 60, 55 when the
reader has just started at 60), clamped to 0 to 100; a hold changes it once only; the glass shows
the same value as simulated, and no sound changes. Fail: a different step, a repeat while held, a value on the glass
that differs, or any change to real sound.

**Stop and undo.** Read-only on the system; the value resets to 60 when the reader starts again.

**Record.** The values after each press and the unit's restart count.

## Network screen (native shell)

All of these use the native shell's Network screen (Settings, Network) on a server that runs with `OPOS_NETWORK=1`.
Several can end the phone's own Wi-Fi connection: the owner runs them, with the USB cable in so that ssh stays up. Use a
test network the owner may lose for a minute, and a second test network for C16. Recovery for lost Wi-Fi is
[R3](#r3-wi-fi-is-off-or-lost).

### C10. Opening the Network screen with real radios

Sources: `packaging/duo-os/NATIVE.md:164` "opening the screen with real radios"; `ROADMAP.md:52` "Opening the screen, a
real join, forget, disconnect, Wi-Fi off and on, a hidden network and the two-tap prompts remain"; PR #132 "opening
the screen with real radios".

**Preconditions.** C04 passed. Wi-Fi on. Nothing on this check changes a radio.

**Steps.**
1. [READ-ONLY] On the phone: `python3 -m opos.net status | python3 -c 'import json,sys; s=json.load(sys.stdin); [print(s[k]["summary"]) for k in ("wifi","bluetooth","modem","airplane")]'` (the `python3 -m opos.net status` read).
2. [READ-ONLY] On the glass: Settings, Network. Wait for the list. Tap one network row, then Cancel.
3. [READ-ONLY] Leave the screen (Home), wait 30 seconds, and open it again.

**Observe.** The radios in the server's sentences, matching step 1; the list with the connected network first, then by
signal, then saved ones not in range; "Reading the list again..." on the second opening until the list comes; a chosen
row showing its choice; nothing changing on the phone.

**Pass or fail.** Pass: the screen agrees with step 1, the list order is as described, and opening, choosing and
leaving change no radio. Fail: a sentence that disagrees with the status read, a wrong order, or any change in Wi-Fi.

**Stop and undo.** Read-only; leave the screen.

**Record.** Pass or fail for each observation. Do not record network names.

### C11. Join with a typed password, wrong first, then right

Sources: `packaging/duo-os/NATIVE.md:164` "a real join (typed or saved)"; PR #132 "a real join (typed or saved)".

**Preconditions.** C10 passed. The test network is in range and not saved; its password is known to the owner only.

**Steps.**
1. [READ-ONLY] On the phone, in a second ssh session, start the process watch from step 9 of [Live checks, in order](duo-os-final.md#live-checks-in-order): `for i in $(seq 1 600); do ps -eo args | grep '[n]mcli'; sleep 0.2; done | sort -u`.
2. [OWNER ONLY] [ENDS WI-FI] On the glass: choose the test network, type a wrong password on the on-screen keyboard and tap Join.
3. [OWNER ONLY] [ENDS WI-FI] Choose it again, type the right password and tap Join.
4. [READ-ONLY] On the phone: `nmcli -t -f DEVICE,STATE,CONNECTION device status` (do not paste the output into the record).

**Observe.** The password shown as bullets only; "The password was not accepted. Check it and try again. Nothing was
saved." for the wrong one; the screen busy until the server answers; the right one connecting and the list putting it
first; the process watch showing `--ask` and never the password.

**Pass or fail.** Pass: the wrong password is refused with that sentence, the right one joins, and no command line holds
the password. Fail: the password appears anywhere in the process list or on screen, or a correct password is refused
(then note it and see the `OPOS_NET_TTY=1` fallback in step 9 of the live checks).

**Stop and undo.** To leave the test network: C15 (Forget), or over ssh [R3](#r3-wi-fi-is-off-or-lost).

**Record.** Pass or fail for the refusal, the join and the process watch. No names, no passwords.

### C12. Join a saved network without a password

Sources: `packaging/duo-os/NATIVE.md:164` "a real join (typed or saved)".

**Preconditions.** C11 passed, so the test network is saved; the phone is on another network or none.

**Steps.**
1. [OWNER ONLY] [ENDS WI-FI] On the glass: choose the saved test network and tap Join without typing anything.
2. [READ-ONLY] Watch the screen until it answers, then read the radios line.

**Observe.** No password field for a saved network; one tap of Join; the radios read again after the answer.

**Pass or fail.** Pass: it joins with no password asked and the screen shows it connected. Fail: a password asked, or no
answer within 100 seconds.

**Stop and undo.** As C11.

**Record.** Pass or fail and the time to connect, by eye.

### C13. A long password on the on-screen keyboard

Sources: `packaging/duo-os/NATIVE.md:164` "the keyboard layout for a long password"; PR #132 "the keyboard for a long
password".

**Preconditions.** A test network whose password is long (40 characters or more, with capitals, digits and symbols).

**Steps.**
1. [OWNER ONLY] [ENDS WI-FI] On the glass: choose that network and type the whole password, switching between letters, `123` and `#+=`.
2. [OWNER ONLY] [ENDS WI-FI] Delete three characters, type them again, and tap Join.

**Observe.** No key under the hinge; the field not hidden behind the keyboard; one bullet per character; no doubled or
missed keys; the join result.

**Pass or fail.** Pass: every character lands once and the join succeeds. Fail: a key unreachable, a missed or doubled
character, or a field hidden.

**Stop and undo.** Cancel empties the password. Leave the network with C15 or [R3](#r3-wi-fi-is-off-or-lost).

**Record.** Pass or fail and any key that misbehaved.

### C14. A hidden network, and editing its name after typing the password

Sources: `packaging/duo-os/NATIVE.md:164` "a hidden network, editing a hidden network's name after typing its
password"; PR #136 "Editing a hidden network's name after typing its password".

**Preconditions.** A test network set to hide its name, whose name and password the owner knows.

**Steps.**
1. [OWNER ONLY] On the glass: tap Other network, type the hidden network's name, then its password. Do not tap Join yet.
2. [OWNER ONLY] Add one letter to the name, then delete it.
3. [OWNER ONLY] [ENDS WI-FI] Type the password again and tap Join.

**Observe.** After step 2, the password field empty and "The name changed, so the password typed for the old name was
cleared. Type the password again."; after step 3, the join answer.

**Pass or fail.** Pass: the edit clears the password with that sentence, and the join with the retyped password
succeeds. Fail: the password survives the edit, or the join is sent with a password typed for another name.

**Stop and undo.** Cancel or leaving the screen empties the password. Leave the network with C15 or
[R3](#r3-wi-fi-is-off-or-lost).

**Record.** Pass or fail for the clear, the sentence and the join.

### C15. Forget a network, with its two taps

Sources: `packaging/duo-os/NATIVE.md:164` "forget, disconnect (with its read before sending, and a roam between the two
taps)"; PR #132 "forget".

**Preconditions.** The test network is saved.

**Steps.**
1. [OWNER ONLY] On the glass: choose the test network and tap Forget once; then tap Cancel (no forget is sent).
2. [OWNER ONLY] Tap Forget again, then choose another row (the prompt must disarm).
3. [OWNER ONLY] [ENDS WI-FI] Choose the test network, tap Forget, and tap it a second time at "Forget this network?".

**Observe.** "Forget this network?" after the first tap; Cancel and another row each disarming it; after the second tap
the network gone from the saved ones.

**Pass or fail.** Pass: only the second tap of an armed prompt forgets, and Cancel and another row disarm it. Fail: a
single tap forgets, or a disarmed prompt forgets.

**Stop and undo.** Join it again with C11 if the owner wants it saved.

**Record.** Pass or fail for each of the three steps.

### C16. Disconnect, with the read before sending, and a roam between the taps

Sources: `packaging/duo-os/NATIVE.md:164` "forget, disconnect (with its read before sending, and a roam between the two
taps)"; PR #136 "a real disconnect with its read, a roam between the two taps".

**Preconditions.** Two saved test networks in range (A and B); the phone is on A.

**Steps.**
1. [OWNER ONLY] [ENDS WI-FI] On the glass: choose A and tap Disconnect, then tap it again at "Disconnect now?".
2. [OWNER ONLY] [ENDS WI-FI] Join A again (C12). Choose A and tap Disconnect once, so the prompt is armed.
3. [OWNER ONLY] [ENDS WI-FI] Over ssh, make the phone roam to B while the prompt is armed: `nmcli connection up id '<B>'` (type the name, do not record it).
4. [OWNER ONLY] [ENDS WI-FI] On the glass, tap the second Disconnect.

**Observe.** In step 1: "Checking that <network> is still the connected network...", then "Disconnecting from
<network>...", then A no longer connected. In step 4: nothing sent, and a sentence saying the phone moved to another
network or that the connected network is not known; B stays connected.

**Pass or fail.** Pass: step 1 reads first and then disconnects A; step 4 sends nothing and says why. Fail: a disconnect
sent without the fresh read, or B dropped in step 4.

**Stop and undo.** Join the network the owner wants with C12, or [R3](#r3-wi-fi-is-off-or-lost).

**Record.** Pass or fail for steps 1 and 4, and the sentences seen (with the names left out).

### C17. Turn Wi-Fi off with two taps, and on with one

Sources: `packaging/duo-os/NATIVE.md:164` "Wi-Fi off and on, the two-tap prompts"; PR #132 "Wi-Fi off and on, the
two-tap prompts added here".

**Preconditions.** Wi-Fi on. The USB cable in (Wi-Fi off does not touch `usb0`).

**Steps.**
1. [OWNER ONLY] On the glass: tap Turn Wi-Fi off once; then tap Cancel.
2. [OWNER ONLY] [ENDS WI-FI] Tap Turn Wi-Fi off, then tap it again at "Turn Wi-Fi off now?".
3. [READ-ONLY] On the phone: `nmcli radio wifi` (the radio state).
4. [OWNER ONLY] Tap Turn Wi-Fi on once.

**Observe.** The prompt after one tap; Cancel disarming it; after step 2 Wi-Fi off on screen and `disabled` from
`nmcli`; the button greyed out ("Wi-Fi: not known yet") until the radios are read again; after step 4 Wi-Fi on with one
tap and the saved network joining again.

**Pass or fail.** Pass: off needs two taps, on needs one, and the screen and `nmcli` agree. Fail: off with one tap, a
button that acts while its state is not known, or a screen that disagrees with `nmcli`.

**Stop and undo.** Turn Wi-Fi on from the screen, or over ssh `nmcli radio wifi on` ([R3](#r3-wi-fi-is-off-or-lost)).

**Record.** Pass or fail for each step.

### C18. The 1.5-second holds, and the long-wait sentence

Sources: `packaging/duo-os/NATIVE.md:164` "the long-wait sentence"; PR #132 "the long-wait sentence".

**Preconditions.** C10 to C17 passed. A test hotspot the owner can switch on and off from its own phone.

**Steps.**
1. [OWNER ONLY] With the Network screen open, switch the test hotspot on, and tap a row in the second after the list changes.
2. [OWNER ONLY] [ENDS WI-FI] Arm Disconnect on the connected network; over ssh `nmcli radio wifi off`, then at once tap the Wi-Fi button on the glass; then `nmcli radio wifi on`.
3. [OWNER ONLY] [ENDS WI-FI] Choose Other network, type a name that no network in range has, type any password and tap Join; wait up to 100 seconds without tapping.

**Observe.** Step 1: "The list just changed. Check it, then tap the network again." and nothing chosen. Step 2: "This
button just changed. Check what it says now, then tap again." and nothing sent. Step 3: the screen busy, then a
sentence that the change may still be going on (not "failed"), and a fresh radios read.

**Pass or fail.** Pass: each hold blocks the tap within 1.5 seconds of the change and a tap after that acts; the long
wait is not called a failure. Fail: a tap that acts within the hold, or a long wait shown as failed.

**Stop and undo.** `nmcli radio wifi on` and join the owner's network again ([R3](#r3-wi-fi-is-off-or-lost)).

**Record.** Pass or fail for each of the three steps, and the sentences seen.

## Lock

### C19. The lock PIN on the glass

Sources: `ROADMAP.md:138` "The lock PIN works on the glass."; PR #31 "PIN entry on the real panel was not tried".

**Preconditions.** The image does not set `OPOS_LOCK` by itself. Turning it on is a config change on the phone: a
server drop-in with `Environment=OPOS_LOCK=1` and a restart of `opos-server`. With it on, the native shell hands over
to the browser, which has the PIN pad. Choose a test PIN of 4 to 8 digits that is not a real one.

**Steps.**
1. [OWNER ONLY] On the phone, with sudo: create `/etc/systemd/system/opos-server.service.d/40-opos-lock.conf` holding `[Service]` and `Environment=OPOS_LOCK=1`, then `sudo systemctl daemon-reload; sudo systemctl restart opos-server`.
2. [OWNER ONLY] On the glass: set the test PIN on the lock screen, then lock.
3. [OWNER ONLY] Type a wrong PIN five times.
4. [OWNER ONLY] Type the right PIN during the wait, then again after the wait ends.

**Observe.** The PIN pad reachable on the glass, nothing under the hinge; after five wrong tries a 30-second wait shown
in seconds; the right PIN refused during the wait and accepted after it.

**Pass or fail.** Pass: as observed above, and a wrong PIN never unlocks. Fail: an unreachable key, a wrong PIN that
unlocks, a right PIN accepted during the wait, or no wait after five wrong tries.

**Stop and undo.** [R4](#r4-the-pin-lock-is-on-or-the-pin-is-forgotten) turns the lock off again and puts the PIN file
aside. Do this before ending the session unless the owner wants the lock kept.

**Record.** Pass or fail for each step. Never record the PIN.

### C20. What a locked phone shows and refuses

Sources: PR #31 "Device: NOT_RUN (D5)"; PR #31 "While locked, /api/state returns the clock, the battery percent and
counts".

**Preconditions.** C19 steps 1 and 2 done; the phone is locked; at least one contact and one alarm exist.

**Steps.**
1. [READ-ONLY] On the phone, read the locked state without printing the page token: `python3 -c "import re,urllib.request as u;b=u.urlopen('http://127.0.0.1:8765/').read().decode();t=re.search(r'name=\"opos-token\" content=\"([^\"]+)\"',b).group(1);print(u.urlopen(u.Request('http://127.0.0.1:8765/api/state',headers={'X-OPOS-Token':t})).read().decode()[:600])"`.
2. On the glass, while locked: try to open Contacts or start a call.
3. When the alarm rings while locked (set it 2 minutes ahead before locking), tap Stop.

**Observe.** The state body holds `"locked": true`, the clock, the battery percent and counts only, and no contact
name or message text; the locked screen refuses the call; Stop works while locked.

**Pass or fail.** Pass: no name or message text in the body or on the glass while locked, writes refused, alarm stop
allowed. Fail: any name or message text shown, or a write that goes through.

**Stop and undo.** Unlock with the test PIN; then [R4](#r4-the-pin-lock-is-on-or-the-pin-is-forgotten).

**Record.** Pass or fail for each step. Do not paste the body if it holds a name (that is itself the failure).

## Clock and alarms

### C21. Clock: save an alarm, snooze, stop, and bedside

Sources: `ROADMAP.md:136` "Clock: save an alarm, snooze, stop, and open bedside."

**Preconditions.** The clock is set (the status bar shows the right time). Clock is named in `OPOS_APPS`.

**Steps.**
1. On the glass: Clock, add an alarm 2 minutes ahead, save it.
2. When it rings, tap Snooze; when it rings again, tap Stop.
3. Tap Bedside, then go back.
4. Delete the alarm: tap Delete, then confirm at "Delete this alarm?".

**Observe.** The alarm listed as On; the ring screen with Stop and Snooze off the hinge; a second ring about 10 minutes
later; the bedside clock; delete taking two taps.

**Pass or fail.** Pass: each step does what it says on the first try. Fail: no ring, a ring at the wrong time, a
control under the hinge, or a delete with one tap.

**Stop and undo.** Stop ends a ring. Delete removes the test alarm.

**Record.** Pass or fail per step, and the interface used (native or browser).

### C22. An alarm rings when due, and stays silent while the clock is unknown

Sources: `ROADMAP.md:135` "An alarm rings when it is due, and it stays silent while the clock is unknown."; PR #25
"Device: NOT_RUN (D1, D2)"; PR #100 "Not measured on a phone".

**Preconditions.** Run the first half right after the session's RAM-boot, before Wi-Fi joins or anyone sets the time,
while the clock reads the image's floor or 1970. If the clock is already set when you start, record steps 2 and 3 as not
run; do not move the clock back to make the check possible.

**Steps.**
1. [READ-ONLY] On the phone: `timedatectl show -p NTPSynchronized; date`.
2. On the glass, with the clock unknown: open Clock and add an alarm 2 minutes ahead by the shown time, if the page allows it.
3. Wait 4 minutes.
4. [OWNER ONLY] [ENDS WI-FI] Turn Wi-Fi on and let the clock set (or set the time as the owner prefers), then add an alarm 2 minutes ahead and wait.

**Observe.** With the clock unknown: no time shown, the warning that alarms will not ring, and no ring. After the clock is
set: the ring at the time.

**Pass or fail.** Pass: silent while unknown with the warning shown, and a ring on time once the clock is set. Fail: a
ring while the clock is unknown, no warning, or no ring once set.

**Stop and undo.** Stop ends a ring; delete the test alarms.

**Record.** The two observations, and the clock state from step 1 (no addresses).

### C23. The alarm tone on the speaker

Sources: `ROADMAP.md:134` "The alarm tone plays on the speaker and steps from 20 percent to 70 percent."; PR #28 "The
tone has not been played on the Duo".

**Preconditions.** On this revision the alarm player's default refuses playback and no audio mixer is connected
([VOLUME.md](../packaging/duo-os/VOLUME.md)), and the roadmap says sound and vibration are not played yet. So this check
cannot pass on this revision; it is listed so that it is run when a player change lands.

**Steps.**
1. During the ring of C21, listen at the speaker.

**Observe.** Whether any tone plays.

**Pass or fail.** On this revision the expected observation is no tone, and the check stays NOT_RUN. It passes only on a
revision with a connected player, when the tone starts at about 20 percent and steps to 70 percent and stops at Stop.

**Stop and undo.** Stop on the ring screen.

**Record.** What was heard, and the revision. Vibration stays NOT_RUN: the haptic pulse is not enabled.

## Everyday apps

### C24. Notes are still on the phone after a restart

Sources: `ROADMAP.md:137` "Notes are still on the phone after a restart."

**Preconditions.** Notes is named in `OPOS_APPS`. The owner says go for a RAM-boot again from the PC.

**Steps.**
1. On the glass: Notes, New note, type a short test note, save it.
2. [OWNER ONLY] [REBOOTS] On the phone: `sudo reboot bootloader`, then the PC session RAM-boots once (as in [Recovery paths](#recovery-paths) R5).
3. After the boot, open Notes.
4. Delete the test note: Delete, then confirm at "Delete this note?".

**Observe.** The note listed and opening with the same text after the boot; delete taking two taps.

**Pass or fail.** Pass: the same text after the boot. Fail: the note missing or changed.

**Stop and undo.** Delete the test note. If the RAM-boot does not come up, follow R5 and stop.

**Record.** Pass or fail, and that the restart was a RAM-boot from the PC.

### C25. Contacts stay on the phone, including a vCard from outside

Sources: `ROADMAP.md:139` "Contacts stay on the phone, including a vCard brought in from outside."; PR #27 "Device:
NOT_RUN (D6)".

**Preconditions.** Contacts is named in `OPOS_APPS`. A test `.vcf` file with one invented person. Import is on the
browser's Contacts page; if the native shell is running, use [R2](#r2-the-native-shell-is-wrong-or-stuck) to switch to
the browser for this check.

**Steps.**
1. Copy the test file to the phone's vCard folder: `scp test.vcf droidian@[<phone-usb-address>]:.local/state/opos/vcards/` (create the folder first with `mkdir -p ~/.local/state/opos/vcards` on the phone).
2. On the glass: Contacts, Import.
3. Add one more invented person by hand.
4. After C24's restart (or the next RAM-boot), open Contacts again.
5. Delete both test people: Delete, then confirm at "Delete this person?".

**Observe.** The imported person and the hand-added person listed; both still there after the restart.

**Pass or fail.** Pass: the import works and both people survive the restart. Fail: an import error, a missing field,
or a person lost.

**Stop and undo.** Delete the test people and the test file.

**Record.** Pass or fail for the import and for the restart, and the interface used.

### C26. The calculator on the glass

Sources: `ROADMAP.md:141` "The calculator works on the glass."

**Preconditions.** Calculator is named in `OPOS_APPS`.

**Steps.**
1. On the glass: Calculator; type `12 + 30 =`, then `7 ÷ 0 =`, then `0.1 + 0.2 =`.

**Observe.** 42; "Cannot divide by zero" for the second, not a crash or a blank; 0.3 for the third (both the browser
page and the native screen use exact decimal arithmetic); every key reachable and none under the hinge.

**Pass or fail.** Pass: the three results as observed and no missed key. Fail: a wrong result, a crash, or a key that
cannot be reached.

**Stop and undo.** Nothing to undo.

**Record.** The three results and the interface used.

### C27. The apps index and More apps on the glass

Sources: `ROADMAP.md:144` "The apps index and More apps on the glass."; PR #23 "Device: NOT_RUN (D1–D11)".

**Preconditions.** `OPOS_APPS` names Clock, Contacts, Notes and Calculator (the image's drop-in). Browser interface for
More apps.

**Steps.**
1. [READ-ONLY] On the phone: `systemctl show opos-server -p Environment` (look for `OPOS_APPS`).
2. On the glass: More apps on the home page, then open each app and come back.

**Observe.** One entry per named app and none for an app not named; each opens and returns home.

**Pass or fail.** Pass: the index matches `OPOS_APPS`, and every entry opens and returns. Fail: an entry missing or extra,
or one that does not open.

**Stop and undo.** Nothing to undo.

**Record.** The list seen and pass or fail per app.

### C28. A camera still

Sources: `ROADMAP.md:142` "A camera still, and the drain while it is taken."; PR #32 "Device: NOT_RUN (D9)".

**Preconditions.** None: on this revision there is no camera code ([camera-spike.md](camera-spike.md)).

**Steps.**
1. [READ-ONLY] On the phone: `ls /opt/opos/src/opos/camera.py`.

**Observe.** The file is absent.

**Pass or fail.** With the file absent there is nothing to run, and the check stays NOT_RUN. It can be run only on a
revision that adds camera code.

**Stop and undo.** Nothing to undo.

**Record.** The revision and that the file is absent.

### C29. Bedside on the real hinge

Sources: `ROADMAP.md:143` "Folded posture, and bedside on the real hinge."; PR #26 "Bedside is a button until posture
exists".

**Preconditions.** C05 passed. No hinge angle or accelerometer reaches Linux on this phone, so book, tent and flat cannot
be told apart (NATIVE.md, The lid).

**Steps.**
1. Stand the phone half open as a tent and watch the screens for 30 seconds; then open Clock and tap Bedside.

**Observe.** Whether anything changes by itself in the tent posture (nothing is expected), and the Bedside button.

**Pass or fail.** The folded part is C05. The automatic bedside posture cannot be detected on this kernel and stays
NOT_RUN; the Bedside button is checked in C21.

**Stop and undo.** Open the phone flat.

**Record.** What changed, if anything, in the tent posture.

## On the PC, and installing

### C30. The image folder check on the Windows PC

Sources: `ROADMAP.md:59` "the script on a Windows PC and on the phone is"; PR #134 "The script on a Windows PC".

**Preconditions.** The Windows PC with PowerShell; the four images and the rootfs hash; the phone **not connected**. The
script erases the phone only with its confirm switch; no step below passes it, so it stops after its file checks and
prints "the phone was not touched".

**Steps.**
1. [READ-ONLY] Copy the image folder to `%TEMP%\opos-folder-check` and run `powershell -File deploy\duo-opos-boot.ps1 -ImageDir $env:TEMP\opos-folder-check -RootfsSha256 <sha256>`.
2. [READ-ONLY] Run the same command with `-ImageDir %USERPROFILE%\opos-duo-images` (the profile folder) and `-LogDir $env:TEMP\opos-folder-check-logs`.
3. [READ-ONLY] Run it once more with the profile folder and no `-LogDir`.
4. [READ-ONLY] Optional, where PowerShell is installed: `python -m unittest tests.test_duo_image_folder_temp` from a checkout.

**Observe.** Step 1: a `WARNING: -ImageDir ... is under the temp folder` line, the hash lines and the dry-run line. Step
2: the `-LogDir` warning only. Step 3: no warning. Each run exits 0 and says the phone was not touched.

**Pass or fail.** Pass: the warnings appear exactly where a temp folder is named, and every run stops at the dry run.
Fail: a missing or extra warning, or a run that waits for a phone.

**Stop and undo.** Remove the copied test folders under `%TEMP%` when done (they are copies).

**Record.** Pass or fail per step and the PowerShell version. No user paths in the record.

### C31. The shell installer's first phone run, with the three hashes

Sources: `ROADMAP.md:69` "phone installation remains"; `docs/duo-usb-install.md:59` "Whether the Duo's bootloader
answers this query in that form"; `docs/duo-usb-install.md:92` "a run of this script on the phone remains"; PR #129
"The first phone run is"; PR #137 (the rechecks before the erase and each RAM-boot).

**Preconditions.** The owner's explicit go in this session for an erase of userdata. Everything on the phone's userdata
is lost, Android data included. The three trusted SHA-256 values for TWRP, the boot image and the rootfs. A Linux or
macOS host with `fastboot` and `adb`, and a writable `TMPDIR` with room for one copy each of the TWRP and boot images.
Battery at 30 percent or more. Since PR #137 the script, with `--yes --erase-userdata`:
- refuses before the erase unless `fastboot getvar unlocked` answers exactly one `unlocked: yes`; whether the Duo's
  bootloader answers in that form is NOT_RUN, and step 1 below is where that real answer is first read;
- copies the TWRP and boot images, when they are checked, into a private folder and RAM-boots those copies, not the
  named files;
- rehashes the three named files and the two copies directly before `fastboot format:ext4 userdata`, the TWRP copy
  before its RAM-boot, the boot copy in TWRP and again before the final RAM-boot, and the rootfs before the push;
- asks the device for its product again after the return to the bootloader, and refuses the final RAM-boot unless it is
  the product checked before the erase.

It still does not check the slot or the battery, does not arm the misc brake and does not validate the raw ext4
contents; the guarded `deploy/duo-opos-boot.ps1` route remains the one to prefer. A refusal after the erase leaves
userdata erased with no system on it, while the bootloader and fastboot still answer. The phone in Boot Select by the
[button steps](duo-usb-install.md#button-steps), where the orange 'unlocked' warning must have shown.

**Steps.**
1. [READ-ONLY] On the host: `fastboot devices` and `fastboot getvar product` and `fastboot getvar unlocked`. If the unlocked answer is not exactly one `unlocked: yes` line, stop here: the script would refuse with nothing erased; record the form of the answer and do not go on.
2. [READ-ONLY] Preview: `sh deploy/duo-rootfs-install.sh <twrp.img> <boot.img> <rootfs.img>` with no other arguments; it prints the steps and touches nothing.
3. [OWNER ONLY] [ERASES] [REBOOTS] Run: `sh deploy/duo-rootfs-install.sh --yes --erase-userdata --twrp-sha256 <hash> --boot-sha256 <hash> --rootfs-sha256 <hash> <twrp.img> <boot.img> <rootfs.img>`.

**Observe.** Step 1: exactly one `fastboot` line, `product: surfaceduo`, and the exact form of the unlocked answer
(expected `unlocked: yes`). Step 3, in the script's output: no `changed after it was checked` refusal before the format;
the same serial used by fastboot and by TWRP's adb (every adb command is `adb -s` with it); the RAM-boots naming the
private copies, not the named files; `/data` mounted in TWRP; the pushed `/data/rootfs.img` passing the hash and size
check; the return to fastboot and the second `getvar product` with the same product; the final RAM-boot starting
Droidian.

**Pass or fail.** Pass: all five confirmations from PR #129 hold, the unlock and product checks pass on the real phone,
no rehash refuses, and the phone reaches the OPOS session. Fail: any refusal, `verify failed`, a serial mismatch, a
different product after the reboot, or a crash. A refusal before the format is safe; write it down.

**Stop and undo.** Before the format nothing has changed: stop. After the format, the data is gone and cannot be
brought back; if the script stops (a rehash or product refusal after the erase included), do not run it again in a
loop: the phone is still in the bootloader or TWRP, so follow R5 and the
[Restore to Android](../packaging/duo-os/README.md#restore-to-android) section. If the boot crashes, stop and do not
retry. The script removes its private copies when it exits.

**Record.** The form of the `getvar unlocked` answer, pass or fail for each of the five confirmations, the unlock,
rehash and product-after-reboot checks, the script's last line, and the date. Never record the serial.

### C32. The persistent kernel

Sources: `ROADMAP.md:40` "a persistent-kernel start and later gates remain"; PR #113 "Starting from powered off is
NOT_RUN"; PR #116 "Every device step remains NOT_RUN"; PR #119 "The rebuild, its start and the second boot_a write are
NOT_RUN".

**Preconditions.** Owner only, gate by gate. This page adds no steps to it.

**Steps.**
1. [OWNER ONLY] Follow [duo-persistent-install.md](duo-persistent-install.md) from the next gate that has not been run; each gate there says when the owner must say go.

**Observe.** What that runbook says to observe at each gate.

**Pass or fail.** As that runbook defines for each gate.

**Stop and undo.** As that runbook says, including its return to Android.

**Record.** In that runbook's own record, and the gate reached here.

## Recovery paths

Use these when a check goes wrong. The aim is that the phone is never left dark or unreachable.

### R1. A screen is dark

1. Open the lid fully, then wait 10 seconds: the shell retries a failed restore after 1 s, 2 s and 4 s, then every 5 s.
2. Over the USB link, read both panels: `cat /sys/class/backlight/panel*-backlight/brightness /sys/class/backlight/panel*-backlight/bl_power`.
3. A `brightness` of 0 is the lid blank. As `droidian`: `PYTHONPATH=/opt/opos/src XDG_RUNTIME_DIR=/run/user/32011 python3 -m opos.native.blank --restore; echo rc=$?` (rc 0 means both panels are lit).
4. A `bl_power` of 4 is the power-key blank. Press the power key briefly once; or, if the image has sudo (a dev image), `sudo /usr/libexec/opos/opos-unblank`, which writes `bl_power` 0 to both panels. Do not press the power key for a lid blank: it would add a power-key blank on top.
5. If both screens are still dark: R5.

### R2. The native shell is wrong or stuck

1. On the glass, if it answers: Settings, "Use the old interface".
2. Over ssh, with sudo: `sudo touch /etc/opos/native-off`. The browser is the interface from the next session start;
   `sudo systemctl restart opos.service` starts the session again now (both screens go dark for a few seconds; that
   restart has not been run on the phone yet, so watch the screens and use R1 if they stay dark).
3. To return to the native shell later: `sudo rm /etc/opos/native-off` and start the session again.

### R3. Wi-Fi is off or lost

The USB link stays up. Over ssh: `nmcli radio wifi on`, or `python3 -m opos.net wifi on`; then join the owner's network
again from the glass (C12) or with `nmcli connection up id '<name>'`. Airplane mode, if it was turned on:
`rfkill list` to see it.

### R4. The PIN lock is on, or the PIN is forgotten

There is no reset button. Over ssh, with sudo: `sudo mv /etc/systemd/system/opos-server.service.d/40-opos-lock.conf
$HOME/40-opos-lock.conf.off; sudo systemctl daemon-reload; sudo systemctl restart opos-server`. The lock is then not
enforced. To clear the test PIN as well: `mv ~/.local/state/opos/lock.json ~/.local/state/opos/lock.json.off`.

### R5. Last resort: leave the running system and RAM-boot again

1. If ssh works: `sudo reboot bootloader` on the phone. Never plain `reboot`.
2. If not: the [button steps](duo-usb-install.md#button-steps): hold Power about 20 seconds until the screen stays dark,
   then with the USB cable in hold Volume-down, add Power, and keep Volume-down held until Boot Select. Never pick
   Start, Recovery Mode or Factory data reset.
3. From the PC: re-arm the misc brake and RAM-boot once, as the PC session did for this image
   ([RAM-boot only, and the misc brake](../packaging/duo-os/README.md#ram-boot-only-and-the-misc-brake)). If it
   crashes, stop and do not retry.

## Covered elsewhere, and not phone checks

| Source | What it says is NOT_RUN | Where it is handled |
|---|---|---|
| PR #13 | the on-device checklist lines | [duo-on-device-checklist.md](duo-on-device-checklist.md); C03 covers its exposure lines |
| PR #39 | the VoLTE checks | [duo-volte.md](duo-volte.md) |
| PR #50 | whether a phone grants the four NetworkManager actions | step 5 of [Live checks, in order](duo-os-final.md#live-checks-in-order) |
| PR #53 | folded behaviour of the app panes | C05 and C29 |
| PR #29, #30, #94 | Notes, the calculator, the Notes delete on a phone | C24 and C26 |
| PR #49, #81, #104 | the D1 to D11 table and on-device checks in general | this page, C21 to C29 |
| PR #113, #116, #119, #128 | the persistent-install steps | C32 |
| PR #3 | a model advisory run | not a phone check |
| PR #52, #70, #101, #103, #131 | CI runs, a full suite run, the Linux run, browser tests on the Mac | not phone checks |
| PR #104 | the optional repository guard | not a phone check |
| PR #134 | the PowerShell tests on the Mac | not a phone check; C30 step 4 runs them on the PC |
| PR #136 | the review pipeline steps | not a phone check |

## Results

Copy this table into the evidence record for a session, fill Date, Result, Interface and Evidence for each check run,
and leave the rest as they are. Result is PASS, FAIL or NOT_RUN; write PASS or FAIL only for a check that was run on the
phone and recorded. Interface is native, browser or PC. Evidence is a link to the session record, not to a pull request.
This copy stays NOT_RUN.

| ID | Check | Date | Result | Interface | Evidence | Notes |
|---|---|---|---|---|---|---|
| C01 | Image build and verification | | NOT_RUN | | | |
| C02 | Image identity on the phone | | NOT_RUN | | | |
| C03 | USB root shell closed, ssh key-only, debug port on loopback | | NOT_RUN | | | |
| C04 | Native shell starts, answers taps and hands over | | NOT_RUN | | | |
| C05 | Lid close blanks and lid open restores | | NOT_RUN | | | |
| C06 | Restore retries and the power-save mode | | NOT_RUN | | | |
| C07 | Current with the lid closed, drain blanked | | NOT_RUN | | | |
| C08 | Power key blanks and lights | | NOT_RUN | | | |
| C09 | Volume keys change only the simulated volume | | NOT_RUN | | | |
| C10 | Opening the Network screen with real radios | | NOT_RUN | | | |
| C11 | Join with a typed password | | NOT_RUN | | | |
| C12 | Join a saved network | | NOT_RUN | | | |
| C13 | A long password on the keyboard | | NOT_RUN | | | |
| C14 | A hidden network, and editing its name | | NOT_RUN | | | |
| C15 | Forget, with its two taps | | NOT_RUN | | | |
| C16 | Disconnect, its read, and a roam between taps | | NOT_RUN | | | |
| C17 | Wi-Fi off with two taps, on with one | | NOT_RUN | | | |
| C18 | The holds, and the long-wait sentence | | NOT_RUN | | | |
| C19 | The lock PIN on the glass | | NOT_RUN | | | |
| C20 | What a locked phone shows and refuses | | NOT_RUN | | | |
| C21 | Clock: save, snooze, stop, bedside | | NOT_RUN | | | |
| C22 | Alarm when due, silent while the clock is unknown | | NOT_RUN | | | |
| C23 | The alarm tone on the speaker | | NOT_RUN | | | no player on this revision |
| C24 | Notes after a restart | | NOT_RUN | | | |
| C25 | Contacts and a vCard | | NOT_RUN | | | |
| C26 | The calculator | | NOT_RUN | | | |
| C27 | The apps index and More apps | | NOT_RUN | | | |
| C28 | A camera still | | NOT_RUN | | | no camera code on this revision |
| C29 | Bedside on the real hinge | | NOT_RUN | | | no hinge angle on this kernel |
| C30 | The image folder check on the Windows PC | | NOT_RUN | | | |
| C31 | The shell installer's first phone run | | NOT_RUN | | | erases userdata |
| C32 | The persistent kernel | | NOT_RUN | | | see duo-persistent-install.md |
