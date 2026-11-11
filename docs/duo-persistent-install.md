<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Persistent kernel on the Duo: the gated first device session

Status, November 11, 2026: **a plan; none of it has been run on a phone.** The probe script (`deploy/duo-persist-probe.ps1`)
and this text were tested against fake tools and file checks only. Nothing here authorizes a write to the phone. After
the read-only gates, G2b requires the owner's explicit acceptance of the last-resort risk before any write. G3, G3b,
G4, G5, G5b and G6 each require the owner's separate word; G3, G3b and G4 write, and G5 is the first menu-start check,
which can start Android and destroy the OPOS install. A powered-off start remains **NOT_RUN**. All device checks in this plan remain **NOT_RUN**.

## The goal and what changes for the owner

Today the OPOS OS on the Surface Duo 1 keeps its root filesystem in the phone's storage (`/data/rootfs.img` inside the
ext4 userdata) but its **kernel is pushed from a Windows PC at every start** (`fastboot boot boot.img`, a RAM-boot).
A power cycle can start stock Android. Read-only observations from the PC session on November 11, 2026 record that any
stock Android boot on OPOS userdata applies metadata encryption in place, making the OPOS rootfs unreadable to Linux.
Returning to Android this way destroys the OPOS install; it is not a harmless exit. The misc brake was intended to stop
that, but its protection is unproven. The owner wants OPOS "installed like a regular Android OS": the kernel image in the phone's own
storage, so the power button starts OPOS with no PC, and, later, kernel updates over the air.

Fastboot flash or erase of `boot`, `vbmeta` and `dtbo` is expected to fail on this phone and is never used here.
The proposed route is hash-guarded `dd` from RAM-booted TWRP. It remains an experiment, not a proven rescue: read,
back up, review the risks and then proceed one gate at a time only on the owner's word.

If everything passes, the owner would see:

- **No PC at start.** Power button, Surface logo, the orange "unlocked" warning, then OPOS.
- **Stock Android's kernel (and the recovery inside it) gone from the slot that was written.** It is backed up in G1 and
  can be put back from TWRP. Boot Select's "Recovery mode" would then start OPOS's kernel, and Microsoft's sideload restore
  would need the stock image put back first (see "Risks").
- **No misc writes** are part of these gates; the brake is not a measured protection for this plan.

What does not change: the bootloader stays unlocked; no step writes the bootloader chain; vbmeta and dtbo are not
written; the install chain (`deploy/duo-opos-boot.ps1`) is untouched and keeps working as the fallback way to start OPOS.

**Prerequisites that this runbook does not solve.**

1. USB data must work: every command below needs fastboot and adb over USB. The owner confirmed USB data works on November 11, 2026; check it in G0.
2. G4 and later need the OPOS rootfs on userdata. That is the install chain's job, it erases Android's data, and it is
   a separate decision of the owner. G0 through G3b do not touch userdata and do not need the rootfs.
3. The pinned images in one folder (`boot.img`, `surfaceduo1-twrp.img`, `misc-brake.img`), battery 20 percent or more,
   the phone on a good cable.

## What is known, and what the experiment settles

Grades: **MEASURED** (a command output or a hash, with where it is), **REPORTED** (a document or a person, dated where the
source has a date), **INFERRED** (follows from measured or reported facts by reasoning; nobody has tried it), **UNKNOWN**. PC research labels **VERIFIED**, **REPORTED** and **NOT_RUN** retain their original meanings below.
A **HYPOTHESIS** is an UNKNOWN that is a candidate explanation, not a finding. "Re-checked" means compared against the
files while this runbook was written.

| # | Claim | Grade | Source |
|---|---|---|---|
| 1 | The bootloader refused `fastboot flash boot_a` with `remote: Device Error` although unlocked; `fastboot boot` worked. | REPORTED, re-checked | One line, item 34 of the October 31, 2026 install-friction log kept on the owner's PC. Every later document repeats it. |
| 2 | Nothing else about that attempt was kept: not the exact output, not the image, not raw or sparse, not which fastboot mode, not what `misc` held. | UNKNOWN | No raw tool output was found in the agent transcripts that were searched. |
| 3 | `vbmeta` is refused the same way. | UNKNOWN | [packaging/duo-os/README.md](../packaging/duo-os/README.md) says "reported"; no first-hand record exists. |
| 4 | The same bootloader's fastboot does write other partitions: on November 7 `erase misc`, `flash misc`, `format:ext4 userdata` and the boot of two images all answered OKAY. That is all this row says. | MEASURED, re-checked | The boot-chain log of the November 7 run (`boot-chain.log` beside the images). |
| 4b | HYPOTHESIS: the refusal concerns the boot LUN; a poisoned `misc` or mode might also matter. Do not try a cure that writes `misc` or changes slots in these gates. | UNKNOWN (HYPOTHESIS) | The port's trap notes and planning research; G1 reads `misc` but does not establish a cure. |
| 5 | On October 31 `boot_a` held a Magisk-patched image (the hash equals the patched file prepared that day); the October 9 content was reported to be stock. So something wrote it. The reported route is Magisk "Direct Install" from a RAM-booted rooted session, a write made on the phone by root, not by fastboot. This is the strongest evidence we have that a root-side block write to the boot LUN (`sde`) works while `fastboot flash boot_a` was refused the same day. It says nothing yet about a write from TWRP. | October 31 hash MEASURED, re-checked; stock on October 9 and the route REPORTED; the conclusion about a root-side write INFERRED | `SHA256SUMS` of the 2026-10-31 backup; friction log item 34; the planning research. |
| 6 | `boot_b` is a complete stock image of an older build than slot a; slot b has never been started by this project. | hash match REPORTED by the planning research (not re-checked), starting it UNKNOWN | Backup hashes; AVB parse of `vbmeta_b`. |
| 7 | Do not copy a past bootloader snapshot into this runbook. G0 reads the current slot, lock state and battery on the day of the install. | UNKNOWN | G0 reads the current values. |
| 8 | This bootloader has no `flashing unlock_critical`; changing the lock state makes it wipe data. Never run `flashing lock` or `flashing unlock`. | REPORTED (strings of the phone's own bootloader image), not re-checked | Planning research; the image is in the October 31 backup, so anyone can re-check. |
| 9 | The text `Device Error` also appears for unrelated causes (a slot that rejects RAM-boots after a crashed kernel, a poisoned `misc`). | REPORTED | The port's own README (undated). |
| 10 | The community (WOA-Project) dual-boot page for the Duo 1 reaches **fastbootd** (userspace fastboot) through stock recovery > "Enter fastboot" and runs `fastboot flash boot <image>` there, with the plain partition name. Its test steps use `adb reboot bootloader` and then `fastboot boot <image>`. It names no firmware version and no carrier. Never tried here. That the bootloader's own fastboot is used there only for `fastboot boot` is this runbook's reading of the page's steps, not a statement on the page. | REPORTED | https://github.com/WOA-Project/SurfaceDuo-Guides/blob/main/Install/DualBoot.md (undated page, read November 7 and November 8, 2026). Its Troubleshooting advice is outside these gates; it also says a bootloader cannot be relocked while the boot image modified by the guide is installed. |
| 11 | The port's own rule is to write the kernel to the slots only after it has started Linux on that phone, which the November 7 RAM-boot did. So the port does not forbid slot writes in general. The standing rule "never flash boot, vbmeta, dtbo or recovery" is the owner's, and G3 asks to relax it for one partition. | REPORTED, re-checked | The header of the port's `release-add-boot.sh`. |
| 12 | The OPOS kernel image fits: 33,972,224 bytes in a 100,663,296-byte partition, header version 2 like the stock images, no AVB footer, unsigned. | MEASURED | SHA-256 `abec1fe9ce4cbb7a9296c3abd4fd7b4acc1283842e86f2bfe0879a7e84fae552`, re-checked on this PC. |
| 13 | The bootloader continued to start a Magisk-patched `boot_a` whose AVB hash does not match its `vbmeta`, in the unlocked ("orange") state. So no vbmeta change is needed while the phone stays unlocked; **never relock**. | REPORTED (parse of the backup), not re-checked | Planning research. |
| 14 | Stock Android on a userdata it does not recognize writes a wipe request to `misc` and reboots to recovery, which offers a factory reset. The exact sequence on this phone was not re-measured. | REPORTED | AOSP recovery source; the October 31 factory reset; the port's "BCB poison" note. |
| 15 | No rescue route is known to this project for a damaged bootloader chain. The one page found is a volunteer's answer on Microsoft's Q&A (question 2026-03-29, answer 2026-03-30). It says the Snapdragon 855 has an EDL mode, that the device-specific Firehose programmer is not publicly released, that Microsoft has a paid out-of-warranty repair, and that some XDA users report partial success. None of that is a route the owner has. | REPORTED (one volunteer answer, partial reports) | https://learn.microsoft.com/en-us/answers/questions/5842635/how-to-fix-hard-brick-surface-duo. "No step touches the bootloader chain" is this project's rule, not something the page states. |
| 16 | Current content of `boot_a`, `boot_b` and `misc`, the read-only flags of the boot LUN, and whether a write from TWRP is accepted. | UNKNOWN | G1 to G3 find out. |
| 17 | What the bootloader does with the misc brake on a plain power-on. It has no literal `bootonce-bootloader` string; on October 31 a reboot with the brake armed reached stock recovery, not fastboot. | UNKNOWN (the November 7 chain worked with the brake armed, but only for RAM-boots) | Friction log item 33; planning research. |
| 18 | TWRP's `dd` wrote `misc` on October 9: the first 2048 bytes (`bs=2048 count=1`) of `sda5`, the brake of the install chain, and it was accepted. The attempt that used a `conv=` option was refused by TWRP's `dd` (attempt 3 failed, attempt 4 worked). `misc` is on the LUN `sda`; the boot slots are on `sde`, so this does not show that a write to `sde` works from TWRP. | MEASURED (not re-checked against the raw log) | Install procedure of October 9, step 5; the Codex session's attempts 3 and 4. |
| 19 | `fastboot boot` of TWRP after a damaged boot or failed OPOS start may be refused. The exit must not be assumed to work. | REPORTED; NOT_RUN on this phone | PC research, November 8, 2026: two reports; G5b checks the exit only after a successful plain start. |
| 20 | The pinned TWRP contains toybox tools, BSD-style `dd` without `conv=` or `status=`, and `sgdisk`; `bootctl` and its HAL are absent. The boot-node `dd` reads and TWRP `blockdev` probes have not been run on this phone. | REPORTED for unpack; NOT_RUN for those phone commands | PC research, November 8, 2026; the pinned-image unpack. |

**PC research, November 8, 2026.** The following labels are the PC research's own; this session did not independently
verify them. **VERIFIED** there: the ABL strings identify the reported flashing error as `EFI_DEVICE_ERROR` below the
policy layer; boot, vbmeta and dtbo are not in the critical table. On October 31 only `boot_a` changed in the compared
backup hashes after a Linux-side Magisk write. Of 20 A/B firmware pairs only abl and sftpfw matched; slot b has older
firmware. ABL strings say `set_successful` is refused in customer mode. **REPORTED**: other Duo owners saw the same
fastboot error on the boot LUN and used TWRP `dd`; two reports describe a damaged slot rejecting TWRP RAM-boots.
**NOT_RUN**: TWRP `dd` to or from either boot slot, TWRP `blockdev --getro` and `--getsize64` on those nodes, and
the boot-slot `dd` hash read on this phone. The mechanism behind the fastboot failure remains unknown.

**Read-only observations from the PC session, November 11, 2026.** In the RAM-booted Halium initramfs,
`/dev/disk/by-partlabel` mapped `boot_a` to `sde9` and `boot_b` to `sde26`, each 100,663,296 bytes; there was no
`/dev/block/by-name` there. BusyBox 1.22.1 supplied `blockdev`, `dd`, `sha256sum` and `readlink`. This is a second
Linux-side environment besides TWRP, an untested alternative only: every `dd` or hash of an `sde` node in either
environment is **NOT_RUN**. Tool presence and the mapping do not establish that a boot-slot write works.

The RAM-boot command line's boot-image part was recorded as:

`rcupdate.rcu_expedited=1 rcu_nocbs=0-7 cgroup.memory=nokmem,nosocket androidboot.hardware=surfaceduo androidboot.hardware.platform=qcom androidboot.console=ttyMSM0 androidboot.memcg=1 lpm_levels.sleep_disabled=1 video=vfb:640x400,bpp=32,memsize=3072000 msm_rtb.filter=0x237 service_locator.enable=1 swiotlb=2048 loop.max_part=7 androidboot.usbcontroller=a600000.dwc3 kpti=off buildvariant=user console=tty0 datapart=/dev/sda6 droidian.lvm.disable page_poison=off debug_pagealloc=off`.

On `fastboot boot` the bootloader appended further arguments; the PC record gives `androidboot.bootloader=2022.815.527`,
`verifiedbootstate=orange`, `slot_suffix=_a`, `dtbo_idx=4`, `dtb_idx=0`, `force_normal_boot=1` and
`product.hardware.sku=att`, followed by an ellipsis. These reproduce the record's abbreviated names, not a complete
transcription of the appended part. What it appends on a plain start from the slot
remains unknown. The same session observed Android's in-place metadata encryption of OPOS userdata, leaving the rootfs
unreadable to Linux.

**What the experiment settles.** G1 and G2 inventory and probe; G3 tests an identity write to `boot_b`; G3b rehearses
the `boot_a` backup restore; G4 writes OPOS to `boot_a`; G5 tests a menu start; G5b tests one TWRP RAM-boot exit after
that start. G6 is a further menu-start observation, not a powered-off start. None of these settles *why* fastboot refused or proves recovery after a failed
start. Every future phone result is **NOT_RUN** until recorded.

## Ground rules

- Never write the bootloader chain: xbl, xbl_config, abl, tz, hyp, keymaster, devcfg, aop, qupfw, uefisecapp, cmnlib, sfpd,
  devinfo, apdp, uefivarstore, ddr, modem, dsp, and the calibration and identity partitions persist, modemst1, modemst2, fsg,
  fsc. There is no rescue.
- Never run `flashing lock`, `flashing unlock` or any `oem` command. Never write `vbmeta`, `dtbo` or `super`.
- Never fastboot flash or erase boot, vbmeta or dtbo. Never write `misc` in these gates; never routinely set active slot b.
- Never run `reboot` or `systemctl reboot` on a RAM-booted OPOS; use `reboot bootloader`
  ([docs/duo-usb-install.md](duo-usb-install.md)).
- One RAM-boot of the OPOS kernel per attempt, never retried; a crashed kernel can leave a slot rejecting more
  ([packaging/duo-os/README.md](../packaging/duo-os/README.md)).
- Stop at the first surprise. A surprise is any output that does not have the shape given under "Expected", any hash that
  differs, any tool that hangs. Do not try the step again until you know why.
- `fastboot` exits with status 0 even when it prints `FAILED`: read the text, not the exit status. Use `adb exec-out`
  for binary data; `adb shell` can turn line feeds into CRLF on Windows.
- The probe script writes only to a folder on the PC. That folder holds the phone's private data (serial number and
  copies of its partitions): keep it outside every git repository (the script refuses otherwise) and off shared drives.

Setup used by the commands below. Replace the placeholders; nothing in this repository fills them for you.

```powershell
$repo   = '<this repository>'                                   # holds deploy\duo-persist-probe.ps1
$images = '<folder with boot.img, surfaceduo1-twrp.img, misc-brake.img>'
$probe  = '<a private folder outside every git repository>'
$serial = '<the Duo''s serial as fastboot devices prints it>'
```

## G0. Preflight (read-only)

**Precondition.** The prerequisites above; the phone in the bootloader menu (Boot Select) on a cable that carries data. To
get there from off: hold Volume-down, add Power, release Power when the screen changes, keep Volume-down until the menu
shows ([Button steps](duo-usb-install.md)). Do not pick Start, Recovery Mode or Factory data reset.

**Commands (PC, PowerShell).**

```powershell
# 1. The three pinned images must hash to the values deploy\duo-opos-boot.ps1 pins.
Get-FileHash -Algorithm SHA256 "$images\boot.img", "$images\surfaceduo1-twrp.img", "$images\misc-brake.img" | Select-Object Hash, Path
# 2. See every command the probe will run, without touching anything (no tool is looked up).
powershell -ExecutionPolicy Bypass -File "$repo\deploy\duo-persist-probe.ps1" -Phase bootloader -DryRun
powershell -ExecutionPolicy Bypass -File "$repo\deploy\duo-persist-probe.ps1" -Phase twrp -DryRun -BootImage "$images\boot.img"
# 3. The preflight itself and the bootloader's answers (the phone must be in the bootloader menu).
powershell -ExecutionPolicy Bypass -File "$repo\deploy\duo-persist-probe.ps1" -Phase bootloader -Serial $serial -OutDir $probe
```

Pinned hashes: `boot.img` `abec1fe9ce4cbb7a9296c3abd4fd7b4acc1283842e86f2bfe0879a7e84fae552`; `surfaceduo1-twrp.img`
`457d8d2397b83eeffb843090da17d7959bcef7ed08658591938b3cda27b8132f`; `misc-brake.img`
`892627cb4356d1d16c37bf243d700b5c9a0ab672d263de9ccd640c86b5cd6a5a`.

**Expected.** Step 3 prints one line per step, each `ok` (the `b00` to `b04` steps are the preflight: product surfaceduo,
current slot a, unlocked yes, battery 20 percent or more), ends with `Done. Nothing was written to the phone.` and leaves
`<date>-<time>-bootloader\` in `$probe` with `report.md`, `probe.json` and `raw\`. Fastboot getvar answers are text on
stderr that the script reads.

**What it proves.** This is the phone the plan was written for, in the state the plan assumes, and the tools can talk to it.

**Decision table.**

| Outcome | Action |
|---|---|
| Hash differs | Stop. Get the pinned image again; do not use another. |
| `STOPPED at b01` (product) | Wrong phone or wrong mode. Stop. |
| `STOPPED at b02` (slot) | The runbook assumes slot a. Do not "fix" it with `set_active`; ask the owner. |
| `STOPPED at b03` (not unlocked) | Stop. Do not unlock: the bootloader wipes data on a lock-state change. |
| `STOPPED at b04` (battery) | Charge, then run again into a new folder. |
| Several fastboot devices | Pass `-Serial`. |
| Every fastboot command times out | A known trap after a multi-chunk sparse flash. Hold Power until the screen is dark (twenty to thirty seconds), enter the bootloader menu again, run once more. |
| `STOPPED` with `timed out:` | The script kills a command that has not finished after `-TimeoutSec` seconds (180 unless you pass another value) and stops with that reason; it does not wait for a hung `fastboot` or `adb`. Same cure as the row above. Raise `-TimeoutSec` only for a slow cable. |

**Rollback.** None: nothing was written.

## G1. Inventory and backup (read-only)

**Precondition.** G0 passed. The probe folder is on a disk with room for about 300 MiB.

State first what is unknown: `misc` may or may not hold the brake. A TWRP crash could lead to stock Android starting
on foreign userdata and a wipe. The brake is not a measured protection; PC research dated November 8, 2026 reports
that the same bytes were present before any project write. No `misc` write is part of these gates.

**Commands (PC, PowerShell, phone in the bootloader menu).**

```powershell
# 1. RAM-boot the pinned TWRP. Do not mount /data in it. TWRP identifies itself as another phone model; that is expected.
fastboot -s $serial boot "$images\surfaceduo1-twrp.img"
# 2. Wait until adb lists the phone as recovery.
adb devices
# 3. Inventory, backup and the G2 probes, in one read-only run.
powershell -ExecutionPolicy Bypass -File "$repo\deploy\duo-persist-probe.ps1" -Phase twrp -Serial $serial -OutDir $probe -BootImage "$images\boot.img"
# 4. Check the backups once more, independently of the script.
$run = (Get-ChildItem $probe -Directory | Where-Object { $_.Name -like '*-twrp' } | Sort-Object Name | Select-Object -Last 1).FullName
Get-Content "$run\SHA256SUMS" | ForEach-Object { $h, $f = $_ -split '  ', 2; $a = (Get-FileHash -Algorithm SHA256 "$run\$f").Hash.ToLower(); if ($a -eq $h) { "ok $f" } else { "DIFFERENT $f" } }
```

Phone side (what step 3 runs in the TWRP shell through adb, all reads): `cat` of `/proc/cmdline`, `/proc/mounts`,
`/proc/partitions` and `/sys/class/block/<node>/ro`, `ls -l` of the by-name directory, `df /tmp`, `readlink -f`,
`blockdev --getsize64` and `--getro`, `sha256sum` of each listed partition, `sgdisk -p` and `-i`, `dmesg`, and
`dd if=<block> bs=4096 count=<n> 2>/dev/null` piped to the PC with `adb exec-out`. It names no output file, so it writes
to stdout. The full list is the allow-list at the top of the script. The script does not call `getprop` or `bootctl`.

**The tools in the pinned TWRP** (row 20; an unpack of the pinned image, nothing run on a phone). Toybox 0.7.6 supplies
`blockdev`, `readlink`, `sha256sum`, `sync`, `df`, `head`, `dmesg`, `getenforce`, `ls` and `cat`. `dd` is a separate
BSD-style binary: it takes `if=`, `of=`, `bs=`, `count=` and `skip=` as plain decimal numbers, has `conv` disabled
(measured on October 9) and no `status=`, so this runbook uses only `if=`, `of=`, `bs=` and `count=`. `sgdisk` is
present. **`bootctl` and the boot-control HAL are absent**, so the probe does not call `bootctl`, and nothing in this
runbook relies on it. PC research dated November 8, 2026 labels TWRP `dd` to or from the boot slots and TWRP
`blockdev --getro` and `--getsize64` on them **NOT_RUN** on this phone. The `exec-out dd` pull is also **NOT_RUN**;
the October 9 backup tool used `exec-out head -c <size> <block>`. If the pull has the wrong size, stop and investigate;
only after the cause is understood, a new folder and `-PullWith head` select that earlier form.

**Expected.** One `ok` line per step, `Done. Nothing was written to the phone.`, and in the folder: `boot_a.img`,
`boot_b.img`, `vbmeta_a.img`, `vbmeta_b.img`, `dtbo_a.img`, `dtbo_b.img`, `misc.img`, `sde-table-head.bin`, `SHA256SUMS`,
`report.md`, `probe.json`, `raw\`. Sizes: 100,663,296 bytes for `boot_a` and `boot_b`, 65,536 for the vbmeta images,
16,777,216 for the dtbo images, 1,048,576 for `misc` and for the table head. Step 4 prints `ok` for every file.
Each partition was hashed on the phone, copied, hashed on the PC and read a second time; all three hashes agree.

**What it proves.** The by-name links resolve to the nodes in the October 31 partition table (`boot_a` is `sde9`, `boot_b`
`sde26`, `vbmeta_a` `sde14`, `vbmeta_b` `sde31`, `dtbo_a` `sde15`, `dtbo_b` `sde32`, `misc` `sda5`), none of them is mounted,
and the backups match the phone reads and PC hashes. It proves nothing about whether a write would be accepted;
the saved partition table head is not a complete backup of all slot-selection state.

**Decision table.**

| Outcome | Action |
|---|---|
| `STOPPED` at a `-pull` step with "short or long read" on the first pull | The `dd` pull form did not work on this TWRP (the one unmeasured form). Run again into a new folder with `-PullWith head`. If that stops too, stop. |
| Any other `STOPPED`, any hash that differs between phone, PC file and second read | **Stop.** Nothing was written. Suspect the cable, the port or the adb version; change one thing and run into a new folder. Do not go on with a backup that failed once. |
| `STOPPED` at a `readlink` or size step | The layout is not the one in this runbook. Stop; the nodes in G3 would be wrong. |
| `STOPPED` because the state is not `recovery` | The script does not change modes. Put the phone in TWRP and run again. |
| `boot_b` hashes to `cf6baf1d7940f2bf3d982e2645e251c0d7edcf89de4e0a6a7ff8ea620cdf8094` | As on October 31. Expected. |
| `boot_b` hashes to anything else | Stop and ask the owner: something wrote the inactive slot. |
| `boot_a` hashes to `bdd438e8fb798344f465deb275e821a9ae8399bd421c90dce7ec7042c02c873f` (patched) or `11a3e3979b92664d1900ef88dffe265fa94ebff865002225e6f33d9cb7867c39` (stock) | Known. Record which. |
| `boot_a` hashes to anything else | Record it; ask the owner before G3b or G4. |
| `misc` first 2048 bytes: equal to the brake, all zero, other | Record it for G5. "Other" means a request is pending: ask the owner. |
| `dtbo_a` or `dtbo_b` not present | The script skips them and says so. Not a failure. |

**Rollback.** None needed: nothing was written. TWRP lives in RAM; `adb reboot bootloader` returns to the menu.
Copy the probe folder to a second place now. Do not go to G3 while the backups exist only once.

## G2. Writability probes and the summary for the owner (read-only)

**Precondition.** G1 passed. The probes are rows of the two reports already produced (the `G2` rows of the bootloader run
and of the TWRP run); there is nothing new to run.

**Commands (PC, PowerShell).**

```powershell
Get-Content "$run\report.md"
Get-Content ((Get-ChildItem $probe -Directory | Where-Object { $_.Name -like '*-bootloader' } | Sort-Object Name | Select-Object -Last 1).FullName + '\report.md')
```

**Expected.** `report.md` opens with "Summary for the owner", then the backups table, the first 2048 bytes of `misc`, the
read-only flags (`blockdev --getro` and `/sys/class/block/<node>/ro` for every target and for the whole `sde` LUN), the
the free space of the phone's `/tmp` (`df /tmp`, the room for the 96 MiB image that G3 pushes), and the candidate image
check. Probes marked "unavailable" (for example `sgdisk` missing from this TWRP) are not failures. The bootloader report holds the full `getvar all` in `raw\b99.txt`
and the answers for `is-userspace`, `has-slot:boot`, `partition-size` and `partition-type` of `boot`, `boot_a` and `boot_b`
(an error for the plain name `boot` is information), and the slot counters.

**What it proves.** Only what the phone *reports*. A read-only flag of 0 removes the cheapest reason for a write to fail;
it does not show that the write will be accepted (a UFS write protect, or a rule inside the bootloader, would not show
here). The probes are where a "no" can show up before any write is tried.

**Decision table.**

| Reading | Meaning | Action |
|---|---|---|
| `ro` is 1 for `boot_a`, `boot_b` or the `sde` LUN, in either source | Write-protected on the kernel side | Stop. Go to "What if the probes say no". |
| `ro` is 0 everywhere | Open on the kernel side; not proof | May present the risks to the owner at G2b. |
| `is-userspace: no` | The bootloader's fastboot, as expected | Note it. (fastbootd would say yes; it was not tried.) |
| `partition-size:boot_a` is `0x6000000` and the plain `boot` is an error | The bootloader maps the slot names, not the plain name | Record it; no fastbootd experiment is part of these gates. |
| `slot-unbootable:b` is yes, or `slot-retry-count:b` is 0 | Slot b is not usable as a boot target | Stop: the proposed emergency exit is not established. |
| `slot-successful:a` is yes, `slot-unbootable:a` is no, retry count is present and nonzero, and current slot is a | Baseline for the in-place write and later comparison | Record these and both slots' successful, unbootable and retry-count values from G0. Stop on missing or unexpected values; do not use `set_active` to repair them. |
| `sgdisk -i 9` and `-i 26` printed attribute flags | Baseline for the slot bits before and after | Keep with the backups. |
| `sgdisk` unavailable | No slot attribute baseline from the phone's side | Not a failure; note that the baseline is missing. (`bootctl` is not probed: the pinned TWRP does not have it, row 20.) |
| `df /tmp` shows less than 114688 available (1K blocks) | `/tmp` cannot take the 96 MiB image with some margin | Stop; G3 would fail at the push. Ask. |
| Candidate image: `Fits boot_b` false, magic not `ANDROID!` | Wrong file | Stop. |
| `misc` content "other" | A request may be pending in the BCB | Ask the owner. |

**The summary for the owner.** The operator pastes into chat: the "Summary for the owner" block of both reports, plus a
line each for: what `boot_a` and `boot_b` hold, the read-only flags, the slot counters, the `misc` state, and "nothing was
written". Then the operator **stops and waits** for G2b. Silence is not consent.

**Rollback.** None: nothing was written.

## G2b. Owner accepts the last-resort risk [OWNER SAYS GO]

**Precondition.** G0 through G2 passed and two separately checked backups exist. Present the current results and all
unknowns to the owner. The slot-b emergency exit is not guaranteed: **VERIFIED** in the PC research dated November 8,
2026, its firmware differs and is older; **REPORTED**, a damaged slot may reject a TWRP RAM-boot. Anti-rollback of the
older chain is **UNKNOWN**. If neither TWRP nor the older chain starts, no rescue is known to this project. Writing
slot a in place has no demonstrated automatic fallback. Neither the backup nor a successful identity write guarantees
recovery after a failed boot.

**Decision.** Before any write, the owner must explicitly accept that last-resort risk in chat. Then each subsequent
gate needs its own explicit go. Silence, a general request to investigate, or approval of this documentation is not
acceptance. The owner decides whether to proceed; the operator cannot authorize it on the owner's behalf. Stop if the
owner declines or any precondition is missing. No phone command is run at this gate.

## G3. Identity write of inactive boot_b [OWNER SAYS GO]

**Precondition.** G2b acceptance is recorded; the owner has said "go G3". G1 and G2 passed, both backup copies were
checked, `boot_b` matches the known stock backup, and TWRP is running with battery at least 20 percent. This relaxes
the standing boot-write rule only for this identity write and its rollback. It does not select slot b or put OPOS there.

**Commands (PC, PowerShell).** Define these helpers in the same window. They contain the only partition-writing `dd`:

```powershell
$ByName = '/dev/block/platform/soc/1d84000.ufshc/by-name'

function Assert-Equal([string]$What, [string]$Got, [string]$Want) {
    if ($Got -cne $Want) { throw "STOP: $What is '$Got', expected '$Want'. Nothing further was run." }
    Write-Host "ok  $What = $Got"
}
function Invoke-Adb {                    # adb for $serial; a non-zero exit stops the block
    $ErrorActionPreference = 'Continue'  # a progress line on stderr is not an error here; the exit code is what counts
    $global:LASTEXITCODE = $null
    $out = & adb -s $serial @args
    if ($LASTEXITCODE -ne 0) { throw "STOP: adb $args exited with $LASTEXITCODE" }
    ($out | Out-String).Trim()
}
function Invoke-Fastboot {               # fastboot exits 0 even on FAILED, so the text is what is checked
    $ErrorActionPreference = 'Continue'
    $text = (& fastboot -s $serial @args 2>&1 | ForEach-Object { "$_" }) -join "`n"
    if (($text -match 'FAILED|error:') -or ($text -notmatch 'OKAY|Finished')) { throw "STOP: fastboot $args said: $text" }
    $text
}
function Get-PhoneSha([string]$Path) { ((Invoke-Adb shell sha256sum $Path) -split '\s+')[0].ToLower() }
function Read-Sums([string]$Path) {      # SHA256SUMS of the G1 backup: file name -> hash
    $sums = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        $hash, $file = $line -split '  ', 2
        if ($file) { $sums[$file.Trim()] = $hash.Trim().ToLower() }
    }
    $sums
}
function Test-Recovery {
    $ErrorActionPreference = 'Continue'
    (& adb devices | Out-String) -match ('(?m)^' + [regex]::Escape($serial) + '\s+recovery\s*$')
}
function Wait-Recovery([int]$Seconds = 180) {
    $until = (Get-Date).AddSeconds($Seconds)
    while (-not (Test-Recovery)) {
        if ((Get-Date) -gt $until) { throw "STOP: adb did not list $serial as recovery within $Seconds seconds. Hold Power until the screen is dark and start again from the bootloader menu." }
        Start-Sleep -Seconds 3
    }
}
function Wait-Fastboot([int]$Seconds = 120) {
    $ErrorActionPreference = 'Continue'
    $until = (Get-Date).AddSeconds($Seconds)
    while ((& fastboot devices | Out-String) -notmatch ('(?m)^' + [regex]::Escape($serial) + '\s+fastboot\s*$')) {
        if ((Get-Date) -gt $until) { throw "STOP: fastboot did not list $serial within $Seconds seconds. Hold Power until the screen is dark and start again." }
        Start-Sleep -Seconds 3
    }
}

function Write-BootPartition {
    [CmdletBinding(DefaultParameterSetName = 'Write')]
    param(
        [Parameter(Mandatory)][ValidateSet('boot_a', 'boot_b')][string]$Name,
        [Parameter(Mandatory)][string]$Image,                                           # a file on the PC
        [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}\z')][string]$ImageHash,    # its SHA-256, recorded earlier
        [Parameter(Mandatory, ParameterSetName = 'Write')][ValidatePattern('^[0-9a-f]{64}\z')][string]$CurrentHash,
        [Parameter(Mandatory, ParameterSetName = 'Restore')][switch]$Restore
    )
    $node = @{ boot_a = '/dev/block/sde9'; boot_b = '/dev/block/sde26' }[$Name]
    # Every check throws before the dd below.
    Assert-Equal 'adb state' (Invoke-Adb get-state) 'recovery'
    Assert-Equal "size of $Image in bytes" ([string](Get-Item -LiteralPath $Image).Length) '100663296'
    Assert-Equal "SHA-256 of $Image on the PC" ((Get-FileHash -Algorithm SHA256 -LiteralPath $Image).Hash.ToLower()) $ImageHash
    Assert-Equal "where $ByName/$Name points" (Invoke-Adb shell readlink -f "$ByName/$Name") $node
    Assert-Equal "size of $node in bytes" (Invoke-Adb shell blockdev --getsize64 $node) '100663296'
    Assert-Equal "read-only flag of $node" (Invoke-Adb shell blockdev --getro $node) '0'
    $own = @($node, '/dev/block/sde', "$ByName/$Name", "/dev/block/by-name/$Name")
    $mounted = @((Invoke-Adb shell cat /proc/mounts) -split "`n" | Where-Object { ($_.Trim() -split '\s+')[0] -in $own })
    Assert-Equal "mounts of $node" ([string]$mounted.Count) '0'
    $before = Get-PhoneSha $node
    if ($PSCmdlet.ParameterSetName -eq 'Write') { Assert-Equal "SHA-256 of $node now (its G1 backup)" $before $CurrentHash }
    else { Write-Host "restoring: $node holds $before now" }
    Invoke-Adb push $Image /tmp/opos-image.img | Out-Null
    Assert-Equal 'SHA-256 of the pushed image on the phone' (Get-PhoneSha /tmp/opos-image.img) $ImageHash
    Invoke-Adb shell dd if=/tmp/opos-image.img "of=$node" bs=4096 | Write-Host          # WRITE: boot_a or boot_b only
    Invoke-Adb shell sync | Out-Null
    Invoke-Adb shell 'echo 3 > /proc/sys/vm/drop_caches' | Out-Null                    # cache control, not a storage write
    Assert-Equal "SHA-256 of $node after the write" (Get-PhoneSha $node) $ImageHash
}
```

The guards require recovery state, the expected link, full size, `--getro` of 0, no mount of the target or LUN, the
recorded backup hash before a normal write, and a verified pushed image. The image and the full partition must be
100663296 bytes. `sync`, cache drop and hash read-back follow the write. The pinned TWRP's `dd` refuses `conv=`
(PC research dated November 8, 2026; the October 9 result); it also has no `status=`. Use no `conv=` option.

The first write puts the exact G1 `boot_b` backup back onto `boot_b`:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $sums = Read-Sums "$run\SHA256SUMS"
    Write-BootPartition -Name boot_b -Image "$run\boot_b.img" -ImageHash $sums['boot_b.img'] -CurrentHash $sums['boot_b.img']
}
```

Cold re-read in a fresh TWRP RAM-boot; compare both slots:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $sums = Read-Sums "$run\SHA256SUMS"
    Invoke-Adb reboot bootloader | Out-Null
    Wait-Fastboot
    Invoke-Fastboot boot "$images\surfaceduo1-twrp.img" | Out-Null
    Wait-Recovery
    Assert-Equal 'cold SHA-256 of boot_b' (Get-PhoneSha /dev/block/sde26) $sums['boot_b.img']
    Assert-Equal 'cold SHA-256 of boot_a (untouched)' (Get-PhoneSha /dev/block/sde9) $sums['boot_a.img']
}
```

**Expected.** Both warm and cold hashes match the backups. `dd` may report `24576+0 records` or another summary;
the hashes are the check. Slot b remains stock. No `misc` or slot-selection write is made.

**What it proves.** If successful, the identity write command returned and the original bytes read back exactly. Because those bytes were
already present, even warm and cold hashes cannot detect a write silently ignored by the device. It does not prove that
a different payload can be written or that a failed write can be recovered.

**Decision table.**

| Outcome | Action |
|---|---|
| A pre-write guard fails | Stop before `dd`; investigate. |
| Write fails or either hash differs | Stop; do not start G3b. If `boot_b` differs, use the G3 rollback; if `boot_a` differs, use the G4 `boot_a` backup rollback. Restore either only while TWRP is reachable, then cold verify both hashes against G1 and record the result. |
| Both cold hashes match | Report and stop for the owner's separate G3b decision. |

**Rollback (restores `boot_b` to its G1 content).** This is a write and is contingent on reaching TWRP:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $sums = Read-Sums "$run\SHA256SUMS"
    if (-not (Test-Recovery)) { Invoke-Fastboot boot "$images\surfaceduo1-twrp.img" | Out-Null }
    Wait-Recovery
    Write-BootPartition -Name boot_b -Image "$run\boot_b.img" -ImageHash $sums['boot_b.img'] -Restore
}
```

After a rollback, cold re-read in a fresh TWRP and compare both slots to G1. If TWRP is unreachable, stop; there is no
proven rollback. Do not keep retrying a crashed kernel from the same slot.

## G3b. Rehearse the boot_a backup restore [OWNER SAYS GO]

**Precondition.** G3 passed its cold read; the owner has said "go G3b" after G2b. The current `boot_a` hash still
matches the checked G1 backup and that backup's identity has been reviewed. This is an identity restore rehearsal,
not an OPOS write. If G1 captured a patched image, it restores that image, not stock Android. Identify an appropriate stock backup before
claiming a return to stock Android; G3b must rehearse the same backup that G4 rollback would use.

**Commands.** In TWRP, use the G3 helper; require the current hash before invoking its restore mode:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $sums = Read-Sums "$run\SHA256SUMS"
    Assert-Equal 'boot_a before restore rehearsal' (Get-PhoneSha /dev/block/sde9) $sums['boot_a.img']
    Write-BootPartition -Name boot_a -Image "$run\boot_a.img" -ImageHash $sums['boot_a.img'] -Restore
}
```

Reboot to the bootloader, RAM-boot a fresh TWRP once, then compare `boot_a` and `boot_b` to their G1 hashes, as in
G3's cold re-read. Record commands, results and hashes. The warm read alone is insufficient.

**Expected.** Both hashes equal G1 in the fresh TWRP. **What it proves:** the command returned and the backed-up bytes
read back on this occasion. Because the target already matched the backup, this rehearsal cannot detect a silently
ignored write or demonstrate restoration after different bytes were present. It does not prove a later rescue will be reachable. **Decision table:** on any error, mismatch,
or unreachable TWRP, stop; do not write OPOS. **Rollback:** if the target differs and TWRP remains reachable, the G4
rollback describes the guarded backup restore; cold verify it. A restore rehearsal must pass before G4.

## G4. Write OPOS to boot_a in place [OWNER SAYS GO]

**Precondition.** G3 and G3b passed including cold reads; the owner has said "go G4". The OPOS rootfs is already on
userdata under a separately authorized installation, battery is at least 20 percent, and TWRP is running. `boot_b`
remains stock. Slot a remains active; never routinely `set_active b` and never put OPOS into both slots.

**Commands (PC, PowerShell).** First prepare a padded copy on the PC. The full image is the pinned `boot.img`. An
optional first payload is `boot-nowrite.img` (PC research dated November 8, 2026 reports a November 4 RAM-boot);
its provenance and hash must be independently identified and accepted by the owner before use. Its plain start remains
**NOT_RUN**. The block below prepares the pinned full image; do not silently substitute another image.

```powershell
$work = '<a folder on the PC>'
$pad  = "$work\opos-boot-padded.img"
Copy-Item -LiteralPath "$images\boot.img" -Destination $pad -Force
$fs = [System.IO.File]::OpenWrite($pad); $fs.SetLength(100663296); $fs.Dispose()
Assert-Equal 'size of the padded image' ([string](Get-Item -LiteralPath $pad).Length) '100663296'
$padHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $pad).Hash.ToLower()
$padHash                                                       # write this down
# The first bytes of the padded file must still be the pinned image (G0 checked boot.img itself: abec1fe9ce4c...):
$all = [System.IO.File]::ReadAllBytes($pad)
$n = (Get-Item -LiteralPath "$images\boot.img").Length
$head = ([BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($all, 0, $n)) -replace '-', '').ToLower()
Assert-Equal 'the first bytes of the padded image hash like boot.img' $head (Get-FileHash -Algorithm SHA256 -LiteralPath "$images\boot.img").Hash.ToLower()
$all = $null
```

If choosing the optional image, stop for a separately reviewed preparation and recorded hash; no hash is pinned here.
Before any later full-image replacement, repeat the applicable gates and separately authorize it; the current-hash
guard must use the independently recorded cold hash of the optional image, not the old G1 hash. Preserve G1 for restore.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $sums = Read-Sums "$run\SHA256SUMS"
    Write-BootPartition -Name boot_a -Image $pad -ImageHash $padHash -CurrentHash $sums['boot_a.img']
}
```

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $sums = Read-Sums "$run\SHA256SUMS"
    Invoke-Adb reboot bootloader | Out-Null
    Wait-Fastboot
    Invoke-Fastboot boot "$images\surfaceduo1-twrp.img" | Out-Null
    Wait-Recovery
    Assert-Equal 'cold SHA-256 of boot_a' (Get-PhoneSha /dev/block/sde9) $padHash
    Assert-Equal 'cold SHA-256 of boot_b (stock)' (Get-PhoneSha /dev/block/sde26) $sums['boot_b.img']
    Invoke-Adb reboot bootloader | Out-Null
    Wait-Fastboot
}
```

**Expected.** Warm and fresh-TWRP cold hashes of `boot_a` equal the padded image; `boot_b` equals its G1 stock backup.
**What it proves:** the bytes read back, not that OPOS starts or that recovery remains reachable. No slot or `misc`
write is made.

**Decision table.**

| Outcome | Action |
|---|---|
| A pre-write guard fails | Stop before `dd`. |
| Write or read-back fails | Stop; restore `boot_a` if TWRP is reachable, then cold verify. |
| Both cold hashes match | Report and wait for G5. |

**Rollback (boot_a).** This uses the G1 backup and requires TWRP to remain reachable; it may be patched rather than
stock, as recorded in G1. Verify the restored partition in a fresh TWRP and compare both slot hashes to G1.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $sums = Read-Sums "$run\SHA256SUMS"
    if (-not (Test-Recovery)) { Invoke-Fastboot boot "$images\surfaceduo1-twrp.img" | Out-Null }
    Wait-Recovery
    Write-BootPartition -Name boot_a -Image "$run\boot_a.img" -ImageHash $sums['boot_a.img'] -Restore
}
```

## G5. First menu start [OWNER SAYS GO]

**Precondition.** G4 passed, slot a remains active, slot b is stock, and the owner has said "go G5". A plain start may
reach stock Android on foreign userdata and its wipe path; the brake is unproven and no misc write is authorized here.

**Commands.** Record the cable state before starting. From Boot Select, choose Start once. At the orange unlocked warning,
**Press nothing else.** Observe for up to five minutes, a safety limit rather than a measured start time. If a cable or
charger connection causes an unexpected start, record the cable state and time; treat it as the single attempt, do not
press Start or Power again, and observe under the same timeout and failure rules. Charger-triggered behavior is unknown.
Success requires OPOS visible on the screens **and** the PC finding its USB network and SSH answering; record both.
Do not choose factory reset while either slot holds OPOS. Do not use plain software `reboot` from persistent OPOS until
a power cycle has been observed and separately approved; from RAM-booted OPOS never use plain `reboot`.

After a successful start, use `reboot bootloader` to reach Boot Select, run the G0 bootloader probe into a new folder,
and compare `current-slot`, `slot-successful`, `slot-unbootable` and `slot-retry-count` for both slots against the G0
baseline. Stop on missing, failed or unexpected values, including a changed or decreasing retry count; do not use
`set_active` to repair them. Do not leave the phone unattended until any discrepancy and countdown behavior are understood.

**Expected.** OPOS starts from slot a, meets both success criteria, and the slot values match G0. **What it proves:**
only the observed menu start and recorded counters, not a powered-off start, continued recovery or charger behavior.
**Decision table:** on recovery, Android setup, a black screen, a restart loop, or timeout, stop: hold Power until the
screen is dark (about twenty to thirty seconds), then hold Volume-down plus Power to enter Boot Select as in G0. Handle
the cable deliberately, record whether it is attached, and watch for an automatic start when connecting it; do not
automatically retry a crashed kernel, write `misc`, or switch slots. Do not select factory reset. If the phone cannot be
stopped or the menu reached, stop and report; rescue is unproven. **Rollback:** if the bootloader and TWRP remain
reachable, restore `boot_a` with G4's rollback and cold verify; otherwise stop and report that no rescue was established.

## G5b. Test the TWRP exit after the menu start [OWNER SAYS GO]

**Precondition.** G5 actually started OPOS and the owner has said "go G5b". This gate is **NOT_RUN**. A successful
check does not prove the exit would work after a failed start.

**Commands.** After G5's post-start slot comparison, RAM-boot the pinned TWRP from Boot Select
once using `fastboot boot`; wait for adb recovery. No `misc` or slot write is included. Stop if TWRP does not appear;
do not loop. Record the result and re-read both slot hashes against G4.

**Expected.** TWRP is reachable and both hashes match G4. **What it proves:** the exit worked once after this particular
menu start. **Decision table:** mismatch or no TWRP means stop and report; success permits a separate G6 decision.
**Rollback:** use G4's backup restore only if TWRP is reachable, with a fresh cold read; otherwise there is no proven
rollback.

## G6. Observe a further menu start [OWNER SAYS GO]

**Precondition.** G5b passed, results were recorded and the owner has said "go G6". No `misc` writes are included.
**Commands:** return to Boot Select from TWRP and select Start once. Record cable state and apply G5's orange-screen,
unexpected cable-triggered start, screen-plus-USB/SSH success criteria, timeout and physical failure response. After
success, return via `reboot bootloader`, run the G0 bootloader probe into another new folder, and compare `current-slot`,
`slot-successful`, `slot-unbootable` and `slot-retry-count` for both slots against G0. **Expected:** OPOS starts and
the values match G0. **What it proves:** only this observed menu start and counter comparison. **Decision table:** stop
on missing or unexpected results; prohibit unattended use until discrepancies and countdown behavior are understood.
Do not retry automatically, write `misc`, or use `set_active`. **Rollback:** G4's restore is conditional on TWRP
remaining reachable and must be cold verified.

A powered-off start remains **NOT_RUN**. It needs a separate owner-approved gate specifying cable state and handling
before the phone is powered off. A charger-triggered start is a separate unknown, not proof of power-button start.

## G7. What an over-the-air kernel update could then mean (a separate, later design)

No kernel OTA is authorized or designed by these gates. [docs/ota-update.md](ota-update.md) describes the current
update work. Preserving a stock emergency slot, write guards, boot success and rollback need separate design and owner
approval; until then OTA stays app-level only.

## Return to Android

Read-only observations from the PC session on November 11, 2026 record that any stock Android boot on OPOS userdata
applies metadata encryption in place and makes the OPOS rootfs unreadable to Linux. A power cycle that reaches Android
destroys the OPOS install; returning to Android is not a harmless exit and requires a separately informed decision.

The only return to stock Android performed on this phone was a factory reset from stock recovery on November 7, 2026.
It was possible because the OPOS installs had left boot, super and firmware untouched (PC research dated November 8,
2026). No partition restore from the backup or stock payload has ever been done or rehearsed. In particular, a `dd`
restore of `boot_a` from the backup has **NOT_RUN**. The G3b restore rehearsal must pass before the persistent OPOS write
in G4. A G1 backup may contain the patched image: identify a suitable stock backup before claiming a return to Android.
A later restore still depends on reaching TWRP, and factory reset must not be used while a slot holds OPOS.

## What if the probes say no

Keep the existing RAM-boot route, subject to its separate authorization and risks. If a layout, backup, read-only flag,
write, cold hash or TWRP reachability check fails, stop and record the result. Do not infer the cause from a refusal.
Entering fastbootd can write `misc` and is outside these gates; it requires a separate gate. Fastboot flash or erase of
boot, vbmeta and dtbo is never used. Do not try a slot switch or misc write as an automatic recovery.

## Never

- Fastboot flash or erase boot, vbmeta or dtbo; write the bootloader or firmware chain, including xbl, xbl_config, abl,
  tz, hyp, keymaster, devcfg, aop, qupfw, uefisecapp, cmnlib, devinfo, modem, dsp, persist, modemst, fsg or fsc.
- Use `sgdisk`, `bootctl` or `qbootctl` to write, edit GPT, change lock state, or run `oem set` or `oem clear`.
  The probe's `sgdisk -p` and `-i` are reads only.
- Routinely set active slot b, put non-stock images in both boot slots, or retry a crashed kernel from the same slot.
- Run `reboot` from RAM-booted OPOS, or Android OTA, Magisk direct install or factory reset while a slot holds OPOS.
- Enter fastbootd without a separate gate, use a multi-chunk sparse userdata flash, or use web EDL/firehose tools.
- Write `misc` in these gates. Nothing here grants a separate authorization for any excluded action.

## Unknown

PC research dated November 8, 2026 leaves these **NOT_RUN** or unknown on this phone: whether TWRP `dd` into the boot
LUN succeeds and reads back exact; whether TWRP RAM-boot survives OPOS in slot a or a failed start; whether a plain
start appends different command-line arguments or passes different dtbo data and starts the image; the powered-off
power-button start; how a powered-off phone reacts to a charger
(the initramfs could loop); and whether a successful slot-a flag avoids a countdown. Whether slot b's older firmware
passes anti-rollback is also unknown. The G5b check only addresses TWRP after a successful start, not after failure.

## Risks

No step here writes the bootloader chain. A wrong target or interrupted `dd` can still be serious. Android's wipe path
can destroy the OPOS userdata. The brake is unproven for plain starts. Never relock. Microsoft's restore depends on
stock recovery, and the `boot_a` backup could be patched. `fastboot` exits 0 on `FAILED`, so inspect its output.
The plan and probe were never run against hardware; all future device checks remain **NOT_RUN** until recorded.
