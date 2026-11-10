# UI byte ledger

How many bytes the OPOS page takes, where they go, and how to measure them again. The interface finalization plan
(section 3) gives every wave of changes one rule: **after the wave, `page()` and the home page the server really sends with
every switch on must both be at most 102,000 bytes.** The test (`tests/test_uxp30_budget.py`) enforces only the hard cap of
102,400 bytes for `page()`; the 400 bytes between the two numbers are the reserve for what the server adds around `page()`.

All numbers are UTF-8 bytes. They are exact, not estimates: the same script gives the same figure on every run.

## What is measured

| Figure | What it is |
|---|---|
| `page()` | `opos.web.page()` with no argument and no environment switch: the number `test_uxp30_budget` checks against 102,400. |
| `page()` spanned | `page(span=Span(675, 42))`: what `OPOS_SPAN=675,42` serves (the Duo configuration). `span.apply()` squeezes whitespace and rewrites the geometry, so it is a little smaller. |
| served, no switch | What `GET /` returns from a running server, no switch set: `page()` plus the token meta tag and the nonce on every `<style>` and `<script>`. |
| served, every switch | The same with `OPOS_OSK=1 OPOS_NETWORK=1 OPOS_FIRSTRUN=1 OPOS_MONITOR=1 OPOS_PANES=1 OPOS_SPAN=675,42`. This is the real Duo image, and the figure the wave gate is about. |

## The ledger of 11c39ba (the base of the room, wave 1 tracks)

| Figure | Bytes | Free under the 102,000 gate |
|---|---:|---:|
| `page()` | 100,748 | 1,252 |
| `page()` spanned | 100,679 | 1,321 |
| served, no switch | 100,977 | 1,023 |
| **served, every switch** | **101,705** | **295** |

What each switch adds to the served page (measured one at a time against "served, no switch"):

| Switch | Bytes | Where |
|---|---:|---|
| `OPOS_OSK=1` | +154 | the keyboard's two tags (`osk.inject_bytes`) |
| `OPOS_NETWORK=1` | +229 | the Settings link to `/network`, inside `page()` (`net_page.add_settings_link`) |
| `OPOS_FIRSTRUN=1` | +0 | first run is its own page |
| `OPOS_MONITOR=1` | +247 | the Settings link to `/monitor` (`monitor_web.link_bytes`) |
| `OPOS_PANES=1` | +136 | the two panes tags (`panes.inject_bytes`) |
| `OPOS_SPAN=675,42` | -38 | `span.apply()`: the whitespace squeeze minus the geometry rules and variables it adds |
| all six | +728 | |

Where the bytes of `page()` are (the files inlined by `@@OPOS:name@@`, largest first; the rest is `web.py`'s own inline
CSS, the demo panel and the glue):

| Bytes | File | Bytes | File |
|---:|---|---:|---|
| 16,062 | `phone-a.js` | 3,320 | `js-2.js` |
| 13,277 | `phone-b.js` | 2,669 | `js-8.js` |
| 10,847 | `phone-b.css` | 2,320 | `phone-b.html` |
| 8,583 | `jaryo.js` | 1,975 / 1,898 | `js-7.js` / `js-5.js` |
| 6,515 | `jaryo.css` | 1,759 | `jaryo-layout.css` |
| 3,994 | `jaryo-layout.js` | 1,475 / 1,351 | `css-1.css` / `css-2.css` |
| 3,720 | `phone-a.css` | 3,252 | the About notices block (`_about_notices()`) |
| 3,391 | `js-3.js` | 7,092 | everything else (inline CSS and demo panel in `web.py`, hooks, token meta, the rest of the small files) |

## What the room track (`ui/w1-room`) did to it

| Step | `page()` | spanned | served, no switch | served, every switch |
|---|---:|---:|---:|---:|
| 11c39ba | 100,748 | 100,679 | 100,977 | 101,705 |
| B-03 retire the pinned dead rules | 99,746 (-1,002) | 99,689 | 99,975 | 100,715 |
| W2-16 Wikipedia sources as text | 99,391 (-355) | 99,336 | 99,620 | 100,362 |
| **net** | **-1,357** | **-1,343** | **-1,357** | **-1,343** |
| free under the 102,000 gate | 2,609 | 2,664 | 2,380 | 1,638 |

B-03 removed seven legacy rules that `jaryo.css` already overrides in every state: the four tone colours of the meter and
of the Shade title (with their glows, the `--tone-*` variables, the charging pulse and its `@keyframes`), the grey
notice card with the yellow bar on Home, the Reload button's own black-on-white colours, the light-red low-battery colour and
the 13 px grey `#notice`. The `data-tone` attribute stays: it is still the word the code sets, and `jaryo.css` gives every tone the one
Jaryo ink. Five tests that asserted the dead rules themselves were rewritten to assert what is true now (see the commit).

W2-16 made a Wikipedia source one line of text, `Source: <title>, Wikipedia`, instead of a `target=_blank` link, and
removed the `#answer a` styles. One page serves every build, so the desktop and demo build show plain-text sources too;
the plan's title says "on the OS image" but its fix is the single code path. If the owner wants links back on the
desktop build, that costs back the 355 bytes W2-16 saved in `page()`, or a build-specific injection like the OSK's.

### Known dead and still in the page (not taken)

Pinned by a test, so they stay until an owner-approved test rewrite. They are the next room, in this order:

| Bytes | Rule | Pinned by |
|---:|---|---|
| 128 | `#home button:focus-visible,#apps-list button:focus-visible,#apps-close:focus-visible{outline:2px solid #fff...}` (`phone-b.css`; `jaryo.css` sets a 3 px ring) | `test_uxp12_home.test_focus_visible` |
| 42 | `#history-label{font-size:14px;opacity:.6}` (`css-3.css`; `jaryo.css` sets rem and opacity 1) | `test_ux_u026_touch_css` |
| 16 | the `min-height:48px` on `#topbar,#textsize` in `phone-b.css` (`jaryo.css` sets it) | `test_uxp13_settings.test_hit_areas` |

## The page is also pinned by a digest

`tests/test_update_client.py`, class `MainPageIsUntouched`, records the SHA-256 of `page("t"*43, "n"*22)` and of the same
with `demo=True` (build id patched to 12 `f`s). **Every change to `page()` makes `test_recorded_digests` fail until the two
constants are re-recorded.** Each item of the interface plan does that in its own commit; when several tracks are merged,
the integrator re-records once on the merged tree (never by hand-merging the two constants). To get the new values:

```
PYTHONPATH=src python3 - <<'EOF'
import hashlib
from unittest import mock
from opos import web
with mock.patch("opos.notices.BUILD", "f" * 12):
    for demo in (False, True):
        print(hashlib.sha256(web.page("t" * 43, "n" * 22, demo=demo).encode("utf-8")).hexdigest())
EOF
```

## How to measure again

From the root of a checkout. It needs only Python 3 and the repository; it starts a server on a free loopback port and
stops it again.

```
PYTHONPATH=src python3 - <<'EOF'
import os, threading, urllib.request
SW = {"OPOS_OSK": "1", "OPOS_NETWORK": "1", "OPOS_FIRSTRUN": "1", "OPOS_MONITOR": "1", "OPOS_PANES": "1", "OPOS_SPAN": "675,42"}
for k in SW: os.environ.pop(k, None)
from opos import web
from opos.span import parse
span = parse(SW["OPOS_SPAN"])
def served(env, **kw):
    os.environ.update(env)
    srv = web.make_server(0, web.Phone(), **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    n = len(urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=20).read())
    srv.shutdown(); srv.server_close()
    for k in env: os.environ.pop(k, None)
    return n
print("page()                %7d" % len(web.page().encode()))
print("page() spanned        %7d" % len(web.page(span=span).encode()))
print("served, no switch     %7d" % served({}))
print("served, every switch  %7d" % served(SW, span=span))
EOF
```

Run it before a change and after each item, and keep both "served" figures under 102,000. For one switch alone, call
`served({"OPOS_OSK": "1"})` (and pass `span=span` only for `OPOS_SPAN`). A checkout reports its build as `unpublished`
(11 bytes); a release build puts a git hash of at most 12 characters in its place, so a shipped image is at most one byte
larger (`test_uxp30_budget` checks that case against the cap).

## Everyday apps

`OPOS_APPS` is not one of the six switches above. When it is absent, `page()` and the served home page are unchanged
(`page()` is not edited). When it names an app, the served home page gains one script tag for `/apps-home.js`
(68 bytes). The script file is not part of the page. Measured with the tag and the six switches, the served home page
is 101,317 bytes (683 bytes under the 102,000 gate). Later apps must not add to this page. `tests/test_apps_shell.py`
checks the gate.
