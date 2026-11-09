<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Meta hackathon reference

This was a hackathon project (Meta hackathon, Oct 2026, Indie developer track). It is kept as a reference; the current OPOS interface is not in this repository.

The current OPOS interface is the Surface Duo operating system: dual-screen, foldable, running as the phone's own OS. No source tree for that interface is in this repository, and this release does not add a hardware dossier.

The hackathon interface was left on its existing paths. `git mv` into this directory would break the postmarketOS package, the runtime archive, and the tests, which import `src/opos` and `packaging/pmos` where they are.

## What is the hackathon interface

| Path | What it is |
|---|---|
| `src/opos/web.py`, `src/opos/web_static/` | The phone page. Jaryo is its design layer. `data-ff=foldable` is a layout of this page, not a second operating system. |
| `src/opos/public_demo.py`, `src/opos/public_static/` | The public browser simulation of that same page. |
| `src/opos/governor.py`, `src/opos/router.py`, `src/opos/runtime.py`, `src/opos/muse.py`, `src/opos/assist.py` | Battery rules and the optional Meta Muse Glimmer helper for the demo. |
| `src/opos/setup/` | Install wizard. Form-factor names include foldable and dual-screen. They record a preference; they do not boot a Duo OS. |
| `src/opos/hw/detect.py` | Can read a device-tree model and recognize the name Surface Duo. It does not implement the Duo interface. |
| `packaging/pmos/` | postmarketOS packages that start the same page in a kiosk. |
| `packaging/pmos/easy/` | Easy-setup scripts. Without `--flash` they print a plan and change nothing. |
| `packaging/pmos/qemu-smoke.sh` | Plan, or a tiny QEMU guest that is not postmarketOS and not OPOS. |
| `docs/pmos.md`, `docs/assets/` | QEMU notes and desktop screenshots of this reference. |
| `deploy/push-to-duo.sh`, `deploy/duo-rootfs-install.sh`, `docs/duo-usb-install.md` | Historical Droidian install notes. Not the current OS, and not a hardware dossier. |

## What is not in this repository

- The Surface Duo OS interface.
- A dual-screen window manager, system image, or device tree for that OS.
- A verified physical-phone flash path.
- A Surface Duo hardware dossier.

## How to run the reference

From the repository root, not from this directory:

```sh
PYTHONPATH=src python3 -m opos.web --port 8765
```

Open http://127.0.0.1:8765/?demo=1. Battery, calls, and messages are simulated.
