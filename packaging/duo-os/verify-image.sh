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
if [ -f "$r/home/droidian/.ssh/authorized_keys" ]; then
    echo "NOTE: DEV image: droidian has $(wc -l < "$r/home/droidian/.ssh/authorized_keys") authorized ssh key(s)"
else
    echo "NOTE: no ssh key in this image (nothing new can log in)"
fi
check "phoc has its -E/--exec startup command" "strings $r/usr/bin/phoc | grep -q 'Command (session) that will be ran at startup'"
# --- track: network (Wi-Fi and radio status) ---
check "the server may manage the phone's network (OPOS_NETWORK=1 drop-in)" "grep -q '^Environment=OPOS_NETWORK=1' $w/opos-server.service.d/20-opos-network.conf"
check "the Network page and its logic are in the image" "[ -f $r/opt/opos/src/opos/net.py ] && [ -f $r/opt/opos/src/opos/net_page.py ]"
if [ -f "$r/etc/polkit-1/rules.d/50-opos-network.rules" ]; then echo "NOTE: polkit rule installed: droidian may change Wi-Fi in NetworkManager without being asked"; else echo "NOTE: no polkit rule from OPOS (the base's own rules decide whether the Network page may join Wi-Fi)"; fi
# --- end track: network ---
check "OPOS revision recorded" "[ -s $r/opt/opos/REVISION ]"
echo "OPOS revision: $(cat $r/opt/opos/REVISION 2>/dev/null)"
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
umount "$mnt"

# Server smoke test: qemu-user with the read-only image as its sysroot (-L), so the image's own
# aarch64 python3 loads its own standard library and runs /opt/opos from the image.
mount -o ro,loop,noload "$img" "$mnt" || exit 1
port=18999
py() { PYTHONPATH="$mnt/opt/opos/src" PYTHONDONTWRITEBYTECODE=1 HOME=/tmp "$q" -L "$mnt" "$mnt/usr/bin/python3" "$@"; }
py -c "import sys; print(sys.version.split()[0], sys.platform)" | sed 's/^/image python3 /'
# >>> track firstboot: first run and time zone (src/opos/firstrun.py, timezone.py). Static wiring, then the image's own python3 and zone files under qemu-user.
zi=$r/usr/share/zoneinfo
fb_list() { py -c 'import sys; from opos import timezone as t; z, s = t.zones(sys.argv[1]); sys.exit(0 if s == "system" and "Europe/Berlin" in z and len(z) > 300 else 1)' "$zi"; }
fb_tz() { [ "$( export TZ=Asia/Kolkata; py -c 'import time; print(time.strftime("%z", time.localtime(0)))' )" = +0530 ]; }
fb_saved() { d=$(mktemp -d); ( export OPOS_CONFIG_DIR=$d; py -m opos.timezone --set Asia/Kolkata >/dev/null &&
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
# --- track osk: the image's own python3 reads the keyboard's files and builds the page tags (no server needed) ---
py -c "
import os
os.environ['OPOS_OSK'] = '1'
from opos import osk
js, css = osk.asset('/osk.js'), osk.asset('/osk.css')
assert js and css and len(js[0]) > 5000 and len(css[0]) > 500, 'keyboard assets missing or tiny'
t = osk.tags('n' * 22)
assert '/osk.js' in t and '/osk.css' in t and 'nonce=' in t
print('on-screen keyboard assets: osk.js', len(js[0]), 'bytes, osk.css', len(css[0]), 'bytes')" > /tmp/opos-verify-osk.txt 2>&1
check "the image's python3 serves the on-screen keyboard's files" "grep -q 'on-screen keyboard assets' /tmp/opos-verify-osk.txt"
tail -n 1 /tmp/opos-verify-osk.txt
kill "$(cat /tmp/opos-verify.pid)" 2>/dev/null
for i in 1 2 3 4 5 6 7 8 9 10; do pkill -f "opos.web --port $port" 2>/dev/null || break; sleep 1; done
head -5 /tmp/opos-verify.log
umount "$mnt"; rmdir "$mnt"
[ $fail -eq 0 ] && echo "VERIFY: PASS (offline; not a boot or display test)" || echo "VERIFY: FAIL"
exit $fail
