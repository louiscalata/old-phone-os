# SPDX-License-Identifier: GPL-3.0-or-later
"""deploy/duo-persist-probe.ps1 is the READ-ONLY probe and backup of the Duo persistent-kernel runbook
(docs/duo-persistent-install.md). It is never run against a phone here. The text tests read it as a file. The PowerShell
tests parse it, run its allow-list on good and bad command lines, and run the whole script against a stand-in adb and
fastboot (tests/fixtures/fake_duo_tools.cs, compiled under Windows PowerShell); they skip when no such PowerShell exists.
The documentation tests read the runbook as a file."""
import base64
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "duo-persist-probe.ps1"
BOOT_SCRIPT = ROOT / "deploy" / "duo-opos-boot.ps1"
RUNBOOK = ROOT / "docs" / "duo-persistent-install.md"
FAKE_CS = ROOT / "tests" / "fixtures" / "fake_duo_tools.cs"
PINNED = {"boot.img": "abec1fe9ce4cbb7a9296c3abd4fd7b4acc1283842e86f2bfe0879a7e84fae552",
          "surfaceduo1-twrp.img": "457d8d2397b83eeffb843090da17d7959bcef7ed08658591938b3cda27b8132f",
          "misc-brake.img": "892627cb4356d1d16c37bf243d700b5c9a0ab672d263de9ccd640c86b5cd6a5a"}
# name, node, size in bytes: the partition table of this unit as recorded on October 31, 2026.
PARTS = (("boot_a", "sde9", 100663296), ("boot_b", "sde26", 100663296), ("vbmeta_a", "sde14", 65536),
         ("vbmeta_b", "sde31", 65536), ("dtbo_a", "sde15", 16777216), ("dtbo_b", "sde32", 16777216),
         ("misc", "sda5", 1048576))
# What guard/check.py refuses in tracked files: private network addresses and user paths.
PRIVATE = (r"(?<![\d.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}(?![\d.])",
           r"(?<![\w.-])/home/[A-Za-z]", r"[A-Za-z]:\\{1,2}Users\\{1,2}", "/Users/[A-Za-z]")
# Words that would make a command a write or a state change; the plan and every call must be free of them.
FORBIDDEN_IN_A_COMMAND = r"\b(flash\w*|erase|format|set_active|oem|reboot|mount|setenforce)\b|\bof=|\bboot\s"


def text():
    return SCRIPT.read_text(encoding="utf-8")


def code():
    """The script without comment-only lines, so a rule quoted in the header cannot satisfy a code check."""
    return "\n".join(line for line in text().splitlines() if not line.lstrip().startswith("#"))


def function_text(name):
    """The source of one function of the script (it ends at the first closing brace in column 0)."""
    found = re.search(r"^function %s\b.*?^\}$" % re.escape(name), code(), re.S | re.M)
    assert found, name
    return found.group(0)


def header():
    lines = text().splitlines()
    return "\n".join(lines[:next(i for i, line in enumerate(lines) if line.startswith("param("))])


def as_list(value):
    """ConvertTo-Json turns a one-element PowerShell array into a bare value."""
    return [] if value is None else [value] if isinstance(value, (str, dict)) else list(value)


# ---------------------------------------------------------------------------------------------------------------------
class PersistProbeScriptText(unittest.TestCase):
    def test_file_form(self):
        raw = SCRIPT.read_bytes()
        self.assertTrue(raw.startswith(b"# SPDX-License-Identifier: GPL-3.0-or-later\n#requires -Version 5.1\n"))
        self.assertNotIn(b"\r", raw)
        self.assertTrue(raw.endswith(b"\n"))
        raw.decode("ascii")

    def test_header_states_the_rules(self):
        flat = " ".join(header().replace("#", " ").split())
        for rule in ("NOTHING IS WRITTEN TO THE PHONE", "Every command goes through Invoke-Allowed",
                     "refuses any line that is not matched, whole and case-sensitively, by the allow-list",
                     "The list holds reads only", "it names no output file, so it writes to stdout",
                     "on the fastboot side only devices and getvar", "never changes the phone's mode",
                     "A wrong mode is refused, not corrected",
                     "It stops at the first failure and says plainly that nothing was written",
                     "refuses to put its output inside any git working tree",
                     "-DryRun prints the commands and exits", "It looks for no tool and contacts no phone"):
            with self.subTest(rule=rule):
                self.assertIn(rule, flat)

    def test_nothing_that_writes_or_changes_state_is_in_the_code(self):
        body = code()
        forbidden = {
            "a flash command": r"(?i)\bflash",
            "an erase": r"(?i)\berase",
            "a format": r"(?i)\bformat",
            "set_active": r"(?i)set[_-]active",
            "an oem command": r"(?i)\boem\b",
            "flashing lock or unlock": r"(?i)\bflashing\b",
            "a RAM-boot (the word boot and a space)": r"(?i)\bboot\s",
            "fastboot boot": r"(?i)fastboot(\.exe)?['\"]?\s+boot\b",
            "a dd output file": r"(?i)\bof=",
            "a redirect into /dev/block": r">\s*/dev/block",
            "a write to a device node": r"(?i)\bof\s*=\s*/dev",
            "bootctl set or mark": r"(?i)bootctl\s+(set|mark)",
            "a boot-control write": r"(?i)(set-active-boot-slot|mark-boot-successful|set-slot-as-unbootable)",
            "a mount command": r"(?i)\bu?mount\b",
            "a reboot": r"(?i)\breboot\b",
            "rm or mkfs": r"\b(rm|mkfs)\b",
            "setenforce or setprop": r"(?i)\bset(enforce|prop)\b",
            "a wipe": r"(?i)\bwipe",
            "sgdisk with an option other than -p or -i": r"sgdisk\s+(?!-p\b|-i\b)",
            "blockdev with a setter": r"(?i)blockdev\s+--(set|rereadpt|flush)",
            "dmesg that clears": r"dmesg\s+-",
            "adb push, pull, install, sideload, root or remount":
                r"(?i)['\"](push|pull|install|sideload|root|remount|reboot)\s",
            "the call operator": r"(?:^|[\s(;{|])&\s*[$\"'(\w]",
            "a way to start another program": r"(?i)Start-Process|Invoke-Expression|\biex\b|Invoke-Command|\bcmd(\.exe)?\b",
            "network or file removal or copy cmdlets": r"(?i)Invoke-WebRequest|Invoke-RestMethod|Remove-Item|Set-Content|Out-File|Copy-Item|Move-Item",
            "file replacement through .NET": r"\[System\.IO\.File\]::(Delete|Move|Copy|Replace|Open\b|WriteAll(?!Text))",
        }
        for label, pattern in forbidden.items():
            with self.subTest(forbidden=label):
                self.assertIsNone(re.search(pattern, body), re.search(pattern, body) and re.search(pattern, body).group(0))

    def test_every_command_goes_through_one_wrapper_that_checks_the_allow_list_first(self):
        body = code()
        self.assertEqual(body.count("[System.Diagnostics.Process]::Start"), 1)
        self.assertEqual(body.count("System.Diagnostics.ProcessStartInfo"), 1)
        self.assertIsNone(re.search(r"New-Object System\.Diagnostics\.Process(?!StartInfo)", body))
        wrapper = function_text("Invoke-Allowed")
        self.assertIn("[System.Diagnostics.Process]::Start", wrapper)
        self.assertLess(wrapper.index("Test-Allowed $Tool $Line"), wrapper.index("[System.Diagnostics.Process]::Start"))
        self.assertLess(wrapper.index("throw \"refused: not on the allow-list"), wrapper.index("[System.Diagnostics.Process]::Start"))
        self.assertIn("$argLine = '-s ' + $script:Serial + ' ' + $Line", wrapper)
        self.assertIn("if ($Line -ne 'devices') {", wrapper)
        # The wrapper is called from one place, with the step's own tool and line.
        outside = body.replace(wrapper, "")
        calls = re.findall(r"Invoke-Allowed\b[^\n]*", outside)
        self.assertEqual(len(calls), 3, calls)
        self.assertIn("Invoke-Allowed", function_text("Invoke-Step"))
        for call in calls:
            self.assertRegex(call, r"Invoke-Allowed \$s\.Tool \$s\.Line")
        # Tools are looked up by file name in one place and never run by name.
        self.assertEqual(len(re.findall(r"adb\.exe|fastboot\.exe", body)), 2)
        self.assertIn("Resolve-Tool 'adb.exe'", body)
        self.assertIn("Resolve-Tool 'fastboot.exe'", body)
        # The check is case-sensitive, anchored by \z, and an unknown tool is refused.
        allowed = function_text("Test-Allowed")
        self.assertIn("-cmatch $r", allowed)
        self.assertNotRegex(allowed, r"(?<!c)-match")
        self.assertRegex(allowed, r"else \{\s*return \$false\s*\}")
        self.assertIn("[^\\x20-\\x7e]", allowed)

    def test_allow_list_holds_reads_only(self):
        body = code()
        phone = body[body.index("$PhoneRules = @("):body.index("$DdPullRule")]
        verbs = re.findall(r"'\^([a-z0-9]+)|\('\^([a-z0-9]+)", phone)
        # The pinned TWRP has no bootctl (row 20 of the runbook) and the plan never asks for getprop: neither is allowed.
        self.assertEqual({a or b for a, b in verbs},
                         {"id", "getenforce", "dmesg", "cat", "ls", "df", "readlink", "blockdev", "sha256sum", "sgdisk"})
        self.assertNotIn("bootctl", body.lower())
        self.assertNotIn("getprop", body.lower())
        self.assertEqual(len(re.findall(r"\\z'", phone)), len(re.findall(r"^\s+\(?'\^", phone, re.M)))
        for read in ("cat /proc/(?:cmdline|version|partitions|mounts)", "--(?:getsize64|getro)", "sgdisk -p /dev/block/sde",
                     "sgdisk -i [0-9]{1,3} /dev/block/sde", "'^df /tmp\\z'"):
            with self.subTest(read=read):
                self.assertIn(read, phone)
        pull = re.search(r"\$DdPullRule = (.*)", body).group(1)
        self.assertTrue(pull.startswith("'^dd if=' + $AnyDev + ' bs="))
        self.assertTrue(pull.endswith(" 2>/dev/null\\z'"))
        self.assertNotIn("of=", pull)
        head = re.search(r"\$HeadPullRule = (.*)", body).group(1)
        self.assertEqual(head, "'^head -c [0-9]{1,9} ' + $AnyDev + '\\z'")
        self.assertIn("$PullRules = @($DdPullRule, $HeadPullRule)", body)
        self.assertIn("$AdbHostRules = @('^devices\\z', '^get-state\\z')", body)
        self.assertIn("$FastbootRules = @('^devices\\z', '^getvar all\\z', '^getvar [a-z][a-z0-9-]*(?::[a-z0-9_]+)?\\z')", body)
        # Only the listed partitions can be named, by exact node or by name.
        self.assertIn("$NodeRx = 'sde(?:9|14|15|26|31|32)|sda5'", body)
        self.assertIn("(?:boot_[ab]|vbmeta_[ab]|dtbo_[ab]|misc)", body)

    def test_backups_are_hashed_on_the_phone_on_the_pc_and_on_a_second_read(self):
        body = code()
        plan = function_text("Build-Plan")
        order = [plan.index(token) for token in ("'-sha'", "'-pull'", "'-again'")]
        self.assertEqual(order, sorted(order))
        self.assertIn("shell sha256sum", plan)
        self.assertEqual(plan.count("Get-PullLine"), 2)
        pull_line = function_text("Get-PullLine")
        self.assertEqual(pull_line.count("bs=4096 count="), 1)
        self.assertIn("'exec-out head -c '", pull_line)
        check = function_text("Confirm-Step")
        for needle in ('if ($r.Bytes -ne [long]$s.Value) { return "short or long read',
                       "if ($disk -cne $r.Sha256) { return 'the file on disk does not hash like the stream that wrote it'",
                       "if ($s.Ref -and $script:Results[$s.Ref].Value -cne $r.Sha256) {",
                       "differs from the hash on the phone",
                       'if ($r.Sha256 -cne $script:Results[$s.Ref].Value) { return "the second read'):
            with self.subTest(needle=needle):
                self.assertIn(needle, check)
        # A failed check stops the run at that step.
        step = function_text("Invoke-Step")
        self.assertIn("if ($status -eq 'failed') { throw $why }", step)
        self.assertIn("CreateNew", function_text("Invoke-Allowed"))
        self.assertEqual(len(re.findall(r"\bcatch\b", body)), 1)
        self.assertIn("foreach ($s in $plan) { Invoke-Step $s }", body)

    def test_a_hung_command_is_killed_after_the_time_limit_and_the_run_stops(self):
        body = code()
        wrapper = function_text("Invoke-Allowed")
        # Reads are asynchronous and bounded by one deadline for the whole command; the exit is waited for with a limit.
        self.assertIn("$deadline = [DateTime]::UtcNow.AddSeconds($limit)", wrapper)
        self.assertIn("$limit = [int]$script:TimeoutSec", wrapper)
        self.assertIn("$src.ReadAsync($buf, 0, $buf.Length)", wrapper)
        self.assertIn("-not $pending.Wait([Math]::Max($left, 0))", wrapper)
        self.assertIn("-not $p.WaitForExit([Math]::Max($left, 1))", wrapper)
        self.assertEqual(wrapper.count("Stop-Hung $p $Tool $Line $limit"), 2)
        self.assertNotIn("$src.Read(", wrapper)
        self.assertIsNone(re.search(r"WaitForExit\(\s*\)", body), "a wait without a limit")
        hung = function_text("Stop-Hung")
        self.assertIn("if (-not $Process.HasExited) { $Process.Kill() }", hung)
        self.assertIn("[void]$Process.WaitForExit(5000)", hung)     # Kill() returns before the process is gone
        self.assertLess(hung.index("$Process.Kill()"), hung.index("WaitForExit(5000)"))
        self.assertLess(hung.index("WaitForExit(5000)"), hung.index("throw"))
        for needle in ("timed out: ", "did not finish within $Seconds seconds and was killed", "Nothing was written to the phone",
                       "hold Power until the screen is dark", "-TimeoutSec"):
            with self.subTest(needle=needle):
                self.assertIn(needle, hung)
        self.assertIn("[ValidateRange(2, 3600)][int]$TimeoutSec = 180", body)
        self.assertIn("$script:TimeoutSec = $TimeoutSec", body)

    def test_the_serial_is_checked_with_an_end_of_string_anchor_in_both_places(self):
        # "$" also matches before a final line feed, so a serial with a trailing newline would pass it.
        body = code()
        self.assertEqual(body.count("'^[A-Za-z0-9._:-]+\\z'"), 2)
        self.assertNotIn("[A-Za-z0-9._:-]+$'", body)

    def test_inventory_stops_on_a_layout_that_is_not_the_recorded_one(self):
        plan = function_text("Build-Plan")
        for needle in ("' does not resolve to '", "' is not ' + $p.Size + ' bytes'", "'unmounted'", "'names'", "'cmdline'"):
            with self.subTest(needle=needle):
                self.assertIn(needle, plan)
        for name, node, size in PARTS:
            with self.subTest(part=name):
                self.assertRegex(text(), r"Name = '%s'; Node = '%s'; Size = \[long\]%d;" % (name, node, size))
        check = function_text("Confirm-Step")
        self.assertIn("is mounted", check)
        self.assertIn("androidboot\\.slot_suffix=_b", check)
        self.assertIn("if ($state -cne $s.Value) { return \"the device state is", check)
        self.assertIn("the script does not change modes", check)
        self.assertIn("if ($targets -contains $f) { return", check)
        self.assertIn("if ($t -ceq [string]$s.Value) { return $null }", check)

    def test_output_is_kept_out_of_git_and_dry_run_touches_nothing(self):
        body = code()
        self.assertIn("Join-Path (Get-Location).Path 'duo-persist-probe'", body)
        refuse = body.index("is inside the git working tree")
        for later in ("Resolve-Tool 'adb.exe'", "New-Item -ItemType Directory", "if ($DryRun)", "Invoke-Step $s"):
            with self.subTest(later=later):
                self.assertLess(refuse, body.index(later))
        self.assertIn("Find-GitRoot $OutDir", body)
        self.assertIn("(Join-Path $d '.git')", function_text("Find-GitRoot"))
        dry = body[body.index("if ($DryRun)"):body.index("$script:Exe = @{}")]
        self.assertIn("exit 0", dry)
        self.assertNotIn("New-Item", dry)
        self.assertNotIn("Resolve-Tool", dry)
        self.assertNotIn("Invoke-Allowed", dry)
        self.assertLess(body.index("if ($DryRun)"), body.index("Resolve-Tool 'adb.exe'"))
        self.assertLess(body.index("if ($DryRun)"), body.index("Resolve-Tool 'fastboot.exe'"))
        self.assertLess(body.index("if ($DryRun)"), body.index("New-Item -ItemType Directory -Path (Join-Path $RunDir 'raw')"))

    def test_failure_path_says_nothing_was_written_and_exits_non_zero(self):
        body = code()
        tail = body[body.index("} catch {"):]
        self.assertIn("exit 1", tail)
        self.assertIn("Nothing was written to the phone", tail)
        self.assertIn("Nothing was written to the phone", function_text("Build-Report"))
        self.assertIn("nothing_written_to_phone = $true", body)
        self.assertIn("The script stopped at the first failure and did nothing after it.", function_text("Build-Report"))
        self.assertIn("STOPPED at $stopId", tail)

    def test_pins_match_the_install_chain_and_the_known_images_are_listed(self):
        boot = BOOT_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("$PortBootSha = '%s'" % PINNED["boot.img"], text())
        self.assertIn("$BrakeSha = '%s'" % PINNED["misc-brake.img"], text())
        for name, digest in PINNED.items():
            with self.subTest(name=name):
                self.assertIn("'%s'" % digest, boot)
        known = re.findall(r"^    '([0-9a-f]{64})' = '([^']*)'$", text(), re.M)
        self.assertEqual(len(known), 8)
        digests = dict(known)
        self.assertIn("11a3e3979b92664d1900ef88dffe265fa94ebff865002225e6f33d9cb7867c39", digests)
        self.assertIn("bdd438e8fb798344f465deb275e821a9ae8399bd421c90dce7ec7042c02c873f", digests)
        self.assertIn("cf6baf1d7940f2bf3d982e2645e251c0d7edcf89de4e0a6a7ff8ea620cdf8094", digests)

    def test_no_private_paths_or_addresses_and_the_guard_accepts_it(self):
        for path in (SCRIPT, RUNBOOK, FAKE_CS, Path(__file__)):
            for pattern in PRIVATE:
                with self.subTest(file=path.name, pattern=pattern):
                    self.assertIsNone(re.search(pattern, path.read_text(encoding="utf-8")))
        import importlib.util
        spec = importlib.util.spec_from_file_location("guard_check", ROOT / "guard" / "check.py")
        check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check)
        self.assertEqual(check.meta_only_errors("deploy/duo-persist-probe.ps1", text()), [])


# ---------------------------------------------------------------------------------------------------------------------
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


@functools.lru_cache(maxsize=None)
def powershells():
    """{major version: path} of every PowerShell that starts here (pwsh and powershell, with or without .exe)."""
    found = {}
    for name in ("pwsh", "pwsh.exe", "powershell", "powershell.exe"):
        path = shutil.which(name)
        if not path:
            continue
        try:
            done = subprocess.run([path, "-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.Major"],
                                  capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        digits = done.stdout.decode("utf-8", "replace").strip()
        if done.returncode == 0 and digits.isdigit():
            found.setdefault(int(digits), path)
    return found


@functools.lru_cache(maxsize=None)
def windows_powershell():
    """Windows PowerShell 5.1 can compile the stand-in tools (Add-Type -OutputAssembly); PowerShell 7 cannot."""
    return powershells().get(5)


def run_powershell(program, timeout=600, stdin=None, exe=None):
    """Run a PowerShell program (the script text is on its stdin); its stdout is one JSON document."""
    program = "$ProgressPreference = 'SilentlyContinue'\n" + program
    encoded = base64.b64encode(program.encode("utf-16-le")).decode("ascii")
    done = subprocess.run([exe or powershell(), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                          input=SCRIPT.read_bytes() if stdin is None else stdin, capture_output=True, timeout=timeout)
    out = done.stdout.decode("utf-8", "replace")
    if done.returncode != 0 or not out.strip():
        raise AssertionError("powershell exit %s\nstdout: %s\nstderr: %s" % (
            done.returncode, out[-2000:], done.stderr.decode("utf-8", "replace")[-2000:]))
    return json.loads(out[out.index("{"):])


# The script's definitions (everything before the main section) in this session, with a function to call them.
PART = "@@@@PART@@@@"   # separates the pieces of stdin: the script text, then what a program needs besides it

HEAD = r"""
$all = [Console]::In.ReadToEnd()
$parts = $all -split '@@@@PART@@@@'
$text = $parts[0]
$extra = $(if ($parts.Count -gt 1) { $parts[1] } else { '' })
$cut = $text.IndexOf('# ---- main ----')
if ($cut -lt 0) { throw 'harness: no main marker' }
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$tokens, [ref]$errors)
. ([scriptblock]::Create($text.Substring(0, $cut)))
"""

PARSE = r"""
$text = [Console]::In.ReadToEnd()
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$tokens, [ref]$errors)
$names = @($ast.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
$sw = @($ast.ParamBlock.Parameters | Where-Object { $_.StaticType.Name -eq 'SwitchParameter' } | ForEach-Object { $_.Name.VariablePath.UserPath })
$mandatory = @($ast.ParamBlock.Parameters | Where-Object { $_.Attributes | Where-Object { $_.TypeName.Name -eq 'Parameter' } } | ForEach-Object { $_.Name.VariablePath.UserPath })
$calls = @($ast.FindAll({ param($a) $a -is [System.Management.Automation.Language.CommandAst] -and $a.GetCommandName() -match '^(adb|fastboot)(\.exe)?$' }, $true)).Count
[pscustomobject]@{ errors = @($errors | ForEach-Object { $_.Message }); names = $names; switches = $sw; mandatory = $mandatory
                   direct_tool_calls = $calls; version = "$($PSVersionTable.PSVersion)" } | ConvertTo-Json -Compress -Depth 4
"""

# Command lines the allow-list must refuse (tool, line), each a way to write, change state or read what it should not.
BAD = [
    ("adb", l) for l in (
        "shell dd if=/dev/zero of=/dev/block/sde26 bs=4096", "shell dd if=/tmp/x of=/dev/block/sde26 bs=4096",
        "shell dd if=/dev/block/sde9 of=/dev/block/sde26 bs=4096", "shell dd if=/dev/block/sde9 bs=4096",
        "exec-out dd if=/dev/block/sde9 bs=4096 count=1 2>/dev/null; reboot",
        "exec-out dd if=/dev/block/sde9 bs=4096 count=1 of=/dev/block/sde26 2>/dev/null",
        "exec-out dd if=/dev/block/sde9 bs=4096 count=1 2>/dev/block/sde26",
        "exec-out dd if=/dev/block/sde9 bs=4096 count=1", "exec-out dd if=/dev/block/sde8 bs=4096 count=1 2>/dev/null",
        "exec-out dd if=/dev/block/sda6 bs=4096 count=1 2>/dev/null", "exec-out dd if=/dev/block/sdb1 bs=4096 count=1 2>/dev/null",
        "exec-out dd if=/dev/block/by-name/userdata bs=4096 count=1 2>/dev/null",
        "exec-out dd if=/dev/zero bs=4096 count=1 2>/dev/null", "exec-out dd if=/dev/block/sde9 bs=4096 count=1 2>/dev/null\n",
        # dd options a pull must not carry: a write position, a conversion, a flag, a status (BSD dd has none of the last two).
        "exec-out dd if=/dev/block/sde9 bs=4096 seek=1 count=1 2>/dev/null",
        "exec-out dd if=/dev/block/sde9 bs=4096 count=1 seek=1 2>/dev/null",
        "exec-out dd if=/dev/block/sde9 bs=4096 skip=1 seek=1 count=1 2>/dev/null",
        "exec-out dd if=/dev/block/sde9 bs=4096 count=1 conv=notrunc 2>/dev/null",
        "exec-out dd if=/dev/block/sde9 bs=4096 count=1 conv=sync 2>/dev/null",
        "exec-out dd if=/dev/block/sde9 bs=4096 count=1 oflag=direct 2>/dev/null",
        "exec-out dd if=/dev/block/sde9 bs=4096 count=1 iflag=direct 2>/dev/null",
        "exec-out dd if=/dev/block/sde9 bs=4096 count=1 status=progress 2>/dev/null",
        "exec-out dd if=/dev/block/sde9 ibs=4096 obs=4096 count=1 2>/dev/null",
        "exec-out dd if=/dev/block/sde9 bs=4k count=1 2>/dev/null",
        "shell dd if=/dev/block/sde9 bs=4096 seek=1 count=1", "shell dd if=/dev/block/sde9 bs=4096 count=1 conv=notrunc",
        # head is allowed only as a pull through exec-out, with a decimal byte count and a listed device.
        "exec-out head -c 99999999999 /dev/block/sde9", "exec-out head -c 4096 /dev/block/sde8",
        "exec-out head -c 4096 /dev/block/by-name/userdata", "exec-out head -c 4096 /dev/zero", "exec-out head -c 4096",
        "exec-out head -n 1 /dev/block/sde9", "exec-out head -c 4096 /dev/block/sde9 /dev/block/sde26",
        "exec-out head -c 4096 /dev/block/sde9 > /dev/block/sde26", "exec-out head -c 4096 /dev/block/sde9 2>/dev/null",
        "exec-out head -c 4096 /dev/block/sde9\n", "exec-out head -c 0x10 /dev/block/sde9", "exec-out head -c -1 /dev/block/sde9",
        "shell head -c 4096 /dev/block/sde9", "exec-out tail -c 4096 /dev/block/sde9",
        # The pinned TWRP has no bootctl and the plan never asks for getprop: neither is on the list any more.
        "shell bootctl hal-info", "shell bootctl get-number-slots", "shell bootctl get-current-slot",
        "shell bootctl is-slot-bootable 1", "shell bootctl get-suffix 0", "shell getprop ro.boot.slot_suffix",
        "shell df", "shell df /data", "shell df -h /tmp", "shell df /tmp /data", "shell df /tmp\n", "exec-out df /tmp",
        "exec-out cat /dev/block/sde9", "exec-out", "exec-out dd", "exec-in dd of=/dev/block/sde26",
        "shell setenforce 0", "shell reboot", "shell reboot bootloader", "shell rm -rf /data", "shell mount -o rw /dev/block/sde26 /mnt",
        "shell umount /data", "shell sgdisk -o /dev/block/sde", "shell sgdisk -d 26 /dev/block/sde", "shell sgdisk -p /dev/block/sdb",
        "shell sgdisk -i 9 /dev/block/sdb", "shell bootctl set-active-boot-slot 1", "shell bootctl mark-boot-successful",
        "shell bootctl set-slot-as-unbootable 0", "shell bootctl is-slot-bootable 2", "shell blockdev --setrw /dev/block/sde26",
        "shell blockdev --setro /dev/block/sde26", "shell blockdev --rereadpt /dev/block/sde", "shell blockdev --getro /dev/block/sde8",
        "shell cat /dev/block/sde9", "shell cat /data/rootfs.img", "shell getprop ro.x; reboot", "shell getprop ro.x && id",
        "shell id | tee /dev/block/sde26", "shell echo 3 > /proc/sys/vm/drop_caches", "shell sha256sum /dev/block/sda6",
        "shell sha256sum /dev/block/sde", "shell sha256sum /dev/block/by-name/userdata", "shell readlink -f /dev/block/by-name/userdata",
        "shell ls -l /data", "shell ls -l /dev/block/by-name", "shell dmesg -c", "shell dmesg -C", "shell getprop", "shell id\n",
        "shell id ", "SHELL id", "shell  id", "shell", "shell ", "shell cat /proc/cmdline > /sdcard/x", 'shell "id"',
        "shell cat /sys/class/block/sdb1/ro", "shell cat /sys/class/power_supply/battery/status", "shell cat /proc/kmsg",
        "shell sh -c id", "shell su -c id", "push a b", "pull a b", "reboot bootloader", "reboot", "install x.apk",
        "sideload x.zip", "root", "remount", "devices -l", "get-state x", "shell getprop é",
    )
] + [
    ("fastboot", l) for l in (
        "flash boot_a x.img", "flash boot x.img", "erase misc", "format:ext4 userdata", "format userdata", "boot x.img",
        "set_active b", "oem device-info", "oem unlock", "flashing unlock", "flashing lock", "flashing unlock_critical",
        "reboot", "reboot bootloader", "reboot-fastboot", "continue", "getvar all; erase misc", "getvar product && reboot",
        "getvar", "getvar ", "getvar  product", "getvar PRODUCT", "getvar product x", "getvar:product", "devices -l",
        "stage x", "getvar $(reboot)", "snapshot-update cancel", "create-logical-partition x", "delete-logical-partition x",
        "wipe-super", "update x.zip", "flashall", "fetch boot x", "gsi wipe", "getvar product\n", "devices\n",
    )
] + [("cmd", "id"), ("powershell", "id"), ("", "devices"), ("pc", "devices"), ("ADB", "devices")]

GOOD = [("adb", l) for l in (
    "devices", "get-state", "shell id", "shell cat /proc/cmdline", "shell cat /proc/mounts", "shell df /tmp",
    "shell ls -l /dev/block/platform/soc/1d84000.ufshc/by-name", "shell readlink -f /dev/block/platform/soc/1d84000.ufshc/by-name/boot_b",
    "shell readlink -f /dev/block/by-name/misc", "shell blockdev --getsize64 /dev/block/sde26", "shell blockdev --getro /dev/block/sde",
    "shell sha256sum /dev/block/sde9", "shell sha256sum /dev/block/by-name/vbmeta_a", "shell sgdisk -p /dev/block/sde",
    "shell sgdisk -i 26 /dev/block/sde",
    "shell cat /sys/class/block/sde26/ro", "shell cat /sys/class/block/sde/size", "shell dmesg", "shell getenforce",
    "exec-out dd if=/dev/block/sde26 bs=4096 count=24576 2>/dev/null", "exec-out dd if=/dev/block/sde bs=4096 count=256 2>/dev/null",
    "exec-out dd if=/dev/block/sda5 bs=4096 skip=0 count=256 2>/dev/null",
    "exec-out head -c 100663296 /dev/block/sde26", "exec-out head -c 1048576 /dev/block/sde",
    "exec-out head -c 1048576 /dev/block/sda5", "exec-out head -c 65536 /dev/block/by-name/vbmeta_a",
)] + [("fastboot", l) for l in ("devices", "getvar all", "getvar product", "getvar partition-size:boot_a", "getvar slot-retry-count:b",
                                "getvar has-slot:boot", "getvar battery-level")]

ALLOW = HEAD + r"""
$corpus = $extra | ConvertFrom-Json
$verdicts = @($corpus | ForEach-Object { [pscustomobject]@{ tool = [string]$_.tool; line = [string]$_.line; allowed = [bool](Test-Allowed ([string]$_.tool) ([string]$_.line)) } })
$plans = [ordered]@{}
foreach ($ph in 'twrp', 'bootloader', 'twrp-head') {
    $plan = @($(if ($ph -eq 'twrp-head') { Build-Plan 'twrp' $true 'head' } else { Build-Plan $ph ($ph -eq 'twrp') }))
    $plans[$ph] = @($plan | ForEach-Object {
        [pscustomobject]@{ id = [string]$_.Id; gate = [string]$_.Gate; tool = [string]$_.Tool; line = [string]$_.Line; mode = [string]$_.Mode
                           out = [string]$_.Out; required = [bool]$_.Required; allowed = $(if ($_.Tool -eq 'pc') { $true } else { [bool](Test-Allowed $_.Tool $_.Line) }) }
    })
}
[pscustomobject]@{ verdicts = $verdicts; twrp = $plans['twrp']; bootloader = $plans['bootloader']; twrp_head = $plans['twrp-head'] } | ConvertTo-Json -Compress -Depth 5
"""

# Confirm-Step on made-up answers: each case is {kind, text, ...step fields}; the result is the message or null.
CHECKS = HEAD + r"""
$script:Present = @{}; $script:Results = @{}; $script:Serial = ''
$cases = $extra | ConvertFrom-Json
$out = @()
foreach ($c in $cases) {
    $script:Serial = [string]$c.serial
    $script:Present = @{}
    $more = @{}
    foreach ($prop in $c.step.PSObject.Properties) { $more[$prop.Name] = $prop.Value }
    $s = New-Step 'x' 'G0' 'adb' 'devices' ([string]$c.kind) $true $more
    $r = [pscustomobject]@{ Code = 0; Text = [string]$c.text; Stderr = ''; Bytes = [long]0; Sha256 = ''; Value = $null }
    $msg = Confirm-Step $s $r
    $out += [pscustomobject]@{ label = [string]$c.label; message = $(if ($msg) { [string]$msg } else { $null }); serial = $script:Serial
                               present = ($script:Present.Keys | Sort-Object | ForEach-Object { $_ + '=' + $script:Present[$_] }) }
}
[pscustomobject]@{ results = @($out) } | ConvertTo-Json -Compress -Depth 5
"""

CANDIDATE = HEAD + r"""
$dir = Join-Path ([IO.Path]::GetTempPath()) ('opos-persist-cand-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $dir | Out-Null
try {
    $out = @()
    foreach ($case in @(@('good', 'ANDROID!', 2, 1000), @('padded', 'ANDROID!', 2, 4096), @('toolarge', 'ANDROID!', 2, 4097), @('notimage', 'MZ......', 0, 1000))) {
        $b = New-Object byte[] $case[3]
        [Text.Encoding]::ASCII.GetBytes($case[1]).CopyTo($b, 0); $b[40] = [byte]$case[2]
        $p = Join-Path $dir $case[0]; [IO.File]::WriteAllBytes($p, $b)
        $c = Test-Candidate $p ([long]4096)
        $s = New-Step 't08' 'G2' 'pc' 'check' 'candidate' $true $null
        $r = [pscustomobject]@{ Code = 0; Text = ''; Stderr = ''; Bytes = [long]0; Sha256 = ''; Value = $null; Candidate = $c }
        $msg = Confirm-Step $s $r
        $out += [pscustomobject]@{ label = $case[0]; magic = $c.Magic; version = $c.HeaderVersion; fits = $c.Fits; padded = $c.PartitionSized; pinned = $c.PinnedPortImage; message = $(if ($msg) { [string]$msg } else { $null }) }
    }
    [pscustomobject]@{ results = @($out) } | ConvertTo-Json -Compress -Depth 4
} finally { Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue }
"""

# The whole script against the stand-in tools. $runs is a JSON list of scenarios; each one starts from its own folders.
FAKE_RUN = r"""
$ErrorActionPreference = 'Stop'
$all = [Console]::In.ReadToEnd()
$parts = $all -split '@@@@PART@@@@'
$text = $parts[0]
$cs = $parts[1]
$runs = $parts[2]
$work = Join-Path ([IO.Path]::GetTempPath()) ('opos-persist-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$origPath = $env:PATH
$origDir = (Get-Location).Path
$results = @()
try {
    $bin = Join-Path $work 'bin'; $empty = Join-Path $work 'empty'; $plain = Join-Path $work 'plain'
    $repo = Join-Path $work 'repo'; $repo2 = Join-Path $work 'repo2'
    foreach ($d in $bin, $empty, $plain, $repo, $repo2) { New-Item -ItemType Directory -Path $d | Out-Null }
    New-Item -ItemType Directory -Path (Join-Path $repo '.git') | Out-Null
    [IO.File]::WriteAllText((Join-Path $repo2 '.git'), 'gitdir: elsewhere')
    Add-Type -TypeDefinition $cs -OutputAssembly (Join-Path $bin 'adb.exe') -OutputType ConsoleApplication
    Copy-Item (Join-Path $bin 'adb.exe') (Join-Path $bin 'fastboot.exe')
    $script = Join-Path $work 'duo-persist-probe.ps1'
    [IO.File]::WriteAllText($script, $text, (New-Object Text.UTF8Encoding($false)))
    $img = Join-Path $work 'boot.img'
    $b = New-Object byte[] 1048576; [Text.Encoding]::ASCII.GetBytes('ANDROID!').CopyTo($b, 0); $b[40] = 2
    [IO.File]::WriteAllBytes($img, $b)
    $i = 0
    foreach ($run in ($runs | ConvertFrom-Json)) {
        $i++
        Get-ChildItem env: | Where-Object { $_.Name -like 'FAKE_*' } | ForEach-Object { Remove-Item -LiteralPath ('env:' + $_.Name) }
        $log = Join-Path $work ("calls-$i.txt")
        $env:FAKE_LOG = $log
        if ($run.env) { foreach ($e in $run.env.PSObject.Properties) { Set-Item -LiteralPath ('env:' + $e.Name) -Value ([string]$e.Value) } }
        $out = Join-Path $work ("out-$i")
        $p = @{ Phase = [string]$run.phase }
        if ($run.out -eq 'dir') { $p.OutDir = $out }
        elseif ($run.out -eq 'inside-repo') { $p.OutDir = (Join-Path $repo 'sub\out') }
        elseif ($run.out -eq 'inside-repo2') { $p.OutDir = (Join-Path $repo2 'x') }
        if ($run.dry) { $p.DryRun = $true }
        if ($run.serial) { $p.Serial = [string]$run.serial }
        if ($run.image) { $p.BootImage = $img }
        if ($run.pullwith) { $p.PullWith = [string]$run.pullwith }
        if ($run.timeout) { $p.TimeoutSec = [int]$run.timeout }
        $env:PATH = $(if ($run.nopath) { $empty } else { $bin })
        Set-Location $(if ($run.cwd -eq 'repo') { $repo } else { $plain })
        $global:LASTEXITCODE = $null
        $threw = $null; $said = ''
        $clock = [Diagnostics.Stopwatch]::StartNew()
        try { $said = (& $script @p *>&1 | Out-String -Width 4000) } catch { $threw = $_.Exception.Message }
        $seconds = $clock.Elapsed.TotalSeconds
        $code = $global:LASTEXITCODE
        Set-Location $work; $env:PATH = $origPath
        $left = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -in 'adb', 'fastboot' -and $_.Path -and $_.Path.StartsWith($bin) }).Count
        $folders = @()
        if (Test-Path -LiteralPath $out) {
            foreach ($d in (Get-ChildItem -LiteralPath $out -Directory)) {
                $files = @(Get-ChildItem -LiteralPath $d.FullName -File -Recurse | ForEach-Object { [pscustomobject]@{ name = $_.FullName.Substring($d.FullName.Length + 1); length = $_.Length } })
                $rd = $d.FullName
                $rep = Join-Path $rd 'report.md'; $pj = Join-Path $rd 'probe.json'; $sm = Join-Path $rd 'SHA256SUMS'
                $folders += [pscustomobject]@{ name = $d.Name; files = $files
                    report = $(if (Test-Path $rep) { [IO.File]::ReadAllText($rep) } else { $null })
                    probe = $(if (Test-Path $pj) { [IO.File]::ReadAllText($pj) } else { $null })
                    sums = $(if (Test-Path $sm) { [IO.File]::ReadAllText($sm) } else { $null })
                    disk = @($files | Where-Object { $_.name -match '\.(img|bin)$' } | ForEach-Object { [pscustomobject]@{ name = $_.name; sha = (Get-FileHash -LiteralPath (Join-Path $rd $_.name) -Algorithm SHA256).Hash.ToLower() } }) }
            }
        }
        $results += [pscustomobject]@{ label = [string]$run.label; threw = $threw; exit = $code; said = $said; seconds = $seconds; left_running = $left
            calls = @(if (Test-Path -LiteralPath $log) { [IO.File]::ReadAllLines($log) })
            folders = @($folders); out_exists = [bool](Test-Path -LiteralPath $out)
            default_dir = [bool](Test-Path -LiteralPath (Join-Path $plain 'duo-persist-probe')) }
        Remove-Item -LiteralPath (Join-Path $plain 'duo-persist-probe') -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $out -Recurse -Force -ErrorAction SilentlyContinue
    }
    [pscustomobject]@{ runs = @($results) } | ConvertTo-Json -Compress -Depth 9
} finally {
    Set-Location $origDir
    $env:PATH = $origPath
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
"""


def stdin_with(*extra):
    """The script text, then each extra piece after the separator (a command line is too short for them)."""
    return SCRIPT.read_bytes() + b"".join(PART.encode("ascii") + piece for piece in extra)


def fake_run(runs, exe=None):
    return run_powershell(FAKE_RUN, timeout=1500, exe=exe, stdin=stdin_with(FAKE_CS.read_bytes(), json.dumps(runs).encode("ascii")))


def content_sha(idx, blocks):
    """The SHA-256 of the first `blocks` blocks of the stand-in's deterministic content for partition index idx."""
    base = bytes((i * 7) & 0xFF for i in range(4096))
    tables = {}
    digest = hashlib.sha256()
    for k in range(blocks):
        shift = (idx * 13 + (k & 0xFF)) & 0xFF
        table = tables.get(shift)
        if table is None:
            table = tables[shift] = bytes((x + shift) & 0xFF for x in range(256))
        block = bytearray(base.translate(table))
        block[0:4] = b"\x0d\x0a\x1a\x00"
        block[4:8] = bytes((k & 0xFF, (k >> 8) & 0xFF, (k >> 16) & 0xFF, 0x0A))
        if idx == 6 and k == 0:
            block[0:2048] = bytes(2048)
        digest.update(block)
    return digest.hexdigest()


def dry_lines(said):
    """[(gate, id, command)] from the lines a dry run prints."""
    found = re.findall(r"(?m)^\[(G\d)\] ([^:]+): (.*?)(?:   (?:->|\().*)?\s*$", said)
    return [(g, i, c.strip()) for g, i, c in found]


@unittest.skipUnless(powershell(), "needs pwsh or powershell to parse the script")
class PersistProbePowerShell(unittest.TestCase):
    def test_parses_without_errors_and_has_the_documented_parameters(self):
        got = run_powershell(PARSE)
        self.assertEqual(got["errors"], [], got["version"])
        self.assertEqual(sorted(got["names"]), ["BootImage", "DryRun", "OutDir", "Phase", "PullWith", "Serial", "TimeoutSec"])
        self.assertEqual(as_list(got["switches"]), ["DryRun"])
        self.assertEqual(as_list(got["mandatory"]), [])
        self.assertEqual(got["direct_tool_calls"], 0, "adb or fastboot is run by name somewhere")

    def test_the_allow_list_refuses_every_writing_or_state_changing_line_and_accepts_the_reads(self):
        corpus = [{"tool": t, "line": l} for t, l in BAD + GOOD]
        got = run_powershell(ALLOW, stdin=stdin_with(json.dumps(corpus).encode("ascii")))
        verdicts = as_list(got["verdicts"])
        self.assertEqual(len(verdicts), len(corpus))
        accepted_bad = [v for v, (t, l) in zip(verdicts[:len(BAD)], BAD) if v["allowed"]]
        self.assertEqual(accepted_bad, [], "the allow-list accepted a line it must refuse")
        refused_good = [v for v in verdicts[len(BAD):] if not v["allowed"]]
        self.assertEqual(refused_good, [], "the allow-list refused a read the plan needs")

    def test_the_wrapper_refuses_a_bad_line_before_it_starts_any_process(self):
        # The tools are pointed at files that do not exist: a line that passes the allow-list fails when the process
        # starts ("cannot find"), a line that does not pass is refused before that point.
        program = HEAD + r"""
$script:Exe = @{ adb = (Join-Path ([IO.Path]::GetTempPath()) 'no-such-dir\adb.exe'); fastboot = (Join-Path ([IO.Path]::GetTempPath()) 'no-such-dir\fastboot.exe') }
$script:Serial = 'SER1'
$corpus = $extra | ConvertFrom-Json
$out = @()
foreach ($c in $corpus) {
    $msg = 'ran'
    try { [void](Invoke-Allowed ([string]$c.tool) ([string]$c.line)) } catch { $msg = $_.Exception.Message }
    $out += [pscustomobject]@{ tool = [string]$c.tool; line = [string]$c.line; message = $msg }
}
[pscustomobject]@{ results = @($out) } | ConvertTo-Json -Compress -Depth 4
"""
        corpus = [{"tool": t, "line": l} for t, l in BAD + GOOD[:3] + GOOD[-3:]]
        got = as_list(run_powershell(program, stdin=stdin_with(json.dumps(corpus).encode("ascii")))["results"])
        self.assertEqual(len(got), len(corpus))
        for item, (tool, line) in zip(got[:len(BAD)], BAD):
            with self.subTest(tool=tool, line=line):
                self.assertTrue(item["message"].startswith("refused: not on the allow-list"), item["message"])
        for item in got[len(BAD):]:
            with self.subTest(allowed=item["line"]):
                self.assertFalse(item["message"].startswith("refused: not on the allow-list"), item["message"])
                self.assertNotEqual(item["message"], "ran")

    def test_a_serial_with_a_trailing_newline_or_other_odd_characters_is_refused_by_the_wrapper(self):
        program = HEAD + r"""
$script:Exe = @{ adb = (Join-Path ([IO.Path]::GetTempPath()) 'no-such-dir\adb.exe') }
$script:TimeoutSec = 5
$out = @()
foreach ($serial in ($extra | ConvertFrom-Json)) {
    $script:Serial = [string]$serial
    $msg = 'ran'
    try { [void](Invoke-Allowed 'adb' 'shell id') } catch { $msg = $_.Exception.Message }
    $out += [pscustomobject]@{ serial = [string]$serial; message = $msg }
}
[pscustomobject]@{ results = @($out) } | ConvertTo-Json -Compress -Depth 4
"""
        serials = ["SER1", "ab.c_d:1-2", "SER1\n", "SER1\r\n", "\nSER1", "SER 1", "SER1;id", "", "SER1\t", "é"]
        got = as_list(run_powershell(program, stdin=stdin_with(json.dumps(serials).encode("ascii")))["results"])
        self.assertEqual(len(got), len(serials))
        for item in got:
            refused = item["message"].startswith("refused: no valid device serial yet")
            with self.subTest(serial=item["serial"]):
                # A valid serial gets as far as starting the (missing) process; every other one is refused before that.
                self.assertEqual(refused, item["serial"] not in ("SER1", "ab.c_d:1-2"), item["message"])

    def test_every_planned_command_is_allowed_and_names_only_the_listed_partitions(self):
        got = run_powershell(ALLOW, stdin=stdin_with(b"[]"))
        for phase in ("twrp", "bootloader", "twrp_head"):
            plan = as_list(got[phase])
            with self.subTest(phase=phase):
                self.assertGreater(len(plan), 10)
                self.assertEqual([s["id"] for s in plan if not s["allowed"]], [])
                self.assertEqual(len({s["id"] for s in plan}), len(plan), "duplicate step ids")
        twrp = as_list(got["twrp"])
        self.assertEqual({s["tool"] for s in twrp}, {"adb", "pc"})
        self.assertEqual({s["tool"] for s in as_list(got["bootloader"])}, {"fastboot"})
        self.assertEqual([s["id"] for s in twrp if s["gate"] == "G0"][:3], ["t00", "t01", "t02"])
        # Gate order: preflight, then inventory and backup, then the probes.
        gates = [s["gate"] for s in twrp]
        self.assertEqual(gates, sorted(gates))
        pulls = {s["id"]: s for s in twrp if s["mode"] == "file"}
        for name, node, size in PARTS:
            with self.subTest(part=name):
                pull = pulls["k-%s-pull" % name]
                self.assertEqual(pull["out"], name + ".img")
                self.assertEqual(pull["line"], "exec-out dd if=/dev/block/%s bs=4096 count=%d 2>/dev/null" % (node, size // 4096))
                again = next(s for s in twrp if s["id"] == "k-%s-again" % name)
                self.assertEqual((again["mode"], again["line"]), ("hash", pull["line"]))
                self.assertEqual(next(s for s in twrp if s["id"] == "i-%s-size" % name)["line"], "shell blockdev --getsize64 /dev/block/" + node)
                self.assertEqual(next(s for s in twrp if s["id"] == "k-%s-sha" % name)["line"], "shell sha256sum /dev/block/" + node)
        self.assertEqual(pulls["k-gpt-pull"]["line"], "exec-out dd if=/dev/block/sde bs=4096 count=256 2>/dev/null")
        # -PullWith head: the same plan with the form the October 9 backup tool used, and no dd left in it.
        twrp_head = as_list(got["twrp_head"])
        self.assertEqual([s["id"] for s in twrp_head], [s["id"] for s in twrp])
        head_pulls = {s["id"]: s for s in twrp_head if s["mode"] == "file"}
        for name, node, size in PARTS:
            with self.subTest(head_part=name):
                self.assertEqual(head_pulls["k-%s-pull" % name]["line"], "exec-out head -c %d /dev/block/%s" % (size, node))
                self.assertEqual(next(s for s in twrp_head if s["id"] == "k-%s-again" % name)["line"], head_pulls["k-%s-pull" % name]["line"])
        self.assertEqual(head_pulls["k-gpt-pull"]["line"], "exec-out head -c 1048576 /dev/block/sde")
        self.assertFalse([s for s in twrp_head if "dd if=" in s["line"]])
        # The df probe is there (the room for the 96 MiB push in G3); bootctl and getprop are not probed at all.
        self.assertEqual(next(s for s in twrp if s["id"] == "p-df-tmp")["line"], "shell df /tmp")
        self.assertFalse([s for s in twrp + twrp_head if "bootctl" in s["line"] or "getprop" in s["line"]])
        # Nothing in the plan names a partition outside the list.
        for s in twrp:
            for node in re.findall(r"/dev/block/(sd[a-z]\d*)", s["line"]):
                self.assertIn(node, {n for _, n, _ in PARTS} | {"sde"}, s["line"])
        for s in twrp + twrp_head + as_list(got["bootloader"]):
            self.assertIsNone(re.search(FORBIDDEN_IN_A_COMMAND, s["line"]), s["line"])
        self.assertEqual([s["line"] for s in as_list(got["bootloader"])][:5],
                         ["devices", "getvar product", "getvar current-slot", "getvar unlocked", "getvar battery-level"])

    def test_each_check_stops_on_the_wrong_answer_and_passes_the_right_one(self):
        def case(label, kind, text_, serial="", **step):
            return {"label": label, "kind": kind, "text": text_, "serial": serial, "step": step}
        devices_ok = "List of devices attached\nSER1\trecovery\n\n"
        cases = [
            case("one device in recovery", "serial", devices_ok, "", Value="recovery"),
            case("daemon noise is ignored", "serial", "* daemon not running; starting now at tcp:5037\n* daemon started successfully\n" + devices_ok, "", Value="recovery"),
            case("device state", "serial", "List of devices attached\nSER1\tdevice\n", "", Value="recovery"),
            case("sideload state", "serial", "List of devices attached\nSER1\tsideload\n", "", Value="recovery"),
            case("unauthorized", "serial", "List of devices attached\nSER1\tunauthorized\n", "", Value="recovery"),
            case("two devices without a serial", "serial", devices_ok.replace("\n\n", "\nSER2\trecovery\n\n"), "", Value="recovery"),
            case("two devices with a serial", "serial", devices_ok.replace("\n\n", "\nSER2\trecovery\n\n"), "SER2", Value="recovery"),
            case("serial not listed", "serial", devices_ok, "SER9", Value="recovery"),
            case("no devices", "serial", "List of devices attached\n\n", "", Value="recovery"),
            case("fastboot device", "serial", "SER1\tfastboot\n", "", Value="fastboot"),
            case("recovery where fastboot is needed", "serial", "SER1\trecovery\n", "", Value="fastboot"),
            case("product ok", "match", "product: surfaceduo\nFinished. Total time: 0.1s", Pattern=r"\bproduct:\s*surfaceduo\b", Message="not a Surface Duo 1"),
            case("product other", "match", "product: other\nFinished.", Pattern=r"\bproduct:\s*surfaceduo\b", Message="not a Surface Duo 1"),
            case("battery 20", "minint", "battery-level: 20\nFinished.", Pattern=r"battery-level:\s*(\d+)", Value=20, Message="low"),
            case("battery 19", "minint", "battery-level: 19\nFinished.", Pattern=r"battery-level:\s*(\d+)", Value=20, Message="low"),
            case("battery 100", "minint", "battery-level: 100\nFinished.", Pattern=r"battery-level:\s*(\d+)", Value=20, Message="low"),
            case("battery 9", "minint", "battery-level: 9\nFinished.", Pattern=r"battery-level:\s*(\d+)", Value=20, Message="low"),
            case("battery unreadable", "minint", "battery-level: FAILED", Pattern=r"battery-level:\s*(\d+)", Value=20, Message="low"),
            case("link ok", "equals", "/dev/block/sde26", Value="/dev/block/sde26", Message="wrong link"),
            case("link other", "equals", "/dev/block/sde99", Value="/dev/block/sde26", Message="wrong link"),
            case("slot a", "cmdline", "x androidboot.slot_suffix=_a y"),
            case("slot b", "cmdline", "x androidboot.slot_suffix=_b y"),
            case("no slot word", "cmdline", "console=ttyMSM0"),
            case("all names", "names", "boot_a -> x\nboot_b -> x\nvbmeta_a -> x\nvbmeta_b -> x\ndtbo_a -> x\ndtbo_b -> x\nmisc -> x\n"),
            case("no dtbo", "names", "boot_a -> x\nboot_b -> x\nvbmeta_a -> x\nvbmeta_b -> x\nmisc -> x\n"),
            case("no misc", "names", "boot_a -> x\nboot_b -> x\nvbmeta_a -> x\nvbmeta_b -> x\ndtbo_a -> x\ndtbo_b -> x\n"),
            case("nothing mounted", "unmounted", "rootfs / rootfs rw 0 0\n/dev/block/sda6 /data ext4 rw 0 0\n"),
            case("boot_b mounted", "unmounted", "rootfs / rootfs rw 0 0\n/dev/block/sde26 /mnt ext4 rw 0 0\n"),
            case("LUN mounted", "unmounted", "/dev/block/sde /mnt ext4 rw 0 0\n"),
            case("by-name mounted", "unmounted", "/dev/block/platform/soc/1d84000.ufshc/by-name/misc /mnt ext4 rw 0 0\n"),
            case("battery ok", "battery", "80"), case("battery low in TWRP", "battery", "12"), case("battery unreadable in TWRP", "battery", "cat: not found"),
            case("sha ok", "sha", "a" * 64 + "  /dev/block/sde9"), case("sha missing", "sha", "sha256sum: not found"),
            case("info never fails", "info", "anything at all"),
        ]
        got = run_powershell(CHECKS, stdin=stdin_with(json.dumps(cases).encode("ascii")))
        by = {r["label"]: r for r in as_list(got["results"])}
        self.assertEqual(len(by), len(cases))
        ok = ["one device in recovery", "daemon noise is ignored", "two devices with a serial", "fastboot device", "product ok", "battery 20",
              "battery 100", "link ok", "slot a", "no slot word", "all names", "no dtbo", "nothing mounted", "battery ok",
              "battery unreadable in TWRP", "sha ok", "info never fails"]
        for label in ok:
            with self.subTest(passes=label):
                self.assertIsNone(by[label]["message"])
        for label in set(by) - set(ok):
            with self.subTest(stops=label):
                self.assertTrue(by[label]["message"], label)
        self.assertIn("the script does not change modes", by["device state"]["message"])
        self.assertIn("pass -Serial", by["two devices without a serial"]["message"])
        self.assertEqual(by["one device in recovery"]["serial"], "SER1")
        self.assertEqual(by["two devices with a serial"]["serial"], "SER2")
        self.assertIn("misc is not listed", by["no misc"]["message"])
        self.assertEqual(as_list(by["no dtbo"]["present"]), ["boot_a=True", "boot_b=True", "dtbo_a=False", "dtbo_b=False", "misc=True", "vbmeta_a=True", "vbmeta_b=True"])

    def test_candidate_image_check_is_pc_only_and_strict(self):
        got = {r["label"]: r for r in as_list(run_powershell(CANDIDATE)["results"])}
        self.assertIsNone(got["good"]["message"])
        self.assertEqual((got["good"]["magic"], got["good"]["version"], got["good"]["fits"], got["good"]["padded"]), ("ANDROID!", 2, True, False))
        self.assertIsNone(got["padded"]["message"])
        self.assertTrue(got["padded"]["padded"])
        self.assertIn("larger than", got["toolarge"]["message"])
        self.assertIn("ANDROID!", got["notimage"]["message"])
        self.assertFalse(got["good"]["pinned"])


@unittest.skipUnless(windows_powershell(), "needs Windows PowerShell 5.1 to compile the stand-in adb and fastboot")
class PersistProbeAgainstStandInTools(unittest.TestCase):
    """The whole script runs against tests/fixtures/fake_duo_tools.cs. No hardware is involved. One PowerShell session
    runs all scenarios, because compiling the stand-in and starting the session cost more than most scenarios."""

    @classmethod
    def setUpClass(cls):
        only = [x for x in os.environ.get("OPOS_PERSIST_SCENARIOS", "").split("|") if x]   # a subset, for a quick local run
        env = {"FAKE_NO_SGDISK": "1", "FAKE_NO_DTBO": "1", "FAKE_RO": "sde26"}
        runs = [
            {"label": "dry twrp", "phase": "twrp", "dry": True, "nopath": True, "out": "dir", "image": True},
            {"label": "dry bootloader", "phase": "bootloader", "dry": True, "nopath": True, "out": "dir"},
            {"label": "dry default folder", "phase": "twrp", "dry": True, "nopath": True},
            {"label": "refused in a repo", "phase": "twrp", "cwd": "repo"},
            {"label": "refused in a repo, dry", "phase": "twrp", "cwd": "repo", "dry": True, "nopath": True},
            {"label": "refused inside a repo by -OutDir", "phase": "twrp", "out": "inside-repo"},
            {"label": "refused inside a worktree", "phase": "twrp", "out": "inside-repo2"},
            {"label": "bootloader image on the wrong phase", "phase": "bootloader", "image": True, "out": "dir"},
            {"label": "twrp ok", "phase": "twrp", "out": "dir", "image": True},
            {"label": "twrp ok with a read-only flag and no extras", "phase": "twrp", "out": "dir", "env": env},
            {"label": "bootloader ok", "phase": "bootloader", "out": "dir"},
            {"label": "not in recovery", "phase": "twrp", "out": "dir", "env": {"FAKE_STATE": "device"}},
            {"label": "two devices", "phase": "twrp", "out": "dir", "env": {"FAKE_DEVICES": "2"}},
            {"label": "slot b", "phase": "twrp", "out": "dir", "env": {"FAKE_SLOT_B": "1"}},
            {"label": "mounted", "phase": "twrp", "out": "dir", "env": {"FAKE_MOUNT": "sde26"}},
            {"label": "wrong link", "phase": "twrp", "out": "dir", "env": {"FAKE_LINK_WRONG": "boot_b"}},
            {"label": "wrong size", "phase": "twrp", "out": "dir", "env": {"FAKE_BADSIZE": "sde26"}},
            {"label": "corrupt first read", "phase": "twrp", "out": "dir", "env": {"FAKE_CORRUPT_FIRST": "sde9"}},
            {"label": "corrupt second read", "phase": "twrp", "out": "dir", "env": {"FAKE_CORRUPT_SECOND": "sde9"}},
            {"label": "short read", "phase": "twrp", "out": "dir", "env": {"FAKE_SHORT": "sde9"}},
            {"label": "other product", "phase": "bootloader", "out": "dir", "env": {"FAKE_PRODUCT": "other"}},
            {"label": "slot b in the bootloader", "phase": "bootloader", "out": "dir", "env": {"FAKE_SLOT": "b"}},
            {"label": "locked", "phase": "bootloader", "out": "dir", "env": {"FAKE_UNLOCKED": "no"}},
            {"label": "battery 19", "phase": "bootloader", "out": "dir", "env": {"FAKE_BATTERY": "19"}},
            {"label": "battery 20", "phase": "bootloader", "out": "dir", "env": {"FAKE_BATTERY": "20"}},
            {"label": "dry twrp head", "phase": "twrp", "dry": True, "nopath": True, "out": "dir", "image": True, "pullwith": "head"},
            {"label": "twrp ok with head pulls", "phase": "twrp", "out": "dir", "image": True, "pullwith": "head"},
            {"label": "head short read", "phase": "twrp", "out": "dir", "pullwith": "head", "env": {"FAKE_SHORT": "sde9"}},
            {"label": "head pull on the wrong phase", "phase": "bootloader", "pullwith": "head", "out": "dir"},
            {"label": "serial with a trailing newline", "phase": "twrp", "serial": "SER1\n", "out": "dir"},
            {"label": "hang in the bootloader", "phase": "bootloader", "out": "dir", "timeout": 3,
             "env": {"FAKE_HANG": "getvar battery-level"}},
            {"label": "hang on the first command", "phase": "twrp", "out": "dir", "timeout": 3, "env": {"FAKE_HANG": "devices"}},
            {"label": "hang during a pull", "phase": "twrp", "out": "dir", "timeout": 3,
             "env": {"FAKE_HANG": "exec-out dd if=/dev/block/sde9 bs=4096 count=24576 2>/dev/null"}},
        ]
        runs = [r for r in runs if not only or r["label"] in only]
        cls.labels = [r["label"] for r in runs]
        cls.runs = {r["label"]: r for r in fake_run(runs)["runs"]}

    def run_of(self, label):
        if label not in self.runs:
            self.skipTest("scenario %r was not run (OPOS_PERSIST_SCENARIOS)" % label)
        return self.runs[label]

    def present(self, labels):
        return [label for label in labels if label in self.runs]

    def folder(self, label):
        folders = as_list(self.run_of(label)["folders"])
        self.assertEqual(len(folders), 1, label)
        return folders[0]

    def probe(self, label):
        return json.loads(self.folder(label)["probe"])

    def steps(self, label):
        return {s["id"]: s for s in as_list(self.probe(label)["steps"])}

    def test_dry_run_prints_the_plan_runs_nothing_and_creates_nothing(self):
        for label, phase in (("dry twrp", "twrp"), ("dry bootloader", "bootloader")):
            if label not in self.runs:
                continue
            run = self.run_of(label)
            with self.subTest(label=label):
                self.assertIsNone(run["threw"])
                self.assertEqual(run["exit"], 0)
                self.assertEqual(as_list(run["calls"]), [], "a tool was started")
                self.assertFalse(run["out_exists"], "the output folder was created")
                self.assertIn("DRY RUN, phase %s. Nothing was run: no tool was looked up and no phone was contacted." % phase, run["said"])
                self.assertGreater(len(dry_lines(run["said"])), 10)
        if "dry default folder" not in self.runs:
            return
        folder = self.run_of("dry default folder")
        self.assertFalse(folder["default_dir"], "the default folder was created by a dry run")
        self.assertRegex(folder["said"], r"Output folder it would create: .*[\\/]plain[\\/]duo-persist-probe[\\/]\d{8}-\d{6}-twrp")

    def test_the_commands_that_run_are_the_commands_the_dry_run_prints(self):
        for phase, dry_label, run_label in (("twrp", "dry twrp", "twrp ok"), ("bootloader", "dry bootloader", "bootloader ok")):
            if dry_label not in self.runs or run_label not in self.runs:
                continue
            with self.subTest(phase=phase):
                planned = [c for _, _, c in dry_lines(self.run_of(dry_label)["said"]) if not c.startswith("(PC only)")]
                ran = as_list(self.run_of(run_label)["calls"])
                self.assertEqual(ran, planned)
                tool = "adb" if phase == "twrp" else "fastboot"
                self.assertTrue(all(c.startswith(tool) for c in ran))

    def test_no_run_issues_a_command_outside_the_allow_list(self):
        allowed = re.compile(r"^(adb|fastboot) (-s <serial> )?(devices|get-state|getvar [a-z][a-z0-9:_-]*|shell (id|getenforce|dmesg|cat /(proc|sys)/[a-z0-9_/.]+|ls /sys/class/power_supply|ls -l /dev/block/platform/soc/1d84000\.ufshc/by-name|df /tmp|readlink -f /dev/block/[a-z0-9_/.-]+|blockdev --get(size64|ro) /dev/block/[a-z0-9_]+|sha256sum /dev/block/[a-z0-9_]+|sgdisk -(p|i [0-9]+) /dev/block/sde)|exec-out (dd if=/dev/block/[a-z0-9_]+ bs=4096 count=[0-9]+ 2>/dev/null|head -c [0-9]+ /dev/block/[a-z0-9_]+))$")
        for label in self.labels:
            for call in as_list(self.run_of(label)["calls"]):
                with self.subTest(label=label, call=call):
                    self.assertRegex(call, allowed)
                    self.assertIsNone(re.search(FORBIDDEN_IN_A_COMMAND, call))

    def test_output_inside_a_git_working_tree_is_refused_before_anything_happens(self):
        for label in self.present(("refused in a repo", "refused in a repo, dry", "refused inside a repo by -OutDir", "refused inside a worktree")):
            run = self.run_of(label)
            with self.subTest(label=label):
                self.assertIn("inside the git working tree", run["threw"] or "")
                self.assertEqual(as_list(run["calls"]), [])
                self.assertFalse(run["out_exists"])
                self.assertEqual(as_list(run["folders"]), [])

    def test_bad_arguments_stop_before_any_tool_runs(self):
        run = self.run_of("bootloader image on the wrong phase")
        self.assertEqual(run["threw"], "-BootImage belongs to -Phase twrp")
        self.assertEqual(as_list(run["calls"]), [])

    def test_a_full_twrp_run_backs_up_byte_exact_and_writes_the_reports(self):
        run = self.run_of("twrp ok")
        self.assertIsNone(run["threw"])
        self.assertIn("Done. Nothing was written to the phone.", run["said"])
        folder = self.folder("twrp ok")
        self.assertRegex(folder["name"], r"^\d{8}-\d{6}-twrp$")
        files = {f["name"]: f["length"] for f in as_list(folder["files"])}
        for name, node, size in PARTS:
            self.assertEqual(files[name + ".img"], size, name)
        self.assertEqual(files["sde-table-head.bin"], 1048576)
        for name in ("SHA256SUMS", "report.md", "probe.json", "probe.log"):
            self.assertIn(name, files)
        self.assertTrue(any(n.startswith("raw\\") or n.startswith("raw/") for n in files))
        # Byte exact: the files hold the stand-in's CR, LF, SUB and NUL bytes, so a text-mode copy would differ.
        sums = dict(line.split("  ", 1)[::-1] for line in folder["sums"].strip().splitlines())
        self.assertEqual(set(sums), {n + ".img" for n, _, _ in PARTS} | {"sde-table-head.bin"})
        for idx, (name, node, size) in enumerate(PARTS):
            with self.subTest(part=name):
                self.assertEqual(sums[name + ".img"], content_sha(idx, size // 4096))
        self.assertEqual(sums["sde-table-head.bin"], content_sha(100, 256))
        on_disk = {d["name"]: d["sha"] for d in as_list(folder["disk"])}
        self.assertEqual({k: v for k, v in on_disk.items()}, sums)
        probe = json.loads(folder["probe"])
        self.assertEqual((probe["schema"], probe["phase"], probe["status"], probe["serial"]), (1, "twrp", "complete", "SER1"))
        self.assertIs(probe["nothing_written_to_phone"], True)
        self.assertEqual([s["status"] for s in as_list(probe["steps"]) if s["status"] not in ("ok",)], [])
        self.assertEqual(len(as_list(probe["backups"])), len(PARTS) + 1)
        for backup in as_list(probe["backups"]):
            self.assertTrue(backup["second_read_agrees"], backup)
        report = folder["report"]
        for needle in ("## Summary for the owner", "Nothing was written to the phone", "## Backups (G1)", "## Writability probes (G2)",
                       "## The first 2048 bytes of misc", "all zero (no brake, no pending request)", "## Candidate kernel image",
                       "## What this run does not settle", "No read-only flag was reported for boot_b",
                       "It does NOT show that a write will be accepted", "private data", "| `df /tmp` | Filesystem"):
            with self.subTest(report=needle):
                self.assertIn(needle, report)
        self.assertIn("Fits boot_b: True", report)

    def test_a_read_only_flag_and_missing_tools_are_reported_not_failures(self):
        label = "twrp ok with a read-only flag and no extras"
        run = self.run_of(label)
        self.assertIsNone(run["threw"])
        probe = self.probe(label)
        self.assertEqual(probe["status"], "complete")
        steps = self.steps(label)
        for sid in ("p-sgdisk", "p-sgdisk-boot-a"):
            self.assertEqual(steps[sid]["status"], "unavailable", sid)
        # bootctl is not in the pinned TWRP: it is not asked for at all, so it cannot show up as "unavailable" either.
        self.assertFalse([sid for sid in steps if "bootctl" in sid or "getprop" in sid])
        self.assertEqual(steps["p-df-tmp"]["status"], "ok")
        for sid in ("i-dtbo_a-link", "k-dtbo_a-pull", "k-dtbo_b-again"):
            self.assertEqual(steps[sid]["status"], "skipped", sid)
        self.assertEqual(len(as_list(probe["backups"])), len(PARTS) - 2 + 1)
        report = self.folder(label)["report"]
        self.assertIn("A read-only flag is set on boot_b or on the sde LUN", report)
        self.assertIn("Do not go on to G3", report)
        self.assertNotIn("No read-only flag was reported", report)
        self.assertIn("not present on this phone", report)
        calls = as_list(run["calls"])
        self.assertFalse([c for c in calls if "sde15" in c or "sde32" in c or "dtbo" in c])
        self.assertFalse([c for c in calls if "bootctl" in c or "getprop" in c])

    def test_the_bootloader_run_does_the_install_chains_preflight_and_saves_the_answers(self):
        run = self.run_of("bootloader ok")
        self.assertIsNone(run["threw"])
        self.assertIn("Done. Nothing was written to the phone.", run["said"])
        folder = self.folder("bootloader ok")
        names = {f["name"] for f in as_list(folder["files"])}
        self.assertIn("raw\\b99.txt", {n.replace("/", "\\") for n in names})
        self.assertEqual(folder["sums"], None)
        report = folder["report"]
        for needle in ("product: surfaceduo", "current-slot: a", "unlocked: yes", "battery-level: 80", "is-userspace: no",
                       "partition-size:boot_a: 0x6000000", "Slot counters as the bootloader reports them",
                       "Mode and naming probes"):
            with self.subTest(report=needle):
                self.assertIn(needle, report)
        steps = self.steps("bootloader ok")
        self.assertEqual(steps["b16"]["status"], "unavailable", "an error answer for the plain name is information")
        self.assertEqual(steps["b04"]["status"], "ok")

    def test_every_failure_stops_at_its_own_step_and_says_nothing_was_written(self):
        expect = {
            "not in recovery": ("t00", "the device state is 'device'"),
            "two devices": ("t00", "pass -Serial"),
            "slot b": ("t03", "slot b"),
            "mounted": ("t05", "is mounted"),
            "wrong link": ("i-boot_b-link", "does not resolve to /dev/block/sde26"),
            "wrong size": ("i-boot_b-size", "is not 100663296 bytes"),
            "corrupt first read": ("k-boot_a-pull", "differs from the hash on the phone"),
            "corrupt second read": ("k-boot_a-again", "the second read"),
            "short read": ("k-boot_a-pull", "short or long read"),
            "other product": ("b01", "not a Surface Duo 1"),
            "slot b in the bootloader": ("b02", "the current slot is not a"),
            "locked": ("b03", "the bootloader is not unlocked"),
            "battery 19": ("b04", "battery is below 20 percent"),
            "head short read": ("k-boot_a-pull", "short or long read"),
            "hang in the bootloader": ("b04", "timed out: fastboot -s <serial> getvar battery-level did not finish within 3 seconds"),
            "hang on the first command": ("t00", "timed out: adb devices did not finish within 3 seconds"),
            "hang during a pull": ("k-boot_a-pull", "timed out: adb -s <serial> exec-out dd if=/dev/block/sde9"),
        }
        for label, (step, why) in expect.items():
            if label not in self.runs:
                continue
            run = self.run_of(label)
            with self.subTest(label=label):
                self.assertEqual(run["exit"], 1)
                self.assertIn("STOPPED at %s. Nothing was written to the phone." % step, run["said"])
                probe = self.probe(label)
                self.assertEqual((probe["status"], probe["stopped_at"]), ("stopped", step))
                self.assertIn(why, probe["reason"])
                self.assertIs(probe["nothing_written_to_phone"], True)
                steps = as_list(probe["steps"])
                self.assertEqual(steps[-1]["id"], step, "something ran after the failure")
                self.assertEqual(steps[-1]["status"], "failed")
                report = self.folder(label)["report"]
                self.assertIn("## STOPPED at %s" % step, report)
                self.assertIn("Nothing was written to the phone", report)
                self.assertNotIn("## Summary for the owner", report)
                # Nothing is asked of the phone after the failing step: one call per recorded step.
                self.assertEqual(len(as_list(run["calls"])), len(steps))

    def test_a_hung_tool_is_killed_at_the_limit_and_the_run_stops_with_the_reason(self):
        for label in self.present(("hang in the bootloader", "hang on the first command", "hang during a pull")):
            run = self.run_of(label)
            with self.subTest(label=label):
                # The stand-in sleeps for 90 seconds; the limit was 3. Anything near 90 means nobody killed it.
                self.assertLess(run["seconds"], 45, "the run waited for the hung command")
                self.assertEqual(run["left_running"], 0, "the hung process was left running")
                self.assertEqual(run["exit"], 1)
                probe = self.probe(label)
                self.assertEqual(probe["status"], "stopped")
                self.assertIn("was killed. Nothing was written to the phone.", probe["reason"])
                self.assertIn("hold Power until the screen is dark", probe["reason"])
                self.assertIn("-TimeoutSec", probe["reason"])
                self.assertIs(probe["nothing_written_to_phone"], True)
        if "hang during a pull" in self.runs:
            # The partial file is not listed as a backup: it never reached the hash comparison.
            folder = self.folder("hang during a pull")
            self.assertNotIn("boot_a.img", folder["sums"] or "")

    def test_pull_with_head_backs_up_the_same_bytes_as_the_dd_pull(self):
        if "twrp ok with head pulls" not in self.runs:
            self.skipTest("scenario was not run")
        run = self.run_of("twrp ok with head pulls")
        self.assertIsNone(run["threw"])
        self.assertIn("Done. Nothing was written to the phone.", run["said"])
        calls = as_list(run["calls"])
        self.assertFalse([c for c in calls if "exec-out dd" in c])
        for name, node, size in PARTS:
            self.assertEqual(calls.count("adb -s <serial> exec-out head -c %d /dev/block/%s" % (size, node)), 2, name)
        self.assertEqual(calls.count("adb -s <serial> exec-out head -c 1048576 /dev/block/sde"), 2)
        folder = self.folder("twrp ok with head pulls")
        sums = dict(line.split("  ", 1)[::-1] for line in folder["sums"].strip().splitlines())
        for idx, (name, node, size) in enumerate(PARTS):
            self.assertEqual(sums[name + ".img"], content_sha(idx, size // 4096), name)
        self.assertEqual(sums["sde-table-head.bin"], content_sha(100, 256))
        if "twrp ok" in self.runs:
            self.assertEqual(sums, dict(line.split("  ", 1)[::-1] for line in self.folder("twrp ok")["sums"].strip().splitlines()))
        if "dry twrp head" in self.runs:
            planned = [c for _, _, c in dry_lines(self.run_of("dry twrp head")["said"]) if not c.startswith("(PC only)")]
            self.assertEqual(calls, planned)
            self.assertFalse([c for c in planned if "exec-out dd" in c])

    def test_pull_with_belongs_to_the_twrp_phase_and_a_serial_with_a_newline_is_refused_up_front(self):
        for label, message in (("head pull on the wrong phase", "-PullWith belongs to -Phase twrp"),
                               ("serial with a trailing newline", "-Serial may hold letters, digits and . _ : - only")):
            if label not in self.runs:
                continue
            run = self.run_of(label)
            with self.subTest(label=label):
                self.assertEqual(run["threw"], message)
                self.assertEqual(as_list(run["calls"]), [])
                self.assertFalse(run["out_exists"])

    def test_a_wrong_mode_is_refused_with_one_call_and_a_battery_of_exactly_twenty_passes(self):
        for label in self.present(("not in recovery", "two devices")):
            self.assertEqual(as_list(self.run_of(label)["calls"]), ["adb devices"], label)
        if "battery 19" in self.runs:
            self.assertEqual(as_list(self.run_of("battery 19")["calls"])[-1], "fastboot -s <serial> getvar battery-level")
            self.assertEqual(self.probe("battery 19")["status"], "stopped")
        if "battery 20" in self.runs:
            self.assertIsNone(self.run_of("battery 20")["threw"])
            self.assertEqual(self.probe("battery 20")["status"], "complete")


@unittest.skipUnless(powershells(), "needs pwsh or powershell")
class PersistProbeOnEveryPowerShell(unittest.TestCase):
    """The script must run on Windows PowerShell 5.1 and on PowerShell 7. These tests run on each one that starts here."""

    def test_parse_allow_list_and_dry_run_on_each_powershell(self):
        dry = r"""
$all = [Console]::In.ReadToEnd()
$text = ($all -split '@@@@PART@@@@')[0]
$work = Join-Path ([IO.Path]::GetTempPath()) ('opos-persist-dry-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $s = Join-Path $work 'p.ps1'; [IO.File]::WriteAllText($s, $text)
    $env:PATH = $work
    $said = (& $s -Phase twrp -DryRun -OutDir (Join-Path $work 'out') *>&1 | Out-String -Width 4000)
    [pscustomobject]@{ said = $said; out = [bool](Test-Path (Join-Path $work 'out')); version = "$($PSVersionTable.PSVersion)" } | ConvertTo-Json -Compress
} finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
"""
        corpus = [{"tool": t, "line": l} for t, l in BAD + GOOD]
        for major, exe in sorted(powershells().items()):
            with self.subTest(powershell=major):
                parsed = run_powershell(PARSE, exe=exe)
                self.assertEqual(parsed["errors"], [], parsed["version"])
                verdicts = as_list(run_powershell(ALLOW, exe=exe, stdin=stdin_with(json.dumps(corpus).encode("ascii")))["verdicts"])
                self.assertEqual([v for v, _ in zip(verdicts[:len(BAD)], BAD) if v["allowed"]], [])
                self.assertEqual([v for v in verdicts[len(BAD):] if not v["allowed"]], [])
                got = run_powershell(dry, exe=exe)
                self.assertIn("DRY RUN, phase twrp", got["said"])
                self.assertFalse(got["out"], got["version"])
                self.assertGreater(len(dry_lines(got["said"])), 40)


# ---------------------------------------------------------------------------------------------------------------------
def runbook():
    return RUNBOOK.read_text(encoding="utf-8")


def flat(s):
    return " ".join(s.split())


def gate_sections(doc):
    """{'G0': text, ...}: each gate's section, from its heading to the next second-level heading."""
    sections = {}
    for match in re.finditer(r"(?m)^## (G\d)\. .*$", doc):
        end = doc.find("\n## ", match.end())
        sections[match.group(1)] = doc[match.start():end if end != -1 else len(doc)]
    return sections


def fenced_blocks(doc):
    return re.findall(r"```[a-z]*\n(.*?)```", doc, re.S)


def doc_block(doc, gate, *needles):
    """The one fenced block of a gate that holds every needle."""
    found = [b for b in fenced_blocks(gate_sections(doc)[gate]) if all(n in b for n in needles)]
    assert len(found) == 1, (gate, needles, len(found))
    return found[0]


class PersistRunbook(unittest.TestCase):
    def setUp(self):
        self.doc = runbook()
        self.gates = gate_sections(self.doc)

    def test_file_form_and_status(self):
        self.assertTrue(self.doc.startswith("<!-- SPDX-License-Identifier: GPL-3.0-or-later -->\n# "))
        self.assertNotIn("\r", self.doc)
        head = flat(self.doc[:self.doc.index("## The goal")])
        for phrase in ("a plan; none of it has been run on a phone", "tested against fake tools and file checks only",
                       "Nothing here authorizes a write to the phone", "G3, G4, G5 and G6 each need the owner's explicit word",
                       "G5 is the first plain power-on, which can start Android's wipe path"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, head)

    def test_every_gate_is_there_in_order(self):
        self.assertEqual(list(self.gates), ["G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7"])
        order = [self.doc.index("## %s. " % g) for g in self.gates]
        self.assertEqual(order, sorted(order))
        titles = {"G0": "Preflight", "G1": "Inventory and backup", "G2": "Writability probes", "G3": "inactive slot",
                  "G4": "next start", "G5": "First unattended start", "G6": "Disarm the brake", "G7": "over-the-air kernel update"}
        for gate, title in titles.items():
            with self.subTest(gate=gate):
                self.assertIn(title, self.doc[self.doc.index("## %s. " % gate):].splitlines()[0])

    def test_only_the_writing_gates_need_the_owners_word_and_say_so(self):
        marked = {g for g, body in self.gates.items() if "[OWNER SAYS GO]" in body.splitlines()[0]}
        # G5 is the first plain power-on: it can start Android's wipe path or the variant A retry countdown and cost the
        # OPOS rootfs, so it is gated like the three gates that write.
        self.assertEqual(marked, {"G3", "G4", "G5", "G6"})
        for gate in ("G3", "G4", "G5", "G6"):
            pre = flat(self.gates[gate])
            with self.subTest(gate=gate):
                self.assertIn("The owner has said \"go %s\"" % gate, pre)
        # The read-only gates and the later design note carry no such marker.
        for gate in ("G0", "G1", "G2", "G7"):
            self.assertNotIn("OWNER SAYS GO", self.gates[gate].splitlines()[0])
        g2 = flat(self.gates["G2"])
        self.assertIn("the owner's word for G3 is the message \"go G3\"", g2)
        for word in ("go G4", "go G5", "go G6"):
            self.assertIn('"%s"' % word, g2)
        self.assertIn("Anything else, including silence, is a no.", g2)
        self.assertIn("relaxes his standing rule", flat(self.gates["G3"]))
        g5 = flat(self.gates["G5"])
        for phrase in ("first step that can start stock Android on the OPOS userdata", "its wipe path", "retry countdown",
                       "can cost the OPOS rootfs and a reinstall with the install chain"):
            with self.subTest(g5=phrase):
                self.assertIn(phrase, g5)

    def test_each_gate_has_precondition_commands_expected_proof_decisions_and_rollback(self):
        for gate in ("G0", "G1", "G2", "G3", "G4", "G5", "G6"):
            body = self.gates[gate]
            for label in ("**Precondition", "**Commands", "**Expected", "**What it proves", "**Decision table", "**Rollback"):
                with self.subTest(gate=gate, label=label):
                    self.assertIn(label, body)
            order = [body.index(label) for label in ("**Precondition", "**Commands", "**Expected", "**What it proves", "**Decision table", "**Rollback")]
            self.assertEqual(order, sorted(order), gate)
            self.assertIn("|---|", body)
        for gate in ("G0", "G1", "G2", "G3", "G6"):
            self.assertIn("```powershell", self.gates[gate])
        self.assertIn("separate, later design", flat(self.gates["G7"]))
        self.assertIn("docs/ota-update.md".replace("docs/", ""), self.gates["G7"])
        self.assertIn("OTA stays app-level only", flat(self.gates["G7"]))

    def test_the_g4_choice_names_the_mechanism_and_its_cost(self):
        g4 = flat(self.gates["G4"])
        for phrase in ("Variant A: `fastboot set_active b`", "Variant B: no slot change", "`bootctl` is not in the pinned TWRP",
                       "the UFS boot LUN", "there is no rescue", "stock Android on the OPOS userdata, the wipe path",
                       "variant B is the safer one", "The owner chooses; the operator does not.", "Invoke-Fastboot set_active a",
                       "**Rollback, variant A**", "**Rollback, variant B**"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, g4)

    def test_g5_explains_the_brake_the_success_criteria_and_the_timeout(self):
        g5 = flat(self.gates["G5"])
        for phrase in ("How the brake and the new slot interact", "The menu appears and stays", "OPOS starts anyway",
                       "Success criteria", "Timeout", "Wait up to five minutes", "a limit chosen for safety, not a measured start time",
                       "hold Power until the screen is dark", "Volume-down plus Power", "Do not pick Factory data reset",
                       "reboot bootloader", "(never `reboot`)", "VID_1D6B&PID_0104"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, g5)

    def test_the_known_table_grades_every_claim(self):
        table = self.doc[self.doc.index("| # | Claim"):self.doc.index("**What the experiment settles.**")]
        rows = [r for r in table.splitlines()[2:] if r.startswith("|")]
        self.assertGreaterEqual(len(rows), 15)
        for row in rows:
            cells = [c.strip() for c in row.strip("|").split("|")]
            with self.subTest(row=cells[0]):
                self.assertEqual(len(cells), 4)
                self.assertRegex(cells[2], r"MEASURED|REPORTED|INFERRED|UNKNOWN")
        flat_table = flat(table)
        for phrase in ("One line, item 34 of the October 31, 2026 install-friction log",
                       "Nothing else about that attempt was kept", "`vbmeta` is refused the same way", "fastbootd",
                       "WOA-Project/SurfaceDuo-Guides", "answered OKAY", "Current content of `boot_a`"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, flat_table)
        for section in ("## The goal and what changes for the owner", "## What is known, and what the experiment settles",
                        "## Ground rules", "## What if the probes say no", "## Risks"):
            with self.subTest(section=section):
                self.assertIn(section + "\n", self.doc)
        self.assertIn("*why* fastboot refused", self.doc)

    def known_rows(self):
        table = self.doc[self.doc.index("| # | Claim"):self.doc.index("**What the experiment settles.**")]
        rows = {}
        for row in table.splitlines()[2:]:
            if row.startswith("|"):
                cells = [c.strip() for c in row.strip("|").split("|")]
                rows[cells[0]] = cells
        return rows

    def test_the_known_table_says_what_each_source_really_says(self):
        rows = self.known_rows()
        self.assertTrue({"4", "4b", "5", "7", "10", "15", "18", "19", "20"} <= set(rows), sorted(rows))
        # The grades are defined, including the two this revision adds.
        legend = flat(self.doc[self.doc.index("Grades: **MEASURED**"):self.doc.index("| # | Claim")])
        for word in ("**INFERRED**", "**HYPOTHESIS**", "**REPORTED**", "**UNKNOWN**"):
            self.assertIn(word, legend)
        # Row 4 is the measurement and only the measurement; the explanation is row 4b and is graded as a hypothesis.
        four, four_b = flat(" ".join(rows["4"])), flat(" ".join(rows["4b"]))
        self.assertEqual(rows["4"][2], "MEASURED, re-checked")
        self.assertIn("on November 7 `erase misc`, `flash misc`, `format:ext4 userdata` and the boot of two images", four)
        self.assertNotIn("specific to the boot slots", four)
        self.assertNotIn("So the refusal", four)
        self.assertEqual(rows["4b"][2], "UNKNOWN (HYPOTHESIS)")
        for phrase in ("HYPOTHESIS: the October 31 refusal is specific to the boot slots", "a poisoned `misc`",
                       "reject operations per slot", "per-slot RAM-boot wedge that also prints `Device Error`", "a write that only fastbootd accepts (row 10)",
                       "`EFI_DEVICE_ERROR`", "cure order", "hold Volume-down plus Power", "RAM-boot TWRP once",
                       "zero the first 2048 bytes of `misc` from TWRP", "never retry a crashed kernel from the last good slot"):
            with self.subTest(row4b=phrase):
                self.assertIn(phrase, four_b)
        # Row 5 keeps the strongest evidence for a root-side write, with its grades.
        five = flat(" ".join(rows["5"]))
        for phrase in ("strongest evidence we have that a root-side block write to the boot LUN (`sde`) works while "
                       "`fastboot flash boot_a` was refused", "It says nothing yet about a write from TWRP",
                       "October 31 hash MEASURED", "the conclusion about a root-side write INFERRED"):
            with self.subTest(row5=phrase):
                self.assertIn(phrase, five)
        # Row 7 does not keep a dated unlock or slot snapshot. G0 reads the phone on the day of the install.
        seven = flat(" ".join(rows["7"]))
        self.assertIn("G0 reads the current slot", seven)
        self.assertNotIn("17:07 UTC", seven)
        self.assertNotIn("retry count", seven)
        self.assertEqual(rows["7"][2], "UNKNOWN")
        # Row 10 says what the WOA-Project page says: fastbootd via stock recovery, the plain name, no firmware, no carrier.
        ten = flat(" ".join(rows["10"]))
        for phrase in ("through stock recovery > \"Enter fastboot\"", "`fastboot flash boot <image>`", "It names no firmware version and no carrier",
                       "this runbook's reading of the page's steps, not a statement on the page",
                       "Troubleshooting advice is a G5 row",
                       "cannot be relocked"):
            with self.subTest(row10=phrase):
                self.assertIn(phrase, ten)
        self.assertNotIn("AT&T", ten)
        self.assertNotIn("names firmware", ten)
        for banned in ("2022.902.48", "2022.815.527", "planning dossier"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, self.doc)
        self.assertEqual(rows["10"][2], "REPORTED")
        self.assertIn("undated page", ten)
        self.assertIn("Troubleshooting advice (REPORTED, undated)", flat(self.gates["G5"]))
        self.assertIn("`fastboot set_active` to the other slot and then back to the original slot", flat(self.gates["G5"]))
        # Row 15 is a volunteer's answer with partial reports, not "no rescue exists".
        fifteen = flat(" ".join(rows["15"]))
        for phrase in ("volunteer's answer", "EDL mode", "not publicly released", "paid out-of-warranty repair", "partial success",
                       "is this project's rule, not something the page states"):
            with self.subTest(row15=phrase):
                self.assertIn(phrase, fifteen)
        self.assertNotIn("no EDL programmer exists publicly", fifteen)
        self.assertTrue(rows["15"][2].startswith("REPORTED (one volunteer answer"))
        # Rows 18 and 19: the October 9 TWRP dd to misc is MEASURED; TWRP reachability is only inferred.
        eighteen = flat(" ".join(rows["18"]))
        for phrase in ("`bs=2048 count=1`", "`sda5`", "attempt 3 failed, attempt 4 worked", "does not show that a write to `sde` works from TWRP"):
            with self.subTest(row18=phrase):
                self.assertIn(phrase, eighteen)
        self.assertTrue(rows["18"][2].startswith("MEASURED"))
        self.assertTrue(rows["19"][2].startswith("INFERRED, not measured"))
        twenty = flat(" ".join(rows["20"]))
        for phrase in ("toybox 0.7.6", "BSD-style `dd` with `conv` disabled and no `status=`", "`sgdisk`",
                       "**`bootctl` and the boot-control HAL are absent.**"):
            with self.subTest(row20=phrase):
                self.assertIn(phrase, twenty)
        # The G3 text no longer states the reachability of TWRP as a fact.
        g3 = flat(self.gates["G3"])
        self.assertNotIn("do not depend on the content of any boot slot", g3)
        self.assertIn("**INFERRED, not measured:** `fastboot boot` takes its image from the PC", g3)
        self.assertIn("(row 19)", g3)

    def test_the_pinned_twrp_tools_are_stated_and_the_probe_does_not_rely_on_missing_ones(self):
        g1 = flat(self.gates["G1"])
        for phrase in ("Toybox 0.7.6 supplies", "`dd` is a separate BSD-style binary", "has `conv` disabled (measured on October 9) and no `status=`",
                       "**`bootctl` and the boot-control HAL are absent**", "so the probe does not call `bootctl`",
                       "The script does not call `getprop` or `bootctl`", "`exec-out head -c <size> <block>`",
                       "run it again into a new folder with `-PullWith head`", "`df /tmp`"):
            with self.subTest(g1=phrase):
                self.assertIn(phrase, g1)
        self.assertNotRegex(g1.replace("The script does not call `getprop` or `bootctl`", ""), r"`getprop`")
        g2 = flat(self.gates["G2"])
        self.assertNotIn("`bootctl` unavailable", g2)
        self.assertIn("(`bootctl` is not probed: the pinned TWRP does not have it, row 20.)", g2)
        self.assertIn("`df /tmp` shows less than 114688 available", g2)
        self.assertIn("`-TimeoutSec`", flat(self.gates["G0"]))
        self.assertIn("-PullWith", self.doc)

    def test_what_if_the_probes_say_no_keeps_the_ram_boot(self):
        section = flat(self.doc[self.doc.index("## What if the probes say no"):self.doc.index("## Risks")])
        for phrase in ("Keep the RAM-boot.", "A read-only flag is 1", "Both fastboot (October 31) and TWRP refuse",
                       "fastbootd", "a separate experiment with its own gate and the owner's word"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)

    def test_risks_name_the_chain_the_wipe_the_brake_and_the_missing_hardware_run(self):
        risks = flat(self.doc[self.doc.index("## Risks"):])
        for phrase in ("No step here writes it", "Android's wipe path", "The brake is unproven for plain starts",
                       "Never relock", "Microsoft's restore", "fastboot` exits 0 on `FAILED`", "never run against hardware"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, risks)

    def test_ground_rules_forbid_the_bootloader_chain_and_lock_state_changes(self):
        rules = flat(self.doc[self.doc.index("## Ground rules"):self.doc.index("Setup used by the commands")])
        for phrase in ("Never write the bootloader chain: xbl, xbl_config, abl, tz, hyp, keymaster, devcfg, aop",
                       "There is no rescue.", "Never run `flashing lock`, `flashing unlock` or any `oem` command",
                       "Never write `vbmeta`, `dtbo` or `super`", "Never run `reboot` or `systemctl reboot` on a RAM-booted OPOS",
                       "One RAM-boot of the OPOS kernel per attempt, never retried", "Stop at the first surprise",
                       "the script refuses otherwise"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, rules)

    def test_no_speed_figure_and_no_percent_sign_anywhere(self):
        # The repository's docs test forbids figures like "240 ms" in the Duo OS README; the same discipline holds here.
        self.assertIsNone(re.search(r"\d+(\.\d+)?\s*(%|tokens|fps|ms\b|mAh|hours? of battery)", self.doc))
        self.assertIsNone(re.search(r"(?i)\d+(\.\d+)?\s*(MB/s|MiB/s|KB/s|KiB/s|GB/s|Mbit/s|Mbps|kbps|per second|/s\b|seconds? per)", self.doc))
        self.assertNotIn("%", self.doc)

    def test_the_code_blocks_write_only_the_partitions_the_gates_allow(self):
        blocks = "\n".join(fenced_blocks(self.doc))
        # The runbook has ONE dd, inside Write-BootPartition (G3, block 1). Its output file is a node taken from a
        # two-entry table, and the function takes only boot_a or boot_b.
        helper = doc_block(self.doc, "G3", "function Write-BootPartition")
        self.assertEqual(blocks.count("shell dd "), 1)
        self.assertEqual(blocks.count("of="), 1)
        self.assertIn("\"of=$node\"", helper)
        self.assertIn("$node = @{ boot_a = '/dev/block/sde9'; boot_b = '/dev/block/sde26' }[$Name]", helper)
        self.assertIn("[ValidateSet('boot_a', 'boot_b')][string]$Name", helper)
        self.assertEqual(len(re.findall(r"# WRITE", self.gates["G3"])), 1)
        # Who calls it: G3 writes boot_b (and restores it), G4 variant B writes boot_a (and restores it), G5 only restores boot_a.
        names = {g: set(re.findall(r"Write-BootPartition -Name (\w+)", body)) for g, body in self.gates.items()}
        self.assertEqual(names, {"G0": set(), "G1": set(), "G2": set(), "G3": {"boot_b"}, "G4": {"boot_a"}, "G5": {"boot_a"},
                                 "G6": set(), "G7": set()})
        for gate, body in self.gates.items():
            self.assertNotIn("of=", "\n".join(fenced_blocks(body)).replace("\"of=$node\"", ""), gate)
        for line in re.findall(r"Write-BootPartition -Name [^\n]*", self.gates["G5"]):
            self.assertIn("-Restore", line)
        # Fastboot commands in the code blocks: reads, the TWRP RAM-boots, the slot change of variant A, the misc brake.
        verbs = []
        commands = "\n".join(b for b in fenced_blocks(self.doc) if "function " not in b)   # the helper definitions are not commands
        for line in commands.splitlines():
            if line.lstrip().startswith("#"):
                continue
            m = re.search(r"\bInvoke-Fastboot (\S+)(?: (\S+))?", line) or re.search(r"\bfastboot\b(?: -s \$serial)? (\S+)(?: (\S+))?", line)
            if not m:
                continue
            verb, arg = m.group(1), m.group(2)
            verbs.append(verb)
            with self.subTest(line=line.strip()):
                self.assertIn(verb, ("boot", "getvar", "set_active", "erase", "flash", "devices"))
                if verb == "boot":
                    self.assertIn("surfaceduo1-twrp.img", line)
                if verb in ("erase", "flash"):
                    self.assertEqual(arg, "misc")
                if verb == "set_active":
                    self.assertIn(arg, ("a", "b"))
        self.assertGreater(len(verbs), 10)
        for token in ("flashing", "oem ", "format", "userdata", "vbmeta_a.img of", "fastboot flash boot", "fastboot -s $serial flash boot",
                      "systemctl reboot", "mkfs", "sgdisk -o", "wipe"):
            with self.subTest(token=token):
                self.assertNotIn(token, blocks)
        self.assertEqual(blocks.count("set_active b"), 1, "only variant A's set_active b is in a command block")
        self.assertIn("set_active b", doc_block(self.doc, "G4", "set_active b"))
        self.assertEqual(blocks.count("set_active a"), 2, "the rollbacks of G4 and G5 go back to slot a")
        # No chain partition is named as a target in any command.
        chain = r"\b(xbl(_config)?|abl|tz|hyp|keymaster|devcfg|aop|qupfw|uefisecapp|cmnlib(64)?|sfpd|devinfo|apdp|uefivarstore|ddr|persist|modemst[12]|fsg|fsc)(_[ab])?\b"
        for match in re.finditer(chain, blocks.replace("duo-persist-probe", "")):
            self.fail("a command block names a protected partition: " + match.group(0))

    def test_write_bootpartition_checks_everything_in_code_before_its_one_dd(self):
        helper = doc_block(self.doc, "G3", "function Write-BootPartition")
        fn = helper[helper.index("function Write-BootPartition"):]
        # Comment lines do not count: a check that is only a comment guards nothing.
        fn = "\n".join(line for line in fn.splitlines() if not line.lstrip().startswith("#"))
        dd_at = fn.index("shell dd ")
        before, after = fn[:dd_at], fn[dd_at:]
        checks = ("Assert-Equal 'adb state' (Invoke-Adb get-state) 'recovery'",
                  "Assert-Equal \"size of $Image in bytes\" ([string](Get-Item -LiteralPath $Image).Length) '100663296'",
                  "Assert-Equal \"SHA-256 of $Image on the PC\" ((Get-FileHash -Algorithm SHA256 -LiteralPath $Image).Hash.ToLower()) $ImageHash",
                  "Assert-Equal \"where $ByName/$Name points\" (Invoke-Adb shell readlink -f \"$ByName/$Name\") $node",
                  "Assert-Equal \"size of $node in bytes\" (Invoke-Adb shell blockdev --getsize64 $node) '100663296'",
                  "Assert-Equal \"read-only flag of $node\" (Invoke-Adb shell blockdev --getro $node) '0'",
                  "Assert-Equal \"mounts of $node\" ([string]$mounted.Count) '0'",
                  "if ($PSCmdlet.ParameterSetName -eq 'Write') { Assert-Equal \"SHA-256 of $node now (its G1 backup)\" $before $CurrentHash }",
                  "Invoke-Adb push $Image /tmp/opos-image.img",
                  "Assert-Equal 'SHA-256 of the pushed image on the phone' (Get-PhoneSha /tmp/opos-image.img) $ImageHash")
        order = []
        for check in checks:
            with self.subTest(check=check):
                found = re.search(r"(?m)^\s*" + re.escape(check), before)
                self.assertIsNotNone(found, "not a code line before the dd")
                order.append(found.start())
        self.assertEqual(order, sorted(order))
        # The guards are executable: a mismatch throws, case-sensitively, and nothing is a comment.
        guard = doc_block(self.doc, "G3", "function Assert-Equal")
        self.assertIn("if ($Got -cne $Want) { throw \"STOP: $What is '$Got', expected '$Want'. Nothing further was run.\" }", guard)
        self.assertNotRegex(before, r"(?m)^\s*#\s*must\b")
        self.assertNotRegex(self.gates["G3"], r"#\s*must (be|equal)")
        # After the write: sync, cache control, and the node must hash to the image, or the block throws.
        tail = [re.search(r"(?m)^\s*" + re.escape(x), after) for x in ("Invoke-Adb shell sync", "Invoke-Adb shell 'echo 3 > /proc/sys/vm/drop_caches'",
                                                                      "Assert-Equal \"SHA-256 of $node after the write\" (Get-PhoneSha $node) $ImageHash")]
        self.assertTrue(all(tail), tail)
        self.assertEqual([m.start() for m in tail], sorted(m.start() for m in tail))
        # The restore skips only the check against the G1 hash of the target, and says what the node holds.
        self.assertRegex(before, r"(?m)^\s*else \{ Write-Host \"restoring: \$node holds \$before now\" \}")
        # The two write calls pass the G1 hash of their own target.
        self.assertIn("Write-BootPartition -Name boot_b -Image $pad -ImageHash $padHash -CurrentHash $sums['boot_b.img']", self.gates["G3"])
        self.assertIn("Write-BootPartition -Name boot_a -Image $pad -ImageHash $padHash -CurrentHash $sums['boot_a.img']", self.gates["G4"])

    def test_every_block_that_touches_the_phone_is_one_statement_that_stops_at_the_first_failed_check(self):
        for gate in ("G3", "G4", "G5", "G6"):
            for block in fenced_blocks(self.gates[gate]):
                if "function " in block or not re.search(r"Invoke-(Adb|Fastboot)|Write-BootPartition", block):
                    continue
                with self.subTest(gate=gate, block=block.splitlines()[1].strip()):
                    self.assertTrue(block.startswith("& {\n    $ErrorActionPreference = 'Stop'\n"), block[:60])
                    self.assertTrue(block.rstrip().endswith("\n}"), block[-20:])
                    self.assertEqual(block.count("\n& {"), 0)
        # The same blocks, one per place, are what the rollbacks say; G5 repeats the G4 one verbatim.
        g4_b = doc_block(self.doc, "G4", "-Name boot_a", "-Restore")
        self.assertIn(g4_b, fenced_blocks(self.gates["G5"]))
        g4_a = doc_block(self.doc, "G4", "set_active a")
        self.assertIn(g4_a, fenced_blocks(self.gates["G5"]))
        self.assertEqual(doc_block(self.doc, "G5", "erase misc"), doc_block(self.doc, "G6", "flash misc"))

    def test_each_gate_has_its_exact_rollback_block_with_the_ram_boot_of_twrp_and_the_adb_devices_wait(self):
        boot = r'''if (-not (Test-Recovery)) { Invoke-Fastboot boot "$images\surfaceduo1-twrp.img" | Out-Null }'''
        restore_b = r'''Write-BootPartition -Name boot_b -Image "$run\boot_b.img" -ImageHash $sums['boot_b.img'] -Restore'''
        restore_a = r'''Write-BootPartition -Name boot_a -Image "$run\boot_a.img" -ImageHash $sums['boot_a.img'] -Restore'''
        # boot_b in G3, boot_a in G4 (variant B) and again in G5: the RAM-boot of TWRP (unless it runs), the wait, the restore.
        for gate, call in (("G3", restore_b), ("G4", restore_a), ("G5", restore_a)):
            with self.subTest(gate=gate):
                lines = [line.strip() for line in doc_block(self.doc, gate, call).splitlines()]
                for needed in (boot, "Wait-Recovery", call):
                    self.assertIn(needed, lines)
                self.assertLess(lines.index(boot), lines.index("Wait-Recovery"))
                self.assertLess(lines.index("Wait-Recovery"), lines.index(call))
        # Waiting means polling adb devices until the phone is listed as recovery, and it gives up.
        helpers = doc_block(self.doc, "G3", "function Write-BootPartition")
        self.assertIn("(& adb devices | Out-String) -match", helpers)
        self.assertIn("while (-not (Test-Recovery)) {", helpers)
        self.assertIn("if ((Get-Date) -gt $until) { throw \"STOP: adb did not list $serial as recovery within $Seconds seconds.", helpers)
        # Variant A goes back with set_active a and reads the slot back; the brake is re-armed with erase and flash of misc.
        for gate in ("G4", "G5"):
            block = doc_block(self.doc, gate, "set_active a")
            self.assertIn("Invoke-Fastboot set_active a | Out-Null", block)
            self.assertIn("if ((Invoke-Fastboot getvar current-slot) -notmatch 'current-slot:\\s*a\\b') { throw", block)
        for gate in ("G5", "G6"):
            block = doc_block(self.doc, gate, "flash misc")
            lines = [line.strip() for line in block.splitlines()]
            self.assertLess(lines.index("Invoke-Fastboot erase misc | Out-Null"),
                            lines.index(r'''Invoke-Fastboot flash misc "$images\misc-brake.img" | Out-Null'''))
        # The G3 rollback follows the table in the text, not the other way round.
        g3 = self.gates["G3"]
        self.assertLess(g3.index("**Rollback (restores `boot_b` to its G1 content).**"), g3.index(restore_b))

    def test_commands_and_pins_match_the_script_and_the_install_chain(self):
        script = text()
        used = set()
        for line in self.doc.splitlines():
            if "duo-persist-probe.ps1" in line and "-Phase" in line:
                used.update(re.findall(r"\s-([A-Za-z]+)", line.split("duo-persist-probe.ps1", 1)[1]))
        self.assertEqual(used, {"Phase", "Serial", "OutDir", "BootImage", "DryRun"})
        for name in used:
            with self.subTest(flag=name):
                self.assertRegex(script, r"\]\$%s\b" % name)
        self.assertIn("deploy\\duo-persist-probe.ps1", self.doc)
        self.assertTrue(SCRIPT.is_file())
        boot = BOOT_SCRIPT.read_text(encoding="utf-8")
        for name, digest in PINNED.items():
            with self.subTest(pin=name):
                self.assertIn(digest, self.doc)
                self.assertIn(digest, boot)
        for name, node, size in PARTS:
            with self.subTest(part=name):
                self.assertIn(node, self.gates["G1"])
        for name in ("boot_a.img", "boot_b.img", "vbmeta_a.img", "vbmeta_b.img", "dtbo_a.img", "dtbo_b.img", "misc.img", "sde-table-head.bin"):
            with self.subTest(file=name):
                self.assertIn(name, self.gates["G1"])
        self.assertIn("sde-table-head.bin", script)
        for flag in ("PullWith", "TimeoutSec"):
            with self.subTest(flag=flag):
                self.assertIn("-" + flag, self.doc)
                self.assertRegex(script, r"\]\$%s\b" % flag)
        self.assertIn("100663296", self.doc)
        self.assertIn("24576+0 records", self.doc)

    def test_no_private_paths_or_addresses(self):
        for pattern in PRIVATE:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, self.doc))

    def test_links_resolve_and_the_two_pointers_exist(self):
        link = re.compile(r"\]\(([^)\s#]+)(#[^)\s]*)?\)")
        for name in ("docs/duo-persistent-install.md", "docs/README.md", "packaging/duo-os/README.md"):
            base = (ROOT / name).parent
            for target, _ in link.findall((ROOT / name).read_text(encoding="utf-8")):
                if re.match(r"[a-z]+:", target):
                    continue
                with self.subTest(doc=name, link=target):
                    self.assertTrue((base / target).exists())
        index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        rows = [line for line in index.splitlines() if "duo-persistent-install.md" in line]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].startswith("- [Persistent kernel on the Duo](duo-persistent-install.md):"))
        self.assertIn("a plan, not run", rows[0])
        readme = (ROOT / "packaging" / "duo-os" / "README.md").read_text(encoding="utf-8")
        lines = [line for line in readme.splitlines() if "duo-persistent-install.md" in line]
        self.assertEqual(len(lines), 1, "ONE line in the Duo OS README: it is edited elsewhere")
        self.assertIn("has not been run", lines[0])
        start = readme.index("## RAM-boot only, and the misc brake")
        self.assertLess(start, readme.index(lines[0]))
        self.assertLess(readme.index(lines[0]), readme.index("## Spanning both panels"))


# ---------------------------------------------------------------------------------------------------------------------
# The runbook's own PowerShell blocks, parsed on every PowerShell and then RUN against the stateful stand-in tools.
PARSE_BLOCKS = r"""
$text = [Console]::In.ReadToEnd()
$blocks = $text | ConvertFrom-Json
$out = @()
foreach ($b in $blocks) {
    $tokens = $null; $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseInput([string]$b, [ref]$tokens, [ref]$errors)
    $t = [string]$b
    $out += [pscustomobject]@{ errors = @($errors | ForEach-Object { $_.Message }); head = $t.Substring(0, [Math]::Min(30, $t.Length)) }
}
[pscustomobject]@{ results = @($out); version = "$($PSVersionTable.PSVersion)" } | ConvertTo-Json -Compress -Depth 4
"""

# Runs blocks of the runbook, in the order each scenario names them, in one session against the stand-in adb and fastboot
# with FAKE_STORE set (pushed files and block-device writes are kept there). A block that throws ends its scenario, as it
# would stop an operator. Reported per scenario: what each block threw, every call the stand-in saw, and what the
# stand-in's two boot partitions and the active slot hold at the end.
FAKE_BLOCKS = r"""
$ErrorActionPreference = 'Stop'
$all = [Console]::In.ReadToEnd()
$parts = $all -split '@@@@PART@@@@'
$cs = $parts[0]
$spec = $parts[1] | ConvertFrom-Json
$root = Join-Path ([IO.Path]::GetTempPath()) ('opos-persist-blocks-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$origPath = $env:PATH
$origDir = (Get-Location).Path
$results = @()
try {
    $bin = Join-Path $root 'bin'; New-Item -ItemType Directory -Path $bin | Out-Null
    Add-Type -TypeDefinition $cs -OutputAssembly (Join-Path $bin 'adb.exe') -OutputType ConsoleApplication
    Copy-Item (Join-Path $bin 'adb.exe') (Join-Path $bin 'fastboot.exe')
    $adb = Join-Path $bin 'adb.exe'
    $env:PATH = $bin
    foreach ($d in 'images', 'run', 'run-nosums', 'run-corrupt', 'padwork', 'short') { New-Item -ItemType Directory -Path (Join-Path $root $d) | Out-Null }
    $images = Join-Path $root 'images'
    $stub = New-Object byte[] 1048576; [Text.Encoding]::ASCII.GetBytes('ANDROID!').CopyTo($stub, 0); $stub[40] = 2
    [IO.File]::WriteAllBytes((Join-Path $images 'boot.img'), $stub)
    [IO.File]::WriteAllBytes((Join-Path $images 'surfaceduo1-twrp.img'), (New-Object byte[] 4096))
    [IO.File]::WriteAllBytes((Join-Path $images 'misc-brake.img'), (New-Object byte[] 2048))
    # The G1 backup folder: the stand-in's own content for boot_a and boot_b, and the SHA256SUMS the probe script writes.
    $run = Join-Path $root 'run'
    & $adb fake-dump sde9 (Join-Path $run 'boot_a.img')
    & $adb fake-dump sde26 (Join-Path $run 'boot_b.img')
    $hashA = (Get-FileHash -Algorithm SHA256 (Join-Path $run 'boot_a.img')).Hash.ToLower()
    $hashB = (Get-FileHash -Algorithm SHA256 (Join-Path $run 'boot_b.img')).Hash.ToLower()
    [IO.File]::WriteAllText((Join-Path $run 'SHA256SUMS'), "$hashA  boot_a.img`n$hashB  boot_b.img`n")
    $runNoSums = Join-Path $root 'run-nosums'
    [IO.File]::WriteAllText((Join-Path $runNoSums 'SHA256SUMS'), ('0' * 64) + "  vbmeta_a.img`n")
    $runCorrupt = Join-Path $root 'run-corrupt'
    foreach ($f in 'boot_a.img', 'boot_b.img') {
        Copy-Item (Join-Path $run $f) (Join-Path $runCorrupt $f)
        $fsx = [IO.File]::Open((Join-Path $runCorrupt $f), 'Open', 'ReadWrite'); $fsx.Position = 7000; $byte = $fsx.ReadByte(); $fsx.Position = 7000; $fsx.WriteByte([byte]($byte -bxor 1)); $fsx.Dispose()
    }
    Copy-Item (Join-Path $run 'SHA256SUMS') (Join-Path $runCorrupt 'SHA256SUMS')
    $shortPad = Join-Path $root 'short\small.img'; [IO.File]::WriteAllBytes($shortPad, (New-Object byte[] 1048576))
    $shortHash = (Get-FileHash -Algorithm SHA256 $shortPad).Hash.ToLower()
    $serial = 'SER1'; $repo = 'unused'; $probe = 'unused'
    . ([scriptblock]::Create([string]$spec.blocks.helpers))
    $padText = ([string]$spec.blocks.pad).Replace("'<a folder on the PC>'", "'" + (Join-Path $root 'padwork') + "'")
    . ([scriptblock]::Create($padText))
    $padHashOrig = $padHash; $padOrig = $pad; $runOrig = $run
    $n = 0
    foreach ($sc in $spec.scenarios) {
        $n++
        Get-ChildItem env: | Where-Object { $_.Name -like 'FAKE_*' } | ForEach-Object { Remove-Item -LiteralPath ('env:' + $_.Name) }
        $store = Join-Path $root "store-$n"; New-Item -ItemType Directory -Path $store | Out-Null
        $log = Join-Path $root "calls-$n.txt"
        $env:FAKE_STORE = $store; $env:FAKE_LOG = $log
        if ($sc.env) { foreach ($e in $sc.env.PSObject.Properties) { Set-Item -LiteralPath ('env:' + $e.Name) -Value ([string]$e.Value) } }
        $pad = $padOrig; $padHash = $padHashOrig; $run = $runOrig
        switch ([string]$sc.override) {
            'pad-hash' { $padHash = 'f' * 64 }
            'pad-short' { $pad = $shortPad; $padHash = $shortHash }
            'run-nosums' { $run = $runNoSums }
            'run-corrupt' { $run = $runCorrupt }
        }
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $done = @()
        foreach ($name in $sc.steps) {
            $msg = $null
            try { . ([scriptblock]::Create([string]$spec.blocks.$name)) *> $null } catch { $msg = $_.Exception.Message }
            $done += [pscustomobject]@{ step = [string]$name; message = $msg }
            if ($msg) { break }
        }
        $seconds = $clock.Elapsed.TotalSeconds
        $calls = @(if (Test-Path -LiteralPath $log) { [IO.File]::ReadAllLines($log) })
        Get-ChildItem env: | Where-Object { $_.Name -like 'FAKE_*' } | ForEach-Object { Remove-Item -LiteralPath ('env:' + $_.Name) }
        $env:FAKE_STORE = $store
        $slotFile = Join-Path $store 'slot'
        $results += [pscustomobject]@{ label = [string]$sc.label; steps = @($done); calls = $calls; seconds = $seconds
            sha26 = ((& $adb fake-sha sde26) | Out-String).Trim(); sha9 = ((& $adb fake-sha sde9) | Out-String).Trim()
            slot = $(if (Test-Path -LiteralPath $slotFile) { [IO.File]::ReadAllText($slotFile) } else { '' }) }
        Remove-Item -LiteralPath $store -Recurse -Force
    }
    $pad = $padOrig; $padHash = $padHashOrig
    [pscustomobject]@{ pad_hash = $padHashOrig; hash_a = $hashA; hash_b = $hashB; scenarios = @($results) } | ConvertTo-Json -Compress -Depth 8
} finally {
    Set-Location $origDir
    $env:PATH = $origPath
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
"""

DD_B = "adb -s <serial> shell dd if=/tmp/opos-image.img of=/dev/block/sde26 bs=4096"
DD_A = "adb -s <serial> shell dd if=/tmp/opos-image.img of=/dev/block/sde9 bs=4096"
BYNAME = "/dev/block/platform/soc/1d84000.ufshc/by-name"


def runbook_blocks():
    doc = runbook()
    return {
        "helpers": doc_block(doc, "G3", "function Write-BootPartition"),
        "pad": doc_block(doc, "G3", "SetLength(100663296)"),
        "g3_write": doc_block(doc, "G3", "Write-BootPartition -Name boot_b", "-CurrentHash"),
        "g3_cold": doc_block(doc, "G3", "cold SHA-256 of boot_b"),
        "g3_rollback": doc_block(doc, "G3", "-Name boot_b", "-Restore"),
        "g4_a_write": doc_block(doc, "G4", "set_active b"),
        "g4_a_rollback": doc_block(doc, "G4", "set_active a"),
        "g4_b_write": doc_block(doc, "G4", "Write-BootPartition -Name boot_a", "-CurrentHash"),
        "g4_b_cold": doc_block(doc, "G4", "cold SHA-256 of boot_a"),
        "g4_b_rollback": doc_block(doc, "G4", "-Name boot_a", "-Restore"),
        "g5_a_rollback": doc_block(doc, "G5", "set_active a"),
        "g5_b_rollback": doc_block(doc, "G5", "-Name boot_a", "-Restore"),
        "g5_rearm": doc_block(doc, "G5", "erase misc"),
        "g6_disarm": doc_block(doc, "G6", "erase misc", "# WRITE"),
        "g6_rearm": doc_block(doc, "G6", "flash misc"),
        # Inline blocks for the helpers and for names the function must refuse.
        "wait_recovery_3": "Wait-Recovery 3",
        "name_misc": "Write-BootPartition -Name misc -Image $pad -ImageHash $padHash -Restore",
        "name_vbmeta": "Write-BootPartition -Name vbmeta_a -Image $pad -ImageHash $padHash -Restore",
        "name_userdata": "Write-BootPartition -Name userdata -Image $pad -ImageHash $padHash -Restore",
        "name_node": "Write-BootPartition -Name sde26 -Image $pad -ImageHash $padHash -Restore",
        "name_boot": "Write-BootPartition -Name boot -Image $pad -ImageHash $padHash -Restore",
        "bad_hash": "Write-BootPartition -Name boot_b -Image $pad -ImageHash ('A' * 64) -Restore",
        "short_hash": "Write-BootPartition -Name boot_b -Image $pad -ImageHash 'abc' -Restore",
        "no_mode": "Write-BootPartition -Name boot_b -Image $pad -ImageHash $padHash",
    }


def block_scenarios():
    S = []

    def add(label, steps, env=None, override=""):
        S.append({"label": label, "steps": steps, "env": env or {}, "override": override})

    # G3 write block
    add("g3 happy", ["g3_write", "g3_cold"])
    add("g3 wrong link", ["g3_write"], {"FAKE_LINK_WRONG": "boot_b"})
    add("g3 wrong size", ["g3_write"], {"FAKE_BADSIZE": "sde26"})
    add("g3 read-only", ["g3_write"], {"FAKE_RO": "sde26"})
    add("g3 node mounted", ["g3_write"], {"FAKE_MOUNT": "sde26"})
    add("g3 whole LUN mounted", ["g3_write"], {"FAKE_MOUNT": "sde"})
    add("g3 not in recovery", ["g3_write"], {"FAKE_STATE": "device"})
    add("g3 pushed image corrupt", ["g3_write"], {"FAKE_PUSH_CORRUPT": "1"})
    add("g3 push fails", ["g3_write"], {"FAKE_PUSH_FAIL": "1"})
    add("g3 target differs from its backup", ["g3_write"], {"FAKE_DIFFERENT": "sde26"})
    add("g3 padded hash wrong", ["g3_write"], override="pad-hash")
    add("g3 padded image has the wrong size", ["g3_write"], override="pad-short")
    add("g3 no hash for boot_b", ["g3_write"], override="run-nosums")
    add("g3 dd fails", ["g3_write"], {"FAKE_DD_FAIL": "1"})
    add("g3 dd does nothing", ["g3_write"], {"FAKE_DD_SILENT": "1"})
    # The function takes boot_a and boot_b only, and a hash only in the right form.
    for key in ("name_misc", "name_vbmeta", "name_userdata", "name_node", "name_boot", "bad_hash", "short_hash", "no_mode"):
        add("refuse " + key, [key])
    # G3 rollback
    add("g3 rollback after a write", ["g3_write", "g3_rollback"])
    add("g3 rollback from the bootloader menu", ["g3_rollback"], {"FAKE_START": "bootloader"})
    add("g3 rollback, fastboot rejects the boot", ["g3_rollback"], {"FAKE_START": "bootloader", "FAKE_FASTBOOT_FAIL": "1"})
    add("g3 rollback with a corrupt backup file", ["g3_rollback"], override="run-corrupt")
    # G4 variant B
    add("g4b happy", ["g3_write", "g4_b_write", "g4_b_cold"])
    add("g4b wrong link", ["g4_b_write"], {"FAKE_LINK_WRONG": "boot_a"})
    add("g4b wrong size", ["g4_b_write"], {"FAKE_BADSIZE": "sde9"})
    add("g4b read-only", ["g4_b_write"], {"FAKE_RO": "sde9"})
    add("g4b mounted", ["g4_b_write"], {"FAKE_MOUNT": "sde9"})
    add("g4b pushed image corrupt", ["g4_b_write"], {"FAKE_PUSH_CORRUPT": "1"})
    add("g4b target differs from its backup", ["g4_b_write"], {"FAKE_DIFFERENT": "sde9"})
    add("g4b no hash for boot_a", ["g4_b_write"], override="run-nosums")
    add("g4b dd does nothing", ["g4_b_write"], {"FAKE_DD_SILENT": "1"})
    add("g4b rollback after a write", ["g4_b_write", "g4_b_rollback"])
    add("g4b rollback from the bootloader menu", ["g4_b_rollback"], {"FAKE_START": "bootloader"})
    add("g5 variant B rollback after a write", ["g4_b_write", "g5_b_rollback"])
    add("g5 variant B rollback, TWRP not running", ["g5_b_rollback"], {"FAKE_START": "bootloader"})
    # G4 variant A and its rollbacks
    add("g4a happy", ["g4_a_write"])
    add("g4a slot b unbootable", ["g4_a_write"], {"FAKE_UNBOOTABLE_B": "yes"})
    add("g4a no retries left", ["g4_a_write"], {"FAKE_RETRY_B": "0"})
    add("g4a set_active fails", ["g4_a_write"], {"FAKE_SET_ACTIVE_FAIL": "1"})
    add("g4a rollback", ["g4_a_write", "g4_a_rollback"])
    add("g5 variant A rollback", ["g4_a_write", "g5_a_rollback"])
    # Re-arming the brake and the G6 disarm
    add("g5 re-arm from TWRP", ["g5_rearm"])
    add("g5 re-arm from the bootloader menu", ["g5_rearm"], {"FAKE_START": "bootloader", "FAKE_STATE": "recovery"})
    add("g6 disarm", ["g6_disarm"])
    add("g6 re-arm", ["g6_rearm"])
    # Waiting is bounded
    add("wait for recovery gives up", ["wait_recovery_3"], {"FAKE_START": "bootloader"})
    only = [x for x in os.environ.get("OPOS_PERSIST_BLOCK_SCENARIOS", "").split("|") if x]   # a subset, for a quick local run
    return [s for s in S if not only or s["label"] in only]


@functools.lru_cache(maxsize=None)
def run_blocks():
    blocks = runbook_blocks()
    spec = {"blocks": blocks, "scenarios": block_scenarios()}
    return run_powershell(FAKE_BLOCKS, timeout=1800, exe=windows_powershell(),
                          stdin=FAKE_CS.read_bytes() + PART.encode("ascii") + json.dumps(spec).encode("ascii"))


@unittest.skipUnless(powershells(), "needs pwsh or powershell")
class PersistRunbookBlocksParse(unittest.TestCase):
    def test_every_powershell_block_of_the_runbook_parses_on_each_powershell(self):
        blocks = fenced_blocks(runbook())
        self.assertGreaterEqual(len(blocks), 20)
        for major, exe in sorted(powershells().items()):
            with self.subTest(powershell=major):
                got = run_powershell(PARSE_BLOCKS, exe=exe, stdin=json.dumps(blocks).encode("ascii"))
                self.assertEqual(len(as_list(got["results"])), len(blocks))
                for item in as_list(got["results"]):
                    self.assertEqual(as_list(item["errors"]), [], item["head"])


@unittest.skipUnless(windows_powershell(), "needs Windows PowerShell 5.1 to compile the stand-in adb and fastboot")
class PersistRunbookBlocksAgainstStandIns(unittest.TestCase):
    """The blocks of G3 to G6 are taken out of the runbook text and RUN, in Windows PowerShell 5.1, against the stand-in tools
    of tests/fixtures/fake_duo_tools.cs with a store: pushed files, dd onto a block device, fastboot boot and set_active are
    kept there. A guard in the text that is only a comment would let the dd run; here it must throw first."""

    @classmethod
    def setUpClass(cls):
        got = run_blocks()
        cls.hash_a, cls.hash_b, cls.pad_hash = got["hash_a"], got["hash_b"], got["pad_hash"]
        cls.by = {s["label"]: s for s in as_list(got["scenarios"])}

    def sc(self, label):
        if label not in self.by:
            self.skipTest("scenario %r was not run (OPOS_PERSIST_BLOCK_SCENARIOS)" % label)
        return self.by[label]

    def messages(self, label):
        return [(s["step"], s["message"]) for s in as_list(self.sc(label)["steps"])]

    def calls(self, label):
        return as_list(self.sc(label)["calls"])

    def dd_calls(self, label):
        return [c for c in self.calls(label) if " of=" in c]

    def assert_boot_partitions_untouched(self, label):
        s = self.sc(label)
        self.assertEqual((s["sha9"], s["sha26"]), (self.hash_a, self.hash_b), "a boot partition changed")

    def index(self, calls, needle, start=0):
        for i in range(start, len(calls)):
            if needle in calls[i]:
                return i
        self.fail("no call with %r after %d in %s" % (needle, start, calls))

    def test_the_padded_image_is_the_pinned_image_plus_zeros(self):
        # The pad block ran in setUpClass without a throw (the harness would have failed): its hash is a real SHA-256.
        self.assertRegex(self.pad_hash, r"^[0-9a-f]{64}$")
        self.assertNotIn(self.pad_hash, (self.hash_a, self.hash_b))

    def test_the_g3_block_writes_boot_b_once_and_only_after_every_check(self):
        label = "g3 happy"
        self.assertEqual([m for _, m in self.messages(label)], [None, None])
        self.assertEqual(self.dd_calls(label), [DD_B])
        calls = self.calls(label)
        at = -1
        for needle in ("get-state", "readlink -f %s/boot_b" % BYNAME, "blockdev --getsize64 /dev/block/sde26", "blockdev --getro /dev/block/sde26",
                       "cat /proc/mounts", "sha256sum /dev/block/sde26", "push <file> /tmp/opos-image.img", "sha256sum /tmp/opos-image.img"):
            at2 = self.index(calls, needle, at + 1)
            self.assertGreater(at2, at, needle)
            at = at2
        dd_at = calls.index(DD_B)
        self.assertGreater(dd_at, at, "the dd came before a check")
        after = dd_at
        for needle in ("shell sync", "echo 3 > /proc/sys/vm/drop_caches", "sha256sum /dev/block/sde26"):
            after = self.index(calls, needle, after + 1)
        # The result: boot_b holds the padded image, boot_a is untouched.
        self.assertEqual((self.sc(label)["sha26"], self.sc(label)["sha9"]), (self.pad_hash, self.hash_a))

    def test_each_wrong_value_makes_the_g3_block_throw_before_any_dd(self):
        cases = {
            "g3 wrong link": "where %s/boot_b points is '/dev/block/sde99', expected '/dev/block/sde26'" % BYNAME,
            "g3 wrong size": "size of /dev/block/sde26 in bytes is '100667392', expected '100663296'",
            "g3 read-only": "read-only flag of /dev/block/sde26 is '1', expected '0'",
            "g3 node mounted": "mounts of /dev/block/sde26 is '1', expected '0'",
            "g3 whole LUN mounted": "mounts of /dev/block/sde26 is '1', expected '0'",
            "g3 not in recovery": "adb state is 'device', expected 'recovery'",
            "g3 pushed image corrupt": "SHA-256 of the pushed image on the phone is",
            "g3 push fails": "exited with 1",
            "g3 target differs from its backup": "SHA-256 of /dev/block/sde26 now (its G1 backup) is",
            "g3 padded hash wrong": "on the PC is '%s', expected '%s'" % (self.pad_hash, "f" * 64),
            "g3 padded image has the wrong size": "in bytes is '1048576', expected '100663296'",
        }
        for label, fragment in cases.items():
            with self.subTest(label=label):
                steps = self.messages(label)
                self.assertEqual(len(steps), 1)
                self.assertIsNotNone(steps[0][1], "the block did not throw")
                self.assertTrue(steps[0][1].startswith("STOP: ") or "exited with" in steps[0][1], steps[0][1])
                self.assertIn(fragment, steps[0][1])
                self.assertEqual(self.dd_calls(label), [], "a dd was issued although a check failed")
                self.assert_boot_partitions_untouched(label)

    def test_a_missing_hash_in_the_backup_sums_stops_the_block_before_it_talks_to_the_phone(self):
        for label, name in (("g3 no hash for boot_b", "CurrentHash"), ("g4b no hash for boot_a", "CurrentHash")):
            with self.subTest(label=label):
                (step, message), = self.messages(label)
                self.assertIsNotNone(message)
                self.assertIn(name, message)
                # Nothing but the question whether TWRP is listed has been asked: no call names the device.
                self.assertEqual([c for c in self.calls(label) if c != "adb devices"], [])
                self.assert_boot_partitions_untouched(label)

    def test_the_function_refuses_any_other_name_and_any_odd_hash_before_it_talks_to_the_phone(self):
        for key in ("name_misc", "name_vbmeta", "name_userdata", "name_node", "name_boot", "short_hash", "no_mode"):
            label = "refuse " + key
            with self.subTest(label=label):
                (step, message), = self.messages(label)
                self.assertIsNotNone(message, "the call was accepted")
                self.assertEqual(self.calls(label), [], "the phone was contacted")
                self.assert_boot_partitions_untouched(label)
        # An upper-case hash passes the binder's pattern but never equals the lower-case one the checks compare with.
        (step, message), = self.messages("refuse bad_hash")
        self.assertIn("on the PC is", message)
        self.assertEqual(self.dd_calls("refuse bad_hash"), [])
        self.assert_boot_partitions_untouched("refuse bad_hash")
        for key in ("name_misc", "name_vbmeta", "name_userdata", "name_node", "name_boot"):
            self.assertRegex(self.messages("refuse " + key)[0][1], r"boot_a,\s*boot_b")

    def test_a_failed_dd_or_a_dd_that_changes_nothing_stops_the_block_and_no_read_back_passes(self):
        (_, message), = self.messages("g3 dd fails")
        self.assertIn("exited with 1", message)
        self.assertEqual(self.dd_calls("g3 dd fails"), [DD_B])
        (_, message), = self.messages("g3 dd does nothing")
        self.assertIsNotNone(message, "the block read back an unchanged partition and went on")
        self.assertIn("SHA-256 of /dev/block/sde26 after the write is '%s', expected '%s'" % (self.hash_b, self.pad_hash), message)
        self.assertEqual(self.dd_calls("g3 dd does nothing"), [DD_B])
        for label in ("g3 dd fails", "g3 dd does nothing"):
            self.assert_boot_partitions_untouched(label)

    def test_the_g3_rollback_restores_boot_b_from_the_backup_and_checks_it(self):
        label = "g3 rollback after a write"
        self.assertEqual([m for _, m in self.messages(label)], [None, None])
        self.assertEqual(self.dd_calls(label), [DD_B, DD_B])
        self.assertEqual((self.sc(label)["sha26"], self.sc(label)["sha9"]), (self.hash_b, self.hash_a))
        calls = self.calls(label)
        # TWRP was running, so the rollback did not try a RAM-boot.
        self.assertFalse([c for c in calls if c.startswith("fastboot")])
        # Push of the backup, hash check on the phone, dd, sync, drop_caches, re-read: in this order after the second dd's checks.
        first_dd = calls.index(DD_B)
        push = self.index(calls, "push <file> /tmp/opos-image.img", first_dd + 1)
        check = self.index(calls, "sha256sum /tmp/opos-image.img", push + 1)
        second_dd = self.index(calls, " of=", check + 1)
        self.assertGreater(second_dd, check)
        tail = second_dd
        for needle in ("shell sync", "echo 3 > /proc/sys/vm/drop_caches", "sha256sum /dev/block/sde26"):
            tail = self.index(calls, needle, tail + 1)

    def test_the_rollbacks_start_with_a_ram_boot_of_twrp_and_an_adb_devices_wait_when_the_phone_is_in_the_menu(self):
        for label, dd in (("g3 rollback from the bootloader menu", DD_B), ("g4b rollback from the bootloader menu", DD_A),
                          ("g5 variant B rollback, TWRP not running", DD_A)):
            with self.subTest(label=label):
                self.assertEqual([m for _, m in self.messages(label)], [None])
                calls = self.calls(label)
                self.assertEqual(calls[0], "adb devices", "the first call finds out whether TWRP is already running")
                boot = self.index(calls, "fastboot -s <serial> boot ")
                self.assertTrue(calls[boot].endswith("surfaceduo1-twrp.img"))
                wait = self.index(calls, "adb devices", boot + 1)
                first_adb = self.index(calls, "adb -s <serial>")
                self.assertLess(boot, wait)
                self.assertLess(wait, first_adb)
                self.assertEqual(self.dd_calls(label), [dd])
        self.assertEqual(self.sc("g3 rollback from the bootloader menu")["sha26"], self.hash_b)
        self.assertEqual(self.sc("g4b rollback from the bootloader menu")["sha9"], self.hash_a)

    def test_a_rejected_ram_boot_or_a_corrupt_backup_file_stops_the_rollback_before_any_dd(self):
        (_, message), = self.messages("g3 rollback, fastboot rejects the boot")
        self.assertIn("STOP: fastboot boot", message)
        self.assertIn("FAILED", message)
        (_, message), = self.messages("g3 rollback with a corrupt backup file")
        self.assertIn("boot_b.img on the PC is", message)
        for label in ("g3 rollback, fastboot rejects the boot", "g3 rollback with a corrupt backup file"):
            self.assertEqual(self.dd_calls(label), [], label)
            self.assert_boot_partitions_untouched(label)

    def test_g4_variant_b_writes_boot_a_after_its_own_checks_and_its_rollback_restores_it(self):
        label = "g4b happy"
        self.assertEqual([m for _, m in self.messages(label)], [None, None, None])
        self.assertEqual(self.dd_calls(label), [DD_B, DD_A])
        self.assertEqual((self.sc(label)["sha9"], self.sc(label)["sha26"]), (self.pad_hash, self.pad_hash))
        calls = self.calls(label)
        second = calls.index(DD_A)
        self.assertGreater(self.index(calls, "readlink -f %s/boot_a" % BYNAME, calls.index(DD_B) + 1), calls.index(DD_B))
        self.assertLess(self.index(calls, "readlink -f %s/boot_a" % BYNAME, calls.index(DD_B) + 1), second)
        for label2, why in (("g4b wrong link", "where %s/boot_a points is '/dev/block/sde99', expected '/dev/block/sde9'" % BYNAME),
                            ("g4b wrong size", "size of /dev/block/sde9 in bytes is '100667392'"),
                            ("g4b read-only", "read-only flag of /dev/block/sde9 is '1'"),
                            ("g4b mounted", "mounts of /dev/block/sde9 is '1'"),
                            ("g4b pushed image corrupt", "SHA-256 of the pushed image on the phone is"),
                            ("g4b target differs from its backup", "SHA-256 of /dev/block/sde9 now (its G1 backup) is")):
            with self.subTest(label=label2):
                (_, message), = self.messages(label2)
                self.assertIn(why, message)
                self.assertEqual(self.dd_calls(label2), [])
                self.assert_boot_partitions_untouched(label2)
        (_, message), = self.messages("g4b dd does nothing")
        self.assertIn("SHA-256 of /dev/block/sde9 after the write is", message)
        back = self.sc("g4b rollback after a write")
        self.assertEqual([m for _, m in self.messages("g4b rollback after a write")], [None, None])
        self.assertEqual((back["sha9"], back["sha26"]), (self.hash_a, self.hash_b))
        self.assertEqual(self.dd_calls("g4b rollback after a write"), [DD_A, DD_A])
        both = self.sc("g5 variant B rollback after a write")
        self.assertEqual([m for _, m in self.messages("g5 variant B rollback after a write")], [None, None])
        self.assertEqual(both["sha9"], self.hash_a)

    def test_g4_variant_a_checks_the_slot_first_and_its_rollbacks_go_back_to_a(self):
        happy = self.sc("g4a happy")
        self.assertEqual([m for _, m in self.messages("g4a happy")], [None])
        self.assertEqual(happy["slot"], "b")
        calls = self.calls("g4a happy")
        self.assertEqual([c for c in calls if "set_active" in c], ["fastboot -s <serial> set_active b"])
        self.assertLess(self.index(calls, "getvar slot-unbootable:b"), self.index(calls, "set_active b"))
        self.assertLess(self.index(calls, "getvar slot-retry-count:b"), self.index(calls, "set_active b"))
        self.assertLess(self.index(calls, "set_active b"), self.index(calls, "getvar current-slot"))
        for label, why in (("g4a slot b unbootable", "slot b is not marked bootable"), ("g4a no retries left", "slot b has no retries left")):
            with self.subTest(label=label):
                (_, message), = self.messages(label)
                self.assertIn(why, message)
                self.assertEqual([c for c in self.calls(label) if "set_active" in c], [])
                self.assertEqual(self.sc(label)["slot"], "")
        (_, message), = self.messages("g4a set_active fails")
        self.assertIn("STOP: fastboot set_active b said:", message)
        self.assertEqual(self.sc("g4a set_active fails")["slot"], "")
        for label in ("g4a rollback", "g5 variant A rollback"):
            with self.subTest(label=label):
                self.assertEqual([m for _, m in self.messages(label)], [None, None])
                self.assertEqual(self.sc(label)["slot"], "a")
                self.assertIn("fastboot -s <serial> set_active a", self.calls(label))

    def test_the_brake_blocks_erase_and_flash_only_misc_and_bring_the_phone_to_the_menu_first(self):
        calls = self.calls("g5 re-arm from TWRP")
        self.assertEqual([m for _, m in self.messages("g5 re-arm from TWRP")], [None])
        order = [self.index(calls, n) for n in ("adb -s <serial> reboot bootloader", "fastboot devices", "fastboot -s <serial> erase misc",
                                                 "fastboot -s <serial> flash misc ")]
        self.assertEqual(order, sorted(order))
        self.assertTrue(calls[order[-1]].endswith("misc-brake.img"))
        from_menu = self.calls("g5 re-arm from the bootloader menu")
        self.assertFalse([c for c in from_menu if "reboot bootloader" in c])
        self.assertIn("fastboot -s <serial> erase misc", from_menu)
        disarm = self.calls("g6 disarm")
        self.assertEqual([c for c in disarm if c.startswith("fastboot")], ["fastboot -s <serial> erase misc"])
        self.assertEqual(self.calls("g6 re-arm"), self.calls("g5 re-arm from TWRP"))
        for label in ("g5 re-arm from TWRP", "g5 re-arm from the bootloader menu", "g6 disarm", "g6 re-arm"):
            self.assert_boot_partitions_untouched(label)

    def test_waiting_for_the_phone_is_bounded(self):
        (_, message), = self.messages("wait for recovery gives up")
        self.assertIn("adb did not list SER1 as recovery within 3 seconds", message)
        self.assertLess(self.sc("wait for recovery gives up")["seconds"], 30)


if __name__ == "__main__":
    unittest.main()
