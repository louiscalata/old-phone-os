# SPDX-License-Identifier: GPL-3.0-or-later
#Requires -Version 7
# Acceptance run of the OPOS OS on a Surface Duo, from the PC. It does not change the phone: over ssh it runs the
# image's read-only /usr/sbin/opos-selftest, prints two files (/opt/opos/REVISION and, if present,
# /var/log/opos-boot-diag.txt), and, when the dev image's Chromium has a DevTools port, forwards that one loopback port
# through ssh and saves what the page really renders as a PNG (Chrome DevTools protocol, Page.captureScreenshot). The
# only phone-side traces are the ssh logins in its logs. Everything lands in an evidence folder on this PC.
#
#   pwsh -File deploy\duo-acceptance.ps1 -Host <phone address> -Key <private key file> [-OutDir <folder>]
#   other options: -User, -Port, -DebugPort, -KnownHosts, -SshPath (an ssh that is not on PATH), -NoScreenshot, -RequireScreenshot, -TimeoutS
#
# Evidence folder <OutDir>\duo-acceptance-<UTC time>\: report.md (checks, times, image revision, SHA-256 of every
# artifact), SHA256SUMS, selftest.txt, revision.txt, opos-boot-diag.txt, page.png and viewport.json when taken.
# Exit status: 0 no check failed, 1 at least one FAIL, 2 the phone could not be asked (ssh or opos-selftest missing).
# A WARN or SKIP never fails the run; the eyes-on-the-glass checks are docs/duo-acceptance.md, part 2.
# -Host is an alias of -PhoneHost: $Host is a constant in PowerShell, so a parameter cannot be named that.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][Alias('Host')][ValidateNotNullOrEmpty()][string]$PhoneHost,
    [string]$Key = '',
    [string]$OutDir = (Join-Path (Get-Location).Path 'duo-acceptance'),
    [string]$User = 'droidian',
    [int]$Port = 22,
    [int]$DebugPort = 9222,
    [string]$KnownHosts = '',
    [string]$SshPath = '',
    [switch]$NoScreenshot,
    [switch]$RequireScreenshot,
    [int]$TimeoutS = 240
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Every command this script ever runs on the phone. Each is a read.
$Remote = [ordered]@{
    Selftest = '/usr/sbin/opos-selftest'
    Revision = 'cat /opt/opos/REVISION'
    DiagTest = 'test -r /var/log/opos-boot-diag.txt'
    Diag     = 'cat /var/log/opos-boot-diag.txt'
}

if ($PhoneHost -notmatch '^[A-Za-z0-9][A-Za-z0-9._:%-]*$') { throw "-Host '$PhoneHost' is not a plain host name or address" }
if ($User -notmatch '^[a-z_][a-z0-9_-]*$') { throw "-User '$User' is not a plain user name" }
if ($Key -and -not (Test-Path -LiteralPath $Key -PathType Leaf)) { throw "-Key '$Key' is not a file" }

if ($SshPath) {
    if (-not (Test-Path -LiteralPath $SshPath -PathType Leaf)) { throw "-SshPath '$SshPath' is not a file" }
    $Ssh = (Resolve-Path -LiteralPath $SshPath).Path
} else {
    $Ssh = (Get-Command ssh -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
}
if (-not $KnownHosts) { $KnownHosts = Join-Path $OutDir 'known_hosts' }
$Started = (Get-Date).ToUniversalTime()
$Run = Join-Path $OutDir ('duo-acceptance-' + $Started.ToString('yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $Run -Force | Out-Null
$SshBase = @('-T', '-n', '-p', "$Port",
    '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
    '-o', 'StrictHostKeyChecking=accept-new', '-o', ('UserKnownHostsFile="' + $KnownHosts.Replace('\', '/') + '"'), '-o', 'LogLevel=ERROR',
    '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3')
if ($Key) { $SshBase += @('-i', $Key, '-o', 'IdentitiesOnly=yes') }
$Rows = [System.Collections.Generic.List[object]]::new()
$Info = [ordered]@{ PhoneTime = '?'; Uptime = '?'; SelftestExit = '?'; Revision = '?' }

function Add-Row([string]$Status, [string]$Id, [string]$Evidence, [string]$Source = 'pc') {
    # Text from the phone or from ssh is data: one line, no control characters, no markup in the report table.
    $Evidence = (($Evidence -replace '[\x00-\x1f\x7f]+', ' ') -replace '\s+', ' ').Trim().Replace('<', '&lt;')
    $Rows.Add([pscustomobject]@{ Status = $Status; Id = $Id; Evidence = $Evidence; Source = $Source })
}

function Invoke-Native([string]$File, [string[]]$ArgList, [int]$Seconds = 60) {
    $psi = [System.Diagnostics.ProcessStartInfo]::new($File)
    foreach ($a in $ArgList) { [void]$psi.ArgumentList.Add($a) }
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $p.StandardInput.Close()
    $ms = [System.IO.MemoryStream]::new()
    $copy = $p.StandardOutput.BaseStream.CopyToAsync($ms)
    $err = $p.StandardError.ReadToEndAsync()
    $done = $p.WaitForExit($Seconds * 1000)
    if (-not $done) {
        try { $p.Kill($true) } catch { }
        [void]$p.WaitForExit(5000)
    }
    try { [void]$copy.Wait(5000) } catch { }
    $text = ''
    if ($err.Wait(5000)) { $text = $err.Result }
    $code = 124
    if ($done) { $code = $p.ExitCode }
    [pscustomobject]@{ ExitCode = $code; Out = $ms.ToArray(); Err = $text.Trim() }
}

function Invoke-Ssh([string]$Command, [int]$Seconds = 60) {
    Invoke-Native $Ssh ($SshBase + @("$User@$PhoneHost", $Command)) $Seconds
}

function ConvertTo-Text([byte[]]$Bytes) { [System.Text.Encoding]::UTF8.GetString($Bytes) }

function Save-Bytes([string]$Name, [byte[]]$Bytes) {
    [System.IO.File]::WriteAllBytes((Join-Path $Run $Name), $Bytes)
}

function Get-FreePort {
    $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $l.Start()
    $n = $l.LocalEndpoint.Port
    $l.Stop()
    $n
}

function Invoke-Cdp($Ws, [int]$Id, [string]$Method, $Params, [int]$Seconds = 30) {
    $cts = [System.Threading.CancellationTokenSource]::new($Seconds * 1000)
    $msg = [ordered]@{ id = $Id; method = $Method; params = $Params } | ConvertTo-Json -Depth 6 -Compress
    $out = [System.ArraySegment[byte]]::new([System.Text.Encoding]::UTF8.GetBytes($msg))
    $null = $Ws.SendAsync($out, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $cts.Token).GetAwaiter().GetResult()
    $buf = [byte[]]::new(65536)
    while ($true) {
        $ms = [System.IO.MemoryStream]::new()
        do {
            $r = $Ws.ReceiveAsync([System.ArraySegment[byte]]::new($buf), $cts.Token).GetAwaiter().GetResult()
            if ($r.MessageType -eq [System.Net.WebSockets.WebSocketMessageType]::Close) { throw 'the page closed the DevTools connection' }
            $ms.Write($buf, 0, $r.Count)
        } while (-not $r.EndOfMessage)
        $obj = (ConvertTo-Text $ms.ToArray()) | ConvertFrom-Json -Depth 20
        if ($obj.PSObject.Properties['id'] -and $obj.id -eq $Id) {
            if ($obj.PSObject.Properties['error']) { throw ('DevTools ' + $Method + ': ' + $obj.error.message) }
            return $obj
        }
    }
}

function Get-PageShot {
    $lp = Get-FreePort
    $psi = [System.Diagnostics.ProcessStartInfo]::new($Ssh)
    $fwd = $SshBase + @('-N', '-o', 'ExitOnForwardFailure=yes', '-L', "127.0.0.1:${lp}:127.0.0.1:${DebugPort}", "$User@$PhoneHost")
    foreach ($a in $fwd) { [void]$psi.ArgumentList.Add($a) }
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardError = $true
    $tun = [System.Diagnostics.Process]::Start($psi)
    $ws = $null
    try {
        $tun.StandardInput.Close()
        $up = $false
        for ($i = 0; $i -lt 60 -and -not $up -and -not $tun.HasExited; $i++) {
            $c = [System.Net.Sockets.TcpClient]::new()
            try { $c.Connect('127.0.0.1', $lp); $up = $true } catch { Start-Sleep -Milliseconds 250 } finally { $c.Dispose() }
        }
        if (-not $up) { return @('SKIP', "the ssh tunnel to the phone's DevTools port did not come up") }
        try {
            $list = Invoke-RestMethod -Uri "http://127.0.0.1:${lp}/json/list" -TimeoutSec 10
        } catch {
            return @('SKIP', "no DevTools answer on the phone's 127.0.0.1:${DebugPort}: not a dev image, or Chromium runs without OPOS_KIOSK_DEBUG_PORT")
        }
        $pages = @($list | Where-Object { $_.type -eq 'page' })
        $page = $pages | Where-Object { $_.url -like 'http://127.0.0.1:*' } | Select-Object -First 1
        if (-not $page) { $page = $pages | Select-Object -First 1 }
        if (-not $page) { return @('FAIL', 'DevTools answered but lists no page') }
        $ws = [System.Net.WebSockets.ClientWebSocket]::new()
        $cts = [System.Threading.CancellationTokenSource]::new(15000)
        $null = $ws.ConnectAsync([System.Uri]::new("ws://127.0.0.1:${lp}/devtools/page/" + $page.id), $cts.Token).GetAwaiter().GetResult()
        $shot = Invoke-Cdp $ws 1 'Page.captureScreenshot' @{ format = 'png' } 45
        $png = [Convert]::FromBase64String($shot.result.data)
        $sig = [BitConverter]::ToString($png, 0, 8).Replace('-', '')
        if ($sig -ne '89504E470D0A1A0A') { return @('FAIL', 'DevTools returned data that is not a PNG') }
        Save-Bytes 'page.png' $png
        $w = ($png[16] * 16777216) + ($png[17] * 65536) + ($png[18] * 256) + $png[19]
        $h = ($png[20] * 16777216) + ($png[21] * 65536) + ($png[22] * 256) + $png[23]
        $js = 'JSON.stringify({w: innerWidth, h: innerHeight, dpr: devicePixelRatio, url: location.href})'
        $vp = Invoke-Cdp $ws 2 'Runtime.evaluate' @{ expression = $js; returnByValue = $true } 15
        $view = $vp.result.result.value | ConvertFrom-Json
        Save-Bytes 'viewport.json' ([System.Text.Encoding]::UTF8.GetBytes($vp.result.result.value))
        if ($view.w -ne 1392 -or $view.h -ne 900 -or $view.dpr -ne 2) {
            Add-Row 'WARN' 'pc.viewport' "the page sees $($view.w)x$($view.h) CSS px at devicePixelRatio $($view.dpr); the port's files give 1392x900 at 2 (2784x1800 px, scale 2)"
        } else {
            Add-Row 'PASS' 'pc.viewport' "the page sees $($view.w)x$($view.h) CSS px at devicePixelRatio $($view.dpr)"
        }
        return @('PASS', "page.png, ${w}x${h} px, from Chromium's own rendering of $($page.url) (not a photo of the panels)")
    } catch {
        return @('FAIL', "DevTools screenshot failed: $($_.Exception.Message)")
    } finally {
        if ($ws) { $ws.Dispose() }
        try { if (-not $tun.HasExited) { $tun.Kill($true) } } catch { }
        $tun.Dispose()
    }
}

# --- 1. the self-test on the phone
$rc = 2
$sel = Invoke-Ssh $Remote.Selftest $TimeoutS
$Info.SelftestExit = "$($sel.ExitCode)"
if ($sel.ExitCode -eq 255) {
    Add-Row 'FAIL' 'pc.ssh' "ssh to $User@$PhoneHost failed: $($sel.Err)"
} elseif ($sel.ExitCode -eq 127) {
    Add-Row 'PASS' 'pc.ssh' "logged in as $User with a key (BatchMode)"
    Add-Row 'FAIL' 'pc.selftest' '/usr/sbin/opos-selftest is not on the phone: an image built before the acceptance suite'
} else {
    Add-Row 'PASS' 'pc.ssh' "logged in as $User with a key (BatchMode)"
    Save-Bytes 'selftest.txt' $sel.Out
    $head = $null
    foreach ($ln in ((ConvertTo-Text $sel.Out) -split '\r?\n')) {
        if ($ln -match '^(PASS|FAIL|WARN|SKIP)\s+(\S+)\s+(.*)$') {
            Add-Row $Matches[1] $Matches[2] $Matches[3] 'phone'
        } elseif ($ln -match '^# opos-selftest \d+\s+(\S+)\s+host=\S+\s+uptime=(\S+)') {
            $Info.PhoneTime = $Matches[1]
            $Info.Uptime = $Matches[2]
        }
    }
    $phoneFails = @($Rows | Where-Object { $_.Source -eq 'phone' -and $_.Status -eq 'FAIL' }).Count
    $phoneRows = @($Rows | Where-Object { $_.Source -eq 'phone' }).Count
    if ($sel.ExitCode -ne 0 -and $sel.ExitCode -ne 1) {
        Add-Row 'FAIL' 'pc.selftest' "opos-selftest exited $($sel.ExitCode): $($sel.Err)"
    } elseif ($phoneRows -eq 0) {
        Add-Row 'FAIL' 'pc.selftest' 'opos-selftest printed no PASS/FAIL/WARN/SKIP line'
    } elseif (($sel.ExitCode -eq 1) -ne ($phoneFails -gt 0)) {
        Add-Row 'FAIL' 'pc.selftest' "opos-selftest exit status $($sel.ExitCode) does not match its $phoneFails FAIL line(s)"
    }
    $rc = 0
}

# --- 2. image revision and the boot diagnostics dump (only if the phone could be asked at all)
if ($rc -eq 0) {
    $rev = Invoke-Ssh $Remote.Revision 20
    $revText = (ConvertTo-Text $rev.Out).Trim()
    if ($rev.ExitCode -eq 0 -and $revText -match '^[0-9a-f]{40}$') {
        Save-Bytes 'revision.txt' $rev.Out
        $Info.Revision = $revText
        Add-Row 'PASS' 'pc.revision' "/opt/opos/REVISION is $($revText.Substring(0, 12))"
    } else {
        Add-Row 'WARN' 'pc.revision' '/opt/opos/REVISION is missing or is not a 40-hex git revision'
    }
    $has = Invoke-Ssh $Remote.DiagTest 20
    if ($has.ExitCode -eq 0) {
        $diag = Invoke-Ssh $Remote.Diag 60
        if ($diag.ExitCode -eq 0) {
            Save-Bytes 'opos-boot-diag.txt' $diag.Out
            Add-Row 'PASS' 'pc.boot-diag' "copied /var/log/opos-boot-diag.txt, $($diag.Out.Length) bytes"
        } else {
            Add-Row 'WARN' 'pc.boot-diag' "could not read /var/log/opos-boot-diag.txt: $($diag.Err)"
        }
    } else {
        Add-Row 'SKIP' 'pc.boot-diag' 'no readable /var/log/opos-boot-diag.txt on the phone (written 90 s after boot)'
    }

    # --- 3. what the page really renders
    if ($NoScreenshot) {
        Add-Row 'SKIP' 'pc.screenshot' 'switched off with -NoScreenshot'
    } else {
        $shot = Get-PageShot
        $status = $shot[0]
        if ($status -eq 'SKIP' -and $RequireScreenshot) { $status = 'FAIL' }
        Add-Row $status 'pc.screenshot' $shot[1]
    }
}

# --- 4. the evidence folder
$Finished = (Get-Date).ToUniversalTime()
function Get-Count([string]$S) { @($Rows | Where-Object { $_.Status -eq $S }).Count }
$nPass = Get-Count 'PASS'
$nWarn = Get-Count 'WARN'
$nSkip = Get-Count 'SKIP'
$nFail = Get-Count 'FAIL'
$verdict = 'PASS'
if ($nFail -gt 0) { $verdict = 'FAIL' }
$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add('# OPOS OS acceptance run')
$lines.Add('')
$lines.Add('- Started (UTC): ' + $Started.ToString('yyyy-MM-ddTHH:mm:ssZ'))
$lines.Add('- Finished (UTC): ' + $Finished.ToString('yyyy-MM-ddTHH:mm:ssZ'))
$lines.Add("- Phone: $User@$PhoneHost, ssh port $Port, key-only (BatchMode)")
$lines.Add('- Image revision (/opt/opos/REVISION): ' + $Info.Revision)
$lines.Add('- Phone clock at the self-test (UTC): ' + $Info.PhoneTime + ', uptime ' + $Info.Uptime)
$lines.Add('- opos-selftest exit status: ' + $Info.SelftestExit)
$lines.Add("- Result: **$verdict** ($nPass PASS, $nWarn WARN, $nSkip SKIP, $nFail FAIL)")
$lines.Add('')
$lines.Add('## Results')
$lines.Add('')
$lines.Add('| Status | Check | Evidence |')
$lines.Add('|---|---|---|')
foreach ($r in $Rows) { $lines.Add('| ' + $r.Status + ' | ' + $r.Id + ' | ' + $r.Evidence.Replace('|', '\|') + ' |') }
$lines.Add('')
$lines.Add('## Artifacts')
$lines.Add('')
$lines.Add('| File | Bytes | Written (UTC) | SHA-256 |')
$lines.Add('|---|---|---|---|')
foreach ($f in (Get-ChildItem -LiteralPath $Run -File | Sort-Object Name)) {
    $hash = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $lines.Add('| ' + $f.Name + ' | ' + $f.Length + ' | ' + $f.LastWriteTimeUtc.ToString('yyyy-MM-ddTHH:mm:ssZ') + ' | ' + $hash + ' |')
}
$lines.Add('')
$lines.Add('## What this run does and does not show')
$lines.Add('')
$lines.Add('- It changed nothing on the phone: it ran the read-only /usr/sbin/opos-selftest and read two files. The phone logged the ssh logins.')
$lines.Add('- A PASS is evidence from the phone (processes, sysfs, HTTP answers) or from Chromium''s own rendering. It is not a look at the panels.')
$lines.Add('- Lock screen on both panels, touch, the hinge column, rotation, fold, the on-screen keyboard, joining Wi-Fi, an over-the-air push, the battery against Android''s reading and the unplug test are the owner''s checks: docs/duo-acceptance.md, part 2.')
[System.IO.File]::WriteAllText((Join-Path $Run 'report.md'), (($lines -join "`n") + "`n"), [System.Text.UTF8Encoding]::new($false))
$sums = foreach ($f in (Get-ChildItem -LiteralPath $Run -File | Where-Object { $_.Name -ne 'SHA256SUMS' } | Sort-Object Name)) {
    (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + $f.Name
}
[System.IO.File]::WriteAllText((Join-Path $Run 'SHA256SUMS'), (($sums -join "`n") + "`n"), [System.Text.UTF8Encoding]::new($false))

# --- 5. the short table
Write-Host ''
foreach ($r in $Rows) {
    $color = @{ PASS = 'Green'; FAIL = 'Red'; WARN = 'Yellow'; SKIP = 'DarkGray' }[[string]$r.Status]
    if (-not $color) { $color = 'Gray' }
    $ev = $r.Evidence
    if ($ev.Length -gt 100) { $ev = $ev.Substring(0, 97) + '...' }
    Write-Host ('{0,-5}{1,-22}{2}' -f $r.Status, $r.Id, $ev) -ForegroundColor $color
}
Write-Host ''
Write-Host "$verdict : $nPass PASS, $nWarn WARN, $nSkip SKIP, $nFail FAIL"
Write-Host "Evidence: $Run"
if ($rc -ne 0) { exit 2 }
if ($nFail -gt 0) { exit 1 }
exit 0
