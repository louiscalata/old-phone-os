# The native shell

An alternative to the browser kiosk on the Surface Duo image: a GTK 4 window, drawn by the phone's GPU, that covers both
screens. The browser interface stays as the fallback and is never running at the same time.

It is not verified on a phone. The rules it follows are tested on a computer without a display; the window, the touch
handling and the frame pacing have not been run on the phone, and no image with it has been built or started.

## What is where

| File | Role |
| --- | --- |
| `src/opos/native/shell.py` | the window, the gestures and the polling of the OPOS server |
| `src/opos/native/screens.py` | the screens: Lock, Home, Phone, Messages, Clock, Contacts, Notes, Calculator, Call log, Settings |
| `src/opos/native/logic.py` | what the screens decide for themselves, with no toolkit, so it is tested anywhere |
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
restore is capped only at each panel's own `max_brightness`. Not verified on a phone: no lid close, blank or restore on the Duo is recorded here (NOT_RUN), and the
current drawn with the lid closed has not been measured.

## Assistants are deferred

An assistant screen (Claude, Codex, Grok with the owner's own key) was part of the first import and has been removed. The
owner's order is to finish the phone first and add AI later. Nothing in the shell talks to a model or keeps a key; the
repository's check that `src/` names no model or host outside the Meta set stays green with it.

## Image

`build-image.sh` installs the launcher and `/etc/default/opos-native`; `verify-image.sh` checks them; the release tool lists
the launcher as an overlay file; `opos-selftest` accepts the native shell as the interface when it is the one running.
