<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Surface Duo image identity

Status: implemented in source and tested with simulated root filesystems and
external-tool stand-ins. A real image build, image verification under qemu-user,
real ext4 release extraction, owner-key signing, and every phone check below are
**NOT_RUN** for this change. Nothing here establishes Surface Duo behavior.

## Distribution and ownership

Old Phone OS is an interface, and a work in progress as a Linux distribution,
built on the community Droidian port, which supplies the kernel, drivers and
system services. OPOS owns the interface, image
identity, update channel and battery rules. OPOS does not claim an independent
kernel, replacement hardware drivers, or independent maintenance of the base.
The phosh fallback and `opos-switch` keep their existing behavior.

The rules protect calls, messages and alarms. The optional model can only pause
more work. Calls and messages on this image remain simulated. The earlier
desktop/public demo remains a hackathon prototype. See [the current limits](duo-os-final.md).

## Recorded identity

`build-image.sh` writes a regular `/etc/os-release` with `NAME="Old Phone OS"`,
`ID=opos`, `PRETTY_NAME="Old Phone OS <version>"`, `VERSION` and `VERSION_ID`,
the 12-character `BUILD_ID`, and the repository's `HOME_URL`. It constructs
`ID_LIKE` from the base's own `ID`, followed by its `ID_LIKE`, with duplicates
removed in their original order. It parses assignment data without executing it.

The base's original file bytes remain at `/usr/lib/os-release.base`. An original
`/etc/os-release` symlink is resolved inside the image, including an absolute
link, then replaced; the upstream `/usr/lib/os-release` is left intact. Rebuilding
an earlier OPOS image keeps its preserved upstream file. The recorded base hash
always describes the immediate input image, including when that input is OPOS.

The default `/etc/hostname` is `opos`, and `/etc/issue` contains the versioned
name. Existing base `/etc/hosts` entries and network configuration are not
rewritten. `pyproject.toml`'s `project.version` is the version source; the builder
reads that file from the selected Git ref and includes it in the application
archive. The existing notices and setup CLI read the same project version.
An OTA tree without that file reads the updater's current-release metadata,
checking that its revision matches the running application tree;
the existing pmOS package installs Python version metadata from its package
version, which the packaging test keeps equal to the project version.

`/etc/opos/image.json` records format 1, version, full revision, 12-character
build ID, repository URL, integer `source_date_epoch`, the base image SHA-256,
all parsed base os-release fields, installed file records and baked switches.
Build time defaults to the selected Git ref's commit timestamp when
`SOURCE_DATE_EPOCH` is omitted; an explicit value must be valid nonnegative Unix
seconds. There is no wall-clock fallback. JSON uses sorted keys, sorted file
paths, fixed indentation and a final newline. File mtimes and builder paths do
not enter it. Identical inputs produce
identical manifest bytes; this is not a reproducible-ext4-image claim.

Regular file records hash their content with SHA-256. Symlink records hash the
UTF-8 link target text and also record that target. The inventory includes the
physical initial application release, `/opt/opos` link, helpers, configuration,
unit links and upstream files changed by the build (including exposure edits).
Unchanged base files are excluded. The manifest excludes its own hash; the
signed release checksum list covers its exact bytes. Optional public-key file
paths are recorded as on/off switches, never as private builder paths or key
material. Runtime environment overrides are not asserted to be baked switches.

The manifest is a record of the baked image. An OTA update, owner configuration
change, hostname change or base package upgrade can make its file check fail.
That is an image-drift finding, not proof that an update signature was invalid.
The About section and `opos-version` report the baked image; the update page's
existing Running now section reports the current application release. Base
updates, kernel updates and trust-key replacement still require a new image;
see [the OTA contract](ota-update.md). There is no new package or service.

On a legacy image that advertises no image identity, an updated selftest reports
`identity.image` as SKIP. An advertised but incomplete or inconsistent identity
still fails. The unprivileged selftest reports WARN with incomplete hash coverage
when permissions prevent reading a listed file, such as a root-owned mode-0600
`/etc/opos/monitor.env`; its permissions stay private. The root image verifier
requires every file hash to match and never skips an unreadable file.

## PC build and release checks, in order

These commands are for the owner on a Linux PC or CI with Python 3.11+, the
existing image-build dependencies, OpenSSH supporting `-Y`, and e2fsprogs
`debugfs`. WSL can provide the Linux tools on a Windows PC; native Windows ext4
extraction is not claimed. No command in this section writes a phone.
Replace the angle-bracket values with owner-selected paths or values.

1. Run the offline identity and release tests:

   ```sh
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest tests.test_duo_identity tests.test_duo_image_release
   ```

   Pass: exit 0 and `OK`. These tests use scratch roots and signing/extraction
   stand-ins; they generate no signing keys and make no sockets or model calls.

2. On the Linux image builder, build the selected source revision:

   ```sh
   sudo env SOURCE_DATE_EPOCH=<owner-chosen-unix-seconds> OPOS_UPDATE_SIGNERS=<public-allowed-signers-file> sh packaging/duo-os/build-image.sh <base-rootfs.img> <opos-rootfs.img> <source-ref>
   ```

   Pass: exit 0, identity finalization succeeds, `fsck clean` and a final image
   SHA-256 are printed. The source ref must contain this implementation. This
   privileged build is **NOT_RUN** here. It needs the actual base image and root.
   The owner may override the commit timestamp and chooses optional update
   URL/dev gates explicitly.

3. Verify that built image on the Linux builder:

   ```sh
   sudo sh packaging/duo-os/verify-image.sh <opos-rootfs.img>
   ```

   Pass: exit 0, the identity/file-hash check says PASS, and the final line is
   `VERIFY: PASS`. This needs root, loop mounts, qemu-user and loopback sockets;
   it is **NOT_RUN** here and is not a boot or hardware test.

4. Package the built raw ext4 image using the owner's existing Ed25519 signing
   key (or an agent-backed public key file). The key must stay outside this
   checkout and the release folder. The tool never generates or copies it:

   ```sh
   python3 tools/opos-image-release.py build <opos-rootfs.img> --key <existing-owner-key> --allowed-signers <public-allowed-signers-file> --out <new-release-folder>
   ```

   Pass: exit 0 and `PASS release built and verified`. The new folder has exactly
   `rootfs.img`, `image.json`, `RELEASE_NOTES.txt`, `SHA256SUMS` and
   `SHA256SUMS.sig`. `debugfs -R 'cat /etc/opos/image.json'` reads the embedded
   manifest without `-w`, root or a mount. The detached SSHSIG signature covers
   the exact checksum list in the existing `opos-update` namespace; the trusted
   identity is `opos-release`, and only Ed25519 envelopes are accepted.

   The notes are an unsigned-in-source template until this command signs their
   bytes. Complete a separate plain-text notes file and add `--notes <notes.txt>`
   to build the final folder. The tool refuses an existing output folder. Editing
   any published notes afterwards invalidates the checksums. This is a full
   image release folder, not an `.opos-update` bundle for the root updater.

5. Independently verify the delivered folder:

   ```sh
   python3 tools/opos-image-release.py verify <release-folder> --allowed-signers <public-allowed-signers-file>
   ```

   Pass: exit 0 and `PASS release signature, image/manifest/notes hashes, and
   embedded manifest`. It verifies the detached signature and all three file
   hashes, then compares the sidecar manifest byte-for-byte with the manifest
   extracted from the image. Altered files, wrong trust, missing files, links or
   extra files fail. Real ext4 extraction and real owner-key signing/verification
   are **NOT_RUN** here; no owner key or built image was supplied.

## Phone checks, in order

Every check below is **NOT_RUN**. Run them only on an already booted, separately
authorized image. This work does not install, flash or boot a phone.

1. `cat /etc/os-release`: pass when the name, version, `ID=opos`, repository URL,
   upstream ancestry and 12-character build ID match the signed image manifest.
2. `cat /usr/lib/os-release.base`: pass when it shows the preserved Droidian
   identity and matches `base.os_release` in `/etc/opos/image.json`.
3. `cat /etc/issue; cat /etc/hostname; hostname`: pass on a fresh image when the
   issue has the OPOS version and both the default file and active hostname say
   `opos`. The active hostname is a boot-time check, not established by fixtures.
4. `opos-version; uname -r`: pass when the version/revision/base match the signed
   manifest and the printed kernel release matches `uname -r`. The wording is
   `Old Phone OS <version>, based on <base name>`.
5. `opos-selftest --only identity`: pass when `identity.image` is PASS, every
   listed hash matches, the preserved base file is present, and exit status is 0.
6. Read the update page with the image's existing Python (no extra package):

   ```sh
   python3 -c "from urllib.request import ProxyHandler, build_opener; print(build_opener(ProxyHandler({})).open('http://127.0.0.1:8765/update', timeout=10).read().decode())"
   ```

   Pass when About contains the same image version, revision, base and kernel.
   On the screen, open Settings > About > Updates and compare the visible facts.
   No extra home-page bytes or home-page identity request were added.
7. `opos-selftest`: pass when it exits 0 with no FAIL; review every WARN/SKIP
   separately against [the on-device checklist](duo-on-device-checklist.md).
   Calls, messages, glass/touch, stability and power behavior need their own
   recorded checks. Do not infer those results from the identity checks.

No fallback exercise is included: this change leaves `opos-switch` and the
phosh fallback untouched, and no service-switching command is needed to read
the distribution identity.
