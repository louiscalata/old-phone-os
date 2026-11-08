<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Screen and power under OPOS: design note

Status, November 7, 2026: **nothing in this note is implemented.** The image changes made alongside it touch the kiosk
script, the image build and the port's composer watchdog, not the display, the backlight, the lid or the power key.
Everything below was read from the port's files and sources (the unit and config snapshots of the OPOS image, the
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

## What OPOS gets of it today

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
4. **Power key and idle: leave them.** Keep `HandlePowerKey=ignore`. If a clean power-off is wanted, only
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
- **Dark and unreachable.** With the power key dead, a blanked phone wakes only by unfolding (the lid daemon) or, perhaps,
  touch. It is not known whether the touch panel still reports with the DSI panel off; if it does, taps reach an invisible
  page. The owner's phone has no USB data, so a stuck-dark phone is recovered by the PMIC hold or by draining the battery.
- **Full brightness on wake.** The port documents panels powering up at full; on OLED that costs battery and the eyes.
- **Calls and alarms.** A blanked screen hides the call and alarm UI; step 3's re-blank must apply to a closed phone only.
- **`HandlePowerKey=poweroff`** (the review's interim fix): any short press, the same key that wakes the screen, powers
  off without asking, and before a desktop takes the power-key inhibitor a stray press does too. Not adopted.
- **Package-owned scripts.** Every `sed` on `sfduo-*` is reverted by an upgrade of the port's package. Reverting step 3
  leaves the phone lit, which is the safe direction.
