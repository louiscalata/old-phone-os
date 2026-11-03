#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu
clone_failed() {
    printf 'bootstrap: cannot clone %s (ref %s): the repository is unreachable, private or not yet published. Use a published OPOS_REPO_URL/OPOS_REF from the release manifest, or run install.sh from a retained checkout.\n' "$1" "$2" >&2
    exit 1
}
main() {
    repo="${OPOS_REPO_URL:?OPOS_REPO_URL must be set}"
    dest="${OPOS_SRC_DIR:-$HOME/opos-src}"
    case "$dest" in
        /*) ;;
        *) printf 'bootstrap: destination must be an absolute path: %s\n' "$dest" >&2; return 1 ;;
    esac
    case "$dest" in
        *[[:space:]%]*)
            printf 'bootstrap: destination must not contain whitespace or %%: %s\n' "$dest" >&2
            return 1
            ;;
    esac
    if [ -e "$dest" ]; then
        printf 'bootstrap: destination exists: %s\n' "$dest" >&2
        return 1
    fi
    ref="${OPOS_REF:-main}"
    case "$ref" in
        ""|-*|*[[:space:]]*) printf 'bootstrap: invalid branch, tag or commit reference\n' >&2; return 1 ;;
    esac
    export GIT_TERMINAL_PROMPT=0
    if [ "${#ref}" -eq 40 ] && ! printf '%s' "$ref" | LC_ALL=C grep -q '[^0-9a-fA-F]'; then
        git clone --no-checkout -- "$repo" "$dest" || clone_failed "$repo" "$ref"
        if ! git -C "$dest" checkout --detach "$ref"; then
            rm -rf -- "$dest"
            clone_failed "$repo" "$ref"
        fi
    else
        git clone --branch "$ref" -- "$repo" "$dest" || clone_failed "$repo" "$ref"
    fi
    sh "$dest/install.sh" --yes --no-launch
}
main "$@"
