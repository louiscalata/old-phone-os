<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# OPOS on a stock-Android Surface Duo

Status, November 7, 2026: **prepared, not yet run on the phone.** The deploy path, real-battery reader and
two-screen detection exist in source and pass tests; the target Duo was stuck in a boot loop when this was
written, so nothing below has a physical-phone result yet. Results get recorded here when they exist.

This path keeps Android. It does not flash, root or wipe the phone, and uninstalling two apps undoes it.
The Droidian route in [duo-usb-install.md](duo-usb-install.md) stays historical and paused.

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

- Any of this on the physical Duo: deploy, live battery, charge-counter health, two-screen detection in the
  phone's Chrome, on-phone Llama speed. The phone was in a boot loop on November 7.
- The helper's "on this phone" wording under Termux, which comes from `OPOS_ON_PHONE=1` in `opos-start` because
  `PHONE_BUILD` only recognises the packaged install.
- Whether the phone's Chrome supports viewport segments. Without them, spanning both screens keeps the wide
  single layout; one screen is unaffected.
