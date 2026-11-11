<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# OPOS on a stock-Android Surface Duo

Status, November 7, 2026: **running on the owner's Surface Duo** (AT&T `duo-att`, stock 2022.902.48 after a
factory reset), deployed over wireless debugging. The owner confirmed on November 11, 2026 that the phone's USB-C port carries data. Results below are from that phone on that day; they are one device and one session, not a field test.

## Results on the phone, November 7, 2026

- **Live battery:** Termux:API readings reached the lock screen and helper facts (for example 43 %, charging,
  32.7 °C, "Charging. Nothing paused."); `battery_source` reported `termux-api` with health from the charge
  counter (98 % while charging at 42-54 %; the November 4 full-charge reading gave 89 %, so this estimate is not
  yet stable across charge levels).
- **On-phone model:** llama-server with Llama 3.2 1B Instruct Q4_K_M (807,694,464 bytes) on the Snapdragon 855:
  17.7-18.4 generated tokens/s, 2.2 s per short warm answer and 5.3 s cold (84-token prompt, 39-45 tokens out).
- **Helper honesty:** with nothing paused, the 1B model asserted pauses in Why? and Ask and read the 16:00 goal as
  "16 hours". Why? with nothing paused, and Ask's pause and time-left questions on the local route, now answer
  from the rules ("battery rules, on this phone"); any model reply that calls a running task paused falls back
  to the rules. Afterwards all five checked questions answered correctly: three instantly from the rules, two
  general ones from the phone's model in 8-11 s.
- **Two screens:** Chrome opened OPOS on one screen beside Termux; spanning was not tested.

This path keeps Android. It does not flash, root or wipe the phone, and uninstalling two apps undoes it.
Removing Android is a separate route that ran on the same phone later that day, once its USB port carried data
again: [OPOS OS for the Surface Duo](../packaging/duo-os/README.md), RAM-booted from a PC, with the same server on
the real battery from sysfs. The older Droidian notes in [duo-usb-install.md](duo-usb-install.md) stay historical.

## How it fits together

```
 Surface Duo (Android 12L)                                   Owner's computer (optional)
 ┌──────────────────────────────────────────────┐            ┌────────────────────────────┐
 │ Chrome ── http://127.0.0.1:8765/ ──┐         │            │ LM Studio: muse-glimmer-30b│
 │                                    ▼         │   USB      │ on 127.0.0.1:1234          │
 │ Termux:  python -m opos.web --live-battery   │◄──adb──────┤ adb reverse tcp:1234       │
 │            │  rules, governor, helper        │  reverse   └────────────────────────────┘
 │            ├─ termux-battery-status (Termux:API) ─ level, temperature, charging, charge counter
 │            ├─ 127.0.0.1:8080 llama-server  (Llama 3.2 1B, verified by model id)
 │            └─ 127.0.0.1:1234 → the computer (Glimmer), only while the cable and reverse are up
 └──────────────────────────────────────────────┘
```

- **Server:** the same `opos.web` as the desktop demo, started with `--live-battery` (or `OPOS_BATTERY=live`).
  `LiveBattery` (`src/opos/hw/live_battery.py`) takes `DrainSim`'s place: it re-reads the battery at most every
  15 s and never simulates drain. The clock is the phone's wall clock, not the demo's noon. The state payload
  adds `battery_source` (`source`, `health_source`) so the screen can say where numbers came from.
- **Time left and the goal come from the phone's own discharge.** On a live battery the panel divides the level
  by a measured rate instead of the model's, and `battery.drain` in the state payload says which:
  `basis` (`measured` or `model`), `per_hour` (the rate in use), `measured_per_hour` (null until trusted),
  `model_per_hour`, `age_s` (seconds since the newest reading), `window_s` (discharge observed so far) and `via`
  (`level` or `counter`). The rate is an exponentially weighted average (30 min time constant) of segments between
  level steps, timed from when each step is first seen, so whole-percent rounding does not read as a burst of
  drain. It is trusted once the run spans 10 minutes from its first level step (or first counter reading) and the
  segments closed so far have lost at least half a point in all (a segment is a whole level step, or a quarter
  point of counter, so in practice one full point in level mode and two quarter points with the counter; in the
  synthetic traces that is about 15 minutes at 12 %/h and 30 at 3 %/h). Until then `basis` is `model`, the
  panel's time left and goal check keep the model's figure, and the helper's `hours_left` is null and its
  `reserve` is 0, not the model's figure; once trusted, all of them follow the measured rate. With the helper
  shown the technical line says "drain from the model, not measured yet (N of 10 min)", then "waiting for the
  next level step" (or "counter step") once the 10 minutes are in but no step has closed a segment.
  Readings arrive only while something polls: nothing samples the battery in the background, so the page's own
  refresh (one real read per 15 s at most) is the only sampler. Plugging in or out, a jump of more than five
  points, or no reading for 15 minutes (screen off, page closed) starts the run again, so measuring needs the
  page open and polling for the 10 minutes or more it takes; a look at the phone for a minute or two at a time
  will mostly show the model's figure. A gap over two minutes only drops the segment it falls in. Readings are
  timed on the boot clock, which keeps counting while the phone suspends, so a sleep shows up as a gap. A charge
  counter (Termux:API's `charge_counter`, or one battery's sysfs `charge_counter`, `charge_now` or `energy_now`)
  is used for finer positions while it stays within about a point of the shown level; if it disagrees with the
  level, or goes missing from a reading after being used, the level steps alone are used for the rest of the run.
  What is paused, and the saving steps toward the goal, still come from the model's task costs. Verified against
  synthetic discharge traces only (`tests/test_measured_drain.py`); no measurement on the phone yet, and whether
  the Duo's sysfs exposes a usable counter is unverified.
- **Calls and messages stay simulated.** `ScreenModem` still answers Phone and Messages; Android keeps the real
  modem. The page's simulation flag for the modem stays true.
- **Models:** the routes are unchanged (`src/opos/assist.py`). The phone-local route sends nothing until
  `/v1/models` on port 8080 lists `llama-3.2-1b-instruct`, which `termux-setup.sh` sets as llama-server's alias.
  Glimmer's default URL `127.0.0.1:1234` reaches a computer through `adb reverse`, with no tunnel or LAN exposure.
- **Interface:** in Settings, Auto now picks the existing two-screen layout while the window spans the hinge
  (`(horizontal-viewport-segments:2)`), without saving that choice. An app on one screen keeps the phone layout.

## Battery health on stock Android

Android does not expose `charge_full_design` to apps. Termux:API v0.53 does report the fuel gauge's
`charge_counter` (µAh), so health = charge counter ÷ (percentage ÷ 100) ÷ design capacity, used only at 30 % charge
or more, where percentage rounding stays small. Design capacity comes from `OPOS_BATTERY_DESIGN_MAH` or the model
table (Surface Duo 3,577 mAh; Surface Duo 2 4,449 mAh). Order of sources: sysfs, charge counter,
`OPOS_BATTERY_HEALTH`, then `assumed` (1.0, labelled as such).

The November 4 backup's `dumpsys battery` gives 3,179,000 µAh at 100 %, so about **89 %** health for this phone.
That is one reading from a capture, not a measurement over a charge cycle.

## Device facts used

From the owner's November 4 read-only backup (`dumpsys`, `getprop`), not from public specs unless marked:

| Fact | Value |
|---|---|
| Model / product | Surface Duo, `duo-att`, build 2022.902.48 (Android 12L, SDK 32), security patch 2023-08-01 |
| SoC / ABI | Snapdragon 855 (`SM8150`, `msmnile`), `arm64-v8a` |
| Display | One 2784 × 1800 surface at density 2.5: two 1350 px screens and an 84 px hinge, so 540 CSS px per screen and a 33.6 px hinge when spanned |
| Battery | Li-ion, 3,179,000 µAh at 100 %; design 3,577 mAh (public spec) |
| Bootloader | Already unlocked (`ro.boot.flash.locked=0`, verified boot `orange`) |
| Browsers | Chrome (system), Microsoft Edge; Termux 0.118.3 was installed (same SHA-256 as the deploy's APK) |

## Deploy and undo

From a checkout on Windows with `adb` on PATH, the phone unlocked and USB debugging allowed:

```
powershell -File deploy\duo-termux.ps1 -ApkDir <folder with the Termux and Termux:API APKs> -Model <Llama-3.2-1B-Instruct-Q4_K_M.gguf> -Bench 3
```

It installs Termux 0.118.3 and Termux:API 0.53.0 (GitHub builds, one signing key), pushes `git archive HEAD` and the
model to `/sdcard/Download/opos`, types `deploy/termux-setup.sh` into Termux, waits for `ready`, opens OPOS in the
browser and, with `-Bench`, times answers from the phone's llama-server into `duo-bench.json`.
A PIN lock blocks the typed command, so unlock the phone first.

Undo: uninstall `com.termux` and `com.termux.api` and delete `/sdcard/Download/opos`.

## Release note

This work changes runtime files, so `packaging/pmos/opos/APKBUILD` still names and checksums the published
`opos-runtime-source-20261102` archive, and `test_release_recipe_digest_matches_current_declared_runtime_bytes`
fails (91 declared files, not 90) until a new runtime archive is cut and published with its own name and
checksum. Local pmOS builds are unaffected: `OPOS_LOCAL_SOURCE=1` staging measures the staged archive itself.

## Not yet verified

- Spanning OPOS across both screens in the phone's Chrome (viewport-segment support), and battery life over a
  real day against stock Android.
- Discharging behaviour: every reading so far was while charging, so no task was paused on the real phone.
- Survival of Termux in the background over hours (it holds a wake lock; Android 12 may still stop it).
- Deploying over USB on a phone whose port works; this one used wireless debugging (`adb pair`, then
  `ANDROID_SERIAL=<ip:port>`), and the setup command is typed into Termux, so the screen must stay unlocked.
