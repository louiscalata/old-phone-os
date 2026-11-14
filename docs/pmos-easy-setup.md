<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Easy setup on postmarketOS

## Reboot warning

> WARNING: the phone will reboot several times during setup. Do not unplug it or press buttons until it says Done.

`confirm-reboot.sh` prints the warning above. The scripts in this repository do
not implement or verify a multi-reboot setup sequence. Do not unplug the phone
during setup.

## The helper does not flash

`easy-install.sh` issues no erase or flash command, and no answer or flag
changes that: it cannot confirm which partition a port's rootfs belongs on, or
that a flasher writes the image it checked. Flash with the commands in the
port's documentation. This is not the Surface Duo Droidian rootfs-in-userdata
route.

`sh packaging/pmos/easy/easy-install.sh IMAGE` prints a plan: the image check,
then `step 2: none`, with the reason. With `--flash` before `IMAGE`, the helper
first says that it does not flash, warns that flashing with the port's own
commands erases the partition they write, and then checks that the selected
image is a readable, non-empty file and reads its header to report raw or sparse
format. This identifies the format; it does not verify that the image suits the
device. A failed check stops with exit status 2 before the selected path is
printed.

After a passing check, `--flash` prints the selected path and refuses with
`refusing to flash` and exit status 1. It asks for no typed confirmation, because
nothing follows it. It no longer runs `pmbootstrap flasher flash_rootfs`, which
flashed a separate pmbootstrap build instead of the checked image, and it runs no
`fastboot` command. `--yes` is no longer accepted.

Device installation remains **NOT_RUN**. Use [pmos.md](pmos.md) for the current
QEMU development workflow.

## Undo the kiosk

Run `opos-kiosk-off` as root.

If a previous session was recorded, it restores that session after a reboot. If no previous session was recorded, it disables tinydm for the next boot using `rc-update del tinydm default` on OpenRC or `systemctl disable tinydm.service` on systemd. It does not stop the current kiosk session; reboot to apply the change.

The command returns a failure if restoring the session or disabling tinydm fails, or if no supported service manager is available. Use `opos-kiosk-off --dry-run` to print the plan without changing the session or services.
