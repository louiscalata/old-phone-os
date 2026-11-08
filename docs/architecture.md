<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Architecture

Paths below are relative to `src/opos/`. Packages are listed as units, including their child
modules.

## Battery policy

- `governor.py`: state and task types, deterministic baseline, advisor validation and safety intersection.
- `router.py`: task affordability from charge, health, heat and the daily goal.
- `runtime.py`: combines policy, drain estimates, reserves, budgets and screen output.
- `battery.py`: sampled drain estimation and remaining-time calculations.
- `budget.py`: mode-dependent CPU, brightness, synchronization and radio budgets.
- `power.py`: saving-step costs, goal planning and protected task names.
- `reserve.py`: worn-battery restrictions, burst limits and shutdown/call-feasibility calculations.
- `eol.py`: simulated voltage, resistance, brownout and reserve arithmetic.

## Helper and models

- `advisor.py`: asynchronous, cached Muse Glimmer advice that can only add pauses; at most one request in flight.
- `assist.py`: Ask routing, the rest rule (`is_resting`), memory checks, optional Wikipedia search and fallback messages.
- `muse.py`: model routes, HTTP completions, Why, Plan and the endpoint check, with one shared deadline across routes.
- `aitools/`: optional coding-tool catalog, installation, wrappers and a standalone power/session guard.

## Phone services

- `essentials.py`: modem interface, calls, validated messages and retry queue.
- `contacts.py`: contact lookup and message-thread presentation.
- `mm_modem.py`: ModemManager adapter using D-Bus command-line calls.

## Web UI

- `web.py`: loopback HTTP server, API dispatch, the simulated `Phone`, the helper's plain facts (`why_paused`) and page assembly.
- `web_static/`: ordered CSS, HTML and JavaScript fragments.
- `monitor.py`, `monitor_web.py`, `web_static/monitor*`: the optional Inference page (read-only; the data and its document, and the glue `web.py` calls), served only while `OPOS_MONITOR=1`.
- `secure.py`: bind and request validation, tokens, body limits, rate limiting and security headers.
- `dashboard.py`: battery details, goals, history, saving plans and undo state.
- `home.py`: home-screen text and quiet-output checks.
- `ui.py`: separate Tk demo and the in-memory `ScreenModem`.

## Setup and install

- `setup/`: CLI, configuration, preferences, device choices, wizard, loopback setup API, service management and self-tests.
- `notices.py`: shared About and legal text, and the generated `docs/NOTICES.md`.

`install.sh` creates the per-user launcher and environment. `packaging/pmos/` holds the APK recipes and
kiosk/init integration; `deploy/` holds deployment, integrity and hardening scripts.

## Hardware

- `hw/`: sysfs battery reading, hardware detection and stored device profiles.

## Simulation and evaluation

- `sim.py`: battery drain, charging and optional voltage-collapse simulation.
- `demo.py`: text demonstration with simulated calls and messages.
- `storyboard.py`: narrated policy and goal demonstration.
- `evaluate.py`: scenario and policy comparisons, and the JSON benchmark policy.
- `profiles.py`: six simulated device profiles and aggregate metrics.
- `goalsim.py`: closed-loop goal simulation.
- `evaleol.py`: worn-cell service durations before and after reserve protection.
- `benchmark_model.py`: benchmark JSON loading and path resolution.

## Request flow

The page fetches `GET /api/state` (`web_static/js-8.js`); actions send token-bearing JSON to
`POST /api` (`js-3.js`, `web.py`). Simulator ticks run while the simulation is on. Only while `OPOS_MONITOR=1`, `GET /monitor`
serves the separate Inference document and `GET /api/monitor` (token required, rate limited) its data: `web.py` takes `Phone.state()`
under the lock, releases it, and only then asks `monitor.get().snapshot(...)`, which may wait seconds on a slow network host.

`web.Handler → Phone.state / _tick → Runtime.step → Governor.decide` computes the baseline, consults
the Router, validates and intersects allowances, then applies runtime reserves. `web.main` normally
wraps the Router in `GlimmerAdvisor`: with the helper visible, a state request can schedule one
background request, and later decisions use its cached extra pauses.

Why and Plan use `muse.explain` / `muse.plan`; Ask uses `assist.answer`. All of them build their prompt
from the helper facts (battery %, health, usable %, temperature, charging, what is paused and
`why_paused`) and call `muse.ask`, which POSTs to `/chat/completions`. The default Glimmer endpoint is
`http://127.0.0.1:1234/v1` with model `muse-glimmer-30b`; `OPOS_GLIMMER_URL` selects another loopback
port. Ask rests when the mode is CRITICAL, the phone is not charging, and less than 15% of the charge
is usable. The Helper app disables Send; the simulator's Ask button instead returns the resting
explanation without calling a model. The public demo answers from rules at every charge level.
Model explanations and the Plan order never execute actions.

`web.Phone` uses `DrainSim` and `ScreenModem`; the hardware reader and the ModemManager adapter are
not wired into this web runtime. The postmarketOS kiosk's `opos-server` service runs this same server, so the
kiosk is simulated in the same way.

`web.Phone` computes one final decision per tick, which the home screen, battery panel, lock-screen story,
helper facts and simulated drain all read; [design-safety-floor.md](design-safety-floor.md) describes the
hard floors, the soft pauses that Undo and foreground use may override, and the cumulative goal budget.

## Page assembly

`web.py:page` expands `@@OPOS:filename@@` placeholders from `web_static/` with `_inline_assets`, in
this order:

- CSS: `css-1.css` to `css-4.css`, inline rules, `phone-a.css`, `phone-b.css`, `jaryo.css`, `jaryo-layout.css`.
- HTML: `phone-a.html`; inside `#phone`, `phone-b.html`, `@@ABOUT_NOTICES@@`, `body-1.html` to `body-4.html`; then the simulator controls.
- JavaScript: `js-1.js` to `js-8.js`, interleaved with inline helper, thread-rendering, simulator-state and render-hook code; then separate scripts `phone-a.js`, `phone-b.js`, `jaryo.js`, `jaryo-layout.js`.

After expansion, `page` replaces `@@ABOUT_NOTICES@@` with the escaped shared notices, applies the demo
option, inserts the token meta tag and adds CSP nonces. Tests hold the page to a 102,400-byte (100 KiB) budget.

## Tests

Test modules stay flat in `tests/` so they keep their `tests.*` import identity (the installer
review suites patch targets by that name). `tests/test_web_static_loader.py` checks fragment
loading. Run discovery from the checkout root:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```
