# SPDX-License-Identifier: GPL-3.0-or-later
# shellcheck shell=sh
STATE="${OPOS_STATE_DIR:-/var/lib/opos}"
TDM="${OPOS_TDM:-tinydm-set-session}"
SESSION="${OPOS_SESSION:-/usr/share/wayland-sessions/cage-ui.desktop}"
if [ -e "$STATE/firstboot-done" ]; then
 printf '%s\n' 'first boot: already done'
 exit 0
fi
mkdir -p "$STATE" || exit 1
"$TDM" -s "$SESSION" || exit 1
: > "$STATE/firstboot-done" || exit 1
printf '%s\n' 'kiosk: ON'
exit 0
