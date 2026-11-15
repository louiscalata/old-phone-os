#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# PC ONLY. Boots an OPOS image in QEMU (aarch64 virt, emulated) and judges the serial log with boot-smoke-verdict.py.
# Its exit status is the verdict's: 0 pass, 1 fail, 3 timeout (no positive line), 2 bad arguments or a missing tool. It
# refuses to run on macOS. Nothing is written to the images: their drives are snapshot=on.
#
#   sh packaging/duo-os/qemu-boot-smoke.sh --kernel VMLINUZ [--initrd INITRD] --rootfs ROOTFS.img (--accept server-started | --expect ID) [--timeout S] [--log LOG]
#   sh packaging/duo-os/qemu-boot-smoke.sh --image DISK.img [--firmware AAVMF_CODE.fd] (--accept server-started | --expect ID) [--timeout S] [--log LOG]
#
# --accept server-started passes on systemd's own "Started opos-server.service" console line, which any image built
# from this checkout prints; nothing powers that guest off, so the run always lasts until the timeout. --expect ID
# requires the documented marker "OPOS_BOOT_OK ID server=ok" (ID: the SHA-256 of the image's /etc/opos/image.json),
# which no shipped unit prints yet. --kernel boots a generic arm64 kernel directly, with ROOTFS.img (build-image.sh's
# OUT) on virtio-blk and the kernel arguments below. --image boots a whole disk through the AAVMF firmware; its own
# boot entry then has to carry panic=-1, and opos.smoke=1 for the proposed marker (QEMU cannot add them). Nothing
# shipped reads opos.smoke=1; it is the gate the proposed marker unit would use. QEMU virt has no hwcomposer: the three
# units that need it are masked, so the run covers systemd ordering, the updater's boot step, the clock floor and the
# server, not the shell.
set -u
me='qemu-boot-smoke.sh'
die() { echo "$me: $1" >&2; exit 2; }
if [ "$(uname -s 2>/dev/null)" = Darwin ]; then
    die "refuses to run on macOS: the boot smoke test belongs to the PC session (Linux with qemu-system-aarch64)"
fi
case $0 in */*) here=${0%/*} ;; *) here=. ;; esac
here=$(cd "$here" && pwd) || die "cannot find the packaging folder"

kernel='' initrd='' rootfs='' image='' firmware='' expect='' accept='' timeout=900 log=''
while [ "$#" -gt 0 ]; do
    [ "$#" -ge 2 ] || die "$1 needs a value"
    case $1 in
        --kernel) kernel=$2 ;;
        --initrd) initrd=$2 ;;
        --rootfs) rootfs=$2 ;;
        --image) image=$2 ;;
        --firmware) firmware=$2 ;;
        --expect) expect=$2 ;;
        --accept) accept=$2 ;;
        --timeout) timeout=$2 ;;
        --log) log=$2 ;;
        *) die "unknown argument: $1" ;;
    esac
    shift 2
done
if [ -n "$image" ]; then
    [ -z "$kernel$initrd$rootfs" ] || die "--image cannot be combined with --kernel, --initrd or --rootfs"
    mode=image
else
    [ -n "$kernel" ] && [ -n "$rootfs" ] || die "give --kernel and --rootfs, or --image"
    mode=kernel
fi
if [ -n "$accept" ]; then
    [ -z "$expect" ] || die "give --accept or --expect, not both"
    [ "$accept" = server-started ] || die "--accept takes only server-started"
    judge="--accept server-started"
else
    [ -n "$expect" ] || die "give --accept server-started, or --expect ID (the SHA-256 of the image's /etc/opos/image.json)"
    judge="--expect $expect"
fi
case $expect in *[!0-9a-f]*) die "--expect must be 64 lowercase hex digits" ;; esac
case $timeout in '' | *[!0-9]* | 0*) die "--timeout must be whole seconds from 1 to 7200" ;; esac
[ "$timeout" -le 7200 ] || die "--timeout must be whole seconds from 1 to 7200"
for f in "$kernel" "$initrd" "$rootfs" "$image"; do
    [ -z "$f" ] || [ -f "$f" ] || die "no such file: $f"
    case $f in *,*) die "QEMU option values cannot hold a comma: $f" ;; esac
done

qemu=$(command -v qemu-system-aarch64) || die "qemu-system-aarch64 not found (Debian and Ubuntu: apt install qemu-system-arm)"
if [ "$mode" = image ]; then
    if [ -z "$firmware" ]; then
        for f in /usr/share/AAVMF/AAVMF_CODE.fd /usr/share/qemu-efi-aarch64/QEMU_EFI.fd /usr/share/edk2/aarch64/QEMU_EFI.fd /usr/share/qemu/edk2-aarch64-code.fd; do
            if [ -f "$f" ]; then firmware=$f; break; fi
        done
        [ -n "$firmware" ] || die "AAVMF firmware not found (Debian and Ubuntu: apt install qemu-efi-aarch64), or give --firmware"
    fi
    [ -f "$firmware" ] || die "AAVMF firmware not found: $firmware"
    case $firmware in *,*) die "QEMU option values cannot hold a comma: $firmware" ;; esac
fi

# A fresh log every run: a log left by an earlier run (which may hold a pass) is never read again.
if [ -z "$log" ]; then
    logdir=$(mktemp -d "${TMPDIR:-/tmp}/opos-boot-smoke.XXXXXX") || die "cannot make a log folder"
    log=$logdir/serial.log
fi
case $log in *,*) die "QEMU option values cannot hold a comma: $log" ;; esac
if [ -e "$log" ] || [ -L "$log" ]; then die "the log already exists; give a new path: $log"; fi
(set -C; : > "$log") || die "cannot create the log: $log"

# The units QEMU virt cannot run (no Android container, no vendor composer, so no phoc and no shell). Nothing else is masked.
masks="systemd.mask=lxc@android.service systemd.mask=android-service@hwcomposer.service systemd.mask=opos.service"
set -- -machine virt -cpu max -m 2048 -smp 2 -accel tcg -display none -no-reboot -serial "file:$log" -monitor none -nic none
if [ "$mode" = kernel ]; then
    set -- "$@" -kernel "$kernel"
    if [ -n "$initrd" ]; then set -- "$@" -initrd "$initrd"; fi
    set -- "$@" -drive "file=$rootfs,format=raw,if=virtio,snapshot=on" \
        -append "console=ttyAMA0 root=/dev/vda rw rootwait panic=-1 opos.smoke=1 $masks"
else
    set -- "$@" -bios "$firmware" -drive "file=$image,format=raw,if=virtio,snapshot=on"
fi

echo "$me: $mode mode, timeout ${timeout}s, serial log $log"
"$qemu" "$@" < /dev/null &
qpid=$!
trap 'kill "$qpid" 2>/dev/null; exit 130' INT
trap 'kill "$qpid" 2>/dev/null; exit 143' TERM
trap 'kill "$qpid" 2>/dev/null; exit 129' HUP
# The hard timeout: a guest that never powers off is ended, and its silence is judged as a timeout.
waited=0 ended=''
while kill -0 "$qpid" 2>/dev/null; do
    if [ "$waited" -ge "$timeout" ]; then
        ended=timeout
        kill "$qpid" 2>/dev/null
        n=0
        while kill -0 "$qpid" 2>/dev/null && [ "$n" -lt 10 ]; do sleep 1; n=$((n + 1)); done
        kill -9 "$qpid" 2>/dev/null
        break
    fi
    sleep 1
    waited=$((waited + 1))
done
wait "$qpid"
qrc=$?
trap - INT TERM HUP
if [ "$ended" = timeout ]; then
    echo "$me: qemu was ended by the timeout after ${timeout}s"
else
    echo "$me: qemu exited with status $qrc after about ${waited}s"
fi
# shellcheck disable=SC2086 # $judge is two words, checked above to hold no other characters.
python3 -I -B "$here/boot-smoke-verdict.py" --log "$log" $judge
exit $?
