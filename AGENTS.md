<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# AGENTS.md

Notes for coding agents working in this repository.

## Overview

Old Phone OS (OPOS) is a phone interface for people who keep an older phone as its battery wears out. It keeps calls, messages and alarms available and pauses less essential work toward a time the owner chooses. The rules decide. A model may only pause more. It cannot turn paused work back on, and it cannot touch calls, messages or alarms.

Say what was run and what was not. Simulated battery, calls and texts stay labelled as simulated. A check that needs the physical phone stays **NOT_RUN** until that phone run is recorded.

## What OPOS is

The Surface Duo OPOS, under `packaging/duo-os/`, is the real OPOS. It is a Linux image on the community Droidian port, with OPOS as the only interface. It has been RAM-booted on one phone. It has not been flashed. Calls and texts on that image are still simulated.

The desktop demo, the public demo and the Meta hackathon material are an earlier prototype. The README labels that prototype with this sentence: "This was a hackathon project."

## Repository map

- `src/opos/` — the Python application. The standard library only. `web.py` serves the phone page.
- `tests/` — `unittest` modules, plus browser drivers.
- `packaging/duo-os/` — the Surface Duo image: units, the image build, the network polkit rule, and `opos-apps.conf`.
- `packaging/pmos/` — postmarketOS packages and QEMU scripts. Not booted on a phone.
- `deploy/` — install, manifest and device scripts. Do not point them at a phone.
- `docs/` — behaviour and limits. `docs/duo-on-device-checklist.md` is the on-device list, each item **NOT_RUN**.
- `guard/` — optional local check (`guard/check.py`, described in `guard/RULES.md`).
- `tools/` — bench and release helpers. Do not cut a tag or a release.
- `README.md` — the demo quickstart and the hackathon label.

## Setup

Python 3.11 or newer. No package install is required for the demo. From the checkout root:

```sh
python3 --version
PYTHONPATH=src python3 -m opos.web --port 8765
```

Open http://127.0.0.1:8765/?demo=1 . Leave that process running. Ctrl-C stops it. Battery, calls and messages in that demo are simulated.

## Tests

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

Run that from the checkout root. The suite needs Git, permission to bind loopback, and a writable temporary directory. Some checks need Node.js 22 (CI installs 22.23.4) and Chromium or Google Chrome (`OPOS_TEST_CHROME` or `CHROME`). A missing optional tool skips with a reason. Shell lint in CI uses ShellCheck 0.11.0. A full run can take more than 30 minutes.

## CI

`.github/workflows/tests.yml` runs on push and on pull request. The job is `unittest`, on Python 3.11 and Python 3.13. The check names are `unittest (3.11)` and `unittest (3.13)`. Both are required. Each runs the discover command above.

## Conventions

- The license is GPL-3.0-or-later. New Python, shell and `.mjs` files need `SPDX-License-Identifier: GPL-3.0-or-later` in the first lines. For `.py`, `.sh`, `.mjs` and `.ps1`, `guard/check.py` looks for a line in the first three that starts with `# SPDX-License-Identifier: GPL-3.0-or-later`.
- The application stays on the Python standard library. Do not add a third-party runtime dependency.
- Docs stay plain and factual. Do not write a device result that was not run.
- One concern per change. Do not push to `main`.
- `AGIW.md` is not in this repository. Do not add it.
- Do not create tags or releases.

## Hard rules

- Device checks stay **NOT_RUN** until someone runs them on the phone and records that run. Unit tests and a desktop browser do not change that.
- No secrets in the tree. No device identifiers: serials, IMEI, MAC addresses, bootloader fingerprints or build fingerprints.
- No device flash and no device install. Do not boot, push or write a phone from this checkout.
- Open-licensed components only. Do not add a proprietary blob, or a dependency whose license is not GPL-3.0-compatible.
- A new Debian package or a new service needs Louis's yes before it is added. `docs/lean.md` is the approved postmarketOS package and service list, and adding to it needs that same yes.
- Everyday apps stay off unless `OPOS_APPS` names them. The Duo image drop-in is `packaging/duo-os/opos-apps.conf`. Absent or empty leaves the main page unchanged.
- The device dossier stays out of the repository.
- `page()` and the home page served with the switches on stay at or under the 102,000-byte gate (`WAVE_GATE` in `tests/test_uxp30_budget.py`). That file also keeps a 102,400-byte hard cap (`PAGE_BUDGET`).

## Where to read next

- `README.md` for the demo and the hackathon sentence.
- `packaging/duo-os/README.md` for the Duo image.
- `docs/duo-os-final.md` for what is simulated and what is not known.
- `docs/duo-on-device-checklist.md` for the **NOT_RUN** device list.
- `docs/ui-byte-ledger.md` for the page-size gate.
