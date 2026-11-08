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

sync
echo "manifest:"
( cd "$r" && find opt/opos/REVISION usr/libexec/opos usr/sbin/opos-switch etc/systemd/system/opos*.service -type f -exec sha256sum {} + )
umount "$mnt"
trap - EXIT
rmdir "$mnt"
e2fsck -fn "$out" >/dev/null && echo "fsck clean"
sha256sum "$out"
