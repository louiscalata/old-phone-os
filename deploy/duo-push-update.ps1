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

function Fail([string]$msg, [int]$code) { [Console]::Error.WriteLine("duo-push-update: $msg"); exit $code }

if ($PhoneHost -notmatch '^[A-Za-z0-9][A-Za-z0-9._:%\[\]-]{0,254}$') { Fail "-Host has characters that an address does not" 3 }
if ($User -notmatch '^[a-z_][a-z0-9_-]{0,31}$') { Fail "-User is not a plain account name" 3 }
if (-not (Test-Path -LiteralPath $Bundle -PathType Leaf)) { Fail "-Bundle: no such file" 3 }
if (-not (Test-Path -LiteralPath $Key -PathType Leaf)) { Fail "-Key: no such file" 3 }
$file = Get-Item -LiteralPath $Bundle
if ($file.Name -notmatch '^opos-[0-9]{1,9}\.opos-update$') { Fail "-Bundle must be named opos-<serial>.opos-update, as tools/opos-release.py names it" 3 }
if ($file.Length -lt 1 -or $file.Length -gt 268435456) { Fail "-Bundle is empty or larger than the updater's 256 MiB limit" 3 }

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

try {
    $before = Invoke-Phone "cat $statusFile"
    if ($before.Code -eq 255) { Fail "cannot reach ${User}@${bare} over ssh: $($before.Err.Trim())" 3 }
    $beforeRaw = if ($before.Code -eq 0) { $before.Out.Trim() } else { "" }

    "copying $($file.Name) ($($file.Length) bytes) to the update inbox"
    $scpArgs = @("-q") + $common + @($file.FullName, "${User}@${scpHost}:$partial")
    & scp @scpArgs
    if ($LASTEXITCODE -ne 0) { Fail "scp failed (exit $LASTEXITCODE): is this an OPOS OS image with the updater, and is the inbox writable by ${User}?" 3 }
    $moved = Invoke-Phone "mv $partial $ready"
    if ($moved.Code -ne 0) { Fail "could not rename the copy into the inbox: $($moved.Err.Trim())" 3 }
    "queued; waiting up to $TimeoutS s for the updater"

    $deadline = (Get-Date).AddSeconds($TimeoutS)
    $last = ""
    $final = $null
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds $PollS
        $r = Invoke-Phone "cat $statusFile"
        if ($r.Code -ne 0 -or -not $r.Out.Trim()) { continue }
        $raw = $r.Out.Trim()
        try { $st = $raw | ConvertFrom-Json } catch { continue }
        if ($raw -ne $last -and $raw -ne $beforeRaw) { "{0:HH:mm:ss} {1}: {2}" -f (Get-Date), $st.phase, $st.message; $last = $raw }
        # A settled status that is byte-for-byte the one from before the push belongs to an earlier update.
        if ($raw -ne $beforeRaw -and $settled -contains $st.phase) { $final = $st; break }
    }
    if ($null -eq $final) {
        # Read-only question, asked only now: is the updater's trigger running? ("failed" is what a tripped start limit used to leave.)
        $units = Invoke-Phone "systemctl is-active opos-update.path opos-update.service"
        $word = @($units.Out -split "\s+" | Where-Object { $_ })
        $pathState = if ($word.Count -ge 1) { $word[0] } else { "unknown" }
        $hint = "the updater may still be working, read status.json again"
        if ($pathState -ne "active" -and $pathState -ne "unknown") {
            $hint = "the update path unit is $pathState, so nothing started the updater and the bundle waits in the inbox; restart the phone, or as root: systemctl reset-failed opos-update.path opos-update.service; systemctl restart opos-update.path"
        }
        Fail "no settled status within $TimeoutS s (last: $last); update units (path, service): $($word -join ', '); $hint" 2
    }
    "result: phase=$($final.phase) serial=$($final.serial) message=$($final.message)"
    if ($final.phase -eq "done") { exit 0 }
    exit 1
} catch {
    Fail $_.Exception.Message 3
}
