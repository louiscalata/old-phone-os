#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# shellcheck shell=sh
STATE="${OPOS_STATE_DIR:-/var/lib/opos}"
TDM="${OPOS_TDM:-tinydm-set-session}"
if test "$#" -gt 0; then
  if test "$#" -eq 1 && test "$1" = "--dry-run"; then
    if test -s "$STATE/prev-session"; then
      IFS= read -r PREV < "$STATE/prev-session"
      printf '[dry-run] opos-kiosk-off plan: would run tinydm-set-session -s %s (kiosk: OFF after reboot); nothing changed\n' "$PREV"
    else
      printf '%s\n' '[dry-run] opos-kiosk-off plan: no previous session recorded, would disable tinydm (kiosk: OFF after reboot); nothing changed'
    fi
    exit 0
  fi
  printf '%s\n' 'usage: opos-kiosk-off [--dry-run]' >&2
  exit 2
fi
if [ ! -s "$STATE/prev-session" ]; then
  if command -v rc-update >/dev/null 2>&1; then
    services=$(rc-update show default) || exit $?
    while read -r service rest; do
      if [ "$service" = tinydm ]; then
        rc-update del tinydm default || exit $?
        break
      fi
    done <<EOF
$services
EOF
  elif command -v systemctl >/dev/null 2>&1; then
    systemctl disable tinydm.service || exit $?
  else
    printf '%s\n' 'cannot disable tinydm: no supported service manager found' >&2
    exit 1
  fi
else
  IFS= read -r PREV < "$STATE/prev-session"
  "$TDM" -s "$PREV" || exit $?
fi
printf '%s\n' 'kiosk: OFF (takes effect after reboot)'
exit 0
