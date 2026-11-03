<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Easy setup on postmarketOS

## Reboot warning

the phone will reboot several times during setup

`confirm-reboot.sh` prints the warning above. The scripts in this repository do not implement or verify a multi-reboot setup sequence: `easy-install.sh` prints a plan by default and invokes `pmbootstrap flasher flash_rootfs` only with `--flash`. Use [pmos.md](pmos.md) for the current QEMU development workflow.

Do not unplug the phone during setup.

## Undo the kiosk

Run `opos-kiosk-off` as root.

If a previous session was recorded, it restores that session after a reboot. If no previous session was recorded, it disables tinydm for the next boot using `rc-update del tinydm default` on OpenRC or `systemctl disable tinydm.service` on systemd. It does not stop the current kiosk session; reboot to apply the change.

The command returns a failure if restoring the session or disabling tinydm fails, or if no supported service manager is available. Use `opos-kiosk-off --dry-run` to print the plan without changing the session or services.
