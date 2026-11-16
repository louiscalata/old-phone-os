# Developer tools

This folder holds helpers for people working on OPOS. None of them runs on the phone. `opos-release.py` is described in
[docs/ota-release-howto.md](../docs/ota-release-howto.md). This page covers the real-GTK check.

## The real-GTK check

The native shell (`src/opos/native`) is tested everywhere against a stand-in toolkit. That cannot show what GTK 4 makes of
a page: how tall it needs to be, or whether the thumb pane and its bottom bar end up on the screen. The real-GTK check
builds the real `Shell` under real GTK 4 and measures it.

| File | What it is |
| --- | --- |
| `tools/gtk-headless.sh` | Starts a headless Weston, 1392x900, in a private runtime folder, runs one command under it, then stops Weston and removes the folder. |
| `tests/gtk_real_worker.py` | The script that runs inside that session. It builds the Shell with every everyday app on (`OPOS_APPS`, so Clock, Contacts, Notes, Calculator, Call log and the server-backed Converter are all pages) and fills the Converter with the server's own list of measures (`opos.convert.snapshot()`), so its buttons have their real words. Then, for each page, each hand, and each layout (two panes, back mode on the left screen, back mode on the right screen), it checks that every row's minimum height is at most 900, that the reading pane and the thumb pane are inside the window, and that a tap on Home's Open button lands on it. |
| `tests/test_native_gtk_real.py` | Runs the worker in its own process and fails with its failed checks. The same file tests the plain rules (what fits, when to skip) and the script's safety, and those run everywhere. |
| `tests/gtk_real_rules.py` | The plain rules, with no GTK import. |

### Running it

On a Linux machine (a WSL distribution works) with `python3-gi`, `gir1.2-gtk-4.0`, `weston`, `dbus-daemon` and a font such
as `fonts-dejavu-core` installed, from the checkout root:

```sh
dbus-run-session -- sh tools/gtk-headless.sh . python3 -B -m unittest tests.test_native_gtk_real
```

The script takes the checkout folder and then the command. It sets `XDG_RUNTIME_DIR` (a new folder under `$TMPDIR` or
`/tmp`, removed when the command ends, or reported by name if the command left files in it), `PYTHONPATH=<checkout>/src`, `GDK_BACKEND=wayland`, `WAYLAND_DISPLAY`,
`OPOS_SPAN=675,42`, `OPOS_GTK_HEADLESS=1` and `GSETTINGS_BACKEND=memory` (GTK keeps its settings in memory and leaves no `dconf` folder behind). It installs nothing, uses no network and needs no administrator rights. It
exits 2, and starts nothing, when called wrongly or when `weston` is not installed.

The worker is hermetic: when `OPOS_SERVER_URL` is unset or empty it sets it to `http://127.0.0.1:1`, a loopback address
nothing listens on, so the shell's poll thread is refused at once instead of asking `http://127.0.0.1:8765`, where a real
OPOS server might be running. A value the caller sets is left alone.

Without that script the real test is collected and **skipped**: it runs only when `OPOS_GTK_HEADLESS` is set, because
another display has another size. It also skips when GTK 4 or a display is missing; `OPOS_REQUIRE_GTK=1` turns that skip
into a failure. The test module does not import GTK; only the worker does.

### Not in CI

The check is not run by `.github/workflows/tests.yml`. The workflow's runner has no GTK 4 session set up for it, and this
change adds no workflow. A step could be added once a runner is known to provide the packages above: install them, then
run the command above. That step has not been tried.

### What has been measured

The PC session that wrote the first version reported 515 checks passing under WSL with Weston, and that the check fails
when a thumb pane is made too tall (peer-measured, on a shell that had its own local Converter page). This version was
adapted to the merged shell and its server-backed Converter on a Mac that has no Weston, so the real run is
**NOT_RUN** here. On the Mac the test is collected and skipped, and the plain rules and the script's checks pass.
