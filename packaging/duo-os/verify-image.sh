#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Offline checks of an OPOS OS image on an x86 builder: unit wiring, phoc's -E, and the OPOS server
# started from /opt/opos by the image's own aarch64 python3 under qemu-user. Not a boot test.
#   sudo sh packaging/duo-os/verify-image.sh OUT.img
set -u
[ "$(id -u)" -eq 0 ] || { echo "verify-image.sh: run as root" >&2; exit 2; }
img=$1
q=$(command -v qemu-aarch64-static) || { echo "needs qemu-aarch64-static" >&2; exit 2; }
mnt=$(mktemp -d)
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
check "the kiosk clears stale profile locks, drops WAYLAND_SOCKET, logs, backs off and can drop the GPU" "grep -q SingletonLock $k && grep -q 'unset WAYLAND_SOCKET' $k && grep -q 'logger -t opos-kiosk' $k && grep -q -- '--disable-gpu' $k && grep -q 'sleep \"\$delay\"' $k"
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
check "OPOS revision recorded" "[ -s $r/opt/opos/REVISION ]"
echo "OPOS revision: $(cat $r/opt/opos/REVISION 2>/dev/null)"
umount "$mnt"

# Server smoke test: qemu-user with the read-only image as its sysroot (-L), so the image's own
# aarch64 python3 loads its own standard library and runs /opt/opos from the image.
mount -o ro,loop,noload "$img" "$mnt" || exit 1
port=18999
py() { PYTHONPATH="$mnt/opt/opos/src" PYTHONDONTWRITEBYTECODE=1 HOME=/tmp "$q" -L "$mnt" "$mnt/usr/bin/python3" "$@"; }
py -c "import sys; print(sys.version.split()[0], sys.platform)" | sed 's/^/image python3 /'
( cd /tmp && py -m opos.web --port $port >/tmp/opos-verify.log 2>&1 & echo $! > /tmp/opos-verify.pid )
ok=1
for i in $(seq 1 60); do
    if python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:$port/', timeout=2).status == 200 else 1)" 2>/dev/null; then ok=0; break; fi
    sleep 1
done
check "OPOS server from the image answers HTTP 200 (qemu-user, ${i}s)" "[ $ok -eq 0 ]"
python3 - "$port" <<'EOF'
import re, sys, json, urllib.request
p = sys.argv[1]
html = urllib.request.urlopen(f"http://127.0.0.1:{p}/", timeout=5).read().decode()
tok = re.search(r'name="opos-token" content="([^"]+)"', html).group(1)
s = json.load(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{p}/api/state", headers={"X-OPOS-Token": tok}), timeout=5))
print("state: battery %s%%, mode %s, lock '%s'" % (s["battery"]["percent"], s.get("helper", {}).get("mode", "-"), s.get("lock_story", "")))
EOF
kill "$(cat /tmp/opos-verify.pid)" 2>/dev/null
for i in 1 2 3 4 5 6 7 8 9 10; do pkill -f "opos.web --port $port" 2>/dev/null || break; sleep 1; done
head -5 /tmp/opos-verify.log
umount "$mnt"; rmdir "$mnt"
[ $fail -eq 0 ] && echo "VERIFY: PASS (offline; not a boot or display test)" || echo "VERIFY: FAIL"
exit $fail
