#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Bind the container debug port to loopback, drop new inbound packets on wlan0,
# and close a passwordless USB root shell unless OPOS_DEV_USB_SHELL=1.
# Usage: sh close-exposure.sh ROOT
# ROOT is a rootfs directory (the image mount at build time, or / on the phone).
set -eu
root=${1:-}
[ -n "$root" ] && [ -d "$root" ] || { echo "usage: close-exposure.sh ROOT" >&2; exit 2; }
case $root in
    */) root=${root%/} ;;
esac

install -d -m 0755 "$root/etc/opos" "$root/usr/libexec/opos" \
    "$root/etc/systemd/system" "$root/etc/systemd/system/multi-user.target.wants"

printf '%s\n' 'listen=127.0.0.1' 'port=5555' > "$root/etc/opos/debug-port.conf"
chmod 0644 "$root/etc/opos/debug-port.conf"

cat > "$root/etc/opos/wlan0-inbound.nft" <<'EOF'
table inet opos_wlan {
    chain input {
        type filter hook input priority 0; policy accept;
        iifname "wlan0" ct state established,related accept
        iifname "wlan0" drop
    }
}
EOF
chmod 0644 "$root/etc/opos/wlan0-inbound.nft"

# Copy this script into the image. A running copy can overwrite itself; install via a temp file.
script_dest=$root/usr/libexec/opos/opos-debug-port
tmp_script=$(mktemp)
cat "$0" > "$tmp_script"
install -m 0755 "$tmp_script" "$script_dest"
rm -f "$tmp_script"

cat > "$root/etc/systemd/system/opos-debug-port.service" <<'EOF'
[Unit]
Description=Bind the container debug port to loopback
DefaultDependencies=no
After=local-fs.target
Before=network-pre.target

[Service]
Type=oneshot
ExecStart=/usr/libexec/opos/opos-debug-port /
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "$root/etc/systemd/system/opos-debug-port.service"
ln -sfn /etc/systemd/system/opos-debug-port.service \
    "$root/etc/systemd/system/multi-user.target.wants/opos-debug-port.service"

rewrite_listen() {
    file=$1
    [ -f "$file" ] || return 0
    tmp=$(mktemp)
    awk '
        {
            gsub(/0\.0\.0\.0:5555/, "127.0.0.1:5555")
            gsub(/\*:5555/, "127.0.0.1:5555")
            gsub(/\[::\]:5555/, "127.0.0.1:5555")
            gsub(/ListenStream=5555$/, "ListenStream=127.0.0.1:5555")
        }
        /adbd/ { gsub(/ -a( |$)/, " ") }
        { print }
    ' "$file" > "$tmp"
    cat "$tmp" > "$file"
    rm -f "$tmp"
}

for file in \
    "$root/etc/systemd/system/adbd.service" \
    "$root/lib/systemd/system/adbd.service" \
    "$root/usr/lib/systemd/system/adbd.service" \
    "$root/etc/init/adbd.conf" \
    "$root/var/lib/lxc/android/default.prop" \
    "$root/var/lib/lxc/android/rootfs/default.prop"
do
    rewrite_listen "$file"
done

is_root_shell() {
    file=$1
    grep -E -q 'telnetd' "$file" || return 1
    grep -E -q '/bin/(sh|bash|dash)|sulogin' "$file"
}

close_unit() {
    base=$(basename "$1")
    ln -sfn /dev/null "$root/etc/systemd/system/$base"
    find "$root/etc/systemd/system" -path '*.wants/*' -name "$base" -exec rm -f {} +
}

dev_shell=0
[ "${OPOS_DEV_USB_SHELL:-0}" = 1 ] && dev_shell=1

for file in \
    "$root"/etc/systemd/system/*telnet* \
    "$root"/lib/systemd/system/*telnet* \
    "$root"/usr/lib/systemd/system/*telnet* \
    "$root"/etc/xinetd.d/*
do
    [ -f "$file" ] || continue
    is_root_shell "$file" || continue
    if [ "$dev_shell" -eq 0 ]; then
        close_unit "$file"
    fi
done

inetd=$root/etc/inetd.conf
if [ -f "$inetd" ] && [ "$dev_shell" -eq 0 ]; then
    tmp=$(mktemp)
    awk '
        /^[[:space:]]*#/ { print; next }
        /telnet/ && /telnetd/ && /\/bin\/(sh|bash|dash)|sulogin/ { print "# opos-closed " $0; next }
        { print }
    ' "$inetd" > "$tmp"
    cat "$tmp" > "$inetd"
    rm -f "$tmp"
fi

if [ "$dev_shell" -eq 1 ]; then
    printf '%s\n' 'enabled=1' 'gate=dev' > "$root/etc/opos/usb-shell.conf"
else
    printf '%s\n' 'enabled=0' > "$root/etc/opos/usb-shell.conf"
fi
chmod 0644 "$root/etc/opos/usb-shell.conf"
