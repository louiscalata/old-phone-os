<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# AGENTS.md

Facts for coding agents. Deeper docs are linked. Do not copy them here.

## What this is

OPOS is a phone operating system for the Microsoft Surface Duo 1. It runs a Linux image on the community Droidian port, with OPOS as the only interface on both screens. Calls, messages, and alarms stay available. Other work can pause so the battery lasts until a time the owner chooses. It is for people who rely on a hand-me-down phone, with no account and no cloud by default.

The Surface Duo OS in this repository is the real OPOS. The desktop demo, the public demo, and the Meta hackathon material are a reference. The README labels that material "This was a hackathon project."

## Repo map

| Path | What it is |
|---|---|
| `src/opos/` | The Python server, governor, phone UI, and everyday apps. Standard library only. |
| `src/opos/web.py` | Loopback HTTP server and `page()`. |
| `src/opos/web_static/` | Fragments inlined into the main page. |
| `tests/` | `unittest` suite. |
| `docs/` | The document index is `docs/README.md`. |
| `packaging/duo-os/` | The Duo image. Do not flash or install it unless Louis says so. |
| `packaging/pmos/` | postmarketOS packages and the runtime source archive. |
| `deploy/` | Shell and PowerShell used by packaging and CI lint. |
| `guard/` | Optional maintainer check. Not a CI job. |
| `.github/workflows/tests.yml` | The only CI workflow. |

## Setup

Python 3.11 or newer. No pip packages. From the checkout root:

```sh
PYTHONPATH=src python3 -m opos.web --help
PYTHONPATH=src python3 -m opos.web --port 8765
```

Leave the server running and open http://127.0.0.1:8765/?demo=1 . Stop it with Ctrl-C. `?demo=1` is simulated battery, calls, and messages. `install.sh --help` prints the per-user installer. Do not pass `--yes` unless Louis asked for an install.

## Build

There is no compile step. This builds the declared runtime archive and prints its file count and SHA-512:

```sh
python3 packaging/pmos/make-runtime-source.py --output /tmp/opos-runtime-source.tar.gz
```

Image scripts under `packaging/duo-os/` and `deploy/` are not part of this check. Do not run them against hardware.

## Test

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

The suite needs Node.js 22 or newer, Git, a loopback socket, and Chromium or Google Chrome (`OPOS_TEST_CHROME` or `CHROME`). Browser checks skip, with a reason, when those tools are missing. CI installs them. The run can take about 30 minutes.

## Lint

CI uses ShellCheck 0.11.0:

```sh
shellcheck deploy/make-manifest.sh deploy/verify-manifest.sh
```

## CI

`.github/workflows/tests.yml` runs the unittest command on Python 3.11 and 3.13 for every push and pull request. The main branch ruleset requires both **unittest (3.11)** and **unittest (3.13)**. Merge only when those two are green. `tests/test_uxp23_polish.py` portrait is flaky: rerun that check once before treating it as a real failure.

## Conventions

One small pull request per concern. Never push to `main`. Docs stay plain and factual: no hype, and a simulated or practice feature says so. On-device work that has not been run is **NOT_RUN**. Do not turn that into a pass.

## Hard rules

- No secrets, tokens, or private keys in the tree, fixtures, or commit messages.
- No device identifiers: serials, IMEI, IMSI, ICCID, MAC addresses, SSIDs, private IPs, bootloader details, or build fingerprints. Fixtures use obviously fake values such as `+15550100`.
- Do not record a test or an on-device check as passed unless that run happened.
- No git tags and no GitHub releases unless Louis says yes.
- Never edit `AGIW.md`.
- Open-licensed components only. The project license is GPL-3.0-or-later. `guard/check.py` lists the other open licenses a dependency may use.
- A new Debian or Alpine package, or a new system service, needs Louis's yes, plus an update to `docs/lean.md` and `tests/test_lean_image.py`.
- No flash, boot, or install on a device unless Louis says so.
- Everyday apps stay off unless `OPOS_APPS` names them (`clock,contacts,...`). Absent or empty leaves the main page unchanged. Each app is its own route. See `docs/apps.md`.
- `page()` and the home page served with every switch on stay at or under **102,000 bytes**. The hard test cap on `page()` alone is 102,400. See `docs/ui-byte-ledger.md` and `tests/test_uxp30_budget.py`. Do not add an app inline.
- The Duo hardware dossier stays out of this repository. Do not add it.

## Where to read next

- `README.md` — what OPOS is, the demo, and current limits.
- `docs/README.md` — the document index.
- `docs/architecture.md` — modules and request flow.
- `docs/design-safety-floor.md` — calls, messages, and alarms stay available.
- `docs/jaryo.md` — text-only, two-color UI.
- `docs/lean.md` — packages and services.
- `docs/security.md` — threat model. Treat its "fixed" notes as historical.
- `ROADMAP.md` — working today, and what is still simulated or unverified.
- `guard/RULES.md` — the optional `python3 -B guard/check.py` maintainer check. It is not CI.
