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
# --- track: network (Wi-Fi and radio status) ---
check "the server may manage the phone's network (OPOS_NETWORK=1 drop-in)" "grep -q '^Environment=OPOS_NETWORK=1' $w/opos-server.service.d/20-opos-network.conf"
check "the Network page and its logic are in the image" "[ -f $r/opt/opos/src/opos/net.py ] && [ -f $r/opt/opos/src/opos/net_page.py ]"
if [ -f "$r/etc/polkit-1/rules.d/50-opos-network.rules" ]; then echo "NOTE: polkit rule installed: droidian may change Wi-Fi in NetworkManager without being asked"; else echo "NOTE: no polkit rule from OPOS (the base's own rules decide whether the Network page may join Wi-Fi)"; fi
# --- end track: network ---
check "OPOS revision recorded" "[ -s $r/opt/opos/REVISION ]"
echo "OPOS revision: $(cat $r/opt/opos/REVISION 2>/dev/null)"
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
check "/etc/opos is 0755 and holds only the commented example: no monitor.env, no setting that is on, nothing secret" "[ -d $r/etc/opos ] && [ \"\$(stat -c %a $r/etc/opos)\" = 755 ] && [ -f $r/etc/opos/monitor.env.example ] && [ ! -e $r/etc/opos/monitor.env ] && ! grep -v '^[[:space:]]*#' $r/etc/opos/monitor.env.example | grep -q '[^[:space:]]'"
check "the example names the reverse-tunnel setting (OPOS_AGIW_URL on the Duo's loopback, OPOS_AGIW_HOST for the monitor's Host)" "grep -qx '#OPOS_AGIW_URL=http://127.0.0.1:18767' $r/etc/opos/monitor.env.example && grep -qx '#OPOS_AGIW_HOST=127.0.0.1:8767' $r/etc/opos/monitor.env.example"
# --- end track: monitor ---
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
# --- track integrate: BEGIN (the keyboard, the Network page and the first run in one server; docs/duo-os-final.md)
# A second server from the image, started with the three switches opos-server.service and its drop-in set, asked for every page
# the pieces add: they must be wired into the same server, not merely present on disk. Offline, like the smoke test above.
cfgd=$(mktemp -d); port2=18998
( export OPOS_OSK=1 OPOS_NETWORK=1 OPOS_FIRSTRUN=1 OPOS_CONFIG_DIR=$cfgd; cd /tmp && py -m opos.web --port $port2 >/tmp/opos-verify-duo.log 2>&1 & echo $! > /tmp/opos-verify-duo.pid )
python3 - "$port2" > /tmp/opos-verify-duo.txt 2>&1 <<'EOF'
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
kill "$(cat /tmp/opos-verify-duo.pid)" 2>/dev/null
for i in 1 2 3 4 5 6 7 8 9 10; do pkill -f "opos.web --port $port2" 2>/dev/null || break; sleep 1; done
rm -rf "$cfgd"
check "the keyboard, the Network page and the first run answer together in one server from the image" "grep -q '^duo pieces ok' /tmp/opos-verify-duo.txt"
tail -n 2 /tmp/opos-verify-duo.txt
# --- track integrate: END
# --- track: monitor (the Inference page): /monitor and /api/monitor answer 404 with the switch off and 200 with it on ---
# Two servers from the image, one without OPOS_MONITOR and one with OPOS_MONITOR=1 (what opos-server.service sets). Offline: the
# local model-port check goes to a closed port on the builder's loopback and no AGIW monitor is configured, so nothing leaves it.
mport_off=18996; mport_on=18997
( unset OPOS_MONITOR OPOS_AGIW_URL OPOS_AGIW_HOST OPOS_LOCAL_URL; cd /tmp && py -m opos.web --port $mport_off >/tmp/opos-verify-monitor-off.log 2>&1 & echo $! > /tmp/opos-verify-monitor-off.pid )
( unset OPOS_AGIW_URL OPOS_AGIW_HOST; export OPOS_MONITOR=1 OPOS_LOCAL_URL=http://127.0.0.1:18995/v1; cd /tmp && py -m opos.web --port $mport_on >/tmp/opos-verify-monitor-on.log 2>&1 & echo $! > /tmp/opos-verify-monitor-on.pid )
python3 - "$mport_off" "$mport_on" > /tmp/opos-verify-monitor.txt 2>&1 <<'EOF'
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
for f in off on; do kill "$(cat /tmp/opos-verify-monitor-$f.pid)" 2>/dev/null; done
for p in $mport_off $mport_on; do for i in 1 2 3 4 5 6 7 8 9 10; do pkill -f "opos.web --port $p" 2>/dev/null || break; sleep 1; done; done
check "/monitor and /api/monitor answer 404 with the switch off and 200 with it on, from the image's own python3" "grep -q '^monitor routes ok' /tmp/opos-verify-monitor.txt"
tail -n 2 /tmp/opos-verify-monitor.txt
# --- end track: monitor ---
# --- qa: BEGIN (opos-selftest; docs/duo-acceptance.md)
check "opos-selftest is installed with its /usr/sbin wrapper, and the image's own python3 can start it" "[ -x $mnt/usr/libexec/opos/opos-selftest ] && [ -x $mnt/usr/sbin/opos-selftest ] && sh -n $mnt/usr/sbin/opos-selftest && grep -q '^exec /usr/libexec/opos/opos-selftest' $mnt/usr/sbin/opos-selftest && py $mnt/usr/libexec/opos/opos-selftest --help | grep -q 'Changes nothing'"
# --- qa: END
umount "$mnt"; rmdir "$mnt"
[ $fail -eq 0 ] && echo "VERIFY: PASS (offline; not a boot or display test)" || echo "VERIFY: FAIL"
exit $fail
