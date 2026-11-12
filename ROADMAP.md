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
- Everyday apps index at `/apps`, only when `OPOS_APPS` names an app ([docs/apps.md](docs/apps.md)). With the variable absent the main page is unchanged. The Duo image names Clock, Contacts, Notes and Calculator. App data stays in local files. Alarms do not ring while the clock is unknown. Sound on a real phone is not verified.
- Camera: research only ([docs/camera-spike.md](docs/camera-spike.md)). No still has been captured. No camera code.
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

- November 12 power-key design: blank is a lease checked by a one-second select loop, with fresh panel reads,
  light on uncertainty and continued recovery for missing or unwritable nodes, including at startup. Restarts have
  no count limit and a three-second delay. Production-controller fake-tree tests are local work; phone behavior,
  native systemd lifecycle and image execution remain **NOT_RUN**. See [POWER.md](packaging/duo-os/POWER.md) for
  current results and limits.
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

## On-device checks

These rows are the phone-features checks D1 through D11. Date stays empty and Result stays NOT_RUN until a phone run fills both, with an evidence link. The link in Evidence today is the feature pull request, not a device receipt. No row here records a pass.

| ID | Check | Date | Result | Evidence |
|---|---|---|---|---|
| D1 | The alarm tone plays on the speaker and steps from 20 percent to 70 percent. | | NOT_RUN | [PR #28](https://github.com/louiscalata/old-phone-os/pull/28) |
| D2 | An alarm rings when it is due, and it stays silent while the clock is unknown. | | NOT_RUN | [PR #25](https://github.com/louiscalata/old-phone-os/pull/25) |
| D3 | Clock: save an alarm, snooze, stop, and open bedside. | | NOT_RUN | [PR #26](https://github.com/louiscalata/old-phone-os/pull/26) |
| D4 | Notes are still on the phone after a restart. | | NOT_RUN | [PR #29](https://github.com/louiscalata/old-phone-os/pull/29) |
| D5 | The lock PIN works on the glass. | | NOT_RUN | [PR #31](https://github.com/louiscalata/old-phone-os/pull/31) |
| D6 | Contacts stay on the phone, including a vCard brought in from outside. | | NOT_RUN | [PR #27](https://github.com/louiscalata/old-phone-os/pull/27) |
| D7 | Battery drain with the panels soft-blanked. | | NOT_RUN | [PR #36](https://github.com/louiscalata/old-phone-os/pull/36) |
| D8 | The calculator works on the glass. | | NOT_RUN | [PR #30](https://github.com/louiscalata/old-phone-os/pull/30) |
| D9 | A camera still, and the drain while it is taken. | | NOT_RUN | [PR #32](https://github.com/louiscalata/old-phone-os/pull/32) |
| D10 | Folded posture, and bedside on the real hinge. | | NOT_RUN | [PR #21](https://github.com/louiscalata/old-phone-os/pull/21) |
| D11 | The apps index and More apps on the glass. | | NOT_RUN | [PR #23](https://github.com/louiscalata/old-phone-os/pull/23) |

## Next

Linux CI portability fixes were verified on a Debian VM. On November 10,
2026, the [hosted Ubuntu baseline](https://github.com/louiscalata/old-phone-os/pull/103)
passed the Python 3.11/3.13 matrix: each version discovered 4,880 tests with
43 explicit skips and zero failures. This does not establish a pass for later
source changes or for the skipped checks.

The combined runtime candidate for #100, #15, #16, #24, #94, #47, #75, #74,
#78, #76 and the font commits from #79 is under review. It retains release-bound
recipe checksums and dynamic runtime inventory checks from #101. The reversible
review candidate uses thumb-pane tiles and separate detail; the later #80
newspaper proposal is preserved separately, pending the final Home decision.
Exact rendered-page snapshots were regenerated. The served page with all switches
on measures 101,868 bytes against the unchanged 102,000-byte gate. Focused local
checks ran 255 tests: 253 passed and two Linux-specific fixture checks failed on
Mac, with no skips. Those failures are not passes or skips. Eight actual-script
Notes cases passed, including deletion/save ordering and edits made before a late
open response; storage error and retry tests also passed. The candidate activates
new Wi-Fi profiles by UUID, cleans private files on creation failures, and keeps
one Contacts jump target per letter bucket. Final browser and hosted Python
matrix checks remain pending for the reviewed combined source. All on-device
checks above remain NOT_RUN.

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
