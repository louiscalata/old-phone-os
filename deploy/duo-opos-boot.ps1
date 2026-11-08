# SPDX-License-Identifier: GPL-3.0-or-later
#requires -Version 5.1
# Put the OPOS OS rootfs on a Surface Duo 1 and RAM-boot it once, following the Droidian port's flow
# (docs/duo-usb-install.md, packaging/duo-os/README.md). Windows only: needs fastboot.exe and adb.exe on PATH
# (ssh.exe too, for the optional diagnostics). Not a permanent install: the phone runs from RAM-boot.
#
#   powershell -File deploy\duo-opos-boot.ps1 -ImageDir <folder> -RootfsSha256 <sha256> [-Key <private key>] [-Yes]
#
# <folder> holds boot.img, surfaceduo1-twrp.img and misc-brake.img (the port release item-duo1-20261025-port0.22.1;
# their SHA-256 are pinned below) and the OPOS rootfs (-Rootfs, default opos-os-rootfs.img) whose SHA-256 you give.
# Without -Yes it checks those files, prints the steps and stops: it does not touch the phone.
# With -Yes it ERASES userdata (everything Android stored) on the phone in Boot Select (fastboot):
#   1. preflight, then arm the misc brake, format userdata as ext4, RAM-boot TWRP;
#   2. push the rootfs to /data/rootfs.img and compare its SHA-256 on the phone;
#   3. back to the bootloader, re-arm the brake, RAM-boot the port's kernel ONCE;
#   4. find the phone on the USB network (TCP 22 across the /24 of the RNDIS adapter's address, host number
#      -PreferHost first; -PhoneIp skips the search) and, only when -Key is given, read its state and logs over
#      ssh. Without -Key that step is skipped. -PhoneIp must be a dotted IPv4 address and -PreferHost a number
#      from 1 to 254; both are checked at the start, before any file is read and before the phone is touched.
#
# Safety rules. They are the point of this script; change none without the owner's say-so.
#   - SHA-256 of every file is checked before the first write; the rootfs is checked again on the phone.
#   - Preflight on the phone: product surfaceduo, current-slot a, unlocked yes, battery 20 percent or more.
#   - The misc brake (a one-shot bootonce-bootloader flag in misc) is armed before TWRP and again before the
#     kernel, so a crash or a reboot should land in Boot Select, not in stock Android (which would wipe
#     userdata). A precaution, not a guarantee.
#   - The kernel is RAM-booted exactly ONCE and never retried, even if something after it fails. A kernel that
#     crashed may leave the slot rejecting RAM-boots with Device Error; stop and ask, do not try again.
#   - Only misc (erase, flash) and userdata (format) are written. Nothing is written to boot, vbmeta, dtbo or
#     recovery (the bootloader refuses it with Device Error and the port forbids it); no slot is changed.
#   - The first failure stops the script. Nothing here reboots the phone's own kernel; the ssh step only reads.
#     Never run reboot or systemctl reboot on the RAM-booted phone: use reboot bootloader.
param(
    [Parameter(Mandatory = $true)][string]$ImageDir,
    [Parameter(Mandatory = $true)][string]$RootfsSha256,
    [string]$Rootfs = 'opos-os-rootfs.img',
    [string]$Key = '',
    [string]$Serial = '',
    [string]$PhoneIp = '',
    [int]$PreferHost = 82,
    [string]$SshUser = 'droidian',
    [string]$LogDir = '',
    [int]$WaitMinutes = 240,
    [switch]$Yes
)
$ErrorActionPreference = 'Stop'

function Log($m) {
    $l = '{0:HH:mm:ss} {1}' -f (Get-Date), $m
    Add-Content -Path $script:log -Value $l
    Write-Host $l
}
# Run a native tool; stderr is text here (fastboot prints its answers there), the exit code is the verdict.
function Native([string]$exe, [string[]]$a) {
    $ErrorActionPreference = 'Continue'
    $o = (& $exe @a 2>&1 | ForEach-Object { "$_" }) -join "`n"
    [pscustomobject]@{ Code = $LASTEXITCODE; Text = $o.Trim() }
}
function Fb {
    $r = Native 'fastboot.exe' (@('-s', $script:Serial) + $args)
    Log ("fastboot $($args -join ' ') -> " + ($r.Text -replace '\s+', ' '))
    if ($r.Code -ne 0 -or $r.Text -match 'FAILED') { throw "fastboot $($args -join ' ') failed: $($r.Text)" }
    $r.Text
}
function Ab {
    $r = Native 'adb.exe' (@('-s', $script:Serial) + $args)
    Log ("adb $($args -join ' ') -> " + (($r.Text -replace '\s+', ' ') -replace '^(.{300}).*$', '$1...'))
    $r.Text
}
function WaitFor([scriptblock]$cond, [int]$sec, [string]$what) {
    $t0 = Get-Date
    while (((Get-Date) - $t0).TotalSeconds -lt $sec) { if (& $cond) { return }; Start-Sleep 2 }
    throw "timed out waiting for $what"
}
function FastbootSerials {
    $ErrorActionPreference = 'Continue'
    @(& fastboot.exe devices 2>$null | ForEach-Object { if ("$_" -match '^(\S+)\s+fastboot') { $Matches[1] } })
}
function InRecovery {
    $ErrorActionPreference = 'Continue'
    ((& adb.exe devices 2>$null | ForEach-Object { "$_" }) -join "`n") -match ('(?m)^' + [regex]::Escape($script:Serial) + '\s+recovery')
}

# Hosts of $Hosts that accept a TCP connection on $Port within $TimeoutMs, in the order given. All probes
# run at once (async connects), so a whole /24 costs one timeout, not 254.
function Test-Tcp([string[]]$Hosts, [int]$Port, [int]$TimeoutMs) {
    $probes = @(foreach ($h in $Hosts) {
        $c = New-Object System.Net.Sockets.TcpClient
        [pscustomobject]@{ Ip = $h; Client = $c; Task = $c.ConnectAsync([System.Net.IPAddress]::Parse($h), $Port) }
    })
    try {
        $end = [DateTime]::UtcNow.AddMilliseconds($TimeoutMs)
        while ([DateTime]::UtcNow -lt $end -and @($probes | Where-Object { -not $_.Task.IsCompleted }).Count -gt 0) { Start-Sleep -Milliseconds 50 }
        @($probes | Where-Object { $_.Task.Status -eq 'RanToCompletion' -and $_.Client.Connected } | ForEach-Object { $_.Ip })
    } finally {
        foreach ($p in $probes) { $p.Client.Close() }
    }
}
# The phone sets no default gateway and is not in the neighbour table until contacted, so look for it: probe the
# preferred host number first, then the rest of the /24 around this PC's own address on the USB adapter. Only
# that /24 is probed, so nothing is sent to another network. Returns the answering addresses, preferred first.
function Find-PhoneSsh([string]$OwnIp, [int]$PreferHost, [int]$Port, [int]$TimeoutMs) {
    $net = $OwnIp.Substring(0, $OwnIp.LastIndexOf('.') + 1)
    $Prefer = ''
    if ($PreferHost -ge 1 -and $PreferHost -le 254) { $Prefer = $net + $PreferHost }
    if ($Prefer -and $Prefer -ne $OwnIp) {
        $hit = @(Test-Tcp @($Prefer) $Port 1500)
        if ($hit.Count) { return $hit }
    }
    $rest = @(1..254 | ForEach-Object { $net + $_ } | Where-Object { $_ -ne $OwnIp -and $_ -ne $Prefer })
    @(Test-Tcp $rest $Port $TimeoutMs)
}
function RndisAddress {
    $ErrorActionPreference = 'Continue'
    foreach ($n in @(Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.InterfaceDescription -match 'Remote NDIS|RNDIS' -and $_.Status -eq 'Up' })) {
        $a = Get-NetIPAddress -InterfaceIndex $n.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($a -and $a.IPAddress) { return $a.IPAddress }
    }
}
function Find-Phone {
    if ($PhoneIp) { return @(Test-Tcp @($PhoneIp) 22 3000) }
    $own = RndisAddress
    if (-not $own) { return @() }
    if ($script:probed -ne $own) { $script:probed = $own; Log "USB network adapter address ${own}: probing TCP 22 across its /24, host $PreferHost first" }
    @(Find-PhoneSsh $own $PreferHost 22 2500)
}
# stderr is merged into the text, and with no known_hosts entry ssh would start it with "Warning: Permanently
# added ... to the list of known hosts." LogLevel=ERROR keeps that out, so boot-diag.txt starts with state=...
function Invoke-Ssh([string]$ip, [string]$remote) {
    $ErrorActionPreference = 'Continue'
    $kh = (Join-Path $LogDir 'known_hosts') -replace '\\', '/'
    $o = (& ssh.exe -i $Key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no -o "UserKnownHostsFile=$kh" -o LogLevel=ERROR -o ConnectTimeout=8 "$SshUser@$ip" $remote 2>&1 | ForEach-Object { "$_" }) -join "`n"
    [pscustomobject]@{ Code = $LASTEXITCODE; Text = $o }
}
# A dotted-quad IPv4 literal (four decimal fields 0-255, no leading zeros), not a host name or a short form.
function Test-Ipv4([string]$s) {
    $o = '(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])'
    $s -match "^$o(\.$o){3}\z"
}

# Inputs, before anything else: nothing below has touched the phone yet.
if ($RootfsSha256 -notmatch '^[0-9a-fA-F]{64}$') { throw '-RootfsSha256 must be 64 hex digits' }
if ($PhoneIp -and -not (Test-Ipv4 $PhoneIp)) { throw '-PhoneIp must be an IPv4 address' }
if ($PreferHost -lt 1 -or $PreferHost -gt 254) { throw '-PreferHost must be a host number from 1 to 254' }
$ImageDir = (Resolve-Path -LiteralPath $ImageDir).Path
if (-not $LogDir) { $LogDir = Join-Path $ImageDir 'logs' }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$log = Join-Path $LogDir 'boot-chain.log'
foreach ($t in @('fastboot.exe', 'adb.exe')) { if (-not (Get-Command $t -ErrorAction SilentlyContinue)) { throw "$t is not on PATH" } }
if ($Key) {
    if (-not (Test-Path -LiteralPath $Key -PathType Leaf)) { throw "-Key $Key is not a file" }
    if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) { throw 'ssh.exe is not on PATH (needed for -Key)' }
}
$want = [ordered]@{
    'boot.img'             = 'abec1fe9ce4cbb7a9296c3abd4fd7b4acc1283842e86f2bfe0879a7e84fae552'
    'surfaceduo1-twrp.img' = '457d8d2397b83eeffb843090da17d7959bcef7ed08658591938b3cda27b8132f'
    'misc-brake.img'       = '892627cb4356d1d16c37bf243d700b5c9a0ab672d263de9ccd640c86b5cd6a5a'
}
if ($want.Contains($Rootfs)) { throw "-Rootfs must not be named $Rootfs" }
$want[$Rootfs] = $RootfsSha256.ToLower()
$p = @{}
foreach ($n in $want.Keys) {
    $p[$n] = Join-Path $ImageDir $n
    $h = (Get-FileHash -LiteralPath $p[$n] -Algorithm SHA256).Hash.ToLower()
    if ($h -ne $want[$n]) { throw "hash mismatch for $n : $h (expected $($want[$n]))" }
    Log "hash ok $n"
}
if (-not $Yes) {
    Log 'dry run (no -Yes): the files are what they should be; the phone was not touched. With -Yes the script would:'
    Log '  wait for the Duo in fastboot; check product surfaceduo, slot a, unlocked, battery 20 percent or more'
    Log '  erase and flash misc (the brake); format:ext4 userdata (ERASES it); RAM-boot TWRP; push and verify the rootfs'
    Log '  back to the bootloader; erase and flash misc again; RAM-boot the kernel ONCE; find the phone; read logs with -Key'
    exit 0
}

$phase = 'preflight (nothing was written to the phone)'
$kernelBoots = 0
try {
    Log "waiting up to $WaitMinutes min for the Duo in fastboot (Boot Select)"
    WaitFor { $s = @(FastbootSerials); if ($Serial) { $s -contains $Serial } else { $s.Count -ge 1 } } ($WaitMinutes * 60) 'the Duo in fastboot'
    if (-not $Serial) {
        $s = @(FastbootSerials)
        if ($s.Count -ne 1) { throw "$($s.Count) fastboot devices are connected; pass -Serial" }
        $Serial = $s[0]
    }
    if ((Fb getvar product) -notmatch '\bproduct:\s*surfaceduo\b') { throw 'not a Surface Duo 1' }
    if ((Fb getvar current-slot) -notmatch 'current-slot:\s*a\b') { throw 'current slot is not a' }
    if ((Fb getvar unlocked) -notmatch 'unlocked:\s*yes') { throw 'bootloader is not unlocked' }
    $bat = [regex]::Match((Fb getvar battery-level), 'battery-level:\s*(\d+)').Groups[1].Value
    if (-not $bat -or [int]$bat -lt 20) { throw "battery $bat percent: needs 20 or more" }
    Log "preflight ok: surfaceduo, slot a, unlocked, battery $bat percent"

    $phase = 'the misc brake, userdata and TWRP (userdata may be empty; boot, vbmeta, dtbo and recovery untouched)'
    Log 'arming the misc brake'
    Fb erase misc | Out-Null
    Fb flash misc $p['misc-brake.img'] | Out-Null
    Log 'formatting userdata: this erases everything Android stored'
    Fb 'format:ext4' userdata | Out-Null
    Fb boot $p['surfaceduo1-twrp.img'] | Out-Null
    WaitFor { InRecovery } 180 'TWRP over adb'
    Ab shell 'mountpoint -q /data || mount -t ext4 /dev/block/platform/soc/1d84000.ufshc/by-name/userdata /data' | Out-Null
    if ((Ab shell 'mountpoint -q /data && echo MOUNTED') -notmatch 'MOUNTED') { throw '/data did not mount in TWRP; stopping before any push' }
    Log 'pushing the rootfs (about 3 minutes for 5.7 GB)'
    $r = Native 'adb.exe' @('-s', $Serial, 'push', $p[$Rootfs], '/data/rootfs.img')
    Log ('adb push -> ' + (($r.Text -split "[\r\n]+" | Where-Object { $_ } | Select-Object -Last 1)))
    if ($r.Code -ne 0) { throw "adb push failed: $($r.Text)" }
    $sum = Ab shell 'sync; sha256sum /data/rootfs.img'
    if (($sum -split '\s+')[0] -ne $want[$Rootfs]) { throw "rootfs hash on the phone differs: $sum" }
    Log 'rootfs verified on the phone'
    Ab shell 'sync; umount /data' | Out-Null
    Ab reboot bootloader | Out-Null
    WaitFor { @(FastbootSerials) -contains $Serial } 150 'fastboot after TWRP'

    $phase = 'the one RAM-boot of the kernel (do NOT run it again for this attempt)'
    Log 're-arming the misc brake, then ONE RAM-boot of the kernel (never retried)'
    Fb erase misc | Out-Null
    Fb flash misc $p['misc-brake.img'] | Out-Null
    if ($kernelBoots -ge 1) { throw 'refusing a second RAM-boot of the kernel' }
    $kernelBoots++
    Fb boot $p['boot.img'] | Out-Null

    WaitFor { Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -match 'VID_1D6B&PID_0104' } } 120 'the Linux USB gadget'
    Log 'Linux USB gadget is up'
    $ip = $null
    $others = @()
    $probed = $null
    WaitFor { $hits = @(Find-Phone); if ($hits.Count) { $script:ip = $hits[0]; $script:others = @($hits | Select-Object -Skip 1) }; [bool]$script:ip } 480 'the phone answering on TCP 22'
    Log "phone answers on TCP 22 at $ip"
    if ($others.Count) { Log "also answering on TCP 22, not used: $($others -join ', ')" }

    if (-not $Key) {
        Log 'no -Key: skipping the diagnostics over ssh; the phone keeps a persistent journal and /var/log/opos-boot-diag.txt'
    } else {
        $remote = 'echo state=$(systemctl is-system-running); date -Is; cat /opt/opos/REVISION; echo --failed; systemctl --failed --no-legend; echo --units; systemctl is-active lxc@android android-service@hwcomposer opos-server opos phosh item; echo --dri; ls /dev/dri; echo --procs; ps -eo pid,user,comm,args | grep -E ''phoc|chromium|composer|opos'' | grep -v grep | cut -c1-220; echo --journal; journalctl -b -u opos.service -u opos-server.service -u opos-fallback.service --no-pager | tail -60'
        Remove-Item -LiteralPath (Join-Path $LogDir 'known_hosts') -ErrorAction SilentlyContinue
        Log 'ssh host keys are not pinned: the phone makes new ones with each image and this is a point-to-point cable'
        $out = $null
        for ($i = 0; $i -lt 12 -and -not $out; $i++) {
            $r = Invoke-Ssh $ip $remote
            if ($r.Code -eq 0) { $out = $r.Text } else { Log "ssh attempt $($i + 1): $($r.Text -replace '\s+', ' ')"; Start-Sleep 20 }
        }
        if ($out) { Add-Content -Path (Join-Path $LogDir 'boot-diag.txt') -Value $out; Log 'diagnostics saved to boot-diag.txt'; Write-Host $out }
        else { Log 'no ssh login with that key (an image built without the dev key?); the logs stay on the phone in the journal' }
    }
    Log 'DONE: the rootfs is on the phone and its kernel was RAM-booted once. Whether the screens lit is for whoever holds the phone to confirm; this script only reports what it printed above.'
} catch {
    Log "STOPPED at: $phase"
    Log "reason: $($_.Exception.Message)"
    Write-Host ''
    Write-Host 'Stopped on the first failure; nothing was retried. See the log:' $log
    if ($kernelBoots -ge 1) { Write-Host 'The kernel was RAM-booted once. Do not run fastboot boot again for this attempt.' }
    exit 1
}
