<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Persistent kernel on the Duo: the gated first device session

Status, November 7, 2026: **a plan; none of it has been run on a phone.** The probe script (`deploy/duo-persist-probe.ps1`)
and this text were tested against fake tools and file checks only. Nothing here authorizes a write to the phone: gates
G3, G4 and G6 each need the owner's explicit word in chat, after the read-only gates have been shown to him.

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

1. USB data must work. The owner's Duo currently charges over USB-C but carries no data (see
   [packaging/duo-os/README.md](../packaging/duo-os/README.md)); every command below needs fastboot and adb over USB.
2. G4 and later need the OPOS rootfs on userdata. That is the install chain's job, it erases Android's data, and it is
   a separate decision of the owner. G0 to G3 do not touch userdata and do not need the rootfs.
3. The pinned images in one folder (`boot.img`, `surfaceduo1-twrp.img`, `misc-brake.img`), battery 20 percent or more,
   the phone on a good cable.

## What is known, and what the experiment settles

Grades: **MEASURED** (a command output or a hash, with where it is), **REPORTED** (a document or a person, dated),
**UNKNOWN**. "Re-checked" means compared against the files while this runbook was written.

| # | Claim | Grade | Source |
|---|---|---|---|
| 1 | The bootloader (version 2022.815.527) refused `fastboot flash boot_a` with `remote: Device Error` although unlocked; `fastboot boot` worked. | REPORTED, re-checked | One line, item 34 of the October 31, 2026 install-friction log kept on the owner's PC. Every later document repeats it. |
| 2 | Nothing else about that attempt was kept: not the exact output, not the image, not raw or sparse, not which fastboot mode, not what `misc` held. | UNKNOWN | No raw tool output was found in the agent transcripts that were searched. |
| 3 | `vbmeta` is refused the same way. | UNKNOWN | [packaging/duo-os/README.md](../packaging/duo-os/README.md) says "reported"; no first-hand record exists. |
| 4 | The same bootloader's fastboot does write other partitions: `erase misc`, `flash misc`, `format:ext4 userdata` and the boot of two images all answered OKAY. So the refusal is specific to the boot slots, not a general write block. | MEASURED, re-checked | The boot-chain log of the November 7 run (`boot-chain.log` beside the images). |
| 5 | On October 31 `boot_a` held a Magisk-patched image (the hash equals the patched file prepared that day); the October 9 content was reported to be stock. So something wrote it. The reported route is Magisk "Direct Install" from a RAM-booted rooted session, a write made on the phone, not by fastboot. | October 31 hash MEASURED, re-checked; stock on October 9 and the route REPORTED | `SHA256SUMS` of the 2026-10-31 backup; friction log item 34; the planning research. |
| 6 | `boot_b` is a complete stock image of an older build than slot a; slot b has never been started by this project. | hash match REPORTED by the planning research (not re-checked), starting it UNKNOWN | Backup hashes; AVB parse of `vbmeta_b`. |
| 7 | Bootloader state: unlocked, critical partitions not unlocked, slots a and b both retry count 7, not unbootable, successful; current slot a. | MEASURED, re-checked | The October 9 getvar records; the November 7 install preflight read slot a and unlocked again. |
| 8 | This bootloader has no `flashing unlock_critical`; changing the lock state makes it wipe data. Never run `flashing lock` or `flashing unlock`. | REPORTED (strings of the phone's own bootloader image), not re-checked | Planning research; the image is in the October 31 backup, so anyone can re-check. |
| 9 | The text `Device Error` also appears for unrelated causes (a slot that rejects RAM-boots after a crashed kernel, a poisoned `misc`). | REPORTED | The port's own README (undated). |
| 10 | The community (WOA-Project) writes the Duo 1 boot partition for good, from **fastbootd** (userspace fastboot, reached through stock recovery > "Enter fastboot") with the plain name `fastboot flash boot`, and uses the bootloader's fastboot only for `fastboot boot`. Never tried here. | REPORTED | https://github.com/WOA-Project/SurfaceDuo-Guides/blob/main/Install/DualBoot.md (undated page, read November 7, 2026). It names firmware 2022.902.48 for the AT&T unit; this unit reports bootloader 2022.815.527 and whether they are the same build is UNKNOWN. |
| 11 | The port's own rule is to write the kernel to the slots only after it has started Linux on that phone, which the November 7 RAM-boot did. So the port does not forbid slot writes in general. The standing rule "never flash boot, vbmeta, dtbo or recovery" is the owner's, and G3 asks to relax it for one partition. | REPORTED, re-checked | The header of the port's `release-add-boot.sh`. |
| 12 | The OPOS kernel image fits: 33,972,224 bytes in a 100,663,296-byte partition, header version 2 like the stock images, no AVB footer, unsigned. | MEASURED | SHA-256 `abec1fe9ce4cbb7a9296c3abd4fd7b4acc1283842e86f2bfe0879a7e84fae552`, re-checked on this PC. |
| 13 | The bootloader continued to start a Magisk-patched `boot_a` whose AVB hash does not match its `vbmeta`, in the unlocked ("orange") state. So no vbmeta change is needed while the phone stays unlocked; **never relock**. | REPORTED (parse of the backup), not re-checked | Planning research. |
| 14 | Stock Android on a userdata it does not recognize writes a wipe request to `misc` and reboots to recovery, which offers a factory reset. The exact sequence on this phone was not re-measured. | REPORTED | AOSP recovery source; the October 31 factory reset; the port's "BCB poison" note. |
| 15 | There is no rescue if the bootloader chain is damaged: no EDL programmer exists publicly. | REPORTED | https://learn.microsoft.com/en-us/answers/questions/5842635/how-to-fix-hard-brick-surface-duo (2026-03-29). The project rule follows from it. |
| 16 | Current content of `boot_a`, `boot_b` and `misc`, the read-only flags of the boot LUN, and whether a write from TWRP is accepted. | UNKNOWN | G1 to G3 find out. |
| 17 | What the bootloader does with the misc brake on a plain power-on. It has no literal `bootonce-bootloader` string; on October 31 a reboot with the brake armed reached stock recovery, not fastboot. | UNKNOWN (the November 7 chain worked with the brake armed, but only for RAM-boots) | Friction log item 33; planning research. |

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

Phone side (what step 3 runs in the TWRP shell through adb, all reads): `getprop`, `cat` of `/proc/cmdline`, `/proc/mounts`,
`/proc/partitions` and `/sys/class/block/<node>/ro`, `ls -l` of the by-name directory, `readlink -f`,
`blockdev --getsize64` and `--getro`, `sha256sum` of each listed partition, `sgdisk -p` and `-i`, `bootctl` queries,
`dmesg`, and `dd if=<block> bs=4096 count=<n> 2>/dev/null` piped to the PC with `adb exec-out`. It names no output
file, so it writes to stdout. The full list is the allow-list at the top of the script.

**Expected.** One `ok` line per step, `Done. Nothing was written to the phone.`, and in the folder: `boot_a.img`,
`boot_b.img`, `vbmeta_a.img`, `vbmeta_b.img`, `dtbo_a.img`, `dtbo_b.img`, `misc.img`, `sde-table-head.bin`, `SHA256SUMS`,
`report.md`, `probe.json`, `raw\`. Sizes: 100,663,296 bytes for `boot_a` and `boot_b`, 65,536 for the vbmeta images,
16,777,216 for the dtbo images, 1,048,576 for `misc` and for the table head. Step 4 prints `ok` for every file.
Each partition was hashed on the phone, copied, hashed on the PC and read a second time; all three hashes agree.

**What it proves.** The by-name links resolve to the nodes in the October 31 partition table (`boot_a` is `sde9`, `boot_b`
`sde26`, `vbmeta_a` `sde14`, `vbmeta_b` `sde31`, `dtbo_a` `sde15`, `dtbo_b` `sde32`, `misc` `sda5`), none of them is mounted,
and we hold verified copies of everything G3 or G4 could change, plus the partition table head that carries the slot
attributes. It proves nothing about whether a write would be accepted.

**Decision table.**

| Outcome | Action |
|---|---|
| Any `STOPPED`, any hash that differs between phone, PC file and second read | **Stop.** Nothing was written. Suspect the cable, the port or the adb version; change one thing and run into a new folder. Do not go on with a backup that failed once. |
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
slot and boot-control answers, and the candidate image check. Probes marked "unavailable" (for example `bootctl` or
`sgdisk` missing from this TWRP) are not failures. The bootloader report holds the full `getvar all` in `raw\b99.txt`
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
| `bootctl` unavailable | This TWRP has no boot-control tool | Fine. G4 variant A uses fastboot, not `bootctl`. |
| Candidate image: `Fits boot_b` false, magic not `ANDROID!` | Wrong file | Stop. |
| `misc` content "other" | A request may be pending in the BCB | Ask the owner. |

**The summary for the owner.** The operator pastes into chat: the "Summary for the owner" block of both reports, plus a
line each for: what `boot_a` and `boot_b` hold, the read-only flags, the slot counters, the `misc` state, and "nothing was
written". Then the operator **stops and waits**. The gates that write are G3, G4 and G6; the owner's word for G3 is the
message "go G3". Anything else, including silence, is a no.

**Rollback.** None: nothing was written.

## G3. Write the OPOS kernel image to the inactive slot [OWNER SAYS GO]

**Precondition.** The owner has said "go G3" in chat after reading the G2 summary. G1 and G2 passed; the backups exist
in two places. Phone in TWRP (the same RAM-booted session, or a fresh one), on a cable, battery 20 percent or more. The
slot named here is **b** (inactive, never started by this project); the active slot is not changed by this gate. The
owner is told that this relaxes his standing rule "never flash boot, vbmeta, dtbo or recovery" for **one** partition,
`boot_b`.

**Commands (PC, PowerShell).** First build the exact bytes to be written: the pinned image padded with zeros to the size of
the partition, so that the partition holds a file whose hash is known and no stale AVB footer remains.

```powershell
$work = '<a folder on the PC>'
$pad  = "$work\opos-boot-padded.img"
Copy-Item "$images\boot.img" $pad
$fs = [System.IO.File]::OpenWrite($pad); $fs.SetLength(100663296); $fs.Dispose()
(Get-Item $pad).Length                                   # 100663296
$padHash = (Get-FileHash -Algorithm SHA256 $pad).Hash.ToLower(); $padHash       # write this down
# The first 33,972,224 bytes must still be the pinned image:
$n = (Get-Item "$images\boot.img").Length; $b = New-Object byte[] $n
$r = [System.IO.File]::OpenRead($pad); [void]$r.Read($b, 0, $n); $r.Dispose()
([BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($b)) -replace '-', '').ToLower()   # abec1fe9ce4c...
```

Then the checks and the write. Every `must` is a stop condition. The write is the one line marked WRITE.

```powershell
adb -s $serial push $pad /tmp/opos-boot-padded.img                    # RAM only; if the push fails for room, see the table
adb -s $serial shell sha256sum /tmp/opos-boot-padded.img              # must equal $padHash
adb -s $serial shell readlink -f /dev/block/platform/soc/1d84000.ufshc/by-name/boot_b   # must be /dev/block/sde26
adb -s $serial shell blockdev --getsize64 /dev/block/sde26            # must be 100663296
adb -s $serial shell blockdev --getro /dev/block/sde26                # must be 0
adb -s $serial shell sha256sum /dev/block/sde26                       # must equal boot_b.img in the G1 SHA256SUMS
adb -s $serial shell dd if=/tmp/opos-boot-padded.img of=/dev/block/sde26 bs=4096        # WRITE: boot_b only
adb -s $serial shell sync
adb -s $serial shell "echo 3 > /proc/sys/vm/drop_caches"              # cache control, not a storage write
adb -s $serial shell sha256sum /dev/block/sde26                       # must equal $padHash
adb -s $serial reboot bootloader
```

TWRP's `dd` rejects `conv=` options (measured on October 9); none is needed for a block device. After the phone is back in
the bootloader menu, the cold re-read (fresh RAM, no cache) is the real confirmation:

```powershell
fastboot -s $serial boot "$images\surfaceduo1-twrp.img"
adb -s $serial shell sha256sum /dev/block/sde26                       # must still equal $padHash
adb -s $serial shell sha256sum /dev/block/sde9                        # must still equal boot_a.img in the G1 SHA256SUMS: untouched
adb -s $serial reboot bootloader
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
| Any check before the write fails | Stop. Nothing was written. |
| The push to `/tmp` fails for lack of room | Stop and ask. A fallback exists (push the unpadded image and write only its 8294 blocks) but it leaves the old tail, including a stale AVB footer; that is a different write and needs the owner's word. |
| `dd` reports an error (I/O error, read-only, not permitted) | Stop. Read `boot_b` back; if its hash is not the backup's, restore it (Rollback). The OPOS image is not in a slot: go to "What if the probes say no". |
| The hash after the write differs from `$padHash` | Restore from the backup at once, verify the restore, stop. |
| Hash equal, cold re-read equal | Success. `boot_b` holds the OPOS kernel. Report to the owner and **stop**. G4 is a separate word. |
| The cold re-read differs from the warm one | The write did not reach the flash. Restore; stop. |
| `boot_a` changed | Impossible with these commands; if it happens, restore it from its backup and stop everything. |

**Rollback (restores `boot_b` to the October 31 or G1 content).**

```powershell
adb -s $serial push "$run\boot_b.img" /tmp/boot_b-backup.img
adb -s $serial shell sha256sum /tmp/boot_b-backup.img                 # must equal boot_b.img in the G1 SHA256SUMS
adb -s $serial shell dd if=/tmp/boot_b-backup.img of=/dev/block/sde26 bs=4096
adb -s $serial shell sync
adb -s $serial shell sha256sum /dev/block/sde26                       # must equal the backup hash
```

`boot_b` is the inactive slot, so a bad `boot_b` does not change how the phone starts. The bootloader and
`fastboot boot` of TWRP do not depend on the content of any boot slot, so TWRP stays reachable.

## G4. Make the written slot the next start [OWNER SAYS GO]

**Precondition.** G3 succeeded. The owner has said "go G4" and has chosen a variant after reading this section. OPOS
rootfs is on userdata (the install chain ran, Prerequisite 2). The phone is in the bootloader menu. Battery 20 percent or more.

There are two ways, and they are not equally safe. **None is the right answer if either check fails.**

| | **Variant A: `fastboot set_active b`** | **Variant B: no slot change; also write `boot_a`** |
|---|---|---|
| What it does | Makes slot b, which G3 wrote, the slot the bootloader starts. Slot a (stock) stays as the fallback. | Writes the same padded image to `boot_a`, the active slot, from TWRP with the same procedure as G3. Slot b already holds it. |
| Mechanism | The bootloader's own `set_active` in fastboot. `bootctl` in TWRP is used only to read in G2: it goes through TWRP's own boot-control build, which has never been exercised on this phone. | `dd` in TWRP, then the same hash checks. |
| What else changes | The slot attributes **and, per the strings of the phone's own bootloader, the UFS boot LUN, so the phone would start slot b's xbl, tz, hyp, modem and dsp: an older build that this project has never started** (REPORTED, not measured). If that older chain does not come up, there is no rescue. | Nothing outside `boot_a`. The firmware chain stays the one that has been starting all along. |
| Fallback after failed starts | The bootloader gives up slot b after its retries and starts slot a: **stock Android on the OPOS userdata, the wipe path.** OPOS never marks a start successful (the port's slot guard is masked in the current image), so the retries are a countdown. | Slot a is already marked successful, which per the A/B contract should mean no countdown (inferred, not measured). The other slot also holds OPOS, so a fallback lands in OPOS, never in Android. |
| Cost | The brick risk above, and the countdown. | The stock kernel and its recovery are gone from slot a (kept in the backup); "Recovery mode" in the menu starts OPOS. |
| Undo | `fastboot set_active a`, if the bootloader menu can still be reached. | TWRP `dd` of the backed-up `boot_a`, verified. |

My reading, as the planner and not a measurement: **variant B is the safer one**, because it changes no firmware chain and
has no countdown. Variant A is what the original plan described, and it is only worth its risk if the owner specifically
wants stock Android to remain the automatic fallback. The owner chooses; the operator does not.

**Commands, variant A (PC, PowerShell, bootloader menu).**

```powershell
fastboot -s $serial getvar slot-unbootable:b        # must say no
fastboot -s $serial getvar slot-retry-count:b       # must be above 0
fastboot -s $serial set_active b                    # WRITE (slot attributes and boot LUN)
fastboot -s $serial getvar current-slot             # must say b
```

**Commands, variant B (PC, PowerShell; phone in TWRP).** The same sequence as G3 with `boot_a` for `boot_b`: the by-name path
`.../by-name/boot_a` must read `/dev/block/sde9`, the size 100663296, `--getro` 0, the hash before the write equal to the
G1 backup of `boot_a`; the write is `dd if=/tmp/opos-boot-padded.img of=/dev/block/sde9 bs=4096`; then `sync`, the hash
equal to `$padHash`, `reboot bootloader`, a cold re-read in a fresh TWRP.

**Expected.** Variant A: `current-slot: b` and nothing else changed (`getvar all` again; compare with G0 and keep both).
Variant B: both `boot_a` and `boot_b` hash to `$padHash`, cold.

**What it proves.** That the bootloader accepted the slot change (A), or that the root-side write to the active slot is
exact (B). Not that OPOS starts.

**Decision table.**

| Outcome | Action |
|---|---|
| A: `set_active` prints FAILED, or `current-slot` does not become b | Nothing changed (check `getvar all`). Stop; offer variant B. |
| A: after `set_active b` the bootloader menu cannot be reached again | There is no remedy in this runbook. This is the risk the table names. |
| A: `current-slot` is b, counters as expected | Go to G5. |
| B: any hash differs | Restore `boot_a` from its backup (Rollback), stop. |
| B: both slots equal `$padHash` | Go to G5. |

**Rollback.** Variant A: `fastboot -s $serial set_active a`, then `getvar current-slot` must say a. Variant B: push
`$run\boot_a.img`, check its hash against the G1 `SHA256SUMS`, `dd ... of=/dev/block/sde9 bs=4096`, `sync`, check the
hash, as in the G3 rollback.

## G5. First unattended start with the brake still armed

**Precondition.** G4 done. Userdata holds the OPOS rootfs. The misc brake is armed: the G1 report says `misc` equals
`misc-brake.img`. If it does not, arming it is the install chain's two commands (`fastboot erase misc`,
`fastboot flash misc "$images\misc-brake.img"`) and needs the owner's word, because it is a write. A PC with the USB
network adapter is attached so the phone can be found; the battery is 20 percent or more.

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

1. Power off: hold Power until the screen is dark (twenty to thirty seconds). Leave the data cable attached.
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
| Stock recovery, "Can't load Android system" | A start reached Android's recovery | **Do not pick Factory data reset** while OPOS is wanted. Hold Power until dark, enter the bootloader menu, roll back. Read `misc` from TWRP before anything else. |
| Stock Android setup screens | Slot a was started on the OPOS userdata | The wipe path ran or is running. Power off; roll back; expect to reinstall the rootfs with the install chain. |
| Black screens, process lights, nothing on the PC | The kernel started and OPOS did not, or the kernel did not start | Hold Power until dark; bootloader menu; roll back. Do not retry. |
| The restart loop | A failing start | Hold Power until dark; bootloader menu; roll back. |

**Rollback.** Bootloader menu (Volume-down plus Power from off). Variant A: `fastboot set_active a`. Variant B: TWRP
restore of `boot_a`. Then re-arm the brake (two commands above) and carry on with the RAM-boot flow, which is unchanged.

## G6. Disarm the brake and test a plain power cycle [OWNER SAYS GO]

**Precondition.** G5 succeeded. The owner has said "go G6". The counters after G5 are known.

**Commands.**

```powershell
# Phone in the bootloader menu (from OPOS: reboot bootloader)
fastboot -s $serial erase misc                       # WRITE: the first step of the install chain, leaves misc all zero
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

**Rollback.** Bootloader menu, then re-arm the brake: `fastboot erase misc`, `fastboot flash misc "$images\misc-brake.img"`.

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

The leading *untested* alternative explanation of October 31 is mode and naming (row 10): the community writes the
partition from fastbootd with `fastboot flash boot`. Testing it means starting stock recovery on this phone's userdata and
sending `fastboot reboot-fastboot` (a bootloader command that writes a request to `misc`); both have side effects nobody
has measured here. It is a separate experiment with its own gate and the owner's word; it starts with read-only
`getvar is-userspace`, `has-slot:boot` and `partition-size:boot` answers in fastbootd. It is not part of this session.

## Risks

- **The bootloader chain.** No step here writes it. The ways it could still be affected are the ones the tables name:
  `set_active` (variant A changes the boot LUN), a `dd` aimed at the wrong node (every write is preceded by `readlink`,
  size and read-only checks, and the nodes are in the allow-list of the script), and a power loss in the middle of a write
  (battery 20 percent or more and a cable).
- **Android's wipe path.** A start of stock Android on the OPOS userdata wipes it. The rootfs is rebuildable (a copy is kept
  on the PC), and Android's own data is already gone, but a wipe still costs a reinstall.
- **The brake is unproven for plain starts** (row 17) and **the retry countdown** (variant A) is real if OPOS never marks a
  start good.
- **Relocking.** The OPOS image is unsigned and starts only because the bootloader is unlocked. Never relock, never
  `flashing lock`.
- **Microsoft's restore** needs stock recovery, which lives in the boot image of a slot. After G4 the slot that was
  written no longer has it until the backup is put back from TWRP.
- **After a normal (not RAM) start** the bootloader may pass the kernel extra command-line items or a different reason for
  starting (for example after a charger insert); the Halium initramfs was only seen with a RAM-boot. UNKNOWN.
- **Tool traps:** `fastboot` exits 0 on `FAILED`; after a multi-chunk sparse flash every fastboot command can time out
  (hold Power twenty to thirty seconds); Windows `adb shell` turns line feeds into CRLF (use `exec-out`); USB addresses
  differ per image.
- **This runbook and the script were never run against hardware.** The script's logic is tested with a stand-in adb and
  fastboot; real TWRP output may differ in shape (for example `readlink` or `blockdev` flags, `ls -l` layout, a missing
  `sgdisk`), in which case the script stops with a reason rather than guessing. That is the intended failure direction.
