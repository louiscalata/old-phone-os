# SPDX-License-Identifier: GPL-3.0-or-later
#requires -Version 5.1
# READ-ONLY probe and backup for the Surface Duo 1 persistent-kernel experiment: gate G0, the inventory and backup part
# of G1, and the probes of G2 in docs/duo-persistent-install.md. NOTHING IS WRITTEN TO THE PHONE: this script has no
# command that writes to it. It looks, and it copies partitions off the phone to a folder on this PC.
#
#   powershell -File deploy\duo-persist-probe.ps1 -Phase bootloader [-Serial <serial>] [-OutDir <folder>] [-TimeoutSec <n>] [-DryRun]
#   powershell -File deploy\duo-persist-probe.ps1 -Phase twrp [-Serial <serial>] [-OutDir <folder>] [-BootImage <file>]
#                                                  [-PullWith dd|head] [-TimeoutSec <n>] [-DryRun]
#   Pulls default to head, verified on November 12, 2026. The selectable dd form is known not to work on this TWRP.
#
# Run it twice, in this order. It never changes the phone's mode: you put the phone in the right place first.
#   -Phase bootloader  the phone is in the bootloader menu (fastboot). G0 preflight as the install chain does it
#                      (product surfaceduo, current-slot a, unlocked yes, battery 30 percent or more), then the
#                      getvar probes of G2, saved in full.
#   -Phase twrp        the phone runs the pinned TWRP image that the runbook RAM-booted, and adb says "recovery". G0
#                      identity checks, the inventory, a backup of boot_a, boot_b, vbmeta_a, vbmeta_b, dtbo_a, dtbo_b and
#                      misc and of the head of the sde partition table (each read twice, hashed on the phone and on
#                      this PC and compared), then the writability probes of G2. -BootImage only checks a candidate
#                      kernel image file on this PC (magic, size, hash); it is never sent to the phone.
#
# Output: <OutDir>\<date>-<time>-<phase>\ with raw\ (the text of every probe), the backups, SHA256SUMS, report.md and
# probe.json. The folder holds the phone's private data (serial number, partition images): keep it off shared drives.
# The default OutDir is .\duo-persist-probe under the current directory.
#
# Safety rules. They are the point of this script; change none without the owner's say-so.
#   - Every command goes through Invoke-Allowed, which starts the one process and first refuses any line that is not
#     matched, whole and case-sensitively, by the allow-list below. The list holds reads only: cat of a few proc and sys
#     files, ls, df of /tmp, readlink, blockdev getsize64 and getro, sha256sum of one of the listed partitions, sgdisk
#     print and info, and dd or head that reads (it names no output file, so it writes to stdout) piped to this PC with
#     adb exec-out; on the fastboot side only devices and getvar. The pinned TWRP has no bootctl, so none is allowed.
#   - A command that does not finish within -TimeoutSec seconds is killed and the run stops with that reason.
#   - The phone must already be in the mode the phase names. A wrong mode is refused, not corrected.
#   - It stops at the first failure and says plainly that nothing was written. A hash that differs between the phone,
#     the PC file and the second read is a failure.
#   - It refuses to put its output inside any git working tree, so private data cannot be committed by accident.
#   - -DryRun prints the commands and exits. It looks for no tool and contacts no phone.
param(
    [ValidateSet('twrp', 'bootloader')][string]$Phase = 'twrp',
    [string]$Serial = '',
    [string]$OutDir = '',
    [string]$BootImage = '',
    [ValidateSet('dd', 'head')][string]$PullWith = 'head',
    [ValidateRange(2, 3600)][int]$TimeoutSec = 180,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'

$ByName = '/dev/block/platform/soc/1d84000.ufshc/by-name'
$MinBattery = 30
# What the October 31 backup and the partition table of this unit say. A different value means this is not the phone
# the runbook was written for: the script stops. Size is the byte size of the partition.
$Parts = @(
    [pscustomobject]@{ Name = 'boot_a'; Node = 'sde9'; Size = [long]100663296; Optional = $false },
    [pscustomobject]@{ Name = 'boot_b'; Node = 'sde26'; Size = [long]100663296; Optional = $false },
    [pscustomobject]@{ Name = 'vbmeta_a'; Node = 'sde14'; Size = [long]65536; Optional = $false },
    [pscustomobject]@{ Name = 'vbmeta_b'; Node = 'sde31'; Size = [long]65536; Optional = $false },
    [pscustomobject]@{ Name = 'dtbo_a'; Node = 'sde15'; Size = [long]16777216; Optional = $true },
    [pscustomobject]@{ Name = 'dtbo_b'; Node = 'sde32'; Size = [long]16777216; Optional = $true },
    [pscustomobject]@{ Name = 'misc'; Node = 'sda5'; Size = [long]1048576; Optional = $false }
)
# SHA-256 of images seen before, to say what a partition holds. Not a promise about what it holds now.
$Known = @{
    '11a3e3979b92664d1900ef88dffe265fa94ebff865002225e6f33d9cb7867c39' = 'the stock boot.img of the OTA set'
    'bdd438e8fb798344f465deb275e821a9ae8399bd421c90dce7ec7042c02c873f' = 'the Magisk-patched boot_a of the 2026-10-31 backup'
    'cf6baf1d7940f2bf3d982e2645e251c0d7edcf89de4e0a6a7ff8ea620cdf8094' = 'the stock older-build boot_b of the 2026-10-31 backup'
    'beb6bacc048a81c569f97e46a7e4e4ce114d52ad6cfab815c11d5257be027cad' = 'vbmeta_a of the 2026-10-31 backup'
    '6a80fc2a57b35de595e06c8f1f1709ed768c4b251ccf638f547c9c90b516a1f0' = 'vbmeta_b of the 2026-10-31 backup'
    'b50982914a1c0b7dd65a5597a10361af95fb7ddb93c2756720c7708f45367de1' = 'dtbo_a of the 2026-10-31 backup (the OTA dtbo.img)'
    '2726ac9632a45f14567848ba530c92d786b6bb3b3beb5c28e5ab37afdef62bd9' = 'dtbo_b of the 2026-10-31 backup'
    '6c93debe281b2edb75e7733391b65d8a0dd64e349eb9a229a58867a3956f341d' = 'misc of the 2026-10-31 backup'
}
$PortBootSha = 'abec1fe9ce4cbb7a9296c3abd4fd7b4acc1283842e86f2bfe0879a7e84fae552'
$BrakeSha = '892627cb4356d1d16c37bf243d700b5c9a0ab672d263de9ccd640c86b5cd6a5a'

# ---- the allow-list: the only commands this script can issue ----
$NodeRx = 'sde(?:9|14|15|26|31|32)|sda5'
$ByNameRx = '(?:platform/soc/1d84000\.ufshc/)?by-name/(?:boot_[ab]|vbmeta_[ab]|dtbo_[ab]|misc)'
$PartDev = '/dev/block/(?:' + $NodeRx + '|' + $ByNameRx + ')'
$AnyDev = '/dev/block/(?:' + $NodeRx + '|sde|' + $ByNameRx + ')'
$PhoneRules = @(
    '^id\z',
    '^getenforce\z',
    '^dmesg\z',
    '^cat /proc/(?:cmdline|version|partitions|mounts)\z',
    ('^cat /sys/class/block/(?:' + $NodeRx + '|sde)/(?:ro|size)\z'),
    '^ls /sys/class/power_supply\z',
    '^cat /sys/class/power_supply/battery/capacity\z',
    '^ls -l /dev/block/platform/soc/1d84000\.ufshc/by-name\z',
    '^df /tmp\z',
    ('^readlink -f /dev/block/' + $ByNameRx + '\z'),
    ('^blockdev --(?:getsize64|getro) ' + $AnyDev + '\z'),
    ('^sha256sum ' + $PartDev + '\z'),
    '^sgdisk -p /dev/block/sde\z',
    '^sgdisk -i [0-9]{1,3} /dev/block/sde\z'
)
# The two ways to copy a block device to this PC through adb exec-out, both reads. The pinned TWRP's dd is a BSD-style
# binary: it takes if=, bs= and count= (and skip=) as plain decimal numbers and has no status= and no conv=. The head
# form is the default, verified on November 12. With exec-out this TWRP's dd receives 2>/dev/null as an operand and
# returns error text instead of partition bytes. -PullWith dd remains selectable; both allow-list rules stay bounded.
$DdPullRule = '^dd if=' + $AnyDev + ' bs=[0-9]{1,8}(?: skip=[0-9]{1,8})?(?: count=[0-9]{1,8})? 2>/dev/null\z'
$HeadPullRule = '^head -c [0-9]{1,9} ' + $AnyDev + '\z'
$PullRules = @($DdPullRule, $HeadPullRule)
$AdbHostRules = @('^devices\z', '^get-state\z')
$FastbootRules = @('^devices\z', '^getvar all\z', '^getvar [a-z][a-z0-9-]*(?::[a-z0-9_]+)?\z')

function Test-Allowed([string]$Tool, [string]$Line) {
    if ($Line -cmatch '[^\x20-\x7e]') { return $false }
    $rules = @()
    $rest = $Line
    if ($Tool -ceq 'fastboot') {
        $rules = $FastbootRules
    } elseif ($Tool -ceq 'adb') {
        if ($Line.StartsWith('shell ')) { $rules = $PhoneRules; $rest = $Line.Substring(6) }
        elseif ($Line.StartsWith('exec-out ')) { $rules = $PullRules; $rest = $Line.Substring(9) }
        else { $rules = $AdbHostRules }
    } else {
        return $false
    }
    foreach ($r in $rules) { if ($rest -cmatch $r) { return $true } }
    return $false
}

# Kills a command that did not finish in time and stops the run with the reason (the main section records it).
function Stop-Hung($Process, [string]$Tool, [string]$Line, [int]$Seconds) {
    if (-not $Process.HasExited) { $Process.Kill() }
    [void]$Process.WaitForExit(5000)
    throw ('timed out: ' + (Get-Shown $Tool $Line) + " did not finish within $Seconds seconds and was killed. Nothing was written to the phone. A hung fastboot is a known trap after a multi-chunk sparse transfer: hold Power until the screen is dark (twenty to thirty seconds), enter the bootloader menu again and run the phase again into a new folder. Raise -TimeoutSec only for a slow cable.")
}

# The one place a process is started. $Line has passed the allow-list, so it holds no quote, no space inside a word and
# no shell syntax except the 2>/dev/null of the selectable dd read (this TWRP passes it as an operand, not a redirect).
# Output is read as bytes, so a backup is not changed by text handling, and hashed while it streams.
function Invoke-Allowed([string]$Tool, [string]$Line, [string]$ToFile = '', [switch]$HashOnly) {
    if (-not (Test-Allowed $Tool $Line)) { throw "refused: not on the allow-list: $Tool $Line" }
    if (-not $script:Exe.ContainsKey($Tool)) { throw "refused: $Tool was not looked up for this phase" }
    $argLine = $Line
    if ($Line -ne 'devices') {
        if ($script:Serial -notmatch '^[A-Za-z0-9._:-]+\z') { throw 'refused: no valid device serial yet' }
        $argLine = '-s ' + $script:Serial + ' ' + $Line
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $script:Exe[$Tool]
    $psi.Arguments = $argLine
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $errTask = $p.StandardError.ReadToEndAsync()
    $src = $p.StandardOutput.BaseStream
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $buf = New-Object byte[] 1048576
    $total = [long]0
    $fs = $null
    $ms = $null
    if ($ToFile) {
        $fs = New-Object System.IO.FileStream($ToFile, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    } elseif (-not $HashOnly) {
        $ms = New-Object System.IO.MemoryStream
    }
    # The whole command, reads and exit, must finish within -TimeoutSec seconds; if not, it is killed and the run stops.
    $limit = [int]$script:TimeoutSec
    $deadline = [DateTime]::UtcNow.AddSeconds($limit)
    try {
        while ($true) {
            $pending = $src.ReadAsync($buf, 0, $buf.Length)
            $left = [int][Math]::Ceiling(($deadline - [DateTime]::UtcNow).TotalMilliseconds)
            if (-not $pending.Wait([Math]::Max($left, 0))) { Stop-Hung $p $Tool $Line $limit }
            $n = $pending.Result
            if ($n -le 0) { break }
            [void]$sha.TransformBlock($buf, 0, $n, $null, 0)
            $total += $n
            if ($null -ne $fs) { $fs.Write($buf, 0, $n) } elseif ($null -ne $ms) { $ms.Write($buf, 0, $n) }
        }
        [void]$sha.TransformFinalBlock($buf, 0, 0)
    } finally {
        if ($null -ne $fs) { $fs.Dispose() }
    }
    $left = [int][Math]::Ceiling(($deadline - [DateTime]::UtcNow).TotalMilliseconds)
    if (-not $p.WaitForExit([Math]::Max($left, 1))) { Stop-Hung $p $Tool $Line $limit }
    $err = $errTask.Result
    $text = ''
    if ($null -ne $ms) { $text = [System.Text.Encoding]::UTF8.GetString($ms.ToArray()) }
    if ($err -and -not $ToFile -and -not $HashOnly) { $text = $text + "`n" + $err }
    [pscustomobject]@{
        Code = $p.ExitCode
        Text = ($text -replace "`r", '').Trim()
        Stderr = $err
        Bytes = $total
        Sha256 = ([System.BitConverter]::ToString($sha.Hash).Replace('-', '')).ToLower()
        Value = $null
    }
}

# ---- the plan: one list, printed by -DryRun and executed otherwise ----
function New-Step([string]$Id, [string]$Gate, [string]$Tool, [string]$Line, [string]$Kind, [bool]$Required, [hashtable]$More) {
    $s = [ordered]@{ Id = $Id; Gate = $Gate; Tool = $Tool; Line = $Line; Kind = $Kind; Required = $Required; Mode = 'text'
                     Part = ''; Out = ''; Value = $null; Ref = ''; Pattern = ''; Message = '' }
    if ($More) { foreach ($k in $More.Keys) { $s[$k] = $More[$k] } }
    [pscustomobject]$s
}

# The adb exec-out line that copies a block device to stdout: head by default, or the selectable dd form.
function Get-PullLine([string]$Way, [string]$Dev, [long]$Bytes) {
    if ($Way -ceq 'head') { return 'exec-out head -c ' + $Bytes + ' ' + $Dev }
    return 'exec-out dd if=' + $Dev + ' bs=4096 count=' + [long]($Bytes / 4096) + ' 2>/dev/null'
}

function Build-Plan([string]$Phase, [bool]$WithCandidate, [string]$PullWith = 'head') {
    $plan = New-Object System.Collections.ArrayList
    if ($Phase -eq 'bootloader') {
        [void]$plan.Add((New-Step 'b00' 'G0' 'fastboot' 'devices' 'serial' $true @{ Value = 'fastboot' }))
        [void]$plan.Add((New-Step 'b01' 'G0' 'fastboot' 'getvar product' 'match' $true @{ Pattern = '\bproduct:\s*surfaceduo\b'; Message = 'not a Surface Duo 1' }))
        [void]$plan.Add((New-Step 'b02' 'G0' 'fastboot' 'getvar current-slot' 'match' $true @{ Pattern = 'current-slot:\s*a\b'; Message = 'the current slot is not a' }))
        [void]$plan.Add((New-Step 'b03' 'G0' 'fastboot' 'getvar unlocked' 'match' $true @{ Pattern = 'unlocked:\s*yes'; Message = 'the bootloader is not unlocked' }))
        [void]$plan.Add((New-Step 'b04' 'G0' 'fastboot' 'getvar battery-level' 'minint' $true @{ Pattern = 'battery-level:\s*(\d+)'; Value = $MinBattery; Message = "battery is below $MinBattery percent" }))
        $i = 10
        foreach ($v in @('version-bootloader', 'secure', 'slot-count', 'is-userspace', 'max-download-size', 'has-slot:boot',
                         'partition-size:boot', 'partition-size:boot_a', 'partition-size:boot_b',
                         'partition-type:boot_a', 'partition-type:boot_b',
                         'slot-retry-count:a', 'slot-retry-count:b', 'slot-successful:a', 'slot-successful:b',
                         'slot-unbootable:a', 'slot-unbootable:b')) {
            [void]$plan.Add((New-Step ('b' + $i) 'G2' 'fastboot' ('getvar ' + $v) 'info' $false $null))
            $i++
        }
        [void]$plan.Add((New-Step 'b99' 'G2' 'fastboot' 'getvar all' 'info' $false $null))
        return $plan.ToArray()
    }
    [void]$plan.Add((New-Step 't00' 'G0' 'adb' 'devices' 'serial' $true @{ Value = 'recovery' }))
    [void]$plan.Add((New-Step 't01' 'G0' 'adb' 'get-state' 'match' $true @{ Pattern = '^recovery$'; Message = 'adb does not report the recovery state' }))
    [void]$plan.Add((New-Step 't02' 'G0' 'adb' 'shell id' 'match' $true @{ Pattern = 'uid=0\('; Message = 'the adb shell is not root' }))
    [void]$plan.Add((New-Step 't03' 'G0' 'adb' 'shell cat /proc/cmdline' 'cmdline' $true $null))
    [void]$plan.Add((New-Step 't04' 'G0' 'adb' ('shell ls -l ' + $ByName) 'names' $true $null))
    [void]$plan.Add((New-Step 't05' 'G0' 'adb' 'shell cat /proc/mounts' 'unmounted' $true $null))
    [void]$plan.Add((New-Step 't06' 'G0' 'adb' 'shell ls /sys/class/power_supply' 'info' $false $null))
    [void]$plan.Add((New-Step 't07' 'G0' 'adb' 'shell cat /sys/class/power_supply/battery/capacity' 'battery' $false $null))
    foreach ($p in $Parts) {
        $dev = '/dev/block/' + $p.Node
        [void]$plan.Add((New-Step ('i-' + $p.Name + '-link') 'G1' 'adb' ('shell readlink -f ' + $ByName + '/' + $p.Name) 'equals' $true @{ Part = $p.Name; Value = $dev; Message = ($p.Name + ' does not resolve to ' + $dev) }))
        [void]$plan.Add((New-Step ('i-' + $p.Name + '-size') 'G1' 'adb' ('shell blockdev --getsize64 ' + $dev) 'equals' $true @{ Part = $p.Name; Value = [string]$p.Size; Message = ($p.Name + ' is not ' + $p.Size + ' bytes') }))
    }
    foreach ($p in $Parts) {
        $dev = '/dev/block/' + $p.Node
        $pull = Get-PullLine $PullWith $dev $p.Size
        [void]$plan.Add((New-Step ('k-' + $p.Name + '-sha') 'G1' 'adb' ('shell sha256sum ' + $dev) 'sha' $true @{ Part = $p.Name }))
        [void]$plan.Add((New-Step ('k-' + $p.Name + '-pull') 'G1' 'adb' $pull 'pull' $true @{ Part = $p.Name; Mode = 'file'; Out = ($p.Name + '.img'); Value = $p.Size; Ref = ('k-' + $p.Name + '-sha') }))
        [void]$plan.Add((New-Step ('k-' + $p.Name + '-again') 'G1' 'adb' $pull 'again' $true @{ Part = $p.Name; Mode = 'hash'; Ref = ('k-' + $p.Name + '-pull') }))
    }
    $gpt = Get-PullLine $PullWith '/dev/block/sde' 1048576
    [void]$plan.Add((New-Step 'k-gpt-pull' 'G1' 'adb' $gpt 'pull' $true @{ Mode = 'file'; Out = 'sde-table-head.bin'; Value = [long]1048576 }))
    [void]$plan.Add((New-Step 'k-gpt-again' 'G1' 'adb' $gpt 'again' $true @{ Mode = 'hash'; Ref = 'k-gpt-pull' }))
    foreach ($p in $Parts) {
        [void]$plan.Add((New-Step ('p-ro-' + $p.Name) 'G2' 'adb' ('shell blockdev --getro /dev/block/' + $p.Node) 'info' $false @{ Part = $p.Name }))
        [void]$plan.Add((New-Step ('p-sysro-' + $p.Name) 'G2' 'adb' ('shell cat /sys/class/block/' + $p.Node + '/ro') 'info' $false @{ Part = $p.Name }))
    }
    [void]$plan.Add((New-Step 'p-ro-lun' 'G2' 'adb' 'shell blockdev --getro /dev/block/sde' 'info' $false $null))
    [void]$plan.Add((New-Step 'p-sysro-lun' 'G2' 'adb' 'shell cat /sys/class/block/sde/ro' 'info' $false $null))
    [void]$plan.Add((New-Step 'p-enforce' 'G2' 'adb' 'shell getenforce' 'info' $false $null))
    [void]$plan.Add((New-Step 'p-sgdisk' 'G2' 'adb' 'shell sgdisk -p /dev/block/sde' 'info' $false $null))
    # sgdisk -i is unavailable in the pinned TWRP (invalid option -- i); omit those two info reads.
    [void]$plan.Add((New-Step 'p-df-tmp' 'G2' 'adb' 'shell df /tmp' 'info' $false $null))
    [void]$plan.Add((New-Step 'p-partitions' 'G2' 'adb' 'shell cat /proc/partitions' 'info' $false $null))
    [void]$plan.Add((New-Step 'p-version' 'G2' 'adb' 'shell cat /proc/version' 'info' $false $null))
    [void]$plan.Add((New-Step 'p-dmesg' 'G2' 'adb' 'shell dmesg' 'info' $false $null))
    if ($WithCandidate) {
        [void]$plan.Add((New-Step 't08' 'G2' 'pc' 'check the -BootImage file on this PC (never sent to the phone)' 'candidate' $true $null))
    }
    return $plan.ToArray()
}

# ---- helpers ----
function Log([string]$m) {
    $l = '{0:HH:mm:ss} {1}' -f (Get-Date), $m
    if ($script:RunDir -and (Test-Path -LiteralPath $script:RunDir)) { Add-Content -LiteralPath (Join-Path $script:RunDir 'probe.log') -Value $l }
    Write-Host $l
}
function Write-TextFile([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}
function Find-GitRoot([string]$Path) {
    $d = $Path
    while ($d) {
        if (Test-Path -LiteralPath (Join-Path $d '.git')) { return $d }
        $parent = Split-Path -Parent $d
        if (-not $parent -or $parent -eq $d) { break }
        $d = $parent
    }
    return $null
}
function Resolve-Tool([string]$Name) {
    $c = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $c) { throw "$Name is not on PATH" }
    return $c.Source
}
function Get-Shown([string]$Tool, [string]$Line) {
    if ($Tool -eq 'pc') { return '(PC only) ' + $Line }
    if ($Line -eq 'devices') { return $Tool + ' devices' }
    return $Tool + ' -s <serial> ' + $Line
}
function Get-Label([string]$Sha) {
    if ($script:Known.ContainsKey($Sha)) { return $script:Known[$Sha] }
    return 'not one of the images listed in this script'
}
function Test-Candidate([string]$Path, [long]$Limit) {
    $fi = Get-Item -LiteralPath $Path -ErrorAction Stop
    $head = New-Object byte[] 64
    $fs = [System.IO.File]::OpenRead($fi.FullName)
    try { [void]$fs.Read($head, 0, 64) } finally { $fs.Dispose() }
    $magic = [System.Text.Encoding]::ASCII.GetString($head, 0, 8)
    $sha = (Get-FileHash -LiteralPath $fi.FullName -Algorithm SHA256).Hash.ToLower()
    [pscustomobject]@{
        Path = $fi.FullName; Size = [long]$fi.Length; Magic = $magic; HeaderVersion = [System.BitConverter]::ToUInt32($head, 40)
        Sha256 = $sha; Fits = ([long]$fi.Length -le $Limit); PartitionSized = ([long]$fi.Length -eq $Limit)
        PinnedPortImage = ($sha -ceq $script:PortBootSha)
    }
}

# Returns $null when the step's answer is acceptable, else the reason it is not.
function Confirm-Step($s, $r) {
    $t = $r.Text
    $short = ($t -replace '\s+', ' ')
    if ($short.Length -gt 160) { $short = $short.Substring(0, 160) + '...' }
    if ($s.Kind -eq 'info') { return $null }
    if ($s.Kind -eq 'serial') {
        $rows = @()
        foreach ($line in ($t -split "`n")) {
            if ($line -match '^(\S+)\s+(\S+)\s*$') { $rows += [pscustomobject]@{ Serial = $Matches[1]; State = $Matches[2] } }
        }
        if ($script:Serial) {
            if (@($rows | Where-Object { $_.Serial -ceq $script:Serial }).Count -ne 1) { return "no device $($script:Serial) in the list: $short" }
        } else {
            if ($rows.Count -ne 1) { return "$($rows.Count) devices are listed; pass -Serial: $short" }
            $script:Serial = $rows[0].Serial
        }
        $state = @($rows | Where-Object { $_.Serial -ceq $script:Serial })[0].State
        if ($state -cne $s.Value) { return "the device state is '$state', the phase needs '$($s.Value)'; the script does not change modes" }
        return $null
    }
    if ($s.Kind -eq 'match') {
        if ($t -cmatch $s.Pattern) { return $null }
        return $s.Message + ': ' + $short
    }
    if ($s.Kind -eq 'minint') {
        if ($t -match $s.Pattern -and [int]$Matches[1] -ge [int]$s.Value) { return $null }
        return $s.Message + ': ' + $short
    }
    if ($s.Kind -eq 'equals') {
        if ($t -ceq [string]$s.Value) { return $null }
        return $s.Message + ' (got: ' + $short + ')'
    }
    if ($s.Kind -eq 'cmdline') {
        if ($t -cmatch 'androidboot\.slot_suffix=_b\b') { return 'the kernel command line says slot b; the runbook starts from slot a' }
        return $null
    }
    if ($s.Kind -eq 'names') {
        foreach ($p in $script:Parts) {
            $listed = ($t -cmatch ('(?m)\b' + $p.Name + '\b'))
            $script:Present[$p.Name] = $listed
            if (-not $listed -and -not $p.Optional) { return "$($p.Name) is not listed in $script:ByName" }
        }
        return $null
    }
    if ($s.Kind -eq 'unmounted') {
        $targets = @('/dev/block/sde')
        foreach ($p in $script:Parts) { $targets += '/dev/block/' + $p.Node; $targets += $script:ByName + '/' + $p.Name; $targets += '/dev/block/by-name/' + $p.Name }
        foreach ($line in ($t -split "`n")) {
            $f = ($line.Trim() -split '\s+')[0]
            if ($targets -contains $f) { return "$f is mounted: $line" }
        }
        return $null
    }
    if ($s.Kind -eq 'battery') {
        if ($t -match '^\d+$' -and [int]$t -lt $script:MinBattery) { return "battery is $t percent, below $script:MinBattery" }
        return $null
    }
    if ($s.Kind -eq 'sha') {
        if ($t -cmatch '^([0-9a-f]{64})\s') { $r.Value = $Matches[1]; return $null }
        return 'sha256sum did not return a hash: ' + $short
    }
    if ($s.Kind -eq 'pull') {
        if ($r.Bytes -ne [long]$s.Value) { return "short or long read: $($r.Bytes) bytes, expected $($s.Value)" }
        $disk = (Get-FileHash -LiteralPath (Join-Path $script:RunDir $s.Out) -Algorithm SHA256).Hash.ToLower()
        if ($disk -cne $r.Sha256) { return 'the file on disk does not hash like the stream that wrote it' }
        if ($s.Ref -and $script:Results[$s.Ref].Value -cne $r.Sha256) {
            return "the hash on the PC ($($r.Sha256)) differs from the hash on the phone ($($script:Results[$s.Ref].Value))"
        }
        $r.Value = $r.Sha256
        return $null
    }
    if ($s.Kind -eq 'again') {
        if ($r.Sha256 -cne $script:Results[$s.Ref].Value) { return "the second read ($($r.Sha256)) differs from the first ($($script:Results[$s.Ref].Value))" }
        $r.Value = $r.Sha256
        return $null
    }
    if ($s.Kind -eq 'candidate') {
        $c = $r.Candidate
        if ($c.Magic -cne 'ANDROID!') { return "the image does not start with ANDROID! (starts with '$($c.Magic)')" }
        if (-not $c.Fits) { return "the image is $($c.Size) bytes, larger than the $($script:Parts[1].Size) of boot_b" }
        return $null
    }
    return "internal: unknown step kind $($s.Kind)"
}

function Add-Record([string]$Id, [string]$Gate, [string]$Command, [bool]$Required, [string]$Status, $Code, $Bytes, [string]$Sha, [string]$Note) {
    [void]$script:Records.Add([pscustomobject]@{ id = $Id; gate = $Gate; command = $Command; required = $Required; status = $Status
                                                  exit_code = $Code; bytes = $Bytes; sha256 = $Sha; note = $Note })
}

function Invoke-Step($s) {
    $script:Current = $s.Id
    $shown = Get-Shown $s.Tool $s.Line
    if ($s.Part -and $script:Present.ContainsKey($s.Part) -and -not $script:Present[$s.Part]) {
        Log "skip $($s.Id): $($s.Part) is not present on this phone"
        Add-Record $s.Id $s.Gate $shown $s.Required 'skipped' $null 0 '' 'partition not present'
        return
    }
    $r = $null
    if ($s.Tool -eq 'pc') {
        $c = Test-Candidate $script:BootImage ($script:Parts[1].Size)
        $r = [pscustomobject]@{ Code = 0; Text = ($c | ConvertTo-Json -Compress); Stderr = ''; Bytes = [long]0; Sha256 = ''; Value = $null; Candidate = $c }
    } elseif ($s.Mode -eq 'file') {
        $r = Invoke-Allowed $s.Tool $s.Line -ToFile (Join-Path $script:RunDir $s.Out)
    } elseif ($s.Mode -eq 'hash') {
        $r = Invoke-Allowed $s.Tool $s.Line -HashOnly
    } else {
        $r = Invoke-Allowed $s.Tool $s.Line
    }
    $script:Results[$s.Id] = $r
    if ($s.Mode -eq 'text') {
        Write-TextFile (Join-Path (Join-Path $script:RunDir 'raw') ($s.Id + '.txt')) ('# ' + $shown + "`n# exit " + $r.Code + "`n" + $r.Text + "`n")
    }
    $why = Confirm-Step $s $r
    $status = 'ok'
    $note = ''
    if ($s.Kind -eq 'info') {
        $short = ($r.Text.Length -lt 300)
        if ($r.Code -ne 0 -or ($short -and $r.Text -match 'not found|No such file|Permission denied|FAILED|inaccessible|unknown option|Usage:')) {
            $status = 'unavailable'; $note = ($r.Text -replace '\s+', ' ')
        }
    } elseif ($why) {
        $status = 'failed'; $note = $why
    } elseif ($r.Stderr -and ($s.Mode -ne 'text') -and $r.Stderr.Trim()) {
        $note = 'adb also printed: ' + ($r.Stderr.Trim() -replace '\s+', ' ')
    }
    Add-Record $s.Id $s.Gate $shown $s.Required $status $r.Code $r.Bytes $r.Sha256 $note
    Log ("{0} {1}: {2}" -f $s.Id, $status, ($(if ($note) { $note } else { $shown })))
    if ($status -eq 'failed') { throw $why }
}

function Get-Answer([string]$Id) {
    if ($script:Results.ContainsKey($Id)) { return $script:Results[$Id].Text }
    return 'not read'
}

function Get-Clean([string]$Id) {
    $x = (Get-Answer $Id) -replace '(?im)^\s*finished\. total time:.*$', ''
    return (($x -replace '\s+', ' ').Trim())
}

function Get-Var([string]$Name) {
    foreach ($x in $script:Plan) { if ($x.Line -ceq ('getvar ' + $Name)) { return (Get-Clean $x.Id) } }
    return ($Name + ': not read')
}

function Build-Summary([string]$Status) {
    $S = New-Object System.Collections.ArrayList
    [void]$S.Add('- Nothing was written to the phone. The script has no command that writes to it.')
    if ($Phase -eq 'bootloader') {
        [void]$S.Add('- Preflight, as the install chain does it: ' + (Get-Clean 'b01') + '; ' + (Get-Clean 'b02') + '; ' + (Get-Clean 'b03') + '; ' + (Get-Clean 'b04') + " (the minimum is $MinBattery percent).")
        $counters = @('slot-retry-count:a', 'slot-retry-count:b', 'slot-successful:a', 'slot-successful:b', 'slot-unbootable:a', 'slot-unbootable:b')
        [void]$S.Add('- Slot counters as the bootloader reports them: ' + (($counters | ForEach-Object { Get-Var $_ }) -join '; ') + '.')
        $naming = @('is-userspace', 'has-slot:boot', 'partition-size:boot', 'partition-size:boot_a', 'partition-size:boot_b', 'partition-type:boot_a', 'partition-type:boot_b')
        [void]$S.Add('- Mode and naming probes: ' + (($naming | ForEach-Object { Get-Var $_ }) -join '; ') + '.')
        [void]$S.Add('- The full dump of every variable is raw/b99.txt. Read it against the decision tables of gate G2 in the runbook.')
        return $S
    }
    $kline = Get-Clean 't03'
    $suffix = 'not stated'
    if ($kline -match 'androidboot\.slot_suffix=(\S+)') { $suffix = $Matches[1] }
    $bl = 'not stated'
    if ($kline -match 'androidboot\.bootloader=(\S+)') { $bl = $Matches[1] }
    [void]$S.Add("- Phone: serial $($script:Serial), adb state recovery, kernel command line slot suffix $suffix, bootloader version $bl.")
    $held = @($script:Records | Where-Object { $_.id -like 'k-*-again' -and $_.status -eq 'ok' }).Count
    [void]$S.Add("- Backups: $held reads of partitions and of the head of the sde partition table are in this folder. Each partition was hashed on the phone, copied, hashed on this PC and read a second time; a mismatch would have stopped the run.")
    foreach ($n in @('boot_a', 'boot_b')) {
        $id = 'k-' + $n + '-again'
        if ($script:Results.ContainsKey($id) -and $script:Results[$id].Value) { [void]$S.Add("- $n holds: " + (Get-Label $script:Results[$id].Value) + '.') }
    }
    [void]$S.Add('- None of the listed partitions, nor the whole sde LUN, is mounted.')
    $flag = $false
    foreach ($id in @('p-ro-boot_b', 'p-sysro-boot_b', 'p-ro-lun', 'p-sysro-lun')) { if ((Get-Answer $id).Trim() -ceq '1') { $flag = $true } }
    if ($flag) {
        [void]$S.Add('- **A read-only flag is set on boot_b or on the sde LUN.** Do not go on to G3; see "What if the probes say no" in the runbook.')
    } else {
        [void]$S.Add('- No read-only flag was reported for boot_b or for the sde LUN. That removes the cheapest reason for a write to fail. It does NOT show that a write will be accepted: a UFS write protect or a rule inside the bootloader would not show here.')
    }
    $miss = @($script:Records | Where-Object { $_.status -eq 'unavailable' } | ForEach-Object { $_.id })
    if ($miss.Count) { [void]$S.Add('- Probes that gave no answer (the tool may not be in this TWRP; not a failure): ' + ($miss -join ', ') + '.') }
    [void]$S.Add('- The decision about G3 is the owner''s. Nothing further happens without the owner''s word.')
    return $S
}

function Build-Report([string]$Status, [string]$StopId, [string]$Reason) {
    $L = New-Object System.Collections.ArrayList
    $stamp2 = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') + ' UTC'
    [void]$L.Add("# Duo persistent-kernel probe, phase $Phase")
    [void]$L.Add('')
    [void]$L.Add("Run at $stamp2 by deploy/duo-persist-probe.ps1. **Nothing was written to the phone.** The script has no command that writes to it.")
    [void]$L.Add('This folder holds private data (the phone serial number, copies of its partitions). Keep it off shared drives and out of git.')
    [void]$L.Add('')
    if ($Status -ne 'complete') {
        [void]$L.Add("## STOPPED at $StopId")
        [void]$L.Add('')
        [void]$L.Add($Reason)
        [void]$L.Add('')
        [void]$L.Add('The script stopped at the first failure and did nothing after it. Nothing was written to the phone. Do not go on to the next gate: read the reason, fix its cause and run the phase again into a new folder.')
        [void]$L.Add('')
    } else {
        [void]$L.Add('## Summary for the owner')
        [void]$L.Add('')
        [void]$L.Add('Every required check passed. Each line below was read from the phone in this run (MEASURED, raw text in raw/); the last section lists what the run cannot settle.')
        [void]$L.Add('')
        foreach ($x in (Build-Summary $Status)) { [void]$L.Add($x) }
        [void]$L.Add('')
    }
    if ($Phase -eq 'twrp') {
        [void]$L.Add('## Backups (G1)')
        [void]$L.Add('')
        [void]$L.Add('| Partition | Node | Bytes | SHA-256 (phone = PC = second read) | What it matches |')
        [void]$L.Add('|---|---|---|---|---|')
        foreach ($p in $Parts) {
            $id = 'k-' + $p.Name + '-again'
            if ($script:Results.ContainsKey($id) -and $script:Results[$id].Value) {
                $h = $script:Results[$id].Value
                [void]$L.Add("| $($p.Name) | $($p.Node) | $($p.Size) | ``$h`` | $(Get-Label $h) |")
            } elseif ($script:Present.ContainsKey($p.Name) -and -not $script:Present[$p.Name]) {
                [void]$L.Add("| $($p.Name) | $($p.Node) | - | not present on this phone | skipped |")
            } else {
                [void]$L.Add("| $($p.Name) | $($p.Node) | - | not read | - |")
            }
        }
        if ($script:Results.ContainsKey('k-gpt-again') -and $script:Results['k-gpt-again'].Value) {
            [void]$L.Add("| head of the sde partition table | sde | 1048576 | ``$($script:Results['k-gpt-again'].Value)`` | the slot attribute bits live here; two reads agree |")
        }
        [void]$L.Add('')
        [void]$L.Add('SHA256SUMS in this folder lists the same hashes in the layout that `sha256sum -c` reads. A label says only that the hash equals an image seen before; it is not a promise about what the partition holds when it is next written.')
        [void]$L.Add('')
        $misc = Join-Path $script:RunDir 'misc.img'
        if (Test-Path -LiteralPath $misc) {
            $fs = [System.IO.File]::OpenRead($misc)
            $head = New-Object byte[] 2048
            try { [void]$fs.Read($head, 0, 2048) } finally { $fs.Dispose() }
            $nz = @($head | Where-Object { $_ -ne 0 }).Count
            $sha2 = [System.Security.Cryptography.SHA256]::Create()
            $hh = ([System.BitConverter]::ToString($sha2.ComputeHash($head)).Replace('-', '')).ToLower()
            $state = 'other content'
            if ($nz -eq 0) { $state = 'all zero (no brake, no pending request)' } elseif ($hh -ceq $BrakeSha) { $state = 'equal to misc-brake.img (the one-shot bootonce-bootloader flag is armed)' }
            [void]$L.Add('## The first 2048 bytes of misc')
            [void]$L.Add('')
            [void]$L.Add("- $state; $nz non-zero bytes; SHA-256 ``$hh``.")
            [void]$L.Add('')
        }
        [void]$L.Add('## Writability probes (G2)')
        [void]$L.Add('')
        [void]$L.Add('| Read | Answer |')
        [void]$L.Add('|---|---|')
        foreach ($x in $script:Records) {
            if ($x.id -match '^p-(ro|sysro|enforce|df)') {
                $ans = ((Get-Answer $x.id) -replace '\s+', ' ')
                if ($x.status -eq 'unavailable') { $ans = 'unavailable: ' + [string]$x.note }
                [void]$L.Add("| ``$($x.command -replace '^adb -s <serial> shell ', '')`` | $ans |")
            }
        }
        [void]$L.Add('')
        [void]$L.Add('The partition table dump (`sgdisk -p`) and the dmesg are in raw/. The two `sgdisk -i` info reads are omitted: this TWRP reports invalid option -- i, so no decoded slot attribute baseline is available. The df line shows the room in the phone''s /tmp for the 96 MiB image that G3 pushes. A probe marked unavailable is not a failure: the tool may simply not be in this TWRP.')
        [void]$L.Add('Pulls use head by default. The selectable -PullWith dd form is known not to work on this TWRP: dd receives 2>/dev/null as an operand and returns error text. A short read with the default head form requires investigation; do not switch to dd as a retry.')
        [void]$L.Add('')
        if ($script:Results.ContainsKey('t08')) {
            $c = $script:Results['t08'].Candidate
            [void]$L.Add('## Candidate kernel image (checked on this PC only)')
            [void]$L.Add('')
            [void]$L.Add("- ``$($c.Path)``: $($c.Size) bytes, magic $($c.Magic), header version $($c.HeaderVersion), SHA-256 ``$($c.Sha256)``.")
            [void]$L.Add("- Fits boot_b: $($c.Fits). Exactly the partition size: $($c.PartitionSized). Equal to the image pinned in deploy/duo-opos-boot.ps1: $($c.PinnedPortImage). The runbook pads the image to the partition size before G3.")
            [void]$L.Add('')
        }
    } else {
        [void]$L.Add('## Bootloader answers (G2)')
        [void]$L.Add('')
        foreach ($x in $script:Records) {
            if ($x.id -match '^b(0[1-4]|[12]\d)$') { [void]$L.Add("- ``$($x.command -replace '^fastboot -s <serial> ', '')``: " + (Get-Clean $x.id)) }
        }
        [void]$L.Add('')
        [void]$L.Add('An error answer for a variable (for example partition-size:boot, a name the bootloader may not map) is information, not a failure.')
        [void]$L.Add('')
    }
    [void]$L.Add('## Steps')
    [void]$L.Add('')
    [void]$L.Add('| Gate | Step | Status | Command | Note |')
    [void]$L.Add('|---|---|---|---|---|')
    foreach ($x in $script:Records) {
        $n = ([string]$x.note -replace '\|', '/')
        if ($n.Length -gt 120) { $n = $n.Substring(0, 120) + '...' }
        [void]$L.Add("| $($x.gate) | $($x.id) | $($x.status) | ``$($x.command)`` | $n |")
    }
    [void]$L.Add('')
    [void]$L.Add('## What this run does not settle')
    [void]$L.Add('')
    [void]$L.Add('- Whether the phone accepts a write to boot_a or boot_b. Nothing was written.')
    [void]$L.Add('- Whether the kernel image starts from a slot, whether the other slot can start, and what the bootloader does after failed starts.')
    [void]$L.Add('- Why an earlier attempt to write boot_a with fastboot was refused (one line in a log, October 31, 2026: no raw output was kept).')
    [void]$L.Add('')
    return ($L -join "`n") + "`n"
}

# ---- main ----
if ($Serial -and $Serial -notmatch '^[A-Za-z0-9._:-]+\z') { throw '-Serial may hold letters, digits and . _ : - only' }
if ($BootImage -and $Phase -ne 'twrp') { throw '-BootImage belongs to -Phase twrp' }
if ($PSBoundParameters.ContainsKey('PullWith') -and $Phase -ne 'twrp') { throw '-PullWith belongs to -Phase twrp' }
if (-not $OutDir) { $OutDir = Join-Path (Get-Location).Path 'duo-persist-probe' }
$OutDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutDir)
$inside = Find-GitRoot $OutDir
if ($inside) { throw "refused: the output folder $OutDir is inside the git working tree $inside. It would hold private data. Pass -OutDir with a folder outside every repository." }
if ($BootImage) {
    $BootImage = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($BootImage)
    if (-not (Test-Path -LiteralPath $BootImage -PathType Leaf)) { throw "-BootImage $BootImage is not a file" }
}
$plan = Build-Plan $Phase ([bool]$BootImage) $PullWith
$stamp = (Get-Date).ToString('yyyyMMdd-HHmmss')
$RunDir = Join-Path $OutDir ($stamp + '-' + $Phase)

if ($DryRun) {
    Write-Host "DRY RUN, phase $Phase. Nothing was run: no tool was looked up and no phone was contacted."
    Write-Host "Output folder it would create: $RunDir"
    Write-Host 'Every command below passed the allow-list check; none of them writes to the phone.'
    foreach ($s in $plan) {
        $extra = ''
        if ($s.Mode -eq 'file') { $extra = '   -> ' + $s.Out }
        if ($s.Mode -eq 'hash') { $extra = '   (second read, hashed on the fly)' }
        if ($s.Tool -ne 'pc' -and -not (Test-Allowed $s.Tool $s.Line)) { throw "internal: the plan holds a line that is not allowed: $($s.Line)" }
        Write-Host ("[{0}] {1}: {2}{3}" -f $s.Gate, $s.Id, (Get-Shown $s.Tool $s.Line), $extra)
    }
    exit 0
}

$script:Exe = @{}
if ($Phase -eq 'twrp') { $script:Exe['adb'] = Resolve-Tool 'adb.exe' } else { $script:Exe['fastboot'] = Resolve-Tool 'fastboot.exe' }
if (Test-Path -LiteralPath $RunDir) { throw "refused: $RunDir already exists" }
New-Item -ItemType Directory -Path (Join-Path $RunDir 'raw') -Force | Out-Null
$script:RunDir = $RunDir
$script:Serial = $Serial
$script:TimeoutSec = $TimeoutSec
$script:BootImage = $BootImage
$script:Results = @{}
$script:Present = @{}
$script:Records = New-Object System.Collections.ArrayList
$script:Plan = $plan
$script:Current = 'start'
$started = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
Log "phase $Phase; output $RunDir; nothing is written to the phone by this script"

$status = 'complete'
$stopId = ''
$reason = ''
try {
    foreach ($s in $plan) { Invoke-Step $s }
} catch {
    $status = 'stopped'
    $stopId = $script:Current
    $reason = $_.Exception.Message
    $last = $null
    if ($script:Records.Count) { $last = $script:Records[$script:Records.Count - 1] }
    if (-not $last -or $last.id -ne $stopId -or $last.status -ne 'failed') { Add-Record $stopId '' '' $true 'failed' $null 0 '' $reason }
}

$sums = New-Object System.Collections.ArrayList
$backups = New-Object System.Collections.ArrayList
foreach ($s in $plan) {
    if ($s.Kind -eq 'pull' -and $script:Results.ContainsKey($s.Id) -and $script:Results[$s.Id].Value) {
        $again = 'k-' + ($s.Id -replace '^k-', '' -replace '-pull$', '') + '-again'
        $agreed = ($script:Results.ContainsKey($again) -and $script:Results[$again].Value -ceq $script:Results[$s.Id].Value)
        [void]$sums.Add($script:Results[$s.Id].Value + '  ' + $s.Out)
        [void]$backups.Add([ordered]@{ file = $s.Out; bytes = $script:Results[$s.Id].Bytes; sha256 = $script:Results[$s.Id].Value
                                       sha256_on_phone = $(if ($s.Ref) { $script:Results[$s.Ref].Value } else { $null }); second_read_agrees = $agreed
                                       label = (Get-Label $script:Results[$s.Id].Value) })
    }
}
if ($sums.Count) { Write-TextFile (Join-Path $RunDir 'SHA256SUMS') (($sums -join "`n") + "`n") }
Write-TextFile (Join-Path $RunDir 'report.md') (Build-Report $status $stopId $reason)
$doc = [ordered]@{
    schema = 1; tool = 'duo-persist-probe'; phase = $Phase; status = $status; stopped_at = $(if ($status -eq 'complete') { $null } else { $stopId })
    reason = $(if ($status -eq 'complete') { $null } else { $reason }); started_utc = $started; finished_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    nothing_written_to_phone = $true; serial = $script:Serial; steps = @($script:Records); backups = @($backups)
}
Write-TextFile (Join-Path $RunDir 'probe.json') (($doc | ConvertTo-Json -Depth 6) + "`n")
if ($status -ne 'complete') {
    Write-Host ''
    Write-Host "STOPPED at $stopId. Nothing was written to the phone. Reason: $reason"
    Write-Host "Report: $(Join-Path $RunDir 'report.md')"
    exit 1
}
Write-Host ''
Write-Host "Done. Nothing was written to the phone. Report: $(Join-Path $RunDir 'report.md')"
