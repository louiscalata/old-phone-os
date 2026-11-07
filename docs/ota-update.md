<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# OPOS over-the-air updates (design)

Status, November 8, 2026: **design only; nothing here is implemented yet.** This document is the contract that the
updater, the client and the release tools are built against. It describes the OPOS OS image for the Surface Duo
(see [packaging/duo-os/README.md](../packaging/duo-os/README.md)); the Android/Termux deploy is not covered.

## What can and cannot be updated

The Duo's bootloader refuses writes to the boot partitions (`Device Error`) and the port's own rules forbid them, so the
kernel and initramfs come from the PC at every RAM-boot. An over-the-air update therefore cannot replace the kernel, the
initramfs, the vendor layer or the Debian base. It updates what OPOS owns:

| Updatable over the air | Not updatable over the air (new image, from the PC) |
|---|---|
| the OPOS app tree (`/opt/opos`: `src/opos`, web assets) | kernel, initramfs, boot/vbmeta, the Halium vendor layer |
| OPOS-owned overlay files: `usr/libexec/opos/*`, `usr/sbin/opos-*`, `etc/systemd/system/opos*.service`, `etc/default/opos*`, `etc/opos/*` except `etc/opos/update/` | Debian packages, the base rootfs, `/etc/opos/update/` (trust keys), the root updater itself |

Updates live in the rootfs image on userdata, so they survive restarts of the RAM-booted OS. A new rootfs image build starts
from its own baked-in revision again.

## Trust model

- Bundles are signed with an Ed25519 key through `ssh-keygen -Y sign` (namespace `opos-update`); verification uses
  `ssh-keygen -Y verify` against `/etc/opos/update/allowed_signers` (root-owned, baked into the image). No Python
  dependency, and the same tool signs on the PC. Allowed-signers line: `opos-release ssh-ed25519 AAAA...`.
- **Root verifies; the unprivileged side only delivers.** OPOS's server runs as `droidian` (no passwordless sudo). It
  drops files into the inbox; a systemd path unit starts a root oneshot that copies the file into root-owned staging
  *before* verifying it (no time-of-check/time-of-use window), then verifies, applies and health-checks.
- The root updater lives outside the updatable tree (`/usr/libexec/opos-updater/opos-updater`, standard library only,
  small) and is changed only by a new image.
- No key in the image means nothing is accepted: `build-image.sh` installs signers only when given
  `OPOS_UPDATE_SIGNERS=<file>` (public keys). The repository carries public keys only. The owner generates and keeps the
  release key (`ssh-keygen -t ed25519`); a development key may sign development images.
- Replay and downgrade: every bundle has a strictly increasing integer `serial`; the updater refuses `serial` not greater
  than the current one and any serial in its `failed` list. A downgrade is a local root action (`opos-updater rollback`),
  never a bundle.

## Bundle format (`opos-<serial>.opos-update`)

A plain (uncompressed) tar with exactly three regular members, in this order: `manifest.json`, `manifest.json.sig`,
`payload.tar.gz`. `manifest.json` is UTF-8 JSON (the signature covers its exact bytes):

```json
{
  "format": 1,
  "serial": 7,
  "version": "0.2.0",
  "revision": "<40-hex git commit>",
  "created": "2026-11-08T12:00:00Z",
  "requires_base": 1,
  "scope": "app",
  "payload": {"sha256": "<hex>", "size": 123456},
  "files": [{"path": "opt/opos/src/opos/web.py", "sha256": "<hex>", "size": 1234, "mode": "0644"}],
  "notes": "short text"
}
```

- `scope` is `app` (apply now: restart `opos-server`, reload the kiosk page; the compositor and `opos.service` are never
  restarted, because restarting the session has reset this phone before) or `boot` (staged; applied at the next boot by
  `opos-update-boot.service`, before `opos-server.service` and `opos.service` start).
- `requires_base` is an integer compared with `/etc/opos/base-version` (the image's base compatibility level).
- `payload.tar.gz` holds exactly the files in `files`: regular files only, relative names, no `..`, no absolute paths,
  no links, devices or directories, no duplicates, each path inside the allowlist above, modes `0644` or `0755` only,
  at most 20,000 files and 256 MiB unpacked, and every size/hash must match the signed manifest.
- `opt/opos/<rest>` installs into the new release directory as `<rest>`; overlay paths replace the file at `/<path>`.

Hosted index `latest.json` (unsigned convenience; trust comes only from verifying the bundle):
`{"serial": 7, "version": "0.2.0", "url": "opos-7.opos-update", "sha256": "<bundle hash>", "size": 123456}`; `url` is
relative to the index's own URL.

## On-device layout and state

```
/opt/opos -> /opt/opos-releases/<serial>-<rev12>        symlink, switched atomically (rename)
/opt/opos-releases/<serial>-<rev12>/{src/opos/..., REVISION, overlay-backup/}
/var/lib/opos-update/{inbox/, staging/, state.json, status.json, lock}      inbox: mode 2770 root:droidian
/etc/opos/update/allowed_signers        /etc/opos/base-version        /etc/opos/update.json
/usr/libexec/opos-updater/opos-updater  root trust anchor
```

`state.json` (root-written): `{"base": 1, "current": {"serial", "version", "revision", "release"}, "previous": [...],
"failed": [serials], "pending_boot": null}`. `status.json` (world-readable, for the UI): `{"phase": "idle|verifying|
applying|checking|done|rolled-back|failed", "message", "serial", "time"}`. The image's initial release is serial 0 built
from the revision in `/opt/opos/REVISION`; `build-image.sh` creates the release-directory layout.

## Apply sequence (root)

1. Take the lock; move the newest `*.opos-update` from the inbox into staging (`O_NOFOLLOW`, regular file only, size cap).
2. Verify: tar structure; signature; manifest schema; `payload` hash/size; `serial` > current and not in `failed`;
   `requires_base`; free space at least twice the unpacked size.
3. Unpack the payload into `releases/.staging-<serial>` with the checks above; verify every file hash and mode; add
   `REVISION`; rename to its final release directory.
4. Smoke test: start the new tree's server as `droidian` on `127.0.0.1:18765` (not the live port); `GET /` must answer
   200 within 20 s; stop it.
5. Overlay files (scope `app`): write `<dest>.new`, fsync, rename; keep the originals in `overlay-backup/`;
   `systemctl daemon-reload` if units changed. Switch `/opt/opos` atomically. Restart `opos-server`; kill the kiosk's
   Chromium so its loop restarts it on the new server. Scope `boot`: record `pending_boot` and stop here.
6. Health check for up to 45 s: `systemctl is-active opos-server`; `GET /` 200; `GET /api/state` (with the page's
   token) returns JSON with a `battery` key. On failure: restore overlay backups, switch the symlink back, restart,
   add the serial to `failed`, set `status.phase` to `rolled-back`.
7. Keep the current and two previous releases; remove older ones. Write state and status (fsync, rename) at each phase.

Boot-time: `opos-update-boot.service` applies `pending_boot` (steps 5-6 without restarts); `opos-update-confirm.service`
checks health after boot and rolls back after two consecutive failed boots (counter in `state.json`). A rollback request
is an empty file `inbox/rollback.request` or `opos-updater rollback`.

## Client (unprivileged, in OPOS)

- Source is opt-in. With no `/etc/opos/update.json` nothing is ever fetched ($0 and no account by default, as the rest of
  OPOS). Config: `{"url": "https://.../latest.json", "auto_check": false, "allow_lan_http": false}`; `https` only, except
  loopback or private-LAN `http` when `allow_lan_http` is true; no redirects to another host; download size cap 256 MiB;
  no background polling unless `auto_check` is true.
- `check` fetches `latest.json` and compares serials; `download` fetches the bundle to `inbox/<name>.partial`, fsyncs,
  renames to `.opos-update`; progress and results come from `status.json`. It never verifies for trust: root does.
- UI: a separate small page at `/update` (not the main page, which is at its size budget) showing the running
  version/revision/serial, check, download/apply, the last status and a rollback request. Existing token, origin and
  rate-limit rules for POSTs apply. Without `/var/lib/opos-update` (desktop demo, Android) the page says updates are not
  available on this install.

## Developer path (no Wi-Fi, no host)

`tools/opos-release.py` builds and signs a bundle from a git revision (`build`, `verify`, `index`). `deploy/duo-push-update.ps1`
copies a bundle into the phone's inbox over the USB network with the dev key and prints `status.json` until it settles. This is
also how app-level changes reach the running phone without a new rootfs image.

## Limits

No kernel, initramfs, vendor-layer or Debian updates; no update of the updater or its trust keys over the air; no
Android/Termux support; not tested on the phone until the updater runs there; there is no hosted update server yet.
