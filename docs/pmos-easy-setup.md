<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Easy setup on postmarketOS

These scripts belong to the hackathon reference, not the Surface Duo OS. That OS interface is not in this repository. The scripts in `packaging/pmos/easy/` do not install postmarketOS, do not reboot a phone, and do not verify a multi-reboot setup. `packaging/pmos/easy/easy-install.sh` prints a plan and exits unless you pass `--flash`. Physical-device flashing is untested. The QEMU development workflow is [pmos.md](pmos.md). `packaging/pmos/qemu-smoke.sh` is a separate plan and tiny-guest smoke; it does not flash hardware.

## Reboot warning

`packaging/pmos/easy/confirm-reboot.sh` prints this line and nothing else until you answer:

> WARNING: this script does not reboot the phone or verify a multi-reboot setup. Physical-device flashing is untested. Do not unplug a device you choose to flash.

With no arguments it then prints `Type reboot and press Enter to continue:`. The word `reboot` only lets the script exit 0. The script does not reboot the computer or a phone, and it does not check that a reboot happened. Any other reply, including an empty one, prints `aborted` and exits 1. `--yes` skips the prompt and exits 0 after the warning. Any other argument prints `usage: confirm-reboot.sh [--yes]` and exits 2.

Do not unplug a device you choose to flash. This repository has not flashed a physical phone.

## What `easy-install.sh` does

Usage: `sh packaging/pmos/easy/easy-install.sh [--flash] [--yes] IMAGE`

Without `--flash` the script prints three lines and exits 0:

1. `step 1: sh <this-directory>/image-prep.sh --check IMAGE`
2. `step 2: sh <this-directory>/confirm-reboot.sh`
3. `step 3: skipped (no --flash)`

That plan does not run `image-prep.sh`, `confirm-reboot.sh`, or `pmbootstrap`. It does not read or write `IMAGE`. It does not create, modify, or delete files. A missing image still exits 0 in plan mode.

With `--flash` it does three things, in order, and stops on the first failure (`set -e`):

1. `sh image-prep.sh --check IMAGE`
2. `sh confirm-reboot.sh`, or `sh confirm-reboot.sh --yes` when `--yes` was passed
3. `pmbootstrap flasher flash_rootfs` with no further arguments

The image path is only given to `image-prep.sh --check`. It is not passed to `pmbootstrap`. `flash_rootfs` uses the rootfs pmbootstrap already installed, which can be a different file from `IMAGE`. The script prints `step 3: flashing (physical-device flashing is untested; pmbootstrap uses its installed rootfs, not the checked image path)` before that command. Physical-device flashing is untested. This repository has not verified that the command reboots a phone once, several times, or at all.

## What `image-prep.sh` does

Usage: `sh packaging/pmos/easy/image-prep.sh --check IMAGE`

`--check` is required. The path must be an existing file. The script reads the first four bytes and prints `format: sparse` when they are `3a ff 26 ed`, otherwise `format: raw`. It does not convert the image, write it, or flash it. A missing file prints `no such image` and exits 2.

## First boot

`packaging/pmos/easy/opos-firstboot.sh` is the packaged `opos-firstboot` command. `OPOS_STATE_DIR` defaults to `/var/lib/opos`, `OPOS_TDM` to `tinydm-set-session`, and `OPOS_SESSION` to `/usr/share/wayland-sessions/cage-ui.desktop`.

If `firstboot-done` already exists in the state directory, it prints `first boot: already done` and exits 0 without changing the session. Otherwise it creates the state directory, runs `tinydm-set-session -s` on the session file, creates `firstboot-done`, and prints `kiosk: ON`. It does not reboot. It does not record a previous session, so a later `opos-kiosk-off` will not find one unless something else wrote `prev-session`.

## Undo the kiosk

Run `opos-kiosk-off` as root. The installed command is `packaging/pmos/easy/opos-kiosk-off.sh`.

If a previous session was recorded, it restores that session after a reboot. If no previous session was recorded, it disables tinydm for the next boot using `rc-update del tinydm default` on OpenRC or `systemctl disable tinydm.service` on systemd. It does not stop the current kiosk session; reboot to apply the change.

The command returns a failure if restoring the session or disabling tinydm fails, or if no supported service manager is available. Use `opos-kiosk-off --dry-run` to print the plan without changing the session or services.

## Limits

- Physical-device flashing is untested.
- These scripts do not implement or verify a multi-reboot setup sequence.
- Plan mode (`easy-install.sh` without `--flash`) makes no changes.
- The November 3, 2026 postmarketOS QEMU boot in [pmos.md](pmos.md) is a separate virtual-machine record. These scripts do not repeat it.
