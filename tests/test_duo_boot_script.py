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


if __name__ == "__main__":
    unittest.main()
