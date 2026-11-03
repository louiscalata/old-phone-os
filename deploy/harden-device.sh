#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
OPOS_ROOT="${OPOS_ROOT:-/}"
OPOS_USER="${OPOS_USER:-droidian}"
case "$OPOS_USER" in
  [a-z_][a-z0-9_-]*)
    case "$OPOS_USER" in
      *[!a-z0-9_-]*) echo "[harden] WARN: bad user name" >&2; exit 1 ;;
    esac
    ;;
  *) echo "[harden] WARN: bad user name" >&2; exit 1 ;;
esac
OPOS_USB_IFACES="${OPOS_USB_IFACES:-usb0 rndis0}"
OPOS_DISABLE_UNITS="${OPOS_DISABLE_UNITS:-avahi-daemon.service avahi-daemon.socket cups.service cups.socket cups-browsed.service rpcbind.service rpcbind.socket}"
MODE="check"
ONLY=""
SKIP=""
ADD_KEY=""
INSTALL_PKGS="0"
usage() {
  cat <<'EOF'
Usage: harden-device.sh [--check|--apply|--rollback] [--only NAME,...] [--skip NAME,...] [--add-key FILE] [--install-packages] [-h|--help]
EOF
}
say() {
  printf '[harden] %s\n' "$1"
}
warn() {
  printf '[harden] WARN: %s\n' "$1" >&2
}
die() {
  warn "$1"
  exit 1
}
p() {
  printf '%s\n' "$ROOT$1"
}
backup() {
  [ "$APPLY" = "1" ] || return 0
  if [ -e "$(p "$1")" ]; then
    mkdir -p "$(dirname "$BACKUP_DIR$1")"
    cp -p "$(p "$1")" "$BACKUP_DIR$1"
    printf 'restore %s\n' "$1" >> "$MANIFEST"
  else
    printf 'created %s\n' "$1" >> "$MANIFEST"
  fi
}
write_file() {
  if [ "$APPLY" != "1" ]; then
    say "would write $1 ($2)"
    return 0
  fi
  backup "$1"
  mkdir -p "$(dirname "$(p "$1")")"
  tmp="$(p "$1").tmp.$$"
  cat > "$tmp"
  chmod "$2" "$tmp"
  mv "$tmp" "$(p "$1")"
}
run() {
  if [ "$APPLY" != "1" ]; then
    say "would run: $*"
    return 0
  fi
  if [ "$TEST" = "1" ]; then
    say "skip (test root): $*"
    return 0
  fi
  "$@"
}
have_cmd() {
  [ "$TEST" = "0" ] && command -v "$1" >/dev/null 2>&1
}
is_selected() {
  case ",$SKIP," in
    *",$1,"*) return 1 ;;
  esac
  if [ -n "$ONLY" ]; then
    case ",$ONLY," in
      *",$1,"*) return 0 ;;
      *) return 1 ;;
    esac
  fi
  return 0
}
# ADD_KEY and INSTALL_PKGS are consumed by libraries in deploy/harden.d.
# shellcheck disable=SC2034
while [ "$#" -gt 0 ]; do
  case "$1" in
    --check) MODE="check"; shift ;;
    --apply) MODE="apply"; shift ;;
    --rollback) MODE="rollback"; shift ;;
    --only) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; ONLY="$2"; shift 2 ;;
    --only=*) ONLY="${1#--only=}"; shift ;;
    --skip) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; SKIP="$2"; shift 2 ;;
    --skip=*) SKIP="${1#--skip=}"; shift ;;
    --add-key) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; ADD_KEY="$2"; shift 2 ;;
    --add-key=*) ADD_KEY="${1#--add-key=}"; shift ;;
    --install-packages) INSTALL_PKGS="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; [ "$#" -eq 0 ] || { usage >&2; exit 2; }; break ;;
    -*) usage >&2; exit 2 ;;
    *) usage >&2; exit 2 ;;
  esac
done
if [ "$MODE" = "check" ]; then APPLY="0"; else APPLY="1"; fi
if [ -n "${TEST:-}" ] && [ -z "${OPOS_HARDEN_TEST:-}" ]; then
  warn "TEST is ignored; use OPOS_HARDEN_TEST instead"
fi
if [ "$OPOS_ROOT" != "/" ]; then TEST="1"; else TEST="0"; fi
if [ -n "${OPOS_HARDEN_TEST:-}" ]; then
  case "$OPOS_HARDEN_TEST" in
    0|1)
      warn "OPOS_HARDEN_TEST overrides TEST: $TEST -> $OPOS_HARDEN_TEST"
      TEST="$OPOS_HARDEN_TEST"
      ;;
    *)
      die "OPOS_HARDEN_TEST must be 0 or 1 (got '$OPOS_HARDEN_TEST')"
      ;;
  esac
fi
ROOT="${OPOS_ROOT%/}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$ROOT/var/backups/opos-harden/$STAMP"
MANIFEST="$BACKUP_DIR/MANIFEST"
STEPS="ssh firewall services updates fde"
script_dir="$(dirname "$0")"
for lib in "$script_dir"/harden.d/*.sh; do
  [ -e "$lib" ] || break
  # shellcheck disable=SC1090
  . "$lib"
done
if [ "$MODE" = "rollback" ]; then
  if [ "$TEST" = "0" ] && [ "$(id -u)" != "0" ]; then die "run as root"; fi
  base="$ROOT/var/backups/opos-harden"
  latest=""
  if [ -d "$base" ]; then
    for d in "$base"/*/; do
      [ -d "$d" ] || continue
      n="${d%/}"
      n="${n##*/}"
      if [ -z "$latest" ] || [ "$n" \> "$latest" ]; then
        latest="$n"
      fi
    done
  fi
  [ -n "${latest:-}" ] || die "no backup found"
  dir="$base/$latest"
  [ -f "$dir/MANIFEST" ] || die "no backup found"
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      restore\ *|created\ *|enabled\ *|disabled\ *|active\ *|inactive\ *)
        rel="${line#* }"
        case "$line" in
          restore\ *|created\ *)
            case "$rel" in
              ""|*[!A-Za-z0-9@._/-]*|*..*|/*\ *|[!/]*) die "bad manifest entry" ;;
            esac
            ;;
          enabled\ *|disabled\ *|active\ *|inactive\ *)
            case "$rel" in
              ""|-*|*[!A-Za-z0-9@._-]* ) die "bad manifest entry" ;;
            esac
            ;;
        esac
        case "$line" in
          restore\ *) cp -p "$dir$rel" "$(p "$rel")" ;;
          created\ *) rm -f "$(p "$rel")" ;;
          enabled\ *) run systemctl enable -- "$rel" ;;
          disabled\ *) run systemctl disable -- "$rel" ;;
          active\ *) run systemctl start -- "$rel" ;;
          inactive\ *) run systemctl stop -- "$rel" ;;
        esac
        ;;
      \#*) : ;;
      "") : ;;
      *) die "bad manifest entry" ;;
    esac
  done < "$dir/MANIFEST"
  if [ "$TEST" = "0" ]; then run systemctl daemon-reload; fi
  say "reload sshd and the firewall yourself if you changed them: systemctl reload ssh; nft delete table inet opos_filter; /etc/opos/firewall-legacy.sh stop"
  say "mode: rollback steps: $STEPS backup: $dir"
  exit 0
fi
if [ "$MODE" = "apply" ] && [ "$TEST" = "0" ] && [ "$(id -u)" != "0" ]; then die "run as root"; fi
if [ "$MODE" = "apply" ]; then
  mkdir -p "$BACKUP_DIR"
  if [ "$TEST" = "0" ]; then chmod 0700 "$BACKUP_DIR"; fi
  printf '# opos-harden %s\n' "$STAMP" > "$MANIFEST"
fi
selected=""
for name in $STEPS; do
  if is_selected "$name"; then selected="$selected $name"; fi
done
selected="${selected# }"
if [ "$MODE" = "apply" ]; then
  say "steps:$selected"
  printf 'type yes-apply to continue: '
  answer=""
  IFS= read -r answer || die "aborted"
  [ "$answer" = "yes-apply" ] || die "aborted"
fi
ran=""
for name in $STEPS; do
  if is_selected "$name"; then
    "step_$name"
    say "step $name done"
    ran="$ran $name"
  fi
done
ran="${ran# }"
if [ "$MODE" = "apply" ]; then say "mode: apply steps:$ran backup: $BACKUP_DIR"; else say "mode: check steps:$ran"; fi
