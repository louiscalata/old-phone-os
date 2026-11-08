<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Roadmap

Status as of November 7, 2026: where Old Phone OS Interface stands
and what comes next. Plans are intentions, not promises.

## Working today

- Battery governor: Normal, Saver and Critical modes from battery level, battery health,
  temperature and a daily goal; calls and messages stay on in every mode.
- Helper with Meta Muse Glimmer 30B on your own computer: supports Why?, Plan and Ask using plain
  facts about the phone. In the final November 3 desktop capture, all four Ask and all four Why?
  requests returned Muse Glimmer within 115 s, with no fallback. Ask took 51.699/53.077 s in dark
  appearance and 42.914/44.782 s in light; Why? took 66.549/60.696 s in dark and 63.853/78.651 s
  in light. These are UI response times, not pure inference or on-phone measurements. See the
  [README capture table](README.md#muse-glimmer-answering). A background advisor may pause extra
  non-essential tasks, never fewer.
- Offline floor: every decision and fallback answer works with no model running.
- Desktop demo with a simulated battery, calls and texts (`?demo=1` simulator controls).
- Setup wizard, per-user installer for macOS and Linux, and postmarketOS packages
  (`opos`, `postmarketos-ui-opos`) that start OPOS as a full-screen kiosk. The kiosk runs the
  same server as the desktop demo, with the same simulated battery, calls and texts: it does not
  read the phone's battery or use its modem yet. On November 3, 2026, an aarch64 image built from
  this tree reached the lock screen and Home in QEMU without a manual renderer override, using the
  packaged tinydm renderer hook. See [docs/pmos.md](docs/pmos.md) for that run and its limits; an
  image built from other source needs its own boot check. An earlier run that set the renderer by
  hand is historical.
- Surface Duo with OPOS as the whole OS ([packaging/duo-os](packaging/duo-os/README.md)): on November 7,
  2026 an image built from this repository's source (the revision is recorded on the image) ran on the owner's Duo, RAM-booted from a PC with no Android, and the
  owner confirmed on the screens that it was working. Logs read from the phone showed phoc on the
  hwcomposer backend (GLES on the Adreno 640) and OPOS reading the real battery from sysfs. One phone, one
  session, with the owner watching: it is RAM-boot only, so a power cycle returns the phone to Android and
  wipes the image.
- Surface Duo on stock Android ([docs/duo-android.md](docs/duo-android.md)): the same server runs under
  Termux with the real battery from Termux:API, and Llama 3.2 1B was timed at 17.7-18.4 generated tokens/s
  on the phone, on November 7, 2026, in one session.

## Simulated or not yet verified

- The desktop demo and the postmarketOS kiosk both simulate the battery, calls and texts
  (`DrainSim` and `ScreenModem` in `src/opos/web.py`). Only the Surface Duo routes, started with
  `--live-battery`, read the phone's real battery (`LiveBattery`). Calls and texts are simulated on every
  route: the ModemManager adapter (`src/opos/mm_modem.py`) is not connected, although the Duo OS image
  carries ModemManager.
- The postmarketOS kiosk has not been tested on a physical phone. Earlier Surface Duo installs ran an
  older snapshot on Droidian; those notes are historical (see
  [docs/duo-usb-install.md](docs/duo-usb-install.md)).
- The Duo OS image has no measurement of speed, battery life or stability, and was seen on one phone in one
  session. It cannot be installed permanently: the bootloader refuses flashes of the boot partition, so every
  start is a RAM-boot from a PC. Its failure path (a failed OPOS shell falling back to phosh) and shell
  switching have not been run on the phone.
- On-phone model inference: Llama 3.2 1B was timed on the Duo under Android (see above); Llama 3.2 3B has no
  phone-speed measurement, and nothing was timed on the Duo OS image.
- Windows install runs `install.ps1`, but the Windows runtime is untested.

## Public browser demo

The public demo is live at [opos-demo-production.up.railway.app](https://opos-demo-production.up.railway.app).
The separate `opos.public_demo` entry point wraps the existing phone page in a
Jaryo landing and isolates each visitor in bounded, expiring memory. Muse Glimmer
and every other model route are off there: public helpers use only battery rules.
Setup and host operations are unreachable. Railway configuration and
[operating notes](docs/public-demo.md) are included in this tree. This is a browser
simulation; the live deployment does not establish physical-phone use.

The public server uses the trusted proxy's right-most forwarded address for rate
limits and preserves sessions on cross-site landing visits. The final source also
includes the public Settings layout, battery history, app status, rules-helper,
simulator-control and link-checker fixes. Handler-level checks, native browser
checks and deployment observations remain separate evidence.

## Next

Linux CI portability fixes were verified on a Debian VM. Hosted CI results for
the Python 3.11/3.13 matrix in `.github/workflows/tests.yml` are still pending;
the Debian verification does not establish a hosted CI pass.

1. **Battery life on a real phone.** Run a Duo route (the OS image or Android with Termux) through
   the same daily routine as the stock system and record battery life; no such measurement exists.
2. **Real calls and texts.** Connect Phone and Messages to ModemManager through
   `src/opos/mm_modem.py` on a device with a working modem, keeping the simulated modem for the
   desktop demo.
3. **On-phone answers.** Measure Llama 3.2 3B, and 1B over more than one session, on an old phone
   and pick the default from the measurement, not a guess.
4. **Connectors.** Plug chat services into Messages as connectors (one unified inbox), never as
   separate apps.
5. **Internet sandbox.** Run the browser and social apps in a sandbox the governor can throttle
   or refuse, with a one-line reason.
6. **Setup gaps.** Wi-Fi joining and lock-PIN enforcement, which setup currently skips.

Ideas and bug reports are welcome as GitHub issues.
