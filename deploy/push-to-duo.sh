#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu
dry_run=0
run() {
  if [ "$dry_run" -eq 1 ]; then
    printf '+ %s\n' "$*"
  else
    "$@"
  fi
}
main() {
  if [ "${1:-}" = "--dry-run" ]; then
    dry_run=1
    shift
  fi
  if [ "$#" -lt 1 ]; then
    echo "usage: push-to-duo.sh [--dry-run] USER@HOST [REF]" >&2
    exit 2
  fi
  case "$1" in
    *@*) ;;
    *) echo "usage: push-to-duo.sh [--dry-run] USER@HOST [REF]" >&2; exit 2 ;;
  esac
  target="$1"
  ref="${2:-HEAD}"
  user="${target%%@*}"
  host="${target#*@}"
  root="$(dirname "$(dirname "$0")")"
  cd "$root" || exit 1
  if [ "$dry_run" -eq 1 ]; then
    tmp="<tmp>"
    remote_dir="<remote-tmp>"
  else
    tmp="$(mktemp -d)"
    remote_dir="$(ssh "$user@$host" mktemp -d /tmp/opos-push.XXXXXXXX)"
    exit_status=0
    trap 'exit_status=$?; ssh "$user@$host" rm -rf "$remote_dir" >/dev/null 2>&1 || true; rm -rf "$tmp"; exit "$exit_status"' EXIT
  fi
  archive="$tmp/opos-push.tgz"
  run git archive --format=tar.gz -o "$archive" "$ref"
  case "$host" in
    *:*)
      dest="$user@[$host]:$remote_dir/opos-push.tgz"
      ;;
    *)
      dest="$user@$host:$remote_dir/opos-push.tgz"
      ;;
  esac
  run scp -q "$archive" "$dest"
  run ssh "$user@$host" sh -s -- "$remote_dir" <<'REMOTE'
set -eu
stage="$1"
if ! mkdir "$HOME/.opos-push.lock"; then
  echo "push-to-duo: deployment locked" >&2
  exit 1
fi
trap 'rmdir "$HOME/.opos-push.lock"' EXIT
keep=2
count=0
for b in "$HOME"/opos.bak-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]; do
  if [ ! -d "$b" ]; then
    continue
  fi
  if [ -L "$b" ]; then
    continue
  fi
  if [ ! -f "$b/install.sh" ]; then
    continue
  fi
  count=$((count + 1))
done
remove=$((count - keep))
if [ "$remove" -gt 0 ]; then
  for b in "$HOME"/opos.bak-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]; do
    if [ ! -d "$b" ]; then
      continue
    fi
    if [ -L "$b" ]; then
      continue
    fi
    if [ ! -f "$b/install.sh" ]; then
      continue
    fi
    rm -rf "$b"
    remove=$((remove - 1))
    if [ "$remove" -le 0 ]; then
      break
    fi
  done
fi
reserve_kb=262144
size_kb=$(du -k "$stage/opos-push.tgz" | cut -f1)
free_kb=$(df -Pk "$HOME" | awk 'NR==2 {print $4}')
need_kb=$((size_kb * 4 + reserve_kb))
if [ "$free_kb" -lt "$need_kb" ]; then
  echo "push-to-duo: not enough free space in $HOME (need $need_kb KB, have $free_kb KB)" >&2
  rm -f "$stage/opos-push.tgz"
  exit 1
fi
backup=""
trap 's=$?; set +e; if [ "$s" -ne 0 ] && [ -n "$backup" ] && [ -d "$backup" ]; then cd "$HOME" || true; rm -rf "$HOME/opos" && mv "$backup" "$HOME/opos" && echo "push-to-duo: install failed, restored previous deployment" >&2; fi; rmdir "$HOME/.opos-push.lock"; exit "$s"' EXIT
stamp=$(date +%Y%m%d-%H%M%S)
if [ -e "$HOME/opos" ]; then
  mv "$HOME/opos" "$HOME/opos.bak-$stamp" && backup="$HOME/opos.bak-$stamp"
fi
mkdir "$HOME/opos"
tar -xzf "$stage/opos-push.tgz" -C "$HOME/opos"
rm -rf "$stage"
cd "$HOME/opos"
sh install.sh --no-launch --yes
systemctl --user try-restart opos-server.service >/dev/null 2>&1 || true
printf 'note: open a new login shell or run ". ~/.profile" so opos is on PATH\n'
"$HOME/.local/bin/opos" status || true
printf 'health: if running is no, start it with: opos run --defaults\n'
REMOTE
}
main "$@"
