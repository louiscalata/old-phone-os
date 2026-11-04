<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Roadmap

Where Old Phone OS Interface stands at the end of the Meta Global AI Developer Hackathon (November 4, 2026)
and what comes next. Plans are intentions, not promises.

## Working today

- Battery governor: Normal, Saver and Critical modes from battery level, battery health,
  temperature and a daily goal; calls, messages and alarms stay on in every mode.
- Helper with Meta Muse Glimmer 30B on your own computer: Why?, Plan and Ask answer from plain facts
  about the phone; a background advisor may pause extra non-essential tasks, never fewer.
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

## Simulated or not yet verified

- The November 3 Glimmer capture follow-up reduces the Why? prompt and keeps helper pause
  attribution current while the demo is stopped. Fake-route regressions cover the changes;
  Why? latency on the revised source still needs a new LM Studio capture. See
  [.pr/helper-glimmer-capture.md](.pr/helper-glimmer-capture.md).
- The desktop demo and the postmarketOS kiosk both simulate the battery, calls and texts
  (`DrainSim` and `ScreenModem` in `src/opos/web.py`). The ModemManager adapter
  (`src/opos/mm_modem.py`) is not connected to them, and the sysfs battery reader
  (`src/opos/hw/battery.py`) is used only by setup to describe the device.
- This source has not been tested on a physical phone. Earlier Surface Duo installs ran an older
  snapshot on Droidian, and that work is paused (see
  [docs/duo-usb-install.md](docs/duo-usb-install.md)).
- On-phone model inference (Llama 3.2 1B/3B) has no phone-speed measurement yet.
- Windows install runs `install.ps1`, but the Windows runtime is untested.

## Public browser demo

The separate `opos.public_demo` entry point wraps the existing phone page in a
Jaryo landing and isolates each visitor in bounded, expiring memory. Public
helpers use only battery rules; setup, model routes and host operations are
unreachable. Railway configuration and [operating notes](docs/public-demo.md)
are in this working copy. This work has not been committed, pushed or deployed.
The README URL remains a placeholder. Native socket/browser and Railway
verification must be recorded separately from handler-level checks.
The security follow-up uses the trusted proxy's right-most forwarded address
for rate limits and preserves sessions on cross-site landing visits. Both
regressions pass through the in-memory HTTP transport; native socket tests
remain blocked by workspace permissions. See [.pr/public-demo.md](.pr/public-demo.md)
for the current test and guard results.

## Next

Linux CI portability fixes are under local verification; hosted Python 3.11/3.13 results are still pending. See [.pr/ci-linux.md](.pr/ci-linux.md) for the failure analysis and verification limits.

1. **A real phone.** Boot the postmarketOS image on a supported device, run setup, and record
   battery life against the stock system with the same daily routine.
2. **Real battery, calls and texts.** Connect Phone and Messages to ModemManager through
   `src/opos/mm_modem.py` and read the real battery on a device with a working modem, keeping the
   simulated battery and modem for the desktop demo.
3. **On-phone answers.** Measure Llama 3.2 1B and 3B on an old phone and pick the default from
   the measurement, not a guess.
4. **Connectors.** Plug chat services into Messages as connectors (one unified inbox), never as
   separate apps.
5. **Internet sandbox.** Run the browser and social apps in a sandbox the governor can throttle
   or refuse, with a one-line reason.
6. **Setup gaps.** Wi-Fi joining and lock-PIN enforcement, which setup currently skips.

Ideas and bug reports are welcome as GitHub issues.
