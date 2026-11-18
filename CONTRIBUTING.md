<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Contributing

Old Phone OS (OPOS) is an experimental Surface Duo 1 interface. The desktop demo uses a simulated battery, calls and messages. Do not treat a desktop run as on-phone testing.

## Desktop demo

Requires Python 3.11+ and a web browser. No package installation is needed. From the extracted folder or a checkout root:

```sh
PYTHONPATH=src python3 -m opos.web --port 8765
```

Open [http://127.0.0.1:8765/?demo=1](http://127.0.0.1:8765/?demo=1) for the unlocked simulator controls. Leave the terminal running; Ctrl-C to stop.

## Tests

Tests are optional; you can finish the demo without running them. The full suite can take 30+ minutes and may create disposable Git commits in a temporary directory. For a focused web check, see [public-demo verification](docs/public-demo.md#verification).

The complete suite also needs Node.js 22+ and Git on PATH, permission to bind loopback sockets, and a writable temporary directory outside the checkout. Browser checks use Chromium or Google Chrome (`OPOS_TEST_CHROME` selects an executable; the UI check also accepts `CHROME`). JavaScript/browser checks skip with a reason when their optional local tools are unavailable. Shell lint uses ShellCheck 0.11.0.

Canonical discovery from the checkout root:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

`.github/workflows/tests.yml` runs the same command on Python 3.11 and 3.13.

Optional maintainer check, from the root of an OPOS Git checkout: `python3 -B guard/check.py`. It requires the checkout's Git metadata and is not supported for a downloaded source archive. It is not needed for the demo. See [guard/RULES.md](guard/RULES.md).

## Documentation

- [docs/README.md](docs/README.md): every document, grouped by what you want to do.
- [docs/architecture.md](docs/architecture.md)
- [ROADMAP.md](ROADMAP.md)

## Provenance and license

Development used Meta models Muse Glimmer, Muse Spark, Muse Spark Open, Muse Wonder together with Codex, Claude and Grok Bot. The submitter is responsible for the submission. See [NEW-WORK.md](NEW-WORK.md), [WORK-BY-MODEL.md](WORK-BY-MODEL.md), [docs/provenance.md](docs/provenance.md) and [docs/META.md](docs/META.md).

GPL-3.0-or-later; [LICENSE](LICENSE) is retained verbatim. Model materials have separate licenses and are not bundled. [THIRD_PARTY.md](THIRD_PARTY.md) links upstream terms.
