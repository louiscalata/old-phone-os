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
  (`opos`, `postmarketos-ui-opos`) that start OPOS as a full-screen kiosk. On November 3, 2026,
  an aarch64 image built from this tree reached the lock screen and Home in QEMU without a manual
  renderer override, using the packaged tinydm renderer hook. See [docs/pmos.md](docs/pmos.md)
  for the observation and its limits; acceptance of an exact release artifact requires its recorded
  source/image identity. Earlier QA-only renderer observations are historical.

## Simulated or not yet verified

- The desktop demo simulates the battery, calls and texts.
- OPOS has not been accepted on a physical phone; Surface Duo work is paused (see
  [docs/duo-usb-install.md](docs/duo-usb-install.md)).
- On-phone model inference (Llama 3.2 1B/3B) has no phone-speed measurement yet.
- Windows install runs `install.ps1`, but the Windows runtime is untested.

## Next

1. **A real phone.** Boot the postmarketOS image on a supported device, run setup, and record
   battery life against the stock system with the same daily routine.
2. **Real calls and texts.** Connect Phone and Messages to ModemManager on a device with a working
   modem, keeping the simulated modem for the desktop demo.
3. **On-phone answers.** Measure Llama 3.2 1B and 3B on an old phone and pick the default from
   the measurement, not a guess.
4. **Connectors.** Plug chat services into Messages as connectors (one unified inbox), never as
   separate apps.
5. **Internet sandbox.** Run the browser and social apps in a sandbox the governor can throttle
   or refuse, with a one-line reason.
6. **Setup gaps.** Wi-Fi joining and lock-PIN enforcement, which setup currently skips.

Ideas and bug reports are welcome as GitHub issues.
