# SPDX-License-Identifier: GPL-3.0-or-later
# shellcheck shell=sh
set -eu
if [ "${1:-}" != "--check" ]; then
  exit 2
fi
if [ "$#" -ne 2 ] || [ ! -f "$2" ]; then
  printf 'no such image\n' >&2
  exit 2
fi
if [ ! -r "$2" ] || [ ! -s "$2" ]; then
  printf 'image must be readable and non-empty\n' >&2
  exit 2
fi
header=$(od -An -tx1 -N4 "$2") || { printf 'cannot read image\n' >&2; exit 2; }
h=$(printf '%s' "$header" | tr -d ' \t\r\n')
if [ "$h" = "3aff26ed" ]; then
  printf 'format: sparse\n'
else
  printf 'format: raw\n'
fi
