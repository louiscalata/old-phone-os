#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
SHA_TOOL=""
command -v sha256sum >/dev/null 2>&1 && SHA_TOOL=sha256sum || true
[ -n "$SHA_TOOL" ] || { command -v shasum >/dev/null 2>&1 && SHA_TOOL=shasum; } || true
sha() {
  case "$SHA_TOOL" in
    sha256sum) sha256sum "$@" ;;
    shasum) shasum -a 256 "$@" ;;
    *) echo "no sha256 tool" >&2; exit 2 ;;
  esac
}
ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$ROOT"
if [ -L "$ROOT/SHA256SUMS" ]; then echo "SHA256SUMS is a symlink" >&2; exit 1; fi
tmp_out="$(mktemp "$ROOT/SHA256SUMS.tmp.XXXXXX")"
cleanup_tmp() {
  rm -f "$tmp_out"
}
trap cleanup_tmp EXIT
: > "$tmp_out"
n=0
if [ -n "$(find src deploy -type l -print 2>/dev/null)" ]; then echo "symlink found" >&2; exit 1; fi
if [ -n "$(find src deploy \( -type p -o -type s -o -type b -o -type c \) -print 2>/dev/null)" ]; then echo "special file found" >&2; exit 1; fi
if [ -n "$(find src deploy \( -name '*.pyc' -o -name '__pycache__' \) -print 2>/dev/null)" ]; then echo "bytecode file found" >&2; exit 1; fi
while IFS= read -r -d '' f; do
  case "$f" in
    *$'\n'*) echo "bad filename" >&2; exit 1 ;;
  esac
  h="$(sha "$f")"
  h="${h%% *}"
  printf '%s  %s\n' "$h" "$f" >> "$tmp_out"
  n=$((n + 1))
done < <({ for d in src deploy; do [ -d "$d" ] || continue; find "$d" -type f -print0; done; if [ -f LICENSE ]; then printf '%s\0' LICENSE; fi; } | LC_ALL=C sort -z)
[ "$n" -gt 0 ] || { echo "no files listed" >&2; exit 1; }
mv -- "$tmp_out" "$ROOT/SHA256SUMS"
trap - EXIT
mh="$(sha "$ROOT/SHA256SUMS")"
mh="${mh%% *}"
echo "wrote SHA256SUMS: $n files"
echo "manifest sha256: $mh"
