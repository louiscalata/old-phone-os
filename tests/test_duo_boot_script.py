# SPDX-License-Identifier: GPL-3.0-or-later
"""deploy/duo-opos-boot.ps1 installs and RAM-boots the Duo OS image; it is never run here.
The text tests read it as a file. The PowerShell tests parse it (and run its network probe against loopback)
with pwsh or powershell when one is installed, and skip otherwise."""
import base64
import functools
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "duo-opos-boot.ps1"
HASHES = ("abec1fe9ce4cbb7a9296c3abd4fd7b4acc1283842e86f2bfe0879a7e84fae552",
          "457d8d2397b83eeffb843090da17d7959bcef7ed08658591938b3cda27b8132f",
          "892627cb4356d1d16c37bf243d700b5c9a0ab672d263de9ccd640c86b5cd6a5a")


def text():
    return SCRIPT.read_text(encoding="utf-8")


def code():
    """The script without comment-only lines, so a rule quoted in the header cannot satisfy a code check."""
    return "\n".join(line for line in text().splitlines() if not line.lstrip().startswith("#"))


def as_list(value):
    """ConvertTo-Json turns a one-element PowerShell array into a bare value."""
    return [] if value is None else [value] if isinstance(value, (str, dict)) else list(value)


def header():
    lines = text().splitlines()
    return "\n".join(lines[:next(i for i, line in enumerate(lines) if line.startswith("param("))])


class DuoBootScriptText(unittest.TestCase):
    def test_file_form(self):
        raw = SCRIPT.read_bytes()
        self.assertTrue(raw.startswith(b"# SPDX-License-Identifier: GPL-3.0-or-later\n"))
        self.assertNotIn(b"\r", raw)
        raw.decode("ascii")

    def test_header_documents_the_safety_rules(self):
        flat = " ".join(header().replace("#", " ").split())
        for rule in ("SHA-256 of every file is checked before the first write",
                     "the rootfs is checked again on the phone",
                     "product surfaceduo, current-slot a, unlocked yes, battery 20 percent or more",
                     "The misc brake",
                     "exactly ONCE and never retried",
                     "Device Error",
                     "Only misc (erase, flash) and userdata (format) are written",
                     "Nothing is written to boot, vbmeta, dtbo or recovery",
                     "no slot is changed",
                     "The first failure stops the script",
                     "ERASES userdata",
                     "Without -Yes it checks those files, prints the steps and stops",
                     "Without -Key that step is skipped",
                     "reboot bootloader"):
            with self.subTest(rule=rule):
                self.assertIn(rule, flat)

    def test_only_misc_and_userdata_are_written_and_nothing_flashes_boot(self):
        self.assertNotIn("fastboot flash boot", text())
        body = code()
        calls = re.findall(r"\bFb\s+('?[\w:.-]+'?)(?:\s+('?[\w:.-]+'?))?", body)
        self.assertTrue(calls)
        for verb, target in calls:
            verb = verb.strip("'")
            with self.subTest(verb=verb, target=target):
                self.assertIn(verb, ("getvar", "erase", "flash", "format:ext4", "boot"))
                if verb in ("erase", "flash"):
                    self.assertEqual(target, "misc")
                if verb == "format:ext4":
                    self.assertEqual(target, "userdata")
        self.assertEqual(sorted(v.strip("'") for v, _ in calls).count("format:ext4"), 1)
        for forbidden in ("set_active", "--slot", "flash:raw", "flashing", "oem ", "fastboot.exe reboot",
                          "poweroff", "shutdown", "mkfs", "dd if=", "rm -", "systemctl reboot", "reboot -f"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)
        self.assertIsNone(re.search(r"\bflash\s+(?!misc\b)\S+", body.replace("flash misc", "")))
        # The phone is only ever sent back to the bootloader, never rebooted into its own kernel.
        for line in body.splitlines():
            if re.search(r"\breboot\b", line):
                self.assertIn("reboot bootloader", line)
        remote = re.search(r"\$remote = '(.*)'\n", body).group(1)
        self.assertNotRegex(remote, r"reboot|poweroff|halt|\brm\b|\bdd\b|mkfs|sudo|\btee\b|>")

    def test_the_kernel_is_booted_once_after_the_hash_checks_and_preflight(self):
        body = code()
        boots = [m.start() for m in re.finditer(r"\bFb boot\b", body)]
        self.assertEqual(len(boots), 2)
        self.assertIn("surfaceduo1-twrp.img", body[boots[0]:boots[0] + 60])
        self.assertIn("boot.img", body[boots[1]:boots[1] + 40])
        self.assertLess(body.index("refusing a second RAM-boot"), boots[1])
        self.assertEqual(body.count("refusing a second RAM-boot"), 1)
        first_write = body.index("Fb erase misc")
        self.assertLess(body.index("hash ok"), first_write)
        self.assertLess(body.index("hash mismatch"), first_write)
        for check in ("product", "current-slot", "unlocked", "battery-level"):
            self.assertLess(body.index("Fb getvar " + check), first_write)
        self.assertRegex(body, r"\[int\]\$bat -lt 20")
        # The rootfs is compared on the phone before the phone leaves TWRP.
        self.assertLess(body.index("sha256sum /data/rootfs.img"), body.index("Ab reboot bootloader"))
        self.assertLess(body.index("Ab reboot bootloader"), boots[1])
        # The brake is armed before TWRP and again before the kernel.
        self.assertEqual(body.count("Fb flash misc"), 2)
        self.assertLess(body.index("Fb flash misc"), boots[0])
        self.assertLess(body.rindex("Fb flash misc"), boots[1])
        self.assertGreater(body.rindex("Fb flash misc"), boots[0])

    def test_pinned_hashes_and_required_inputs(self):
        for digest in HASHES:
            self.assertEqual(text().count(digest), 1)
        body = code()
        self.assertEqual(body.count("Mandatory = $true"), 2)
        self.assertIn("[Parameter(Mandatory = $true)][string]$ImageDir", body)
        self.assertIn("[Parameter(Mandatory = $true)][string]$RootfsSha256", body)
        self.assertIn("-RootfsSha256 must be 64 hex digits", body)
        self.assertIn('if ($h -ne $want[$n]) { throw "hash mismatch for', body)
        self.assertIn("Get-FileHash -LiteralPath $p[$n] -Algorithm SHA256", body)
        self.assertIn("if (($sum -split '\\s+')[0] -ne $want[$Rootfs]) { throw", body)

    def test_nothing_is_written_without_yes_and_nothing_is_hard_coded(self):
        body = code()
        self.assertLess(body.index("if (-not $Yes)"), body.index("Fb getvar"))
        self.assertIn("[switch]$Yes", body)
        self.assertIn("exit 0", body[body.index("if (-not $Yes)"):body.index("Fb getvar")])
        for private in ("C:\\Users", "AppData", "/Users/", "/home/", "scratchpad", "duo_dev", "DUO-SERIAL-REDACTED", "Temp\\"):
            with self.subTest(private=private):
                self.assertNotIn(private, text())
        self.assertIn("[string]$Serial = ''", body)
        self.assertIn("[string]$Key = ''", body)
        self.assertIn("Join-Path $ImageDir 'logs'", body)

    def test_ssh_key_is_optional(self):
        body = code()
        self.assertIn("if (-not $Key)", body)
        self.assertIn("skipping the diagnostics over ssh", body)
        self.assertIn("if ($Key) {", body)
        self.assertIn("-Key $Key is not a file", body)
        # Everything ssh needs sits in the branch that runs only with a key.
        self.assertGreater(body.index("Invoke-Ssh $ip"), body.index("if (-not $Key)"))
        self.assertEqual(body.count("Invoke-Ssh $ip"), 1)

    def test_discovery_probes_the_adapters_subnet_instead_of_asking_for_a_gateway(self):
        body = code()
        for dead in ("IPv4DefaultGateway", "Get-NetNeighbor", "Test-NetConnection", "NextHop"):
            with self.subTest(dead=dead):
                self.assertNotIn(dead, body)
        for live in ("System.Net.Sockets.TcpClient", "ConnectAsync", "Get-NetIPAddress", "Remote NDIS|RNDIS",
                     "OwnIp.LastIndexOf('.')", "1..254"):
            with self.subTest(live=live):
                self.assertIn(live, body)
        self.assertIn("[int]$PreferHost = 82", body)
        self.assertIn("Find-PhoneSsh $own $PreferHost 22", body)
        # The preferred host is probed alone before the sweep, and only inside the adapter's /24.
        find = body[body.index("function Find-PhoneSsh"):body.index("function RndisAddress")]
        self.assertLess(find.index("Test-Tcp @($Prefer)"), find.index("1..254"))
        self.assertIn("$net + $PreferHost", find)
        self.assertIn("-ne $OwnIp", find)

    def test_phone_ip_and_prefer_host_are_checked_first(self):
        body = code()
        self.assertIn("throw '-PhoneIp must be an IPv4 address'", body)
        self.assertIn("throw '-PreferHost must be a host number from 1 to 254'", body)
        for check in ("-PhoneIp must be an IPv4 address", "-PreferHost must be a host number"):
            at = body.index(check)
            with self.subTest(check=check):
                for later in ("Resolve-Path", "New-Item", "Get-Command $t", "Get-FileHash", "if (-not $Yes)",
                              "@(FastbootSerials)", "Fb getvar", "Fb erase misc", "Fb boot", "Invoke-Ssh $ip"):
                    self.assertLess(at, body.index(later), later)
        self.assertIn("if ($PhoneIp -and -not (Test-Ipv4 $PhoneIp))", body)


@functools.lru_cache(maxsize=None)
def powershell():
    """The first PowerShell that actually starts (a path on PATH can still be unusable, e.g. disabled WSL interop)."""
    for name in ("pwsh", "powershell", "powershell.exe"):
        found = shutil.which(name)
        if not found:
            continue
        try:
            done = subprocess.run([found, "-NoProfile", "-NonInteractive", "-Command", "exit 0"],
                                  capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            return found
    return None


def run_powershell(program, timeout=180):
    """Run a PowerShell program with the script text on its stdin; its stdout is one JSON document."""
    program = "$ProgressPreference = 'SilentlyContinue'\n" + program
    encoded = base64.b64encode(program.encode("utf-16-le")).decode("ascii")
    done = subprocess.run([powershell(), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                          input=SCRIPT.read_bytes(), capture_output=True, timeout=timeout)
    out = done.stdout.decode("utf-8", "replace")
    if done.returncode != 0 or not out.strip():
        raise AssertionError("powershell exit %s\nstdout: %s\nstderr: %s" % (
            done.returncode, out[-2000:], done.stderr.decode("utf-8", "replace")[-2000:]))
    return json.loads(out[out.index("{"):])


AST = r"""
$text = [Console]::In.ReadToEnd()
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$tokens, [ref]$errors)
function FunctionText([string]$name) {
    $f = $ast.Find({ param($a) $a -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $a.Name -eq $name }, $true)
    $f.Extent.Text
}
"""

PARSE = AST + r"""
$cmds = $ast.FindAll({ param($a) $a -is [System.Management.Automation.Language.CommandAst] -and $a.GetCommandName() -eq 'Fb' }, $true)
$boots = @($cmds | Where-Object { $_.CommandElements[1].Extent.Text -eq 'boot' } | ForEach-Object {
    $n = $_.Parent; $nested = $false
    while ($n) {
        if ($n -is [System.Management.Automation.Language.LoopStatementAst] -or $n -is [System.Management.Automation.Language.ScriptBlockExpressionAst] -or $n -is [System.Management.Automation.Language.FunctionDefinitionAst]) { $nested = $true }
        $n = $n.Parent
    }
    [pscustomobject]@{ line = $_.Extent.StartLineNumber; nested = $nested }
})
$mandatory = @($ast.ParamBlock.Parameters | Where-Object { $_.Attributes | Where-Object { $_.TypeName.Name -eq 'Parameter' -and ($_.NamedArguments | Where-Object { $_.ArgumentName -eq 'Mandatory' }) } } | ForEach-Object { $_.Name.VariablePath.UserPath })
$names = @($ast.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
[pscustomobject]@{ errors = @($errors | ForEach-Object { $_.Message }); boots = $boots; mandatory = $mandatory; names = $names; version = "$($PSVersionTable.PSVersion)" } | ConvertTo-Json -Compress -Depth 4
"""

# The phone is host 82 of a /24 around this PC's own address; loopback stands in for that /24.
DISCOVER = AST + r"""
. ([scriptblock]::Create((FunctionText 'Test-Tcp')))
. ([scriptblock]::Create((FunctionText 'Find-PhoneSsh')))
$listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Parse('127.0.0.82'), 0)
try { $listener.Start() } catch { '{"skip": "cannot bind 127.0.0.82"}'; exit 0 }
$port = $listener.LocalEndpoint.Port
$swept = @(Find-PhoneSsh '127.0.0.147' 200 $port 2500)
$preferred = @(Find-PhoneSsh '127.0.0.147' 82 $port 2500)
$listener.Stop()
$sw = [Diagnostics.Stopwatch]::StartNew()
$none = @(Find-PhoneSsh '127.0.0.147' 82 $port 1000)
[pscustomobject]@{ swept = $swept; preferred = $preferred; none = $none.Count; none_ms = $sw.ElapsedMilliseconds } | ConvertTo-Json -Compress
"""


def ipv4_program(cases):
    return (AST + ". ([scriptblock]::Create((FunctionText 'Test-Ipv4')))\n"
            "$cases = '" + json.dumps(cases) + "' | ConvertFrom-Json\n"
            "[pscustomobject]@{ results = @($cases | ForEach-Object { [bool](Test-Ipv4 $_) }) } | ConvertTo-Json -Compress\n")


# The whole script, run in this PowerShell session with fastboot.exe, adb.exe, Get-PnpDevice and Start-Sleep
# replaced by functions: every call is appended to a log, and the fake phone answers a healthy preflight. PATH is emptied
# first, so even if a function were bypassed no real fastboot or adb could be found. The three pinned hashes in
# the script's copy are swapped for those of small stand-in files (data only; the pinning itself is tested as
# text above). $runs is a JSON list of {label, args}; each run starts from a clean image folder.
FAKE_PHONE = AST + r"""
$work = Join-Path ([IO.Path]::GetTempPath()) ('opos-duo-boot-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $empty = Join-Path $work 'empty-path'
    New-Item -ItemType Directory -Path $empty | Out-Null
    $env:PATH = $empty
    $global:CallLog = Join-Path $work 'calls.txt'
    $img = Join-Path $work 'img'
    New-Item -ItemType Directory -Path $img | Out-Null
    foreach ($n in 'boot.img', 'surfaceduo1-twrp.img', 'misc-brake.img', 'opos-os-rootfs.img') {
        Set-Content -LiteralPath (Join-Path $img $n) -Value ('stand-in ' + $n) -Encoding ASCII
        $actual = (Get-FileHash -LiteralPath (Join-Path $img $n) -Algorithm SHA256).Hash.ToLower()
        $pinned = [regex]::Match($text, "'" + [regex]::Escape($n) + "'\s*=\s*'([0-9a-f]{64})'").Groups[1].Value
        if ($pinned) { $text = $text.Replace($pinned, $actual) } else { $rootfs = $actual }
    }
    if (-not $rootfs -or $text -notmatch 'hash mismatch') { throw 'harness: stand-in hashes were not set up' }
    $global:RootfsSum = $rootfs
    function global:fastboot.exe {
        Add-Content -LiteralPath $global:CallLog -Value ('fastboot ' + ($args -join ' '))
        $global:LASTEXITCODE = 0
        $a = $args -join ' '
        if ($a -eq 'devices') { "SER1`tfastboot" }
        elseif ($a -match 'getvar product') { 'product: surfaceduo' }
        elseif ($a -match 'getvar current-slot') { 'current-slot: a' }
        elseif ($a -match 'getvar unlocked') { 'unlocked: yes' }
        elseif ($a -match 'getvar battery-level') { 'battery-level: 80' }
    }
    function global:adb.exe {
        Add-Content -LiteralPath $global:CallLog -Value ('adb ' + ($args -join ' '))
        $global:LASTEXITCODE = 0
        $a = $args -join ' '
        if ($a -eq 'devices') { 'List of devices attached'; "SER1`trecovery" }
        elseif ($a -match 'echo MOUNTED') { 'MOUNTED' }
        elseif ($a -match 'sha256sum') { $global:RootfsSum + '  /data/rootfs.img' }
        elseif ($a -match ' push ') { '1 file pushed' }
    }
    function global:Get-PnpDevice { [pscustomobject]@{ InstanceId = 'USB\VID_1D6B&PID_0104\1' } }
    # The fake phone answers at once, so any wait means the script went somewhere it should not: stop it there
    # (it reports "STOPPED" and exits 1) instead of letting a regression sit out its multi-minute timeouts.
    function global:Start-Sleep { throw 'harness: the script waits for something the fake phone never gives' }
    $tmp = Join-Path $work 'duo-opos-boot.ps1'
    Set-Content -LiteralPath $tmp -Value $text -Encoding UTF8
    $results = @()
    foreach ($run in ($runs | ConvertFrom-Json)) {
        $p = @{ ImageDir = $img; RootfsSha256 = $global:RootfsSum; WaitMinutes = 1 }
        foreach ($prop in $run.args.PSObject.Properties) { $p[$prop.Name] = $prop.Value }
        Remove-Item -LiteralPath $global:CallLog -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (Join-Path $img 'logs') -Recurse -Force -ErrorAction SilentlyContinue
        $global:LASTEXITCODE = $null
        $threw = $null
        try { & $tmp @p *> $null } catch { $threw = $_.Exception.Message }
        $logs = Join-Path $img 'logs'
        $logfile = Join-Path $logs 'boot-chain.log'
        $results += [pscustomobject]@{
            label = $run.label
            threw = $threw
            exit = $global:LASTEXITCODE
            calls = @(if (Test-Path -LiteralPath $global:CallLog) { Get-Content -LiteralPath $global:CallLog | ForEach-Object { [string]$_ } })
            logs_dir = [bool](Test-Path -LiteralPath $logs)
            log = $(if (Test-Path -LiteralPath $logfile) { (Get-Content -LiteralPath $logfile) -join "`n" } else { '' })
        }
    }
    [pscustomobject]@{ runs = @($results) } | ConvertTo-Json -Compress -Depth 6
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
"""


def fake_phone_program(runs):
    return "$runs = '" + json.dumps(runs).replace("'", "''") + "'\n" + FAKE_PHONE


@unittest.skipUnless(powershell(), "needs pwsh or powershell to parse the script")
class DuoBootScriptPowerShell(unittest.TestCase):
    def test_parses_without_errors_and_asks_only_for_the_essentials(self):
        got = run_powershell(PARSE)
        self.assertEqual(got["errors"], [], got["version"])
        self.assertEqual(sorted(got["mandatory"]), ["ImageDir", "RootfsSha256"])
        for name in ("Key", "Serial", "PhoneIp", "PreferHost", "LogDir", "Yes", "Rootfs", "WaitMinutes"):
            self.assertIn(name, got["names"])

    def test_the_kernel_boot_is_not_in_a_loop_or_a_retry_block(self):
        boots = run_powershell(PARSE)["boots"]
        if isinstance(boots, dict):
            boots = [boots]
        self.assertEqual(len(boots), 2)
        for boot in boots:
            self.assertFalse(boot["nested"], boot)

    def test_probe_finds_the_phone_across_the_subnet_and_prefers_its_address(self):
        got = run_powershell(DISCOVER)
        if "skip" in got:
            self.skipTest(got["skip"])
        for key in ("swept", "preferred"):
            value = got[key]
            self.assertEqual([value] if isinstance(value, str) else value, ["127.0.0.82"], key)
        self.assertEqual(got["none"], 0)
        self.assertLess(got["none_ms"], 10000)

    def test_ipv4_check_accepts_dotted_quads_only(self):
        good = ["0.0.0.0", "127.0.0.1", "203.0.113.7", "255.255.255.255", "9.9.9.9", "1.20.200.249"]
        bad = ["", "phone.local", "1", "1.2", "1.2.3", "1.2.3.4.5", "256.1.1.1", "1.2.3.256", "01.2.3.4", "1.2.3.04",
               "0x7f.0.0.1", "1.2.3.4:22", "2001:db8::1", " 1.2.3.4", "1.2.3.4 ", "1.2.3.4\n", "1.2.3.-4",
               "\u0661.2.3.4", "1,2,3,4", "203.0.113.7/24", "-1.2.3.4"]
        got = as_list(run_powershell(ipv4_program(good + bad))["results"])
        self.assertEqual(got, [True] * len(good) + [False] * len(bad))

    def test_a_bad_phone_ip_or_prefer_host_stops_before_any_tool_runs(self):
        """The late failure this prevents: a typo in -PhoneIp used to surface only after userdata was formatted,
        the rootfs pushed and the one kernel RAM-boot spent. Here every fastboot and adb call is recorded."""
        bad_ips = ["phone.local", "203.0.113", "203.0.113.256", "1", "0x7f.0.0.1", "2001:db8::1", "203.0.113.7:22",
                   "203.0.113.07", " 203.0.113.7", "203.0.113.7 "]
        runs = [{"label": "ip %r" % ip, "args": {"PhoneIp": ip, "Yes": True}} for ip in bad_ips]
        runs.append({"label": "dry run", "args": {"PhoneIp": "phone.local"}})
        runs += [{"label": "host %s" % n, "args": {"PreferHost": n, "Yes": True}} for n in (0, 255, -1, 1000)]
        got = as_list(run_powershell(fake_phone_program(runs))["runs"])
        self.assertEqual(len(got), len(runs))
        for run in got:
            with self.subTest(run=run["label"]):
                expected = ("-PreferHost must be a host number from 1 to 254" if run["label"].startswith("host")
                            else "-PhoneIp must be an IPv4 address")
                self.assertEqual(run["threw"], expected)
                self.assertEqual(as_list(run["calls"]), [], "a fastboot or adb command ran")
                self.assertFalse(run["logs_dir"], "the script got as far as making its log folder")

    def test_good_phone_ip_and_prefer_host_pass_the_check_and_a_dry_run_runs_no_tool(self):
        runs = [{"label": "ip", "args": {"PhoneIp": "203.0.113.7"}},
                {"label": "first host", "args": {"PreferHost": 1}},
                {"label": "last host", "args": {"PreferHost": 254, "PhoneIp": "255.255.255.255"}},
                {"label": "none given", "args": {}}]
        got = as_list(run_powershell(fake_phone_program(runs))["runs"])
        self.assertEqual(len(got), len(runs))
        for run in got:
            with self.subTest(run=run["label"]):
                self.assertIsNone(run["threw"])
                self.assertEqual(run["exit"], 0)
                self.assertEqual(as_list(run["calls"]), [])
                self.assertTrue(run["logs_dir"])
                self.assertIn("hash ok boot.img", run["log"])
                self.assertIn("dry run (no -Yes)", run["log"])


if __name__ == "__main__":
    unittest.main()
