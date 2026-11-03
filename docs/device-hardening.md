<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Device hardening runbook

Runbook for `deploy/harden-device.sh`, for the phone's owner.

## Before you start

- Back up the phone first.
- Keep a second SSH session or USB console open.
- You need root on the phone (`--apply` and `--rollback` refuse to run otherwise).
- The script was only tested against a fake root, never on the Duo.
- Each step needs the owner's yes; nothing here was applied to the phone.

## Check first

```sh
sudo deploy/harden-device.sh --check
```

- `check` is the default mode.
- Prints what would change (`would write ...`, `would run: ...`).
- Does not apply configuration or service changes. Firewall probing can create and remove a temporary candidate ruleset.

## Add your key first

```sh
sudo deploy/harden-device.sh --apply --add-key FILE
```

- `FILE` is your SSH public key file.
- Why: in `--apply` the ssh step stops with `no authorized key: run again with --add-key FILE` before changing anything when no key is present; in `--check` it only warns.
- Add the key first so key-only login does not lock the owner out.
- `FILE` must hold a valid public key (base64 field required, and `ssh-keygen -l` must accept it when `ssh-keygen` exists).
- When `--add-key` is supplied, the step refuses symlinked `.ssh` or `authorized_keys`.

## Apply

```sh
sudo deploy/harden-device.sh --apply
```

- Requires root.
- Prompts `type yes-apply to continue:`; type `yes-apply` exactly.
- Configuration files written through `write_file` are backed up under `/var/backups/opos-harden/<STAMP>/` and listed in `MANIFEST`. The SSH key addition, SSH-directory permissions and ownership, and password change are not backed up by that mechanism.
- Select steps:

```sh
sudo deploy/harden-device.sh --apply --only ssh,firewall
sudo deploy/harden-device.sh --apply --skip fde
sudo deploy/harden-device.sh --check --only fde
```

- `--only NAME,...` runs only the named steps.
- `--skip NAME,...` skips the named steps.
- Step names: `ssh`, `firewall`, `services`, `updates`, `fde`.
- `--install-packages` permits the updates step to install `unattended-upgrades`; the current firewall step does not install its backend packages.
- The firewall probes nftables and IPv4/IPv6 iptables-legacy. `OPOS_FW_BACKEND=auto` prefers usable nftables and otherwise uses usable iptables-legacy; `nft` and `iptables` explicitly select a backend. It stops when the selected backend is unusable, or when neither backend works in auto mode. A successful apply loads that backend's rules and enables `opos-firewall.service`.
- Unknown flags exit with usage (exit 2).

## What each step does

| step | change | files written |
|---|---|---|
| ssh | key-only login, no root login, `AllowUsers`, new password prompt | Managed SSH configuration is backed up; key additions, permissions, ownership and password changes are outside that backup mechanism. |
| firewall | Selects nftables or iptables-legacy, defaults to denying inbound traffic, permits SSH on the configured USB interfaces, loads the rules and enables `opos-firewall.service`; backend packages must already be installed. | Managed rule files, service definition and backend state are written through the backup mechanism. |
| services | `avahi`, `cups`, `rpcbind` off; running-but-disabled units are stopped as well | unit enable/disable entries recorded in `MANIFEST` as `enabled ...` / `disabled ...` |
| updates | Debian security updates only, sysctl hardening | any file it changes, backed up first and listed in `MANIFEST` |
| fde | read-only report only, changes nothing | none |

## Roll back

```sh
sudo deploy/harden-device.sh --rollback
```

- Requires root.
- Restores the newest backup under `/var/backups/opos-harden`.
- `restore ...` entries are copied back; `created ...` entries are removed; `enabled ...` / `disabled ...` entries are re-enabled / re-disabled.
- Then, as the script says, reload ssh and delete the nft table yourself if you changed them:

```sh
systemctl reload ssh; nft delete table inet opos_filter
```

## Full-disk encryption

- This step is a read-only report only; the script never enables encryption.
- Droidian encryption is opt-in and can leave a phone unbootable.
- The Duo boots from RAM so feasibility is unverified.
- The report checks the device marker, the encryption service package, and whether root is on a device-mapper volume.
- To get a verdict:

```sh
sudo deploy/harden-device.sh --only fde --check
```

- Read the report.
- Ask on the Droidian channel before enabling.
- The owner decides.

## Environment variables

| variable | default from the script |
|---|---|
| `OPOS_ROOT` | `/`; a different value normally selects test mode, in which the `run` helper skips commands, but `OPOS_HARDEN_TEST=0` overrides that mode. |
| `OPOS_HARDEN_TEST` | Unset; `0` or `1` explicitly overrides the derived test mode. |
| `OPOS_USER` | `droidian` (must match `[a-z_][a-z0-9_-]*`, else the script exits 1) |
| `OPOS_USB_IFACES` | `usb0 rndis0` |
| `OPOS_DISABLE_UNITS` | `avahi-daemon.service avahi-daemon.socket cups.service cups.socket cups-browsed.service rpcbind.service rpcbind.socket` |
| `OPOS_NFT` | Defaults to `nft`; overrides the binary used for backend probing, not the later `nft` apply command. |
