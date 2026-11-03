<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Duo USB install

This is a historical Droidian development guide, not an installation or stock-restore procedure for the current source export. Duo device work remains paused. The formatting, flashing and boot sections below are incomplete historical records and must not be executed without a separately verified device-specific plan. For the current desktop and QEMU scope, see [README.md](../README.md).

## Model access through a reverse tunnel

Run SSH on the PC so it connects to the Duo:

ssh -N -R 1236:127.0.0.1:1236 <duo-user>@<duo-address>

Keep the PC model listening on 127.0.0.1:1236, then on the Duo open a drop-in for the user service by running `systemctl --user edit opos-server.service`, add `[Service]` and `Environment=OPOS_LAN_URL=http://127.0.0.1:1236/v1` in the drop-in, save the drop-in, then run `systemctl --user restart opos-server.service`. OPOS reads OPOS_LAN_URL only from the service environment.

A PC reboot or USB replug drops the tunnel, so restart the command after either event. Add a PC logon task that retries every 10 seconds and rediscovers the Duo's current link-local IPv6 address and interface scope before reconnecting.

## No curl on the phone

Check the phone service with stock `python3`:

python3 -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8765/").status)'

python3 -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8765/?demo=1").status)'

A healthy running service returns `200` for both requests.

## Turn on the helper

“The helper starts hidden each time OPOS starts. Open the OPOS page with /?demo=1 in the phone’s browser to show the demo controls. Tap Show helper to show Why?, Plan and Ask; tap Hide helper to hide them. While the helper is hidden, these actions do not contact a model. An answer from the model on your computer takes about 30 to 70 seconds; Cancel stops waiting.”

## Push a build with push-to-duo.sh

From a checkout on the computer with the USB link, run `sh deploy/push-to-duo.sh --dry-run USER@fe80::1%8` to preview, then run the same command without `--dry-run`.
The address, including the zone after `%`, is the phone's link-local address as seen from that computer, and the zone can change when the cable is replugged.
The script archives the git ref (HEAD by default), copies it to the phone, moves any existing `~/opos` to `~/opos.bak-<timestamp>`, unpacks the archive and runs `sh install.sh --no-launch --yes`.
Afterwards it prints a PATH note and the output of `opos status`.
`running: no` is expected until the service is started with `opos run --defaults`.
It never edits `~/.ssh/config`.

## Rootfs install through TWRP
Droidian root is the file rootfs.img inside the ext4 userdata filesystem mounted at /data.
Never flash the image onto userdata; that produces Halium rootfs is empty.
rootfs.img must be raw ext4; convert an Android sparse image to raw before proceeding.
Run `sh deploy/duo-rootfs-install.sh TWRP_IMG DROIDIAN_BOOT_IMG ROOTFS_IMG` from the computer; without `--yes` it only prints the steps without executing them, and with `--yes` before the three image paths it executes them.
During execution it compares the SHA-256 hash and byte size of `/data/rootfs.img` against the local image and stops with `verify failed` if either differs.
Step 1 erases everything on userdata.
1. Format userdata with fastboot format:ext4 userdata to erase it.
2. RAM-boot TWRP with fastboot boot, then mount userdata at /data.
3. Push the image with adb push to /data/rootfs.img.
4. Compare SHA-256 hashes and stop on mismatch.
5. Return to the bootloader with a single fastboot boot; stop on crash.

## Rebooting safely / recovering

opos run prints a warning on Surface Duo because a RAM-booted kernel does not survive a normal restart.

If the kernel was RAM-booted from a PC with fastboot boot, do not run reboot or systemctl reboot on the phone. Doing so starts the stock kernel and strands the phone.

To restart safely:
1. On the phone, run `reboot bootloader`.
2. Re-arm the misc brake.
3. RAM-boot once from the PC with `fastboot boot`.

The repository does not yet record the boot image or the misc-brake command, so keep the ones you used for the first boot on the PC.

## Button steps

The battery must be at 20 percent or more before any boot attempt.

1. Hold Power for about 20 seconds until the screen stays dark; keep holding through any flash.
2. With the phone off and connected by USB, hold Volume-down first, then add Power.
3. Keep both held through the Surface logo and the orange 'unlocked' warning.
4. Release Power when the screen changes and keep Volume-down held until Boot Select appears.

Boot Select IS the fastboot screen and has no fastboot label. Never press Power alone at the orange warning. Never pick Start, Recovery Mode or Factory data reset. If stock recovery displays Cannot load Android system, power off and repeat the sequence.

Never run `systemctl reboot` or `reboot` on a RAM-booted kernel; use `reboot bootloader`. Allow one RAM boot per confirmed attempt after completing D019 confirmation checks. If it crashes, stop and do not retry.

A factory reset restores stock encrypted Android userdata. Before installation, reformat userdata to ext4 with `fastboot format:ext4 userdata`, following D019 device-selection precautions. This erases userdata.

Duo device work stays paused.

## Convert and verify a sparse image before flashing
Before flashing a large raw image, run `python3 deploy/sparse_image.py convert <raw-image> <sparse-image>` to create a sparse copy.
Run `python3 deploy/sparse_image.py check <raw-image> <sparse-image>` to expand the sparse contents as a stream and compare sha256 with the original, without creating another raw file.
Flash the sparse copy only when check succeeds and the round-trip sha256 matches; otherwise stop and do not flash.
fastboot 37.0.1 on Windows reportedly crashes with bad_alloc on a 5.7 GB raw ext4 image.

## Open OPOS full-screen at login
On phone shells such as Phosh, run sh install.sh --kiosk --yes to add a user-level login autostart that opens the OPOS page full-screen on port 8765 of the phone.
Chromium is the default browser and must be installed; to use an installed Firefox instead, run OPOS_KIOSK_BROWSER=firefox sh install.sh --kiosk --yes.
This needs no root and changes nothing about boot.
The entry is opos-kiosk.desktop in ~/.config/autostart; remove it with sh install.sh --uninstall or by deleting that file.
Run opos run --defaults once so the user service that serves the page starts at login.

## Surface Duo USB link

The phone chooses the USB subnet; inspect the PC adapter rather than assuming a range. The PC may receive only 169.254.x without a DHCP lease and mDNS or IPv4 sweeps may find nothing.

On Windows identify the USB adapter and its ifIndex:
```
Get-NetIPConfiguration | Where-Object {$_.NetAdapter.Status -eq 'Up'}
Get-NetNeighbor -AddressFamily IPv6 | Where-Object {$_.InterfaceIndex -eq $ifIndex}
```
The phone appears as a fe80:: neighbor on that interface. Note the InterfaceIndex used for link-local scope.

Connect with link-local IPv6:
```
ssh user@fe80::1%8
scp -P 22 file user@[fe80::1%8]:/path
```
%8 selects the local interface; replace 8 with the ifIndex shown above.

SSH config example:
```
Host duo
    HostName fe80::1%%8
    User user
    Port 22
```
In an SSH config file %% becomes a literal % after expansion.

ifIndex can change after replugging the USB cable, so re-run discovery.

Discovery/connect checklist:
- Confirm USB adapter is up with Get-NetIPConfiguration
- Find phone fe80:: neighbor with Get-NetNeighbor filtered to that ifIndex
- Use ssh and scp with [fe80::1%8] syntax and matching interface scope
- Re-check ifIndex after replug

