<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Source module map

A file-by-file map of `src/opos/`, updated for AI mode and GPU Link. Descriptions are taken from each module's docstring, or from the module's own classes and functions when it has no module docstring. This is an inventory, not a claim that the modules have been re-tested here. For request flow and page assembly, see [architecture.md](architecture.md).

Counted with `git ls-files src/opos`: **123 Python modules** and **50 other files**, **173 tracked files** in total. The count includes subpackages and package markers. It excludes untracked files, including local `__pycache__` and `.pyc` files. Of the Python modules, 78 are directly under `src/opos/`; 45 are in subpackages.

## Python modules

### Package root

- `__init__.py`: Package init; exits if the interpreter is older than Python 3.11.

### Battery policy

- `governor.py`: Power state, mode, priority and task types; deterministic `rule_decide` baseline; advisor validation and safety intersection in `Governor`.
- `router.py`: Battery-aware routing of tasks by power, heat, and goals.
- `runtime.py`: The final decision: the policy's soft pauses, then the owner's overrides, then the hard floors.
- `battery.py`: Battery drain and reserve helpers.
- `budget.py`: `Budget` and `budget_for`: mode-dependent CPU, brightness, synchronization and radio budgets, plus `allows_app`.
- `power.py`: Saving-step costs, goal planning (`plan`, `plan_reason`), protected-task hours and low-power wording.
- `reserve.py`: Worn-battery reserve level, the names it still runs, burst shaping, shutdown and call-feasibility checks.
- `eol.py`: Simulated open-circuit voltage, resistance, brownout, burst floor, call reserve and shutdown arithmetic for worn cells.

### Helper and models

- `advisor.py`: Asynchronous, cached Muse Glimmer advice (`GlimmerAdvisor`) that can only add pauses; advice is reused within a half-hour goal bucket, with at most one request in flight.
- `assist.py`: Ask routing (local, LAN and other routes), the rest rule (`is_resting`), memory checks, optional search, rules-based answers for pause and time-left questions, and fallback messages.
- `muse.py`: Model routes, HTTP chat completions (`ask`), streamed completions (`stream`), Why/Plan helpers, endpoint checks, and a shared deadline across routes.
- `ai_answers.py`: The shared local answer store, with bounded plain text and answer state transitions.
- `gpu_link_proto.py`: GPU Link message validation, signatures and protocol limits.
- `gpu_link.py`: Enrolment, model-file sharing and leased answer work for one PC.
- `gpu_link_server.py`: The optional loopback GPU Link listener and its start/stop lifecycle.

### Phone services

- `essentials.py`: `Modem` protocol, number normalization, `Message`, and the `Essentials` calls and validated-messages retry queue.
- `contacts.py`: `Contact` and `Book` lookup, plus message-thread presentation (`threads`, `thread`).
- `vcard.py`: A small vCard 3.0 reader and writer. FN, N, TEL and NOTE are kept. Other fields are counted and skipped.
- `mm_modem.py`: ModemManager modem foundation.
- `call_events.py`: Optional modem observer, separate from the Modem protocol's three methods.
- `calllog.py`: A bounded local call log. `record(event)` is the optional modem observer; recording is enabled only when `OPOS_APPS=calls`.
- `inbox.py`: A local notice list and quiet hours.

### Everyday apps and pages

- `apps.py`: The everyday-app list. `OPOS_APPS` names which apps are on; absent or empty means none.
- `apps_page.py`: GET /apps and the More apps link.
- `appdoc.py`: The shared document for an everyday app.
- `home.py`: Build home screens and check quiet output.
- `calls_page.py`: Call log page and read/clear API.
- `contacts_page.py`: Local contacts. The practice phone still refuses a real call. Demo rows appear only in an empty book.
- `notes_page.py`: Notes. One text file per note, kept on the phone. Plain text only.
- `calc_page.py`: Calculator. The arithmetic runs in the page. Nothing is stored.
- `convert.py`: Length, mass, volume and temperature.
- `convert_page.py`: Converter. Length, mass, volume and temperature, on this phone. Nothing is stored.
- `clock_page.py`: The Clock page: alarms, a timer, a stopwatch and a bedside clock.
- `alarms.py`: Alarm and timer scheduler. Pure: no sound, no thread, no network.
- `alarm_sound.py`: Play an alarm tone through paplay and, only after a self-test file says so, a haptic pulse.
- `net.py`: The phone's network, read honestly and changed only where the system allows it.
- `net_page.py`: The Network page (GET /network) and its POST actions net_status, net_scan, net_join, net_forget, net_wifi.
- `notices.py`: Shared legal and about text: one source for the kiosk About screen, the install-time setup screens, the desktop installers and the case study.
- `notices_page.py`: The notice list and quiet hours.
- `ai_page.py`: AI mode's phone and GPU panes, question API and phone streaming worker; enabled only by `OPOS_APPS=ai`.
- `firstrun.py`: First run on the Duo OS: the setup page, the time zone page, and the flag that says setup was shown.
- `firstrun_page.py`: The two small pages of the Duo OS first run: /firstrun (a few choices, all skippable) and /timezone.
- `timezone.py`: The owner's time zone, applied without root.

### Web UI and security

- `web.py`: Loopback HTTP server, API dispatch, the simulated `Phone`, helper wording (`why_paused`, `lock_story`), and page assembly.
- `secure.py`: Loopback server security helpers.
- `dashboard.py`: Battery details, goals, history, saving plans and undo state for the pull-down dashboard, with 12-hour time wording.
- `ui.py`: Separate Tk demo (`PhoneApp`) and the in-memory `ScreenModem` used by the simulation, which can emit practice call events.
- `monitor.py`: Inference monitor data for the OPOS kiosk.
- `monitor_web.py`: The web side of the Inference page: what opos.web needs to serve GET /monitor and GET /api/monitor.
- `public_demo.py`: Explicit public, ephemeral simulation. Never start this through `opos.web`.
- `osk.py`: The Duo OS on-screen keyboard: a small in-page web keyboard that opos.web serves only on request.
- `panes.py`: Panes: a black screen and a white screen on a window that spans both panels of a two-screen phone.
- `span.py`: Span: one browser window covering both panels of a two-screen phone and the hinge between them.
- `statusbar.py`: What the status bar and Home say about the phone's own facts (interface plan W1-05, W1-06).
- `fonts.py`: Newsreader and Inter Tight, both SIL Open Font License.
- `lockpin.py`: Optional lock PIN. The hash is PBKDF2-HMAC-SHA256. The server starts enforcing only when `OPOS_LOCK=1`.
- `practice.py`: Practice mode: the words and the one rule that keep the simulated phone honest.

### Clock, display, input and volume

- `clock_health.py`: Shared clock trust rule for the server and the image self-test.
- `clockcheck.py`: Is the system clock plausible? A phone that has never had its time set reports 1970.
- `display.py`: One brightness value for both Duo panels.
- `powerkey.py`: A Duo blank is a lease, justified again on each nonblocking input-loop tick.
- `volume.py`: Read-only, explicitly simulated volume state; no mixer is connected.
- `volumekey.py`: Duo volume keys: simulated state only until OPOS has a media/ringer mixer.

### Updates, identity and storage

- `update_client.py`: Over-the-air updates, the unprivileged half (docs/ota-update.md, "Client").
- `update_page.py`: GET /update: the software-updates page.
- `version.py`: Read the application version from its source, current OTA release or package.
- `image_identity.py`: Read and check the baked image identity.
- `store.py`: Local files for everyday apps. JSON under the state directory, mode 0600, no network.
- `tokenfile.py`: The page token on disk: made once, private, and kept across restarts (opos.web --token-file).

### Native shell (native/)

- `native/__init__.py`: The native OPOS shell (GTK 4).
- `native/blank.py`: Both screens dark while the lid is closed, and exactly as bright as before once it opens.
- `native/displayconfig.py`: The one piece of org.gnome.Mutter.DisplayConfig the phone's lid daemon asks for: the PowerSaveMode property.
- `native/facing.py`: Which screen faces the user while the Duo is folded back.
- `native/hinge.py`: The hinge angle of the Surface Duo 1, read from the phone's sensor service (sensorfw, D-Bus name com.nokia.SensorService on the system bus).
- `native/layout.py`: The parts of the native shell that need no toolkit: the hinge gap, which hand, and what a swipe or a drag means.
- `native/logic.py`: What the native screens work out for themselves, with no toolkit: typed text, sums, and the lines shown for an alarm, a timer and a call.
- `native/netlogic.py`: What the native Network screen works out for itself, with no toolkit: the rows of networks, the line under each, whether a password is needed, the radio sentences, the replies, and the typed password, which is held so it cannot be shown.
- `native/posture.py`: How the phone is held, as far as Linux can tell.
- `native/screens.py`: The screens of the native shell.
- `native/shell.py`: The native OPOS shell: a GTK 4 window, drawn by the phone's GPU, in place of the browser kiosk.
- `native/updatelogic.py`: The native Updates screen's view of the existing updater response: status, progress and available actions.
- `native/ui.py`: The native shell's few widgets and its one stylesheet.

### Setup and install (setup/)

- `setup/__init__.py`: Package marker for the setup command and first-run package.
- `setup/__main__.py`: Run the Old Phone OS Interface setup command line with python -m opos.setup.
- `setup/apply.py`: Apply and uninstall orchestration for first-run wizard.
- `setup/buttons.py`: On-screen button prompts for first-boot setup.
- `setup/cli.py`: Command line interface for Old Phone OS Interface setup.
- `setup/config.py`: Setup configuration directory, defaults, form factor, and field checks for device, services and the daily goal.
- `setup/device.py`: Device detection for first-run wizard.
- `setup/jaryo_steps.py`: HTML and CSS fragments for the Jaryo-styled first-run steps (`fragment`), including the edition panel.
- `setup/jaryo_steps_js.py`: Embedded front-end script for those setup steps (edition, resume, skip-all, and token-bearing requests).
- `setup/notices_screen.py`: Install notices, readable before setup applies any changes.
- `setup/prefs.py`: Load, validate, save and skip first-run preference answers, including resume and finish.
- `setup/selftest.py`: Fast install-time self-test for Old Phone OS Interface.
- `setup/server.py`: Loopback web server for the Old Phone OS Interface first-run setup wizard.
- `setup/services.py`: Render and install user-level services for Old Phone OS Interface.
- `setup/wizard_page.py`: Single-page setup wizard for Old Phone OS Interface.

### Hardware (hw/)

- `hw/__init__.py`: Device profiles and detection.
- `hw/battery.py`: Battery sysfs reader.
- `hw/detect.py`: Detects a known phone, a generic profile, or either (`detect`, `detect_generic`, `detect_or_generic`).
- `hw/device.py`: Hardware facts of a supported phone, declared once.
- `hw/facts.py`: Compare a phone's declared hardware facts (opos.hw.device) with what its kernel reports.
- `hw/live_battery.py`: The phone's real battery, behind the same attributes the web Phone reads from DrainSim.
- `hw/store.py`: Stored `Battery` and `Profile` records, including built-in profile names.

### Optional coding tools (aitools/)

- `aitools/__init__.py`: Package marker for the optional coding-tools package.
- `aitools/__main__.py`: Run coding tools CLI via python -m opos.aitools.
- `aitools/catalog.py`: Loads and validates tool definitions (`Tool`) from the coding-tools catalog.
- `aitools/cli.py`: Command-line subcommands for the optional coding tools.
- `aitools/installer.py`: Downloads, verifies and installs a catalogued coding tool.
- `aitools/launcher.py`: Writes or removes the shell wrapper block marked `# >>> opos coding tools >>>`.
- `aitools/place.py`: Install location and architecture helpers, including Lite and Full modes.
- `aitools/power.py`: Reads battery supply state from sysfs (`/sys/class/power_supply`), or from `OPOS_POWER_SUPPLY_DIR`.
- `aitools/sessions.py`: Session tracking and power-aware pausing.
- `aitools/wizard.py`: Resolves the coding-tools table path, including `OPOS_AI_TOOLS_TABLE`.

### Simulation and evaluation

- `sim.py`: Battery-drain simulations for normal and end-of-life batteries.
- `demo.py`: Text demonstration with a logging modem (`LogModem`) and simulated calls and messages.
- `storyboard.py`: Narrated policy and goal demonstration (`We're giving more capabilities and life to an old phone.`).
- `evaluate.py`: Benchmark battery policies on DrainSim.
- `profiles.py`: Measure the governor on six device profiles.
- `goalsim.py`: Closed-loop goal simulation (`simulate`) over a drain loop.
- `evaleol.py`: EOL evaluation helpers.
- `benchmark_model.py`: Shared model loader for benchmark harnesses.

## Other files under `src/opos/`

These 50 files are static assets (HTML, CSS, JavaScript, fonts, licenses and one sound). They are grouped by directory. Individual fragment files under `web_static/` are the ordered pieces `web.py` assembles into the main page, plus a few standalone sheets.

### `apps_static/`

Front-end scripts for everyday app pages.

- `apps_static/calc.js`: Calculator page script.
- `apps_static/home.js`: Home page script.

### `osk_static/`

On-screen keyboard stylesheet and script.

- `osk_static/osk.css`: On-screen keyboard styles.
- `osk_static/osk.js`: On-screen keyboard script.

### `panes_static/`

Dual-pane layout CSS and script, including the panes-mirror sheet.

- `panes_static/panes-mirror.css`: Mirrored dual-pane styles.
- `panes_static/panes.css`: Dual-pane layout styles.
- `panes_static/panes.js`: Dual-pane layout script.

### `public_static/`

Standalone public-demo HTML, CSS and JavaScript.

- `public_static/public-demo.css`: Public demo styles.
- `public_static/public-demo.html`: Public demo document.
- `public_static/public-demo.js`: Public demo script.

### `sounds/`

Alarm tone used by `alarm_sound.py` (`alarm.wav`).

- `sounds/alarm.wav`: Alarm tone played through paplay.

### `web_static/`

Ordered CSS, HTML and JavaScript fragments assembled into the main page, plus the optional Inference-page assets, Jaryo sheets, PIN and volume scripts, and OFL font files.

- `web_static/body-1.html`: Main-page HTML fragment 1.
- `web_static/body-2.html`: Main-page HTML fragment 2.
- `web_static/body-3.html`: Main-page HTML fragment 3.
- `web_static/body-4.html`: Main-page HTML fragment 4.
- `web_static/css-1.css`: Main-page CSS fragment 1.
- `web_static/css-2.css`: Main-page CSS fragment 2.
- `web_static/css-3.css`: Main-page CSS fragment 3.
- `web_static/css-4.css`: Main-page CSS fragment 4.
- `web_static/fonts/INTERTIGHT-OFL.txt`: SIL Open Font License text for Inter Tight.
- `web_static/fonts/NEWSREADER-OFL.txt`: SIL Open Font License text for Newsreader.
- `web_static/fonts/inter-tight-400-latin.woff2`: Latin Inter Tight regular face.
- `web_static/fonts/inter-tight-700-latin.woff2`: Latin Inter Tight bold face.
- `web_static/fonts/newsreader-400-latin.woff2`: Latin Newsreader regular face.
- `web_static/fonts/newsreader-700-latin.woff2`: Latin Newsreader bold face.
- `web_static/fonts.css`: Newsreader and Inter Tight `@font-face` rules (SIL OFL); served as `/fonts.css`, not part of `page()`.
- `web_static/jaryo-layout.css`: Jaryo layout rules.
- `web_static/jaryo-layout.js`: Jaryo layout script.
- `web_static/jaryo.css`: Jaryo visual rules for Home and related chrome.
- `web_static/jaryo.js`: Jaryo front-end behavior for the web UI.
- `web_static/js-1.js`: Main-page JavaScript fragment 1.
- `web_static/js-2.js`: Main-page JavaScript fragment 2.
- `web_static/js-3.js`: Main-page JavaScript fragment 3.
- `web_static/js-4.js`: Main-page JavaScript fragment 4.
- `web_static/js-5.js`: Main-page JavaScript fragment 5.
- `web_static/js-6.js`: Main-page JavaScript fragment 6.
- `web_static/js-7.js`: Main-page JavaScript fragment 7.
- `web_static/js-8.js`: Main-page JavaScript fragment 8.
- `web_static/monitor-theme.js`: Theme helper for the optional Inference page.
- `web_static/monitor.css`: Styles for the optional Inference page.
- `web_static/monitor.html`: Document for the optional Inference page.
- `web_static/monitor.js`: Script for the optional Inference page.
- `web_static/phone-a.css`: Phone-shell hook-point stylesheet (part A).
- `web_static/phone-a.html`: Phone-shell hook-point markup (part A).
- `web_static/phone-a.js`: Phone-shell hook-point script (part A).
- `web_static/phone-b.css`: Phone-shell hook-point stylesheet (part B).
- `web_static/phone-b.html`: Phone-shell hook-point markup (part B).
- `web_static/phone-b.js`: Phone-shell hook-point script (part B).
- `web_static/pin.js`: PIN pad script; loaded only while `OPOS_LOCK=1`.
- `web_static/volume.js`: Volume-meter script that reuses the state poll and does not change sound.
