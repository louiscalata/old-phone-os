<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Proposed cross-platform persistent-boot tooling

**Status: NOT_RUN on a phone. Do not install yet.** A complete restoration to Android must first be physically rehearsed and recorded. No such rehearsal or complete Android recovery method is established here. A dry run, boot-partition readback, and fake-fastboot test do not satisfy that gate. Calls and texts remain simulated; device checks remain **NOT_RUN**. Stop on an unexpected result, failure, timeout, or uncertainty.

The existing [persistent-kernel procedure](duo-persistent-install.md) remains the source for its evidence, device history, risks, gates, and independent owner permissions. This document proposes a **different, untested** PC tool and route: it fetches and flashes the current `boot_a` or `boot_b` directly in the observed fastboot mode. The existing procedure proposes TWRP block reads and writes, includes `misc` and slot handling, and requires separate owner permission at its gates. A fastboot `fetch` or `flash` may be unsupported or refused here even if the TWRP route works; neither route has been established on the phone. This tool does not switch slots, change `misc`, erase, format, install userdata, or unlock or relock the device. It does not replace the existing procedure's other decision gates. Its requirement for a **physical Android restore rehearsal before install** is stricter than a dry-run review of rollback. The existing procedure also calls the USB statement in the Duo OS README out of date; that statement has since been corrected with the owner’s November 11 confirmation.

## 1. Prepare and identify

Use Windows PowerShell, Python 3.11 or newer, Android platform-tools (`fastboot.exe`) and OpenSSH `ssh-keygen` on PATH. Run from the repository root. `--fastboot` can instead name the full path to `fastboot.exe`, including spaces. Set these private variables locally; never put the serial, device outputs, images, signing key or backups in this repository or a shared drive. On Windows, protect the parent and backup using the owner's private account and filesystem access controls; Python's POSIX directory mode does not establish a Windows ACL.

```powershell
$fastboot = 'C:\platform tools\fastboot.exe' # replace with the actual path, or use 'fastboot.exe' on PATH
$serial = '<exact serial from fastboot devices>'
$slot = '<current slot: a or b>'
$bootRaw = 'C:\private\boot-original.img'
$boot = 'C:\private\boot-full-partition.img' # new file, must not exist
$rootfs = 'C:\private\rootfs.img'
$manifest = 'C:\private\opos-images.manifest' # new file
$privateKey = 'C:\private\opos-signing-key'
$signers = 'C:\private\allowed_signers'
$signature = "$manifest.sig"
$backup = 'C:\private\duo-boot-backup' # new directory, parent must exist
```

Use independently trusted, compatible images and a separately protected signing key and allowed-signers file, with principal `opos-install`. A signature establishes who signed the image hashes, not publisher provenance, compatibility, rootfs behavior, or bootability. The tool checks the signature and hashes in Python, but **does not inspect the filesystem or run** [the Linux-only image verifier](../packaging/duo-os/verify-image.sh). Independently review the image and any applicable verifier results for the exact rootfs hash before proceeding. The existing procedure describes the prerequisites, the risks of Android's possible userdata wipe, the brake, and unknown boot behavior.

With the phone in the bootloader menu and a data cable connected, discover privately and check the current answers. These commands are read-only queries, but entering bootloader mode changes the running mode. Observe the serial; replace the placeholder above and use the slot actually reported. Do not switch slots or unlock anything.

```powershell
& $fastboot --version
& $fastboot devices
& $fastboot -s $serial getvar model
& $fastboot -s $serial getvar product
& $fastboot -s $serial getvar unlocked
& $fastboot -s $serial getvar current-slot
& $fastboot -s $serial getvar battery-level
& $fastboot -s $serial getvar "partition-size:boot_$slot"
```

Fastboot 37.0.1 on Windows reportedly crashes with `bad_alloc` on a 5.7 GB raw ext4 image; see [the USB install notes](duo-usb-install.md). Preflight prints the installed version and this warning. This tool never flashes the rootfs. The tool requires the named serial to occur exactly once, product `surfaceduo`, an unlocked bootloader, matching current slot, battery 20–100%, and a reported positive boot partition size. The owner must confirm the physical model. Fastboot may print `FAILED` with exit status zero; the tool checks output as well as the exit status. Unsupported queries, fetch, flash or readback mean **stop**. Device state and fastboot support remain **NOT_RUN**.

## 2. Prepare and sign the images on the PC

The boot image must be a trusted raw image for the selected rootfs, padded to **exactly** the reported boot partition size. Confirm the decimal size independently and replace the placeholder below. The exclusive-create command refuses to overwrite an existing file. It changes the PC only.

```powershell
$partsize = <reported size in decimal bytes>
py -3 -c "import pathlib,sys; src,dst,n=pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]),int(sys.argv[3]); size=src.stat().st_size; assert 0 < size <= n; f=dst.open('xb'); f.write(src.read_bytes()); chunk=bytes(1048576); left=n-size; exec('while left:\n k=min(left,len(chunk)); f.write(chunk[:k]); left-=k'); f.close()" "$bootRaw" "$boot" "$partsize"
Get-Item -LiteralPath $boot | Select-Object Length
```

Check the byte count before proceeding. Create a new manifest of the two image hashes, sign it, and independently protect the signing key and trusted allowed-signers file. This manifest's exact signed bytes may use LF or CRLF. Do not overwrite an existing manifest or signature.

```powershell
py -3 -c "import hashlib,pathlib,sys; h=lambda p: hashlib.file_digest(open(p,'rb'),'sha256').hexdigest(); p=pathlib.Path(sys.argv[3]); p.open('x',encoding='utf-8',newline='').write('boot '+h(sys.argv[1])+'\nrootfs '+h(sys.argv[2])+'\n')" "$boot" "$rootfs" "$manifest"
if (Test-Path -LiteralPath $signature) { throw "Signature already exists; stop" }
ssh-keygen -Y sign -f "$privateKey" -n opos-install "$manifest"
```

If a command fails, stop and investigate; do not reuse a partially written file without checking it. Keep a separate private copy of the expected image hashes and inspect the signer's identity. The signed boot image must be the entire partition length, not merely the shorter original boot file.

## 3. Review, preflight, and back up

The first command in each pair is a dry run: it prints every external command that the operation would invoke, including signature verification, and creates no file or connection. `--execute` enables the read-only device checks and local backup creation; only `install` and `restore` flash the phone. Always examine the printed target and paths first.

```powershell
py -3 tools\duo_persistent.py preflight --fastboot "$fastboot" --device "$serial" --slot "$slot" --boot "$boot" --rootfs "$rootfs" --manifest "$manifest" --signature "$signature" --signers "$signers"
py -3 tools\duo_persistent.py preflight --execute --fastboot "$fastboot" --device "$serial" --slot "$slot" --boot "$boot" --rootfs "$rootfs" --manifest "$manifest" --signature "$signature" --signers "$signers"
py -3 tools\duo_persistent.py backup --fastboot "$fastboot" --device "$serial" --slot "$slot" --backup "$backup" --boot "$boot" --rootfs "$rootfs" --manifest "$manifest" --signature "$signature" --signers "$signers"
py -3 tools\duo_persistent.py backup --execute --fastboot "$fastboot" --device "$serial" --slot "$slot" --backup "$backup" --boot "$boot" --rootfs "$rootfs" --manifest "$manifest" --signature "$signature" --signers "$signers"
Get-Content -LiteralPath (Join-Path $backup 'manifest')
Get-Item -LiteralPath (Join-Path $backup "boot_$slot.img") | Select-Object Length
Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $backup "boot_$slot.img")
```

Check the backup manifest's serial, partition, full byte count and SHA-256 against the independent results and reported size. Keep and independently verify a second private copy. The backup can contain a modified kernel rather than stock Android; its identity is **UNKNOWN** until independently established. Fetch support and complete readback are **NOT_RUN**. A successful backup establishes only the fetched bytes.

## 4. Restore rehearsal and gate

Review the proposed boot-partition restore without making a phone write:

```powershell
py -3 tools\duo_persistent.py restore --fastboot "$fastboot" --device "$serial" --slot "$slot" --backup "$backup"
```

**Do not proceed to install:** the owner must first define, authorize, physically rehearse and record a complete Android recovery on the appropriate device and confirm a successful Android start. The exact commands for that separate complete recovery are **UNKNOWN** here. The boot backup alone cannot establish a complete Android restore: other partitions, slot metadata and userdata may also matter, and stock Android may request a wipe on foreign userdata. The rehearsal and Android start are **NOT_RUN**.

The following is a proposed restore of this one boot partition only, after separate owner authorization and only if the same serial and slot remain accessible. It requires the verified manifest and full backup and reads back the entire partition. It is not a complete Android restore:

```powershell
py -3 tools\duo_persistent.py restore --execute --fastboot "$fastboot" --device "$serial" --slot "$slot" --backup "$backup"
```

If a write or readback fails, do not reboot; stop and investigate. A failed flash may still have changed bytes, so the tool attempts readback even after a reported flash failure. If access is lost, no recovery is established here.

## 5. Install only after the gate

After the complete physical Android recovery rehearsal is recorded and the owner separately authorizes the write, review the dry run and then, only if all checks pass, run the second command:

```powershell
py -3 tools\duo_persistent.py install --fastboot "$fastboot" --device "$serial" --slot "$slot" --backup "$backup" --boot "$boot" --rootfs "$rootfs" --manifest "$manifest" --signature "$signature" --signers "$signers"
py -3 tools\duo_persistent.py install --execute --recovery-rehearsed --fastboot "$fastboot" --device "$serial" --slot "$slot" --backup "$backup" --boot "$boot" --rootfs "$rootfs" --manifest "$manifest" --signature "$signature" --signers "$signers"
```

`--recovery-rehearsed` is the owner's attestation of a complete physical Android recovery rehearsal; the tool cannot verify it. It rechecks device state, the signed hashes, the full backup and the current boot partition against the backup before flashing. It then fetches the entire partition and checks size and hash. An interrupted or rejected write can leave the phone unbootable. A matching readback proves bytes, not bootability or Android recovery. The tool cannot guarantee access after a failure.

## 6. First boot and acceptance

Only after the owner accepts the separate first-boot risks in the existing procedure, observe the device with no PC assistance. The `misc` brake and slot behavior are **UNKNOWN**; a plain start can change storage or lead Android/recovery toward a reset. Do not accept a factory reset. OPOS must visibly start without the PC, then do so on a second independent power cycle. Both are **NOT_RUN**. Follow [Duo OS acceptance](duo-acceptance.md) and the [on-device checklist](duo-on-device-checklist.md), recording each device result privately. These plans do not establish a rescue for a damaged bootloader chain.
