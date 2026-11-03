# SPDX-License-Identifier: GPL-3.0-or-later
param([switch]$DryRun, [switch]$NoLaunch, [switch]$Yes, [switch]$Uninstall, [switch]$KeepConfig)
$ErrorActionPreference = 'Stop'
$OposHome = if ($env:OPOS_HOME) { $env:OPOS_HOME } else { Join-Path $env:LOCALAPPDATA 'opos' }
$OposHome = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OposHome)
$BinDir = if ($env:OPOS_BIN_DIR) { $env:OPOS_BIN_DIR } else { Join-Path $OposHome 'bin' }
$BinDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($BinDir)
$Root = $PSScriptRoot
$VenvDir = Join-Path $OposHome 'venv'
$VenvPy = Join-Path $VenvDir 'Scripts\python.exe'
$Launcher = Join-Path $BinDir 'opos.cmd'
$Manifest = Join-Path $OposHome 'manifest'
$Check = 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
function Say([string]$m) { Write-Host "==> $m" }
function Test-Py([string]$Command, [string[]]$Arguments = @()) {
  try { $g = Get-Command $Command -CommandType Application -ErrorAction Stop } catch { return $false }
  try { & $g.Source @Arguments -c $Check 2>$null | Out-Null } catch { return $false }
  return ($LASTEXITCODE -eq 0)
}
if ($Uninstall) {
  if ((Split-Path -Leaf $OposHome) -ne 'opos') { [Console]::Error.WriteLine("refusing to uninstall: OPOS_HOME must end in 'opos': $OposHome"); exit 1 }
  $found = $false
  if (Test-Path -LiteralPath $Launcher) {
    $found = $true
    $u = @('uninstall')
    if ($KeepConfig) { $u += '--keep-config' }
    if ($DryRun) { $u += '--dry-run' }
    Say "running launcher uninstall"
    & $Launcher @u
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  }
  foreach ($p in @($Launcher, $VenvDir, $Manifest)) {
    if (Test-Path -LiteralPath $p) {
      $found = $true
      if ($DryRun) { Say "would remove $p" } elseif ($p -eq $VenvDir) { Remove-Item -LiteralPath $p -Recurse -Force; Say "removed $p" } else { Remove-Item -LiteralPath $p -Force; Say "removed $p" }
    }
  }
  if ((Test-Path -LiteralPath $OposHome) -and @(Get-ChildItem -LiteralPath $OposHome -Force).Count -eq 0) {
    $found = $true
    if ($DryRun) { Say "would remove $OposHome" } else { Remove-Item -LiteralPath $OposHome -Force; Say "removed $OposHome" }
  }
  if (-not $found) { Say 'nothing to remove' }
  exit 0
}
if ($env:OPOS_PYTHON) {
  # An override is a literal executable path (possibly containing spaces).
  # Also retain the supported Windows launcher form, e.g. OPOS_PYTHON=py -3.12.
  $cands = @(@{ Command = $env:OPOS_PYTHON.Trim('"'); Arguments = @() })
  if ($env:OPOS_PYTHON -match '^(py(?:\.exe)?)\s+(-3(?:\.\d+)?)$') {
    $cands = @(@{ Command = $Matches[1]; Arguments = @($Matches[2]) })
  }
} else {
  $cands = @(
    @{ Command = 'py'; Arguments = @('-3.13') },
    @{ Command = 'py'; Arguments = @('-3.12') },
    @{ Command = 'py'; Arguments = @('-3.11') },
    @{ Command = 'py'; Arguments = @('-3') },
    @{ Command = 'python3'; Arguments = @() },
    @{ Command = 'python'; Arguments = @() }
  )
}
$Python = $null
$PythonArgs = @()
foreach ($c in $cands) {
  if (Test-Py $c.Command $c.Arguments) {
    $Python = (Get-Command $c.Command -CommandType Application -ErrorAction Stop).Source
    $PythonArgs = $c.Arguments
    break
  }
}
if (-not $Python) { [Console]::Error.WriteLine('Python 3.11 or newer is required: https://www.python.org/downloads/'); exit 1 }
Say "using python $Python $($PythonArgs -join ' ')"
if ((Test-Path -LiteralPath $VenvPy) -and (Test-Py $VenvPy)) {
  Say "reuse venv $VenvDir"
} elseif ($DryRun) {
  Say "would create venv $VenvDir"
} else {
  & $Python @PythonArgs -m venv $VenvDir
  if ($LASTEXITCODE -ne 0) { [Console]::Error.WriteLine('venv creation failed'); exit 1 }
  Say "created venv $VenvDir"
}
$req = Join-Path $Root 'requirements.txt'
$need = $false
foreach ($l in (Get-Content -LiteralPath $req -ErrorAction Stop)) { if ($l.Trim() -ne '' -and -not $l.TrimStart().StartsWith('#')) { $need = $true; break } }
if ($need) {
  if ($DryRun) { Say 'would install packages from requirements.txt' }
  else {
    & $VenvPy -m pip install -r $req --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { [Console]::Error.WriteLine('package installation failed'); exit 1 }
    Say 'installed third-party packages'
  }
} else {
  Say 'no third-party packages to install'
}
if ($DryRun) {
  Say 'would run self-test'
} else {
  $HadPythonPath = Test-Path Env:PYTHONPATH
  $OldPythonPath = $env:PYTHONPATH
  try {
    $env:PYTHONPATH = Join-Path $Root 'src'
    & $VenvPy -m opos.setup.selftest
  } finally {
    if ($HadPythonPath) { $env:PYTHONPATH = $OldPythonPath }
    else { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue }
  }
  if ($LASTEXITCODE -ne 0) { [Console]::Error.WriteLine('self-test failed, nothing was installed'); exit 1 }
  Say 'self-test passed'
}
if ($DryRun) {
  Say "would write launcher $Launcher"
  Say "would write manifest $Manifest"
} else {
  New-Item -ItemType Directory -Force $BinDir | Out-Null
  New-Item -ItemType Directory -Force $OposHome | Out-Null
  $enc = [System.Text.UTF8Encoding]::new($false)
  # Batch expands percent signs even inside quotes; ! must stay literal too.
  $BatchSrc = (Join-Path $Root 'src').Replace('%', '%%')
  $BatchPy = $VenvPy.Replace('%', '%%')
  $bat = @(
    '@echo off',
    'setlocal DisableDelayedExpansion',
    'chcp 65001 >nul',
    ('set "PYTHONPATH=' + $BatchSrc + '"'),
    'if /I "%~1"=="web" goto web',
    ('"' + $BatchPy + '" -m opos.setup %*'),
    'exit /b %errorlevel%',
    ':web',
    ('"' + $BatchPy + '" -m opos.web --port 8765'),
    'exit /b %errorlevel%'
  )
  [System.IO.File]::WriteAllText($Launcher, ($bat -join "`r`n"), $enc)
  [System.IO.File]::WriteAllText($Manifest, "launcher=$Launcher`r`nvenv=$VenvDir`r`nrepo=$Root", $enc)
  Say "wrote launcher $Launcher"
  Say "wrote manifest $Manifest"
}
# Print commands as PowerShell literals; do not change PATH in this installer.
if ($DryRun) {
  Say 'Dry run finished. Nothing was installed.'
} else {
  $QuotedBinDir = "'" + $BinDir.Replace("'", "''") + "'"
  $QuotedLauncher = "'" + $Launcher.Replace("'", "''") + "'"
  Say 'After installation, copy and run this command to enable opos in this PowerShell session:'
  Write-Host ('$env:Path = ' + $QuotedBinDir + ' + '';'' + $env:Path')
  Say "For future shells: open Edit environment variables for your account, select User variables > Path > Edit > New, add $BinDir, then reopen PowerShell."
  Say 'Windows setup does not start the application server. Complete the setup wizard, or apply defaults with:'
  Write-Host ('& ' + $QuotedLauncher + ' run --defaults')
  Say 'After setup, start the server in another PowerShell window and leave it open:'
  Write-Host ('& ' + $QuotedLauncher + ' web')
  Say 'Then open http://127.0.0.1:8765/. Press Ctrl+C in the server window to stop it.'
}
if (-not $NoLaunch -and -not $DryRun) { & $Launcher run; exit $LASTEXITCODE }
