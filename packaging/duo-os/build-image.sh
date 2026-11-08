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
[ "$(readlink -f "$base")" != "$(readlink -f "$out")" ] || { echo "OUT must differ from BASE" >&2; exit 2; }
mnt=$(mktemp -d)
cleanup() { umount "$mnt" 2>/dev/null || true; rmdir "$mnt" 2>/dev/null || true; }
trap cleanup EXIT

echo "copying $base -> $out"
cp --sparse=always "$base" "$out"
e2fsck -fy "$out" >/dev/null 2>&1 || true
mount -o loop "$out" "$mnt"
r=$mnt
[ -f "$r/usr/bin/phosh-session" ] && [ -x "$r/usr/bin/chromium" ] && [ -x "$r/usr/bin/python3" ] \
    || { echo "base rootfs lacks phosh-session, chromium or python3" >&2; exit 1; }

g() { git -c safe.directory="$src" -C "$src" "$@"; }
echo "installing OPOS $(g rev-parse --short "$ref") into /opt/opos"
rm -rf "$r/opt/opos"
mkdir -p "$r/opt/opos"
g archive "$ref" src LICENSE THIRD_PARTY.md | tar -x -C "$r/opt/opos"
g rev-parse "$ref" > "$r/opt/opos/REVISION"

install -d "$r/usr/libexec/opos"
install -m 0755 "$here/opos-kiosk" "$r/usr/libexec/opos/opos-kiosk"
install -m 0755 "$here/opos-session" "$r/usr/libexec/opos/opos-session"
install -m 0755 "$here/opos-switch" "$r/usr/sbin/opos-switch"
for u in opos-server.service opos.service opos-fallback.service; do
    install -m 0644 "$here/$u" "$r/etc/systemd/system/$u"
done

# OPOS is the shell from the first boot: phosh's symlink goes, OPOS's two are added.
rm -f "$r/etc/systemd/system/graphical.target.wants/phosh.service" "$r/etc/systemd/system/graphical.target.wants/item.service"
ln -sf /etc/systemd/system/opos.service "$r/etc/systemd/system/graphical.target.wants/opos.service"
ln -sf /etc/systemd/system/opos-server.service "$r/etc/systemd/system/multi-user.target.wants/opos-server.service"

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
if [ -n "${OPOS_DEV_SSH_PUBKEY:-}" ]; then
    ln -sf /etc/systemd/system/opos-clock-set.path "$r/etc/systemd/system/multi-user.target.wants/opos-clock-set.path"
    echo "DEV IMAGE: Unix seconds dropped in ~/opos-set-time over ssh set the clock (opos-clock-set.path)"
fi
echo "clock floor $floor ($(date -u -d "@$floor" +%FT%TZ 2>/dev/null || echo '?'))"
# --- clock: END

# DEV ONLY. OPOS_DEV_SSH_PUBKEY=<file.pub> lets the computer that holds the matching private key log in as
# droidian over the USB network (the base's sshd already refuses passwords there; keys work). Leave it unset
# for an image that is meant to be carried: without it nothing new can log in.
if [ -n "${OPOS_DEV_SSH_PUBKEY:-}" ]; then
    uid=$(awk -F: '$1=="droidian"{print $3}' "$r/etc/passwd")
    gid=$(awk -F: '$1=="droidian"{print $4}' "$r/etc/passwd")
    home=$(awk -F: '$1=="droidian"{print $6}' "$r/etc/passwd")
    [ -n "$uid" ] && [ -n "$home" ] || { echo "base rootfs has no droidian user" >&2; exit 1; }
    [ -d "$r$home" ] || install -d -m 0755 -o "$uid" -g "$gid" "$r$home"
    install -d -m 0700 -o "$uid" -g "$gid" "$r$home/.ssh"
    cat "$OPOS_DEV_SSH_PUBKEY" >> "$r$home/.ssh/authorized_keys"
    chown "$uid:$gid" "$r$home/.ssh/authorized_keys"
    chmod 0600 "$r$home/.ssh/authorized_keys"
    echo "DEV IMAGE: droidian accepts the ssh key '$(cut -d' ' -f3- "$OPOS_DEV_SSH_PUBKEY")' (remove with: rm ~/.ssh/authorized_keys)"
fi

sync
echo "manifest:"
( cd "$r" && find opt/opos/REVISION usr/libexec/opos usr/sbin/opos-switch etc/systemd/system/opos*.service etc/systemd/system/opos*.path -type f -exec sha256sum {} + )
umount "$mnt"
trap - EXIT
rmdir "$mnt"
e2fsck -fn "$out" >/dev/null && echo "fsck clean"
sha256sum "$out"
