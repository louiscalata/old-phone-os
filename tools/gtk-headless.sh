#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# gtk-headless.sh <checkout> <command> [argument...]
#
# Runs the command under a headless Weston, 1392x900, with real GTK 4: for tests/test_native_gtk_real.py. It starts one
# Weston in a private runtime folder it makes under the temporary folder, points the command at it (GDK_BACKEND=wayland,
# WAYLAND_DISPLAY, OPOS_GTK_HEADLESS=1, PYTHONPATH=<checkout>/src) and, when the command ends, stops Weston and removes
# the folder. It installs nothing, uses no network and no sudo, and ends with the command's own exit status.
# Needs weston on PATH (and, for the command, python3-gi and gir1.2-gtk-4.0). Run it from a D-Bus session
# (dbus-run-session -- sh tools/gtk-headless.sh ...) so GTK has a bus; see tools/README.md.
set -eu

if [ "$#" -lt 2 ]; then
    printf '%s\n' 'usage: gtk-headless.sh <checkout> <command> [argument...]' >&2
    exit 2
fi
checkout=$1
shift
if [ ! -d "$checkout/src/opos" ]; then
    printf '%s\n' "gtk-headless: $checkout is not an OPOS checkout (no src/opos)" >&2
    exit 2
fi
if ! command -v weston >/dev/null 2>&1; then
    printf '%s\n' 'gtk-headless: weston is not installed here; nothing was started' >&2
    exit 2
fi

run=$(mktemp -d "${TMPDIR:-/tmp}/opos-gtk.XXXXXX")
chmod 700 "$run"
weston_pid=

cleanup() {
    status=$?
    trap - EXIT
    if [ -n "$weston_pid" ]; then
        kill "$weston_pid" 2>/dev/null || true
        wait "$weston_pid" 2>/dev/null || true
    fi
    # Only the files Weston and its socket leave directly in our own folder are removed, one by one; there is no
    # recursive delete. A folder that still holds anything else is left in place, and said.
    case $run in
        */opos-gtk.*)
            for f in "$run"/* "$run"/.[!.]*; do
                if [ -e "$f" ] || [ -L "$f" ]; then
                    rm -- "$f" 2>/dev/null || true
                fi
            done
            rmdir -- "$run" 2>/dev/null || printf '%s\n' "gtk-headless: left $run (not empty)" >&2
            ;;
    esac
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

XDG_RUNTIME_DIR=$run
PYTHONPATH=$checkout/src
GDK_BACKEND=wayland
WAYLAND_DISPLAY=opos-test
OPOS_GTK_HEADLESS=1
GDK_SCALE=1
GSK_RENDERER=cairo
OPOS_SPAN=675,42
NO_AT_BRIDGE=1
GSETTINGS_BACKEND=memory
PYTHONDONTWRITEBYTECODE=1
export XDG_RUNTIME_DIR PYTHONPATH GDK_BACKEND WAYLAND_DISPLAY OPOS_GTK_HEADLESS GDK_SCALE GSK_RENDERER OPOS_SPAN NO_AT_BRIDGE GSETTINGS_BACKEND PYTHONDONTWRITEBYTECODE

weston --backend=headless --renderer=pixman --no-config --socket=opos-test --width=1392 --height=900 --scale=1 \
    --log="$run/weston.log" >/dev/null 2>&1 &
weston_pid=$!

tries=0
while [ ! -S "$run/opos-test" ] && [ "$tries" -lt 50 ]; do
    sleep 0.1
    tries=$((tries + 1))
done
if [ ! -S "$run/opos-test" ]; then
    printf '%s\n' 'gtk-headless: weston did not start; the end of its log:' >&2
    tail -n 5 "$run/weston.log" >&2 2>/dev/null || true
    exit 1
fi

"$@"
