# AI coding tools

## 1. What it is
Run the command examples from the retained source checkout.

Optional step 6 of 7 in the first-run wizard ("AI coding tools"). Installs Meta's terminal coding agent, Muse Code, into a per-tool sandbox. User-level only, no sudo. Nothing is installed unless you tick it.

The set of tools is data, not code, in `deploy/ai-tools.json` (`{"version":1,"tools":[...]}`). Adding a tool is adding one object with fields `id, name, binary, installer_url, interpreter, installer_args, installer_env, archs, min_free_mb, state_subdir, run_env, lite_env, signin, docs_url` as validated by `catalog.py`.

## 2. Where it goes
Prefix choice:

- `OPOS_TOOLS_PREFIX` if set and absolute, else
- `/userdata/opos` if `/userdata` is a writable directory, else
- `~/.local/share/opos` (e.g. `/home/u/.local/share/opos`)

Each tool gets `<prefix>/opos-tools/<id>/home` as its `HOME` during install. The vendor binary lands at `<sandbox>/.local/bin/<binary>`. A wrapper is installed at `<prefix>/opos-tools/bin/<binary>` and a marked block in the shell profile adds that bin to `PATH`.

Here, “sandbox” means that per-tool directory and its configured environment. The vendor installer and executable still run with the user's privileges; this is not an operating-system security sandbox.

If `/userdata` is not writable, automatic selection falls back to the home folder. To use an existing persistent filesystem without making its parent writable, create a tool directory you own and explicitly select it:

    sudo mkdir -p /userdata/opos && sudo chown "$USER" /userdata/opos
    export OPOS_TOOLS_PREFIX=/userdata/opos

Set `OPOS_TOOLS_PREFIX` in the environment of the setup or CLI process that installs the tools.

Before install the wizard checks `archs` against the CPU and `min_free_mb` against free space.

## 3. Lite and Full
Lite is the default. Its wrapper reads `${OPOS_POWER_SUPPLY_DIR:-/sys/class/power_supply}` and exits 75 when it identifies a battery without an external-power indication. If no battery is identified, including when the directory cannot be read, startup is allowed. Full has no start-time battery check; its running session is governed by the guard.

In Lite, the guard pauses an identified session and its descendants with `SIGSTOP` when the power reader reports battery operation, then resumes them with `SIGCONT` when external power returns. Full continues on battery while the level exceeds its reserve threshold.

When the power reader reports battery operation, Full pauses at or below the larger of its estimated essentials reserve and a fixed 20% floor; an unknown battery level also pauses it. Missing or unreadable battery identification is currently treated as external power, so the implementation does not fail closed in every unknown-power case.

Lite also sets `lite_env` from the table (e.g. disable background auto-update). Switch modes:

    PYTHONPATH=src python3 -m opos.aitools mode lite
    PYTHONPATH=src python3 -m opos.aitools mode full

Known limit: after resume an interactive shell may still show the job as stopped; press `fg` or Enter.
Known limit: sessions are recognised by the executable living inside the tool's folder, so a tool must ship a native executable (Muse Code ships one). PID reuse is a residual risk.

## 4. Sign in
OPOS never asks for, stores or logs a key. After install it prints `Sign in yourself: <signin>` from the table and you complete the vendor's own flow.
Muse Code uses Meta's models only (Muse Spark by default).

Muse Code is paid and needs a Meta account, and it runs on Mac and Windows. A free path that needs no account: the open-source opencode CLI configured with an OpenAI-compatible provider whose baseURL is your own Muse Glimmer server (default http://127.0.0.1:1234/v1). Pointing Muse Code itself at a local endpoint is described by community posts (a provider and endpoint_transport entry in ~/.config/muse/settings.json) but has not been verified by us.

## 5. Governor
`with_session_task` and `sync_dashboard` in `sessions.py` add an `app in use` essential task while a session runs. These are helpers plus the standalone guard; they are not yet wired into `web.Phone`, so the dashboard does not show it today.

## 6. Uninstall
Removes exactly what was added:

    PYTHONPATH=src python3 -m opos.aitools uninstall
    PYTHONPATH=src python3 -m opos.aitools uninstall --dry-run

Deletes `<prefix>/opos-tools` (wrappers, sandboxes including any sign-in data kept there, run dir, env, manifest) and the marked profile block. A profile file the install created and that becomes empty is deleted. Use `--dry-run` to preview.

## 7. Commands

    PYTHONPATH=src python3 -m opos.aitools list
    PYTHONPATH=src python3 -m opos.aitools install --tool <id> [--mode lite|full] [--prefix PATH] [--dry-run] [--force]
    PYTHONPATH=src python3 -m opos.aitools mode lite|full [--prefix PATH]
    PYTHONPATH=src python3 -m opos.aitools status [--prefix PATH]
    PYTHONPATH=src python3 -m opos.aitools uninstall [--prefix PATH] [--dry-run]
    PYTHONPATH=src python3 -m opos.aitools guard --run-dir PATH [--interval SECONDS]
    PYTHONPATH=src python3 -m opos.aitools power

- `list` — list catalog entries with sign-in hint.
- `install` — install one or more tools after preflight.
- `mode` — re-render wrappers for installed tools.
- `status` — show prefix, mode, installed tools and running sessions.
- `uninstall` — remove tools and profile block.
- `guard` — singleton via `run/guard.lock`; exits after 60s with no sessions.
- `power` — print `external power` or `battery`.

## 8. How it was tested
All with mocks: network, installers, `/sys/class/power_supply`, `/proc`, signals. A simulation with a stand-in binary in a temporary folder showed refusal on battery (exit 75), pause on unplug, resume on replug, and exact uninstall. This was not run on a real Surface Duo and the real vendor installers were not executed.
