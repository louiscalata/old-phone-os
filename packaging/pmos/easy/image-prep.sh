# SPDX-License-Identifier: GPL-3.0-or-later
# shellcheck shell=sh
set -eu
if [ "${1:-}" != "--check" ]; then
  exit 2
fi
if [ "$#" -lt 2 ] || [ ! -f "$2" ]; then
  printf 'no such image\n' >&2
  exit 2
fi
h="$(od -An -tx1 -N4 "$2" | tr -d ' \t\r\n')"
if [ "$h" = "3aff26ed" ]; then
  printf 'format: sparse\n'
else
  printf 'format: raw\n'
fi
