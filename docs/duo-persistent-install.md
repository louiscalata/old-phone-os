<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Persistent kernel on the Duo: the gated first device session

Status, November 7, 2026: **a plan; none of it has been run on a phone.** The probe script (`deploy/duo-persist-probe.ps1`)
and this text were tested against fake tools and file checks only. Nothing here authorizes a write to the phone: gates
G3, G4, G5 and G6 each need the owner's explicit word in chat, after the read-only gates have been shown to him. G3, G4
and G6 write; G5 is the first plain power-on, which can start Android's wipe path, so it needs the word too.

## The goal and what changes for the owner

Today the OPOS OS on the Surface Duo 1 keeps its root filesystem in the phone's storage (`/data/rootfs.img` inside the
ext4 userdata) but its **kernel is pushed from a Windows PC at every start** (`fastboot boot boot.img`, a RAM-boot).
A plain power cycle starts stock Android, which is expected not to accept the foreign userdata and to wipe it (that
sequence was not re-measured); the misc brake exists to stop that. The owner wants OPOS "installed like a regular Android OS": the kernel image in the phone's own
storage, so the power button starts OPOS with no PC, and, later, kernel updates over the air.

The project believes the bootloader refuses to let us write the boot partition. That belief rests on one log line
(see "What is known"). This runbook is a small experiment that finds out, safely, in this order: read, back up,
look at what the phone reports, and only then, one gate at a time and each on the owner's word, write.

If everything passes, the owner would see:

- **No PC at start.** Power button, Surface logo, the orange "unlocked" warning, then OPOS.
- **Stock Android's kernel (and the recovery inside it) gone from the slot that was written.** It is backed up in G1 and
  can be put back from TWRP. Boot Select's "Recovery mode" would then start OPOS's kernel, and Microsoft's sideload restore
  would need the stock image put back first (see "Risks").
- **The misc brake becomes optional**, but only after G6.

What does not change: the bootloader stays unlocked; no step writes the bootloader chain; vbmeta and dtbo are not
written; the install chain (`deploy/duo-opos-boot.ps1`) is untouched and keeps working as the fallback way to start OPOS.

**Prerequisites that this runbook does not solve.**

1. USB data must work: every command below needs fastboot and adb over USB. It worked on November 7 (the install chain
   pushed a 5.7 GB image over it); it was broken before that, so check it in G0 (the
   [packaging/duo-os/README.md](../packaging/duo-os/README.md) line that says otherwise is out of date).
2. G4 and later need the OPOS rootfs on userdata. That is the install chain's job, it erases Android's data, and it is
   a separate decision of the owner. G0 to G3 do not touch userdata and do not need the rootfs.
3. The pinned images in one folder (`boot.img`, `surfaceduo1-twrp.img`, `misc-brake.img`), battery 20 percent or more,
   the phone on a good cable.

## What is known, and what the experiment settles

Grades: **MEASURED** (a command output or a hash, with where it is), **REPORTED** (a document or a person, dated where the
source has a date), **INFERRED** (follows from measured or reported facts by reasoning; nobody has tried it), **UNKNOWN**.
A **HYPOTHESIS** is an UNKNOWN that is a candidate explanation, not a finding. "Re-checked" means compared against the
files while this runbook was written.

| # | Claim | Grade | Source |
|---|---|---|---|
| 1 | The bootloader refused `fastboot flash boot_a` with `remote: Device Error` although unlocked; `fastboot boot` worked. | REPORTED, re-checked | One line, item 34 of the October 31, 2026 install-friction log kept on the owner's PC. Every later document repeats it. |
| 2 | Nothing else about that attempt was kept: not the exact output, not the image, not raw or sparse, not which fastboot mode, not what `misc` held. | UNKNOWN | No raw tool output was found in the agent transcripts that were searched. |
| 3 | `vbmeta` is refused the same way. | UNKNOWN | [packaging/duo-os/README.md](../packaging/duo-os/README.md) says "reported"; no first-hand record exists. |
| 4 | The same bootloader's fastboot does write other partitions: on November 7 `erase misc`, `flash misc`, `format:ext4 userdata` and the boot of two images all answered OKAY. That is all this row says. | MEASURED, re-checked | The boot-chain log of the November 7 run (`boot-chain.log` beside the images). |
| 4b | HYPOTHESIS: the October 31 refusal is specific to the boot slots, not a general write block. Nothing measures it: row 1 is one reported line and row 2 lists what is missing. Other explanations fit the same facts: (a) a poisoned `misc` (the port's README says it makes the bootloader reject operations per slot, and describes a per-slot RAM-boot wedge that also prints `Device Error` while the getvars look normal); (b) the mode, a write that only fastbootd accepts (row 10); (c) an `EFI_DEVICE_ERROR` from the bootloader's storage layer for the boot LUN, which is what `Device Error` is in the firmware's own terms (INFERRED from the strings of the phone's bootloader image, where policy refusals have their own texts such as "Flashing is not allowed"). The port's cure order, if a slot rejects operations: hold Volume-down plus Power to reach the bootloader menu, RAM-boot TWRP once, zero the first 2048 bytes of `misc` from TWRP, switch slot, and never retry a crashed kernel from the last good slot (the last two steps are writes, so each needs the owner's word). | UNKNOWN (HYPOTHESIS) | The port's README ("Known kernel traps"), the port's trap table, the planning research. G1's reading of `misc` excludes (a) when it is all zero or the brake. |
| 5 | On October 31 `boot_a` held a Magisk-patched image (the hash equals the patched file prepared that day); the October 9 content was reported to be stock. So something wrote it. The reported route is Magisk "Direct Install" from a RAM-booted rooted session, a write made on the phone by root, not by fastboot. This is the strongest evidence we have that a root-side block write to the boot LUN (`sde`) works while `fastboot flash boot_a` was refused the same day. It says nothing yet about a write from TWRP. | October 31 hash MEASURED, re-checked; stock on October 9 and the route REPORTED; the conclusion about a root-side write INFERRED | `SHA256SUMS` of the 2026-10-31 backup; friction log item 34; the planning research. |
| 6 | `boot_b` is a complete stock image of an older build than slot a; slot b has never been started by this project. | hash match REPORTED by the planning research (not re-checked), starting it UNKNOWN | Backup hashes; AVB parse of `vbmeta_b`. |
| 7 | Do not copy a past bootloader snapshot into this runbook. G0 reads the current slot, lock state and battery on the day of the install. | Procedure | G0. |
| 8 | This bootloader has no `flashing unlock_critical`; changing the lock state makes it wipe data. Never run `flashing lock` or `flashing unlock`. | REPORTED (strings of the phone's own bootloader image), not re-checked | Planning research; the image is in the October 31 backup, so anyone can re-check. |
| 9 | The text `Device Error` also appears for unrelated causes (a slot that rejects RAM-boots after a crashed kernel, a poisoned `misc`). | REPORTED | The port's own README (undated). |
| 10 | The community (WOA-Project) dual-boot page for the Duo 1 reaches **fastbootd** (userspace fastboot) through stock recovery > "Enter fastboot" and runs `fastboot flash boot <image>` there, with the plain partition name. Its test steps use `adb reboot bootloader` and then `fastboot boot <image>`. It names no firmware version and no carrier. Never tried here. That the bootloader's own fastboot is used there only for `fastboot boot` is this runbook's reading of the page's steps, not a statement on the page. | REPORTED | https://github.com/WOA-Project/SurfaceDuo-Guides/blob/main/Install/DualBoot.md (undated page, read November 7 and November 8, 2026). Its Troubleshooting advice is a G5 row; it also says a bootloader cannot be relocked while the boot image modified by the guide is installed. |
| 11 | The port's own rule is to write the kernel to the slots only after it has started Linux on that phone, which the November 7 RAM-boot did. So the port does not forbid slot writes in general. The standing rule "never flash boot, vbmeta, dtbo or recovery" is the owner's, and G3 asks to relax it for one partition. | REPORTED, re-checked | The header of the port's `release-add-boot.sh`. |
| 12 | The OPOS kernel image fits: 33,972,224 bytes in a 100,663,296-byte partition, header version 2 like the stock images, no AVB footer, unsigned. | MEASURED | SHA-256 `abec1fe9ce4cbb7a9296c3abd4fd7b4acc1283842e86f2bfe0879a7e84fae552`, re-checked on this PC. |
| 13 | The bootloader continued to start a Magisk-patched `boot_a` whose AVB hash does not match its `vbmeta`, in the unlocked ("orange") state. So no vbmeta change is needed while the phone stays unlocked; **never relock**. | REPORTED (parse of the backup), not re-checked | Planning research. |
| 14 | Stock Android on a userdata it does not recognize writes a wipe request to `misc` and reboots to recovery, which offers a factory reset. The exact sequence on this phone was not re-measured. | REPORTED | AOSP recovery source; the October 31 factory reset; the port's "BCB poison" note. |
| 15 | No rescue route is known to this project for a damaged bootloader chain. The one page found is a volunteer's answer on Microsoft's Q&A (question 2026-03-29, answer 2026-03-30). It says the Snapdragon 855 has an EDL mode, that the device-specific Firehose programmer is not publicly released, that Microsoft has a paid out-of-warranty repair, and that some XDA users report partial success. None of that is a route the owner has. | REPORTED (one volunteer answer, partial reports) | https://learn.microsoft.com/en-us/answers/questions/5842635/how-to-fix-hard-brick-surface-duo. "No step touches the bootloader chain" is this project's rule, not something the page states. |
| 16 | Current content of `boot_a`, `boot_b` and `misc`, the read-only flags of the boot LUN, and whether a write from TWRP is accepted. | UNKNOWN | G1 to G3 find out. |
| 17 | What the bootloader does with the misc brake on a plain power-on. It has no literal `bootonce-bootloader` string; on October 31 a reboot with the brake armed reached stock recovery, not fastboot. | UNKNOWN (the November 7 chain worked with the brake armed, but only for RAM-boots) | Friction log item 33; planning research. |
| 18 | TWRP's `dd` wrote `misc` on October 9: the first 2048 bytes (`bs=2048 count=1`) of `sda5`, the brake of the install chain, and it was accepted. The attempt that used a `conv=` option was refused by TWRP's `dd` (attempt 3 failed, attempt 4 worked). `misc` is on the LUN `sda`; the boot slots are on `sde`, so this does not show that a write to `sde` works from TWRP. | MEASURED (not re-checked against the raw log) | Install procedure of October 9, step 5; the Codex session's attempts 3 and 4. |
| 19 | `fastboot boot` of TWRP works whatever the boot slots hold, so every rollback below can start. | INFERRED, not measured | `fastboot boot` takes the image from the PC and the bootloader menu does not read the boot slots. Against it: the port's README reports a slot that rejects RAM-boots with `Device Error` after a crashed kernel or a poisoned `misc` (row 4b). |
| 20 | The tools in the pinned TWRP (3.5.2_9, a Mi 9 build): toybox 0.7.6 with `blockdev`, `readlink`, `sha256sum`, `sync`, `df`, `head`, `dmesg`, `getenforce`, `ls`, `cat`; a separate BSD-style `dd` with `conv` disabled and no `status=`; `sgdisk`; a `/tmp`. **`bootctl` and the boot-control HAL are absent.** | REPORTED (an unpack of the pinned image, read-only; nothing was run on a phone). Measured on this TWRP by earlier sessions: the by-name path, `readlink -f`, `blockdev --getsize64`, `sha256sum` of a block device and `adb exec-out`. | The independent check of this runbook; the October 9 backup tool and the November 7 boot-chain log. The pull as `exec-out dd ... 2>/dev/null` itself was never run: the October 9 tool read with `exec-out head -c <size> <block>`. |

**What the experiment settles.** G1 to G2: whether the block devices are writable on the kernel side and what the phone
holds now. G3: whether a write from TWRP is accepted and reads back exact. G4 to G5: whether the OPOS kernel starts
from a slot and how the slot counters behave. G6: whether the power button alone suffices.
**What it does not settle:** *why* fastboot refused on October 31. G3 uses TWRP, not fastboot. Whether fastbootd would
accept the write (row 10) is a separate, later experiment, listed under "What if the probes say no".

## Ground rules

- Never write the bootloader chain: xbl, xbl_config, abl, tz, hyp, keymaster, devcfg, aop, qupfw, uefisecapp, cmnlib, sfpd,
  devinfo, apdp, uefivarstore, ddr, modem, dsp, and the calibration and identity partitions persist, modemst1, modemst2, fsg,
  fsc. There is no rescue.
- Never run `flashing lock`, `flashing unlock` or any `oem` command. Never write `vbmeta`, `dtbo` or `super`.
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

State first what is unknown: `misc` may or may not hold the brake (the last measured arming was before the November 7
kernel RAM-boot). The next command RAM-boots TWRP exactly as every earlier session did. If TWRP crashed and the phone
started stock Android on a userdata it does not recognize, the OS image on it could be wiped. The project's guard against
that is the brake, which is a write to `misc`; this runbook does not make that write before the owner has seen the
probes. If the owner wants it first, he says "arm the brake" and the operator runs the two commands of the install chain
(`fastboot erase misc`, then `fastboot flash misc "$images\misc-brake.img"`), nothing else.

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
runbook relies on it. What earlier sessions measured on this TWRP: the by-name path, `readlink -f`,
`blockdev --getsize64`, `sha256sum` of a block device and `adb exec-out`. The one step nobody has run is the pull as
`exec-out dd if=<block> bs=4096 count=<n> 2>/dev/null`; the October 9 backup tool read partitions with
`exec-out head -c <size> <block>`. If the `dd` pull returns the wrong size, the probe stops at the size check
("short or long read"); run it again into a new folder with `-PullWith head`, which uses the form proven on October 9.

**Expected.** One `ok` line per step, `Done. Nothing was written to the phone.`, and in the folder: `boot_a.img`,
`boot_b.img`, `vbmeta_a.img`, `vbmeta_b.img`, `dtbo_a.img`, `dtbo_b.img`, `misc.img`, `sde-table-head.bin`, `SHA256SUMS`,
`report.md`, `probe.json`, `raw\`. Sizes: 100,663,296 bytes for `boot_a` and `boot_b`, 65,536 for the vbmeta images,
16,777,216 for the dtbo images, 1,048,576 for `misc` and for the table head. Step 4 prints `ok` for every file.
Each partition was hashed on the phone, copied, hashed on the PC and read a second time; all three hashes agree.

**What it proves.** The by-name links resolve to the nodes in the October 31 partition table (`boot_a` is `sde9`, `boot_b`
`sde26`, `vbmeta_a` `sde14`, `vbmeta_b` `sde31`, `dtbo_a` `sde15`, `dtbo_b` `sde32`, `misc` `sda5`), none of them is mounted,
and we hold verified copies of everything G3 or G4 variant B could change, plus the partition table head that carries the
slot attributes. It does NOT cover G4 variant A: `set_active` may also change the UFS boot-LUN selector (REPORTED from the
bootloader strings) and G1 saves only the first 1 MiB of `sde`, with no read of the other LUNs' slot bits, so variant A has
no complete backup (the planner recommends variant B for that reason). It proves nothing about whether a write would be
accepted.

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
| `boot_a` hashes to anything else | Record it; ask the owner before G4 variant B. |
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
| `ro` is 1 for `boot_b` or for the `sde` LUN, in either source | Write-protected on the kernel side | Stop. Go to "What if the probes say no". |
| `ro` is 0 everywhere | Open on the kernel side; not proof | May go to the owner for G3. |
| `is-userspace: no` | The bootloader's fastboot, as expected | Note it. (fastbootd would say yes; it was not tried.) |
| `partition-size:boot_a` is `0x6000000` and the plain `boot` is an error | The bootloader maps the slot names, not the plain name | Note it for the fastbootd experiment. |
| `slot-unbootable:b` is yes, or `slot-retry-count:b` is 0 | Slot b is not usable as a boot target | G4 variant A is out; variant B or stop. |
| `slot-successful:a` is yes | Slot a is marked good; a write to it in place needs no `set_active` | Note it for G4. |
| `sgdisk -i 9` and `-i 26` printed attribute flags | Baseline for the slot bits before and after | Keep with the backups. |
| `sgdisk` unavailable | No slot attribute baseline from the phone's side | Not a failure; note that the baseline is missing. (`bootctl` is not probed: the pinned TWRP does not have it, row 20.) |
| `df /tmp` shows less than 114688 available (1K blocks) | `/tmp` cannot take the 96 MiB image with some margin | Stop; G3 would fail at the push. Ask. |
| Candidate image: `Fits boot_b` false, magic not `ANDROID!` | Wrong file | Stop. |
| `misc` content "other" | A request may be pending in the BCB | Ask the owner. |

**The summary for the owner.** The operator pastes into chat: the "Summary for the owner" block of both reports, plus a
line each for: what `boot_a` and `boot_b` hold, the read-only flags, the slot counters, the `misc` state, and "nothing was
written". Then the operator **stops and waits**. The gates that need the owner's word are G3, G4, G5 and G6 (G3, G4 and G6
write, and G5 is the first plain power-on); the owner's word for G3 is the message "go G3", and likewise "go G4",
"go G5" and "go G6" for the others. Anything else, including silence, is a no.

**Rollback.** None: nothing was written.

## G3. Write the OPOS kernel image to the inactive slot [OWNER SAYS GO]

**Precondition.** The owner has said "go G3" in chat after reading the G2 summary. G1 and G2 passed; the backups exist
in two places. Phone in TWRP (the same RAM-booted session, or a fresh one), on a cable, battery 20 percent or more. The
slot named here is **b** (inactive, never started by this project); the active slot is not changed by this gate. The
owner is told that this relaxes his standing rule "never flash boot, vbmeta, dtbo or recovery" for **one** partition,
`boot_b`.

**Commands (PC, PowerShell).** Five blocks, in this order, in one PowerShell window (`$serial`, `$images`, `$repo` and
`$probe` come from the setup above, `$run` from G1). Blocks 1 and 2 define helpers and build a file on the PC; they touch
no phone. Blocks 3 and 4 touch the phone. Each of them is one statement (`& { ... }`), so a failed check throws and ends
the whole block before the next line runs, even when the block is pasted into a console. A `STOP:` message means that
nothing after that line ran: read it and do not retry before you know why.

Block 1 defines the helpers. It holds the **only `dd` in this runbook** (the line marked WRITE, inside
`Write-BootPartition`). The function takes only `boot_a` or `boot_b`, and it throws before that line unless every check
listed after block 3 has passed. Paste it again in any new PowerShell window: G4 to G6 use it too.

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

Block 2 builds the exact bytes to be written: the pinned image padded with zeros to the size of the partition, so that
the partition holds a file whose hash is known and no stale AVB footer remains.

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

Block 3 is the write. It needs the phone in TWRP.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $sums = Read-Sums "$run\SHA256SUMS"
    Write-BootPartition -Name boot_b -Image $pad -ImageHash $padHash -CurrentHash $sums['boot_b.img']
}
```

`Write-BootPartition` throws before the `dd`, and so before anything is written, unless all of these hold: adb says
`recovery`; the image file on the PC is exactly 100663296 bytes and hashes to the recorded hash; the by-name link of the
partition resolves to its node from the G1 inventory (`/dev/block/sde26` for `boot_b`, `/dev/block/sde9` for `boot_a`);
`blockdev --getsize64` of that node is 100663296 and `--getro` is 0; no mount names the node, the whole `sde` LUN or the
by-name path; the SHA-256 of the node now equals its backup hash in the G1 `SHA256SUMS` (a restore skips this one and
prints what the node holds); and the image pushed to the phone's `/tmp` hashes, on the phone, to the recorded hash. After
the `dd` it runs `sync`, drops the caches and requires the node to hash to the image; a mismatch throws, and the
Rollback below is then the next step. TWRP's `dd` is BSD-style: only `if=`, `of=`, `bs=` and plain decimal numbers are
used; `conv=` is refused by it (measured on October 9) and there is no `status=`.

Block 4 is the cold re-read: the phone goes back to the bootloader menu, a fresh TWRP is started (fresh RAM, no cache),
and both slots are read again. Then the bootloader phase of the probe runs again (reads only).

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $sums = Read-Sums "$run\SHA256SUMS"
    Invoke-Adb reboot bootloader | Out-Null
    Wait-Fastboot
    Invoke-Fastboot boot "$images\surfaceduo1-twrp.img" | Out-Null
    Wait-Recovery
    Assert-Equal 'cold SHA-256 of boot_b' (Get-PhoneSha /dev/block/sde26) $padHash
    Assert-Equal 'cold SHA-256 of boot_a (untouched)' (Get-PhoneSha /dev/block/sde9) $sums['boot_a.img']
    Invoke-Adb reboot bootloader | Out-Null
    Wait-Fastboot
}
```

```powershell
powershell -ExecutionPolicy Bypass -File "$repo\deploy\duo-persist-probe.ps1" -Phase bootloader -Serial $serial -OutDir $probe
```

Nothing else is written: not `boot_a`, not vbmeta, not dtbo, not `misc`, no slot change. The last command is the read-only
bootloader phase again; its slot counters must equal the first run's.

**Expected.** `dd` ends with `24576+0 records in` and `24576+0 records out` (some `dd` builds print a different summary or
none; the hashes are what count). The hash after the write equals `$padHash`, the cold re-read equals it too, `boot_a` is
unchanged, and the slot counters are as before.

**What it proves.** That a root-side write to the boot LUN is accepted and reads back exact. It does **not** show that the
OPOS kernel starts from that slot, and it does not show why fastboot refused on October 31.

**Decision table.**

| Outcome | Action |
|---|---|
| Any check before the write fails (a `STOP:` line naming the check) | Stop. Nothing was written: the block threw before the `dd`. |
| The push to `/tmp` fails for lack of room | Stop and ask. A fallback exists (push the unpadded image and write only its 8294 blocks) but it leaves the old tail, including a stale AVB footer; that is a different write and needs the owner's word. |
| `dd` reports an error (I/O error, read-only, not permitted) | Stop. Read `boot_b` back; if its hash is not the backup's, restore it (Rollback). The OPOS image is not in a slot: go to "What if the probes say no". |
| The hash after the write differs from `$padHash` | Restore from the backup at once, verify the restore, stop. |
| Hash equal, cold re-read equal | Success. `boot_b` holds the OPOS kernel. Report to the owner and **stop**. G4 is a separate word. |
| The cold re-read differs from the warm one | The write did not reach the flash. Restore; stop. |
| `boot_a` changed | Impossible with these commands; if it happens, restore it from its backup (the G4 Rollback block for `boot_a`) and stop everything. |

**Rollback (restores `boot_b` to its G1 content).** One block. It RAM-boots TWRP unless TWRP is already running, waits
until `adb devices` lists the phone as `recovery`, and then runs the same checks as the write, with the backup as the
image: the file is pushed, hashed on the phone against the G1 `SHA256SUMS`, written with `dd`, synced, the caches are
dropped, and the partition is read back and must hash to the backup.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $sums = Read-Sums "$run\SHA256SUMS"
    if (-not (Test-Recovery)) { Invoke-Fastboot boot "$images\surfaceduo1-twrp.img" | Out-Null }
    Wait-Recovery
    Write-BootPartition -Name boot_b -Image "$run\boot_b.img" -ImageHash $sums['boot_b.img'] -Restore
}
```

`boot_b` is the inactive slot, so a bad `boot_b` does not change how the phone starts. **INFERRED, not measured:**
`fastboot boot` takes its image from the PC and the bootloader menu does not read the boot slots, so TWRP should stay
reachable whatever a slot holds (row 19). The port's own notes report the opposite for a slot that rejects RAM-boots
after a crashed kernel or a poisoned `misc`; if the first line of the Rollback fails with `Device Error`, use the cure
order under "What if the probes say no".

## G4. Make the written slot the next start [OWNER SAYS GO]

**Precondition.** G3 succeeded. The owner has said "go G4" and has chosen a variant after reading this section. OPOS
rootfs is on userdata (the install chain ran, Prerequisite 2). The phone is in the bootloader menu. Battery 20 percent or
more. The helper block of G3 has been pasted in this PowerShell window, and `$pad`, `$padHash` and `$run` are still set
(variant B needs them; if the window is new, repeat blocks 1 and 2 of G3 and set `$run` as in G1).

There are two ways, and they are not equally safe. **None is the right answer if either check fails.**

| | **Variant A: `fastboot set_active b`** | **Variant B: no slot change; also write `boot_a`** |
|---|---|---|
| What it does | Makes slot b, which G3 wrote, the slot the bootloader starts. Slot a (stock) stays as the fallback. | Writes the same padded image to `boot_a`, the active slot, from TWRP with the same procedure as G3. Slot b already holds it. |
| Mechanism | The bootloader's own `set_active` in fastboot. `bootctl` is not in the pinned TWRP, so nothing here uses it. | `dd` in TWRP through `Write-BootPartition`, then the same hash checks. |
| What else changes | The slot attributes **and, per the strings of the phone's own bootloader, the UFS boot LUN, so the phone would start slot b's xbl, tz, hyp, modem and dsp: an older build that this project has never started** (REPORTED, not measured). If that older chain does not come up, there is no rescue. | Nothing outside `boot_a`. The firmware chain stays the one that has been starting all along. |
| Fallback after failed starts | The bootloader gives up slot b after its retries and starts slot a: **stock Android on the OPOS userdata, the wipe path.** OPOS never marks a start successful (the port's slot guard is masked in the current image), so the retries are a countdown. | Slot a is already marked successful, which per the A/B contract should mean no countdown (inferred, not measured). The other slot also holds OPOS, so a fallback lands in OPOS, never in Android. |
| Cost | The brick risk above, and the countdown. | The stock kernel and its recovery are gone from slot a (kept in the backup); "Recovery mode" in the menu starts OPOS. |
| Undo | `fastboot set_active a`, if the bootloader menu can still be reached. | `Write-BootPartition -Restore` of the backed-up `boot_a`, verified. |

My reading, as the planner and not a measurement: **variant B is the safer one**, because it changes no firmware chain and
has no countdown. Variant A is what the original plan described, and it is only worth its risk if the owner specifically
wants stock Android to remain the automatic fallback. The owner chooses; the operator does not.

**Commands, variant A (PC, PowerShell, bootloader menu).**

```powershell
& {
    $ErrorActionPreference = 'Stop'
    if ((Invoke-Fastboot getvar slot-unbootable:b) -notmatch 'slot-unbootable:b:\s*no\b') { throw 'STOP: slot b is not marked bootable' }
    if ((Invoke-Fastboot getvar slot-retry-count:b) -notmatch 'slot-retry-count:b:\s*[1-9]') { throw 'STOP: slot b has no retries left' }
    Invoke-Fastboot set_active b | Out-Null                 # WRITE: slot attributes and, per the bootloader's strings, the boot LUN
    if ((Invoke-Fastboot getvar current-slot) -notmatch 'current-slot:\s*b\b') { throw 'STOP: the bootloader did not switch to slot b; run the Rollback' }
}
```

**Commands, variant B (PC, PowerShell).** The same checks as G3 with `boot_a` for `boot_b`: the by-name link of `boot_a`
must resolve to `/dev/block/sde9`, the size must be 100663296, `--getro` 0, and the hash before the write must equal the
G1 backup of `boot_a`. They are the checks of `Write-BootPartition`, which throws before the `dd` if one fails. Block 1:
the write; the phone must be in TWRP (the first line RAM-boots it from the bootloader menu).

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $sums = Read-Sums "$run\SHA256SUMS"
    if (-not (Test-Recovery)) { Invoke-Fastboot boot "$images\surfaceduo1-twrp.img" | Out-Null }
    Wait-Recovery
    Write-BootPartition -Name boot_a -Image $pad -ImageHash $padHash -CurrentHash $sums['boot_a.img']
}
```

Block 2: the cold re-read in a fresh TWRP; both slots must now hash to `$padHash`.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Invoke-Adb reboot bootloader | Out-Null
    Wait-Fastboot
    Invoke-Fastboot boot "$images\surfaceduo1-twrp.img" | Out-Null
    Wait-Recovery
    Assert-Equal 'cold SHA-256 of boot_a' (Get-PhoneSha /dev/block/sde9) $padHash
    Assert-Equal 'cold SHA-256 of boot_b' (Get-PhoneSha /dev/block/sde26) $padHash
    Invoke-Adb reboot bootloader | Out-Null
    Wait-Fastboot
}
```

**Expected.** Variant A: `current-slot: b` and nothing else changed (`getvar all` again; compare with G0 and keep both).
Variant B: both `boot_a` and `boot_b` hash to `$padHash`, cold.

**What it proves.** That the bootloader accepted the slot change (A), or that the root-side write to the active slot is
exact (B). Not that OPOS starts.

**Decision table.**

| Outcome | Action |
|---|---|
| A: a `STOP:` line before `set_active` | Nothing changed. Stop; offer variant B. |
| A: `set_active` prints FAILED, or `current-slot` does not become b | Nothing changed (check `getvar all`). Stop; offer variant B. |
| A: after `set_active b` the bootloader menu cannot be reached again | There is no remedy in this runbook. This is the risk the table names. |
| A: `current-slot` is b, counters as expected | Go to G5. |
| B: a `STOP:` line before the write | Nothing was written. Stop. |
| B: any hash differs after the write | Restore `boot_a` from its backup (the Rollback for variant B), stop. |
| B: both slots equal `$padHash` | Go to G5. |

**Rollback, variant A** (phone in the bootloader menu): back to the slot that was active.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Invoke-Fastboot set_active a | Out-Null
    if ((Invoke-Fastboot getvar current-slot) -notmatch 'current-slot:\s*a\b') { throw 'STOP: current-slot is not a' }
}
```

**Rollback, variant B** (from the bootloader menu or from TWRP): `boot_a` goes back to its G1 content, the stock image or
the Magisk-patched one, whichever the backup holds.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $sums = Read-Sums "$run\SHA256SUMS"
    if (-not (Test-Recovery)) { Invoke-Fastboot boot "$images\surfaceduo1-twrp.img" | Out-Null }
    Wait-Recovery
    Write-BootPartition -Name boot_a -Image "$run\boot_a.img" -ImageHash $sums['boot_a.img'] -Restore
}
```

## G5. First unattended start with the brake still armed [OWNER SAYS GO]

**Precondition.** G4 done. **The owner has said "go G5"**, which also covers the rollback writes listed under this gate
(they only put the phone back to its state before G5). It was said after being told that this is the first step that can start
stock Android on the OPOS userdata (its wipe path) or, in variant A, begin the retry countdown, and that either can cost
the OPOS rootfs and a reinstall with the install chain. Userdata holds the OPOS rootfs. The misc brake is armed: the G1
report says `misc` equals `misc-brake.img`. If it does not, arming it is the install chain's two commands
(`fastboot erase misc`, `fastboot flash misc "$images\misc-brake.img"`) and needs the owner's word, because it is a write.
A PC with the USB network adapter is attached so the phone can be found; the battery is 20 percent or more.

**How the brake and the new slot interact (what is known).** The brake asks the bootloader to stop in the bootloader menu
on the next start. The bootloader has no literal `bootonce-bootloader` string, and on October 31 a reboot with it armed
reached stock recovery instead; only RAM-boots have been measured with it armed. So three things can happen and G5 is the
test that tells which:

1. **The menu appears and stays.** The brake was honoured. Nothing is wrong with the kernel image. Do not pick any menu
   entry; go to G6's first step to learn whether the brake is one-shot.
2. **OPOS starts anyway.** The bootloader ignored the brake on a normal start. The brake is then no safety here and the
   owner should know it.
3. **Something else** (stock recovery with "Can't load Android system", a black screen, a restart loop): see the table.

**Commands.** Phone in the bootloader menu.

1. Power off: hold Power until the screen is dark (twenty to thirty seconds). The project's recorded trap is that a
   charger or cable plugged into an OFF phone auto-boots stock Android: so unplug the cable first, power off, and plug the
   cable back in at the moment you choose for step 2. If the phone starts by itself with the cable attached, that counts as
   step 2: note the time and carry on from there (the armed brake is the safety for this case).
2. Press Power once. **Press nothing else.** Note the time. Watch the screens. (The Button steps say never to press Power
   alone at the orange warning. That rule protects the RAM-boot flow, where a normal start means stock Android. Here the
   normal start is the thing under test, which is why the brake is armed.)
3. On the PC, wait for the Linux USB gadget (device id `VID_1D6B&PID_0104`, the same one the install chain waits for) and
   for TCP port 22 of the phone to answer; `deploy\duo-opos-boot.ps1` documents the discovery, and
   `pwsh -File deploy\duo-acceptance.ps1 -Host <phone address> -Key <private key file>` runs the read-only acceptance
   checks once it answers.

**Expected.** One of the three cases above, within the timeout. Only the first two leave the phone in a state that needs no
rollback.

**Success criteria.** The owner sees OPOS on the screens, **and** the PC finds the phone on the USB network.
**Timeout.** Wait up to five minutes from the Power press. This is a limit chosen for safety, not a measured start time;
the persistent path has never been timed. When it passes without success: hold Power until the screen is dark
(twenty to thirty seconds), then Volume-down plus Power to the bootloader menu, and use the rollback.

**After a success.** From OPOS, run `reboot bootloader` (never `reboot`), then the bootloader phase of the probe script
again. Compare `slot-retry-count`, `slot-successful` and `slot-unbootable` with G0: this shows whether an OPOS start
consumes a retry, which decides whether the slot guard must be made to work before anything is left unattended.

**What it proves.** That the OPOS kernel, started by the bootloader itself from the phone's storage, reaches a working
OPOS; and what the brake does on a plain start. It does not prove that the next start works, that the brake is not
needed (G6), or anything about stability.

**Decision table.**

| What the owner sees | Meaning | Action |
|---|---|---|
| OPOS, and the PC finds the phone | Success | Read the counters; report; wait for the owner's word on G6. |
| The bootloader menu, no OPOS | Brake honoured (case 1) | Do not choose Start. Report. Power-cycle once more only if the owner says so. |
| The device keeps returning to the bootloader menu although the brake is not the cause | The community page's Troubleshooting advice (REPORTED, undated) is to run `fastboot set_active` to the other slot and then back to the original slot | Not part of this runbook: it is two slot writes, so it needs the owner's separate word. In variant B no slot was ever changed. |
| Stock recovery, "Can't load Android system" | A start reached Android's recovery | **Do not pick Factory data reset** while OPOS is wanted. Hold Power until dark, enter the bootloader menu, roll back. Read `misc` from TWRP before anything else. |
| Stock Android setup screens | Slot a was started on the OPOS userdata | The wipe path ran or is running. Power off; roll back; expect to reinstall the rootfs with the install chain. |
| Black screens, process lights, nothing on the PC | The kernel started and OPOS did not, or the kernel did not start | Hold Power until dark; bootloader menu; roll back. Do not retry. |
| The restart loop | A failing start | Hold Power until dark; bootloader menu; roll back. |

**Rollback.** Hold Power until the screen is dark, then Volume-down plus Power to the bootloader menu (from off). The
blocks below need the helper block of G3 pasted in the PowerShell window. Then run **one** of the next two blocks, the one
for the variant that was used, and then the third block, which re-arms the brake.

Variant A (phone in the bootloader menu): back to slot a.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Invoke-Fastboot set_active a | Out-Null
    if ((Invoke-Fastboot getvar current-slot) -notmatch 'current-slot:\s*a\b') { throw 'STOP: current-slot is not a' }
}
```

Variant B (RAM-boots TWRP, restores `boot_a` from the G1 backup, verifies):

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $sums = Read-Sums "$run\SHA256SUMS"
    if (-not (Test-Recovery)) { Invoke-Fastboot boot "$images\surfaceduo1-twrp.img" | Out-Null }
    Wait-Recovery
    Write-BootPartition -Name boot_a -Image "$run\boot_a.img" -ImageHash $sums['boot_a.img'] -Restore
}
```

Both variants, then: re-arm the brake and carry on with the RAM-boot flow, which is unchanged.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    if (Test-Recovery) { Invoke-Adb reboot bootloader | Out-Null }
    Wait-Fastboot
    Invoke-Fastboot erase misc | Out-Null
    Invoke-Fastboot flash misc "$images\misc-brake.img" | Out-Null
}
```

## G6. Disarm the brake and test a plain power cycle [OWNER SAYS GO]

**Precondition.** G5 succeeded. The owner has said "go G6". The counters after G5 are known. The helper block of G3 is
pasted in the PowerShell window.

**Commands.**

```powershell
& {
    $ErrorActionPreference = 'Stop'
    # Phone in the bootloader menu (from OPOS: reboot bootloader)
    Invoke-Fastboot erase misc | Out-Null                # WRITE: the first step of the install chain, leaves misc all zero
}
```

Then: hold Power until dark, press Power once, watch, same success criteria and timeout as G5. A software `reboot` from
OPOS is a separate step: it stays forbidden until the owner has seen the plain power cycle work and says so.

**Expected.** OPOS starts with the brake gone. `misc` all zero is a clean request block.

**What it proves.** That the power button alone suffices and that the brake is not needed for this start. Not stability
over hours, battery life, or what happens after an Android update.

**Decision table.**

| Outcome | Action |
|---|---|
| OPOS starts | Success. Record the date, the variant, the counters. |
| Anything else in the table of G5 | Same actions as there; then re-arm the brake. |
| OPOS starts only the first time | Read the counters; the retry countdown is probably running (variant A). Do not leave the phone unattended; ask the owner. |

**Rollback.** Bootloader menu (Volume-down plus Power from off), then re-arm the brake with the last block of the G5
Rollback, repeated here:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    if (Test-Recovery) { Invoke-Adb reboot bootloader | Out-Null }
    Wait-Fastboot
    Invoke-Fastboot erase misc | Out-Null
    Invoke-Fastboot flash misc "$images\misc-brake.img" | Out-Null
}
```

## G7. What an over-the-air kernel update could then mean (a separate, later design)

Not part of this runbook; nothing here is built. [docs/ota-update.md](ota-update.md) says that today the kernel cannot be
updated over the air because the boot partitions were thought unwritable, and its `scope` field has the value `boot` for
changes applied at the next start. If G3 to G6 pass, a kernel update would be a different kind of bundle: a signed image
that the root updater writes to the inactive slot with `dd`, verifies by hash, and then switches to. That brings exactly
the questions above into the updater: which slot to write, how to switch (G4's two variants), who marks a start good (the
slot guard, which is masked today), and what the rollback is. It must be designed, reviewed and approved separately; until
then OTA stays app-level only.

## What if the probes say no

Keep the RAM-boot. Nothing changes for the owner: the install chain starts OPOS from the PC as it does today.

| Probe result | What to do |
|---|---|
| Layout differs, a mount, or a failed backup | Stop; fix the cause; nothing was written. |
| A read-only flag is 1 | The boot LUN is write-protected on the kernel side. Document it in [packaging/duo-os/README.md](../packaging/duo-os/README.md) with the report attached; keep the RAM-boot. |
| G3's `dd` is refused although the flags are 0 | Both fastboot (October 31) and TWRP refuse: something lower, UFS or the bootloader, protects the boot LUN. Document it. |
| Slot b cannot be used | Variant B, or stop. |
| Everything passes but G5 or G6 fails | Roll back; document which case of G5 happened; keep the RAM-boot. |
| `fastboot boot` of TWRP is rejected with `Device Error` (row 19 was only inferred) | Do not retry the same slot's RAM-boot in a loop. The port's cure order, which needs the owner's word because the last steps write: Volume-down plus Power to the bootloader menu; RAM-boot TWRP once; if it starts, read `misc` and, only if it holds a request, zero its first 2048 bytes from TWRP; then try the other slot (a `set_active`). Never retry a crashed kernel from the last good slot. If TWRP will not start at all, there is no rollback in this runbook: stop. |

Other explanations of October 31 (row 4b) that the probes help to separate: a poisoned `misc` (G1 reads it) and an error
from the storage layer for the boot LUN (G3's `dd` from TWRP shows whether the kernel side can write). The leading
*untested* alternative is mode and naming (row 10): the community writes the
partition from fastbootd with `fastboot flash boot`. Testing it means starting stock recovery on this phone's userdata and
sending `fastboot reboot-fastboot` (a bootloader command that writes a request to `misc`); both have side effects nobody
has measured here. It is a separate experiment with its own gate and the owner's word; it starts with read-only
`getvar is-userspace`, `has-slot:boot` and `partition-size:boot` answers in fastbootd. It is not part of this session.

## Risks

- **The bootloader chain.** No step here writes it. The ways it could still be affected are the ones the tables name:
  `set_active` (variant A changes the boot LUN), a `dd` aimed at the wrong node (the only `dd` is inside
  `Write-BootPartition`, which takes `boot_a` or `boot_b` only and throws before it unless `readlink`, size, read-only,
  mount and hash checks pass, and the nodes of the probe are in the allow-list of the script), and a power loss in the
  middle of a write (battery 20 percent or more and a cable).
- **Android's wipe path.** A start of stock Android on the OPOS userdata wipes it. The rootfs is rebuildable (a copy is kept
  on the PC), and Android's own data is already gone, but a wipe still costs a reinstall.
- **The brake is unproven for plain starts** (row 17) and **the retry countdown** (variant A) is real if OPOS never marks a
  start good.
- **Relocking.** The OPOS image is unsigned and starts only because the bootloader is unlocked. Never relock, never
  `flashing lock`. The community page says the same: the bootloader cannot be relocked while a boot image modified by its
  guide is installed (row 10).
- **Microsoft's restore** needs stock recovery, which lives in the boot image of a slot. After G4 the slot that was
  written no longer has it until the backup is put back from TWRP.
- **After a normal (not RAM) start** the bootloader may pass the kernel extra command-line items or a different reason for
  starting (for example after a charger insert); the Halium initramfs was only seen with a RAM-boot. UNKNOWN.
- **Tool traps:** `fastboot` exits 0 on `FAILED`; after a multi-chunk sparse flash every fastboot command can time out
  (hold Power twenty to thirty seconds; the probe script kills a hung command after `-TimeoutSec` seconds and stops); Windows `adb shell` turns line feeds into CRLF (use `exec-out`); USB addresses
  differ per image.
- **This runbook and the script were never run against hardware.** The script's logic is tested with a stand-in adb and
  fastboot; real TWRP output may differ in shape (for example `readlink` or `blockdev` flags, `ls -l` layout, a missing
  `sgdisk`), in which case the script stops with a reason rather than guessing. That is the intended failure direction.
