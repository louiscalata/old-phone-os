<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# First run and time zone on the Duo OS

Status, November 7, 2026: **implemented and tested off the phone** (unit tests, a real server, a desktop browser at the
Duo's 1392 x 900 size). Not yet run on the Duo. What only the phone can show is listed at the end.

A fresh Duo OS boot has the wrong clock (1970 until something sets it), the wrong time zone (UTC), and no first-run
screen. The unprivileged OPOS user cannot change the system zone, so OPOS keeps its own and applies it itself.

## What the owner sees

On the first start (no `firstrun.json` in the config directory) the kiosk opens a short setup, all of it skippable:

1. **Welcome**, with a plain sentence when the phone's clock reads before 2026: "The phone's clock reads 1970, which is
   almost certainly wrong, so the time and the daily goal will be off. It should correct itself once the phone is online."
2. **Time zone**: tap a region, then a city (a search box is optional; nothing needs the keyboard).
3. **Daily goal**: the time the battery should last until, set with buttons.
4. **Helper**: show or hide it.
5. **Wi-Fi**: a hand-off, see below.
6. **Ready**: a summary and Finish.

The text sits in the left panel and the controls in the right one, so nothing is drawn on the 42 px hinge. "Skip setup"
is always on screen; every step has "Skip this step". Choices that are skipped are left as they were.

Later, Settings has a **Date and time** section with a "Time zone: ..." link to `/timezone`, the same chooser on its own
page. It is added by the server, only on the Duo OS: the shipped page is byte for byte what it was (it stays under its
size budget) and the Duo OS page is a few hundred bytes larger.

## The switch

`OPOS_FIRSTRUN=1` in `opos-server.service` turns everything on. With it unset or anything else, `opos.web` behaves exactly as
before: no route, hook, header or byte changes. The hook is one block in `opos.web.main()`; the logic is in
`src/opos/firstrun.py` (routes, flag, answers), `firstrun_page.py` (the two pages), `timezone.py` and `clockcheck.py`.

## Time zone

| What | Where |
|---|---|
| the owner's zone | one line, `<config>/timezone` (for user `droidian`: `~/.config/opos/timezone`), written atomically, mode 0600 |
| the server | at start, `opos.web.main()` calls `firstrun.install()`, which applies the zone (`TZ` and `time.tzset()`) before the `Phone` first reads the clock; changes from the pages apply at once |
| the screen | `opos-kiosk` reads that line before every Chromium start, checks it (letters, digits, `_ + - /` only, at most 64 characters, no `..`, no leading `/ + -`) and exports `TZ`; Chromium reads `TZ` when it starts |
| the list | the zone files found under `/usr/share/zoneinfo` (the image's package list has tzdata 2026e); copies (`posix/`, `right/`), old country-named directories and `Etc/` are left out. If that directory is missing, a short built-in list with fixed POSIX rules is offered and the page says so |
| validation | a name is only ever compared with that list; it is never joined into a path, never reaches a shell, and a saved line that is not exactly a listed zone is ignored. The kiosk script treats the file as untrusted text for the same reason |

Because Chromium reads `TZ` only when it starts, saving a different zone makes the server stop the kiosk's Chromium (a fixed
`pkill -TERM -u <uid> -f` on its profile directory, `$HOME/.cache/opos-kiosk`; no shell, nothing from the request in it) and
`opos-kiosk`'s loop starts it again two seconds later with the new zone. The screen is dark for a few seconds; the
compositor and `opos.service` are not touched. Nothing restarts when the screen already runs the chosen zone, and
`OPOS_KIOSK_RESTART=0` in the server's environment turns the restart off. On the first-run page the restart happens once, at
Finish, so the setup is not interrupted halfway.

The system zone does not change: `date`, `timedatectl`, ssh sessions and the journal stay in UTC. From a shell,
`PYTHONPATH=/opt/opos/src python3 -m opos.timezone [--list | --set NAME]` shows or sets the zone; the server needs a restart and
the screen a Chromium restart to follow a change made that way.

## The clock

`src/opos/clockcheck.py` has one function, `clock_plausible(now=None, floor_year=2026)`: true when the system clock reads
2026 or later. It is deliberately tiny; a richer clock state belongs to the clock work and can replace it. This image enables
`systemd-timesyncd` and `item-clock-seed.service`, so the sentence says the clock should correct itself once the phone is
online; whether it does on the phone is not checked here.

## Where the answers go

| Choice | Saved in | Applied to the running server |
|---|---|---|
| time zone | `<config>/timezone` | yes, at once |
| daily goal | `config.json` (`goal`; created for a Surface Duo when absent, otherwise only `goal` and `applied` change) | yes (`Dashboard.set_goal`) |
| Helper | `prefs.json` (the setup answer `helper`, then `applied`) | yes (`Phone.set_visible`); the next start reads it too |
| first run done | `firstrun.json`: `{"version": 1, "skipped": false}`, or `true` when skipped | n/a |

## Setup can never block the phone

- A request that does not ask for `text/html` (the kiosk's wait loop, health checks) always gets the home
  page with status 200. Only a browser opening `/` is sent to `/firstrun`, and only until the flag exists.
- Skip setup, "Open Home" and `/?firstrun=later` all reach the home screen. The query changes nothing on the server: it only makes that
  one response the home page, so a request from anyone cannot switch the setup off for everyone. "Open Home" first sends an
  authenticated `POST /api/firstrun {"action": "later"}`, which stops the redirect until the server restarts, and goes home
  whether or not that worked. If the flag cannot be written, the server stops
  offering setup for that run instead of looping.
- If the config directory cannot be written there is no first run at all; if deciding fails for any reason the home page is
  shown; if `opos.web` cannot load the track at all, it logs one line and starts normally.

## Wi-Fi hand-off

The Wi-Fi step asks the server whether `/network` exists (`HEAD /network`). If it answers 200 the step shows "Open Wi-Fi
setup" linking to it; otherwise it says joining a network is not available on this screen yet and that the step can be
skipped. The Duo OS image serves `/network` (docs/network.md), so the link is there. Leaving for `/network` and coming back
through its Home link returns to this page at the Wi-Fi step with the earlier answers kept (they are held in `sessionStorage`
for this tab for 30 minutes, and dropped when the setup is finished or skipped). Without a `/network` route Wi-Fi is joined
from a shell with NetworkManager's `nmcli`.

## Routes and files other work can rely on

| | |
|---|---|
| pages | `GET /firstrun`, `GET /timezone` (host check, nonce CSP, same token as `/`) |
| JSON | `GET /api/firstrun`, `GET /api/timezone` (token); `POST /api/firstrun` `{"action": "skip"}`, `{"action": "later"}` or `{"action": "finish", "zone", "goal": {"enabled", "time"}, "helper", "client_tz"}`; `POST /api/timezone` `{"zone", "client_tz"}` (the same origin, type, token and rate checks as `/api`, in that order: a rejected request is not charged to the rate limit) |
| files | `<config>/timezone`, `<config>/firstrun.json`; `config.json`, `prefs.json` as written by the setup |
| environment | `OPOS_FIRSTRUN=1` (on), `OPOS_KIOSK_RESTART=0` (no Chromium restart) |
| kiosk | `opos-kiosk` must keep starting Chromium with `--user-data-dir=$HOME/.cache/opos-kiosk` (the restart finds it by that) and must keep re-reading `<config>/timezone` before each start |

## What only the phone can show

The tests here run on a Linux PC with its own zone database. They show that the zone is saved, validated, applied to a fresh
server process and exported by the kiosk script, and that the pages work in a desktop browser. They do not show, and the owner's
test on the Duo has to: that Chromium on the phone reads `TZ` (the status-bar clock follows the chosen zone), that the Chromium
restart brings the screen back on its own, that the page fits the two panels with nothing on the hinge, that touch works on
every control, that the clock sentence appears and then goes away once the network sets the time, and that the saved zone
survives a new RAM-boot from the PC (never a power cycle: it can start Android and destroy the OPOS install).
