#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
sha() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$@"
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$@"
  else
    echo "no sha256 tool" >&2
    exit 2
  fi
}
usage() {
  echo "usage: $(basename "$0") [--expect-manifest-sha HEX] [ROOT]" >&2
}
fail() {
  echo "FAIL: $1" >&2
  exit 1
}
expect=""
have_expect=0
ROOT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --expect-manifest-sha)
      [ $# -ge 2 ] || { usage; exit 2; }
      expect="$2"
      have_expect=1
      shift 2
      ;;
    -*)
      usage
      exit 2
      ;;
    *)
      [ -z "$ROOT" ] || { usage; exit 2; }
      ROOT="$1"
      shift
      ;;
  esac
done
if [ "$have_expect" -eq 1 ]; then
  case "$expect" in
    *[!0-9a-f]*) fail "bad expected sha" ;;
  esac
  [ "${#expect}" -eq 64 ] || fail "bad expected sha"
fi
[ -n "$ROOT" ] || ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f "$ROOT/SHA256SUMS" ] || fail "missing SHA256SUMS"
if [ "$have_expect" -eq 1 ]; then
  actual="$(sha "$ROOT/SHA256SUMS")"
  actual="${actual%% *}"
  [ "$actual" = "$expect" ] || fail "manifest sha256 mismatch"
fi
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
manifest_paths="$tmpdir/manifest_paths"
found_paths="$tmpdir/found_paths"
: > "$manifest_paths"
: > "$found_paths"
n=0
while IFS= read -r line || [ -n "$line" ]; do
  [ -n "$line" ] || fail "bad manifest line"
  h="${line%% *}"
  p="${line#* }"
  p="${p# }"
  [ -n "$h" ] || fail "bad manifest line"
  [ -n "$p" ] || fail "bad manifest line"
  case "$h" in
    *[!0-9a-f]*) fail "bad manifest line: $p" ;;
  esac
  [ "${#h}" -eq 64 ] || fail "bad manifest line: $p"
  case "$p" in
    /*) fail "absolute path: $p" ;;
  esac
  case "$p" in
    *..*) fail "dotdot path: $p" ;;
  esac
  case "$p" in
    *__pycache__*|*.pyc|SHA256SUMS|*/SHA256SUMS) fail "unexpected entry: $p" ;;
  esac
  case "$p" in
    LICENSE|src/*|deploy/*) ;;
    *) fail "unexpected entry: $p" ;;
  esac
  if grep -Fxq -- "$p" "$manifest_paths"; then
    fail "duplicate entry: $p"
  fi
  printf '%s\n' "$p" >> "$manifest_paths"
  [ -f "$p" ] || fail "missing file: $p"
  [ ! -L "$p" ] || fail "symlink listed: $p"
  a="$(sha "$p")"
  a="${a%% *}"
  [ "$a" = "$h" ] || fail "hash mismatch: $p"
  n=$((n + 1))
done < "$ROOT/SHA256SUMS"
[ "$n" -gt 0 ] || fail "empty manifest"
{
  for d in src deploy; do
    [ -d "$d" ] || continue
    find "$d" -type f -print
  done
  if [ -f LICENSE ]; then
    printf '%s\n' LICENSE
  fi
if [ -n "$(find src deploy \( -name '*.pyc' -o -name '__pycache__' \) -print 2>/dev/null)" ]; then
  fail "bytecode file found"
fi
} | while IFS= read -r f || [ -n "$f" ]; do
  printf '%s\n' "$f"
done | LC_ALL=C sort > "$found_paths"
LC_ALL=C sort "$manifest_paths" -o "$manifest_paths"
extra="$(comm -13 "$manifest_paths" "$found_paths")"
[ -z "$extra" ] || fail "unlisted file: $extra"
while IFS= read -r -d '' s; do
  fail "symlink found: $s"
done < <({ for d in src deploy; do [ -d "$d" ] || continue; find "$d" -type l -print0; done; } | LC_ALL=C sort -z)
while IFS= read -r -d '' s; do
  fail "special file found: $s"
done < <({ for d in src deploy; do [ -d "$d" ] || continue; find "$d" \( -type p -o -type s -o -type b -o -type c \) -print0; done; } | LC_ALL=C sort -z)
echo "ok: $n files verified"
