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
check "OTA: the inbox is 2770 root:droidian, staging 0700 and the state directory 0755 root-owned" "[ \"\$(stat -c %a:%u:%g $r/var/lib/opos-update/inbox)\" = 2770:0:$ota_gid ] && [ \"\$(stat -c %a:%u:%g $r/var/lib/opos-update/staging)\" = 700:0:0 ] && [ \"\$(stat -c %a:%u:%g $r/var/lib/opos-update)\" = 755:0:0 ]"
check "OTA: etc/opos/update.json is absent (nothing is fetched) or names one address and leaves auto_check off" "ota_source_ok"
ota_keys=$(grep -c . "$r/etc/opos/update/allowed_signers" 2>/dev/null || true)
if [ "${ota_keys:-0}" -gt 0 ]; then echo "NOTE: update signers: $ota_keys public key(s)"; else echo "NOTE: update signers: NONE (this image accepts no updates)"; fi
# ---- END OPOS OTA checks ---------------------------------------------------------------------------------------
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
