<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Releasing OPOS updates: the owner's how-to

How to make the release key, cut a signed update bundle, host it and push it to the Surface Duo OS image. The contract
(bundle format, trust model, what the phone does with a bundle) is [ota-update.md](ota-update.md); this page is the
release side, on your PC. The tools are `tools/opos-release.py` (build, verify, index) and `deploy/duo-push-update.ps1`
(copy to the phone over USB and wait for the result).

Status: the tools are tested on a PC with throw-away keys, and `tests/test_ota_roundtrip.py` feeds the bundles they build to
the phone's real updater code (with stand-ins for systemd, runuser and the browser). Nothing here has run on the phone;
see [Limits](#limits).

You need a PC (Windows or Linux) with Python 3.11 or newer, git, and the OpenSSH client (`ssh-keygen`, `ssh`, `scp`).
On Windows the client is under Settings, Optional features, OpenSSH Client; Git for Windows brings one too.

## Make the release key

One key signs every release. The phone trusts the public half only.

```sh
ssh-keygen -t ed25519 -C opos-release -f <a folder outside the repository>/opos-release-key
```

Use a passphrase. This makes `opos-release-key` (private) and `opos-release-key.pub` (public). `build` runs
`ssh-keygen -Y sign`, which asks for the passphrase on your terminal, or you can pass the `.pub` file as `--key` and sign through
ssh-agent.

- The private key stays off the repository, off the phone and out of every image, chat and backup that is not yours.
  `tools/opos-release.py build` refuses a key that sits inside the repository it is building, and a `--key` file called
  `.pub` that is not an OpenSSH public key (the `.pub` name is what lets a key sit in the repository, so the content has to
  be one). It also refuses to build when any file it would ship, the app tree and the overlay files you name, holds private
  key material, whatever the file is called: it looks for PEM, OpenSSH, PGP and PuTTY private-key headers in the bytes, so a
  private key saved as `src/opos/release.pub` or `notes.txt` stops the build, and the message names the file. `verify`
  makes the same check on a finished bundle. A key that was ever committed is still in git history; treat it as exposed.
  Keep two offline copies: a lost key means no more updates for any image that trusts it, and a new image is the only fix.
- A stolen key is as bad as it sounds: a signed bundle can replace the app, the OPOS scripts and the OPOS systemd units,
  and some of those run as root. Treat the key like a root password for every phone that trusts it.
- The `allowed_signers` file is what the phone and `verify` check against: one line per trusted key,
  `opos-release ssh-ed25519 AAAA...`. Print it from the public half:

```sh
python tools/opos-release.py keygen-help --pub <a folder outside the repository>/opos-release-key.pub
```

Save the line as `allowed_signers`. It holds public keys only, so it may live in the repository; the private key may not.
A separate development key is fine for images that only you carry; do not give such an image to anyone else.

## Build an image that trusts the key

Per the design, the image build installs `/etc/opos/update/allowed_signers` only when it is given
`OPOS_UPDATE_SIGNERS=<file>`; an image built without it accepts no update at all.

```sh
sudo env OPOS_RELEASE=1 OPOS_DEV_SSH_PUBKEY= OPOS_DEV_USB_SHELL=0 OPOS_DEV_SPAN=0 OPOS_DEV_SUDO=0 OPOS_OWNER_SSH_PUBKEY=owner.pub OPOS_UPDATE_SIGNERS=allowed_signers sh packaging/duo-os/build-image.sh <base-rootfs.img> <out.img> HEAD
```

Add `OPOS_UPDATE_URL=https://<host>/<folder>/latest.json` to the same command to write the phone's update source
(`/etc/opos/update.json`, checks only when asked) into the image. Without it the phone fetches nothing, which is all the USB
push below needs. Keep every `OPOS_DEV_*` override and `OPOS_RELEASE=1` in the release command. Every build first copies the base into one
exclusively created file in a private 0700 staging directory next to OUT. Release builds check that file's SHA-256 against
`packaging/duo-os/release-bases.sha256` beside the builder, then inspect that same file and refuse an OPOS image even if its hash is listed.
The list has no environment or argument override; fixture tests use a fixture checkout. The seeded community-base hash was recomputed on the owner's
PC on 2026-11-12 and equals the publisher's saved, unsigned manifest (the release is no longer online); do not admit a modified image. The builder refuses active inherited dev inputs, keeps the existing
policy checks, and runs `verify-image.sh` on the staged release image. Only a successful build is published by an atomic rename that refuses an existing
OUT; failures remove staging and leave OUT untouched. `/etc/opos/build-mode` records `release` or `dev`; release verification refuses dev or unmarked images.
Dev builds accept unpinned or reused bases, always record `dev`, and report verification as `NOT_RUN` unless `OPOS_VERIFY_IMAGE=1` is requested.
The normal service span is independent of the dev-span drop-in. The OTA release builder and updater reject both that drop-in and `/etc/sudoers.d/90-opos-dev`.

The required `OPOS_OWNER_SSH_PUBKEY=owner.pub` is the recovery and USB-push login key. It enables no DevTools or dev privileges; it is a different key from the release key and the two must never be swapped. The signers file is baked
into the image and cannot be changed over the air.
The builder and verifier require an effective `AuthorizedKeysFile` that uses the installed recovery key and safe, traversable home-directory paths. `OPOS_NO_SSH=1` instead chooses no SSH access at all: it removes the login keys, disables and masks SSH services and sockets, and prevents USB-push recovery. The older `OPOS_ALLOW_NO_SSH=1` spelling has the same meaning; neither can be combined with a key.

## Cut a release

1. **Serial.** An integer that only goes up, and you never reuse a serial: not for a fix, not for a bundle you withdrew,
   not for one that failed on the phone (a phone remembers failed serials and refuses them forever). The image itself is
   serial 0. `build` refuses to overwrite a bundle and refuses a serial that is not above the `latest.json` in its output
   folder; tag the release in git as well (`ota-8`).
2. **Source.** Commit first and build from a ref (`--ref HEAD`, the default). A directory (`--dir`) works for an unpacked
   archive but needs `--revision` and `--created` outside git, and a Windows or network share cannot say which files are
   executable (only files starting with `#!` are).
3. **Requires base.** `--requires-base N` is the image's base level (`/etc/opos/base-version`, 1 by default). Raise it when
   a release needs something only a newer image has.
4. **Scope.** `app` is applied at once (OPOS's server restarts and the kiosk page reloads; the compositor and
   `opos.service` never restart). `boot` is staged and applied at the next boot.
5. **Overlay files.** The app tree (`src/opos`, `LICENSE`, `THIRD_PARTY.md`) always goes. Files from `packaging/duo-os` go
   only when you name them, from the same ref: `--overlay opos-kiosk`, `opos-session`, `opos-diag`, `opos-switch`,
   `opos.service`, `opos-server.service`, `opos-fallback.service`, `opos-diag.service`. Anything off the spec's allowlist is
   refused, including the updater's own files, `etc/opos/update.json`, `etc/opos/base-version` and everything under
   `etc/opos/update/`.

Build, then check the result with the separate verifier, then write the index:

```sh
python tools/opos-release.py build --ref HEAD --serial 8 --version 0.2.1 --key <folder>/opos-release-key --allowed-signers allowed_signers --out dist --notes "short text" --overlay opos-kiosk
python tools/opos-release.py verify dist/opos-8.opos-update --allowed-signers allowed_signers --current-serial 7 --list
python tools/opos-release.py index dist/opos-8.opos-update --allowed-signers allowed_signers
```

`build` signs the manifest with `ssh-keygen -Y sign -n opos-update` and checks its own output before it keeps it. `verify`
is a second, independent implementation of the checks the phone's updater makes: tar structure, signature, schema, hashes,
the allowlist, and the payload member by member. If it and the phone ever disagree, one of them has a bug.

Builds are reproducible: the same ref, arguments and key give the same bytes (sorted entries, fixed times, root owner,
normalised modes, gzip header time 0, line endings as committed, and Ed25519 signatures are deterministic). A Windows PC and
an Ubuntu PC gave identical bundles when this was tried, but gzip output is not guaranteed to match across zlib versions, so
publish the bundle you built and tested; do not rebuild "the same" release somewhere else and expect the same hash.

## Host the update

The phone fetches `latest.json` and then the bundle it names. Put both in one folder on any static HTTPS host:

```json
{"serial": 8, "version": "0.2.1", "url": "opos-8.opos-update", "sha256": "<bundle hash>", "size": 123456}
```

`url` is relative to `latest.json`'s own address. Upload the bundle first and `latest.json` second, so the index never
points at a file that is not there yet. The phone is opt-in: with no `/etc/opos/update.json` it never fetches anything;
to use a host, build the image with `OPOS_UPDATE_URL` (above) or, as root on the phone, write
`{"url": "https://<host>/<folder>/latest.json", "auto_check": false, "allow_lan_http": false}` there.

- **GitHub Releases.** Attach `opos-8.opos-update` and `latest.json` to a release to keep an archive and to download the
  bundle by hand for the USB push. At the time of writing GitHub answers a release-asset download with a redirect to a
  different host, and the client as specified refuses redirects to another host, so a phone cannot update from a Release
  directly. Serve what phones fetch from a static site on one host (GitHub Pages is one) or any web server you run.
- **Plain HTTP** works only for loopback or a private LAN with `allow_lan_http` set to true.
- **Trust comes from the signature only.** A host cannot make the phone accept an unsigned or older bundle; it can withhold
  updates, and the `sha256` and `size` in `latest.json` are a convenience check, not trust.

## Push over USB

For the running phone, with no Wi-Fi and no host, copy the bundle into the phone's inbox over the USB network. This needs an
image built with a login key (above) and that key's private half on your PC.

```powershell
powershell -File deploy\duo-push-update.ps1 -Bundle dist\opos-8.opos-update -Host fe80::1%8 -Key <login key>
```

`-Host` is the phone's address on the USB network (an IPv6 link-local address needs its zone, as in the example; see
[duo-usb-install.md](duo-usb-install.md)), `-Key` the login key (not the release key), `-User` defaults to `droidian`. The
script reads the serial from the bundle's manifest (and refuses a bundle whose file name says another), copies to
`opos-8.opos-update.partial` in `/var/lib/opos-update/inbox`, renames it on the phone so the path unit never sees half a
file, then reads `/var/lib/opos-update/status.json` every two seconds until the phase is `done`, `rolled-back` or `failed`
**for that serial**, or `-TimeoutS` (300) runs out. A status for another serial is printed as "not this update" and never ends
the wait, so if serial 7 is still applying when you push serial 8, 7 finishing is not reported as 8 finishing. On the phone
it runs only `cat` and `mv`; the phone's own updater does the verifying and applying, so the script cannot make it accept
anything unsigned. Exit codes: 0 done, 1 rolled back or failed, 2 timed out waiting, 3 could not deliver.

One case cannot be matched by serial: a bundle refused before its manifest is read, a bad signature above all, leaves no
serial in `status.json`. The script takes that refusal for your bundle's when the updater was idle just before the hand-over
(the usual case, and the quick answer for a wrong key) or when it saw the updater verifying your file first; if the updater
was busy with something else, it keeps waiting and, at the timeout, prints what the updater said last. Read `status.json`
yourself then. (The updater would make this exact by writing the inbox file name or the bundle's sha256 into every status.)

The first connection records the phone's host key (`StrictHostKeyChecking=accept-new`). A freshly built image has a new host
key, so after re-imaging run `ssh-keygen -R <address>` once.

## Rotate the key

The phone trusts what is in the image's `allowed_signers`, and that file changes only with a new image. So:

1. Make the new key and an `allowed_signers` with both keys, old line and new line.
2. Build and install a new image with both keys.
3. Sign releases with the new key from then on. Phones still on an older image keep needing the old key until they get the
   new image.
4. When every phone you care about runs an image with both keys, build the next image with only the new key, and destroy
   the old private key.

A leaked key follows the same steps, but there is no over-the-air revocation: a phone trusts the old key until it is
re-imaged, so treat each of them as exposed until then.

## After a failed update

Read what the phone says: `ssh -i <login key> droidian@<address> cat /var/lib/opos-update/status.json`.

- `failed`: rejected before anything changed (signature, serial not above the current one, base level, free space, a bad
  bundle). Nothing was applied. Fix the cause.
- `rolled-back`: the new release started but failed its health check; the phone went back to the previous release, and the
  serial is now in its `failed` list. (After a rollback you asked for through the inbox, `rolled-back` is also the phase, but
  the serial is not in the list.)
- Neither: the push timed out. The updater may still be working; read `status.json` again before doing anything.

A serial that failed on a phone is never accepted again, so the fix goes out under a new serial (the next number, never a
reuse) and a new bundle. To go back to the previous release on purpose, create an empty file named `rollback.request` in
the inbox (over ssh as the login user; this is also what the Update page's button does), or run `opos-updater rollback` as
root on the phone. The inbox request is honoured once per applied update and does not burn the serial it leaves, so pushing
that same bundle again undoes it; the root command goes back as often as you ask and does burn the serial. A `boot`-scope update that
fails two boots in a row rolls itself back. A `boot`-scope update that is staged and has not run yet can be cancelled from the
Update page ("Cancel staged update", offered even on the first update, when there is nothing to go back to, on an image whose
updater knows the form; an older image keeps the old rules), or by hand on such an image: write the
text `cancel-staged` into `rollback.request.partial` in the inbox and rename it to `rollback.request`. That drops the staged update
and leaves what is installed alone; its serial is not burned, so the same bundle can be delivered again. If the phone is unusable, re-image it from the PC with `build-image.sh`; the
image starts again from its own baked-in release.

### A rollback that did not finish

A failed update, a requested rollback and recovery after a crash all end the same way: the saved files go back, `/opt/opos`
is switched and the server restarts. When that cannot finish (no room, systemd refuses, a saved file is damaged) the updater
does not guess. It keeps the journal (`txn` in `state.json`) and the saved originals, exits 1, and looks at no bundle or
request until a run has finished the job. Meanwhile the server runs whatever is live, which may be a mix of old and new
files, so repair it rather than leave it. Do it as root over ssh, and do not delete anything in `/opt/opos-releases`.

What the phone says:

- `status.json` has `"phase": "failed"` and a message that starts `the previous release is not back yet:` and then the cause:
  `cannot put usr/libexec/opos/opos-kiosk back: [Errno 28] No space left on device; the next run tries again`, or
  `systemd did not take the restored release (...)`, or `the overlay backup <path>/index.json is missing or unreadable (...);
  refusing to guess, the transaction stays open` (also `is malformed`, and `the backup of <path> is damaged; not restoring
  it`). The message is cut at 300 characters; `journalctl -u opos-update.service -u opos-update-boot.service` has the whole line.
- `/usr/libexec/opos-updater/opos-updater status` (changes nothing) prints `INTERRUPTED apply of serial N; the next run
  recovers it` (or `rollback of serial N`). With `--json` it shows the `txn` record: `kind`, `serial`, `release` (the release
  being left; its directory holds the backups) and `from` (an apply) or `to` (a rollback): the release to go back to.

What to check, with `R=/opt/opos-releases/<txn.release>`:

- `python3 -m json.tool $R/overlay-backup/index.json`: one entry for each file the update touches. `path` (from `/`),
  `existed`, and when it did exist `mode` (decimal: 493 is 0755; `printf '%o\n' 493`) and the `sha256` of the original, which is
  saved in `$R/overlay-backup/files/<path>`. `sha256sum` that copy and compare it with the entry. `existed: false` means the
  update created the file and a restore deletes it.
- `sha256sum /<path>` and `stat -c %a /<path>` for each entry. A live file that already has the entry's hash and mode is left
  alone by the restore; a different one is replaced by the saved copy.
- `readlink /opt/opos`. After an update that failed it still names `txn.release` until the restore has switched it back.

What to do:

1. If the cause is room, make room on the file system of the file named: `df /usr/libexec/opos /etc/opos /opt`, then delete
   what is yours to delete (logs, downloads). Delete any `*.new` beside the overlay files (`/usr/libexec/opos`, `/usr/sbin`,
   `/etc/systemd/system`, `/etc/default`, `/etc/opos`) too: the updater now removes them itself before a restore, but an image
   built before that change can have a half-written one holding the very room the restore needs. If the cause is systemd (a
   rate limit, a bus error), wait a minute.
2. Let the updater do the job: `systemctl start opos-update.service` (with an empty inbox it only recovers) or reboot
   (`opos-update-boot.service` recovers before the server and the session start). It is done when `status` has no
   `INTERRUPTED` line and `status.json` says `rolled-back` (an apply: `interrupted; the previous release was restored`; its
   serial is now in `failed`). Do not use `opos-updater rollback` for this: it recovers first and, if that works, then goes back
   one release more.
3. If it stops at the same place again because the backup itself is unusable (a lost or malformed `index.json`, a saved copy
   that does not match its hash), the updater cannot finish and will not guess. Either re-image the phone, or repair it by hand
   and close the journal yourself, as a last resort:
   - `systemctl stop opos-update.path`, and check that `opos-update.service` is not running.
   - Put each overlay file back: `install -o root -g root -m <octal mode> $R/overlay-backup/files/<path> /<path>`, using only
     a saved copy you can vouch for (its hash matches, or you know it is the image's file); delete each file that did not
     exist. Without `index.json`, the files under `$R/overlay-backup/files/` are the ones that existed, and every other path
     in `$R/manifest.json` that does not start with `opt/opos/` is one that did not; the mode is the image's, since the saved
     copies are `0600`. With no copy you can vouch for, re-image.
   - Point `/opt/opos` back at `txn.from`: `ln -s opos-releases/<txn.from> /opt/opos.new && mv -T /opt/opos.new /opt/opos`.
     Then `systemctl daemon-reload` and `systemctl restart opos-server.service`, and check that the Update page opens.
   - Close the journal the way the updater would have for an `apply`: copy `/var/lib/opos-update/state.json` aside, then set
     `"txn": null`, add the serial to `"failed"` and raise `"floor"` to at least that serial (it is burned: the fix goes out
     under a new number), and set `"pending_boot": null` if `txn.boot` is true. Keep it valid JSON: the updater refuses a
     `state.json` it cannot read. Closing a `rollback` journal also moves `current` and `previous`, so re-image instead.
   - `systemctl start opos-update.path`, then `systemctl start opos-update.service` once. `status` should show the running
     release you expect and no `INTERRUPTED` line.

## Limits

- Written against the [design](ota-update.md). The tools are tested with throw-away keys on a PC (Linux and Windows). Neither
  the updater nor these tools have run on the phone; expect to find problems at the first real push.
- No kernel, initramfs, vendor layer, Debian package, updater or trust-key update over the air. Those come with a new image.
- No revocation, no threshold or multi-party signing, no hardware token beyond what `ssh-keygen -Y sign` does with an
  ssh-agent (pass the `.pub` file as `--key`). Key custody is the whole security story.
- The build tool is a little stricter than the spec's path globs: it also refuses `etc/opos/update.json`,
  `etc/opos/base-version` and any `opos-update*` name in `usr/sbin`, `etc/systemd/system` and `etc/default`, because those
  belong to the updater and the image; it also refuses nested paths under `usr/libexec/opos` and `etc/opos`.
- GitHub Releases cannot serve the phone directly while the client refuses cross-host redirects.
- Byte-identical rebuilds are certain on one machine and were seen across Windows and Ubuntu, but are not guaranteed across zlib versions.
