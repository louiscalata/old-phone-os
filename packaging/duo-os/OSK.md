<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# On-screen keyboard for the Duo OS

Status, November 7, 2026: **built and exercised on a desktop browser; not yet on the phone.** The in-page keyboard (the
default) ran in a desktop Chromium pane at 1024 x 768 and 700 x 800 CSS px against the demo server, with mouse clicks and
synthetic pointer events standing in for touch, and its logic is under unit tests. It has not been seen at the phone's
1392 x 900, with a finger, or under phoc. The Wayland keyboard (opt-in) has only been checked against sources, never started.

## The problem

OPOS is the Duo OS's only interface: phoc runs one Chromium kiosk and nothing else. Phosh normally starts a keyboard and the
port's item shell has its own; OPOS runs neither, and the Duo has no physical keyboard. So the helper's Ask field, the
Messages composer (`#number`, `#msg`) and the goal time in the shade (`#goal`, an `<input type=time>`) could not be typed
into by touch. Those are the page's only text fields; Settings and the first-run steps have none (`select`, radios, buttons).

## What the sources say (Option A, the port's keyboard)

| Question | Answer | Where |
|---|---|---|
| Is a keyboard in the image? | `phosh-osk-stevia` 0.55.0, with hunspell-en-us and presage. **No** squeekboard, and no `phosh-osk-data-eu` | `old-phone-os-real/port/item/packages.txt` |
| Which build? | The port's patched stevia (on a display whose `phoc.ini` names a `tiling-seam` it takes the right panel in landscape, a compact 480 px, 60 px rows), put in place by `sfduo-osk-install` | `sfduo-osk-install`, `surface-duo-ai/sources/stevia-duo-hardware` (`phosh-osk-stevia.c`) |
| How is it started? | By phosh's session: `gnome-session@phosh.target` wants `mobi.phosh.OSK.target`, which wants `mobi.phosh.OSK.service` and `Requisite`s `gnome-session-initialized.target`. OPOS's session has no gnome-session, so that chain cannot start here | image snapshot, `usr/lib/systemd/user/` |
| Does phoc 0.47 offer what stevia needs? | Yes: `wlr_input_method_manager_v2`, `wlr_text_input_manager_v3` with a relay between them, layer-shell (a layer named `osk` is raised above a focused layer), virtual-keyboard, foreign-toplevel, data-control, and its own `zphoc_device_state_v1` | `phoc-duo-hardware/src/desktop.c`, `input-method-relay.c`, `layer-shell.c`, `device-state.c` |
| Can a plain client bind them? | Yes. phoc hides its privileged globals only from clients that carry a security context (flatpak-style); a normal `droidian` client sees them all | `phoc-duo-hardware/src/server.c` `phoc_server_filter_globals` |
| What does stevia require? | input-method-v2, virtual-keyboard, seat, foreign-toplevel, layer-shell, xdg-output, `zphoc_device_state_v1` and data-control, or it does not start; GSettings (`mobi.phosh.osk`, and `org.gnome.desktop.a11y.applications screen-keyboard-enabled`, which keeps it hidden while false); a session bus | `pos-wayland.c` `pos_wayland_has_wl_protcols`, `phosh-osk-stevia.c` |
| Will Chromium talk to it? | `/etc/chromium.d/item` gives Chromium `--ozone-platform=wayland --enable-wayland-ime --wayland-text-input-version=3`, and item's notes say its keyboard rises on that same Chromium's text-input-v3 for a field. That was item's compositor, not phoc | image snapshot, `port/source/item/README.md` |

Everything on the compositor and keyboard side is confirmed from source. What no source can show:

1. That Chromium 155 in OPOS's kiosk really sends `text_input.enable` to phoc when a field is touched, and that phoc then
   activates stevia.
2. That stevia starts as a plain `droidian` client outside gnome-session on this image (schemas, fonts, session bus).
3. That phoc does not report a hardware keyboard on the Duo (`ID_INPUT_KEYBOARD`, the value-4 bit of the device-state
   capabilities); stevia stays folded while it does.
4. What `screen-keyboard-enabled` is on the image; phosh sets it, and OPOS has no phosh.
5. How the keyboard looks over a fullscreen kiosk. The layer surface covers the bottom of the right panel and Chromium is
   not resized, so the page cannot know to lift a field that sits below it.

## Decision

**Option B, an in-page web keyboard, is the default; Option A, the port's stevia, is an opt-in with a live test.** B depends
on nothing but the page and the server (no compositor protocol, no schemas, no second process), it fails closed (if
`/osk.js` does not load, nothing changes), it can keep the focused field visible because it knows its own height, and it
knows about the hinge. A is better typing (word correction, layouts) if it works, but five things above cannot be confirmed
without the phone, and an untried second client beside the only interface is the wrong default.

One selector, `OPOS_OSK`, so that only one keyboard ever runs:

| `OPOS_OSK` | Server | Second process |
|---|---|---|
| `1` or `web` (the image's default) | adds the in-page keyboard to the page and serves `/osk.js`, `/osk.css` | none |
| `stevia` | adds nothing, serves nothing | `opos-osk.service` starts `phosh-osk-stevia` |
| `off`, `0`, unset anywhere else | adds nothing: the page and every route are byte-for-byte what they were | none |

`opos-server.service` sets `OPOS_OSK=1` and reads `/etc/default/opos-osk` after it, which overrides it
(systemd: settings from an `EnvironmentFile` override `Environment=`). `opos-osk.service` reads the same file and does
nothing unless it says `stevia`. The file ships with the setting commented out. After editing it:
`sudo systemctl restart opos-server opos-osk`, then `pkill chromium` as `droidian` (`opos-kiosk` starts it again).

## Option B: the in-page keyboard

- **Server** (`src/opos/osk.py`). While `OPOS_OSK` is `1`/`web`, `web.Handler` passes the finished page through
  `osk.inject_bytes`, which adds a `<link rel=stylesheet href=/osk.css>` and a `<script src=/osk.js defer>` before
  `</body>`, both under the page's own CSP nonce, and `GET`/`HEAD` of `/osk.js` and `/osk.css` answer from
  `src/opos/osk_static/`. The files are outside `web_static`, so they are not inlined and the main page's 102,400-byte budget
  is untouched (`web.page()` does not know about the keyboard). Any other full-page route that wants typing by touch calls
  `osk.inject(html, nonce)`; it is a no-op while the keyboard is off.
- **When it shows.** On touching a text field (`input` of a text kind, `textarea`; not `readonly`, `disabled`,
  checkbox, radio, range, buttons, `inputmode=none`), including a second tap on a field the page already focused. Not when
  the page only calls `focus()` (the Helper app does that on opening, and a keyboard over the answers is not wanted).
  It hides on the Hide key, when focus goes to anything that is not a text field, when the field disappears, or on the
  first key from a physical keyboard (a later touch shows it again). The keys never take focus from the field.
- **What it types.** Letters (shift, caps lock on a double tap, automatic capitals after a full stop), numbers, symbols; a phone
  pad for `type=tel`/`inputmode=tel`, digits for `type=number`, and a 24-hour pad for `<input type=time>`, where the digits go into
  a buffer and the field gets `HH:MM` (`1`, `6`, `3`, `0` makes 16:30; a first digit above 2 is taken as `0n`; two or three digits are filled out with zeros when you tap Done, Hide or elsewhere, one digit changes nothing). Backspace repeats
  while held. Space, Enter, Delete.
- **Events.** For every key the field gets `keydown`, `beforeinput`, the new value and `input`, then `keyup`, so the page's own
  handlers fire (the Ask, Send and next-field handlers on Enter are the existing ones). `change` is sent when Enter is
  pressed or the field is left, because a value set from script never produces the browser's own. `maxlength` is honoured.
  Key events are untrusted, so `phone-a.js`'s hardware-keyboard detection ignores them.
- **Where it sits, and why.** The window spans both panels (1392 x 900 CSS px: two 675 px panes and a 42 px hinge). The hinge
  comes from, in order, the viewport segments if the browser ever reports them, the page's `--pane`/`--hinge`
  (improve/hinge-layout's `OPOS_SPAN`), and `OPOS_OSK_HINGE` (default 42, `0` for none).
  - **Letters: split, one half on each pane.** Each half is 420 px wide and hugs the outer edge of its pane, where a thumb holding
    the open phone rests, so the hinge falls in the empty middle and no key is ever on it or split by it. Every row has two
    halves of equal weight, so a row is two rectangles. Both thumbs type. A **Join** key in the top row puts the keyboard as
    one block on the pane that holds the field (the choice is remembered per browser); the port's own keyboard made the
    same one-panel choice for one-handed use. **Split** switches back.
  - **Pads** (phone, digits, time): one block 380 px wide on the outer edge of the pane that holds the field.
  - A window on one pane (under 840 px, or no hinge known) gets one block across the window, up to 760 px.
  - At 1392 x 900 every key is at least 56 px wide in every layout and mode (`tests/osk_core_test.js` computes it from the real
    layouts); keys are at least 56 px tall, the Hide/Join row 44 px, and the whole keyboard is 306 px high.
- **Keeping the field visible.** The keyboard overlays the page, so on showing it adds a spacer the height of the keyboard to the
  end of the page and scrolls whatever scrolls (the body is the page's scroller, because `html` and `body` both set
  `overflow-x:hidden`) until the field is clear of it. Hiding removes the spacer; with a finger down it waits until the
  tap has landed, so the page does not move under the tap on Send.
- **Look.** The page's own `--jaryo-bg`/`--jaryo-fg`, so light, dark and the theme setting follow the page; square keys with
  1 px borders; Enter, Shift and caps lock are inverted. No icons: labels are words, because the image has no symbol fonts
  to rely on. No external files, no network, no `eval`, nothing written but one `localStorage` flag.
- **Accessibility.** A `role=group` named "On-screen keyboard"; every key is a `<button type=button>` with a name
  (punctuation is spelled out, Shift has `aria-pressed`); a click with no pointer behind it (assistive technology, `detail` 0)
  presses the key. The keys are not in the tab order, so a physical keyboard's Tab is unaffected.
- **Not done.** No word correction or prediction, no accented letters by long press, no emoji, no IME composition, no
  `contenteditable`, no arrow keys (touch places the caret), and a `type=number` field takes digits only (the browser cannot hold
  a half-typed `1.`). A rotated window is not handled (the hinge would run across it). The standalone `opos-setup` wizard server is not part of
  the Duo OS session and does not load the keyboard.

## Option A: the port's keyboard (opt-in, untried)

`/etc/default/opos-osk` with `OPOS_OSK=stevia`, then `sudo systemctl restart opos-server opos-osk`. `opos-osk.service`
(enabled, `Requisite=opos.service` so it never starts beside phosh, `PartOf=opos.service`) runs `/usr/libexec/opos/opos-osk`,
which finds phoc's socket in `/run/user/<uid>`, sets `GDK_BACKEND=wayland`, a session bus address and
`screen-keyboard-enabled`, and runs `phosh-osk-stevia --allow-replacement`. Stevia unfolds when phoc activates it for a
text-input-v3 client; with it there is no lifting of the page and no Join/Split.

## Live checks for the phone

All as `droidian` over ssh unless stated. Nothing here needs root.

### Option B (the default)

```sh
# 1. The server has it on, and says so over HTTP (the Debian base has no curl)
tr '\0' '\n' < /proc/$(systemctl show -p MainPID --value opos-server)/environ | grep OPOS_OSK     # OPOS_OSK=1
python3 -c "import urllib.request as u; b=u.urlopen('http://127.0.0.1:8765/').read().decode(); print('/osk.js' in b, '/osk.css' in b)"   # True True
python3 -c "import urllib.request as u; r=u.urlopen('http://127.0.0.1:8765/osk.js'); print(r.status, r.headers['Content-Type'], len(r.read()))"   # 200 text/javascript; charset=utf-8 ~30000
```

Then on the phone, with the kiosk page reloaded (`pkill chromium`, wait for `opos-kiosk`):

1. Open Phone, tap the number field. **A pad** (1-9, `+ * #`, Delete, Enter, Hide) appears on the pane of the field, at its outer
   edge; the field is above it. Tap `5 5 5`: the field shows 555. Enter moves to the message field and the **letters** appear.
2. Letters: **one half on each pane**, the hinge between them, the first letter a capital, Hide top left, Join top right.
   Hold Delete: it repeats. Tap `123`, `#+=`, `ABC`. Tap Join: one block on the pane of the field; tap Split: back.
   Type a message and tap **Send**: it sends, and the page does not jump under the tap.
3. Open the Helper: the keyboard does **not** appear by itself. Tap the Ask field: it appears. Type and press Enter: the helper answers.
4. Open the shade, tap the "Last until" time: the **24-hour pad** shows `HH:MM`. Tap `1 7 3 0`: the field reads 17:30 and the
   shade's goal line follows (the page's own change handler ran).
5. Tap the page background, or Hide: the keyboard goes and the page scrolls back. Plug in a USB keyboard and press a key
   in a field: the keyboard goes; touch the field again: it returns.
6. Look for: a key under the hinge (none expected), a field hidden behind the keyboard, a missed tap on a key, keys that
   repeat or double (a held finger on a letter must not repeat it), slow response (the Duo's CPU is the 855).

With the dev image's DevTools port (improve/kiosk-units: `ssh -L 9222:127.0.0.1:9222 droidian@<phone>`, then
`chrome://inspect` on the PC), in the page's console: `JSON.stringify({w: innerWidth, h: innerHeight, plan: oposOsk.st.plan})`
should read `w` 1392, `h` 900 and, after tapping a letters field, `plan.layout` `split` with `half` 420 (or `pane` with `width` 667
after Join). If `w` is not 1392 the span numbers in `opos-server.service` (`OPOS_SPAN`) and `OPOS_OSK_HINGE` need the measured values.

### Option A (the port's stevia)

To try it without root and without two keyboards at once: a second server with no in-page keyboard and a second Chromium
window on it, then stevia by hand with its log in view.

```sh
export XDG_RUNTIME_DIR=/run/user/$(id -u); ls -l $XDG_RUNTIME_DIR     # wayland-0 (a socket), wayland-0.lock, bus
export WAYLAND_DISPLAY=wayland-0 DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus GDK_BACKEND=wayland
dpkg -l | grep -E 'phosh-osk|squeekboard'                            # phosh-osk-stevia 0.55.0..., no squeekboard
cmp /usr/bin/phosh-osk-stevia /usr/lib/sfduo/osk/phosh-osk-stevia/*/phosh-osk-stevia && echo "the port's patched build"
udevadm info -e | grep -c 'ID_INPUT_KEYBOARD=1'                       # 0: no hardware keyboard (else stevia stays folded)
gsettings get org.gnome.desktop.a11y.applications screen-keyboard-enabled    # true needed; else gsettings set ... true
ps -o args= -C chromium | tr ' ' '\n' | grep -E 'wayland|ime'          # --ozone-platform=wayland --enable-wayland-ime --wayland-text-input-version=3

# a second OPOS server without the in-page keyboard, and a second window on it
cd /opt/opos/src && OPOS_OSK=off PYTHONPATH=/opt/opos/src OPOS_BATTERY=live python3 -m opos.web --port 8766 --live-battery >/tmp/osk-test.log 2>&1 &
chromium --user-data-dir=/tmp/osk-test --no-first-run --noerrdialogs http://127.0.0.1:8766/ >/dev/null 2>&1 &

# in a second ssh session (same exports): the keyboard, with its debug log
G_MESSAGES_DEBUG=all phosh-osk-stevia --replace 2>&1 | tee /tmp/stevia.log
```

On the phone, tap a text field in the second window. In the stevia log:

- `Device state capabilities: 0x0` to `0x3` is fine; **a value with the 0x4 bit (0x4 to 0x7) means phoc reports a hardware
  keyboard**: stevia stays folded (`gsettings set mobi.phosh.osk ignore-hw-keyboards true` overrides it).
- `active: 1, enabled: 1` on touching a field: stevia unfolds, on the right panel, about 480 px wide. Type into the field.
- `active: 1, enabled: 0`: blocked (the a11y key, the hardware keyboard, or the activation filter). `active: 0` or no line at all:
  Chromium did not enable text-input on phoc, so Option A does not work as configured.
- A missing-protocol message at start: phoc is not offering something stevia needs; paste it.
- `POS_DEBUG=force-show phosh-osk-stevia --replace` keeps it unfolded and types through the virtual keyboard: that tests
  the layer-shell placement and typing without text-input.
- Expect the keyboard to cover the lower part of the right panel; the kiosk is fullscreen and does not shrink.

Clean up: Ctrl-C stevia, `pkill -f osk-test`, `pkill -f 'opos.web --port 8766'`. To make it the default after a good result, as root:
`echo OPOS_OSK=stevia > /etc/default/opos-osk && systemctl restart opos-server opos-osk`, then `pkill chromium` as `droidian`.
