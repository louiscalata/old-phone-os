#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu
DRY=0
NOLAUNCH=0
YES=0
UNINST=0
KEEP=0
FORCE=0
HOME_DATA="${XDG_DATA_HOME:-$HOME/.local/share}/opos"
OPOS_HOME="${OPOS_HOME:-$HOME_DATA}"
OPOS_BIN_DIR="${OPOS_BIN_DIR:-$HOME/.local/bin}"
usage() {
  printf 'Usage: install.sh [--dry-run] [--no-launch] [--kiosk] [--yes|-y] [--uninstall [--keep-config] [--force|-f]] [--help|-h]\n'
}
fail() {
  printf '%s\n' "$1" >&2
  exit "${2:-1}"
}
say() {
  if [ "$DRY" -eq 1 ]; then
    printf '==> would %s\n' "$1"
  else
    printf '==> %s\n' "$1"
  fi
}
sq() {
  case "$1" in
    *\'*)
      printf 'path contains single quote: %s\n' "$1" >&2
      return 1
      ;;
    *)
      printf "'%s'" "$1"
      ;;
  esac
}
opos_owned_path() {
  [ "$(canon_file "$1")" = "$(canon_file "$2")" ]
}
canon_repo() {
  _cr="$1"
  while [ "$_cr" != "/" ] && [ "${_cr%/}" != "$_cr" ]; do
    _cr="${_cr%/}"
  done
  if [ -d "$_cr" ] && _cr_resolved=$(CDPATH='' cd -- "$_cr" && pwd -P 2>/dev/null); then
    printf '%s' "$_cr_resolved"
  else
    printf '%s' "$_cr"
  fi
}
canon_file() {
  _cf="$1"
  _cf_dir=$(dirname -- "$_cf")
  _cf_base=$(basename -- "$_cf")
  if _cf_dir_resolved=$(CDPATH='' cd -- "$_cf_dir" && pwd -P 2>/dev/null); then
    _cf_path="$_cf_dir_resolved/$_cf_base"
    if command -v readlink >/dev/null 2>&1; then
      _cf_count=0
      while [ -L "$_cf_path" ] && [ "$_cf_count" -lt 20 ]; do
        _cf_count=$((_cf_count + 1))
        _cf_link=$(readlink "$_cf_path" 2>/dev/null) || break
        case "$_cf_link" in
          /*)
            _cf_path="$_cf_link"
            ;;
          *)
            _cf_path="$(dirname -- "$_cf_path")/$_cf_link"
            ;;
        esac
      done
      _cf_dir=$(dirname -- "$_cf_path")
      _cf_base=$(basename -- "$_cf_path")
      if _cf_dir_resolved=$(CDPATH='' cd -- "$_cf_dir" && pwd -P 2>/dev/null); then
        _cf_path="$_cf_dir_resolved/$_cf_base"
      fi
    fi
    printf '%s' "$_cf_path"
  else
    printf '%s' "$_cf"
  fi
}
py_ok() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}
find_python() {
  if [ -n "${OPOS_PYTHON:-}" ]; then
    set -- "$OPOS_PYTHON"
  else
    set -- python3.13 python3.12 python3.11 python3 python
  fi
  for c in "$@"; do
    case "$c" in
      */*)
        [ -x "$c" ] || continue
        ;;
      *)
        command -v "$c" >/dev/null 2>&1 || continue
        ;;
    esac
    if py_ok "$c"; then
      if _resolved=$(command -v "$c" 2>/dev/null) && [ -n "$_resolved" ]; then
        printf '%s' "$_resolved"
      else
        printf '%s' "$c"
      fi
      return 0
    fi
  done
  return 1
}
hint() {
  u=$(uname -s 2>/dev/null || printf '')
  case "${PREFIX:-}" in
    *com.termux*)
      printf 'pkg install python\n'
      return
      ;;
  esac
  case "$u" in
    Darwin)
      printf 'brew install python\n'
      return
      ;;
  esac
  if [ "$u" = "Linux" ] && command -v apt-get >/dev/null 2>&1; then
    printf 'sudo apt install python3 python3-venv\n'
    return
  fi
  printf 'download Python 3.11+ from https://www.python.org/downloads/\n'
}
check_paths() {
  for _p in "$ROOT" "$OPOS_HOME" "$OPOS_BIN_DIR"; do
    case "$_p" in
      *' '*|*[[:space:]]*|*%*)
        printf "path must not contain spaces or '%%' (the user service unit cannot quote them): %s\n" "$_p" >&2
        exit 1
        ;;
    esac
  done
}
parse_args() {
  KIOSK=0
  for a in "$@"; do
    case "$a" in
      --dry-run)
        DRY=1
        ;;
      --no-launch)
        NOLAUNCH=1
        ;;
      --kiosk)
        KIOSK=1
        ;;
      --yes|-y)
        YES=1
        ;;
      --uninstall)
        UNINST=1
        ;;
      --keep-config)
        KEEP=1
        ;;
      --force)
        FORCE=1
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      --*)
        usage >&2
        exit 2
        ;;
      -*)
        case "$a" in
          -y|-h|-f)
            ;;
          *)
            usage >&2
            exit 2
            ;;
        esac
        case "$a" in
          -y)
            YES=1
            ;;
          -f)
            FORCE=1
            ;;
          -h)
            usage
            exit 0
            ;;
        esac
        ;;
      *)
        usage >&2
        exit 2
        ;;
    esac
  done
  _kiosk_dir="$HOME/.config/autostart"
  _kiosk_target="$_kiosk_dir/opos-kiosk.desktop"
  if [ "$UNINST" -eq 1 ]; then
    if [ -f "$_kiosk_target" ] && grep -qx "X-OPOS-Kiosk=true" "$_kiosk_target"; then
      say "remove kiosk autostart $_kiosk_target"
      if [ "$DRY" -eq 0 ]; then
        rm -f "$_kiosk_target"
      fi
    fi
    return 0
  fi
  if [ "$KIOSK" -eq 0 ]; then
    return 0
  fi
  _kiosk_browser="${OPOS_KIOSK_BROWSER:-chromium}"
  if ! command -v "$_kiosk_browser" >/dev/null 2>&1; then
    printf 'warning: kiosk browser not found: %s; autostart not written\n' "$_kiosk_browser" >&2
    return 0
  fi
  say "write kiosk autostart $_kiosk_target"
  if [ "$DRY" -eq 1 ]; then
    return 0
  fi
  mkdir -p "$_kiosk_dir"
  printf '[Desktop Entry]\nType=Application\nName=OPOS\nExec=%s --kiosk http://127.0.0.1:8765/\nX-OPOS-Kiosk=true\n' "$_kiosk_browser" > "$_kiosk_target"
  return 0
}
ensure_venv() {
  if [ -x "$VENV/bin/python" ] && py_ok "$VENV/bin/python"; then
    say "reuse the existing venv"
  else
    say "create venv at $VENV"
    if [ "$DRY" -eq 0 ]; then
      (
        umask 077
        mkdir -p "$OPOS_HOME"
      )
      _staging_root="$(mktemp -d "$OPOS_HOME/.venv-staging.XXXXXX")" || fail "cannot create staging directory"
      _staging_venv="$_staging_root/venv"
      if "$py" -m venv "$_staging_venv" 2>/dev/null; then
        :
      else
        rm -rf "$_staging_venv"
        if "$py" -m venv --without-pip "$_staging_venv" 2>/dev/null; then
          say "venv created without pip (ensurepip missing; OPOS has no dependencies)"
        elif [ "$(uname -s)" = "Linux" ] && command -v apt-get >/dev/null 2>&1 && [ -t 0 ] && [ "$YES" -eq 0 ]; then
          printf '==> sudo may ask for your password; to skip, run: sudo apt install python3-venv, then re-run install.sh\n'
          printf 'Install python3-venv with sudo apt? [y/N] '
          IFS= read -r ans || ans=""
          case "$ans" in
            [Yy]*)
              sudo apt-get install -y python3-venv
              if "$py" -m venv "$_staging_venv"; then
                :
              else
                rm -rf "$_staging_root"
                printf 'sudo apt install python3-venv\n' >&2
                exit 1
              fi
              ;;
            *)
              rm -rf "$_staging_root"
              printf 'sudo apt install python3-venv\n' >&2
              exit 1
              ;;
          esac
        else
          rm -rf "$_staging_root"
          printf 'sudo apt install python3-venv\n' >&2
          exit 1
        fi
      fi
      rm -rf "$VENV"
      if [ -e "$VENV" ]; then
        rm -rf "$_staging_root"
        fail "cannot remove existing venv at $VENV"
      fi
      if ! mv "$_staging_venv" "$VENV"; then
        rm -rf "$_staging_root"
        fail "cannot move staged venv to $VENV"
      fi
      rmdir "$_staging_root" 2>/dev/null || rm -rf "$_staging_root"
    fi
  fi
}
install_requirements() {
  REQ="$ROOT/requirements.txt"
  NEED=0
  HASHLESS=0
  if [ -f "$REQ" ]; then
    NEED=$(grep -v -e '^[[:space:]]*$' -e '^[[:space:]]*#' "$REQ" | head -n 1 | wc -l)
    if [ "$NEED" -gt 0 ]; then
      if grep -v -e '^[[:space:]]*$' -e '^[[:space:]]*#' "$REQ" | grep -q -v -e '--hash'; then
        HASHLESS=1
      else
        HASHLESS=0
      fi
    fi
  fi
  if [ "$NEED" -eq 0 ]; then
    say "skip pip: no third-party packages needed"
  else
    if [ "$HASHLESS" -eq 0 ]; then
      say "install requirements (hashed)"
    else
      say "install requirements"
    fi
    if [ "$DRY" -eq 0 ]; then
      if [ "$HASHLESS" -eq 0 ]; then
        "$VENV/bin/python" -m pip install --disable-pip-version-check --require-hashes -r "$REQ"
      else
        "$VENV/bin/python" -m pip install --disable-pip-version-check -r "$REQ"
      fi
    fi
  fi
}
run_selftest() {
  say "run self-test"
  if [ "$DRY" -eq 0 ]; then
    PYTHONPATH="$ROOT/src" "$VENV/bin/python" -m opos.setup.selftest || fail "self-test failed, nothing was installed"
  fi
}
write_launcher() {
  say "write launcher $DEF_LAUNCHER"
  if [ "$DRY" -eq 0 ]; then
    RQ=$(sq "$ROOT") || fail "cannot quote repo path safely"
    VQ=$(sq "$VENV") || fail "cannot quote venv path safely"
    (
      umask 022
      mkdir -p "$OPOS_BIN_DIR"
    )
    printf '#!/bin/sh\nexport PYTHONPATH=%s/src\nexec %s/bin/python -m opos.setup "$@"\n' "$RQ" "$VQ" > "$TMP_LAUNCHER"
    chmod 0755 "$TMP_LAUNCHER"
    mv "$TMP_LAUNCHER" "$OPOS_BIN_DIR/opos"
  fi
}
write_manifest() {
  say "write manifest $MANIFEST"
  if [ "$DRY" -eq 0 ]; then
    (
      umask 077
      mkdir -p "$OPOS_HOME"
      printf 'launcher=%s\nvenv=%s\nrepo=%s\n' "$OPOS_BIN_DIR/opos" "$VENV" "$ROOT" > "$TMP_MANIFEST"
      chmod 0600 "$TMP_MANIFEST"
      mv "$TMP_MANIFEST" "$MANIFEST"
    )
  fi
}
remove_path() {
  p="$1"
  kind="$2"
  [ -n "$p" ] || return 0
  [ "$p" != "/" ] || return 0
  [ "$p" != "$HOME" ] || return 0
  bn=$(basename "$p")
  case "$bn" in
    opos)
      ;;
    venv)
      parent_bn=$(basename "$(dirname "$p")")
      [ "$parent_bn" = "opos" ] || return 0
      ;;
    manifest)
      parent_bn=$(basename "$(dirname "$p")")
      [ "$parent_bn" = "opos" ] || return 0
      ;;
    *)
      return 0
      ;;
  esac
  if [ "$kind" = "d" ]; then
    [ -d "$p" ] || return 0
  else
    [ -e "$p" ] || return 0
  fi
  FOUND=1
  if [ "$DRY" -eq 1 ]; then
    printf '==> would remove %s\n' "$p"
    return 0
  fi
  printf '==> removed %s\n' "$p"
  if [ "$kind" = "d" ]; then
    rm -rf "$p"
  else
    rm -f "$p"
  fi
}
is_hardening_active() {
  _hard_root="${OPOS_ROOT:-}"
  if [ -n "$_hard_root" ]; then
    _hard_backups="$_hard_root/var/backups/opos-harden"
    _hard_ssh="$_hard_root/etc/ssh/sshd_config.d/10-opos-hardening.conf"
  else
    _hard_backups="/var/backups/opos-harden"
    _hard_ssh="/etc/ssh/sshd_config.d/10-opos-hardening.conf"
  fi
  if [ -e "$_hard_backups" ]; then
    return 0
  fi
  if [ -e "$_hard_ssh" ]; then
    return 0
  fi
  if command -v nft >/dev/null 2>&1; then
    if nft list tables 2>/dev/null | grep -q "inet opos_filter"; then
      return 0
    fi
  fi
  if [ -n "${_hard_root:-}" ]; then
    if [ -e "$_hard_root/var/lib/nft-opos-filter" ]; then
      return 0
    fi
  fi
  return 1
}
retain_hardening_rollback() {
  _retain_clone="$1"
  [ -n "$_retain_clone" ] || return 1
  case "$OPOS_HOME" in
    ""|"/")
      return 1
      ;;
    /*)
      ;;
    *)
      return 1
      ;;
  esac
  _retain_src="$_retain_clone/deploy/harden-device.sh"
  _retain_dst="$OPOS_HOME/opos-harden-rollback.sh"
  case "$_retain_dst" in
    "$_retain_clone"/*)
      return 1
      ;;
  esac
  if [ ! -f "$_retain_src" ]; then
    return 1
  fi
  if [ "$DRY" -eq 1 ]; then
    printf '==> would retain hardening rollback at %s\n' "$_retain_dst"
    return 0
  fi
  if [ -e "$_retain_dst" ] || [ -L "$_retain_dst" ]; then
    return 1
  fi
  if [ -L "$_retain_src" ]; then
    return 1
  fi
  (
    umask 077
    mkdir -p "$OPOS_HOME"
  ) || return 1
  if [ -e "$_retain_dst" ] || [ -L "$_retain_dst" ]; then
    return 1
  fi
  if ! opos_owned_path "$_retain_dst" "$OPOS_HOME/opos-harden-rollback.sh"; then
    return 1
  fi
  _retain_py=""
  if command -v find_python >/dev/null 2>&1; then
    if ! _retain_py="$(find_python 2>/dev/null)"; then
      return 1
    fi
    [ -n "$_retain_py" ] || return 1
  else
    for _retain_c in python3.13 python3.12 python3.11 python3 python; do
      if command -v "$_retain_c" >/dev/null 2>&1; then
        if "$_retain_c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
          _retain_py="$(command -v "$_retain_c")"
          break
        fi
      fi
    done
    [ -n "$_retain_py" ] || return 1
  fi
  _retain_tmp=""
  _retain_tmp="$(mktemp "$OPOS_HOME/.opos-harden-rollback.XXXXXX")" || return 1
  if ! "$_retain_py" -c 'import os,sys; fd=os.open(sys.argv[1], os.O_RDONLY|os.O_NOFOLLOW); os.close(fd)' "$_retain_tmp"; then
    rm -f "$_retain_tmp"
    return 1
  fi
  _retain_tmp_id=""
  if ! _retain_tmp_id="$("$_retain_py" -c 'import os,sys; s=os.lstat(sys.argv[1]); print("%s:%s" % (s.st_dev, s.st_ino))' "$_retain_tmp")"; then
    rm -f "$_retain_tmp"
    return 1
  fi
  [ -n "$_retain_tmp_id" ] || {
    rm -f "$_retain_tmp"
    return 1
  }
  _retain_check_tmp() {
    [ ! -L "$_retain_tmp" ] || return 1
    [ -f "$_retain_tmp" ] || return 1
    _retain_cur="$("$_retain_py" -c 'import os,sys; s=os.lstat(sys.argv[1]); print("%s:%s" % (s.st_dev, s.st_ino))' "$_retain_tmp")" || return 1
    [ "$_retain_cur" = "$_retain_tmp_id" ] || return 1
    "$_retain_py" -c 'import os,sys; fd=os.open(sys.argv[1], os.O_RDONLY|os.O_NOFOLLOW); os.close(fd)' "$_retain_tmp" || return 1
  }
  if ! _retain_check_tmp; then
    rm -f "$_retain_tmp"
    return 1
  fi
  if ! cp -p "$_retain_src" "$_retain_tmp"; then
    rm -f "$_retain_tmp"
    return 1
  fi
  if ! _retain_check_tmp; then
    rm -f "$_retain_tmp"
    return 1
  fi
  if ! chmod 0700 "$_retain_tmp"; then
    rm -f "$_retain_tmp"
    return 1
  fi
  if ! "$_retain_py" -c 'import os,sys; fd=os.open(sys.argv[1], os.O_RDONLY|os.O_NOFOLLOW); os.fchmod(fd, 0o700); os.close(fd)' "$_retain_tmp"; then
    rm -f "$_retain_tmp"
    return 1
  fi
  if ! _retain_check_tmp; then
    rm -f "$_retain_tmp"
    return 1
  fi
  if [ -e "$_retain_dst" ] || [ -L "$_retain_dst" ]; then
    rm -f "$_retain_tmp"
    return 1
  fi
  if ! "$_retain_py" -c 'import os, sys; os.link(sys.argv[1], sys.argv[2])' "$_retain_tmp" "$_retain_dst"; then
    rm -f "$_retain_tmp"
    return 1
  fi
  rm -f "$_retain_tmp"
  printf '==> hardening rollback retained at %s\n' "$_retain_dst"
  _retain_q="'"
  _retain_rest="$_retain_dst"
  while [ -n "$_retain_rest" ]; do
    case "$_retain_rest" in
      *\'*)
        _retain_pre="${_retain_rest%%\'*}"
        _retain_rest="${_retain_rest#*\'}"
        _retain_q="$_retain_q$_retain_pre'\\''"
        ;;
      *)
        _retain_q="$_retain_q$_retain_rest'"
        _retain_rest=""
        ;;
    esac
  done
  printf '==> hardening active: run sudo %s --rollback\n' "$_retain_q"
  printf '==> then run: sudo systemctl reload ssh\n'
  printf '==> then run: sudo nft delete table inet opos_filter\n'
  return 0
}
print_hardening_instructions() {
  _hard_clone_path="$1"
  _hard_has_clone=0
  if [ -n "$_hard_clone_path" ]; then
    _hard_has_clone=1
  fi
  if [ "$_hard_has_clone" -eq 1 ]; then
    printf '==> hardening active: run sudo deploy/harden-device.sh --rollback before clone is deleted\n'
    printf '==> then run: sudo systemctl reload ssh\n'
    printf '==> then run: sudo nft delete table inet opos_filter\n'
  else
    printf '==> hardening active: run sudo deploy/harden-device.sh --rollback\n'
    printf '==> then run: sudo systemctl reload ssh\n'
    printf '==> then run: sudo nft delete table inet opos_filter\n'
  fi
}
is_linger_enabled() {
  if command -v loginctl >/dev/null 2>&1; then
    if loginctl show-user "$USER" 2>/dev/null | grep -q "Linger=yes"; then
      return 0
    fi
  fi
  if [ -n "${OPOS_ROOT:-}" ]; then
    if [ -e "$OPOS_ROOT/var/lib/opos-linger" ]; then
      return 0
    fi
  fi
  return 1
}
opos_owns_linger() {
  [ -e "${OPOS_ROOT:-}/var/lib/opos-linger" ]
}
remove_clone() {
  _rc_repo="$1"
  [ -n "$_rc_repo" ] || return 0
  _rc_canon="$(canon_repo "$_rc_repo")"
  _root_canon="$(canon_repo "$ROOT")"
  if [ "$_rc_canon" != "$_root_canon" ]; then
    return 0
  fi
  if [ ! -d "$_rc_canon/.git" ]; then
    return 0
  fi
  if [ ! -f "$_rc_canon/install.sh" ]; then
    return 0
  fi
  if [ "$_rc_canon" = "/" ]; then
    return 0
  fi
  if [ "$_rc_canon" = "$HOME" ]; then
    return 0
  fi
  FOUND=1
  if ! command -v git >/dev/null 2>&1; then
    printf '==> preserving checkout with owner contents %s\n' "$_rc_canon"
    return 0
  fi
  if ! _rc_top="$(git -C "$_rc_canon" rev-parse --show-toplevel 2>/dev/null)"; then
    printf '==> preserving checkout with owner contents %s\n' "$_rc_canon"
    return 0
  fi
  _rc_top_canon="$(canon_repo "$_rc_top")"
  if [ "$_rc_top_canon" != "$_rc_canon" ]; then
    return 0
  fi
  printf '==> preserving checkout with owner contents %s\n' "$_rc_canon"
  return 0
}
verified_recovery() {
  [ -f "$MANIFEST" ] || return 1
  [ "$UVENV" = "$OPOS_HOME/venv" ] || return 1
  opos_owned_path "$UVENV" "$OPOS_HOME/venv" || return 1
  [ -x "$UVENV/bin/python" ] || [ "${_try_recovery:-0}" = "1" ] || return 1
  [ -d "$ROOT/src/opos" ] || return 1
  [ "$(canon_repo "$UREPO")" = "$(canon_repo "$ROOT")" ] || return 1
  return 0
}
run_verified_recovery() {
  _verified=0
  if verified_recovery; then
    _verified=1
  fi
  if [ "$_verified" -eq 0 ] && [ -n "$UREPO" ] && [ "$(canon_repo "$UREPO")" != "$(canon_repo "$ROOT")" ]; then
    printf 'repo mismatch: run the uninstaller from the original checkout\n' >&2
  fi
  if [ "$_verified" -eq 1 ]; then
    _delegated_ran=1
    if [ "${_try_recovery:-0}" = "1" ]; then
      printf '==> launcher failed, trying verified recovery entry point\n' >&2
    fi
    _recovery_python="$UVENV/bin/python"
    if [ "${_try_recovery:-0}" = "1" ] && ! py_ok "$_recovery_python"; then
      if py_ok python3; then
        _recovery_python="python3"
      else
        _recovery_python=""
      fi
    fi
    if [ -z "$_recovery_python" ]; then
      printf '==> recovery python unavailable after launcher failure\n' >&2
    elif ( CDPATH='' cd -- "$ROOT/src" && PYTHONPATH="$ROOT/src" PYTHONSAFEPATH=1 "${_recovery_python:-$UVENV/bin/python}" -m opos.setup "$@" ); then
      _delegated_status=0
    else
      _delegated_status=$?
      printf '==> recovery uninstall failed with exit %s\n' "$_delegated_status" >&2
    fi
  fi
  return 0
}
do_uninstall() {
  UVENV=""
  _du_home="$OPOS_HOME"
  while [ "$_du_home" != "/" ] && [ "${_du_home%/}" != "$_du_home" ]; do
    _du_home="${_du_home%/}"
  done
  _du_bin="$OPOS_BIN_DIR"
  while [ "$_du_bin" != "/" ] && [ "${_du_bin%/}" != "$_du_bin" ]; do
    _du_bin="${_du_bin%/}"
  done
  OPOS_HOME="$_du_home"
  OPOS_BIN_DIR="$_du_bin"
  VENV="$OPOS_HOME/venv"
  MANIFEST="$OPOS_HOME/manifest"
  DEF_LAUNCHER="$OPOS_BIN_DIR/opos"
  _du_ok=1
  case "$OPOS_HOME" in
    ""|"/") _du_ok=0 ;;
    /*) ;;
    *) _du_ok=0 ;;
  esac
  case "$OPOS_BIN_DIR" in
    ""|"/") _du_ok=0 ;;
    /*) ;;
    *) _du_ok=0 ;;
  esac
  LAUNCHER="$DEF_LAUNCHER"
  UVENV="$VENV"
  UREPO=""
  _du_launcher_cksum=""
  if [ -f "$MANIFEST" ]; then
    UVENV="$(sed -n 's/^venv=//p' "$MANIFEST" | head -n 1)"
    UREPO="$(sed -n 's/^repo=//p' "$MANIFEST" | head -n 1)"
    _du_launcher_cksum="$(sed -n 's/^launcher_cksum=//p' "$MANIFEST" | head -n 1)"
    [ -n "${UVENV:-}" ] || UVENV="$VENV"
    _manifest_launcher="$(sed -n 's/^launcher=//p' "$MANIFEST" | head -n 1)"
    _manifest_launcher_display="$_manifest_launcher"
    if printf '%s' "$_manifest_launcher" | LC_ALL=C grep -q '[^ -~]'; then
      _manifest_launcher_display="[redacted]"
    fi
    if [ -n "${_manifest_launcher:-}" ]; then
      _manifest_ok=0
      case "$_manifest_launcher" in
        /*)
          ;;
        *)
          _manifest_ok=1
          ;;
      esac
      case "$_manifest_launcher" in
        /|/.|/..|/./*|/../*|*/./*|*/../*|*/.|*/..)
          _manifest_ok=1
          ;;
      esac
      if [ "$_manifest_ok" -eq 0 ]; then
        if [ "$(canon_file "$_manifest_launcher")" != "$(canon_file "$DEF_LAUNCHER")" ]; then
          _manifest_ok=1
        fi
      fi
      if [ "$_manifest_ok" -eq 1 ]; then
        printf 'warning: ignoring untrusted launcher in manifest: %s\n' "$_manifest_launcher_display" >&2
        if [ "$DRY" -eq 1 ]; then
          printf '==> warning: ignoring untrusted launcher in manifest: %s\n' "$_manifest_launcher_display"
        fi
      fi
    fi
  fi
  if [ "$_du_ok" -eq 0 ]; then
    LAUNCHER=""
    UVENV=""
    UREPO=""
  fi
  if [ "$_du_ok" -eq 1 ] && [ -n "$_du_launcher_cksum" ]; then
    if [ -e "$DEF_LAUNCHER" ] || [ -L "$DEF_LAUNCHER" ]; then
      if [ ! -f "$DEF_LAUNCHER" ]; then
        printf 'warning: ownership verification failed: %s\n' "$DEF_LAUNCHER" >&2
        return 1
      fi
      if ! _du_current_cksum="$(cksum < "$DEF_LAUNCHER")"; then
        printf 'warning: ownership verification failed: %s\n' "$DEF_LAUNCHER" >&2
        return 1
      fi
      if [ "$_du_current_cksum" != "$_du_launcher_cksum" ]; then
        printf 'warning: ownership verification failed: %s\n' "$DEF_LAUNCHER" >&2
        return 1
      fi
    fi
  fi
  set -- uninstall
  if [ "$KEEP" -eq 1 ]; then
    set -- "$@" --keep-config
  fi
  if [ "$DRY" -eq 1 ]; then
    set -- "$@" --dry-run
  fi
  _launcher_trusted=0
  if [ "$LAUNCHER" = "$DEF_LAUNCHER" ]; then
    if [ ! -L "$LAUNCHER" ]; then
      _launcher_trusted=1
    fi
  fi
  _have_launcher=0
  if [ "$_launcher_trusted" -eq 1 ]; then
    if [ -f "$LAUNCHER" ] && [ -x "$LAUNCHER" ]; then
      _have_launcher=1
    fi
  fi
  _delegated_status=0
  _delegated_ran=0
  _try_recovery=0
  if [ "$_have_launcher" -eq 1 ]; then
    _delegated_ran=1
    if [ "$DRY" -eq 1 ]; then
      printf '==> would run %s %s\n' "$LAUNCHER" "$*"
    else
      if "$LAUNCHER" "$@"; then
        _delegated_status=0
      else
        _delegated_status=$?
        printf '==> launcher uninstall failed with exit %s\n' "$_delegated_status" >&2
        case "$_delegated_status" in
          1|126|127)
            _try_recovery=1
            ;;
        esac
      fi
    fi
  else
    if [ "$DRY" -eq 1 ]; then
      _verified=0
      if verified_recovery; then
        _verified=1
      fi
      if [ "$_verified" -eq 1 ]; then
        printf '==> would run recovery entry point %s %s\n' "$UVENV/bin/python" "$*"
      else
        if [ -n "$UREPO" ] && [ "$(canon_repo "$UREPO")" != "$(canon_repo "$ROOT")" ]; then
          printf 'repo mismatch: run the uninstaller from the original checkout\n' >&2
        fi
        if [ "$FORCE" -eq 1 ]; then
          printf 'warning: force uninstall without verified recovery entry point, removing files only\n' >&2
        else
          printf '==> would fail: launcher not found %s\n' "$LAUNCHER" >&2
          printf 'revert by hand: restore sshd, firewall and service state, then remove %s and %s\n' "$LAUNCHER" "$MANIFEST" >&2
          return 1
        fi
      fi
    else
      if [ -f "$MANIFEST" ]; then
        run_verified_recovery "$@"
        if [ "$_verified" -eq 0 ]; then
          if [ "$FORCE" -eq 1 ]; then
            printf 'warning: force uninstall without verified recovery entry point, removing files only\n' >&2
          else
            printf 'launcher not found and no verified recovery entry point: %s\n' "$LAUNCHER" >&2
            printf 'revert by hand: restore sshd, firewall and service state, then remove %s and %s\n' "$LAUNCHER" "$MANIFEST" >&2
            return 1
          fi
        fi
      fi
    fi
  fi
  if [ "$_try_recovery" -eq 1 ] && [ "$DRY" -eq 0 ]; then
    run_verified_recovery "$@"
    if [ "$_verified" -eq 0 ]; then
      printf '==> no verified recovery entry point after launcher failure\n' >&2
    fi
  fi
  if [ "$_delegated_ran" -eq 1 ] && [ "$_delegated_status" -ne 0 ]; then
    if [ "$FORCE" -eq 1 ]; then
      printf 'warning: force uninstall after failed delegation, removing files only\n' >&2
    else
      return "$_delegated_status"
    fi
  fi
  if [ "$DRY" -eq 0 ]; then
    if [ -e "$OPOS_HOME/sshd_config.bak" ] || [ -L "$OPOS_HOME/sshd_config.bak" ]; then
      printf 'error: manual SSH restoration required for %s; restore sshd configuration by hand\n' "$OPOS_HOME/sshd_config.bak" >&2
      return 1
    fi
  fi
  FOUND=0
  _hardening_active=0
  if is_hardening_active; then
    _hardening_active=1
  fi
  _clone_candidate=""
  if [ -n "${UREPO:-}" ]; then
    _clone_canon="$(canon_repo "$UREPO")"
    _root_canon_check="$(canon_repo "$ROOT")"
    if [ "$_clone_canon" = "$_root_canon_check" ]; then
      if [ -d "$_clone_canon/.git" ] && [ -f "$_clone_canon/install.sh" ]; then
        if [ "$_clone_canon" != "/" ] && [ "$_clone_canon" != "$HOME" ]; then
          _clone_candidate="$_clone_canon"
        fi
      fi
    fi
  fi
  _restore_failed=0
  if [ "$DRY" -eq 1 ]; then
    if test -f "$OPOS_HOME/sshd_config.bak"; then
      printf '==> would remove %s\n' "$OPOS_HOME/sshd_config.bak"
      if test -f "/etc/ssh/sshd_config"; then
        printf '==> would overwrite /etc/ssh/sshd_config from %s\n' "$OPOS_HOME/sshd_config.bak"
      fi
    fi
    if command -v ufw >/dev/null 2>&1; then
      printf '==> would run ufw --force disable\n'
    fi
    if test -f "$OPOS_HOME/firewall.state"; then
      printf '==> would remove %s\n' "$OPOS_HOME/firewall.state"
    fi
    if command -v systemctl >/dev/null 2>&1; then
      printf '==> would run systemctl --user disable --now opos-server.service\n'
      printf '==> would run systemctl --user daemon-reload\n'
    fi
    if test -f "$HOME/.config/systemd/user/opos-server.service" && test ! -L "$HOME/.config/systemd/user/opos-server.service"; then
      printf '==> would remove %s\n' "$HOME/.config/systemd/user/opos-server.service"
    fi
    if [ "$_hardening_active" -eq 1 ]; then
      if [ -n "$_clone_candidate" ]; then
        print_hardening_instructions "$_clone_candidate"
      else
        print_hardening_instructions ""
      fi
    fi
    if opos_owns_linger && is_linger_enabled; then
      printf '==> linger is enabled: run sudo loginctl disable-linger %s\n' "$USER"
    fi
  else
    _sshd="skipped"
    _firewall="skipped"
    _service="skipped"
    _sshd_dropin="/etc/ssh/sshd_config.d/60-opos.conf"
    _sshd_marker="# Managed by OPOS - sshd drop-in"
    _sshd_expected="$OPOS_HOME/sshd_dropin.expected"
    _sshd_preserved="$OPOS_HOME/sshd_dropin.preserved"
    if [ ! -f "$_sshd_expected" ] && [ -f "$OPOS_HOME/sshd_dropin" ]; then
      _sshd_expected="$OPOS_HOME/sshd_dropin"
    fi
    if [ -L "$_sshd_dropin" ]; then
      printf 'refusing to write sshd config through symlink: %s\n' "$_sshd_dropin" >&2
      _sshd="failed"
    elif [ -f "$_sshd_dropin" ] && [ "$(head -n 1 "$_sshd_dropin")" != "$_sshd_marker" ]; then
      printf 'refusing to overwrite non-OPOS file: %s\n' "$_sshd_dropin" >&2
      _sshd="failed"
    elif [ -f "$_sshd_dropin" ]; then
      if [ "$(head -n 1 "$_sshd_dropin")" = "$_sshd_marker" ]; then
        _sshd_verified=0
        if [ -f "$_sshd_expected" ]; then
          if cmp "$_sshd_dropin" "$_sshd_expected" >/dev/null; then
            _sshd_verified=1
          fi
        fi
        if [ "$_sshd_verified" -eq 1 ]; then
          if rm -f "$_sshd_dropin"; then
            _sshd="succeeded"
          else
            _sshd="failed"
          fi
        else
          _sshd_preserved_dst="$_sshd_preserved"
          if [ -e "$_sshd_preserved_dst" ] || [ -L "$_sshd_preserved_dst" ]; then
            _sshd_preserved_n=1
            while [ -e "${_sshd_preserved_dst}.${_sshd_preserved_n}" ] || [ -L "${_sshd_preserved_dst}.${_sshd_preserved_n}" ]; do
              _sshd_preserved_n=$((_sshd_preserved_n + 1))
            done
            _sshd_preserved_dst="${_sshd_preserved_dst}.${_sshd_preserved_n}"
          fi
          # noclobber opens with O_EXCL, so an existing file or (dangling) symlink is never written through.
          if ( set -C; cat "$_sshd_dropin" > "$_sshd_preserved_dst" ); then
            printf 'manual sshd drop-in restoration required: %s has been modified (preserved at %s); automatic removal requires manual reconciliation\n' "$_sshd_dropin" "$_sshd_preserved_dst" >&2
          else
            printf 'manual sshd drop-in restoration required: %s has been modified; automatic removal requires manual reconciliation\n' "$_sshd_dropin" >&2
          fi
          _sshd="failed"
        fi
      fi
    fi
    if [ -f "$OPOS_HOME/sshd_config.bak" ]; then
      if [ -L "/etc/ssh/sshd_config" ]; then
        printf 'refusing to write sshd config through symlink: %s\n' "/etc/ssh/sshd_config" >&2
        _sshd="failed"
      elif [ "$_sshd" = "failed" ]; then
        :
      elif [ -L "$OPOS_HOME/sshd_config.bak" ]; then
        _sshd="failed"
      elif [ -f "$_sshd_dropin" ]; then
        _sshd="failed"
      elif [ ! -f "/etc/ssh/sshd_config" ]; then
        _sshd="failed"
      elif ! _bak_meta="$(stat -c '%u %a' "$OPOS_HOME/sshd_config.bak" || stat -f '%u %Lp' "$OPOS_HOME/sshd_config.bak")"; then
        _sshd="failed"
      elif [ -z "$_bak_meta" ]; then
        _sshd="failed"
      elif ! _bak_uid="$(printf '%s' "$_bak_meta" | cut -d ' ' -f 1)" || [ -z "$_bak_uid" ]; then
        _sshd="failed"
      elif ! _bak_mode="$(printf '%s' "$_bak_meta" | cut -d ' ' -f 2)" || [ -z "$_bak_mode" ]; then
        _sshd="failed"
      elif [ "$_bak_uid" != "0" ]; then
        _sshd="failed"
      elif case "$_bak_mode" in *[!0-7]* | "") true ;; *) false ;; esac; then
        _sshd="failed"
      elif case "$_bak_mode" in *[2367]) true ;; *) false ;; esac; then
        _sshd="failed"
      elif case "${_bak_mode%?}" in *[2367]) true ;; *) false ;; esac; then
        _sshd="failed"
      elif ! sshd -t -f "$OPOS_HOME/sshd_config.bak"; then
        _sshd="failed"
      elif ! _sshd_tmp="$(mktemp "/etc/ssh/sshd_config.opos.XXXXXX")"; then
        _sshd="failed"
      elif ! cp -p -f "$OPOS_HOME/sshd_config.bak" "$_sshd_tmp"; then
        rm -f "$_sshd_tmp"
        _sshd="failed"
      elif ! cmp "$OPOS_HOME/sshd_config.bak" "$_sshd_tmp" >/dev/null; then
        rm -f "$_sshd_tmp"
        _sshd="failed"
      elif ! sshd -t -f "$_sshd_tmp"; then
        rm -f "$_sshd_tmp"
        _sshd="failed"
      elif [ -d "/etc/ssh/sshd_config" ]; then
        rm -f "$_sshd_tmp"
        _sshd="failed"
      elif [ -e "$OPOS_HOME/sshd_config.live" ] || [ -L "$OPOS_HOME/sshd_config.live" ]; then
        rm -f "$_sshd_tmp"
        printf 'manual sshd restoration required: %s requires manual reconciliation\n' "$OPOS_HOME/sshd_config.live" >&2
        _sshd="failed"
      elif ! { rm -f "$OPOS_HOME/sshd_config.live" && cat "/etc/ssh/sshd_config" > "$OPOS_HOME/sshd_config.live"; }; then
        rm -f "$_sshd_tmp" "$OPOS_HOME/sshd_config.live"
        _sshd="failed"
      elif ! mv -f "$_sshd_tmp" "/etc/ssh/sshd_config"; then
        rm -f "$_sshd_tmp" "$OPOS_HOME/sshd_config.live"
        _sshd="failed"
      elif [ ! -f "/etc/ssh/sshd_config" ]; then
        rm -f "$_sshd_tmp"
        printf 'previous sshd configuration preserved at %s\n' "$OPOS_HOME/sshd_config.live" >&2
        _sshd="failed"
      elif ! cmp "$OPOS_HOME/sshd_config.bak" "/etc/ssh/sshd_config" >/dev/null; then
        rm -f "$_sshd_tmp"
        printf 'previous sshd configuration preserved at %s\n' "$OPOS_HOME/sshd_config.live" >&2
        _sshd="failed"
      elif ! rm -f "$OPOS_HOME/sshd_config.live"; then
        _sshd="failed"
      elif ! rm -f "$OPOS_HOME/sshd_config.bak"; then
        _sshd="failed"
      else
        _sshd="succeeded"
      fi
    fi
    if [ -f "$OPOS_HOME/firewall.state" ] && [ "$_firewall" != "failed" ]; then
      if _fw_state=$(cat "$OPOS_HOME/firewall.state"); then
        case "$_fw_state" in
          active|on)
            if rm -f "$OPOS_HOME/firewall.state"; then
              _firewall="succeeded"
            else
              printf 'manual firewall restoration is required: restore the firewall by hand from %s\n' "$OPOS_HOME/firewall.state" >&2
              _firewall="failed"
            fi
            ;;
          inactive|off)
            if ufw --force disable; then
              if ufw reload; then
                if rm -f "$OPOS_HOME/firewall.state"; then
                  _firewall="succeeded"
                else
                  printf 'manual firewall restoration is required: restore the firewall by hand from %s\n' "$OPOS_HOME/firewall.state" >&2
                  _firewall="failed"
                fi
              else
                printf 'manual firewall restoration is required: restore the firewall by hand from %s\n' "$OPOS_HOME/firewall.state" >&2
                _firewall="failed"
              fi
            else
              ufw reload || true
              printf 'manual firewall restoration is required: restore the firewall by hand from %s\n' "$OPOS_HOME/firewall.state" >&2
              _firewall="failed"
            fi
            ;;
          *)
            printf 'invalid saved firewall state in %s\n' "$OPOS_HOME/firewall.state" >&2
            _firewall="failed"
            ;;
        esac
      else
        printf 'invalid saved firewall state in %s\n' "$OPOS_HOME/firewall.state" >&2
        _firewall="failed"
      fi
    fi
    # BEGIN fallback service cleanup
    _service_uid=""
    _service_home="$HOME"
    _service_sudo=0
    _service_skip=0
    _service_runtime=""
    if _service_uid=$(id -u); then
      case "$_service_uid" in
        ""|*[!0-9]*)
          _service_skip=1
          ;;
        0)
          _service_skip=1
          if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
            if _service_target_uid=$(id -u "$SUDO_USER") && _service_passwd=$(getent passwd "$SUDO_USER"); then
              case "$_service_target_uid" in
                ""|*[!0-9]*|0)
                  ;;
                *)
                  _service_target_home=$(printf '%s' "$_service_passwd" | cut -d ':' -f 6)
                  case "$_service_target_home" in
                    /*)
                      _service_uid="$_service_target_uid"
                      _service_home="$_service_target_home"
                      _service_sudo=1
                      _service_skip=0
                      ;;
                  esac
                  ;;
              esac
            fi
          fi
          ;;
      esac
    else
      _service_skip=1
    fi
    if [ "$_service_skip" -eq 1 ]; then
      printf '%s\n' 'service cleanup skipped: no original non-root user could be resolved' >&2
    fi
    if [ "$_service_sudo" -eq 1 ]; then
      _service_runtime=/run/user/${_service_uid}
    fi
    _unit="$_service_home/.config/systemd/user/opos-server.service"
    _service_run() {
      if [ "$_service_sudo" -eq 1 ]; then
        sudo -u "$SUDO_USER" env "HOME=$_service_home" "XDG_RUNTIME_DIR=$_service_runtime" "DBUS_SESSION_BUS_ADDRESS=unix:path=${_service_runtime}/bus" "LC_ALL=C" "$@"
      else
        LC_ALL=C "$@"
      fi
    }
    if [ "$_service_skip" -eq 0 ]; then
      if command -v systemctl >/dev/null 2>&1; then
        _probe_out=""
        _probe_ok=0
        _listed=0
        if _probe_out=$(_service_run systemctl --user list-unit-files --no-legend opos-server.service); then
          _probe_ok=1
          if printf '%s\n' "$_probe_out" | grep -q -e '^opos-server\.service[[:space:]]' -e '^opos-server\.service$'; then
            _listed=1
          fi
        fi
        _have_file=0
        if [ -f "$_unit" ] && [ ! -L "$_unit" ]; then
          _have_file=1
        fi
        if [ -L "$_unit" ]; then
          printf 'preserving unverified service unit %s\n' "$_unit" >&2
          _service="failed"
        elif [ "$_probe_ok" -eq 1 ] && { [ "$_have_file" -eq 1 ] || [ "$_listed" -eq 1 ]; }; then
          _service="succeeded"
          _diag=""
          if _diag=$(_service_run systemctl --user disable --now opos-server.service 2>&1); then
            :
          else
            if [ -n "$_diag" ]; then
              printf '%s\n' "$_diag" >&2
            fi
            case "$_diag" in
              *"does not exist"*)
                _service="skipped"
                ;;
              *)
                _service="failed"
                ;;
            esac
          fi
          if [ "$_service" != "failed" ]; then
            if [ -f "$_unit" ]; then
              if ! _service_run rm -f "$_unit"; then
                _service="failed"
              fi
            fi
          fi
          if ! _service_run systemctl --user daemon-reload; then
            _service="failed"
          fi
        else
          if [ "$_probe_ok" -eq 0 ]; then
            if [ -f "$_unit" ]; then
              printf 'systemd probe failed; preserving %s\n' "opos-server.service" >&2
            fi
            _service="failed"
          fi
        fi
      else
        if [ -f "$_unit" ]; then
          printf 'systemctl not found; preserving %s\n' "opos-server.service" >&2
          _service="failed"
        fi
      fi
    fi
    # END fallback service cleanup
    printf '==> restore summary: sshd=%s firewall=%s service=%s\n' "$_sshd" "$_firewall" "$_service"
    if [ "$_sshd" = "failed" ]; then
      printf 'sshd restore failed; backup retained at %s; retry as root: cp -f %s /etc/ssh/sshd_config\n' "$OPOS_HOME/sshd_config.bak" "$OPOS_HOME/sshd_config.bak" >&2
    fi
    if [ "$_sshd" = "succeeded" ] && [ -f "$OPOS_HOME/sshd_config.bak" ]; then
      _sshd="failed"
    fi
    if [ "$_sshd" = "failed" ] || [ "$_firewall" = "failed" ] || [ "$_service" = "failed" ]; then
      _restore_failed=1
    fi
  fi
  opos_owned_path "$LAUNCHER" "$DEF_LAUNCHER" || return 1
  if [ "$LAUNCHER" = "$DEF_LAUNCHER" ]; then
    remove_path "$LAUNCHER" f
  fi
  if [ -n "${_manifest_launcher:-}" ] && [ "$(canon_file "$_manifest_launcher")" != "$(canon_file "$DEF_LAUNCHER")" ]; then
    printf 'warning: not removing launcher outside trusted location: %s\n' "$_manifest_launcher_display" >&2
  fi
  remove_path "$MANIFEST" f
  if [ "$_restore_failed" -eq 1 ]; then
    :
  elif [ -f "$OPOS_HOME/sshd_config.bak" ]; then
    :
  elif [ -f "$OPOS_HOME/firewall.state" ]; then
    :
  elif [ "$DRY" -eq 1 ]; then
    remove_path "$OPOS_HOME" d
  else
    if [ -n "${UVENV:-}" ] && [ "$UVENV" = "$OPOS_HOME/venv" ] && opos_owned_path "$UVENV" "$OPOS_HOME/venv"; then
      remove_path "$UVENV" d
    fi
    rmdir "$OPOS_HOME" 2>/dev/null || true
  fi
  if [ "$DRY" -eq 0 ]; then
    rmdir "$OPOS_BIN_DIR" 2>/dev/null || true
    if [ "$_restore_failed" -eq 1 ]; then
      :
    elif [ -f "$OPOS_HOME/sshd_config.bak" ]; then
      :
    elif [ -f "$OPOS_HOME/firewall.state" ]; then
      :
    else
      rmdir "$(dirname "$OPOS_HOME")" 2>/dev/null || true
    fi
  fi
  if [ "$_hardening_active" -eq 1 ]; then
    if [ -n "$_clone_candidate" ] && [ "$FORCE" -eq 1 ]; then
      if retain_hardening_rollback "$_clone_candidate"; then
        remove_clone "$_clone_candidate"
      else
        printf '==> hardening active: clone not removed (rollback helper unavailable)\n' >&2
      fi
    else
      if [ "$DRY" -eq 0 ]; then
        if [ -n "$_clone_candidate" ]; then
          print_hardening_instructions "$_clone_candidate"
        else
          print_hardening_instructions ""
        fi
      fi
      if [ -n "$_clone_candidate" ] && [ "$FORCE" -eq 0 ]; then
        printf '==> hardening active: clone not removed (use --force to override)\n'
      fi
    fi
  else
    if [ -n "$_clone_candidate" ]; then
      remove_clone "$_clone_candidate"
    fi
  fi
  if [ "$DRY" -eq 0 ]; then
    if opos_owns_linger && is_linger_enabled; then
      printf '==> linger is enabled: run sudo loginctl disable-linger %s\n' "$USER"
    fi
  fi
  if [ "$FOUND" -eq 0 ]; then
    printf '==> nothing to remove\n'
  fi
  if [ "$_restore_failed" -eq 1 ]; then
    printf 'warning: restore failed; sshd/firewall recovery files retained in %s\n' "$OPOS_HOME" >&2
    return 1
  fi
  return 0
}
duo_reboot_warning() {
  _duo_root="${OPOS_DT_ROOT:-}"
  for _duo_f in "${_duo_root}/proc/device-tree/model" "${_duo_root}/sys/firmware/devicetree/base/model"; do
    [ -f "$_duo_f" ] || continue
    _duo_m=$(tr -d '\000' 2>/dev/null <"$_duo_f") || _duo_m=""
    case "$_duo_m" in *[Ss]urface\ [Dd]uo*)
      printf '%s\n' 'warning: Surface Duo detected. If this kernel was RAM-booted from a PC with fastboot boot, do not run reboot or systemctl reboot: the phone would start the stock kernel and be stranded.' >&2
      printf '%s\n' 'To restart safely run reboot bootloader, then re-arm the misc brake and RAM-boot once from the PC. See docs/duo-usb-install.md, section "Rebooting safely / recovering".' >&2;;
    esac; break
  done; return 0
}
ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
parse_args "$@"
if [ "$KEEP" -eq 1 ] && [ "$UNINST" -eq 0 ]; then
  usage >&2
  exit 2
fi
if [ "$UNINST" -eq 0 ]; then
  check_paths
fi
VENV="$OPOS_HOME/venv"
MANIFEST="$OPOS_HOME/manifest"
DEF_LAUNCHER="$OPOS_BIN_DIR/opos"
TMP_LAUNCHER="$OPOS_BIN_DIR/.opos.$$"
TMP_MANIFEST="$MANIFEST.tmp.$$"
cleanup() {
  _rc=$?
  if [ -n "${_staging_root:-}" ] && [ -d "$_staging_root" ]; then
    _staging_parent="$(dirname -- "$_staging_root")"
    _staging_base="$(basename -- "$_staging_root")"
    case "$_staging_base" in
      .venv-staging.*)
        if [ "$_staging_parent" = "$OPOS_HOME" ]; then
          case "$_staging_root" in
            *..*)
              ;;
            *)
              rm -rf "$_staging_root"
              ;;
          esac
        fi
        ;;
    esac
  fi
  rm -f "$TMP_LAUNCHER" "$TMP_MANIFEST" 2>/dev/null || true
  exit "$_rc"
}
_staging_root=""
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [ "$UNINST" -eq 0 ] && [ "$YES" -eq 0 ]; then
  if [ "$(id -u 2>/dev/null || printf 1)" -eq 0 ]; then
    printf 'warning: per-user install as root is probably not what you want\n' >&2
  fi
fi
if [ "$UNINST" -eq 1 ]; then
  if do_uninstall; then
    _uninstall_rc=0
  else
    _uninstall_rc=$?
  fi
  exit "$_uninstall_rc"
fi
if py=$(find_python); then
  :
else
  printf 'Python 3.11 or newer is required\n%s' "$(hint)" >&2
  exit 1
fi
say "use Python $py"
ensure_venv
install_requirements
run_selftest
write_launcher
write_manifest
pp=":$PATH:"
case "$pp" in
  *":$OPOS_BIN_DIR:"*)
    ;;
  *)
    printf 'warning: %s not in PATH, add: export PATH="%s:%s"\n' "$OPOS_BIN_DIR" "$OPOS_BIN_DIR" "\$PATH" >&2
    if [ "$DRY" -ne 1 ] && [ -f "$HOME/.profile" ] && ! grep -qxF '# added by Old Phone OS install' "$HOME/.profile"; then
      case "$OPOS_BIN_DIR" in
        *[\\\"\$\`]*)
          ;;
        *)
          if [ -n "$(tail -c 1 "$HOME/.profile")" ]; then
            printf '\n' >> "$HOME/.profile"
          fi
          if printf '%s\n' '# added by Old Phone OS install' >> "$HOME/.profile" && printf 'export PATH="%s:%s"\n' "$OPOS_BIN_DIR" "\$PATH" >> "$HOME/.profile"; then
            printf '==> added %s to PATH in %s (open a new shell)\n' "$OPOS_BIN_DIR" "$HOME/.profile"
          fi
          ;;
      esac
    fi
    ;;
esac
duo_reboot_warning
if [ "$DRY" -eq 1 ]; then
  printf '==> dry run finished, nothing was written\n'
else
  printf '==> installed: run %s%s%s (%s)\n' "\`" "opos" "\`" "$OPOS_BIN_DIR/opos"
fi
if [ "$NOLAUNCH" -eq 0 ] && [ "$DRY" -eq 0 ]; then
  exec "$OPOS_BIN_DIR/opos" run
fi
