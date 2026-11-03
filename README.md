<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Old Phone OS Interface (OPOS)

OPOS is Louis Calata's solo entry in the Meta Global AI Developer Hackathon with Cerebral Valley (November 2026). It is a hackathon prototype, not a finished product.

OPOS is a prototype phone interface for people who keep an older phone as its battery wears out. It pauses less essential work such as email, maps and photos backup so the battery can last until a time the owner chooses, while calls and messages stay on. Optional open-weight Meta Muse Glimmer 30B runs on the owner's computer, explains decisions in plain words when asked, and may pause extra tasks but never re-enables anything the rules paused or touches calls, messages or alarms; the rules work without it. Try the desktop demo below: it simulates battery, calls and messages and needs only Python 3.11+, no account or model download.

## Quickstart

```sh
PYTHONPATH=src python3 -m opos.web --port 8765
```

Open `http://127.0.0.1:8765/`, unlock the screen and select Phone, Messages, Helper or Settings. Run the command from the repository root. The demo is a simulation: physical phones and on-phone model inference are not tested (see [Limits](#limits)).

Demo walkthrough:

1. Open `http://127.0.0.1:8765/?demo=1` for simulator controls.
2. The demo starts at 60% battery on a worn battery with 60% health, so about 36% is usable. To last until the demo's 16:00 goal it pauses maps and photos backup; calls, messages and email stay on, and with the helper shown, Muse Glimmer's background advisor may pause email too.
3. Tap the thin bar at the top to open battery details and click Start simulation to watch it drain.
4. Click Show helper and try Why?, Plan or type a question such as "Why are maps paused?" and Ask. Muse Glimmer answers from the phone's facts. With no model running, Why answers from the rules, Plan keeps a fixed order and Ask says no model is running.
5. Drag the Battery slider below about 25% (under 15% usable). Ask rests to save power, and calls and messages still work.

![Historical desktop simulation](docs/assets/demo.gif)
Historical desktop simulator material, not current native acceptance or physical-device evidence.

![Historical slow-server stand-in](docs/assets/wait-demo.gif)
Wait GIF recorded against a stand-in slow server with simulated answer text. Not actual Glimmer timing.

## How it works

Battery state drives a rule decision, then an advisory plan and explanation. Model text cannot enable a task the rule router deferred.

The router allows essential tasks first, then everything while charging, and defers non-essential tasks at 45 C or more. With a daily goal, a non-essential task runs only if it fits the remaining energy budget; without one, optional tasks need an effective charge of 80 or more and useful tasks need enough charge for 4 hours. Effective charge = level x battery health. The governor then intersects the router's choice with its own safety floor (which still applies heat limits while charging), and the runtime adds a worn-battery reserve that keeps calls, messages and alarms (of these, the desktop demo simulates calls and messages only) and can pause even the app in use. Mode is CRITICAL (only essentials), SAVER (something paused) or NORMAL. See [docs/design-safety-floor.md](docs/design-safety-floor.md).

Agentic loop with visible helper: observe battery state -> decide with router -> plan (Plan proposes an order and does not run the steps itself) -> act -> explain -> undo. Helper text never changes what the phone does; Glimmer's background advisor can pause extra non-essential tasks, never fewer than the router.

Glimmer may only pause additional non-essential tasks. It cannot allow anything the router deferred and cannot touch calls, messages or alarms.

## Meta AI in OPOS

| Route | Purpose | Requirements and scope |
|---|---|---|
| Rules floor | Deterministic decisions and fallback text | No model or provider account. Simulations are not measured battery savings. |
| Muse Glimmer 30B | Routing advice, Why, Plan and Ask | Your own server serving the model id `muse-glimmer-30b` (for example LM Studio) at `http://127.0.0.1:1234/v1`. If port 1234 is taken, set `OPOS_GLIMMER_URL=http://127.0.0.1:<port>/v1` to point OPOS at LM Studio's actual port. On a 64 GB M5 Pro MacBook Pro running Muse Glimmer 30B (Q4_K_M) in LM Studio, answers took about 30-60 seconds in November 2026 tests, most of it hidden reasoning. |
| Llama 3.2 1B/3B | Optional phone-local answers via server at loopback port 8080 | About 1.5/4 GB free memory; phone speed is unmeasured; weights are not shipped. |
| User-owned LAN server | Optional Glimmer endpoint selected with OPOS_LAN_URL | Private-address validation; opt-in. OPOS does not expose a server for you. |
| Wikipedia | Optional snippets for one question | Unchecked-by-default Search Wikipedia option sends that question to Wikipedia. |
| Spark Open | Optional paid hosted route for Why and Plan only when battery estimate falls short of daily goal | Off by default. Needs `OPOS_SPARK_OPEN=1` and `BASETEN_API_KEY`. Ask does not use it. |

Check endpoint with:
```sh
PYTHONPATH=src python3 -m opos.muse --check
```
This check reports the model ids the endpoint serves and `model_served` (whether the exact id `muse-glimmer-30b` is among them); `--check --probe` also sends one short request to confirm the model returns a completion.

## Simulation results

All numbers below simulate the deterministic rule router against fixed rules, with no model involved; they are not measurements on a real phone.

* Simulation with worn battery: router 518 important minutes vs rules 325.
* Simulation with fresh battery: router 743 important minutes vs rules 437. The router gives up useful minutes to buy important minutes: 252 useful minutes for the router vs 403 for fixed rules.
* Averaged over 40 simulated worn phones, working calls lasted 5.52 hours with the router alone and 6.54 hours with the router plus OPOS's worn-battery reserve and burst shaping (fixed rules: 3.67 and 4.73 hours); without the reserve and burst shaping every simulated phone browned out, and with them none did.

Reproduce: `PYTHONPATH=src python3 -m opos.profiles` (about 10 s) and `PYTHONPATH=src python3 -m opos.evaleol` (about 30 s).

## Install the per-user application

From the retained source directory:
```sh
sh install.sh --yes --no-launch
export PATH="$HOME/.local/bin:$PATH"
opos run --defaults
opos status
```

The installer keeps using that source checkout and creates a per-user launcher and virtual environment. Keep the checkout. `--no-launch` leaves setup for the later `opos run` step.

Optional bootstrap:
```sh
OPOS_REPO_URL=https://github.com/louiscalata/old-phone-os OPOS_REF=main sh bootstrap.sh
```

## postmarketOS packages and QEMU

See [docs/pmos.md](docs/pmos.md) for pinned toolchain, pmaports checkout and local-source staging. [Historical Duo USB notes](docs/duo-usb-install.md) are retained separately.

## Tests

The complete suite also requires Node.js and Git on PATH, permission to bind loopback sockets, and a writable temporary directory outside the checkout. JavaScript checks skip with a reason when Node.js is unavailable. Some fixtures create disposable Git history in that temporary directory and require local commits to succeed.

Run discovery canonically from the checkout root:
```sh
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

Optional repository checks use `python3 guard/check.py`; see [guard/RULES.md](guard/RULES.md).

## Limits

**Focused package inputs; performance gains remain unmeasured.** See [docs/lean.md](docs/lean.md).

Demo is simulation only. No physical phone testing is claimed and native on-phone inference is not tested. On a 64 GB M5 Pro MacBook Pro running Muse Glimmer 30B (Q4_K_M) in LM Studio, answers took about 30-60 seconds in November 2026 tests, most of it hidden reasoning. Ask rests when the mode is Critical and less than 15% of the charge is usable. Spark Open is off by default and requires user credentials. Wikipedia search is opt-in. On Windows run `install.cmd` (which invokes `install.ps1`); Windows runtime is untested.

## Documentation

- [docs/README.md](docs/README.md): every document, grouped by what you want to do.
- [docs/architecture.md](docs/architecture.md): module map and how a request flows from the page to the governor and to Muse Glimmer.
- [ROADMAP.md](ROADMAP.md): what works today and what comes next.

## Contributions and license

Development used Meta models Muse Glimmer, Muse Spark, Muse Spark Open, Muse Wonder together with Codex, Claude and Grok Bot. The submitter is responsible for the submission. See [NEW-WORK.md](NEW-WORK.md), [WORK-BY-MODEL.md](WORK-BY-MODEL.md), [docs/provenance.md](docs/provenance.md) and [docs/META.md](docs/META.md).

GPL-3.0-or-later; [LICENSE](LICENSE) is retained verbatim. Model materials have separate licenses and are not bundled. [THIRD_PARTY.md](THIRD_PARTY.md) links upstream terms.
