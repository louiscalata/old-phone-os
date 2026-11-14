# SPDX-License-Identifier: GPL-3.0-or-later
# Push an OPOS update bundle (tools/opos-release.py build) into the Duo OS image's update inbox over the USB network,
# then follow the phone's updater until it settles. The phone's own updater checks the signature and applies the
# update; this script only delivers the file and reads status.json. Nothing runs on the phone except cat and mv
# (plus the scp transfer itself), and the copy lands under a .partial name that the inbox's path unit ignores until
# the final mv, so a half-written file is never picked up.
#
#   powershell -File deploy\duo-push-update.ps1 -Bundle dist\opos-8.opos-update -Host <phone address> -Key <ssh login key>
#
# -Key is the key that logs in to the phone's account (the image's development ssh key), NOT the release signing key.
# -Host is the phone's address on the USB network (an IPv6 link-local address with its zone, such as fe80::1%8, works).
# Exit codes: 0 done, 1 rolled-back or failed, 2 timed out waiting, 3 could not deliver (bad argument, no route, no inbox).
#
# Which status is this bundle's? Only one that names it. The updater writes the bundle's serial (the one in its manifest, read
# here from the bundle) into every status after it has read the manifest; a status for another serial is somebody else's
# update (one that was already running when this one was handed over, say) and is never taken for this one, whatever phase it
# is in. Two statuses carry no serial: "verifying", which names the inbox file ("checking opos-8.opos-update"), and a refusal
# before the manifest was read (a bad signature). The refusal counts as this bundle's only if the updater was idle when the
# bundle was handed over or has since been seen verifying this file; otherwise the script keeps waiting and says at the end what
# it saw. A request identifier in every status (the inbox name or the bundle's sha256) would make that last case exact.
param(
    [Parameter(Mandatory = $true)][string]$Bundle,
    [Parameter(Mandatory = $true)][Alias("Host")][string]$PhoneHost,
    [Parameter(Mandatory = $true)][string]$Key,
    [string]$User = "droidian",
    [int]$TimeoutS = 300,
    [ValidateRange(1, 60)][int]$PollS = 2
)
$ErrorActionPreference = "Stop"
$inbox = "/var/lib/opos-update/inbox"
$statusFile = "/var/lib/opos-update/status.json"
$settled = @("done", "rolled-back", "failed")
$working = @("verifying", "applying", "checking")

function Fail([string]$msg, [int]$code) { [Console]::Error.WriteLine("duo-push-update: $msg"); exit $code }

if ($PhoneHost -notmatch '^[A-Za-z0-9][A-Za-z0-9._:%\[\]-]{0,254}$') { Fail "-Host has characters that an address does not" 3 }
if ($User -notmatch '^[a-z_][a-z0-9_-]{0,31}$') { Fail "-User is not a plain account name" 3 }
if (-not (Test-Path -LiteralPath $Bundle -PathType Leaf)) { Fail "-Bundle: no such file" 3 }
if (-not (Test-Path -LiteralPath $Key -PathType Leaf)) { Fail "-Key: no such file" 3 }
$file = Get-Item -LiteralPath $Bundle
if ($file.Name -notmatch '^opos-[0-9]{1,10}\.opos-update$') { Fail "-Bundle must be named opos-<serial>.opos-update, as tools/opos-release.py names it" 3 }
if ($file.Length -lt 1 -or $file.Length -gt 268435456) { Fail "-Bundle is empty or larger than the updater's 256 MiB limit" 3 }

function Read-Exactly($stream, [byte[]]$buffer, [int]$count) {
    $got = 0
    while ($got -lt $count) {
        $n = $stream.Read($buffer, $got, $count - $got)
        if ($n -le 0) { return $false }
        $got += $n
    }
    return $true
}

# The serial in the bundle's manifest, which is the serial the updater puts in status.json. The manifest is the first member of
# the plain tar: a 512-byte header (name at offset 0, size in octal at offset 124), then the member's bytes. $null if it is not.
function Get-BundleSerial([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    try {
        $header = New-Object byte[] 512
        if (-not (Read-Exactly $stream $header 512)) { return $null }
        if ([Text.Encoding]::ASCII.GetString($header, 0, 100).Split([char]0)[0] -ne "manifest.json") { return $null }
        $sizeText = [Text.Encoding]::ASCII.GetString($header, 124, 12).Trim([char]0, " ")
        if ($sizeText -notmatch '^[0-7]{1,11}$') { return $null }
        $size = [Convert]::ToInt64($sizeText, 8)
        if ($size -lt 2 -or $size -gt 8388608) { return $null }
        $body = New-Object byte[] ([int]$size)
        if (-not (Read-Exactly $stream $body ([int]$size))) { return $null }
        $m = [regex]::Match([Text.Encoding]::UTF8.GetString($body), '(?<!\\)"serial"\s*:\s*([0-9]{1,10})\s*[,}]')
        if (-not $m.Success) { return $null }
        return [int64]$m.Groups[1].Value
    } finally {
        $stream.Dispose()
    }
}

$serial = Get-BundleSerial $file.FullName
if ($null -eq $serial) { Fail "-Bundle: its first tar member is not a manifest.json with a serial, so this is not a bundle from tools/opos-release.py" 3 }
$nameSerial = [int64]($file.Name -replace '^opos-([0-9]+)\.opos-update$', '$1')
if ($serial -ne $nameSerial) { Fail "-Bundle is named for serial $nameSerial but its manifest says serial $serial" 3 }

$bare = $PhoneHost.Trim("[", "]")
$scpHost = if ($bare.Contains(":")) { "[$bare]" } else { $bare }
$partial = "$inbox/$($file.Name).partial"
$ready = "$inbox/$($file.Name)"
$common = @("-i", $Key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10")

# The only way this script runs anything on the phone. Callers pass cat or mv, nothing else.
function Invoke-Phone([string]$RemoteCommand) {
    $errFile = [IO.Path]::GetTempFileName()
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & ssh @common "${User}@${bare}" $RemoteCommand 2> $errFile
        $code = $LASTEXITCODE
        $err = Get-Content -LiteralPath $errFile -Raw
    } finally {
        $ErrorActionPreference = $saved
        Remove-Item -LiteralPath $errFile -ErrorAction SilentlyContinue
    }
    [pscustomobject]@{ Code = $code; Out = (($out | ForEach-Object { "$_" }) -join "`n"); Err = "$err" }
}

# status.json as the phone has it now: its raw text and the parsed object, or $null when it cannot be read.
function Read-Status() {
    $r = Invoke-Phone "cat $statusFile"
    if ($r.Code -ne 0 -or -not $r.Out.Trim()) { return $null }
    $raw = $r.Out.Trim()
    try { $st = $raw | ConvertFrom-Json } catch { return $null }
    if ($null -eq $st) { return $null }
    [pscustomobject]@{ Raw = $raw; Status = $st }
}

try {
    $first = Invoke-Phone "cat $statusFile"
    if ($first.Code -eq 255) { Fail "cannot reach ${User}@${bare} over ssh: $($first.Err.Trim())" 3 }

    "copying $($file.Name) ($($file.Length) bytes) to the update inbox"
    $scpArgs = @("-q") + $common + @($file.FullName, "${User}@${scpHost}:$partial")
    & scp @scpArgs
    if ($LASTEXITCODE -ne 0) { Fail "scp failed (exit $LASTEXITCODE): is this an OPOS OS image with the updater, and is the inbox writable by ${User}?" 3 }

    # The status as it is just before the bundle is handed over (the copy can take a while): what is already there is not this
    # update's, and whether the updater was busy then decides how a refusal with no serial is read below.
    $baseline = Read-Status
    $baseRaw = if ($null -ne $baseline) { $baseline.Raw } else { "" }
    $basePhase = if ($null -ne $baseline) { "$($baseline.Status.phase)" } else { "" }
    $moved = Invoke-Phone "mv $partial $ready"
    if ($moved.Code -ne 0) { Fail "could not rename the copy into the inbox: $($moved.Err.Trim())" 3 }
    "queued as serial $serial; waiting up to $TimeoutS s for the updater"

    # $mine: the updater is known to be on this bundle (it was seen verifying this file) or was idle at the hand-over, and has not
    # been seen on anything else since. Only then is a refusal that has no serial taken for this bundle's.
    $mine = $working -notcontains $basePhase
    $deadline = (Get-Date).AddSeconds($TimeoutS)
    $last = ""
    $lastOther = ""
    $seen = ""
    $final = $null
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds $PollS
        $cur = Read-Status
        if ($null -eq $cur -or $cur.Raw -eq $baseRaw -or $cur.Raw -eq $seen) { continue }
        $seen = $cur.Raw
        $st = $cur.Status
        $phase = "$($st.phase)"
        $hasSerial = $null -ne $st.serial -and "$($st.serial)" -ne ""
        if ($hasSerial) {
            $ours = "$($st.serial)" -eq "$serial"
            $mine = $ours
        } elseif ($phase -eq "verifying") {
            $ours = "$($st.message)" -ceq "checking $($file.Name)"
            $mine = $ours
        } else {
            $ours = $mine -and $phase -eq "failed"
        }
        $line = "{0:HH:mm:ss} {1}: {2}" -f (Get-Date), $phase, $st.message
        if ($ours) {
            $line
            $last = $line
        } else {
            "{0:HH:mm:ss} (not this update, serial {1}) {2}: {3}" -f (Get-Date), $st.serial, $phase, $st.message
            $lastOther = $line
        }
        if ($ours -and $settled -contains $phase) { $final = $st; break }
    }
    if ($null -eq $final) {
        # Read-only question, asked only now: is the updater's trigger running? ("failed" is what a tripped start limit used to leave.)
        $units = Invoke-Phone "systemctl is-active opos-update.path opos-update.service"
        $word = @($units.Out -split "\s+" | Where-Object { $_ })
        $pathState = if ($word.Count -ge 1) { $word[0] } else { "unknown" }
        $hint = "the updater may still be working, read status.json again"
        if ($pathState -ne "active" -and $pathState -ne "unknown") {
            $hint = "the update path unit is $pathState, so nothing started the updater and the bundle waits in the inbox; keep the phone on; starting the updater again needs root on the phone, which only a development image gives the login user; there, over ssh from the PC, run as root on the phone: systemctl reset-failed opos-update.path opos-update.service; systemctl restart opos-update.path"
        }
        $others = if ($lastOther) { "; the updater's last word, which does not name serial $serial, was: $lastOther" } else { "" }
        Fail "no settled status within $TimeoutS s for serial $serial (last: $last)$others; update units (path, service): $($word -join ', '); $hint" 2
    }
    "result: phase=$($final.phase) serial=$($final.serial) message=$($final.message)"
    if ($final.phase -eq "done") { exit 0 }
    exit 1
} catch {
    Fail $_.Exception.Message 3
}
