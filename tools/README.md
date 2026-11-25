# Developer tools

This folder holds helpers for people working on OPOS. None of them runs on the phone. `opos-release.py` is described in
[docs/ota-release-howto.md](../docs/ota-release-howto.md). This page covers the GPU Link stand-in and the
real-GTK check.

## The real-GTK check

The native shell (`src/opos/native`) is tested everywhere against a stand-in toolkit. That cannot show what GTK 4 makes of
a page: how tall it needs to be, or whether the thumb pane and its bottom bar end up on the screen. The real-GTK check
builds the real `Shell` under real GTK 4 and measures it.

| File | What it is |
| --- | --- |
| `tools/gpu-link-soak.py` | Loopback fault and restart gates; JSON/MD/log output. `--help`; phone **NOT_RUN**. |
| `tools/gtk-headless.sh` | Starts a headless Weston, 1392x900, in a private runtime folder, runs one command under it, then stops Weston and removes the folder. |
| `tests/gtk_real_worker.py` | The script that runs inside that session. It enables every registered everyday app through `OPOS_APPS`, including Notices and AI mode, and builds the Shell's supported app pages. AI mode has a browser page; it adds no native screen. The worker fills the Converter with the server's own list of measures (`opos.convert.snapshot()`), so its buttons have their real words. Then, for each native page, each hand, and each layout (two panes, back mode on the left screen, back mode on the right screen), it checks that every row's minimum height is at most 900, that the reading pane and the thumb pane are inside the window, and that a tap on Home's Open button lands on it. |
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

## GPU Link stand-in PC

| File | What it is |
| --- | --- |
| `tools/gpu-link-preflight.py` | Read-only PC readiness check: `python3 -B tools/gpu-link-preflight.py --server http://127.0.0.1:8080/v1/chat/completions --model NAME --key-file ./pc.key --engine-log ./llama-server.log`. Checks adb selection and existing forward, one unsigned hello under the stand-in rules (model readiness not reported), key metadata, NVIDIA inventory, first usable content through stream completion, assistant-prefix continuation, GPU observations and speed, and stand-in help. GPU PASS says "engine log and utilization agree: evidence, not proof". It requires a regular, non-symlink engine log modified within `--engine-log-max-age HOURS` (default 12), a listening line matching the server port, CUDA device and positive offload lines before listening, no later failure, a baseline before the request, at least three samples wholly inside generation, and a utilization rise of at least 15 points. Missing, stale, untied or oversized logs and a short sample window warn; logged failures and explicit stream errors fail, even after content. Streams need a finish reason or `[DONE]`; an unexpected finish reason or an extra choice prevents a PASS (a failure found first is reported instead). Invisible text alone is unusable. Continuation checks the entire consecutive comma sequence from 5 (the requested stop at 12 is not enforced); only when the server reports a stop at the token limit may the list end after a comma or inside the next number (the part after the last comma must be empty or the start of the number that follows); a PASS means the reply continued the stored partial on this fixed probe, and a prompt cache may behave differently on a long real answer. PASS is evidence, not clearance for a whole demo. Measured by this check, not a benchmark; real CUDA logs and physical-phone checks remain NOT_RUN. `--quick` skips chat and GPU checks; `--no-gpu-probe` skips only the load probe; `--transport ssh` skips adb. Engine-log aliases of `--key-file` or `--json` are refused (exit 2), including hard links; logs are read only through the first 2 MiB. Malformed adb lines warn and cannot select a device; printed and JSON details strip controls. `--json PATH` exclusively creates a new results file and refuses existing paths (exit 2). `--adb` intentionally selects trusted executable code: one path or command name; whitespace is accepted only for an existing file path. Creates no forward, enrolment or engine. |
| `tools/gpu-link-forward-keeper.py` | Keeps one loopback GPU Link forward for the target and options supplied: `python3 -B tools/gpu-link-forward-keeper.py --mode ssh --ssh-target USER@HOST`, or `--mode adb [--serial SERIAL]`. Restarts its SSH child with doubling backoff capped at max(interval, 1.0 s). Inherited ssh configuration (for example ProxyCommand or extra forwards) is not isolated unless `--ssh-config FILE` points to a minimal existing regular file; with it, give the identity using `--ssh-identity FILE`. Both files are checked by metadata only, never read by the keeper. Polls adb with a pinned selected serial on every later command; creates only a missing forward with `adb -s SELECTED forward --no-rebind tcp:LOCAL tcp:REMOTE`, never overwriting a competing mapping. Leaves adb forwards in place. On handled shutdown targets its child and, on POSIX, the child process group. Descendants that change process group or session, for example with `setpgid()` or `setsid()`, are not tracked. The keeper requires exclusive ownership of reaping its child: no `SIGCHLD` handler that reaps and no other `wait`. When non-reaping `waitid` observation is available, the precheck before the first group signal can detect an already externally reaped child. It does not protect fallback operation or prevent concurrent reaping. With usable `waitid`/`WNOWAIT` observation, the final group kill precedes reaping the leader; without them (including some macOS Python builds), polling may reap it first and no group signal is sent afterward, so same-group descendants may survive. Runtime `ENOSYS`/`EINVAL` disables `waitid` process-wide, including later `Keeper` objects; other observation errors retry once after up to 10 ms, clamped by any supplied cleanup deadline, then fall back for that call. EPERM from a group signal is accepted only after confirming leader exit. When non-reaping observation is unavailable or fails transiently, a bounded child wait may confirm exit by reaping; no later group signal is sent after reaping. If leader exit cannot be confirmed, `group_signal()` raises the original signalling error. Normal cleanup propagates that error; the final best-effort escalation suppresses `OSError` and records incomplete cleanup. A leader confirmed exited by the final escalation's fallback wait is treated as cleaned up. Leader exit does not prove that all descendants are gone. Shutdown shares one 1.0 s waiting budget from the first observed stop across termination grace, EPERM observation, reader joins, final child waits and reporter drain. This does not prove completed termination or bound arbitrary scheduling delays. A cleanup wait timeout causes one final escalation attempt. Signalling or observation errors may instead propagate after incomplete cleanup is recorded. Unconfirmed cleanup is terminal in both modes: it retains the child handle, starts no further command and reports once `cleanup incomplete: the SSH child may still be running` (SSH) or `cleanup incomplete: the adb child may still be running` (adb). The non-exceptional terminal-cleanup path returns 0 in both modes; signalling errors may propagate. A further signal that arrives after cleanup has restored the original handlers ends the keeper by that signal's default action. A SIGKILLed keeper cannot clean up: run `--status` before starting and stop a stale ssh by hand. On Windows the direct child is terminated; descendants are not tracked (NOT_RUN). Reports `ssh running (child alive for 1.0s; forward readiness not verified)` and `ssh exited (was running …s)`; startup observation measures child liveness, not readiness. Reports `adb forward listed (not verified end to end)` or `adb forward created (not verified end to end)` with adb operation duration; an adb failure after observation means `adb forward state unknown (adb unavailable)`. Only `--status` probes loopback TCP (0.5 s): `forward answers on 127.0.0.1:LOCAL (listener identity unverified)` or `nothing answers on 127.0.0.1:LOCAL`. `--once` ensures adb once and is refused for SSH. SSH stdout/stderr and adb stderr are discarded. ADB stdout is parsed as records and never echoed, keeping addresses and identity paths out of reports. Create must exit successfully and produce no nonblank stdout records accepted by the parser; blank lines and ordinary spaces are permitted. The adb create exit status is what “created” rests on; the mapping is not re-listed after the no-overwrite create. The keeper’s own output uses a daemon writer owning a non-inheritable duplicate of its descriptor; only that writer closes it. If duplication or inheritance protection fails, reporting is silently disabled. At most 64 queued report entries plus one entry being written are retained (help is one multiline entry). Overflow drops the oldest entries. A later successful write reports accumulated overflow losses once as `(N lines not shown)`; shutdown and sink failure may prevent that notice or final status delivery. EAGAIN retries while readiness waiting succeeds. A readiness-wait error also disables reporting, as do other write errors. Shutdown waits at most 0.3 s for reporter output, capped by the shared deadline (`--status` and `--help`: at most 1 s); a stuck writer retains its duplicate until process exit. POSIX adb readers use cancellable nonblocking readiness waits at any descriptor number so escaped stdout writers do not accumulate readers or descriptors. Readiness uses `poll` where available; without it, descriptors below 1024 use `select` and higher descriptors sleep then retry nonblocking I/O. A reporter `POLLNVAL` on a still-valid sink also sleeps then retries the write. Windows keeps blocking pipe reads; at most four outstanding abandoned readers are allowed on either platform. At the cap no new adb command starts: it reports `adb output reader limit reached; waiting` once per ten seconds and keeps polling until a reader finishes. Trusted executable overrides are `--ssh` and `--adb`; starts no engine. Real SSH, real adb, physical cable recovery and Windows remain NOT_RUN. |

`gpu-link-standin-pc.py` plays the PC side through an existing loopback forward.
It creates no forward and starts no engine. Enrol using the owner's developer command:

```sh
python3 -B tools/gpu-link-standin-pc.py --key-file ./pc.key --enrol-request | <phone enrolment command>
```

Replace the placeholder with `adb shell` running the phone's `nc` to the link service's
loopback port, or an SSH command with strict host-key checking running the same.
The request travels on that command's standard input. This mode opens no connection
and accepts stdout only as a pipe or socket, refusing terminals and regular files.
Where the platform cannot identify the descriptor type, it still refuses terminals.
Compare the code printed on stderr with the phone and
approve on the phone, then run:

```sh
python3 -B tools/gpu-link-standin-pc.py --key-file ./pc.key
```

The default `--phone` is `127.0.0.1:8767`. Where the Inference monitor runs on that computer,
forward the phone's port to a different local port and pass it with `--phone`. Practice mode returns simulated text and
publishes no token count or speed. `--text server --server URL --model NAME` uses
an existing loopback chat-completions server; it never starts one. Server token counts
fall back to an estimate of words generated under the current lease, excluding the
stored partial. After a resume, the token count shown on the phone covers text produced
since the resume. Keep the chat-completions server warm before starting: the tool waits
30 seconds for its first bytes, and a longer silence fails that answer. The same socket
timeout applies while reading the stream. `--rate` limits text posts to at most four per second. Questions and
answers appear in event lines only with `--show-text`.

`--give-up-after SECONDS` bounds consecutive failures (default 300; 0 retries without
an expiry). Successful signed exchanges reset the connection failure window. A pending
text post has its own failure window, cleared only when that post is accepted; successful
status replies do not reset it or its message throttle. Connection failures,
signed server errors, unavailable hello replies and repeated epoch changes wait at least one second before
retrying. Failure messages appear at most once per 30 seconds during a continuous
failure window; expiry exits with status 3. An unaccepted key stops with a diagnostic.
A routine epoch change repeats the request with a fresh nonce and keeps the live source,
lease and pending text. A signed lease refusal ends that generation and resumes from stored text.

Keys are raw 32-byte files created with mode 0600 on POSIX. Existing POSIX keys must
be regular files owned by the current user with mode 0600; symlinks are refused.
On Windows the tool checks file type and size; it does not check inherited access rules.
Physical phone execution and real Windows execution remain **NOT_RUN** here.
