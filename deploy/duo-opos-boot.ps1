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
#   0. before the phone is touched, copy the verified images into a new folder under the log folder, hash the
#      copies again and keep them open (read-only, no other writer or deleter) until the script ends; every
#      fastboot and adb command gets the copies, never the originals;
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
#   - The rootfs must be a RAW ext4 image: zstd, gzip, xz and Android sparse files are refused by their magic
#     bytes, the ext4 superblock must be there and the file at least as long as it says. Checked first, in the
#     dry run too, from the first 1.4 KB and the length (the image is not loaded). Blocks larger than 4096
#     bytes are refused: the Duo's kernel has 4 KiB pages. The staged copy is checked again.
#   - Preflight on the phone: product surfaceduo, current-slot a, unlocked yes, battery 20 percent or more.
#   - The misc brake (a one-shot bootonce-bootloader flag in misc) is armed before TWRP and again before the
#     kernel, so a crash or a reboot should land in Boot Select, not in stock Android (which would wipe
#     userdata). A precaution, not a guarantee. If writing the brake fails, the script says in capitals that
#     the brake may be ABSENT, tries the brake image once more (no second erase) and stops; the phone must
#     then stay in Boot Select: do not power-cycle it or let it restart. The by-hand command it prints names the
#     verified staged brake image (path in double quotes), which is kept for that case only. The same holds if the
#     run is interrupted (Ctrl+C) after the erase and before a flash was confirmed: the warning is printed at the end.
#   - The log file (boot-chain.log) is best effort: if it cannot be written, every message still goes to the screen
#     and nothing stops because of it.
#   - The kernel is RAM-booted exactly ONCE and never retried, even if something after it fails. A kernel that
#     crashed may leave the slot rejecting RAM-boots with Device Error; stop and ask, do not try again.
#   - Only misc (erase, flash) and userdata (format) are written. Nothing is written to boot, vbmeta, dtbo or
#     recovery (the bootloader refuses it with Device Error and the port forbids it); no slot is changed.
#   - The first failure stops the script, adb commands included (a failed umount of /data in TWRP stops it
#     before the phone leaves TWRP). Nothing here reboots the phone's own kernel; the ssh step only reads.
#     Never run reboot or systemctl reboot on the RAM-booted phone: use reboot bootloader.
#   - Finding the phone on the USB network refuses to guess: if more than one RNDIS-like adapter is up it stops
#     and lists them (-PhoneIp skips the search). The ssh host key is not pinned; the fingerprint that ssh
#     accepted is printed so it can be compared with the phone's own.
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

# Best effort on the file: a log that cannot be written (disk full, folder gone, file locked) must never stop the
# chain or swallow a message, least of all the brake warning. The line always reaches the screen; the failed
# write is mentioned once, on the screen only.
function Log($m) {
    $l = '{0:HH:mm:ss} {1}' -f (Get-Date), $m
    try { Add-Content -LiteralPath $script:log -Value $l -ErrorAction Stop }
    catch {
        if (-not $script:logBroken) {
            $script:logBroken = $true
            Write-Host "(the log file could not be written: $($_.Exception.Message) -- messages continue on the screen only)"
        }
    }
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
# Like Fb: a nonzero exit code stops the chain. A command that must succeed as a whole is written as one
# checked sequence (a && b), because with a;b only the last command's status comes back.
function Ab {
    $r = Native 'adb.exe' (@('-s', $script:Serial) + $args)
    Log ("adb $($args -join ' ') -> " + (($r.Text -replace '\s+', ' ') -replace '^(.{300}).*$', '$1...'))
    if ($r.Code -ne 0) { throw "adb $($args -join ' ') failed (exit $($r.Code)): $($r.Text)" }
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

function Show-BrakeWarning {
    Log '!!! THE MISC BRAKE MAY BE ABSENT !!!'
    Log 'KEEP THE PHONE IN BOOT SELECT (fastboot). Do not power-cycle it, do not let it restart on its own, do not choose Start or Recovery Mode.'
    Log 'Without the brake an ordinary restart can reach stock Android, which wipes userdata (and the rootfs on it).'
}
function Show-BrakeByHand {
    Log "To put the brake back by hand, with the phone still in Boot Select: fastboot -s $Serial flash misc `"$($p['misc-brake.img'])`""
    Log "That file is the verified copy (SHA-256 $($want['misc-brake.img'])); it is kept for this. Do not use the one in the image folder without checking it."
}
# The misc brake: erase misc, then write the brake image. A failure here is not an ordinary stop: misc may now hold
# no brake, and an ordinary restart of the phone could reach stock Android, which would wipe the foreign userdata.
# So it is said loudly, the brake image is tried ONCE more (without another erase) and the chain stops either way.
# The catch at the end of the script shows the warning again, last, where it is not scrolled away.
# The flag goes up BEFORE the erase and comes down only after a flash that was confirmed good, so that what the catch
# below never sees (Ctrl+C, a stopped pipeline) still leaves it up: the finally at the end of the script then keeps
# the brake image and says it.
function Set-MiscBrake([string]$image) {
    $script:brakeMayBeAbsent = $true
    $why = $null
    try {
        Fb erase misc | Out-Null
        Fb flash misc $image | Out-Null
    } catch { $why = $_.Exception.Message }
    if (-not $why) { $script:brakeMayBeAbsent = $false; return }
    Log "BRAKE WRITE FAILED: $why"
    Show-BrakeWarning
    Log 'trying the brake image once more, without another erase'
    $again = $null
    try { Fb flash misc $image | Out-Null } catch { $again = $_.Exception.Message }
    if (-not $again) {
        $script:brakeMayBeAbsent = $false
        Log 'the second try succeeded, so the brake should be armed again; the script still stops here, because the first failure stops it'
        throw "the misc brake write failed ($why) and succeeded on the second try; nothing further is written"
    }
    throw "the misc brake could not be written ($why; second try: $again)"
}

# The rootfs goes to /data/rootfs.img and Halium loop-mounts it as it is, so it must be a RAW ext4 image. Reads only
# the first 1400 bytes and the length. Returns '' when it looks right, otherwise what is wrong with it.
function Get-Ext4Problem([string]$path) {
    $fs = New-Object System.IO.FileStream($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    try {
        $len = $fs.Length
        $b = New-Object byte[] 1400
        $n = 0
        while ($n -lt $b.Length) {
            $got = $fs.Read($b, $n, $b.Length - $n)
            if ($got -le 0) { break }
            $n += $got
        }
    } finally { $fs.Dispose() }
    $magics = [ordered]@{
        'zstd'           = [byte[]](0x28, 0xB5, 0x2F, 0xFD)
        'gzip'           = [byte[]](0x1F, 0x8B)
        'xz'             = [byte[]](0xFD, 0x37, 0x7A, 0x58, 0x5A, 0x00)
        'Android sparse' = [byte[]](0x3A, 0xFF, 0x26, 0xED)
    }
    foreach ($k in $magics.Keys) {
        $m = $magics[$k]
        $same = $n -ge $m.Length
        for ($i = 0; $same -and $i -lt $m.Length; $i++) { if ($b[$i] -ne $m[$i]) { $same = $false } }
        if ($same) { return "it is a $k file, not a raw ext4 image (decompress or convert it to raw ext4 first)" }
    }
    if ($n -lt 1082) { return "it is only $len bytes, too short to hold an ext4 superblock" }
    if ($b[1080] -ne 0x53 -or $b[1081] -ne 0xEF) { return 'there is no ext4 superblock magic (0xEF53) at byte 1080' }
    # Superblock at byte 1024, all little endian: blocks_count_lo at +4, log_block_size at +24 (block size = 1024 << that),
    # feature_incompat at +96 (0x80 = 64-bit block count), blocks_count_hi at +336.
    $blocks = [uint64]$b[1028] + [uint64]$b[1029] * 256 + [uint64]$b[1030] * 65536 + [uint64]$b[1031] * 16777216
    $logbs = [uint64]$b[1048] + [uint64]$b[1049] * 256 + [uint64]$b[1050] * 65536 + [uint64]$b[1051] * 16777216
    if ($logbs -gt 6) { return "the ext4 superblock has an impossible block size (log_block_size $logbs)" }
    # The Duo's kernel has 4 KiB pages and cannot mount an ext4 whose blocks are larger than a page.
    if ($logbs -gt 2) { return "the ext4 block size is larger than 4096 bytes (log_block_size $logbs), which the Duo's kernel cannot mount" }
    $hi = [uint64]$b[1360] + [uint64]$b[1361] * 256 + [uint64]$b[1362] * 65536 + [uint64]$b[1363] * 16777216
    if (($b[1120] -band 0x80) -and $hi -ne 0) { return 'the ext4 superblock describes a filesystem of 16 TiB or more (64-bit block count), far beyond this phone' }
    if ($blocks -eq 0) { return 'the ext4 superblock says the filesystem has no blocks' }
    $size = [uint64]1024
    for ($i = 0; $i -lt $logbs; $i++) { $size = $size * 2 }
    $need = $blocks * $size
    if ($len -lt $need) { return "the file is $len bytes but its ext4 superblock describes $need bytes (a truncated copy?)" }
    ''
}

# Copy one verified image into the staging folder, open the copy read-only with FileShare.Read (while that handle
# lives nobody else can write to the copy, truncate it, rename it or delete it) and hash it THROUGH the handle, so
# the bytes that were hashed are the bytes every later command reads. The caller keeps the returned handle open
# until the end of the run. fastboot.exe and adb.exe only read, and reading is shared. Left over: an administrator
# or a driver can still bypass share modes. If a fastboot ever refuses to read a file held like this, the fallback
# is to release the handle and hash the copy again right before each use; that leaves a window between the hash and
# the read, closed only for the rootfs (by the sha256sum on the phone), so it is not done unless it has to be.
function Stage-Image([string]$src, [string]$dst, [string]$sha) {
    [System.IO.File]::Copy($src, $dst)
    [System.IO.File]::SetAttributes($dst, [System.IO.FileAttributes]::Normal)   # Copy keeps a read-only flag, which would block the delete
    $fs = New-Object System.IO.FileStream($dst, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read, 1048576)
    try {
        $hasher = [System.Security.Cryptography.SHA256]::Create()
        try { $h = ([BitConverter]::ToString($hasher.ComputeHash($fs)) -replace '-', '').ToLower() } finally { $hasher.Dispose() }
        if ($h -ne $sha) { throw "the staged copy of $([System.IO.Path]::GetFileName($src)) hashes to $h (expected $sha): the file changed while it was copied" }
        $fs.Position = 0
    } catch {
        $fs.Dispose()
        throw
    }
    $fs
}
# Close the staged files and delete them and their folder. Best effort: a failure here is logged, never thrown.
function Release-Staged($handles, $files, [string]$dir) {
    foreach ($h in @($handles)) { try { $h.Dispose() } catch { Log "could not close a staged file: $($_.Exception.Message)" } }
    foreach ($f in @($files)) { try { [System.IO.File]::Delete($f) } catch { Log "could not delete ${f}: $($_.Exception.Message)" } }
    if ($dir) { try { [System.IO.Directory]::Delete($dir) } catch { Log "could not delete ${dir}: $($_.Exception.Message)" } }
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
# Every RNDIS-like adapter that is up and has an IPv4 address: its name, description and address. With -Any also
# those that have none yet.
function RndisAdapters([switch]$Any) {
    $ErrorActionPreference = 'Continue'
    @(foreach ($n in @(Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.InterfaceDescription -match 'Remote NDIS|RNDIS' -and $_.Status -eq 'Up' })) {
        $a = Get-NetIPAddress -InterfaceIndex $n.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($a -and $a.IPAddress) { [pscustomobject]@{ Name = "$($n.Name)"; Description = "$($n.InterfaceDescription)"; Address = "$($a.IPAddress)" } }
        elseif ($Any) { [pscustomobject]@{ Name = "$($n.Name)"; Description = "$($n.InterfaceDescription)"; Address = 'no IPv4 address yet' } }
    })
}
# The address of THE RNDIS-like adapter, or nothing while there is none. With more than one it refuses: another
# tethered or RNDIS device could be the one that answers, and its ssh server would be taken for the phone's.
function RndisAddress {
    $all = @(RndisAdapters)
    if ($all.Count -gt 1) {
        $list = ($all | ForEach-Object { "$($_.Name) ($($_.Description)) $($_.Address)" }) -join '; '
        throw "more than one RNDIS-like USB network adapter is up, so the Duo cannot be told from another device: $list. Unplug the other device or pass -PhoneIp with the Duo's address."
    }
    if ($all.Count) { $all[0].Address }
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
# The known_hosts file is named WITHOUT a folder: ssh runs in the log folder for the call (PowerShell starts a native
# tool in its current location), so the file lands there. A path in the option value would break on a space: ssh reads
# the value of UserKnownHostsFile as a list of files and splits it at blanks (a log folder under "C:\Duo Images" would
# make it write to C:\Duo), and Windows PowerShell 5.1 and PowerShell 7 quote an option value with quotes in it
# differently. -Key is made absolute at the start for the same reason: the working directory changes under it.
function Invoke-Ssh([string]$ip, [string]$remote) {
    $ErrorActionPreference = 'Continue'
    Push-Location -LiteralPath $LogDir -ErrorAction Stop
    try {
        $o = (& ssh.exe -i $Key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=known_hosts -o LogLevel=ERROR -o ConnectTimeout=8 "$SshUser@$ip" $remote 2>&1 | ForEach-Object { "$_" }) -join "`n"
        $code = $LASTEXITCODE
    } finally { Pop-Location }
    [pscustomobject]@{ Code = $code; Text = $o }
}
# StrictHostKeyChecking=no accepts whatever host key answers, and the phone makes new keys with each image, so the
# key cannot be pinned in advance. What can be done is to show the fingerprint ssh took (it wrote the key to the
# known_hosts file in the log folder, see Invoke-Ssh), so the owner can compare it with the phone's own.
function Show-HostKey {
    $ErrorActionPreference = 'Continue'
    $kh = Join-Path $LogDir 'known_hosts'
    if (-not (Test-Path -LiteralPath $kh)) { Log "ssh left no host key in $kh (it never got as far as the key exchange)"; return }
    if (-not (Get-Command ssh-keygen.exe -ErrorAction SilentlyContinue)) { Log "ssh-keygen.exe is not on PATH, so the host key fingerprint cannot be printed; the key ssh accepted is in $kh"; return }
    $fps = @(& ssh-keygen.exe -l -f $kh 2>&1 | ForEach-Object { "$_" } | Where-Object { $_ -match 'SHA256:' })
    if (-not $fps.Count) { Log "ssh-keygen could not read a fingerprint from $kh"; return }
    foreach ($f in $fps) { Log "ssh accepted this host key without checking it first: $f" }
    Log 'Compare it with the phone (on the phone: ssh-keygen -l -f /etc/ssh/ssh_host_ed25519_key.pub). If they differ, whatever answered was not the Duo: do not trust boot-diag.txt.'
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
$LogDir = (Resolve-Path -LiteralPath $LogDir).Path
$log = Join-Path $LogDir 'boot-chain.log'
foreach ($t in @('fastboot.exe', 'adb.exe')) { if (-not (Get-Command $t -ErrorAction SilentlyContinue)) { throw "$t is not on PATH" } }
if ($Key) {
    if (-not (Test-Path -LiteralPath $Key -PathType Leaf)) { throw "-Key $Key is not a file" }
    if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) { throw 'ssh.exe is not on PATH (needed for -Key)' }
    $Key = (Resolve-Path -LiteralPath $Key).ProviderPath   # absolute: ssh runs in the log folder (see Invoke-Ssh)
}
$want = [ordered]@{
    'boot.img'             = 'abec1fe9ce4cbb7a9296c3abd4fd7b4acc1283842e86f2bfe0879a7e84fae552'
    'surfaceduo1-twrp.img' = '457d8d2397b83eeffb843090da17d7959bcef7ed08658591938b3cda27b8132f'
    'misc-brake.img'       = '892627cb4356d1d16c37bf243d700b5c9a0ab672d263de9ccd640c86b5cd6a5a'
}
if ($want.Contains($Rootfs)) { throw "-Rootfs must not be named $Rootfs" }
$want[$Rootfs] = $RootfsSha256.ToLower()
$p = @{}
foreach ($n in $want.Keys) { $p[$n] = Join-Path $ImageDir $n }
# The rootfs is looked at first: a .zst or a sparse image with its correct hash would pass every later check.
if (-not (Test-Path -LiteralPath $p[$Rootfs] -PathType Leaf)) { throw "-Rootfs $Rootfs is not a file in $ImageDir" }
$bad = Get-Ext4Problem $p[$Rootfs]
if ($bad) { throw "-Rootfs $Rootfs is not a raw ext4 image: $bad" }
Log "rootfs $Rootfs looks like a raw ext4 image"
foreach ($n in $want.Keys) {
    $h = (Get-FileHash -LiteralPath $p[$n] -Algorithm SHA256).Hash.ToLower()
    if ($h -ne $want[$n]) { throw "hash mismatch for $n : $h (expected $($want[$n]))" }
    Log "hash ok $n"
}
if (-not $Yes) {
    Log 'dry run (no -Yes): the files are what they should be; the phone was not touched. With -Yes the script would:'
    Log '  copy the four images into a new folder under the log folder, hash the copies again and hold them open'
    Log '  wait for the Duo in fastboot; check product surfaceduo, slot a, unlocked, battery 20 percent or more'
    Log '  erase and flash misc (the brake); format:ext4 userdata (ERASES it); RAM-boot TWRP; push and verify the rootfs'
    Log '  back to the bootloader; erase and flash misc again; RAM-boot the kernel ONCE; find the phone; read logs with -Key'
    exit 0
}

# From here every fastboot and adb command gets a staged copy (see Stage-Image), never the file in $ImageDir. The
# copies are made before the wait for the phone, which can last hours: whatever happens to the originals then no
# longer matters. $orig keeps the original paths for messages only.
$orig = $p.Clone()
$stage = $null
$staged = New-Object System.Collections.ArrayList
$held = New-Object System.Collections.ArrayList
$script:brakeMayBeAbsent = $false
$failed = $false
$phase = 'staging the verified images (nothing was written to the phone)'
$kernelBoots = 0
try {
    $stage = Join-Path $LogDir ('stage-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Path $stage | Out-Null
    foreach ($n in @($want.Keys)) {
        $dst = Join-Path $stage $(if ($n -eq $Rootfs) { 'rootfs.img' } else { $n })
        [void]$staged.Add($dst)
        [void]$held.Add((Stage-Image $orig[$n] $dst $want[$n]))
        $p[$n] = $dst
        Log "staged $n and hashed the copy again: ok"
        if ($n -eq $Rootfs) {
            # The check above looked at the original; this one looks at the bytes that will be pushed.
            $bad = Get-Ext4Problem $dst
            if ($bad) { throw "the staged copy of -Rootfs $Rootfs is not a raw ext4 image: $bad" }
        }
    }
    $phase = 'preflight (nothing was written to the phone)'
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
    # The phone is in Boot Select, so it has no USB network adapter of its own yet: one that is up now belongs to
    # another device, and the search for the phone after the kernel boot could not tell them apart. Refuse now,
    # before anything is written and the one RAM-boot is spent. (-PhoneIp skips the search.)
    if (-not $PhoneIp) {
        $other = @(RndisAdapters -Any)
        if ($other.Count) {
            $names = ($other | ForEach-Object { '{0} ({1}) {2}' -f $_.Name, $_.Description, $_.Address }) -join '; '
            throw "an RNDIS-like USB network adapter is already up although the Duo has not booted: $names. The phone could not be told from that device. Unplug it or pass -PhoneIp with the Duo's address."
        }
    }

    $phase = 'the misc brake, userdata and TWRP (userdata may be empty; boot, vbmeta, dtbo and recovery untouched)'
    Log 'arming the misc brake'
    Set-MiscBrake $p['misc-brake.img']
    Log 'formatting userdata: this erases everything Android stored'
    Fb 'format:ext4' userdata | Out-Null
    Fb boot $p['surfaceduo1-twrp.img'] | Out-Null
    WaitFor { InRecovery } 180 'TWRP over adb'
    Ab shell 'mountpoint -q /data || mount -t ext4 /dev/block/platform/soc/1d84000.ufshc/by-name/userdata /data' | Out-Null
    if ((Ab shell 'mountpoint -q /data && echo MOUNTED || echo UNMOUNTED') -notmatch '\bMOUNTED\b') { throw '/data did not mount in TWRP; stopping before any push' }
    Log 'pushing the rootfs (about 3 minutes for 5.7 GB)'
    $r = Native 'adb.exe' @('-s', $Serial, 'push', $p[$Rootfs], '/data/rootfs.img')
    Log ('adb push -> ' + (($r.Text -split "[\r\n]+" | Where-Object { $_ } | Select-Object -Last 1)))
    if ($r.Code -ne 0) { throw "adb push failed: $($r.Text)" }
    $sum = Ab shell 'sync && sha256sum /data/rootfs.img'
    if (($sum -split '\s+')[0] -ne $want[$Rootfs]) { throw "rootfs hash on the phone differs: $sum" }
    Log 'rootfs verified on the phone'
    Ab shell 'sync && umount /data' | Out-Null
    Ab reboot bootloader | Out-Null
    WaitFor { @(FastbootSerials) -contains $Serial } 150 'fastboot after TWRP'

    $phase = 'the one RAM-boot of the kernel (do NOT run it again for this attempt)'
    Log 're-arming the misc brake, then ONE RAM-boot of the kernel (never retried)'
    Set-MiscBrake $p['misc-brake.img']
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
        Show-HostKey
    }
    Log 'DONE: the rootfs is on the phone and its kernel was RAM-booted once. Whether the screens lit is for whoever holds the phone to confirm; this script only reports what it printed above.'
} catch {
    $failed = $true
    Log "STOPPED at: $phase"
    Log "reason: $($_.Exception.Message)"
    Write-Host ''
    Write-Host 'Stopped on the first failure; nothing was retried. See the log:' $log
    if ($script:brakeMayBeAbsent) {
        Show-BrakeWarning
        Show-BrakeByHand
    }
    if ($kernelBoots -ge 1) { Write-Host 'The kernel was RAM-booted once. Do not run fastboot boot again for this attempt.' }
} finally {
    if ($script:brakeMayBeAbsent) {
        # The catch above says it when a failure got there. Ctrl+C or a stopped pipeline skip the catch and land here with
        # the flag up: say it here, and still release the staged files whatever the saying does.
        if (-not $failed) { try { Show-BrakeWarning; Show-BrakeByHand } catch { } }
        # Everything is closed and removed except the verified brake image (and so its folder), named in the by-hand command above.
        Release-Staged $held @($staged | Where-Object { $_ -ne $p['misc-brake.img'] }) $null
    } else {
        Release-Staged $held $staged $stage
    }
}
if ($failed) { exit 1 }
