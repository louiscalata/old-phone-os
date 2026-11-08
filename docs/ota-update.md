<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# OPOS over-the-air updates

Status, November 8, 2026: **implemented (root updater, client, release tools) and tested on Linux with stand-ins for systemd, runuser and the browser; not tested on the phone.**
`tests/test_ota_roundtrip.py` builds real signed bundles with `tools/opos-release.py`, fetches them with the client from a
loopback server, and has the updater apply, health-check, refuse and roll them back; the unit tests of each part sit
beside it. What no test reaches: the systemd path unit and the unit hardening on systemd 257, the real `runuser`/PAM,
the kiosk browser coming back after its restart, and the phone's kernel, flash and clock. Expect to find problems at the first
real run. This document is the contract that the updater, the client and the release tools were built against; "As built"
below lists where the code is stricter or more specific than the text. It describes the OPOS OS image for the Surface Duo
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
- **The one exception: the rollback request.** The Update page's "Request rollback" is an empty file, `inbox/rollback.request`,
  and root honours it although it carries no signature, so any process that can write the inbox (anything running as
  `droidian`, which is also what OPOS's own server is) can send the phone back to its previous release. That is a
  downgrade to code the owner signed and the phone ran, never to anything else, and it is limited: root honours **one
  requested step back per applied update** (`state.json` `stepped_back`; an applied update clears it), and a requested
  rollback does **not** burn the serial it leaves, so installing the same signed release again undoes it. What the limit does not
  stop: one step back per update, by anyone with that uid; and a process that keeps `opos-server` down through the
  45-second health window of a real update gets that update rolled back and its serial burned (the owner then ships the
  fix under a new serial). A process that can do that can already kill the UI at will, so it is accepted, not fixed.

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
"failed": [serials], "pending_boot": null, "stepped_back": null}`. `status.json` (world-readable, for the UI): `{"phase": "idle|verifying|
applying|checking|done|rolled-back|failed", "message", "serial", "time"}`. The image's initial release is serial 0 built
from the revision in `/opt/opos/REVISION`; `build-image.sh` creates the release-directory layout.

## Apply sequence (root)

1. Take the lock; move the newest `*.opos-update` from the inbox into staging (`O_NOFOLLOW`, regular file only, size cap).
2. Verify: tar structure; signature; manifest schema; `payload` hash/size; `serial` > current and not in `failed`;
   `requires_base`; free space at least twice the unpacked size.
3. Unpack the payload into `releases/.staging-<serial>` with the checks above; verify every file hash and mode; add
   `REVISION`; rename to its final release directory.
4. Smoke test: start the new tree's server as `droidian` on a loopback port the kernel picks (`--port 0`, never the live
   port and never a fixed one that another process could hold); the server prints the address it got and the updater
   reads it from its own log; `GET /` must answer 200 within 20 s; stop it.
5. Overlay files (scope `app`): write `<dest>.new`, fsync, rename; keep the originals in `overlay-backup/`;
   `systemctl daemon-reload` if units changed. Switch `/opt/opos` atomically. Restart `opos-server`; kill the kiosk's
   Chromium so its loop restarts it on the new server. Scope `boot`: record `pending_boot` and stop here.
6. Health check for up to 45 s: `systemctl is-active opos-server`; `GET /` 200; `GET /api/state` (with the page's
   token) returns JSON with a `battery` key. On failure: restore overlay backups, switch the symlink back, restart,
   add the serial to `failed`, set `status.phase` to `rolled-back`.
7. Keep the current and two previous releases; remove older ones. Write state and status (fsync, rename) at each phase.

Boot-time: `opos-update-boot.service` applies `pending_boot` (steps 5-6 without restarts); `opos-update-confirm.service`
checks health after boot and rolls back after two consecutive failed boots (counter in `state.json`). A rollback request
is an empty file `inbox/rollback.request` (limited, see the trust model) or `opos-updater rollback` (root, not limited).

## Client (unprivileged, in OPOS)

- Source is opt-in. With no `/etc/opos/update.json` nothing is ever fetched ($0 and no account by default, as the rest of
  OPOS). Config: `{"url": "https://.../latest.json", "auto_check": false, "allow_lan_http": false}`; `https` only, except
  loopback or private-LAN `http` when `allow_lan_http` is true; no redirects to another host; download size cap 256 MiB;
  no background polling unless `auto_check` is true.
- `check` fetches `latest.json` and compares serials; `download` fetches the bundle to `inbox/<name>.partial`, fsyncs,
  renames to `.opos-update`; progress and results come from `status.json`. It never verifies for trust: root does.
- UI: a separate small page at `/update` (not the main page, which is at its size budget; the main page has one link to it,
  "Updates" in Settings under About, and the page's "Back to the phone" returns) showing the running
  version/revision/serial, check, download/apply, the last status and a rollback request. Existing token, origin and
  rate-limit rules for POSTs apply. Without `/var/lib/opos-update` (desktop demo, Android) the page says updates are not
  available on this install.

## Developer path (no Wi-Fi, no host)

`tools/opos-release.py` builds and signs a bundle from a git revision (`build`, `verify`, `index`). `deploy/duo-push-update.ps1`
copies a bundle into the phone's inbox over the USB network with the dev key and prints `status.json` until it settles. This is
also how app-level changes reach the running phone without a new rootfs image.

## As built

Where the code is stricter or more specific than the text above. Every item is a tightening; the text above is the minimum.

- **Layout.** `/opt/opos` is a *relative* symlink (`opos-releases/<serial>-<rev12>`, so it also resolves inside a mounted
  image); `<rev12>` is the first 12 hex digits of the revision; the image's own release is `0-<rev12>`. A release directory
  holds the app tree, `REVISION`, `manifest.json`, `manifest.json.sig`, `overlay/` and `overlay-backup/`; those five names
  are reserved and a bundle may not write them. The current release and two previous ones are kept.
- **Bundle contents.** A bundle is a complete application tree (`opt/opos/src/opos/**`, `LICENSE`, `THIRD_PARTY.md`, as
  `git archive` gives it), never a delta; a bundle with only overlay files is refused. Overlay paths are single-level and
  exact, as in the table above. Refused although a glob would match them: `etc/opos/update.json`, `etc/opos/base-version`,
  anything named `opos-update*` in `usr/sbin`, `etc/systemd/system` and `etc/default`, `usr/libexec/opos-updater/**`, and
  names ending `.new`. A path component matches `[A-Za-z0-9_][A-Za-z0-9._+-]*` (no dotfiles, spaces, `@` or non-ASCII).
- **Manifest.** `requires_base` is a minimum (accepted when not above `/etc/opos/base-version`); `notes` is optional (at most
  1,000 characters); no other keys; duplicate JSON keys, `NaN` and a byte-order mark are refused; modes are the strings
  `"0644"` and `"0755"`; payload member names equal the manifest paths exactly and the tar header mode equals the manifest
  mode. The outer tar is plain, exactly the three members in order, and only zero padding follows. Limits: 20,000 files,
  256 MiB unpacked; the client and the release tool stop a bundle at 256 MiB, the updater at 272 MiB.
- **Inbox.** The client writes `opos-<serial>.opos-update.partial`, fsyncs, then renames it; `rollback.request` is an empty
  regular file. Root deletes every `*.opos-update` and `rollback.request` it looks at, accepted or not, removes `.partial`
  files older than a day, applies the newest bundle by modification time and discards the others; a symlink, FIFO or
  directory in the inbox is removed and reported (also when an unexpected error stops the run). The path unit matches
  only `*.opos-update` and `rollback.request`. `opos-update.service` has **no start limit**: every file dropped in the inbox
  is one start, and a limit would let any process that can write the inbox switch the service and the path unit off until
  root resets them. What bounds a flood instead: everything looked at is removed, and the updater lingers after each run
  (2 s; 30 s after a run that could not work: exit 1 or 75), so the path unit starts at most one run per few seconds.
- **Serials.** A serial must be above the running one and above a staged boot update, and not in `failed`. A rolled-back
  serial (after a failed health or boot check, `opos-updater rollback`, or recovered after an interrupted apply) is added to
  `failed` and never accepted again; a refused bundle, a failed smoke test and a rollback *requested through the inbox* are
  not (see the trust model: that one is limited to one step back per applied update, and installing the same signed release
  again undoes it). A newer app update supersedes a staged boot update, and a rollback cancels it.
- **State.** `state.json` also holds `schema`, `trial` (boot scope: the failed-boot counter), `stepped_back` (the serial the
  phone last went back from, until the next update is applied) and `txn` (a journal written
  before the first live change; the next run undoes an interrupted apply or finishes an interrupted rollback). `status.json`
  also holds `epoch`, and each bundle or request that is looked at rewrites it (the USB push script waits for it to change).
  Its `message` can contain text that came from a bundle or the inbox, so the UI escapes it. A bundle refused before its
  manifest is read (a bad signature, say) leaves `serial` empty.
- **Health and smoke tests.** Health: `systemctl is-active opos-server.service` is `active`; `GET /` on port 8765 answers
  200 and carries `<meta name="opos-token" content="...">`; `GET /api/state` with that token returns a JSON object with a
  `battery` key. Boot confirmation uses the same check. Smoke test: the new release's server runs as `droidian` through
  `runuser` with `--port 0`; `opos.web` prints `http://127.0.0.1:<port>/` once it listens (the port it really got, not the
  one asked for), the updater reads that line from its own root-owned log, and `GET /` on that port must answer 200 within
  20 seconds. There is no well-known port to hold or to answer on: a server that does not print its address fails the test.
  A process running as `droidian` could still kill the smoke server and answer on its port in the instant between the line
  and the request; the health check on the live port after the switch, and the rollback it triggers, are the net under that.
- **What the updater touches.** It asks systemd for `daemon-reload`, `restart opos-server.service` and `is-active
  opos-server.service` and nothing else, so never `opos.service`; it kills the kiosk's Chromium with `pkill`, and
  `opos-kiosk` waits for the server before every start of the browser. Exit codes: 0 done or nothing to do, 3 a bundle or
  request was refused or rolled back and 75 another run holds the lock (the unit accepts both: `SuccessExitStatus=3 75`),
  1 fatal, 2 usage.
- **Hosting.** The client follows a redirect only within one scheme, host and port, so a phone cannot fetch from GitHub
  release assets (served from another host); use a static site on one host. The image build takes
  `OPOS_UPDATE_SIGNERS=<public keys file>` (no key: nothing is accepted) and `OPOS_UPDATE_URL=<address of latest.json>` (no
  address: nothing is fetched); [the owner's how-to](ota-release-howto.md) covers the rest.

## Limits

No kernel, initramfs, vendor-layer or Debian updates; no update of the updater or its trust keys over the air; no
Android/Termux support; no revocation of a release key; not tested on the phone (see the status line); there is no hosted
update server yet. Any process running as `droidian` can still ask for one step back per update (trust model).
