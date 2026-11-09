#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# shellcheck shell=sh
# Plan by default. --guest boots a tiny initramfs. It does not boot postmarketOS or OPOS.
set -eu

usage() {
  printf '%s\n' 'usage: qemu-smoke.sh [--help|--plan|--guest]'
  printf '%s\n' '--plan (default) prints the postmarketOS QEMU steps and does not boot or flash.'
  printf '%s\n' '--guest boots a tiny initramfs in qemu-system-x86_64 and is not an OPOS image boot.'
  printf '%s\n' 'Physical-device flashing is untested and is not performed by this script.'
}

plan() {
  printf '%s\n' 'qemu-smoke: plan only. This does not boot postmarketOS, flash hardware, or download images.'
  printf '%s\n' 'Physical-device flashing is untested.'
  printf '%s\n' '1. COPYFILE_DISABLE=1 OPOS_LOCAL_SOURCE=1 sh packaging/pmos/stage-aports.sh <pmaports-checkout>'
  printf '%s\n' '2. pmbootstrap config aports <pmaports-checkout>'
  printf '%s\n' '3. pmbootstrap config device qemu-aarch64'
  printf '%s\n' '4. pmbootstrap config ui opos'
  printf '%s\n' '5. pmbootstrap init'
  printf '%s\n' '6. pmbootstrap checksum opos postmarketos-ui-opos'
  printf '%s\n' '7. pmbootstrap build opos'
  printf '%s\n' '8. pmbootstrap build postmarketos-ui-opos'
  printf '%s\n' '9. pmbootstrap install'
  printf '%s\n' '10. pmbootstrap qemu --image-size=2G -m 2048 --no-gl'
  printf '%s\n' 'A guest smoke (--guest) boots a tiny initramfs and is not an OPOS or postmarketOS boot.'
}

pick_kernel() {
  if [ -n "${OPOS_QEMU_KERNEL:-}" ]; then
    printf '%s\n' "$OPOS_QEMU_KERNEL"
    return 0
  fi
  found=""
  for candidate in /boot/vmlinuz-*; do
    if [ -f "$candidate" ]; then
      found=$candidate
    fi
  done
  printf '%s\n' "$found"
}

guest() {
  qemu=$(command -v qemu-system-x86_64 || true)
  busybox=${OPOS_QEMU_BUSYBOX:-$(command -v busybox || true)}
  kernel=$(pick_kernel)
  if [ -z "$qemu" ] || [ ! -x "$qemu" ] || [ -z "$busybox" ] || [ ! -x "$busybox" ] || [ -z "$kernel" ] || [ ! -f "$kernel" ]; then
    printf '%s\n' 'qemu-smoke: --guest needs qemu-system-x86_64, an executable busybox, and a vmlinuz (set OPOS_QEMU_KERNEL)' >&2
    return 1
  fi
  if [ ! -r "$kernel" ]; then
    printf '%s\n' "qemu-smoke: kernel is not readable: $kernel" >&2
    printf '%s\n' 'qemu-smoke: copy it to a readable path and set OPOS_QEMU_KERNEL. This smoke does not use sudo.' >&2
    return 1
  fi
  log=${OPOS_QEMU_SMOKE_LOG:-qemu-guest-smoke.log}
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  root=$work/root
  mkdir -p "$root/bin"
  cp "$busybox" "$root/bin/busybox"
  chmod 755 "$root/bin/busybox" "$root"
  cat > "$root/init" <<'EOF'
#!/bin/busybox sh
/bin/busybox echo OPOS_QEMU_GUEST_SMOKE_OK
exec /bin/busybox poweroff -f
EOF
  chmod 755 "$root/init"
  (
    cd "$root"
    find . | cpio -o -H newc > "$work/initrd.img"
  )
  if [ ! -s "$work/initrd.img" ]; then
    printf '%s\n' 'qemu-smoke: initramfs is empty' >&2
    return 1
  fi
  {
    printf '%s\n' '# OPOS QEMU guest smoke'
    printf '%s\n' "# date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '%s\n' "# uname: $(uname -srm)"
    printf '%s\n' "# qemu: $("$qemu" --version | head -n 1)"
    printf '%s\n' "# kernel: $kernel"
    printf '%s\n' "# busybox: $busybox"
    printf '%s\n' '# note: This is not a postmarketOS or OPOS UI boot. Physical-device flashing was not performed.'
  } > "$log"
  set +e
  timeout 90 "$qemu" \
    -machine accel=tcg \
    -cpu max \
    -m 256 \
    -kernel "$kernel" \
    -initrd "$work/initrd.img" \
    -append "console=ttyS0 rdinit=/init panic=-1" \
    -display none \
    -serial stdio \
    -no-reboot \
    -net none >> "$log" 2>&1
  qemu_rc=$?
  set -e
  if ! grep -q 'OPOS_QEMU_GUEST_SMOKE_OK' "$log"; then
    printf '%s\n' "qemu-smoke: guest did not print OPOS_QEMU_GUEST_SMOKE_OK (qemu exit $qemu_rc); log $log" >&2
    return 1
  fi
  printf '%s\n' "qemu-smoke: guest printed OPOS_QEMU_GUEST_SMOKE_OK (qemu exit $qemu_rc); log $log"
  printf '%s\n' 'qemu-smoke: this is not a postmarketOS or OPOS UI boot. Physical-device flashing is untested.'
}

if [ "$#" -gt 1 ]; then
  usage >&2
  exit 2
fi
mode=${1:---plan}
case "$mode" in
  --help|-h)
    usage
    ;;
  --plan)
    plan
    ;;
  --guest)
    guest
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
