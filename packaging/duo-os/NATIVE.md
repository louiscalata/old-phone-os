# The native shell

An alternative to the browser kiosk on the Surface Duo image: a GTK 4 window, drawn by the phone's GPU, that covers both
screens. The browser interface stays as the fallback and is never running at the same time.

It is not verified on a phone. The rules it follows are tested on a computer without a display; the window, the touch
handling and the frame pacing have not been run on the phone, and no image with it has been built or started.

## What is where

| File | Role |
| --- | --- |
| `src/opos/native/shell.py` | the window, the gestures and the polling of the OPOS server |
| `src/opos/native/screens.py` | the screens: Lock, Home, Phone, Messages, Clock, Contacts, Notes, Calculator, Call log, Settings, Network |
| `src/opos/native/logic.py` | what the screens decide for themselves, with no toolkit, so it is tested anywhere |
| `src/opos/native/netlogic.py` | the same for the Network screen: its rows, sentences and replies, and the typed password |
| `src/opos/native/layout.py`, `ui.py` | the hinge gap, swipe and drag rules; the few widgets and the stylesheet |
| `src/opos/native/posture.py` | reads the lid switch, read-only; closed or open is all Linux can tell on this phone |
| `src/opos/native/blank.py`, `displayconfig.py` | both screens dark while the lid is closed, and lit as before when it opens |
| `packaging/duo-os/opos-native` | the session launcher: keeps the shell running and hands over to the browser when it cannot |

The rules for battery, calls, messages, alarms, notes and storage stay in the OPOS server. The shell draws what the server
last said and asks it to act.

## How a start is chosen

`opos-kiosk` is still the one client of phoc. At its start it hands its own process to `/usr/libexec/opos/opos-native` when
that file is installed and `/etc/opos/native-off` does not exist. The launcher goes back to the browser kiosk (the same
process, with `OPOS_NATIVE_TRIED` set, so it never hands back) when:

- GTK 4 or PyGObject cannot be loaded;
- first-run setup is not finished (no `firstrun.json` in the owner's config folder, the one the server writes);
- the PIN lock is on (`OPOS_LOCK=1`, or the shell found the server enforcing it and ended with status 11): the shell has no
  PIN screen;
- the owner chose "Use the old interface" in Settings (status 10);
- the shell ended 3 times in a row within 20 s, or did not report a window within 30 s, or ended 6 times in 10 minutes.

`/etc/opos/native-off` keeps the browser as the interface from the next time the session starts. The two-screen span
(`OPOS_SPAN`) comes from `/etc/default/opos-native`, which the image build writes with the server's own value. The
everyday apps (Clock, Contacts, Notes, Call log) get pages only when `OPOS_APPS` names them, as on the server: the
launcher reads the same `Environment=OPOS_APPS=` line from the server's drop-in (`OPOS_NATIVE_APPS_CONF`, default
`/etc/systemd/system/opos-server.service.d/20-opos-apps.conf`); absent or empty means none.

## The rules the screens follow

- Every frame is a complete, accurate snapshot, and each tap is answered in one frame. Nothing slides or fades:
  `ANIMATE` in `ui.py` is off, and any movement is a skin to add later on top of that.
- Every reading pane (Contacts, Notes, Call log, Settings and the others) scrolls by finger. `kinetic_scrolling` is always
  on, whatever `ANIMATE` says, because in GTK 4 it is what turns on touch dragging in a scrolled view at all. GTK's own
  scroll inertia comes with it and is kept: the scrolled content is already in its final state, so no frame shows a
  wrong value; the coast only changes which part of it is in view.
- The battery sheet, the lock screen and the bottom edge of an app page follow the finger while it is down (direct
  manipulation), exactly as far as the finger has moved. When it lifts, the next frame shows the result, complete: the
  sheet open or closed, Home or the page it was on. There is no easing, no coasting and no spring-back on a timer.
- Deleting an alarm, a person or a note takes two taps ("Delete this alarm?", "Delete this person?", "Delete this
  note?"), as in the browser. Add alarm and Call are busy until the server's answer is on screen, so a second tap sends
  nothing.
- Phone says the emergency sentence of the shared risk notice ("OPOS cannot place an emergency call; keep another way
  to call for help.") whenever calls are real; Settings shows the shared notices, as the browser's About does.
- A reply to a request changes the screen only if no newer reply has. When the reply to a change (a call, a hang-up, a
  send) is drawn, a poll sent before that moment is refused too, so a state from before the call can never bring Call
  back while the call is up. A failed poll keeps the last good state and says the connection is lost; it never shows an
  empty phone.
- Notes opens only the note its last open or New note request asked for, matched by request number.
- A change the owner asks for shows only when the server has confirmed it. A typed message, a new contact or a new alarm
  stays where it is until then; a refusal (an empty battery, a number the practice phone will not dial) is shown in the
  server's own words.
- Calls, messages and the Home card say "practice" while the modem is the practice one. A clock nobody set shows no time and
  warns that alarms will not ring.
- The shell tells the launcher it is ready only after its window is on screen, has drawn a frame and has finished
  warming up.

## The Network screen

Settings has a Network button (Home has no tile for it). The screen shows the radios in the server's own sentences
(Wi-Fi, airplane mode, Bluetooth, mobile data) and the networks it can see, connected first, then by signal, then the
saved ones not in range. It can scan again, join (a typed password, or none for a saved or open network), join a hidden
network by name, forget, disconnect, and turn Wi-Fi on or off. It uses the server's existing actions, the same ones the
browser's `/network` page uses: `net_status`, `net_scan`, `net_join`, `net_forget` and `net_wifi` (`src/opos/net.py` does
every change; nothing on the server side changes). Changes work only where the server runs with `OPOS_NETWORK=1`;
elsewhere the server refuses them and the screen shows its sentence.

- Nothing is asked while the screen is not showing. On opening it reads the radios once, then scans once; while it shows
  it reads the radios again every 10 s and never rescans on its own. Opening it, polling and choosing a row change
  nothing.
- Forget, Disconnect and Turn Wi-Fi off take two taps ("Forget this network?", "Disconnect now?", "Turn Wi-Fi off
  now?"); choosing another row, Cancel or leaving the screen disarms them. Every newer radios read or list that is
  drawn finds the chosen row again by name and takes its new state, or closes the choice when the row is gone; a Forget
  or Disconnect prompt is dropped when that row's state changed or when the radios name another connected network.
  The server's disconnect names no network (it drops whatever is connected), and the radios read on screen can be up
  to 10 s old, so the second Disconnect tap sends nothing by itself. If the read on screen names another network, or
  none is known, it stops there and says why. Otherwise the screen reads the radios again (skipping the server's
  few-second cache), says "Checking that <network> is still the connected network...", and stays busy; it sends the
  disconnect only when that read, asked after the tap, still names the network on the prompt, and then says
  "Disconnecting from <network>...". If the phone moved to another network between the two taps, or the read does not
  say which network is connected, nothing is sent and the screen says why. Cancel, choosing a row, Other network or
  leaving the screen while that read is waiting ends the confirmation, so the read then sends nothing. What remains
  is the moment between the server's read and its disconnect; closing that needs the server's action to name the
  network, which this change does not do. While the Wi-Fi state is not known the switch is greyed out and sends
  nothing. Turn Wi-Fi on takes one tap.
- The main button and the Wi-Fi button act only on what the owner has seen them say. What one of them says or does
  can change for a reason other than the owner's own tap (a newer radios read, a newer list, or a change's answer),
  for example when an armed or unarmed Disconnect turns into Join, or Turn Wi-Fi off into Turn Wi-Fi on. For 1.5 s
  after that, a tap on it sends nothing and the screen says "This button just changed. Check what
  it says now, then tap again." A tap after that acts as the button says. The Wi-Fi button is held the same way when it
  appears by itself (a newer list drops the chosen row, or a change's answer closes the choice, and the screen goes back
  to its idle buttons). The rows follow the same rule: when a newer list puts another network in a place, or changes
  what a tap there would offer (for example from "needs a password" to "saved"), a tap on a row in the next 1.5 s
  chooses nothing and the screen says "The list just changed. Check it, then tap the network again." A list that only
  brings new signal strengths, in the same order, holds nothing. The owner's own taps start no hold, so the second tap
  of a prompt they armed, or Join right after choosing a row, acts at once.
- Each visit to the screen is numbered; leaving starts a new one. A radios read or a list sent for an earlier visit is
  dropped and starts nothing. What a visit read is forgotten when the screen is left, because the phone can move to
  another network or the Wi-Fi be switched while the screen is not showing (the browser's `/network` page, or
  NetworkManager roaming). So the next visit starts at the floor described below: until its own first radios read is
  drawn the screen says "Reading the radios again to show what the change did...", the Wi-Fi button says "Wi-Fi: not
  known yet" and sends nothing, and a Disconnect prompt sends nothing. Until its own first list is drawn the list has
  one row, "Reading the list again...", which chooses nothing and says "The list is being read again. Wait for it,
  then tap a network."; the first list that replaces it holds row taps for 1.5 s like any newer list that changes a
  place. A change's answer from an earlier visit says its outcome, sets the same floor as below
  in the same cases, frees the screen and, while the screen shows, reads the radios again. It does not
  touch the choice or the typed password of the visit now showing. A scan that opening the screen had to put off while
  an earlier request was still waiting runs once the screen is free, so the list is read again too.
- The radios and the scan each keep their own order: an older answer never replaces a newer one. Their requests are
  numbered from one count, so between a radios read and a list the screen knows which was asked later. Whether the
  chosen row is the connected one follows the later-asked of the two on screen: a list asked before the radios read on
  screen does not turn Disconnect back into Join (or Join into Disconnect), and neither does a radios read asked
  before the list on screen, whichever answer arrives last. A row tapped in a list older than the radios read is
  opened as that read says it is. The order is the order of asking; the server may still answer two requests sent
  close together in the other order, and the next read corrects that. Every answer the
  server gives to a change sets a floor: a confirmed change, one with no answer in time on either side (the screen's
  own wait, or the server's "The network service did not answer in time. Try again.": NetworkManager may still be
  going on), and a failed one (a failed join may already have dropped the old network). Only a refusal before anything
  ran (an HTTP 4xx such as 429, "too many tries") sets none. At the floor every read sent before it is refused when it
  comes, and the radios read drawn before it is no longer trusted: until a read sent after the change is drawn the
  screen says "Reading the radios again to show what the change did...", the Wi-Fi button says "Wi-Fi: not known yet"
  and sends nothing, and a Disconnect prompt sends nothing (the screen says the connected network is not known right
  now). The radios (skipping the server's few-second cache) and the list are read again after every answer.
- A join waits up to 100 s for the server, which may take about 95 s; a change with no answer in time is not called
  failed: the screen says the change may still be going on and reads the radios again.
- The password is typed on the on-screen keyboard into `netlogic.Secret`, which prints and formats as
  `<password hidden>` and cannot be pickled or copied. It belongs to the one network name its keys were typed for.
  For a hidden network, any edit of the name (a key added or deleted) empties the password, and the screen says "The
  name changed, so the password typed for the old name was cleared. Type the password again." A join sends a password
  only with the name it was typed for; if the two differ, nothing is sent and the password is asked for again. It is
  shown as bullets, sent once in the join request to the
  server on loopback, and emptied before the request is handed to the sending thread, on every answer to a change sent
  in the same visit (success, error or no answer in time), on Cancel, on choosing another row, when a newer list drops
  the chosen row or no longer asks for a password, and when the screen is left (the lid closing leaves it too). While a change or a
  shown scan (Scan again, or the one on opening) is waiting, password keys are not kept, and the screen says "Keys are
  not kept while OPOS is busy. Type the password once it answers." The screen stays busy until a change's answer comes,
  even after leaving and coming back, so nothing typed in a later visit can be waiting when an earlier visit's answer
  arrives. If the sending thread cannot be started, nothing is sent: the screen frees itself and says
  "That did not go through." The sending thread drops its copy once the request has gone, and hides any text of the
  reply that would contain it. Nothing in the screen prints, logs or saves it; the
  server hands it to NetworkManager (see [network.md](../../docs/network.md)), which keeps a joined network's password.
  A Python string cannot be wiped from memory, so "emptied" means no reference is kept, not that the bytes are erased.
- Company or school (802.1X) and WEP networks are listed and explained, and offer no Join.

This build has not been run on the phone. On November 13, 2026 the owner checked the PC session's patch 0013 on the Duo
under real GTK, with only `net_status` and `net_scan` sent: that build showed the real radios and scan, a row opened its
choice, and the poll stopped after leaving the screen. This port changes what was checked (two taps for Forget,
Disconnect and Turn Wi-Fi off, a greyed-out Wi-Fi switch while its state is unknown, `leave()` on every page change, a
fresh status read after a change, and per-action waits), so that check does not carry over. **NOT_RUN** on the phone for
this build: opening the screen with real radios, a real join (typed or saved), a hidden network, editing a hidden
network's name after typing its password, forget, disconnect (with its read before sending, and a roam between the two
taps), Wi-Fi off and on, the two-tap prompts, the long-wait sentence, and the keyboard layout for a long password. The
password-per-name rule, the shared order of radios reads and lists, and the read before a disconnect were tested only
on the stand-in toolkit with an invented network (`tests/test_native_net.py`, `tests/test_native_net_fuzz.py`). Several can end the
phone's own connection, so the owner runs them.

## The lid

The kernel shows one posture signal on this phone: an input switch named "Surface Duo Lid". `posture.py` opens it
read-only and non-blocking (never grabbed, nothing written; nothing under `/sys/kernel/debug` is read). When the lid
closes the shell locks; 350 ms later it writes 0 to the two panels' `brightness` files, after saving their exact values in
`$XDG_RUNTIME_DIR/opos-saved-brightness`. Opening the lid writes them back. The shell also owns
`org.gnome.Mutter.DisplayConfig` on the session bus, if nobody else does, and serves its `PowerSaveMode` property, which
the port's lid daemon sets; it never replaces another owner and lets a later one take the name over. If the lid switch is
missing or cannot be read the shell says so once and does not blank on its own. No hinge angle or accelerometer reaches
Linux on this phone, so book, tent and flat postures cannot be told apart and are not guessed.

The screens are lit again when the lid opens, when the shell starts with the lid not closed, when its lid signal is lost,
when the lid daemon sets `PowerSaveMode` to 0, when it ends for any reason it can handle (SIGTERM included), after every
end of the shell, clean ones included (the launcher, before its 2 s pause; a no-op when nothing is dark; the next shell
darkens them again if the lid is closed, so a closed phone stays lit until then: about 2 minutes at most while the
server refuses connections, and up to about 6 minutes when each of the launcher's 120 server probes waits its 2 s
timeout plus its 1 s pause), and
before the browser starts: by the launcher when it hands over, and by `opos-kiosk` itself when the launcher never ran
(`/etc/opos/native-off`). Both run `python3 -m opos.native.blank --restore`, which tries for up to 5 s (wall time, the tries included) and exits non-zero
if it could not light both panels; the launcher logs that, and neither lets it stop the browser. When that restore gave
up, `opos-kiosk` runs `python3 -m opos.native.blank --restore --for 120` in the background (at most one try every 10 s,
and no try starts more than 2 minutes after the first, on the monotonic clock), so Chromium starts at once. A panel that is missing, unreadable or refuses the write during a restore is
listed as failed (the saved file, if any, is kept), and the shell tries again 1 s, 2 s and 4 s later, then every 5 s, for
as long as the lid is not closed and nobody has asked for dark since. If the kernel drops lid events (`SYN_DROPPED`) the
watcher ignores the lid events from that point in the read and reads the switch once with `EVIOCGSW`; if that read fails,
or the first `EVIOCGSW` read when the watcher starts fails, the lid counts as unknown, the watcher ends and the screens
are lit. A blank that finds the saved file already there (a crash between
saving and writing, or a panel that came back on with the lid closed) writes 0 again and keeps the saved values. A
restore is capped only at each panel's own `max_brightness`. With no saved file, a panel whose `brightness` file is there
but cannot be read is written its default (capped by its readable `max_brightness`), since it may be dark, and stays listed
as failed until a read shows it lit. A `PowerSaveMode` of 1 to 3 that the shell refuses (the lid reads open) is not kept:
the property reads 0, because the screens stay lit; every value still reaches the blank or the restore. After the shell
restores on its own (the lid opened), the property keeps the last accepted value until the daemon sends 0, and a lid
watcher that stops is not started again (the screens are lit and the lid is unknown until the shell restarts). Not verified on a phone: no lid close, blank or restore on the Duo is recorded here (NOT_RUN), and the
current drawn with the lid closed has not been measured.

## Assistants are deferred

An assistant screen (Claude, Codex, Grok with the owner's own key) was part of the first import and has been removed. The
owner's order is to finish the phone first and add AI later. Nothing in the shell talks to a model or keeps a key; the
repository's check that `src/` names no model or host outside the Meta set stays green with it.

## Image

`build-image.sh` installs the launcher and `/etc/default/opos-native`; `verify-image.sh` checks them; the release tool lists
the launcher as an overlay file; `opos-selftest` accepts the native shell as the interface when it is the one running.
