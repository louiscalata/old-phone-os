#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
main() {
    if [ "$#" -eq "1" ] && [ "$1" = "--help" ]; then
        printf '%s\n' 'usage: qemu-demo.sh [--help]'
        return 0
    fi
    if [ "$#" -gt "0" ]; then
        printf '%s\n' 'usage: qemu-demo.sh [--help]' >&2
        return 2
    fi
    OUT="${OPOS_APORTS_DIR:-$HOME/opos-pmaports}"
    DEVICE="${OPOS_QEMU_DEVICE:-qemu-aarch64}"
    case "$DEVICE" in
        qemu-amd64|qemu-aarch64) ;;
        *) printf '%s\n' 'qemu-demo: choose qemu-amd64 or qemu-aarch64' >&2; return 2 ;;
    esac
    out_q=$(python3 -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$OUT") || return 1
    device_q=$(python3 -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$DEVICE") || return 1
    printf '%s\n' "1. COPYFILE_DISABLE=1 OPOS_LOCAL_SOURCE=1 sh packaging/pmos/stage-aports.sh $out_q"
    printf '%s\n' "2. pmbootstrap config aports $out_q"
    printf '%s\n' "3. pmbootstrap config device $device_q"
    printf '%s\n' '4. pmbootstrap config ui opos'
    printf '%s\n' '5. pmbootstrap init'
    printf '%s\n' '6. pmbootstrap checksum opos postmarketos-ui-opos'
    printf '%s\n' '7. pmbootstrap build opos'
    printf '%s\n' '8. pmbootstrap build postmarketos-ui-opos'
    printf '%s\n' '9. pmbootstrap install'
    printf '%s\n' '10. pmbootstrap qemu --image-size=2G -m 2048 --no-gl'
    printf '%s\n' '11. ssh -p 2222 user@127.0.0.1'
    return 0
}
main "$@"
