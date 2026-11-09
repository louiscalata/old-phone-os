<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Contributing

This repository is the Meta hackathon reference (October 2026, Indie developer track). The Surface Duo OS interface is not here. Changes should say which of those they touch, and should not describe the reference demo as that operating system.

## Tests

From the repository root, with Python 3.11 or newer:

```sh
PYTHONPATH=src python3 -m opos.web --port 8765
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

The full suite can take more than 30 minutes. Browser checks need Node.js 22+ and Chromium or Google Chrome. ShellCheck 0.11.0 covers `packaging/pmos/easy/*.sh` and `packaging/pmos/qemu-smoke.sh`.

`sh packaging/pmos/easy/easy-install.sh IMAGE` must keep making no changes unless `--flash` is passed. Physical-device flashing is untested; do not add a claim that a phone was flashed unless the pull request includes the evidence.

## Commits

License is GPL-3.0-or-later. Keep existing copyright and SPDX headers.

The optional checker `python3 -B guard/check.py` still enforces the hackathon commit window, which closed on 2026-11-04, and it requires a `Provenance:` trailer whose id appears in `docs/provenance.md` on a line that names a Meta model. A commit dated after that window fails the checker. That failure is the event window, not a secret scan. Do not backdate a commit to pass it.

See [SECURITY.md](SECURITY.md) for vulnerability reports.
