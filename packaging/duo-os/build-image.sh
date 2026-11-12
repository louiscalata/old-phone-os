#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Build an OPOS OS rootfs for the Surface Duo from the community Droidian port's rootfs:
# the port's kernel, Halium layer and hardware services stay; OPOS replaces the phone shell
# as the only interface (phosh stays installed as the fallback, as with the port's item).
#
#   sudo sh packaging/duo-os/build-image.sh BASE_ROOTFS.img OUT.img [GIT_REF]
#
# BASE_ROOTFS.img is read, never written. OUT.img is a new raw ext4 image in the same layout
# (Halium loop-mounts it as /data/rootfs.img). Nothing is flashed.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "build-image.sh: run as root (loop mount)" >&2; exit 2; }
[ "$#" -ge 2 ] || { echo "usage: build-image.sh BASE_ROOTFS.img OUT.img [GIT_REF]" >&2; exit 2; }
base=$1 out=$2 ref=${3:-HEAD}
here=$(cd "$(dirname "$0")" && pwd)
src=$(cd "$here/../.." && pwd)
g() { git -c safe.directory="$src" -C "$src" "$@"; }
build_mode=dev
if [ "${OPOS_RELEASE:-0}" = 1 ]; then
    python3 -I -B "$here/image-policy.py" release-inputs unused || exit 2
    build_mode=release
fi
case ${OPOS_VERIFY_IMAGE:-0} in 0 | 1) ;; *) echo "OPOS_VERIFY_IMAGE must be 0 or 1" >&2; exit 2 ;; esac
# --- dev span: CHECK BEGIN (OPOS_DEV_SPAN; the block that acts on it is "dev span: BEGIN" below)
# Refused before OUT is touched, so a wrong call leaves no half-built image behind: OPOS_DEV_SPAN is 1, or unset/empty/0, and 1 is a dev-image
# option that only works together with OPOS_DEV_SSH_PUBKEY.
case ${OPOS_DEV_SPAN:-} in '' | 0 | 1) ;; *) echo "build-image.sh: OPOS_DEV_SPAN must be 1 (or unset)" >&2; exit 2 ;; esac
if [ "${OPOS_DEV_SPAN:-}" = 1 ] && [ -z "${OPOS_DEV_SSH_PUBKEY:-}" ]; then
    echo "build-image.sh: OPOS_DEV_SPAN=1 is a dev-image option: it needs OPOS_DEV_SSH_PUBKEY=<file.pub> as well" >&2
    exit 2
fi
# --- dev span: CHECK END
# --- dev sudo: CHECK BEGIN (OPOS_DEV_SUDO; the block that acts on it is "dev sudo: BEGIN" below)
# Refused before OUT is touched, exactly as OPOS_DEV_SPAN is: OPOS_DEV_SUDO is 1, or unset/empty/0, and 1 is a dev-image option that only works together
# with OPOS_DEV_SSH_PUBKEY. An image with passwordless root for the login user is never an image to carry, so it is not offered without the dev key.
case ${OPOS_DEV_SUDO:-} in '' | 0 | 1) ;; *) echo "build-image.sh: OPOS_DEV_SUDO must be 1 (or unset)" >&2; exit 2 ;; esac
if [ "${OPOS_DEV_SUDO:-}" = 1 ] && [ -z "${OPOS_DEV_SSH_PUBKEY:-}" ]; then
    echo "build-image.sh: OPOS_DEV_SUDO=1 is a dev-image option: it needs OPOS_DEV_SSH_PUBKEY=<file.pub> as well" >&2
    exit 2
fi
# --- dev sudo: CHECK END
# --- polkit: CHECK BEGIN (OPOS_NET_POLKIT; the block that acts on it is in "track: network" below)
# The rule is installed only for a 1, so a typo (yes, true, 2) used to build an image WITHOUT it and say nothing, and without the rule the phone cannot join
# Wi-Fi. Refused before OUT is touched: unset, empty, 0 or 1.
case ${OPOS_NET_POLKIT:-} in '' | 0 | 1) ;; *) echo "build-image.sh: OPOS_NET_POLKIT must be 1 (or unset)" >&2; exit 2 ;; esac
# --- polkit: CHECK END
# Owner recovery is independent of every development feature. Refuse bad inputs before copying.
# Keep the old spelling as an alias; either choice now means SSH is disabled and masked.
OPOS_NO_SSH=${OPOS_NO_SSH:-${OPOS_ALLOW_NO_SSH:-0}}
case ${OPOS_ALLOW_NO_SSH:-0}:$OPOS_NO_SSH:${OPOS_RELEASE:-0} in
    0:0:0 | 0:0:1 | 0:1:0 | 0:1:1 | 1:1:0 | 1:1:1) ;;
    *) echo "OPOS_NO_SSH, OPOS_ALLOW_NO_SSH and OPOS_RELEASE must be 0 or 1, with no conflicting choices" >&2; exit 2 ;;
esac
if [ "$OPOS_NO_SSH" = 1 ]; then
    [ -z "${OPOS_OWNER_SSH_PUBKEY:-}${OPOS_DEV_SSH_PUBKEY:-}" ] || { echo "OPOS_NO_SSH conflicts with SSH keys" >&2; exit 2; }
else
    [ -n "${OPOS_OWNER_SSH_PUBKEY:-}" ] || { echo "OPOS_OWNER_SSH_PUBKEY=<owner.pub> is required (or OPOS_NO_SSH=1 for no SSH access at all)" >&2; exit 2; }
    python3 -I -B "$here/image-policy.py" key "$OPOS_OWNER_SSH_PUBKEY" || exit 2
fi
if [ -n "${OPOS_DEV_SSH_PUBKEY:-}" ]; then
    python3 -I -B "$here/image-policy.py" key "$OPOS_DEV_SSH_PUBKEY" || exit 2
fi
# Resolve the parent once; the private image and OUT must share a filesystem.
out_dir=$(python3 -I -B "$here/image-policy.py" output-parent "$out") || exit 2
out=$out_dir/$(basename "$out")
python3 -I -B "$here/image-policy.py" check-output "$out" || exit 2
stage='' mnt='' image=''
image_detached() {
    attached=$(losetup -j "$image") || { echo "build-image.sh: cannot check loop attachments; retaining $image" >&2; return 1; }
    if [ -n "$attached" ]; then
        printf 'build-image.sh: backing file still attached; retaining %s\n%s\n' "$image" "$attached" >&2
        return 1
    fi
}
cleanup() {
    status=$?
    trap - EXIT
    trap '' INT TERM HUP
    keep=0
    if [ -n "$mnt" ]; then
        if mountpoint -q "$mnt"; then
            if ! umount "$mnt"; then
                echo "build-image.sh: mount point still attached: $mnt; retaining backing file $image" >&2
                keep=1 status=1
            fi
        fi
        if mountpoint -q "$mnt"; then
            echo "build-image.sh: mount point still attached: $mnt; retaining backing file $image" >&2
            keep=1 status=1
        else
            rmdir "$mnt" 2>/dev/null || true
        fi
    fi
    if [ -n "$stage" ]; then
        if [ -f "$image" ] && ! image_detached; then keep=1 status=1; fi
        if [ "$keep" = 0 ]; then rm -rf "$stage" || true; fi
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

# --- identity epoch: BEGIN (scratch-root tests; no mount in this block)
# An omitted release time comes from the selected ref, never the wall clock.
if [ "${SOURCE_DATE_EPOCH+x}" != x ]; then
    # --no-show-signature: log.showSignature would put signature text before the timestamp.
    SOURCE_DATE_EPOCH=$(g log -1 --no-show-signature --format=%ct "$ref")
fi
case $SOURCE_DATE_EPOCH in '' | *[!0-9]*) echo "SOURCE_DATE_EPOCH must be nonnegative Unix seconds" >&2; exit 2 ;; esac
export SOURCE_DATE_EPOCH
# --- identity epoch: END

stage=$(mktemp -d "$out_dir/.opos-build.XXXXXX")
chmod 0700 "$stage"
image=$stage/rootfs.img
identity_state=$stage/identity-state
# Exclusive creation inside the private directory; cp retains sparse image holes.
(set -C; : > "$image")
echo "copying $base -> private staging image"
cp --sparse=always "$base" "$image"
if [ "${OPOS_RELEASE:-0}" = 1 ]; then
    base_sha=$(python3 -I -B "$here/image-policy.py" release-base "$image") || exit 2
else
    base_sha=$(python3 -I -B "$here/image-policy.py" image-sha256 "$image")
fi
echo "staged base SHA-256: $base_sha"
# The mount point stays outside staging, so cleanup never walks a mounted image.
mnt=$(mktemp -d)
if [ "${OPOS_RELEASE:-0}" = 1 ]; then
    mount -o ro,loop,noload "$image" "$mnt"
    python3 -I -B "$here/image-policy.py" release-base-layout "$mnt"
    umount "$mnt"
fi

e2fsck -fy "$image" >/dev/null 2>&1 || true
mount -o loop "$image" "$mnt"
r=$mnt
[ -f "$r/usr/bin/phosh-session" ] && [ -x "$r/usr/bin/chromium" ] && [ -x "$r/usr/bin/python3" ] \
    || { echo "base rootfs lacks phosh-session, chromium or python3" >&2; exit 1; }

# --- identity prepare: BEGIN (scratch-root tests; no mount in this block)
g show "$ref:pyproject.toml" > "$identity_state.metadata"
python3 -I -B "$src/tools/opos-image-identity.py" prepare --root "$r" --state "$identity_state" \
    --base "$image" --base-sha256 "$base_sha" --metadata "$identity_state.metadata" --revision "$(g rev-parse "$ref")"
# --- identity prepare: END
echo "installing OPOS $(g rev-parse --short "$ref") into /opt/opos"
rm -rf "$r/opt/opos"
mkdir -p "$r/opt/opos"
g archive "$ref" src LICENSE THIRD_PARTY.md pyproject.toml | tar -x -C "$r/opt/opos"
g rev-parse "$ref" > "$r/opt/opos/REVISION"

install -d "$r/usr/libexec/opos"
install -m 0755 "$here/opos-kiosk" "$r/usr/libexec/opos/opos-kiosk"
install -m 0755 "$here/opos-session" "$r/usr/libexec/opos/opos-session"
install -m 0755 "$here/opos-switch" "$r/usr/sbin/opos-switch"
for u in opos-server.service opos.service opos-fallback.service; do
    install -m 0644 "$here/$u" "$r/etc/systemd/system/$u"
done

# The port's composer watchdog bounces a vendor composer that never became DRM master and then restarts
# phosh, which is not running here: a phoc already on the killed composer would stay dark. Restart OPOS
# too (try-restart leaves a unit that is not running alone). The script belongs to the port's adaptation
# package, so a package upgrade puts the original back; verify-image.sh reports that.
# Idempotent: a BASE that is an earlier OPOS image already has the edit, and the sed would add opos.service
# a second time. Only a script that has neither the original line nor the edited one is refused.
wd=$r/usr/local/sbin/sfduo-composer-watchdog
if grep -qF 'systemctl try-restart phosh.service opos.service 2>/dev/null || true' "$wd"; then
    echo "sfduo-composer-watchdog already restarts opos.service; leaving it as it is"
elif grep -qF 'systemctl try-restart phosh.service 2>/dev/null || true' "$wd"; then
    sed -i -e 's|systemctl try-restart phosh\.service |systemctl try-restart phosh.service opos.service |' \
        -e 's|reacquired DRM master - restarting phosh"|reacquired DRM master - restarting the shell (phosh or opos)"|' "$wd"
else
    echo "base rootfs: sfduo-composer-watchdog is not the script the OPOS edit is written for" >&2; exit 1
fi
if ! grep -qF 'systemctl try-restart phosh.service opos.service 2>/dev/null || true' "$wd" || ! sh -n "$wd"; then
    echo "the sfduo-composer-watchdog edit did not take" >&2; exit 1
fi

# OPOS is the shell from the first boot: phosh's symlink goes, OPOS's two are added.
rm -f "$r/etc/systemd/system/graphical.target.wants/phosh.service" "$r/etc/systemd/system/graphical.target.wants/item.service"
ln -sf /etc/systemd/system/opos.service "$r/etc/systemd/system/graphical.target.wants/opos.service"
ln -sf /etc/systemd/system/opos-server.service "$r/etc/systemd/system/multi-user.target.wants/opos-server.service"

# --- qa: BEGIN (opos-selftest; docs/duo-acceptance.md)
# The acceptance script the PC (deploy/duo-acceptance.ps1) and the owner run on the phone as droidian: read-only, no root.
# /usr/sbin/opos-selftest is a two-line wrapper; both paths are OPOS-owned, so an over-the-air update can replace the script.
install -m 0755 "$here/opos-selftest" "$r/usr/libexec/opos/opos-selftest"
printf '%s\n' '#!/bin/sh' '# SPDX-License-Identifier: GPL-3.0-or-later' 'exec /usr/libexec/opos/opos-selftest "$@"' > "$r/usr/sbin/opos-selftest"
chmod 0755 "$r/usr/sbin/opos-selftest"
( cd "$r" && sha256sum usr/libexec/opos/opos-selftest usr/sbin/opos-selftest )
# --- qa: END

# Boot evidence that survives a failed boot: the rootfs lives on userdata, so a persistent journal and one
# diagnostics dump (opos-diag, 90 s after boot) can be read afterwards over ssh or from TWRP.
mkdir -p "$r/var/log/journal" "$r/etc/systemd/journald.conf.d"
printf '[Journal]\nStorage=persistent\nSystemMaxUse=200M\n' > "$r/etc/systemd/journald.conf.d/50-opos-persistent.conf"
install -m 0755 "$here/opos-diag" "$r/usr/libexec/opos/opos-diag"
install -m 0644 "$here/opos-diag.service" "$r/etc/systemd/system/opos-diag.service"
ln -sf /etc/systemd/system/opos-diag.service "$r/etc/systemd/system/multi-user.target.wants/opos-diag.service"

# --- clock: BEGIN (opos-clock, opos-clock-set; packaging/duo-os/README.md, "The clock")
# The Duo boots at 1970 after a RAM-boot. opos-clock raises a clock earlier than a floor to it and never lowers one;
# the floor is the OPOS commit's time, never earlier than 2026. Real time (systemd-timesyncd, kept as the base has it)
# takes over once there is a network. The droidian user has no passwordless sudo, so a developer's PC sets the time by
# dropping Unix seconds into ~/opos-set-time over ssh: a root path unit checks and applies them (dev images only).
floor=$(g log -1 --format=%ct "$ref")
case $floor in '' | *[!0-9]*) echo "cannot read the commit time of $ref" >&2; exit 1 ;; esac
[ "$floor" -ge 1767225600 ] || floor=1767225600
echo "$floor" > "$r/opt/opos/CLOCK_FLOOR"
install -m 0755 "$here/opos-clock" "$r/usr/libexec/opos/opos-clock"
install -m 0644 "$here/opos-clock.service" "$r/etc/systemd/system/opos-clock.service"
ln -sf /etc/systemd/system/opos-clock.service "$r/etc/systemd/system/multi-user.target.wants/opos-clock.service"
clock_home=$(awk -F: '$1=="droidian"{print $6}' "$r/etc/passwd")
[ -n "$clock_home" ] || { echo "base rootfs has no droidian user" >&2; exit 1; }
for u in opos-clock-set.path opos-clock-set.service; do
    sed "s|@DROP@|$clock_home/opos-set-time|g" "$here/$u" > "$r/etc/systemd/system/$u"
    chmod 0644 "$r/etc/systemd/system/$u"
done
rm -f "$r/etc/systemd/system/multi-user.target.wants/opos-clock-set.path"
if [ -n "${OPOS_DEV_SSH_PUBKEY:-}" ]; then
    ln -sf /etc/systemd/system/opos-clock-set.path "$r/etc/systemd/system/multi-user.target.wants/opos-clock-set.path"
    echo "DEV IMAGE: Unix seconds dropped in ~/opos-set-time over ssh set the clock (opos-clock-set.path)"
fi
echo "clock floor $floor ($(date -u -d "@$floor" +%FT%TZ 2>/dev/null || echo '?'))"
# --- clock: END

# --- track osk: the on-screen keyboard (packaging/duo-os/OSK.md) ---------------------------------------------------------
# The default is the in-page keyboard: opos-server.service sets OPOS_OSK=1 and src/opos/osk.py serves /osk.js and /osk.css
# from the OPOS tree archived above (src/opos/osk_static). The opt-in is the port's phosh-osk-stevia, started by
# opos-osk.service, which is enabled but idle until /etc/default/opos-osk says OPOS_OSK=stevia.
install -m 0755 "$here/opos-osk" "$r/usr/libexec/opos/opos-osk"
install -m 0644 "$here/opos-osk.service" "$r/etc/systemd/system/opos-osk.service"
ln -sf /etc/systemd/system/opos-osk.service "$r/etc/systemd/system/graphical.target.wants/opos-osk.service"
printf '%s\n' '# On-screen keyboard. OPOS_OSK=1 (the default when this line is absent): the in-page keyboard served by opos-server.' \
    '# OPOS_OSK=off: none. OPOS_OSK=stevia: the port'"'"'s phosh-osk-stevia instead (opt-in, not yet tried on the phone).' \
    '# After a change: sudo systemctl restart opos-server opos-osk, then pkill chromium as droidian (opos-kiosk starts it again).' \
    '#OPOS_OSK=1' > "$r/etc/default/opos-osk"
chmod 0644 "$r/etc/default/opos-osk"
# --- end track osk --------------------------------------------------------------------------------------------------------

# --- wifi compat: BEGIN
install -d "$r/etc/NetworkManager/conf.d"
rm -f "$r/etc/NetworkManager/conf.d/90-opos-wifi-compat.conf"
printf '%s\n' '[connection]' 'wifi-sec.pmf=1' > "$r/etc/NetworkManager/conf.d/90-opos-wifi-compat.conf"
chown 0:0 "$r/etc/NetworkManager/conf.d/90-opos-wifi-compat.conf"
chmod 0644 "$r/etc/NetworkManager/conf.d/90-opos-wifi-compat.conf"
# --- wifi compat: END

# --- track: network (Wi-Fi and radio status): Settings > Network and /network may join Wi-Fi through NetworkManager ---
# The drop-in turns control on (without it the page only reads). The polkit rule is left out unless
# OPOS_NET_POLKIT=1 (docs/network.md).
install -d "$r/etc/systemd/system/opos-server.service.d"
install -m 0644 "$here/opos-network.conf" "$r/etc/systemd/system/opos-server.service.d/20-opos-network.conf"
if [ "${OPOS_NET_POLKIT:-0}" = 1 ]; then
    # not `install -d` on a directory that is there: it would set the mode to 0755, and the base's rules.d is 0750 root:polkitd on purpose
    [ -d "$r/etc/polkit-1/rules.d" ] || install -d -m 0755 "$r/etc/polkit-1/rules.d"
    install -m 0644 "$here/50-opos-network.rules" "$r/etc/polkit-1/rules.d/50-opos-network.rules"
    echo "NETWORK: polkit rule installed: opos-server.service as droidian may use the four NetworkManager actions"
else
    rm -f "$r/etc/polkit-1/rules.d/50-opos-network.rules"  # one left by an earlier OPOS image used as the BASE goes too
fi
( cd "$r" && sha256sum etc/systemd/system/opos-server.service.d/20-opos-network.conf $(ls etc/polkit-1/rules.d/50-opos-network.rules 2>/dev/null) )
# --- end track: network ---

# Everyday apps (/apps). The drop-in is installed with no names, so the main page stays as it is until a name is set.
install -d "$r/etc/systemd/system/opos-server.service.d"
install -m 0644 "$here/opos-apps.conf" "$r/etc/systemd/system/opos-server.service.d/20-opos-apps.conf"

# --- track: monitor (the Inference page; packaging/duo-os/README.md, "Inference page") ---
# opos-server.service sets OPOS_MONITOR=1 and reads /etc/opos/monitor.env when it exists. The image installs no such file and
# nothing secret: only the directory and a commented example, so the page fetches nothing but the phone's own 127.0.0.1 model-port
# check until the owner adds a file.
install -d -m 0755 "$r/etc/opos"
# Always replace inherited mode, including a plain dev build with no dev knobs.
rm -f "$r/etc/opos/build-mode"
printf '%s\n' "$build_mode" > "$r/etc/opos/build-mode"
chmod 0644 "$r/etc/opos/build-mode"
cat > "$r/etc/opos/monitor.env.example" <<'EOF'
# Optional settings for the Inference page (Settings > Inference). None is needed: without /etc/opos/monitor.env the page checks
# this phone's own 127.0.0.1 model port and fetches nothing else.
#
# To show a computer's AGIW monitor as well, copy this file to /etc/opos/monitor.env, remove the # from the lines you want, then run
#   sudo systemctl restart opos-server
# The monitor is read over a reverse tunnel from that computer, so the Duo's own loopback reaches it: the Duo's 127.0.0.1:18767 is
# the computer's 127.0.0.1:8767 (packaging/duo-os/README.md, "Inference page").
#OPOS_AGIW_URL=http://127.0.0.1:18767
#OPOS_AGIW_HOST=127.0.0.1:8767
EOF
chmod 0644 "$r/etc/opos/monitor.env.example"
( cd "$r" && sha256sum etc/opos/monitor.env.example )
# --- end track: monitor ---

# The Duo server user is droidian. The postmarketOS modem rule names user opos and is not installed here.
[ -d "$r/etc/polkit-1/rules.d" ] || install -d -m 0755 "$r/etc/polkit-1/rules.d"
install -m 0644 "$here/50-opos-modem.rules" "$r/etc/polkit-1/rules.d/50-opos-modem.rules"

# --- recovery: BEGIN
# OPOS_OWNER_SSH_PUBKEY provides recovery without enabling any development feature.
python3 -I -B "$here/image-policy.py" recovery-paths "$r"
if [ "${OPOS_NO_SSH:-${OPOS_ALLOW_NO_SSH:-0}}" != 1 ]; then
    uid=$(awk -F: '$1=="droidian"{print $3}' "$r/etc/passwd")
    gid=$(awk -F: '$1=="droidian"{print $4}' "$r/etc/passwd")
    home=$(awk -F: '$1=="droidian"{print $6}' "$r/etc/passwd")
    [ -n "$uid" ] && [ -n "$home" ] || { echo "base rootfs has no droidian user" >&2; exit 1; }
    case $home in /*) ;; *) echo "invalid droidian home" >&2; exit 1 ;; esac
    case $home in *..*) echo "invalid droidian home" >&2; exit 1 ;; esac
    for p in "$r$home" "$r$home/.ssh" "$r$home/.ssh/authorized_keys"; do
        [ ! -L "$p" ] || { echo "linked SSH recovery path" >&2; exit 1; }
    done
    install -d -m 0755 -o "$uid" -g "$gid" "$r$home"
    install -d -m 0700 -o "$uid" -g "$gid" "$r$home/.ssh"
    cat "$OPOS_OWNER_SSH_PUBKEY" > "$r$home/.ssh/authorized_keys"
    if [ -n "${OPOS_DEV_SSH_PUBKEY:-}" ]; then
        printf '\n' >> "$r$home/.ssh/authorized_keys"
        cat "$OPOS_DEV_SSH_PUBKEY" >> "$r$home/.ssh/authorized_keys"
    fi
    chown "$uid:$gid" "$r$home/.ssh/authorized_keys"
    chmod 0600 "$r$home/.ssh/authorized_keys"
    rm -f "$r/etc/opos/no-ssh"
else
    home=$(awk -F: '$1=="droidian"{print $6}' "$r/etc/passwd")
    [ -n "$home" ] || { echo "base rootfs has no droidian user" >&2; exit 1; }
    rm -f "$r$home/.ssh/authorized_keys"
    python3 -I -B "$here/image-policy.py" disable-ssh "$r"
    echo "NO SSH ACCESS: SSH services and sockets are disabled and masked"
fi
python3 -I -B "$here/image-policy.py" recovery "$r"
# --- recovery: END
# DEV ONLY. A separate developer key enables the clock drop and loopback DevTools.
if [ -n "${OPOS_DEV_SSH_PUBKEY:-}" ]; then
    # The same developer can then reach the kiosk Chromium's DevTools on the phone's loopback, for page
    # screenshots: ssh -L 9222:127.0.0.1:9222 droidian@<phone>, then curl http://127.0.0.1:9222/json
    printf '%s\n' '# DEV IMAGE: opos-kiosk starts Chromium with DevTools on 127.0.0.1 at this port. Delete the file to turn it off.' \
        'OPOS_KIOSK_DEBUG_PORT=9222' > "$r/etc/default/opos-kiosk"
    chmod 0644 "$r/etc/default/opos-kiosk"
    echo "DEV IMAGE: the kiosk Chromium serves DevTools on the phone's 127.0.0.1:9222 (remove with: sudo rm /etc/default/opos-kiosk)"
else
    rm -f "$r/etc/default/opos-kiosk"
fi

# --- dev span: BEGIN (OPOS_DEV_SPAN; packaging/duo-os/README.md, "Spanning both panels")
# DEV ONLY, and only together with OPOS_DEV_SSH_PUBKEY (the check at the top refuses the other cases). The service already sets OPOS_SPAN=675,42;
# this optional drop-in records the dev build choice separately and uses that same geometry. The unit file itself is not edited.
# A BASE that is an earlier dev-span image loses the drop-in unless the variable is set again. Removing the drop-in restores the unit's setting.
rm -f "$r/etc/systemd/system/opos-server.service.d/30-opos-dev-span.conf"
if [ "${OPOS_DEV_SPAN:-}" = 1 ] && [ -n "${OPOS_DEV_SSH_PUBKEY:-}" ]; then
    install -d -m 0755 "$r/etc/systemd/system/opos-server.service.d"
    printf '%s\n' '# DEV IMAGE (OPOS_DEV_SPAN=1): the two-screen span, for the window that covers both panels (675 + 42 + 675 CSS px).' \
        '# Delete this file, then daemon-reload and restart opos-server, to restore the service file setting (which also enables the span).' \
        '[Service]' 'Environment=OPOS_SPAN=675,42' > "$r/etc/systemd/system/opos-server.service.d/30-opos-dev-span.conf"
    chmod 0644 "$r/etc/systemd/system/opos-server.service.d/30-opos-dev-span.conf"
    ( cd "$r" && sha256sum etc/systemd/system/opos-server.service.d/30-opos-dev-span.conf )
    echo "DEV IMAGE: opos-server runs with OPOS_SPAN=675,42 (drop-in 30-opos-dev-span.conf; remove it to restore the service file setting)"
fi
# --- dev span: END

# --- dev sudo: BEGIN (OPOS_DEV_SUDO; packaging/duo-os/README.md, "Dev options: passwordless sudo")
# DEV ONLY, and only together with OPOS_DEV_SSH_PUBKEY (the check at the top refuses the other cases). In the base, droidian is in the sudo group but
# sudo asks for a password that nobody has, and root cannot log in, so a developer who can ssh in cannot become root to install a file, restart a
# service or read a root-only log. OPOS_DEV_SUDO=1 writes one sudoers file, /etc/sudoers.d/90-opos-dev, 0440 root:root, holding exactly the
# line below. It is validated with visudo -cf when the builder has visudo, before it goes into the image. EVERYTHING that runs as droidian (the
# OPOS server, the kiosk Chromium, any ssh session) is then root: an image built this way must never be carried, lent or shared, and the
# over-the-air design (which assumes an unprivileged droidian) does not protect it. A BASE that is an earlier dev-sudo image loses the file unless the
# variable is set again. On a running phone: sudo rm /etc/sudoers.d/90-opos-dev.
rm -f "$r/etc/sudoers.d/90-opos-dev"
python3 -I -B "$here/image-policy.py" sudo "$r"
if [ "${OPOS_DEV_SUDO:-}" = 1 ] && [ -n "${OPOS_DEV_SSH_PUBKEY:-}" ]; then
    awk -F: '$1=="droidian"{f=1} END{exit !f}' "$r/etc/passwd" || { echo "base rootfs has no droidian user" >&2; exit 1; }
    sudo_tmp=$(mktemp)
    printf '%s\n' 'droidian ALL=(ALL) NOPASSWD:ALL' > "$sudo_tmp"
    chmod 0440 "$sudo_tmp"
    if command -v visudo >/dev/null 2>&1 && ! visudo -cf "$sudo_tmp" >/dev/null; then
        rm -f "$sudo_tmp"
        echo "build-image.sh: visudo rejects the dev sudoers line" >&2
        exit 1
    fi
    install -d -m 0755 "$r/etc/sudoers.d"
    install -m 0440 "$sudo_tmp" "$r/etc/sudoers.d/90-opos-dev"
    rm -f "$sudo_tmp"
    ( cd "$r" && sha256sum etc/sudoers.d/90-opos-dev )
    echo "DEV: passwordless sudo for droidian (/etc/sudoers.d/90-opos-dev, 0440 root:root); this image must never be carried or shared"
fi
# --- dev sudo: END

# --- usb hardening: BEGIN (always, no knob; packaging/duo-os/README.md, "Removed on purpose: the USB root shell and the password login")
# Two ways into the base that need no key, both removed from every image:
#  1. The port's USB gadget script (sfduo-usb-gadget.sh, run at every boot by sfduo-usb.service) ends by starting busybox telnetd with /bin/bash as its login program
#     on the USB link's address: a root shell with no authentication for anyone on the cable. Its one line is replaced by a comment; everything else in the script,
#     the trailing exit 0 and the file's mode and owner stay (the script is rewritten in place, not replaced).
#  2. The port's sshd drop-in 10-item-password-usb-only.conf lets a password in (droidian has one, a four-digit PIN) for every client whose source address is in the
#     USB link's /24, on ANY interface: Wi-Fi too, once one is joined. The drop-in becomes key-only. A Match block cannot be overridden from another file (its
#     keywords override the global section), so the port's file itself is rewritten, under its own name and with its own mode and owner.
# ssh with a key (OPOS_DEV_SSH_PUBKEY, and OPOS_DEV_SUDO for root) replaces both. The exposure block below separately binds adbd's debug port to loopback
# and closes other root telnet units; keep that hardening too.
ug=$r/usr/local/sbin/sfduo-usb-gadget.sh
[ -f "$ug" ] && [ ! -L "$ug" ] && [ -x "$ug" ] || { echo "base rootfs has no executable sfduo-usb-gadget.sh (the port's USB gadget script)" >&2; exit 1; }
ug_lines=$(wc -l < "$ug")
ug_tmp=$(mktemp)
awk '/^[ \t]*#/ { print; next } /telnetd/ { print "# OPOS: the root telnet shell that was started here, with no login, was removed by the OPOS image build (build-image.sh); ssh with a key is the way in."; next } { print }' "$ug" > "$ug_tmp"
if cmp -s "$ug_tmp" "$ug"; then
    echo "sfduo-usb-gadget.sh starts no telnet shell; leaving it as it is"
else
    cat "$ug_tmp" > "$ug"
    echo "USB: the root telnet shell in sfduo-usb-gadget.sh was removed (no login was asked on the USB link)"
fi
rm -f "$ug_tmp"
if grep -v '^[[:space:]]*#' "$ug" | grep -q telnetd || ! sh -n "$ug" || [ "$(wc -l < "$ug")" -ne "$ug_lines" ]; then
    echo "the sfduo-usb-gadget.sh edit did not take" >&2; exit 1
fi
sd=$r/etc/ssh/sshd_config.d
sf=$sd/10-item-password-usb-only.conf
if [ -e "$sf" ] || [ -L "$sf" ]; then
    [ -f "$sf" ] && [ ! -L "$sf" ] || { echo "base rootfs: $sf is not a plain file" >&2; exit 1; }
else
    install -d -m 0755 "$sd"
    install -m 0644 /dev/null "$sf"
fi
sf_tmp=$(mktemp)
printf '%s\n' '# OPOS image: ssh logs in with a key only, from every address. The port'"'"'s version of this file let a password in (a Match Address block with' \
    '# PasswordAuthentication yes) for any client whose source address is in the USB link'"'"'s /24, on any interface, Wi-Fi included once one is joined; the' \
    '# block was removed on purpose by build-image.sh. Recovery uses OPOS_OWNER_SSH_PUBKEY. A package upgrade of the port may put the original back;' \
    '# verify-image.sh reports that.' \
    'PasswordAuthentication no' 'KbdInteractiveAuthentication no' > "$sf_tmp"
cat "$sf_tmp" > "$sf"
rm -f "$sf_tmp"
if ! python3 -I -B "$here/image-policy.py" ssh "$r"; then
    echo "base rootfs: another sshd setting turns a password login on; the OPOS key-only edit is not enough" >&2; exit 1
fi
( cd "$r" && sha256sum usr/local/sbin/sfduo-usb-gadget.sh etc/ssh/sshd_config.d/10-item-password-usb-only.conf )
echo "USB: ssh is key-only (the password login for the USB link's subnet was removed from 10-item-password-usb-only.conf)"
# --- usb hardening: END

# ---- BEGIN OPOS OTA updates (self-contained; needs $r and $here; docs/ota-update.md) ----------------------------
# Release layout (/opt/opos -> /opt/opos-releases/0-<rev12>), the update state and inbox directories, the trust
# anchor (/etc/opos/update/allowed_signers, public keys only) and the root updater with its units. Over-the-air
# updates change the application tree and OPOS-owned overlay files; the updater and its keys change only with a new image.
ota_uid=$(awk -F: '$1=="droidian"{print $3}' "$r/etc/passwd")
ota_gid=$(awk -F: '$1=="droidian"{print $4}' "$r/etc/passwd")
[ -n "$ota_uid" ] && [ -n "$ota_gid" ] || { echo "base rootfs has no droidian user" >&2; exit 1; }
ota_rev=$(head -n 1 "$r/opt/opos/REVISION")
case $ota_rev in *[!0-9a-f]*) ota_rev="" ;; esac
[ "${#ota_rev}" -eq 40 ] || { echo "/opt/opos/REVISION is not a 40-hex git revision" >&2; exit 1; }
[ -d "$r/opt/opos" ] && [ ! -L "$r/opt/opos" ] || { echo "/opt/opos is not the installed OPOS tree" >&2; exit 1; }
ota_rel="0-$(printf '%s' "$ota_rev" | cut -c 1-12)"
rm -rf "$r/opt/opos-releases"
mkdir -p "$r/opt/opos-releases"
mv "$r/opt/opos" "$r/opt/opos-releases/$ota_rel"
ln -s "opos-releases/$ota_rel" "$r/opt/opos"
echo "release layout: /opt/opos -> opos-releases/$ota_rel"

install -d -m 0755 "$r/var/lib/opos-update"
install -d -m 0700 "$r/var/lib/opos-update/staging"
install -d -m 0755 "$r/var/lib/opos-update/inbox"
chown "0:$ota_gid" "$r/var/lib/opos-update/inbox"
chmod 2770 "$r/var/lib/opos-update/inbox"

install -d -m 0755 "$r/etc/opos" "$r/etc/opos/update"
printf '1\n' > "$r/etc/opos/base-version"
chmod 0644 "$r/etc/opos/base-version"
ota_sg="$r/etc/opos/update/allowed_signers"
: > "$ota_sg"
if [ -n "${OPOS_UPDATE_SIGNERS:-}" ]; then
    [ -f "$OPOS_UPDATE_SIGNERS" ] || { echo "OPOS_UPDATE_SIGNERS: $OPOS_UPDATE_SIGNERS is not a file" >&2; exit 1; }
    # Public ed25519 keys only: "ssh-ed25519 KEY [comment]" or "opos-release ssh-ed25519 KEY [comment]".
    # Anything else (a private key, another key type, a stray line) stops the build.
    awk '
        /^[ \t]*(#|$)/ { next }
        $1 == "ssh-ed25519" && $2 ~ /^[A-Za-z0-9+\/=]+$/ { print "opos-release ssh-ed25519 " $2; n++; next }
        $1 == "opos-release" && $2 == "ssh-ed25519" && $3 ~ /^[A-Za-z0-9+\/=]+$/ { print "opos-release ssh-ed25519 " $3; n++; next }
        { bad = 1; print "OPOS_UPDATE_SIGNERS: unsupported line (only ssh-ed25519 public keys): " substr($1, 1, 24) > "/dev/stderr" }
        END { if (bad || n < 1) exit 1 }
    ' "$OPOS_UPDATE_SIGNERS" > "$ota_sg.tmp" || { rm -f "$ota_sg.tmp"; echo "OPOS_UPDATE_SIGNERS: no usable public key" >&2; exit 1; }
    mv "$ota_sg.tmp" "$ota_sg"
fi
chmod 0644 "$ota_sg"
ota_n=$(grep -c . "$ota_sg" || true)
if [ "$ota_n" -gt 0 ]; then
    echo "update signers: $ota_n public key(s) installed in /etc/opos/update/allowed_signers"
else
    echo "update signers: NONE installed; this image accepts no updates (set OPOS_UPDATE_SIGNERS=<public keys file> to allow them)"
fi

# Optional update source for the phone's OPOS client (OPOS_UPDATE_URL=https://host/folder/latest.json). Without it the
# phone fetches nothing; the USB push (deploy/duo-push-update.ps1) needs no source. The client allows plain http only
# for this phone or a private network and tells the owner when an address cannot be used.
rm -f "$r/etc/opos/update.json"
if [ -n "${OPOS_UPDATE_URL:-}" ]; then
    case $OPOS_UPDATE_URL in https://* | http://*) ;; *) echo "OPOS_UPDATE_URL must start with https:// (or http:// for a private network)" >&2; exit 1 ;; esac
    case $OPOS_UPDATE_URL in *[!A-Za-z0-9._~:/?\&=%+@,-]*) echo "OPOS_UPDATE_URL has characters a web address does not need" >&2; exit 1 ;; esac
    ota_lan=false
    case $OPOS_UPDATE_URL in http://*) ota_lan=true ;; esac
    printf '{"url": "%s", "auto_check": false, "allow_lan_http": %s}\n' "$OPOS_UPDATE_URL" "$ota_lan" > "$r/etc/opos/update.json"
    chmod 0644 "$r/etc/opos/update.json"
    echo "update source: $OPOS_UPDATE_URL (checks only when asked)"
else
    echo "update source: none (the phone fetches nothing; push bundles over USB, or set OPOS_UPDATE_URL)"
fi

install -d -m 0755 "$r/usr/libexec/opos-updater"
install -m 0755 "$here/updater/opos-updater" "$r/usr/libexec/opos-updater/opos-updater"
install -d -m 0755 "$r/etc/systemd/system/multi-user.target.wants"
for u in opos-update.path opos-update.service opos-update-boot.service opos-update-confirm.service; do
    install -m 0644 "$here/updater/$u" "$r/etc/systemd/system/$u"
done
for u in opos-update.path opos-update-boot.service opos-update-confirm.service; do
    ln -sf "/etc/systemd/system/$u" "$r/etc/systemd/system/multi-user.target.wants/$u"
done
( cd "$r" && sha256sum usr/libexec/opos-updater/opos-updater etc/opos/base-version etc/opos/update/allowed_signers )
# ---- END OPOS OTA updates ----------------------------------------------------------------------------------------

# --- track: exposure: container debug port on loopback, no passwordless USB root shell ---
# No inherited development setting may preserve a root telnet shell.
OPOS_DEV_USB_SHELL=0 sh "$here/close-exposure.sh" "$r"
# --- end track: exposure ---
if [ "${OPOS_RELEASE:-0}" = 1 ]; then
    python3 -I -B "$here/image-policy.py" release "$r"
fi

# Both panels, one backlight value. Not a phone-page change.
install -m 0644 "$here/opos-brightness.service" "$r/etc/systemd/system/opos-brightness.service"
ln -sfn /etc/systemd/system/opos-brightness.service \
    "$r/etc/systemd/system/multi-user.target.wants/opos-brightness.service"

# --- power key: BEGIN (direct bl_power only; no composer or output-power calls)
install -d "$r/usr/libexec/opos" "$r/etc/systemd/system/opos.service.wants"
install -m 0755 "$here/opos-unblank" "$r/usr/libexec/opos/opos-unblank"
install -m 0644 "$here/opos-powerkey.service" "$r/etc/systemd/system/opos-powerkey.service"
ln -sfn /etc/systemd/system/opos-powerkey.service \
    "$r/etc/systemd/system/opos.service.wants/opos-powerkey.service"
# --- power key: END

# --- volume keys: BEGIN (simulated state only; no audio route)
install -d "$r/etc/systemd/system/opos.service.wants"
install -m 0644 "$here/opos-volumekey.service" "$r/etc/systemd/system/opos-volumekey.service"
ln -sfn /etc/systemd/system/opos-volumekey.service \
    "$r/etc/systemd/system/opos.service.wants/opos-volumekey.service"
# --- volume keys: END

# --- identity finish: BEGIN (after all OPOS installs and base edits)
python3 -I -B "$src/tools/opos-image-identity.py" finish --root "$r" --state "$identity_state"
# --- identity finish: END
sync
echo "manifest:"
set -- opt/opos/REVISION usr/libexec/opos usr/sbin/opos-switch usr/local/sbin/sfduo-composer-watchdog
if [ -e "$r/etc/default/opos-kiosk" ]; then set -- "$@" etc/default/opos-kiosk; fi
( cd "$r" && find "$@" etc/systemd/system/opos*.service etc/systemd/system/opos*.path -type f -exec sha256sum {} + )
umount "$mnt"
rmdir "$mnt"
mnt=''
rm -f "$identity_state" "$identity_state.metadata"
e2fsck -fn "$image" >/dev/null
echo "fsck clean"
if [ "${OPOS_RELEASE:-0}" = 1 ] || [ "${OPOS_VERIFY_IMAGE:-0}" = 1 ]; then
    if ! sh "$here/verify-image.sh" "$image"; then
        echo "build-image.sh: image verification failed; cleanup will retain any attached backing file" >&2
        exit 1
    fi
else
    echo "DEV IMAGE: verify-image.sh was NOT_RUN (set OPOS_VERIFY_IMAGE=1 to run it)"
fi
image_detached || exit 1
final_sha=$(python3 -I -B "$here/image-policy.py" image-sha256 "$image")
echo "publishing image (exclusive rename follows):"
printf '%s  %s\n' "$final_sha" "$out"
# Publication is the commit point. Reporting has succeeded; handled signals are
# ignored through the rename and cleanup, whose status preserves this command's.
trap '' INT TERM HUP
python3 -I -B "$here/image-policy.py" publish "$image" "$out"
