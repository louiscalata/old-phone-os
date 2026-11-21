# The native shell

An alternative to the browser kiosk on the Surface Duo image: a GTK 4 window, drawn by the phone's GPU, that covers both
screens. The browser interface stays as the fallback and is never running at the same time.

It is not verified on a phone. The rules it follows are tested on a computer without a display; the window, the touch
handling and the frame pacing have not been run on the phone, and no image with it has been built or started.

## What is where

| File | Role |
| --- | --- |
| `src/opos/native/shell.py` | the window, the gestures and the polling of the OPOS server |
| `src/opos/native/screens.py` | the screens: Lock, Home, Phone, Messages, Clock, Contacts, Notes, Calculator, Converter, Call log, Settings, Network |
| `src/opos/native/logic.py` | what the screens decide for themselves, with no toolkit, so it is tested anywhere |
| `src/opos/native/netlogic.py` | the same for the Network screen: its rows, sentences and replies, and the typed password |
| `src/opos/native/layout.py`, `ui.py` | the hinge gap, swipe and drag rules; the few widgets and the stylesheet |
| `src/opos/native/posture.py` | reads the lid switch, read-only, and names the posture from the lid and the hinge angle: closed, book, flat, tent or back |
| `src/opos/native/hinge.py` | reads the hinge angle and the accelerometer from the phone's sensor service (sensorfw) over D-Bus, read-only, with adaptive polling |
| `src/opos/native/facing.py` | in back mode, which screen faces you, from the accelerometer |
| `src/opos/native/blank.py`, `displayconfig.py` | both screens dark while the lid is closed, and lit as before when it opens; in back mode, the screen that does not face you dark |
| `packaging/duo-os/opos-native` | the session launcher: keeps the shell running and hands over to the browser when it cannot |

The rules for battery, calls, messages, alarms, notes and storage stay in the OPOS server. The shell draws what the server
last said and asks it to act. The Calculator and the Converter have no state to keep, so they are worked out in the shell
itself (the Converter with the server's own converter code).

## How a start is chosen

`opos-kiosk` is still the one client of phoc. At its start it hands its own process to `/usr/libexec/opos/opos-native` when
that file is installed and `/etc/opos/native-off` does not exist. The launcher goes back to the browser kiosk (the same
process, with `OPOS_NATIVE_TRIED` set, so it never hands back) when:

- GTK 4 or PyGObject cannot be loaded;
- first-run setup is not finished (no `firstrun.json` in the owner's config folder, the one the server writes);
- the PIN lock is on (`OPOS_LOCK=1`, or the shell found the server enforcing it, or the server did not say whether it
  has one 5 times in a row, and the shell ended with status 11): the shell has no PIN screen;
- the shell ended 3 times in a row within 20 s, or did not report a window within 30 s, or ended 6 times in 10 minutes.

An older shell can still return status 10. The launcher treats it as an unexpected exit and applies the normal restart and failure limits.

`/etc/opos/native-off` keeps the browser as the interface from the next time the session starts. The two-screen span
(`OPOS_SPAN`) comes from `/etc/default/opos-native`, which the image build writes with the server's own value. The
everyday apps (Clock, Contacts, Notes, Converter, Call log) get pages only when `OPOS_APPS` names them, as on the server: the
launcher reads the same `Environment=OPOS_APPS=` line from the server's drop-in (`OPOS_NATIVE_APPS_CONF`, default
`/etc/systemd/system/opos-server.service.d/20-opos-apps.conf`); absent or empty means none.

The shell asks one server: the phone's own, at `http://127.0.0.1:8765`. `OPOS_SERVER_URL` may name another port on
127.0.0.1 and nothing else; a value of any other shape is refused and said once in the log, and the phone's own server
is asked. `OPOS_LOCAL_URL` (the Helper's model server) is not read. No proxy from the environment is used, and a
redirect to another address is not followed, so the shell's token and the owner's actions stay on the phone.

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
- Deleting an alarm or a person takes two taps ("Delete this alarm?" or "Delete this person?"). In Notes,
  Delete this note opens a confirmation in the action pane. Cancel keeps the draft and Yes, delete sends the request.
  The reading pane keeps its size and editing pauses while the confirmation is open. Add alarm and Call are busy
  until the server's answer is on screen, so a second tap sends nothing. When the answer to a call or a hang-up does
  not come, the button stays busy until a state asked for after that is drawn, so while the server cannot be reached it
  stays busy; the screen then no longer says it is asking. It
  says "No answer about that call. OPOS is being asked whether it went out; Call comes back when it says." or "No
  answer about hanging up. The call may still be up; OPOS is being asked, and the button comes back when it says." A
  request that could not be sent at all (no thread could be started) is answered at once as not sent, so no button
  waits for an answer that cannot come.
- Phone says the emergency sentence of the shared risk notice ("OPOS cannot place an emergency call; keep another way
  to call for help.") whenever calls are real; Settings shows the shared notices, as the browser's About does.
- A reply to a request changes the screen only if no newer reply has. When the reply to a change (a call, a hang-up, a
  send) is drawn, a poll sent before that moment is refused too, so a state from before the call can never bring Call
  back while the call is up. A failed poll keeps the last good state and says the connection is lost; it never shows an
  empty phone.
- Notes opens only the note its last open or New note request asked for, matched by request number.
- Converter (`OPOS_APPS` names `convert`) works its measures and conversions out in the shell's own process, with the
  code the server runs for `/api/convert` and the browser's `/convert` page (`opos.convert` and that page's own handler:
  one table of factors, one set of refusals). It needs no server and keeps working while the server is down; only if that
  code cannot be loaded does it ask the server's `/api/convert` instead. A conversion has no state, so this does not move
  any of the server's rules into the shell, and the Calculator already works the same way. The answer shown is that
  code's own text and line, and a refusal is said in the browser page's words ("Enter a number.", "Length, mass and
  volume stay at zero or more.", "That is below absolute zero."). The shell never runs while the server has a PIN lock
  (it hands over to the browser), so working a conversion out here opens nothing a lock keeps closed. The amount is typed
  on the screen's own number pad;
  Measure, From and To step through the converter's units, and Swap exchanges From and To. One conversion is asked at a
  time: Convert says "Converting" and a second tap sends nothing. While it is on its way the amount and the units cannot
  change, and an answer is drawn only when it is the answer to the last request, for the measure and units on screen;
  any later change clears it. A conversion changes nothing on the server, so its answer sets no floor and asks for no
  refresh. A conversion that cannot be worked out says "That conversion is not available.", never an error's own text.
  Nothing is stored: the amount and the last answer live in the page while the shell runs.
- A change the owner asks for shows only when the server has confirmed it. A typed message, a new contact or a new alarm
  stays where it is until then; a refusal (an empty battery, a number the practice phone will not dial) is shown in the
  server's own words.
- Calls, messages and the Home card say "practice" while the modem is the practice one. A clock nobody set shows no time and
  warns that alarms will not ring.
- The shell tells the launcher it is ready only after its window is on screen, has drawn a frame and has finished
  warming up.
- The shell has no PIN screen, so it draws a state only together with the server's answer "no PIN lock" (`/api/lock`
  answers 404). The question is asked beside the first state, again while it has no answer, and again after the server
  went away. The answer "yes" ends the shell for the browser. No answer (the check failed or timed out) is never read
  as "no": the state is not drawn, the shell goes to its own lock page and stays there, and that page says "OPOS has
  not said whether this phone has a PIN lock. Waiting for its answer." After 5 times in a row the shell ends with
  status 11 and the browser shows whatever the server enforces. A lost connection is not counted: the last good state
  stays, as before. The shell's own lock page is not a lock: it opens with a swipe, and only the server's PIN lock,
  shown by the browser, keeps anyone out.

## Date & time

Open **Date & time** from native Settings. Pick a region, choose a city and use **Use this zone** to save it. The selected
city scrolls fully into view, and a city already in view keeps its position. Changing the zone changes when alarms ring in
local time. The page shows the clock status the server reports and gives no control to set the system clock.

The native shell uses the existing `GET /api/timezone` and `POST /api/timezone` from the first-run setup. When the server
confirms a save, the shell applies that zone to its own clock without restarting the browser. The page shows when the
server cannot supply the zone list. Built-in zone rules can lag changes to local law.

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
missing or cannot be read the shell says so once and does not blank on its own. The kernel gives Linux no hinge angle or
accelerometer on this phone; both come from the phone's sensor service (see "The hinge and back mode"), and while it gives
none the shell knows only open and closed and does not guess book, tent or flat.

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
restores on its own (the lid opened), the property keeps the last accepted value until the daemon sends 0. A lid
watcher that stops (the switch is missing, cannot be read, or its thread cannot be started) is said once in the log;
the screens are lit, the lid counts as unknown, and the switch is looked for again 5 s, 15 s and 30 s later, then every
60 s, for as long as the shell runs. The new watcher's first report is the switch's state at that moment and is handled as any
report: closed locks and darkens, open lights. Looking again darkens nothing by itself, and nothing a watcher says
after it was lost is listened to.

A lid switch that flaps (it is found, reports, and is lost again, over and over) is handled one loss at a time, with no
limit and no memory of earlier losses. The screens are lit at each loss. Each report starts the waits over, so the next
look is 5 s after each loss, and the log gets one line for each loss that follows a report (a switch that stays away
is said once). A closed phone with a flapping switch therefore alternates: dark from each report of "closed" until the
next loss, then lit for about 5 s or more, until the next watcher reports "closed". An open phone stays lit throughout. This
is the safe side: the cost is a closed phone that lights up again and again and a log that grows by a line at each
loss; no order of losses and reports leaves an open phone, or one whose lid is unknown, dark. The shell does not slow
down or give up on a switch that keeps flapping. Not verified on a phone: no lid close, blank or restore on the Duo is recorded here (NOT_RUN), and the
current drawn with the lid closed has not been measured.

## Follow-ups of November 14

Four changes, all checked on a computer with the stand-in toolkit only (`tests/test_native_followups.py`); **NOT_RUN**
on the phone, like the rest of the shell: the server's address (`OPOS_SERVER_URL`, never `OPOS_LOCAL_URL`), what Phone
says while an answer is missing, the hold on the lock page while the PIN lock question has no answer, and the lid
watcher that is looked for again.

Left for the owner, with no code here:

- A PIN screen in the shell. The shell still has no PIN screen, and with the PIN lock on the browser is the interface.
  What it should look like, how it should count wrong tries with the server, and what the lid closing should do to the
  server's lock are the owner's to decide.
- The server's lock is not set when the lid closes or when the shell goes to its own lock page; the shell has no part
  in it while the lock is off, and is not running while it is on.
- While the shell is held on its lock page an alarm that rings is not brought forward and cannot be stopped from the
  shell (for at most 5 polls, then the browser has the screen); it comes forward once when the shell is free.
- After a server restart that switched the PIN lock on, the shell learns of it at its next poll, up to 3 s later (5 s
  more if that poll times out). A reply to an action sent in between is still drawn if the server gave one, which it
  does only while its lock is open.
- Whether the launcher should wait for the port `OPOS_SERVER_URL` names is not decided; it always waits for 8765.
- A lid switch that flaps lights a closed phone at each loss and adds a log line at each loss (see "The lid"). Whether
  the shell should wait longer, or stop looking, after many losses is not decided: either would leave the lid unknown
  for longer, which is lit, never dark.
- On the phone: a lid switch that disappears and comes back (the looks every 60 s cost one read of
  `/sys/class/input` each; their effect on battery is not measured), and every sentence above on the glass.

## The hinge and back mode

The hinge angle and the accelerometer come from the phone's sensor service, sensorfw (`sensorfwd.service`, D-Bus name
`com.nokia.SensorService` on the system bus), which reads Android's sensors through the container's sensors HAL.
`hinge.py` asks it for a `hingesensor` session and an `accelerometersensor` session and reads them. It never starts,
stops or configures the service and writes nothing to the phone's sensors. The image starts it, not `hinge.py`: the
image build installs `opos-sensorfwd-delayed.timer` (stage 1), which starts `sensorfwd.service` once, 120 s after boot,
unless `/etc/opos/sensors-off` exists. That file blocks future starts only; a live stop is
`sudo systemctl disable --now opos-sensorfwd-delayed.timer`, then `sudo systemctl stop sensorfwd` (see
[README.md](README.md)). sensorfw drops a session that never connects to its data socket (`/var/run/sensord.sock`: the session id sent
as an int32, one byte read back) after about 10 seconds, and the angle then freezes, so the reader connects for each
session. A started sensor reads 0 until its first report, so the first reading waits 0.8 s and a 0 before any real
reading is not taken as an angle. When the service is not running or a read fails, the watcher says so once in the log,
the shell keeps the lid-only behaviour above, and the watcher looks again every 5 s. Starting the service is the
owner's decision, and the owner approved the timer on 2026-11-15 for stage 1 only, because the PC session reported that
a sensor-chip stall once reset this phone: the start is delayed, has the kill switch above and is capped at 3 starts in 5
minutes. Nothing here reads `/sys/kernel/debug`.

The angle is in degrees: 0 folded shut, about 180 flat, 360 folded back. `posture.py` names the zones closed below 15
degrees, book 15 to 170, flat 170 to 190, tent 190 to 345, back from 345, and keeps a zone until the angle leaves it by
6 degrees, so the name does not flutter at a border. Closed comes from the lid switch only: an angle near 0 with the lid
open reads "open". Each change is one log line, `opos shell: posture A -> B (hinge N)`. Settings shows the posture and
the angle, and in back mode adds "One screen is lit; the other is dark."

Back mode is posture back with the lid switch reading open (an unknown lid is not open, so both screens stay lit). The
page is on one screen and one screen wide; the other screen is dark and ignores touches that go down on it. The
accelerometer is in the right half of the phone: a z reading above +0.35 g means the right screen faces you, below
-0.35 g the left; in between the shell keeps the screen touched last (the thumb side before any touch). A new verdict
must hold for 0.8 s before the lit screen changes. Any other posture, no hinge reading, the lid closing, the lid signal
lost or the shell ending leaves back mode and lights both screens (the lid closing hands over to its own blank of
both). The angle never locks, changes the page or the hand. The light and proximity sensors are not used; the PC
session found them unusable folded back.

Polling is adaptive: every 0.25 s while the hinge is moving (it changed by 2 degrees or more in the last 3 s) or sits
at 300 degrees or more, where the accelerometer chooses the screen; otherwise every 1 s, with the accelerometer read
every 2 s. While the lid is closed the shell reads neither sensor (the accelerometer session is closed) and starts again
when the lid opens. Idle CPU, peer-measured (the PC session, one phone, each case read once, clock ticks of user and
system time per 30 seconds for the shell and the sensor service): 93 and 45 ticks with fixed fast polling, 31 and 21
with adaptive polling, 20 and 0 with the lid closed; the shell alone used 13 before any hinge work. These are tick
counts, not battery measurements, and were not reproduced from the Mac.

Seen on the phone, as reported by the PC session: on November 15, 2026, on one Surface Duo with the hinge code running
as an installed release (the shell only; the server untouched; some items were seen on an earlier version of the same
change), the owner confirmed the back-mode layout, taps on the lit screen, the lit screen following a flip after about
a second, and both screens lit again after the lid opened, and the shell's log showed all five postures in one fold
test (closed from the lid, the other four from the angle). One phone, one session. **NOT_RUN**: a dark screen ignoring
touch, the Settings Hinge line on the glass, a cold boot with `opos-sensorfwd-delayed.timer` (the image build installs
it, but no image with it has run on a phone), ten cold boots, and any other phone.

## Assistants are deferred

An assistant screen (Claude, Codex, Grok with the owner's own key) was part of the first import and has been removed. The
owner's order is to finish the phone first and add AI later. Nothing in the shell talks to a model or keeps a key; the
repository's check that `src/` names no model or host outside the Meta set stays green with it.

## Image

`build-image.sh` installs the launcher and `/etc/default/opos-native`; `verify-image.sh` checks them; the release tool lists
the launcher as an overlay file; `opos-selftest` accepts the native shell as the interface when it is the one running.
