#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Offline checks of an OPOS OS image on an x86 builder: unit wiring, phoc's -E, and the OPOS server
# started from /opt/opos by the image's own aarch64 python3 under qemu-user. Not a boot test.
#   sudo sh packaging/duo-os/verify-image.sh OUT.img
set -u
[ "$(id -u)" -eq 0 ] || { echo "verify-image.sh: run as root" >&2; exit 2; }
[ $# -ge 1 ] || { echo "usage: sudo sh verify-image.sh OUT.img" >&2; exit 2; }
img=$1
case $img in /*) ;; *) img=$PWD/$img ;; esac
q=$(command -v qemu-aarch64-static) || { echo "needs qemu-aarch64-static" >&2; exit 2; }
# This script is root. Everything it runs (the image's python3 under qemu-user, this builder's python3) starts in, and
# keeps its HOME and every scratch file in, ONE private directory that only root can enter: mktemp -d makes it
# new, owned by root and mode 700. Never /tmp (a local user plants /tmp/opos/__init__.py, or a symlink where a
# fixed /tmp file name is written as root) and never the caller's directory (it may be writable by others).
umask 077
work=$(mktemp -d "${TMPDIR:-/tmp}/opos-verify.XXXXXX") || { echo "verify-image.sh: cannot make a private work directory" >&2; exit 2; }
[ -d "$work" ] && [ ! -L "$work" ] && [ "$(stat -c '%u %a' "$work")" = "0 700" ] || { echo "verify-image.sh: $work is not a private root-owned directory" >&2; exit 2; }
# What this script started is stopped, and what it made is removed, whichever way it ends: the end, an error, or INT, TERM or
# HUP (those run the same cleanup through exit). Servers are stopped by the pid this script started, never by a name or
# a command line, so a server somebody else runs is never touched. A second signal does not interrupt the cleanup.
pids='' mnt=''
# up PID: succeeds while PID runs (a child that has exited and not been waited for is a zombie: not running)
up() { up_s=$(ps -o stat= -p "$1" 2>/dev/null) && case $up_s in Z* | '') false ;; esac; }
cleanup() {
    trap '' INT TERM HUP
    cd /
    for p in $pids; do kill "$p" 2>/dev/null; done
    n=0
    while [ "$n" -lt 50 ]; do
        alive=0
        for p in $pids; do up "$p" && alive=1; done
        [ "$alive" -eq 0 ] && break
        sleep 0.1
        n=$((n + 1))
    done
    for p in $pids; do kill -9 "$p" 2>/dev/null; done
    if [ -n "$mnt" ]; then
        mountpoint -q "$mnt" 2>/dev/null && umount "$mnt" 2>/dev/null
        rmdir "$mnt" 2>/dev/null
    fi
    rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
cd "$work" || exit 2
# The image's python3 must not import from the working directory, the user site or the caller's environment:
# PYTHONSAFEPATH (python 3.11 and later) keeps the directory of the script or -c/-m out of sys.path, PYTHONNOUSERSITE
# keeps ~/.local out, and the working directory is the private one. This builder's own python3 runs with -I.
export PYTHONSAFEPATH=1 PYTHONNOUSERSITE=1 HOME="$work"
unset PYTHONSTARTUP PYTHONINSPECT PYTHONHOME
# The mount point is its own mktemp -d, not inside $work: the cleanup above removes $work recursively and must never
# be able to walk into a mounted image.
mnt=$(mktemp -d) || exit 2
fail=0
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }
mount -o ro,loop,noload "$img" "$mnt" || exit 1
r=$mnt
w=$r/etc/systemd/system
check "opos.service in graphical.target" "[ -L $w/graphical.target.wants/opos.service ]"
check "opos-server.service in multi-user.target" "[ -L $w/multi-user.target.wants/opos-server.service ]"
check "phosh not started at boot" "[ ! -e $w/graphical.target.wants/phosh.service ]"
check "phosh kept as fallback" "[ -f $r/lib/systemd/system/phosh.service ]"
check "the shell session runs phoc with the kiosk as its only client" "grep -q 'ExecStart=.*/usr/libexec/opos/opos-session' $w/opos.service && [ -x $r/usr/libexec/opos/opos-session ] && [ -x $r/usr/libexec/opos/opos-kiosk ] && grep -q -- '-E /usr/libexec/opos/opos-kiosk' $r/usr/libexec/opos/opos-session"
check "phoc is NOT started in shell mode (-S keeps a black shield over the panels)" "! grep -v '^[[:space:]]*#' $r/usr/libexec/opos/opos-session | grep -qE '(^| )-S( |\$)'"
check "restarts go straight to activating, as item.service" "grep -q '^RestartMode=direct' $w/opos.service"
check "phoc drives the panels through hwcomposer, as phosh's drop-in does" "grep -q '^Environment=WLR_BACKENDS=hwcomposer,libinput' $w/opos.service && grep -q '^Environment=EGL_PLATFORM=hwcomposer' $w/opos.service && grep -q 'WLR_BACKENDS=.hwcomposer' $r/lib/systemd/system/phosh.service.d/20-wlroots-hwc-env.conf"
check "the shell is bound to the vendor composer and switches VT before starting" "grep -q '^BindsTo=android-service@hwcomposer.service' $w/opos.service && grep -q '^ExecStartPre=+chvt 7' $w/opos.service"
check "the vendor composer is still started at boot" "[ -L $w/graphical.target.wants/android-service@hwcomposer.service ]"
check "journal is persistent, so a failed boot leaves evidence" "grep -q '^Storage=persistent' $r/etc/systemd/journald.conf.d/50-opos-persistent.conf && [ -d $r/var/log/journal ]"
check "boot diagnostics dump is enabled" "[ -L $w/multi-user.target.wants/opos-diag.service ] && [ -x $r/usr/libexec/opos/opos-diag ]"
k=$r/usr/libexec/opos/opos-kiosk
check "the kiosk and session scripts parse" "sh -n $k && sh -n $r/usr/libexec/opos/opos-session"
check "pgrep and pkill are in the image (opos-session ends phoc by name, and the kiosk looks for a Chromium still on its profile)" "[ -x $r/usr/bin/pkill ] && [ -x $r/usr/bin/pgrep ]"
check "the kiosk clears stale profile locks, drops WAYLAND_SOCKET, logs, backs off and can drop the GPU" "grep -q SingletonLock $k && grep -q 'unset WAYLAND_SOCKET' $k && grep -q 'logger -t opos-kiosk' $k && grep -q -- '--disable-gpu' $k && grep -q 'sleep \"\$delay\"' $k"
check "the kiosk waits for the server before every start of the browser, not only the first: wait_for_server is called inside the loop, before the start time is taken and before chromium runs" "awk '/^while :; do/ { l = 1; next } l && /^[[:space:]]+wait_for_server([[:space:]]|\$)/ && !t && !c { f = 1 } l && /^[[:space:]]+t0=/ { t = 1 } l && /^[[:space:]]+chromium[[:space:]]/ { c = 1 } END { exit !f }' $k"
check "a Chromium that cannot run ends the session and the unit fails at once (the kiosk gives up after 6 quick exits and leaves a marker; opos-session runs phoc as a child and exits 75; opos.service does not restart 75 and has OnFailure=; the fallback starts phosh)" "grep -q 'giveup=6' $k && grep -q 'opos-gave-up' $k && grep -q 'opos-gave-up' $r/usr/libexec/opos/opos-session && grep -q 'exit 75' $r/usr/libexec/opos/opos-session && grep -qF -- '-E /usr/libexec/opos/opos-kiosk <&3 3<&- &' $r/usr/libexec/opos/opos-session && ! grep -qE '^exec (systemd-cat|dbus-run-session|/usr/bin/phoc)' $r/usr/libexec/opos/opos-session && grep -qx 'RestartPreventExitStatus=75' $w/opos.service && ! grep -q '^SuccessExitStatus' $w/opos.service && grep -qx 'StartLimitIntervalSec=60' $w/opos.service && grep -qx 'StartLimitBurst=3' $w/opos.service && grep -qx 'Restart=always' $w/opos.service && grep -qx 'OnFailure=opos-fallback.service' $w/opos.service && grep -q 'systemctl start phosh.service' $w/opos-fallback.service"
check "the composer watchdog restarts OPOS as well as phosh (a package upgrade of the port reverts this)" "grep -qF 'systemctl try-restart phosh.service opos.service' $r/usr/local/sbin/sfduo-composer-watchdog && sh -n $r/usr/local/sbin/sfduo-composer-watchdog"
check "a Chromium DevTools port exists only together with a dev ssh key" "[ ! -e $r/etc/default/opos-kiosk ] || { [ -f $r/home/droidian/.ssh/authorized_keys ] && grep -qx 'OPOS_KIOSK_DEBUG_PORT=9222' $r/etc/default/opos-kiosk; }"
if [ -f "$r/home/droidian/.ssh/authorized_keys" ]; then
    echo "NOTE: DEV image: droidian has $(wc -l < "$r/home/droidian/.ssh/authorized_keys") authorized ssh key(s)"
else
    echo "NOTE: no ssh key in this image (nothing new can log in)"
fi
if [ -e "$r/etc/default/opos-kiosk" ]; then
    echo "NOTE: DEV image: the kiosk Chromium serves DevTools on 127.0.0.1 ($(grep -v '^#' "$r/etc/default/opos-kiosk" | tr '\n' ' '))"
else
    echo "NOTE: no DevTools port in this image"
fi
check "phoc has its -E/--exec startup command" "strings $r/usr/bin/phoc | grep -q 'Command (session) that will be ran at startup'"
# --- track: network (Wi-Fi and radio status) ---
check "the server may manage the phone's network (OPOS_NETWORK=1 drop-in)" "grep -q '^Environment=OPOS_NETWORK=1' $w/opos-server.service.d/20-opos-network.conf"
check "the Network page and its logic are in the image" "[ -f $r/opt/opos/src/opos/net.py ] && [ -f $r/opt/opos/src/opos/net_page.py ]"
if [ -f "$r/etc/polkit-1/rules.d/50-opos-network.rules" ]; then echo "NOTE: polkit rule installed: droidian may change Wi-Fi in NetworkManager without being asked"; else echo "NOTE: no polkit rule from OPOS (the base's own rules decide whether the Network page may join Wi-Fi)"; fi
# --- end track: network ---
check "OPOS revision recorded" "[ -s $r/opt/opos/REVISION ]"
echo "OPOS revision: $(cat $r/opt/opos/REVISION 2>/dev/null)"
# ---- BEGIN OPOS OTA checks (self-contained; needs $r $w and check(); docs/ota-update.md) -------------------------
ota_link_ok() {
    t=$(readlink "$r/opt/opos") || return 1
    n=${t#opos-releases/}
    case $t in opos-releases/[0-9]*-[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;; *) return 1 ;; esac
    [ -d "$r/opt/opos-releases/$n" ] && [ -s "$r/opt/opos-releases/$n/REVISION" ] \
        && [ "$(cut -c 1-12 "$r/opt/opos-releases/$n/REVISION")" = "${n#*-}" ]
}
ota_unit_enabled() { [ "$(readlink "$w/multi-user.target.wants/$1")" = "/etc/systemd/system/$1" ] && [ -f "$w/$1" ]; }
ota_updater_ok() {
    u=$r/usr/libexec/opos-updater/opos-updater
    [ -f "$u" ] && [ ! -L "$u" ] && [ -x "$u" ] && [ "$(stat -c %a "$u")" = 755 ] \
        && [ "$(head -n 1 "$u")" = '#!/usr/bin/python3 -IB' ] \
        && python3 -I -B -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$u"
}
ota_signers_ok() {
    s=$r/etc/opos/update/allowed_signers
    [ -f "$s" ] && [ ! -L "$s" ] && [ "$(stat -c %a "$s")" = 644 ] || return 1
    ! grep -vE '^opos-release ssh-ed25519 [A-Za-z0-9+/=]+$' "$s" | grep -q .
}
ota_units_ok() {
    grep -q '^ExecStart=/usr/libexec/opos-updater/opos-updater apply-inbox$' "$w/opos-update.service" \
        && grep -q '^ExecStart=/usr/libexec/opos-updater/opos-updater apply-pending-boot$' "$w/opos-update-boot.service" \
        && grep -q '^ExecStart=/usr/libexec/opos-updater/opos-updater confirm-boot$' "$w/opos-update-confirm.service" \
        && grep -q '^PathExistsGlob=/var/lib/opos-update/inbox/\*.opos-update$' "$w/opos-update.path" \
        && grep -q '^Before=opos-server.service opos.service$' "$w/opos-update-boot.service" \
        && [ ! -L "$w/multi-user.target.wants/opos-update.service" ]
}
ota_source_ok() {
    s=$r/etc/opos/update.json
    [ ! -e "$s" ] && [ ! -L "$s" ] && return 0
    [ -f "$s" ] && [ ! -L "$s" ] && [ "$(stat -c %a "$s")" = 644 ] \
        && python3 -I -B -c 'import json, sys; d = json.load(open(sys.argv[1])); assert set(d) == {"url", "auto_check", "allow_lan_http"} and d["url"].startswith(("https://", "http://")) and d["auto_check"] is False' "$s"
}
ota_gid=$(awk -F: '$1=="droidian"{print $4}' "$r/etc/passwd")
check "OTA: /opt/opos is a symlink to <serial>-<rev12> under /opt/opos-releases and REVISION matches" "ota_link_ok"
check "OTA: opos-update.path, opos-update-boot.service and opos-update-confirm.service are enabled" "ota_unit_enabled opos-update.path && ota_unit_enabled opos-update-boot.service && ota_unit_enabled opos-update-confirm.service"
check "OTA: the unit files call the updater, watch the inbox and run before the session" "ota_units_ok"
check "OTA: the root updater is present, executable (0755) and parses" "ota_updater_ok"
check "OTA: the updater and its trust files are owned by root" "[ \"\$(stat -c %u:%g $r/usr/libexec/opos-updater/opos-updater)\" = 0:0 ] && [ \"\$(stat -c %u:%g $r/etc/opos/update/allowed_signers)\" = 0:0 ] && [ \"\$(stat -c %u $r/etc/opos/update)\" = 0 ]"
check "OTA: allowed_signers holds only 'opos-release ssh-ed25519 <public key>' lines (or nothing)" "ota_signers_ok"
check "OTA: base-version is 1" "[ \"\$(cat $r/etc/opos/base-version)\" = 1 ]"
check "OTA: the inbox is mode 2770, staging 0700 and the state directory 0755" "[ \"\$(stat -c %a $r/var/lib/opos-update/inbox)\" = 2770 ] && [ \"\$(stat -c %a $r/var/lib/opos-update/staging)\" = 700 ] && [ \"\$(stat -c %a $r/var/lib/opos-update)\" = 755 ]"
check "OTA: the inbox is root:droidian and staging and the state directory are root-owned" "[ \"\$(stat -c %u:%g $r/var/lib/opos-update/inbox)\" = 0:$ota_gid ] && [ \"\$(stat -c %u:%g $r/var/lib/opos-update/staging)\" = 0:0 ] && [ \"\$(stat -c %u:%g $r/var/lib/opos-update)\" = 0:0 ]"
check "OTA: etc/opos/update.json is absent (nothing is fetched) or names one address and leaves auto_check off" "ota_source_ok"
ota_keys=$(grep -c . "$r/etc/opos/update/allowed_signers" 2>/dev/null || true)
if [ "${ota_keys:-0}" -gt 0 ]; then echo "NOTE: update signers: $ota_keys public key(s)"; else echo "NOTE: update signers: NONE (this image accepts no updates)"; fi
# ---- END OPOS OTA checks ---------------------------------------------------------------------------------------
# --- clock: BEGIN
cfloor=$(cat "$r/opt/opos/CLOCK_FLOOR" 2>/dev/null); cok=1
case $cfloor in '' | *[!0-9]*) ;; *) [ "$cfloor" -ge 1767225600 ] && [ "$cfloor" -le $(( $(date +%s) + 86400 )) ] && cok=0 ;; esac
check "clock floor recorded: a Unix time from 2026 on and not after this builder's tomorrow" "[ $cok -eq 0 ]"
echo "clock floor: $cfloor"
check "opos-clock runs at boot after the port's clock services, and parses" "[ -L $w/multi-user.target.wants/opos-clock.service ] && [ -x $r/usr/libexec/opos/opos-clock ] && sh -n $r/usr/libexec/opos/opos-clock && grep -q '^After=timekeeper.service item-clock-seed.service' $w/opos-clock.service"
check "opos-clock has no Before= on sysinit, the time targets or the network (that made an ordering cycle on the port)" "! grep -E '^Before=.*(sysinit|basic|time-set|time-sync|timesyncd|network)' $w/opos-clock.service"
check "network time still takes over: systemd-timesyncd enabled, not masked" "[ -L $w/sysinit.target.wants/systemd-timesyncd.service ] && [ \"\$(readlink $w/systemd-timesyncd.service)\" != /dev/null ]"
chome=$(awk -F: '$1=="droidian"{print $6}' "$r/etc/passwd"); drop=$(sed -n 's/^PathChanged=//p' "$w/opos-clock-set.path" 2>/dev/null)
check "the time-file drop is filled in for the droidian home, with one file name in both units" "[ -n '$chome' ] && [ '$drop' = '$chome/opos-set-time' ] && grep -q '^ExecStart=/usr/libexec/opos/opos-clock set $drop\$' $w/opos-clock-set.service && ! grep -q @DROP@ $w/opos-clock-set.path $w/opos-clock-set.service"
if [ -f "$r$chome/.ssh/authorized_keys" ]; then
    check "DEV image: the time-file drop is enabled" "[ -L $w/multi-user.target.wants/opos-clock-set.path ]"
else
    check "no ssh key: the time-file drop is not enabled" "[ ! -L $w/multi-user.target.wants/opos-clock-set.path ]"
fi
# --- clock: END
# --- track osk: the on-screen keyboard (packaging/duo-os/OSK.md) ---
o=$r/opt/opos/src/opos
check "the in-page on-screen keyboard is in the image (osk.py, osk.js, osk.css)" "[ -s $o/osk.py ] && [ -s $o/osk_static/osk.js ] && [ -s $o/osk_static/osk.css ]"
check "opos-server turns the in-page keyboard on and reads /etc/default/opos-osk for an override" "grep -qx 'Environment=OPOS_OSK=1' $w/opos-server.service && grep -qx 'EnvironmentFile=-/etc/default/opos-osk' $w/opos-server.service && [ -f $r/etc/default/opos-osk ]"
check "opos-osk (the opt-in Wayland keyboard) is installed, parses, and its unit is enabled but idle" "sh -n $r/usr/libexec/opos/opos-osk && [ -x $r/usr/libexec/opos/opos-osk ] && [ -L $w/graphical.target.wants/opos-osk.service ] && grep -q '^Requisite=opos.service' $w/opos-osk.service && ! grep -v '^#' $r/etc/default/opos-osk | grep -q OPOS_OSK"
if [ -x "$r/usr/bin/phosh-osk-stevia" ]; then
    echo "NOTE: phosh-osk-stevia is in the image, so OPOS_OSK=stevia (opt-in, untried on the phone) has a keyboard to start"
else
    echo "NOTE: no phosh-osk-stevia in this image: the opt-in OPOS_OSK=stevia would start nothing"
fi
# --- track: monitor (the Inference page; packaging/duo-os/README.md, "Inference page"): static wiring ---
mo=$r/opt/opos/src/opos
check "the Inference page is on: opos-server.service sets OPOS_MONITOR=1 and reads the optional /etc/opos/monitor.env" "grep -qx 'Environment=OPOS_MONITOR=1' $w/opos-server.service && grep -qx 'EnvironmentFile=-/etc/opos/monitor.env' $w/opos-server.service"
check "the Inference page is in the image (monitor.py, monitor_web.py and its document)" "[ -s $mo/monitor.py ] && [ -s $mo/monitor_web.py ] && [ -s $mo/web_static/monitor.html ] && [ -s $mo/web_static/monitor.css ] && [ -s $mo/web_static/monitor.js ] && [ -s $mo/web_static/monitor-theme.js ]"
check "/etc/opos is owned by root:root, mode 0755, and holds only the commented example: no monitor.env, no setting that is on, nothing secret" "[ -d $r/etc/opos ] && [ \"\$(stat -c %a $r/etc/opos)\" = 755 ] && [ \"\$(stat -c %u:%g $r/etc/opos)\" = 0:0 ] && [ -f $r/etc/opos/monitor.env.example ] && [ ! -e $r/etc/opos/monitor.env ] && ! grep -v '^[[:space:]]*#' $r/etc/opos/monitor.env.example | grep -q '[^[:space:]]'"
check "the example names the reverse-tunnel setting (OPOS_AGIW_URL on the Duo's loopback, OPOS_AGIW_HOST for the monitor's Host)" "grep -qx '#OPOS_AGIW_URL=http://127.0.0.1:18767' $r/etc/opos/monitor.env.example && grep -qx '#OPOS_AGIW_HOST=127.0.0.1:8767' $r/etc/opos/monitor.env.example"
# --- end track: monitor ---
umount "$mnt"

# Server smoke test: qemu-user with the read-only image as its sysroot (-L), so the image's own
# aarch64 python3 loads its own standard library and runs /opt/opos from the image.
mount -o ro,loop,noload "$img" "$mnt" || exit 1
# --- servers: BEGIN (loopback ports, and the servers' lifetime)
# The servers below listen on loopback ports that this builder's python3 picks (bind to port 0 and read it back) instead of
# fixed ones: a stale server from an aborted run, or a local user's listener on a fixed port, would answer the readiness loop
# and the state probe with its own data, and the checks would pass without the image's server. A port that already answers
# is a FAIL and nothing is started on it. Each server is started so that its pid is the process itself (a subshell that execs
# it), and is stopped by that pid alone, here or by the cleanup trap above.
pick_port() { python3 -I -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
port_answers() { python3 -I -c 'import socket, sys; s = socket.socket(); s.settimeout(2); sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)' "$1"; }
# pick_port_apart [PORT ...]: a picked port that is none of the PORTs given (two picks in a row can return the same port while
# nothing has bound the first yet, and a server under qemu-user takes seconds to bind)
pick_port_apart() {
    pa_n=0
    while [ "$pa_n" -lt 20 ]; do
        pa_p=$(pick_port) || return 1
        case " $* " in *" $pa_p "*) ;; *) echo "$pa_p"; return 0 ;; esac
        pa_n=$((pa_n + 1))
    done
    return 1
}
# serve PORT LOGFILE [NAME=value ...]: the image's OPOS server on 127.0.0.1:PORT in the background, its pid in $sv_pid and in $pids.
serve() {
    sv_port=$1 sv_log=$2
    shift 2
    if port_answers "$sv_port"; then
        echo "FAIL something already answers on 127.0.0.1:$sv_port before the image's server was started, so its answers would not be the image's"
        fail=1
        return 1
    fi
    (
        # What the caller's environment holds for OPOS (a switch left on in a shell) never reaches a server: only the NAME=value
        # arguments of this call do, so the servers that must have a switch off have it off.
        for sv_v in $(env | sed -n 's/^\(OPOS_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$sv_v"; done
        for sv_kv in "$@"; do export "$sv_kv"; done
        export PYTHONPATH="$mnt/opt/opos/src" PYTHONDONTWRITEBYTECODE=1
        exec "$q" -L "$mnt" "$mnt/usr/bin/python3" -m opos.web --port "$sv_port" > "$sv_log" 2>&1
    ) &
    sv_pid=$!
    pids="$pids $sv_pid"
}
# stop_server PID: TERM, up to 5 s, KILL; PID leaves $pids so the cleanup never signals a number that may have been reused
stop_server() {
    kill "$1" 2>/dev/null
    n=0
    while up "$1" && [ "$n" -lt 50 ]; do
        sleep 0.1
        n=$((n + 1))
    done
    kill -9 "$1" 2>/dev/null
    keep=''
    for p in $pids; do [ "$p" = "$1" ] || keep="$keep $p"; done
    pids=$keep
}
# --- servers: END
port=$(pick_port) && [ -n "$port" ] || { echo "verify-image.sh: cannot pick a free loopback port" >&2; exit 2; }
py() { PYTHONPATH="$mnt/opt/opos/src" PYTHONDONTWRITEBYTECODE=1 "$q" -L "$mnt" "$mnt/usr/bin/python3" "$@"; }
py -c "import sys; print(sys.version.split()[0], sys.platform)" | sed 's/^/image python3 /'
# >>> track firstboot: first run and time zone (src/opos/firstrun.py, timezone.py). Static wiring, then the image's own python3 and zone files under qemu-user.
zi=$r/usr/share/zoneinfo
fb_list() { py -c 'import sys; from opos import timezone as t; z, s = t.zones(sys.argv[1]); sys.exit(0 if s == "system" and "Europe/Berlin" in z and len(z) > 300 else 1)' "$zi"; }
fb_tz() { [ "$( export TZ=Asia/Kolkata; py -c 'import time; print(time.strftime("%z", time.localtime(0)))' )" = +0530 ]; }
fb_saved() { d=$(mktemp -d "$work/cfg.XXXXXX"); ( export OPOS_CONFIG_DIR=$d; py -m opos.timezone --set Asia/Kolkata >/dev/null &&
    [ "$(py -c 'import time; from opos import timezone as t; t.apply_saved(); print(time.strftime("%z", time.localtime(0)))')" = +0530 ] ); rc=$?; rm -rf "$d"; return $rc; }
check "first run is on: opos-server.service sets OPOS_FIRSTRUN=1" "grep -q '^Environment=OPOS_FIRSTRUN=1' $w/opos-server.service"
check "the kiosk exports the saved time zone to Chromium" "grep -q 'track firstboot' $r/usr/libexec/opos/opos-kiosk && sh -n $r/usr/libexec/opos/opos-kiosk"
if [ -f "$zi/UTC" ] && [ -f "$zi/Europe/Berlin" ]; then
    check "zone database present: OPOS lists the system zones (more than 300)" "fb_list"
    check "TZ=Asia/Kolkata reads +0530 with the image's python3 and zone files" "fb_tz"
    check "a saved zone is applied by the image's python3 (opos.timezone, TZ + tzset)" "fb_saved"
else
    echo "NOTE: no /usr/share/zoneinfo in this image: OPOS offers only its short built-in zone list"
fi
# <<< track firstboot
ok=1 i=0 smoke_pid=''
if serve "$port" "$work/opos-verify.log"; then
    smoke_pid=$sv_pid
    for i in $(seq 1 60); do
        up "$smoke_pid" || break  # the server ended: whatever answers on the port now is not the image's
        if python3 -I -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:$port/', timeout=2).status == 200 else 1)" 2>/dev/null; then ok=0; break; fi
        sleep 1
    done
fi
check "OPOS server from the image answers HTTP 200 (qemu-user, ${i}s)" "[ $ok -eq 0 ]"
if [ $ok -eq 0 ]; then
# --- state probe: BEGIN (the authenticated /api/state; its exit status is a check, it is not only printed)
# An image whose "/" works but whose /api/state raises, answers 500, sends malformed JSON or lacks the fields the page reads
# must FAIL here. The probe prints "state: ..." only after every field it reads has been validated.
sp=0
python3 -I - "$port" > "$work/opos-verify-state.txt" 2>&1 <<'EOF' || sp=$?
import json, re, sys, urllib.request


def die(why):
    print("state probe FAILED: " + why)
    sys.exit(1)


base = "http://127.0.0.1:%s" % sys.argv[1]
try:
    html = urllib.request.urlopen(base + "/", timeout=5).read().decode("utf-8", "replace")
    m = re.search(r'name="opos-token" content="([^"]+)"', html)
    if not m:
        die("no opos-token on /")
    req = urllib.request.Request(base + "/api/state", headers={"X-OPOS-Token": m.group(1)})
    s = json.load(urllib.request.urlopen(req, timeout=5))
except Exception as e:  # an HTTP 500 or 403, a timeout, a refused connection, text that is not JSON
    die("%s: %s" % (type(e).__name__, e))
if not isinstance(s, dict):
    die("/api/state is not a JSON object")
b = s.get("battery")
pct = b.get("percent") if isinstance(b, dict) else None
if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct <= 100:
    die("battery.percent is %r, not a number from 0 to 100" % (pct,))
story = s.get("lock_story")
if not isinstance(story, str):
    die("lock_story is %r, not text" % (story,))
helper = s.get("helper", {})
if not isinstance(helper, dict):
    die("helper is %r, not an object" % (helper,))
print("state: battery %s%%, mode %s, lock '%s'" % (pct, helper.get("mode", "-"), story))
EOF
check "the authenticated /api/state answers with the fields the page reads (battery percent, lock story)" "[ $sp -eq 0 ] && grep -q '^state: ' \"\$work/opos-verify-state.txt\""
cat "$work/opos-verify-state.txt"
# --- state probe: END
else  # nothing of the image's answers (or the port was taken): probing it would read somebody else's data
    echo "FAIL the authenticated /api/state was not probed: the image's server did not start or did not answer"
    fail=1
fi
# --- track osk: the image's own python3 reads the keyboard's files and builds the page tags (no server needed) ---
py -c "
import os
os.environ['OPOS_OSK'] = '1'
from opos import osk
js, css = osk.asset('/osk.js'), osk.asset('/osk.css')
assert js and css and len(js[0]) > 5000 and len(css[0]) > 500, 'keyboard assets missing or tiny'
t = osk.tags('n' * 22)
assert '/osk.js' in t and '/osk.css' in t and 'nonce=' in t
print('on-screen keyboard assets: osk.js', len(js[0]), 'bytes, osk.css', len(css[0]), 'bytes')" > "$work/opos-verify-osk.txt" 2>&1
check "the image's python3 serves the on-screen keyboard's files" "grep -q 'on-screen keyboard assets' \"\$work/opos-verify-osk.txt\""
tail -n 1 "$work/opos-verify-osk.txt"
# >>> track panes: the black and white pane surfaces (src/opos/panes.py, docs/jaryo-panes.md)
po=$r/opt/opos/src/opos
check "the pane surfaces are in the image (panes.py, panes.css, panes-mirror.css, panes.js)" "[ -s $po/panes.py ] && [ -s $po/panes_static/panes.css ] && [ -s $po/panes_static/panes-mirror.css ] && [ -s $po/panes_static/panes.js ]"
check "opos-server turns the pane surfaces on (they paint only once OPOS_SPAN is on)" "grep -qx 'Environment=OPOS_PANES=1' $w/opos-server.service"
py -c "
import os
os.environ['OPOS_PANES'] = '1'
from opos import panes, span
css, js = panes.asset('/panes.css', span.Span(675, 42)), panes.asset('/panes.js')
assert css and js and len(css[0]) > 5000 and len(js[0]) > 1000, 'pane assets missing or tiny'
t = panes.tags('n' * 22)
assert '/panes.css' in t and '/panes.js' in t and 'nonce=' in t
assert b'min-width:1055px' in css[0] and b'viewport-segment' not in css[0], 'the span geometry was not applied'
print('pane assets: panes.css', len(css[0]), 'bytes, panes.js', len(js[0]), 'bytes')" > "$work/opos-verify-panes.txt" 2>&1
check "the image's python3 serves the pane surfaces' files and builds their geometry from the span" "grep -q 'pane assets' \"\$work/opos-verify-panes.txt\""
tail -n 1 "$work/opos-verify-panes.txt"
# <<< track panes
[ -z "$smoke_pid" ] || stop_server "$smoke_pid"
head -5 "$work/opos-verify.log"
# --- track integrate: BEGIN (the keyboard, the Network page and the first run in one server; docs/duo-os-final.md)
# A second server from the image, started with the three switches opos-server.service and its drop-in set, asked for every page
# the pieces add: they must be wired into the same server, not merely present on disk. Offline, like the smoke test above.
cfgd=$(mktemp -d "$work/cfg.XXXXXX")
port2=$(pick_port) && [ -n "$port2" ] || { echo "verify-image.sh: cannot pick a free loopback port" >&2; exit 2; }
if serve "$port2" "$work/opos-verify-duo.log" OPOS_OSK=1 OPOS_NETWORK=1 OPOS_FIRSTRUN=1 "OPOS_CONFIG_DIR=$cfgd"; then
    duo_pid=$sv_pid
    python3 -I - "$port2" > "$work/opos-verify-duo.txt" 2>&1 <<'EOF'
import sys, time, urllib.request
base = "http://127.0.0.1:%s" % sys.argv[1]
def get(path, accept=None):
    req = urllib.request.Request(base + path, headers={"Accept": accept} if accept else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.geturl(), r.read().decode("utf-8", "replace")
for _ in range(60):
    try:
        get("/")
        break
    except OSError:
        time.sleep(1)
home = get("/")[2]
need = [("the home page loads the keyboard", 'src="/osk.js"' in home), ("Settings links to /network", 'href="/network"' in home),
        ("Settings links to /timezone", 'href="/timezone"' in home)]
for path in ("/network", "/firstrun", "/timezone"):
    st, _, body = get(path)
    need.append((path + " loads the keyboard", st == 200 and 'src="/osk.js"' in body))
for path in ("/osk.js", "/osk.css"):
    need.append((path + " is served", get(path)[0] == 200))
need.append(("a browser is sent to the first run", get("/", "text/html,application/xhtml+xml")[1].endswith("/firstrun")))
need.append(("a script asking for / is not", get("/")[1] == base + "/"))
bad = [n for n, ok in need if not ok]
print("duo pieces ok" if not bad else "duo pieces FAILED: " + "; ".join(bad))
EOF
    stop_server "$duo_pid"
else
    echo "duo pieces FAILED: the image's server did not start" > "$work/opos-verify-duo.txt"
fi
rm -rf "$cfgd"
check "the keyboard, the Network page and the first run answer together in one server from the image" "grep -q '^duo pieces ok' \"\$work/opos-verify-duo.txt\""
tail -n 2 "$work/opos-verify-duo.txt"
# --- track integrate: END
# --- track: monitor (the Inference page): /monitor and /api/monitor answer 404 with the switch off and 200 with it on ---
# Two more servers from the image, started through serve() on picked loopback ports: one without OPOS_MONITOR and one with
# OPOS_MONITOR=1 (what opos-server.service sets). serve() gives them no OPOS_* variable of the caller's, so the "off" one is
# off. Offline: the local model-port check goes to a loopback port that is picked and left closed and no AGIW monitor is
# configured, so nothing leaves the builder. Each server is stopped by its pid; they run from, and write only into, $work.
monitor_out=$work/opos-verify-monitor.txt
mport_off=$(pick_port) && [ -n "$mport_off" ] || { echo "verify-image.sh: cannot pick a free loopback port" >&2; exit 2; }
if serve "$mport_off" "$work/opos-verify-monitor-off.log"; then
    mon_off_pid=$sv_pid
    mport_on=$(pick_port_apart "$mport_off") && mport_dead=$(pick_port_apart "$mport_off" "$mport_on") || mport_on=''
    if [ -n "$mport_on" ] && serve "$mport_on" "$work/opos-verify-monitor-on.log" OPOS_MONITOR=1 "OPOS_LOCAL_URL=http://127.0.0.1:$mport_dead/v1"; then
        mon_on_pid=$sv_pid
        python3 -I - "$mport_off" "$mport_on" > "$monitor_out" 2>&1 <<'EOF'
import json, re, sys, time, urllib.error, urllib.request
def get(port, path, token=None):
    req = urllib.request.Request("http://127.0.0.1:%s%s" % (port, path), headers={"X-OPOS-Token": token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
def home(port):
    for _ in range(90):
        try:
            return get(port, "/")[1]
        except OSError:
            time.sleep(1)
    return ""
off_port, on_port = sys.argv[1:3]
off, on = home(off_port), home(on_port)
tok = lambda page: (re.search(r'name="opos-token" content="([^"]+)"', page) or [None, None])[1]
t_off, t_on = tok(off), tok(on)
need = [("both servers answered", bool(t_off and t_on)),
        ("off: /monitor is 404", get(off_port, "/monitor")[0] == 404),
        ("off: /api/monitor is 404 even with the token", get(off_port, "/api/monitor", t_off)[0] == 404),
        ("off: Settings has no link to it", 'href="/monitor"' not in off),
        ("on: Settings links to /monitor", 'href="/monitor"' in on)]
st, doc = get(on_port, "/monitor")
need.append(("on: /monitor is 200 with the token meta and the title", st == 200 and 'name="opos-token"' in doc and "<title>Inference - OPOS</title>" in doc))
need.append(("on: /api/monitor without the token is 403", get(on_port, "/api/monitor")[0] == 403))
st, body = get(on_port, "/api/monitor", t_on)
try:
    view = json.loads(body)
    shape = st == 200 and view["version"] == 1 and [s["id"] for s in view["sections"]] == ["duo", "remote"]
except (ValueError, KeyError, TypeError):
    shape = False
need.append(("on: /api/monitor with the token is 200, two sections", shape))
bad = [n for n, ok in need if not ok]
print("monitor routes ok" if not bad else "monitor routes FAILED: " + "; ".join(bad))
EOF
        # a server that ended while it was asked is not what answered
        { up "$mon_off_pid" && up "$mon_on_pid"; } || echo "monitor routes FAILED: one of the image's servers ended while it was being asked" > "$monitor_out"
        stop_server "$mon_on_pid"
    else
        echo "monitor routes FAILED: the image's second server did not start" > "$monitor_out"
    fi
    stop_server "$mon_off_pid"
else
    echo "monitor routes FAILED: the image's server did not start" > "$monitor_out"
fi
check "/monitor and /api/monitor answer 404 with the switch off and 200 with it on, from the image's own python3" "grep -q '^monitor routes ok' \"\$monitor_out\""
tail -n 2 "$monitor_out"
# --- end track: monitor ---
# --- qa: BEGIN (opos-selftest; docs/duo-acceptance.md)
check "opos-selftest is installed with its /usr/sbin wrapper, and the image's own python3 can start it" "[ -x $mnt/usr/libexec/opos/opos-selftest ] && [ -x $mnt/usr/sbin/opos-selftest ] && sh -n $mnt/usr/sbin/opos-selftest && grep -q '^exec /usr/libexec/opos/opos-selftest' $mnt/usr/sbin/opos-selftest && py $mnt/usr/libexec/opos/opos-selftest --help | grep -q 'Changes nothing'"
# --- qa: END
umount "$mnt"; rmdir "$mnt"
[ $fail -eq 0 ] && echo "VERIFY: PASS (offline; not a boot or display test)" || echo "VERIFY: FAIL"
exit $fail
