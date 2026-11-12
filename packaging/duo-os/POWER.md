<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Screen and power under OPOS: design note

Status, November 12, 2026: **short-press power-key blank/light is implemented; not run on a phone (NOT_RUN).**
The PC session reported that OPOS image 2 delivered KEY_POWER (116) press/release from `qpnp_pon`, with logind
ignoring the key and no phosh listener. Both panel backlights have max 255; `bl_power` is root-only and `brightness`
is writable by group `video`. Those observations do not verify the new service.

`opos-powerkey.service` runs `opos.powerkey` as root while OPOS is the shell. A clean release within 1.5 seconds
chooses blank or light through `bl_power` (4 blank, 0 light); accepted short presses less than 300 milliseconds
apart are ignored. Repeats are ignored. A duplicate press or a press still pending after the long-press bound
chooses light and cancels that held sequence until release. A real long hold never toggles; its expiry can cause
one recovery light attempt, rather than lighting again on every tick. On every reopen the daemon finds the input device by the exact sysfs name
`qpnp_pon`, never an event number. That device also reports KEY_VOLUMEDOWN (114); only KEY_POWER (116) is handled,
and every other key code is ignored. Evdev timestamps use `CLOCK_MONOTONIC` so wall-clock corrections cannot stretch
debounce or shorten a long hold. Fold/lid, idle, suspend and logind policy are unchanged. A separate
[volume-key reader](VOLUME.md) now reports simulated volume; it does not change this power reader or sound.

Blank is a lease that the daemon must keep justifying. Its main loop uses `select()` and nonblocking reads with a
retained partial-event buffer. It rereads both panels on every loop tick; a remembered target never establishes
their state. The select timeout is one second while the panels are blanked, recovery is pending, or a key press
still needs expiry checking. Only confirmed lit panels with nothing pending allow an indefinite wait for input.

A blank needs a clean short power press, fresh readings of both panels as lit, an active-shell probe, and no pending
recovery. The production probe remains `systemctl is-active --quiet opos.service`, with a 0.5-second timeout;
nonzero status, launch errors and timeouts mean not active. After its single blank pass, the daemon reads both
panels back. Anything other than both blanked chooses light. It never reapplies blank to repair disagreement.
While both panels are blanked, every tick repeats the shell probe; an inactive probe, disagreement, missing node,
invalid value or read error chooses light. If the lid daemon lights both panels, the next read simply finds both
lit. No coordination with the lid daemon is required. An unfold between the two blank writes produces mixed
readback and lighting; an unfold after the writes is observed on the next tick. A later clean short press can
start a new blank lease if its conditions hold.

Lighting is always allowed and never consults the shell probe or writes `brightness`. It happens on a short press
unless blanking is permitted, daemon start and stop, input-device loss, clock-ioctl failure, `SYN_DROPPED`, duplicate
press, expired pending press, or an unsafe tick. `SYN_DROPPED` also clears the pending press and discards events
through the next `SYN_REPORT`. Input lookup, open, read, EOF and clock-ioctl failures light before a one-second
select wait and retry; the exact-name lookup runs again on every reopen.

Every light attempt writes both panels and reads both back. A missing node, write error, failed read or nonzero
value keeps recovery pending, with one-second retries independent of input events. Both expected panels must be
present, readable and lit before recovery clears, so a blank node appearing after all prestart helpers have
finished is still lit. Recovery-pending and recovered transitions are logged once each, rather than every tick.
This also applies at daemon startup: absent or unwritable panels keep recovery pending inside the running loop;
temporary inability to unblank is not a fatal startup error.

A blank can outlive an observable shell crash/restart or an unfold interleave by **about one one-second tick**
before lighting is attempted. This is not atomicity: allow the shell probe's configured timeout of up to 0.5 seconds
after process creation, process scheduling and sysfs I/O as well. There is no hard wall-clock bound if writes remain
impossible, a node remains absent, or the daemon or kernel stops making progress. Recovery continues while the
daemon runs. Unit-active is weaker than compositor-ready: a replacement compositor can still be unready while
the unit is active, and a crash/restart that is entirely between probes may be missed. The lease does not prove
visual shell readiness or successful physical wake. Those behaviors still need phone checks.

Each pass of the shared `opos-unblank` helper gives each panel up to three bounded attempts, with 0.1 seconds between attempts.
Missing nodes are logged, are checked again on later attempts and are not an error. A present panel that still rejects
its write after the retries makes the helper exit nonzero; the other panel is still attempted. It also reads panel state after
writes and reapplies 0 on disagreement. The units prefix its prestart/stop commands with `-`, so failures remain visible
in the journal and cannot stop OPOS, the key daemon or the fallback from starting. The shell uses `-+` to run its
prestart as root. Unblank runs before OPOS, the key daemon and fallback start, and after the key daemon stops.
`opos-fallback.service` is ordered after the key daemon's stop, including its final unblank. The daemon is enabled with
OPOS, bound to its lifetime and uses `Restart=always`; it is not enabled for a phosh-only boot.
`StartLimitIntervalSec=0` in `[Unit]` disables start-count exhaustion, so repeated exits cannot leave recovery
permanently stopped by the manager's start limit. `Restart=always` and `RestartSec=3` in `[Service]` keep the
restart count unbounded but space attempts by at least three seconds, plus helper and process time. At most one
restart attempt every three seconds avoids a tight crash loop that could hurt the phone while preserving recovery
when panel writes become possible. This is the configured policy; native systemd and phone execution remain **NOT_RUN**.

The root key unit adds `NoNewPrivileges=yes`, `ProtectHome=yes`, `PrivateTmp=yes` and `ProtectSystem=full`. The last
makes `/usr`, `/boot` and `/etc` read-only, retaining access to `/dev/input`, `systemctl is-active` and the real
backlight nodes under `/sys/devices` behind `/sys/class/backlight` symlinks. These hardening directives and the actual
service lifecycle have **not run on a phone (NOT_RUN)**. **It is not known whether touch still reports with the panels blanked.** Backlight
writes do not lock the page; invisible taps may still act. Panel wake behavior, brightness on wake and power savings
are unverified.

Review round 2 finding 5 (verification depth, drop-ins and `GIT_REF` versus the working tree) is explicitly out of
scope for this design change. The image verifier requires the installed `/opt/opos/src/opos/powerkey.py`;
the build installs the power-key unit without a separate source-module guard. Runtime still comes from `GIT_REF`,
while packaging comes from the working tree. The verifier checks effective prestart and stop commands in the shipped
unit files, including later resets; it does not analyze drop-ins or prove that an existing module has the intended
behavior. Expected-revision/hash binding and behavioral verification of installed image artifacts remain unresolved.
No image build, image verification, native systemd lifecycle or phone execution was run for this round (**NOT_RUN**).

The remaining review below is historical (November 7). It was read from the port's files and sources (the unit and config snapshots of the OPOS image, the
port's `build.sh` and READMEs, the phoc and phosh trees). **None of it was observed on a phone running OPOS**, and the
current figures are the port's own, measured under phosh.

## What the port does for phosh

| Job | Who | Where |
|---|---|---|
| Power key | logind ignores it (`HandlePowerKey=ignore`); the phosh session acts on it (the port README: it locks) | `logind.conf.d/10-droidian-power-key.conf`, port README |
| Fold | `sfduo-lid` (a system service, root) bridges the hinge sensor to the kernel's lid switch; logind answers `lock`. On fold the daemon sets `PowerSaveMode` 3 on `org.gnome.Mutter.DisplayConfig` through the session user's bus; on unfold it injects KEY_WAKEUP, writes `bl_power` 0 to both panels, writes back the brightness saved in `/run/user/32011/sfduo-brightness`, sets `PowerSaveMode` 0, and starts avahi again | `sfduo-lid-daemon`, `logind.conf.d/50-sfduo-lid.conf` |
| Blanking | phosh serves `org.gnome.Mutter.DisplayConfig` (`monitor-manager.c`) and turns `PowerSaveMode` into the wlr output-power protocol; phoc's handler commits `enabled=false` on the output, which the hwcomposer backend hands to the vendor composer. phoc itself serves no `DisplayConfig` | phosh, phoc `output.c` |
| Brightness | gnome-settings-daemon writes `panel0-backlight` and `panel1-backlight`; `sfduo-brightness` (a GNOME autostart) writes the level back when a panel powers up at full; systemd-backlight restores the saved level at boot (the port seeds 90 of 255 on a fresh install) | `sfduo-brightness`, 98-sfduo-backlight.rules |
| Idle | `idle-delay` is 0, so an open Duo never blanks itself and only dims; Droidian's `mobile-power-saver` puts the CPUs on `powersave` while the screen is off and `sfduo-cpufreq` undoes that while it is on | port `adaptation/system/README.md` |
| IdleHint | the shell sets logind's IdleHint when the screen goes dark; Droidian's ofono reads it to quiet the modem | comment in `item.service` |
| Suspend | off in the session, and the fold only locks; after a resume `sfduo-unblank` nudges the screen on, but only when `pgrep -x phosh` finds phosh | `99-sfduo.conf`, port `build.sh` |

## What OPOS got of it at the November 7 review

The review was right that nothing in OPOS owns any of this. In detail:

- The panels are lit from the moment phoc is up, at the saved level, and never blank. OPOS has a dark theme and the panels
  are OLED; no figure for the current draw exists yet.
- The power key does nothing: logind ignores it and nothing in OPOS reacts to it.
- A fold stops avahi and nothing else. `lock` has no listener, and the lid daemon's blank call goes to a bus name only phosh
  serves; it swallows the error. An unfold writes `bl_power` 0 (harmless) and cannot restore a level.
- No brightness keeper runs, so a panel that comes back at full stays there. OPOS computes a brightness wish
  (`budget.py`) and has no sysfs writer for it.
- Nobody sets IdleHint. `opos.service` already sets `XDG_SESSION_TYPE=wayland`, which `item.service` says is what lets a
  session set it.
- `mobile-power-saver` does not run (no gnome-session), so nothing puts the CPUs on `powersave`; `sfduo-cpufreq` waits up
  to 120 s for phosh, then runs anyway.
- `opos-session` runs phoc under `dbus-run-session` (a private bus) when `DBUS_SESSION_BUS_ADDRESS` is unset; the lid
  daemon calls `/run/user/<uid>/bus`. Which bus phoc and Chromium are on has not been checked.

## The safest minimal approach

Each step stands on its own; stop at any of them.

1. **Change nothing; measure.** On a dev image (ssh), read only: the four attributes of both `panel*-backlight`
   (`brightness`, `max_brightness`, `bl_power`, `actual_brightness`), `current_now` from the fuel gauges with the panels
   lit, the CPU governors, `loginctl show-session <id> -p IdleHint`, and `busctl --user list | grep DisplayConfig` on the
   droidian user's bus (expected: nothing). That gives the cost of "always lit" under OPOS before anything is designed
   against the port's phosh figures (126-184 mA lit, 33-55 mA blanked, folded).
2. **Brightness only.** OPOS writes the two `brightness` files. The port's udev rule opens exactly those to group `video`,
   and the session user is in it. No composer, no compositor; the worst outcome is a wrong level, so keep a floor (one clean
   install came up at 2 of 255) and restore the user's level at start.
3. **Blank on fold through `bl_power`, not through the composer.** The lid daemon already finds the fold and the unfold,
   re-blanks a lit closed phone after about 4 s, and judges "lit" by `bl_power`. Only `display_power(3)` has no effect.
   The smallest change is a build-time edit of that one function, like the watchdog edit, to write 4 (mode 3) or 0
   (mode 0) to both `bl_power`; the daemon is root, `bl_power` is root-only (the udev rule changes `brightness` alone).
   The port's `sfduo-screens` calls this "the same DSI panel-power path the system uses". It also says rendering continues
   underneath, so the 33-55 mA figure may not carry over; step 1 gives the baseline to compare.
4. **Power key: short-press toggle implemented above; idle still left alone.** Keep `HandlePowerKey=ignore`. If a clean power-off is wanted, only
   `HandlePowerKeyLongPress=poweroff`, after a test that it does not meet the PMIC hard-reset hold. A short press as a
   screen toggle needs a process that reads KEY_POWER and a root helper for `bl_power`.
5. **IdleHint and suspend: later.** Set IdleHint only once the screen can be dark, from a process in the session
   (`opos-server` is a system unit, outside it). Do not enable suspend: `sfduo-unblank` does nothing without phosh, so a
   phone that suspends under OPOS may not light again.

## What could go wrong

- **The composer.** The port's own comments record resets from a new compositor on an old composer (2026-10-03), from the
  composer started twice within a second, from a shell started 0.7 s after it, and from systemd stopping it with the shell
  (`BindsTo`, one restart in two). Those are composer start/stop sequences. I found no recorded reset from `bl_power` or
  `PowerSaveMode`, but the one recorded output-off failure is `wlr-randr --off` crashing the compositor of the day on the
  sole `HWCOMPOSER-1` output. So no step above goes through the composer or the output-power protocol; that path (what
  phosh does) waits for step 1 and a recovery plan.
- **A blank that outlives the shell.** `bl_power` 4 stays when OPOS restarts (`Restart=always`) or falls back to phosh. The
  new shell would come up dark and look dead. Whatever blanks must be undone first thing at `opos.service` start and in
  `opos-fallback.service`.
- **Dark and unreachable.** If the new power-key path fails, a blanked phone wakes only by unfolding (the lid daemon) or, perhaps,
  touch. It is not known whether the touch panel still reports with the DSI panel off; if it does, taps reach an invisible
  page. The owner's phone has no USB data, so a stuck-dark phone is recovered by the PMIC hold or by draining the battery.
- **Full brightness on wake.** The port documents panels powering up at full; on OLED that costs battery and the eyes.
- **Calls and alarms.** A blanked screen hides the call and alarm UI; step 3's re-blank must apply to a closed phone only.
- **`HandlePowerKey=poweroff`** (the review's interim fix): any short press, the same key that wakes the screen, powers
  off without asking, and before a desktop takes the power-key inhibitor a stray press does too. Not adopted.
- **Package-owned scripts.** Every `sed` on `sfduo-*` is reverted by an upgrade of the port's package. Reverting step 3
  leaves the phone lit, which is the safe direction.
