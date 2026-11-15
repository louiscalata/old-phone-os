<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Everyday apps

`OPOS_APPS` names the extra apps, comma-separated (`clock,contacts`). When the variable is absent or empty, the main page is unchanged and `/apps` is not served. The Duo image drop-in `packaging/duo-os/opos-apps.conf` names `clock,contacts,notes,calc,calls`. The demo and the postmarketOS build do not set the variable.

Each named app is its own page. The main page gains one link, More apps, inside All apps. `web.page()` does not grow.

App files live under `$XDG_STATE_HOME/opos` (the default state directory when that variable is unset). Writes are atomic and mode 0600. A file that cannot be read is moved aside and the app starts empty. Nothing in this path uses the network.

Each app has a disclosure line: works offline, sends data, uses, real or practice. The helper is not given a way to write these files.

An app that fails to open shows a short page with Home. Phone and the front page keep working.

## Call log

`calls` enables `/calls` and the token-protected `/api/calls`. The log keeps up to 200 calls in `calls.json`, under `OPOS_STATE_DIR` when set, otherwise `$XDG_STATE_HOME/opos` (or `~/.local/state/opos`). It uses the same atomic Store writes as contacts and notes: a private directory (0700) and file (0600). Oldest start times are pruned first. Unreadable or invalid files are moved to `calls.json.bad-<time>` and the log starts empty with a recovery notice. If the copy cannot be kept, the log refuses to overwrite the file. A storage failure does not prevent a call, but that event may be missing from the log.

Each row stores the number, the contact name known at the start (empty when unknown), direction, UTC start time, duration in connected seconds, and whether the modem was practice or real. The page shows newest first, grouped by the viewing phone's local day. Search matches names and numbers. A row opens the existing Phone number form; the owner still taps Call. Clear all calls requires a separate confirmation and also clears the missed count. It does not delete recovery copies.

Recording happens through the optional modem `call_observer`, using `opos.calllog.record(CallEvent(...))`. An adapter emits `started`, `connected` when answered, and `ended` with a unique 32-character hexadecimal call id, number, incoming or outgoing direction, Unix event time, and an explicit practice flag. The ending event supplies connected seconds from the adapter's monotonic clock, or `None` when unknown. It can also supply `answered` so a lost connected write cannot turn an answered incoming call into a missed one; ScreenModem supplies it. No Modem protocol method changes. Starts are saved immediately with duration unavailable; interrupted calls stay that way after a restart. An unanswered incoming call becomes missed with duration zero. Reading the API does not acknowledge calls: `missed_count` counts missed rows still retained, decreasing on pruning or clear. While the PIN lock is on and the phone is locked, the lock screen's missed-call count is this number; Home does not read it yet.

The current phone uses ScreenModem, so its calls are labelled Practice. Its incoming and answer hooks are local simulation hooks, with no incoming-call UI added here. A future real adapter can use the same observer with `practice=False`; no real modem is connected by this change. Example contacts are used only for practice calls in an empty book. There is no account, network request outside the local server, new package, or new service. Keyboard input, pane layout, and Home use the shared app shell. Every on-device call-log check is **NOT_RUN**.

## While the phone is locked

This applies only with the PIN lock on (`OPOS_LOCK=1`); with it off, nothing here changes. While the phone is locked, a read of an app's API (`GET /api/contacts`, `/api/notes`, `/api/clock`, `/api/calls`) answers with the same body as a locked `/api/state`: `locked`, the clock, the battery percent, and the lock counts. It carries no names, numbers, note titles, alarm labels or call rows. The Clock answer adds whether an alarm is ringing and its time (`Alarm 07:05`), without the label, so Stop and Snooze can still be offered; those two requests and Hang up stay allowed, and their answers are the locked body too. The lock counts are the missed calls kept by the Call log app (0 when that app is off) and the next alarm's time. The message count stays 0: the phone keeps no record of which messages were read, so there is no count of new ones to show. The app pages themselves are fixed documents with no saved data in them.

With the lock on, the served home page gains one tag for `/pin.js`, the PIN pad. Showing the lock screen locks the server. Unlock, a swipe up, Escape, Call and Helper on the lock screen all ask the server: with a saved PIN the pad asks for the digits and sends them, and with no saved PIN the server unlocks without digits. The lock screen comes off only after the server has answered that the phone is unlocked; a wrong PIN, a running wait, or no answer leaves it on. The script does not compare or keep a PIN. It was run in node against a stand-in page (`tests/test_lock_pad.py`); a browser and the phone are **NOT_RUN**.

## Not run on a phone

The Duo image names clock, contacts, notes, calc and calls. Each is its own page. The home page only gains the existing short `/apps-home.js` tag, which adds the More apps link and, when an alarm is armed, the words "Alarm" and "Alarm 07:00" on the lock screen. Enabling calls adds no home-page bytes.

No install has been made for this work. Every on-device check in the phone-features spec (D1-D11) is NOT_RUN. Sound and vibration are not played yet.
