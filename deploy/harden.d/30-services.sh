#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
step_services() {
  for unit in $OPOS_DISABLE_UNITS; do
    case "$unit" in
      ""|*[!A-Za-z0-9@._-]*) die "bad unit name" ;;
    esac
    if have_cmd systemctl && systemctl list-unit-files -- "$unit" --no-legend | grep -q .; then
      if systemctl is-enabled --quiet -- "$unit"; then
        en="enabled"
      else
        en="disabled"
      fi
      if systemctl is-active --quiet -- "$unit"; then
        ac="active"
      else
        ac="inactive"
      fi
      if [ "$APPLY" = "1" ]; then
        if [ "$en" = "enabled" ]; then
          printf '%s %s\n' "$en" "$unit" >> "$MANIFEST"
          if [ "$ac" = "active" ]; then
            printf '%s %s\n' "$ac" "$unit" >> "$MANIFEST"
          fi
        elif [ "$ac" = "active" ]; then
          printf '%s %s\n' "$ac" "$unit" >> "$MANIFEST"
        fi
      fi
      if [ "$en" = "enabled" ]; then
        run systemctl disable --now -- "$unit"
      elif [ "$ac" = "active" ]; then
        run systemctl stop -- "$unit"
        say "services: $unit stopped (was running but disabled)"
      else
        say "services: $unit already off"
      fi
    else
      say "services: $unit not present or already off"
    fi
  done
  say "services: unused services disabled"
}
