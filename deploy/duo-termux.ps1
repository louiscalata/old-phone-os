# SPDX-License-Identifier: GPL-3.0-or-later
# Put OPOS on a stock-Android phone (Surface Duo) over adb, without flashing or wiping:
# installs Termux and Termux:API, pushes this checkout's HEAD and an optional GGUF model,
# types deploy/termux-setup.sh into Termux, then opens OPOS in the phone's browser.
# adb reverse makes a Glimmer server on this computer's 127.0.0.1:1234 answer on the phone's.
# Undo: uninstall com.termux and com.termux.api and delete /sdcard/Download/opos.
#
#   powershell -File deploy\duo-termux.ps1 -ApkDir <dir with the two APKs> [-Model <file.gguf>] [-Bench 3]
param(
    [Parameter(Mandatory = $true)][string]$ApkDir,
    [string]$Model = "",
    [int]$Bench = 0,
    [int]$SetupTimeoutS = 2400
)
$ErrorActionPreference = "Stop"
$drop = "/sdcard/Download/opos"
function Adb { $out = & adb @args 2>&1; if ($LASTEXITCODE -ne 0) { throw "adb $($args -join ' '): $out" }; $out }
function Sh([string]$cmd) { (Adb shell $cmd | Out-String).Trim() }

$devices = @(& adb devices | Select-String "`tdevice$")
if ($devices.Count -ne 1) { throw "need exactly one authorized adb device, found $($devices.Count) (check the cable and tap Allow on the phone)" }
$info = [ordered]@{
    model   = Sh "getprop ro.product.model"
    android = Sh "getprop ro.build.version.release"
    sdk     = Sh "getprop ro.build.version.sdk"
    abi     = Sh "getprop ro.product.cpu.abi"
    mem     = Sh "grep -E 'MemTotal|MemAvailable' /proc/meminfo"
}
$info.GetEnumerator() | ForEach-Object { "{0,-8} {1}" -f $_.Key, $_.Value }

$pkgs = Sh "pm list packages com.termux"
foreach ($p in @(@{ id = "com.termux"; glob = "termux-app_*arm64*.apk" }, @{ id = "com.termux.api"; glob = "termux-api*.apk" })) {
    if ($pkgs -match "package:$([regex]::Escape($p.id))(\r?$|\s)") { "installed: $($p.id)"; continue }
    $apk = Get-ChildItem $ApkDir -Filter $p.glob | Select-Object -First 1
    if (-not $apk) { throw "no $($p.glob) in $ApkDir" }
    "installing $($apk.Name)"
    Adb install -r $apk.FullName | Out-Null
}

$root = Split-Path -Parent $PSScriptRoot
$tmp = Join-Path ([IO.Path]::GetTempPath()) "opos-src-$PID.tar.gz"
git -C $root archive --format=tar.gz -o $tmp HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }
Sh "mkdir -p $drop; rm -f $drop/status $drop/setup.log" | Out-Null
Adb push $tmp "$drop/opos-src.tar.gz" | Out-Null
Remove-Item $tmp
Adb push (Join-Path $PSScriptRoot "termux-setup.sh") "$drop/termux-setup.sh" | Out-Null
if ($Model) {
    $size = (Get-Item $Model).Length
    if ((Sh "stat -c %s $drop/model.gguf 2>/dev/null") -ne "$size") { "pushing model ($size bytes)"; Adb push $Model "$drop/model.gguf" | Out-Null }
}
# Termux targets API 28, so the legacy storage grant lets it read the drop folder.
foreach ($perm in "READ_EXTERNAL_STORAGE", "WRITE_EXTERNAL_STORAGE") { Sh "pm grant com.termux android.permission.$perm" | Out-Null }

Sh "input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard" | Out-Null
Sh "am start -n com.termux/com.termux.app.TermuxActivity" | Out-Null
"waiting for Termux's first-run bootstrap"
Start-Sleep -Seconds 25
Sh "input text 'sh%s$drop/termux-setup.sh'; input keyevent 66" | Out-Null

$deadline = (Get-Date).AddSeconds($SetupTimeoutS)
$last = ""
do {
    Start-Sleep -Seconds 10
    $st = Sh "cat $drop/status 2>/dev/null"
    if ($st -ne $last) { "{0:HH:mm:ss} {1}" -f (Get-Date), $st; $last = $st }
    if ($st -like "failed*") { Sh "tail -n 40 $drop/setup.log"; throw $st }
    if (-not $st -and (Get-Date) -gt $deadline.AddSeconds(90 - $SetupTimeoutS)) {
        "setup has not started; typing the command again"
        Sh "am start -n com.termux/com.termux.app.TermuxActivity; input text 'sh%s$drop/termux-setup.sh'; input keyevent 66" | Out-Null
        $deadline = $deadline.AddSeconds(60)
    }
} until ($st -eq "ready" -or (Get-Date) -gt $deadline)
if ($st -ne "ready") { Sh "tail -n 40 $drop/setup.log"; throw "setup timed out at: $st" }
"battery: " + (Sh "cat $drop/battery.json")

Adb reverse tcp:1234 tcp:1234 | Out-Null
Adb forward tcp:18765 tcp:8765 | Out-Null
$code = (Invoke-WebRequest http://127.0.0.1:18765/ -UseBasicParsing -TimeoutSec 10).StatusCode
"OPOS on the phone answered HTTP $code"
Sh "am start -a android.intent.action.VIEW -d http://127.0.0.1:8765/" | Out-Null

if ($Model -and $Bench -gt 0) {
    Adb forward tcp:18080 tcp:8080 | Out-Null
    $ready = $false
    for ($i = 0; $i -lt 120 -and -not $ready; $i++) {
        try { $ready = (Invoke-RestMethod http://127.0.0.1:18080/health -TimeoutSec 5).status -eq "ok" } catch { Start-Sleep -Seconds 2 }
    }
    if (-not $ready) { throw "llama-server on the phone did not become healthy" }
    $ids = (Invoke-RestMethod http://127.0.0.1:18080/v1/models -TimeoutSec 10).data.id -join ","
    "llama-server model ids: $ids"
    $q = @{
        model = "llama-3.2-1b-instruct"; max_tokens = 96; temperature = 0
        messages = @(
            @{ role = "system"; content = "You explain an old phone's battery decisions in two plain sentences." },
            @{ role = "user"; content = "Battery 60%, health 60% (about 36% usable), not charging, goal 16:00. Maps and photos backup are paused. Why are maps paused?" }
        )
    } | ConvertTo-Json -Depth 5
    $rows = for ($n = 1; $n -le $Bench; $n++) {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $r = Invoke-RestMethod http://127.0.0.1:18080/v1/chat/completions -Method Post -ContentType "application/json" -Body $q -TimeoutSec 600
        $sw.Stop()
        [pscustomobject]@{
            run = $n; wall_s = [math]::Round($sw.Elapsed.TotalSeconds, 3)
            prompt_tok = $r.usage.prompt_tokens; gen_tok = $r.usage.completion_tokens
            prompt_tok_s = [math]::Round($r.timings.prompt_per_second, 2); gen_tok_s = [math]::Round($r.timings.predicted_per_second, 2)
            reply = $r.choices[0].message.content
        }
    }
    $rows | Format-List
    $rows | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $root "duo-bench.json")
}
