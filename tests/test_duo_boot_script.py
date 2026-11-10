# SPDX-License-Identifier: GPL-3.0-or-later
"""deploy/duo-opos-boot.ps1 installs and RAM-boots the Duo OS image; it is never run here.
The text tests read it as a file. The PowerShell tests parse it (and run its network probe against loopback)
with pwsh or powershell when one is installed, and skip otherwise."""
import base64
import functools
import json
import re
import shlex
import shutil
import struct
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


def function_text(name):
    """The source of one function of the script (it ends at the first closing brace in column 0)."""
    found = re.search(r"^function %s\b.*?^\}$" % re.escape(name), code(), re.S | re.M)
    assert found, name
    return found.group(0)


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
        first_write = body.index("Set-MiscBrake $p[")
        self.assertLess(body.index("hash ok"), first_write)
        self.assertLess(body.index("hash mismatch"), first_write)
        for check in ("product", "current-slot", "unlocked", "battery-level"):
            self.assertLess(body.index("Fb getvar " + check), first_write)
        self.assertRegex(body, r"\[int\]\$bat -lt 20")
        # The rootfs is compared on the phone before the phone leaves TWRP.
        self.assertLess(body.index("sha256sum /data/rootfs.img"), body.index("Ab reboot bootloader"))
        self.assertLess(body.index("Ab reboot bootloader"), boots[1])
        # The brake is armed before TWRP and again before the kernel, both times through Set-MiscBrake.
        self.assertEqual(body.count("Set-MiscBrake $p['misc-brake.img']"), 2)
        self.assertLess(body.index("Set-MiscBrake $p["), boots[0])
        self.assertLess(body.rindex("Set-MiscBrake $p["), boots[1])
        self.assertGreater(body.rindex("Set-MiscBrake $p["), boots[0])

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

    def test_preflight_checks_each_throw_and_no_native_fastboot_call_can_bypass_the_wrapper(self):
        body = code()
        checks = {"product": ("surfaceduo", "not a Surface Duo 1"),
                  "current-slot": ("current-slot:", "current slot is not a"),
                  "unlocked": ("unlocked:", "bootloader is not unlocked")}
        for var, (pattern, notice) in checks.items():
            with self.subTest(getvar=var):
                lines = [line for line in body.splitlines() if "Fb getvar %s)" % var in line]
                self.assertEqual(len(lines), 1)
                self.assertIn("-notmatch", lines[0])
                self.assertIn(pattern, lines[0])
                self.assertIn("throw '%s'" % notice, lines[0])
        battery = [line for line in body.splitlines() if "[int]$bat -lt 20" in line]
        self.assertEqual(len(battery), 1)
        self.assertIn("throw", battery[0])
        # Every fastboot and adb command names the phone by serial.
        for name in ("Fb", "Ab"):
            with self.subTest(wrapper=name):
                self.assertIn("@('-s', $script:Serial)", function_text(name))
        # The only places that run fastboot.exe are the Fb wrapper and the device listing; the PATH check only looks.
        outside = body.replace(function_text("Fb"), "").replace(function_text("FastbootSerials"), "")
        for line in outside.splitlines():
            if "fastboot.exe" in line:
                with self.subTest(line=line.strip()):
                    self.assertIn("Get-Command $t", line)
                    self.assertNotRegex(line, r"&|Native|Start-Process")
        self.assertEqual(body.count("Native 'fastboot.exe'"), 1)
        self.assertIn("Native 'fastboot.exe'", function_text("Fb"))
        self.assertIn("& fastboot.exe devices", function_text("FastbootSerials"))
        self.assertNotRegex(body, r"(?i)Start-Process|Invoke-Expression|\biex\b|&\s*['\"]?\$?fastboot\b(?!\.exe devices)")
        # The one adb call outside the wrapper (the push) names the serial too.
        for call in re.findall(r"Native 'adb\.exe' @\(([^)]*)\)", body):
            self.assertTrue(call.startswith("'-s', $Serial"), call)

    def test_phone_ip_and_prefer_host_are_checked_first(self):
        body = code()
        self.assertIn("throw '-PhoneIp must be an IPv4 address'", body)
        self.assertIn("throw '-PreferHost must be a host number from 1 to 254'", body)
        for check in ("-PhoneIp must be an IPv4 address", "-PreferHost must be a host number"):
            at = body.index(check)
            with self.subTest(check=check):
                for later in ("Resolve-Path", "New-Item", "Get-Command $t", "Get-FileHash", "if (-not $Yes)",
                              "@(FastbootSerials)", "Fb getvar", "Set-MiscBrake $p[", "Fb boot", "Invoke-Ssh $ip"):
                    self.assertLess(at, body.index(later), later)
        self.assertIn("if ($PhoneIp -and -not (Test-Ipv4 $PhoneIp))", body)

    def test_ssh_keeps_the_known_hosts_warning_out_of_the_saved_text(self):
        ssh = function_text("Invoke-Ssh")
        self.assertIn("2>&1", ssh)
        self.assertIn("-o LogLevel=ERROR", ssh)
        for option in ("BatchMode=yes", "IdentitiesOnly=yes", "-i $Key", "StrictHostKeyChecking=no"):
            self.assertIn(option, ssh)
        # Only the PATH check and this one call mention ssh.exe.
        self.assertEqual(len([line for line in code().splitlines() if "ssh.exe" in line]), 2)

    def test_header_documents_the_install_chain_guards(self):
        flat = " ".join(header().replace("#", " ").split())
        for rule_text in ("must be a RAW ext4 image",
                          "zstd, gzip, xz and Android sparse files are refused by their magic bytes",
                          "the brake may be ABSENT",
                          "tries the brake image once more (no second erase)",
                          "stay in Boot Select",
                          "adb commands included",
                          "keep them open",
                          "never the originals",
                          "refuses to guess",
                          "fingerprint that ssh accepted is printed"):
            with self.subTest(rule=rule_text):
                self.assertIn(rule_text, flat)

    def test_the_brake_is_written_in_one_place_and_the_retry_does_not_erase_again(self):
        body = code()
        brake = function_text("Set-MiscBrake")
        outside = body.replace(brake, "")
        self.assertNotIn("Fb erase misc", outside)
        self.assertNotIn("Fb flash misc", outside)
        self.assertEqual(brake.count("Fb erase misc"), 1)
        self.assertEqual(brake.count("Fb flash misc"), 2)
        first_catch = brake.index("catch {")
        self.assertGreater(brake.rindex("Fb flash misc"), first_catch)
        self.assertNotIn("Fb erase misc", brake[first_catch:])
        # The failure is said where it happens, and the script stops whatever the second try does.
        self.assertIn("Show-BrakeWarning", brake)
        self.assertIn("BRAKE WRITE FAILED", brake)
        self.assertEqual(brake.count("throw "), 2)
        warning = function_text("Show-BrakeWarning")
        for words in ("MAY BE ABSENT", "KEEP THE PHONE IN BOOT SELECT", "power-cycle", "restart on its own", "wipes userdata"):
            with self.subTest(words=words):
                self.assertIn(words, warning)
        # ... and again last, in the catch, where it is not scrolled away.
        caught = body[body.index("} catch {\n    $failed = $true"):]
        self.assertIn("if ($script:brakeMayBeAbsent)", caught)
        self.assertLess(caught.index("Show-BrakeWarning"), caught.index("} finally {"))

    def test_the_brake_flag_goes_up_before_the_erase_and_comes_down_only_after_a_confirmed_flash(self):
        body = code()
        brake = function_text("Set-MiscBrake")
        # Up once, before anything is written: what the catch below never sees (Ctrl+C) still leaves it up.
        self.assertEqual(brake.count("$script:brakeMayBeAbsent = $true"), 1)
        self.assertEqual(body.count("brakeMayBeAbsent = $true"), 1)
        self.assertLess(brake.index("$script:brakeMayBeAbsent = $true"), brake.index("Fb erase misc"))
        self.assertLess(brake.index("$script:brakeMayBeAbsent = $true"), brake.index("try {"))
        # Down twice: after the erase and flash of the first try, and after the flash of the second try.
        downs = [m.start() for m in re.finditer(re.escape("$script:brakeMayBeAbsent = $false"), brake)]
        self.assertEqual(len(downs), 2)
        self.assertGreater(downs[0], brake.index("} catch {"))
        self.assertLess(downs[0], brake.index("BRAKE WRITE FAILED"))
        self.assertGreater(downs[1], brake.rindex("Fb flash misc"))
        # The finally keeps the brake image whenever the flag is up, and says so when the catch did not.
        final = body[body.index("} finally {\n    if ($script:brakeMayBeAbsent)"):]
        self.assertIn("if (-not $failed) { try { Show-BrakeWarning; Show-BrakeByHand } catch { } }", final)
        self.assertLess(final.index("Show-BrakeByHand"), final.index("Release-Staged"))

    def test_the_by_hand_brake_command_puts_the_staged_path_in_double_quotes(self):
        hand = function_text("Show-BrakeByHand")
        self.assertIn("flash misc `\"$($p['misc-brake.img'])`\"\"", hand)
        self.assertNotIn("$orig", hand)
        self.assertNotIn("flash misc $(", hand)

    def test_the_log_is_best_effort_and_every_line_reaches_the_screen(self):
        log = function_text("Log")
        self.assertIn("try { Add-Content -LiteralPath $script:log -Value $l -ErrorAction Stop }", log)
        self.assertIn("\n    catch {\n", log)
        # The screen line comes after the catch and is not inside any try, so a failed write cannot hide it.
        self.assertTrue(log.endswith("    }\n    Write-Host $l\n}"), log)
        self.assertEqual(log.count("Write-Host $l"), 1)
        self.assertEqual(log.count("try {"), 1)

    def test_ssh_runs_in_the_log_folder_and_the_key_is_made_absolute(self):
        ssh = function_text("Invoke-Ssh")
        self.assertIn("Push-Location -LiteralPath $LogDir", ssh)
        self.assertIn("finally { Pop-Location }", ssh)
        self.assertIn("-o UserKnownHostsFile=known_hosts", ssh)
        self.assertNotIn("$kh", ssh)
        self.assertLess(ssh.index("Push-Location"), ssh.index("& ssh.exe"))
        self.assertLess(ssh.index("& ssh.exe"), ssh.index("Pop-Location"))
        # -Key is resolved while the location is still the caller's, before Invoke-Ssh changes it.
        body = code()
        self.assertIn("$Key = (Resolve-Path -LiteralPath $Key).ProviderPath", body)
        self.assertLess(body.index("-Key $Key is not a file"), body.index("$Key = (Resolve-Path"))
        self.assertLess(body.index("$Key = (Resolve-Path"), body.index("Invoke-Ssh $ip"))

    def test_ab_stops_on_a_nonzero_exit_and_the_umount_is_one_checked_sequence(self):
        body = code()
        self.assertRegex(function_text("Ab"), r"if \(\$r\.Code -ne 0\) \{ throw ")
        self.assertIn("Ab shell 'sync && umount /data'", body)
        self.assertIn("Ab shell 'sync && sha256sum /data/rootfs.img'", body)
        self.assertNotRegex(body, r"sync;")
        # The unmount is checked before the phone leaves TWRP, which is before the kernel boot.
        self.assertLess(body.index("sync && umount /data"), body.index("Ab reboot bootloader"))
        self.assertLess(body.index("Ab reboot bootloader"), body.rindex("Fb boot"))
        # The "is /data mounted" question answers with text on success and failure, so its own message survives.
        self.assertIn("echo MOUNTED || echo UNMOUNTED", body)
        self.assertIn(r"-notmatch '\bMOUNTED\b'", body)

    def test_the_rootfs_is_checked_as_raw_ext4_before_any_hash_copy_or_phone_command(self):
        body = code()
        at = body.index("Get-Ext4Problem $p[$Rootfs]")
        for later in ("Get-FileHash", "if (-not $Yes)", "Stage-Image $orig", "@(FastbootSerials)", "Fb getvar",
                      "Set-MiscBrake $p["):
            with self.subTest(later=later):
                self.assertLess(at, body.index(later))
        check = function_text("Get-Ext4Problem")
        for magic in ("0x28, 0xB5, 0x2F, 0xFD", "0x1F, 0x8B", "0xFD, 0x37, 0x7A, 0x58, 0x5A, 0x00", "0x3A, 0xFF, 0x26, 0xED"):
            with self.subTest(magic=magic):
                self.assertIn(magic, check)
        # Only the first 1200 bytes and the length are read: the image is never loaded.
        self.assertIn("New-Object byte[] 1400", check)
        self.assertIn("$fs.Length", check)
        self.assertNotRegex(check, r"ReadAllBytes|Get-Content|ReadToEnd|ComputeHash")
        for field in ("$b[1080]", "$b[1028]", "$b[1048]", "$b[1120]", "$b[1360]"):
            self.assertIn(field, check)

    def test_every_command_gets_a_staged_copy_and_the_handles_are_held_until_the_end(self):
        body = code()
        # The copies are made before the wait for the phone, which can last hours.
        self.assertLess(body.index("Stage-Image $orig[$n]"), body.index("@(FastbootSerials)"))
        self.assertLess(body.index("Stage-Image $orig[$n]"), body.index("Fb getvar"))
        stage = function_text("Stage-Image")
        self.assertIn("[System.IO.File]::Copy($src, $dst)", stage)
        self.assertIn("SetAttributes($dst, [System.IO.FileAttributes]::Normal)", stage)
        self.assertIn("[System.IO.FileAccess]::Read, [System.IO.FileShare]::Read", stage)
        # Hashed through the handle that is kept, after opening it: the bytes hashed are the bytes later read.
        self.assertLess(stage.index("New-Object System.IO.FileStream"), stage.index("ComputeHash($fs)"))
        self.assertNotIn("Get-FileHash", stage)
        self.assertIn("    } else {\n        Release-Staged $held $staged $stage\n    }\n}\nif ($failed) { exit 1 }", body)
        # ... except the verified brake image, kept for the by-hand command after a failed brake write.
        self.assertIn("if ($script:brakeMayBeAbsent) {\n        if (-not $failed) { try { Show-BrakeWarning; Show-BrakeByHand } catch { } }\n"
                      "        Release-Staged $held @($staged | Where-Object", body)
        self.assertIn("Where-Object { $_ -ne $p['misc-brake.img'] }", body)
        # The staged rootfs is checked again, with its copy open.
        self.assertIn("$bad = Get-Ext4Problem $dst", body)
        self.assertLess(body.index("$bad = Get-Ext4Problem $dst"), body.index("@(FastbootSerials)"))
        # The originals are named in messages only; no tool is ever handed one.
        for line in body.splitlines():
            if "$orig" in line:
                with self.subTest(line=line.strip()):
                    self.assertNotRegex(line, r"\b(Fb|Ab|Native)\b")
        self.assertEqual(body.count("$p[$n] = $dst"), 1)
        # A new folder under the log folder for every run, and it is never reused.
        self.assertIn("Join-Path $LogDir ('stage-'", body)
        self.assertIn("New-Item -ItemType Directory -Path $stage", body)

    def test_discovery_refuses_to_guess_between_adapters_and_shows_the_host_key(self):
        body = code()
        find = function_text("RndisAddress")
        self.assertIn("$all.Count -gt 1", find)
        self.assertIn("throw", find)
        self.assertIn("-PhoneIp", find)
        self.assertNotIn("return $a.IPAddress", body)
        # Adapters already up while the phone is still in Boot Select are refused in the preflight, before any write.
        early = body.index("$other = @(RndisAdapters -Any)")
        self.assertLess(body.index("preflight ok"), early)
        self.assertLess(early, body.index("Set-MiscBrake $p["))
        self.assertIn("if (-not $PhoneIp) {", body[early - 40:early])
        # The existing dev-key behaviour stays; the fingerprint ssh took is printed after the diagnostics.
        self.assertIn("StrictHostKeyChecking=no", function_text("Invoke-Ssh"))
        self.assertIn("ssh-keygen.exe -l -f $kh", function_text("Show-HostKey"))
        self.assertLess(body.index("Log 'diagnostics saved to boot-diag.txt'"), body.index("        Show-HostKey"))
        self.assertLess(body.index("        Show-HostKey"), body.index("Log 'DONE:"))


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


# The command line of a Windows process is limited to 32767 characters, which the larger programs below exceed once
# they are encoded. So the command line carries only this loader; the program and the script text travel on stdin,
# split at a marker line. The program is dot-sourced at the top level (global scope), with the script text in
# $global:ScriptText.
SCRIPT_MARKER = "\n#--OPOS-SCRIPT-FOLLOWS--\n"
LOADER = ('$all = [Console]::In.ReadToEnd(); $m = "`n#--OPOS-SCRIPT-FOLLOWS--`n"; '
          '$i = $all.IndexOf($m, [StringComparison]::Ordinal); $global:ScriptText = $all.Substring($i + $m.Length); '
          '. ([scriptblock]::Create($all.Substring(0, $i)))')


def run_powershell(program, timeout=180):
    """Run a PowerShell program with the script text available to it; its stdout is one JSON document."""
    program = "$ProgressPreference = 'SilentlyContinue'\n" + program
    encoded = base64.b64encode(LOADER.encode("utf-16-le")).decode("ascii")
    done = subprocess.run([powershell(), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                          input=program.encode("ascii") + SCRIPT_MARKER.encode("ascii") + SCRIPT.read_bytes(),
                          capture_output=True, timeout=timeout)
    out = done.stdout.decode("utf-8", "replace")
    if done.returncode != 0 or not out.strip():
        raise AssertionError("powershell exit %s\nstdout: %s\nstderr: %s" % (
            done.returncode, out[-2000:], done.stderr.decode("utf-8", "replace")[-2000:]))
    return json.loads(out[out.index("{"):])


AST = r"""
$text = $global:ScriptText
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

# The phone is host 82 of a /24 around this PC's own address; loopback stands in for that /24. A second
# listener on host 20 (same port) is something else that answers on TCP 22: it tells "the preferred host was
# probed first and alone" from "the sweep happened to find it".
DISCOVER = AST + r"""
. ([scriptblock]::Create((FunctionText 'Test-Tcp')))
. ([scriptblock]::Create((FunctionText 'Find-PhoneSsh')))
$listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Parse('127.0.0.82'), 0)
try { $listener.Start() } catch { '{"skip": "cannot bind 127.0.0.82"}'; exit 0 }
$port = $listener.LocalEndpoint.Port
$second = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Parse('127.0.0.20'), $port)
try { $second.Start() } catch { $listener.Stop(); '{"skip": "cannot bind 127.0.0.20 on the same port"}'; exit 0 }
$swept = @(Find-PhoneSsh '127.0.0.147' 200 $port 2500)
$preferred = @(Find-PhoneSsh '127.0.0.147' 82 $port 2500)
$preferred_other = @(Find-PhoneSsh '127.0.0.147' 20 $port 2500)
$second.Stop()
$only = @(Find-PhoneSsh '127.0.0.147' 200 $port 2500)
$listener.Stop()
$sw = [Diagnostics.Stopwatch]::StartNew()
$none = @(Find-PhoneSsh '127.0.0.147' 82 $port 1000)
[pscustomobject]@{ swept = $swept; preferred = $preferred; preferred_other = $preferred_other; only = $only; none = $none.Count; none_ms = $sw.ElapsedMilliseconds } | ConvertTo-Json -Compress
"""


def ipv4_program(cases):
    return (AST + ". ([scriptblock]::Create((FunctionText 'Test-Ipv4')))\n"
            "$cases = '" + json.dumps(cases) + "' | ConvertFrom-Json\n"
            "[pscustomobject]@{ results = @($cases | ForEach-Object { [bool](Test-Ipv4 $_) }) } | ConvertTo-Json -Compress\n")


# ssh.exe is replaced by a function that behaves like OpenSSH with StrictHostKeyChecking=no and no known_hosts
# entry: its first line, on stderr, is the "Permanently added" warning, unless LogLevel=ERROR was passed. It records
# where it was started: the PowerShell location of the moment, and, on Windows, the working directory a REAL native
# process gets from PowerShell (cmd.exe /c cd). Run once with a plain log folder and once with a space in its name.
SSH = AST + r"""
$base = Join-Path ([IO.Path]::GetTempPath()) ('opos-duo-ssh-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $base | Out-Null
try {
    $Key = Join-Path $base 'dev.key'
    Set-Content -LiteralPath $Key -Value 'stand-in private key' -Encoding ASCII
    $SshUser = 'droidian'
    . ([scriptblock]::Create((FunctionText 'Invoke-Ssh')))
    function global:ssh.exe {
        $global:SshArgs = @($args | ForEach-Object { "$_" })
        $global:SshCwd = (Get-Location).ProviderPath
        $global:NativeCwd = $null
        if ([Environment]::OSVersion.Platform -eq 'Win32NT') { $global:NativeCwd = ((& cmd.exe /c cd) -join '').Trim() }
        $global:LASTEXITCODE = 0
        if ($args -notcontains 'LogLevel=ERROR') { Write-Error "Warning: Permanently added '203.0.113.7' (ED25519) to the list of known hosts." }
        'state=running'
        '2026-11-07T10:00:00+00:00'
    }
    $got = @()
    foreach ($name in 'plain', 'with a space in it') {
        $LogDir = Join-Path $base $name
        New-Item -ItemType Directory -Path $LogDir | Out-Null
        $before = (Get-Location).ProviderPath
        $r = Invoke-Ssh '203.0.113.7' 'echo hi'
        $got += [pscustomobject]@{ name = $name; logdir = $LogDir; code = $r.Code; text = $r.Text; args = @($global:SshArgs)
            cwd = $global:SshCwd; native_cwd = $global:NativeCwd; location_restored = ((Get-Location).ProviderPath -eq $before) }
    }
    [pscustomobject]@{ runs = @($got) } | ConvertTo-Json -Compress -Depth 4
} finally {
    Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue
}
"""

# The whole script, run in this PowerShell session with fastboot.exe, adb.exe, ssh.exe, ssh-keygen.exe, the network
# cmdlets, Get-PnpDevice and Start-Sleep replaced by functions: every fastboot and adb call is appended to a log, and
# the fake phone answers a healthy preflight. PATH is emptied first, so even if a function were bypassed no real
# fastboot or adb could be found. The three pinned hashes in the script's copy are swapped for those of small
# stand-in files (data only; the pinning itself is tested as text above), the rootfs is a small raw ext4 stand-in,
# and the script's TCP probe is replaced by one that answers with its first host. $runs is a JSON list of
#   {label, args, rootfs (base64), key, fail, others, late_others, tamper, no_keygen, no_early_ext4,
#    imgdir, logdir, relative, lock_log_on, stop_on, stop_skip}
# and each run starts from freshly written image files. imgdir names the image folder under the work folder (default
# img) and logdir a log folder beside it (default: logs inside the image folder), so a folder name can have a space in
# it. relative lists the parameters (ImageDir, LogDir, Key) that are passed as paths relative to the work folder while
# the PowerShell location is the work folder and the process working directory is another one. lock_log_on is a regex:
# the log file is made read-only when a fastboot command matching it is issued. stop_on is a regex for a line the script
# prints: when that line (the stop_skip+1)th time is printed the script's output pipeline is stopped, which interrupts
# the script the way Ctrl+C does (the catch blocks are skipped, the finally blocks run). fail is a list of {tool, match, mode, skip, times, text}:
# the (skip+1)th call of that tool whose arguments match the regex fails `times` times, as a nonzero exit
# ('exit'), a nonzero exit with FAILED in the answer ('failed'), FAILED with exit 0 ('failed0') or a replaced
# answer ('output'). Every path handed to a tool is probed: can another opener still get it with FileShare.None
# (that is, is a handle held), and what does it hash to at that moment. tamper rewrites the original images when
# the first getvar is answered, as a download or a sync tool might. others are RNDIS-like adapters that are up
# from the start; late_others come up with the phone's own adapter once the kernel has been booted.
FAKE_PHONE = AST + r"""
$work = Join-Path ([IO.Path]::GetTempPath()) ('opos-duo-boot-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $empty = Join-Path $work 'empty-path'
    New-Item -ItemType Directory -Path $empty | Out-Null
    $env:PATH = $empty
    $global:CallLog = Join-Path $work 'calls.txt'
    $global:ProbeLog = Join-Path $work 'probes.txt'
    $global:SshLog = Join-Path $work 'ssh-calls.txt'
    $procdir = Join-Path $work 'process-cwd'
    New-Item -ItemType Directory -Path $procdir | Out-Null
    $startCwd = [Environment]::CurrentDirectory
    $img = Join-Path $work 'img'
    New-Item -ItemType Directory -Path $img | Out-Null
    $names = 'boot.img', 'surfaceduo1-twrp.img', 'misc-brake.img', 'opos-os-rootfs.img'
    $global:Originals = @($names | ForEach-Object { Join-Path $img $_ })
    $global:Expected = @{}
    foreach ($n in $names[0..2]) {
        Set-Content -LiteralPath (Join-Path $img $n) -Value ('stand-in ' + $n) -Encoding ASCII
        $actual = (Get-FileHash -LiteralPath (Join-Path $img $n) -Algorithm SHA256).Hash.ToLower()
        $pinned = [regex]::Match($text, "'" + [regex]::Escape($n) + "'\s*=\s*'([0-9a-f]{64})'").Groups[1].Value
        if (-not $pinned) { throw 'harness: a pinned hash was not found' }
        $text = $text.Replace($pinned, $actual)
        $global:Expected[$n] = $actual
    }
    if ($text -notmatch 'hash mismatch') { throw 'harness: stand-in hashes were not set up' }
    $tcp = 'function Test-Tcp([string[]]$Hosts, [int]$Port, [int]$TimeoutMs) {'
    if (-not $text.Contains($tcp)) { throw 'harness: Test-Tcp was not found' }
    $text = $text.Replace($tcp, $tcp + "`n" + '    return @($Hosts | Select-Object -First 1)')
    $defaultRootfs = New-Object byte[] 4096
    $defaultRootfs[1028] = 4
    $defaultRootfs[1080] = 0x53
    $defaultRootfs[1081] = 0xEF
    $defaultRootfs[3000] = 7
    function global:Reset-Images($b64) {
        foreach ($n in 'boot.img', 'surfaceduo1-twrp.img', 'misc-brake.img') {
            Set-Content -LiteralPath (Join-Path $img $n) -Value ('stand-in ' + $n) -Encoding ASCII
        }
        if ($null -ne $b64) { $bytes = [Convert]::FromBase64String($b64) } else { $bytes = $defaultRootfs }
        [IO.File]::WriteAllBytes((Join-Path $img 'opos-os-rootfs.img'), $bytes)
        $global:RootfsSum = (Get-FileHash -LiteralPath (Join-Path $img 'opos-os-rootfs.img') -Algorithm SHA256).Hash.ToLower()
        $global:Expected['rootfs.img'] = $global:RootfsSum
    }
    function global:Get-FailRule([string]$tool, [string]$a) {
        foreach ($r in $global:FailRules) {
            if ($r.tool -ne $tool -or $a -notmatch $r.match) { continue }
            if ($r.skip -gt 0) { $r.skip--; continue }
            if ($r.used -lt $r.times) { $r.used++; return $r }
        }
    }
    function global:Apply-Rule($r) {
        if ($r.mode -eq 'exit') { $global:LASTEXITCODE = 1; 'something went wrong' }
        elseif ($r.mode -eq 'failed') { $global:LASTEXITCODE = 1; "FAILED (remote: 'Device Error')" }
        elseif ($r.mode -eq 'failed0') { $global:LASTEXITCODE = 0; "FAILED (remote: 'Device Error')" }
        else { $global:LASTEXITCODE = 0; $r.text }
    }
    function global:Probe-Paths($argv) {
        foreach ($x in $argv) {
            $s = "$x"
            if ($s.Length -gt 3 -and (Test-Path -LiteralPath $s -PathType Leaf -ErrorAction SilentlyContinue)) {
                $heldByOther = $false
                try { $f = [IO.File]::Open($s, 'Open', 'Read', 'None'); $f.Dispose() } catch { $heldByOther = $true }
                $h = (Get-FileHash -LiteralPath $s -Algorithm SHA256).Hash.ToLower()
                Add-Content -LiteralPath $global:ProbeLog -Value (($s -replace '\\', '/') + '|' + $heldByOther + '|' + $h)
            }
        }
    }
    function global:fastboot.exe {
        $a = $args -join ' '
        Add-Content -LiteralPath $global:CallLog -Value ('fastboot ' + $a)
        $global:LASTEXITCODE = 0
        if ($null -eq $global:ProcCwd) { $global:ProcCwd = [Environment]::CurrentDirectory; $global:PsLoc = (Get-Location).ProviderPath }
        Probe-Paths $args
        if ($global:LockLogOn -and $a -match $global:LockLogOn -and (Test-Path -LiteralPath $global:LogFile)) {
            Set-ItemProperty -LiteralPath $global:LogFile -Name IsReadOnly -Value $true
        }
        if ($global:Tamper -and $a -match 'getvar product') {
            foreach ($f in $global:Originals) { Set-Content -LiteralPath $f -Value 'replaced after the hash check' -Encoding ASCII }
        }
        $rule = Get-FailRule 'fastboot' $a
        if ($rule) { Apply-Rule $rule; return }
        if ($a -eq 'devices') { "SER1`tfastboot" }
        elseif ($a -match 'getvar product') { 'product: surfaceduo' }
        elseif ($a -match 'getvar current-slot') { 'current-slot: a' }
        elseif ($a -match 'getvar unlocked') { 'unlocked: yes' }
        elseif ($a -match 'getvar battery-level') { 'battery-level: 80' }
        elseif ($a -match ' boot .*boot\.img$') { $global:KernelBooted = $true }
    }
    function global:adb.exe {
        $a = $args -join ' '
        Add-Content -LiteralPath $global:CallLog -Value ('adb ' + $a)
        $global:LASTEXITCODE = 0
        Probe-Paths $args
        $rule = Get-FailRule 'adb' $a
        if ($rule) { Apply-Rule $rule; return }
        if ($a -eq 'devices') { 'List of devices attached'; "SER1`trecovery" }
        elseif ($a -match 'echo MOUNTED') { 'MOUNTED' }
        elseif ($a -match 'sha256sum') { $global:RootfsSum + '  /data/rootfs.img' }
        elseif ($a -match ' push ') { '1 file pushed' }
    }
    function global:Get-PnpDevice { [pscustomobject]@{ InstanceId = 'USB\VID_1D6B&PID_0104\1' } }
    function global:Get-NetAdapter {
        [CmdletBinding()] param()
        $global:Others
        if ($global:KernelBooted) { $global:LateOthers; [pscustomobject]@{ Name = 'Ethernet 9'; InterfaceDescription = 'Remote NDIS based Internet Sharing Device'; Status = 'Up'; ifIndex = 90 } }
        [pscustomobject]@{ Name = 'Wi-Fi'; InterfaceDescription = 'Intel(R) Wi-Fi 6 AX201'; Status = 'Up'; ifIndex = 3 }
        [pscustomobject]@{ Name = 'Ethernet 4'; InterfaceDescription = 'Remote NDIS Compatible Device'; Status = 'Disconnected'; ifIndex = 4 }
    }
    function global:Get-NetIPAddress {
        [CmdletBinding()] param($InterfaceIndex, $AddressFamily)
        $ip = $global:AddrOf["$InterfaceIndex"]
        if ($ip) { [pscustomobject]@{ IPAddress = $ip } }
    }
    # Like a native ssh: it starts in the PowerShell location of the moment (which is what PowerShell does for a native
    # tool), it cannot log in without its identity file, and the value of UserKnownHostsFile is a list of files
    # separated by blanks of which the first receives a new host key. Every call is recorded as a line of JSON.
    function global:ssh.exe {
        $cwd = (Get-Location).ProviderPath
        $argv = @($args | ForEach-Object { "$_" })
        $global:LASTEXITCODE = 0
        $call = [ordered]@{ cwd = $cwd; argv = @($argv | Select-Object -First ($argv.Count - 2)); key_found = $true; wrote = '' }
        $ki = [array]::IndexOf($argv, '-i')
        if ($ki -ge 0 -and -not (Test-Path -LiteralPath $argv[$ki + 1] -PathType Leaf)) {
            $call.key_found = $false
            Add-Content -LiteralPath $global:SshLog -Value ($call | ConvertTo-Json -Compress)
            $global:LASTEXITCODE = 255
            'Permission denied (publickey).'
            return
        }
        $kv = @($argv | Where-Object { $_ -like 'UserKnownHostsFile=*' })
        if ($kv.Count) {
            $first = @(($kv[0] -replace '^UserKnownHostsFile=', '') -split '\s+' | Where-Object { $_ })[0]
            $full = [IO.Path]::Combine($cwd, $first)
            try {
                Set-Content -LiteralPath $full -Value '203.0.113.82 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAKEKEY' -ErrorAction Stop
                $call.wrote = $full
            } catch { }
        }
        Add-Content -LiteralPath $global:SshLog -Value ($call | ConvertTo-Json -Compress)
        'state=running'
    }
    # The fake phone answers at once, so any wait means the script went somewhere it should not: stop it there
    # (it reports "STOPPED" and exits 1) instead of letting a regression sit out its multi-minute timeouts.
    function global:Start-Sleep { throw 'harness: the script waits for something the fake phone never gives' }
    $tmp = Join-Path $work 'duo-opos-boot.ps1'
    Set-Content -LiteralPath $tmp -Value $text -Encoding UTF8
    # A copy whose first look at the rootfs (the one on the original) always passes: the second look, at the staged
    # copy, is then the only one left.
    $early = '$bad = Get-Ext4Problem $p[$Rootfs]'
    if (-not $text.Contains($early)) { throw 'harness: the early rootfs check was not found' }
    $tmpNoEarly = Join-Path $work 'duo-opos-boot-no-early-check.ps1'
    Set-Content -LiteralPath $tmpNoEarly -Value $text.Replace($early, '$bad = ""') -Encoding UTF8
    $results = @()
    foreach ($run in ($runs | ConvertFrom-Json)) {
        $imgName = $(if ($run.imgdir) { [string]$run.imgdir } else { 'img' })
        $img = Join-Path $work $imgName
        New-Item -ItemType Directory -Force -Path $img | Out-Null
        $global:Originals = @($names | ForEach-Object { Join-Path $img $_ })
        $logs = $(if ($run.logdir) { Join-Path $work ([string]$run.logdir) } else { Join-Path $img 'logs' })
        $global:LogFile = Join-Path $logs 'boot-chain.log'
        Reset-Images $run.rootfs
        $p = @{ ImageDir = $img; RootfsSha256 = $global:RootfsSum; WaitMinutes = 1 }
        foreach ($prop in $run.args.PSObject.Properties) { $p[$prop.Name] = $prop.Value }
        if ($run.logdir) { $p['LogDir'] = $logs }
        $rel = @($run.relative | Where-Object { $_ })
        if ($rel -contains 'ImageDir') { $p['ImageDir'] = $imgName }
        if ($rel -contains 'LogDir') { $p['LogDir'] = [string]$run.logdir }
        if ($run.key) {
            $p['Key'] = Join-Path $work 'dev.key'
            Set-Content -LiteralPath $p['Key'] -Value 'stand-in private key' -Encoding ASCII
            if ($rel -contains 'Key') { $p['Key'] = 'dev.key' }
        }
        Remove-Item -Path 'Function:\ssh-keygen.exe' -ErrorAction SilentlyContinue
        if (-not $run.no_keygen) {
            function global:ssh-keygen.exe {
                $global:LASTEXITCODE = 0
                '256 SHA256:TESTFINGERPRINTabcdefghijklmnopqrstuvwxyz0123456 203.0.113.82 (ED25519)'
            }
        }
        $global:FailRules = @(foreach ($r in @($run.fail)) {
            if ($r) {
                [pscustomobject]@{ tool = $r.tool; match = $r.match; mode = $r.mode; text = $r.text; used = 0
                    times = $(if ($null -ne $r.times) { [int]$r.times } else { 1000 })
                    skip = $(if ($null -ne $r.skip) { [int]$r.skip } else { 0 }) }
            }
        })
        $global:AddrOf = @{ '90' = '203.0.113.147'; '3' = '192.0.2.5'; '4' = '169.254.1.1' }
        $mk = { param($o) [pscustomobject]@{ Name = $o.name; InterfaceDescription = $o.desc; Status = 'Up'; ifIndex = [int]$o.index } }
        $global:Others = @(foreach ($o in @($run.others)) { if ($o) { $global:AddrOf["$($o.index)"] = $o.ip; & $mk $o } })
        $global:LateOthers = @(foreach ($o in @($run.late_others)) { if ($o) { $global:AddrOf["$($o.index)"] = $o.ip; & $mk $o } })
        $global:KernelBooted = $false
        $global:Tamper = [bool]$run.tamper
        Remove-Item -LiteralPath $global:CallLog, $global:ProbeLog, $global:SshLog -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $logs -Recurse -Force -ErrorAction SilentlyContinue
        $global:LockLogOn = [string]$run.lock_log_on
        $global:ProcCwd = $null
        $global:PsLoc = $null
        $global:LASTEXITCODE = $null
        $threw = $null
        $target = $tmp
        if ($run.no_early_ext4) { $target = $tmpNoEarly }
        $captured = New-Object System.Collections.ArrayList
        $stopOn = [string]$run.stop_on
        $stopSkip = [int]$run.stop_skip
        $stopState = @{ seen = 0 }
        if ($rel.Count) { Push-Location -LiteralPath $work; [Environment]::CurrentDirectory = $procdir }
        try {
            # Everything the script prints is kept. A line matching stop_on is passed on to Select-Object -First 1,
            # which stops the pipeline upstream of it: a PipelineStopped-type exception, like Ctrl+C.
            & $target @p *>&1 | ForEach-Object {
                $line = "$_"
                [void]$captured.Add($line)
                if ($stopOn -and $line -match $stopOn) { if ($stopState.seen -ge $stopSkip) { $line }; $stopState.seen++ }
            } | Select-Object -First 1 | Out-Null
        } catch { $threw = $_.Exception.Message } finally {
            if ($rel.Count) { Pop-Location; [Environment]::CurrentDirectory = $startCwd }
        }
        $logfile = $global:LogFile
        $results += [pscustomobject]@{
            label = $run.label
            threw = $threw
            exit = $global:LASTEXITCODE
            output = @($captured)
            proc_cwd = $global:ProcCwd
            ps_location = $global:PsLoc
            ssh_calls = @(if (Test-Path -LiteralPath $global:SshLog) { Get-Content -LiteralPath $global:SshLog | ForEach-Object { [string]$_ } })
            calls = @(if (Test-Path -LiteralPath $global:CallLog) { Get-Content -LiteralPath $global:CallLog | ForEach-Object { [string]$_ } })
            probes = @(if (Test-Path -LiteralPath $global:ProbeLog) { Get-Content -LiteralPath $global:ProbeLog | ForEach-Object { [string]$_ } })
            expected = $global:Expected
            stage_left = @(Get-ChildItem -LiteralPath $logs -Recurse -Force -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match 'stage-\d' } | ForEach-Object { $_.FullName })
            stage_hashes = @(Get-ChildItem -LiteralPath $logs -Recurse -Force -File -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match 'stage-\d' } | ForEach-Object { $_.Name + '|' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower() })
            logs_dir = [bool](Test-Path -LiteralPath $logs)
            log = $(if (Test-Path -LiteralPath $logfile) { (Get-Content -LiteralPath $logfile) -join "`n" } else { '' })
        }
    }
    [pscustomobject]@{ runs = @($results) } | ConvertTo-Json -Compress -Depth 6
} finally {
    [Environment]::CurrentDirectory = $startCwd
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
"""


def fake_phone_program(runs):
    return "$runs = '" + json.dumps(runs).replace("'", "''") + "'\n" + FAKE_PHONE


def ext4_image(blocks=4, log_bs=0, length=4096, magic=0xEF53, head=b"", incompat=0, blocks_hi=0):
    """A stand-in raw ext4 image: only the superblock fields the installer reads are real (blocks_count_lo at byte
    1028, log_block_size at 1048, the 0xEF53 magic at 1080, feature_incompat at 1120, blocks_count_hi at 1360).
    `length` is the size of the file, whatever the superblock says, and `head` overwrites the first bytes (a
    compression magic, say)."""
    data = bytearray(max(length, 4096))
    struct.pack_into("<I", data, 1028, blocks)
    struct.pack_into("<I", data, 1048, log_bs)
    struct.pack_into("<H", data, 1080, magic)
    struct.pack_into("<I", data, 1120, incompat)
    struct.pack_into("<I", data, 1360, blocks_hi)
    data[:len(head)] = head
    return bytes(data[:length])


def b64(data):
    return base64.b64encode(data).decode("ascii")


# (label, rootfs bytes, text the problem must contain; "" means the image is accepted)
EXT4_CASES = [
    ("valid, 1 KiB blocks", ext4_image(), ""),
    ("valid, 4 KiB blocks and exactly as long", ext4_image(blocks=3, log_bs=2, length=12288), ""),
    ("valid, 2 KiB blocks", ext4_image(blocks=3, log_bs=1, length=6144), ""),
    ("valid, 64-bit feature with a zero high word", ext4_image(incompat=0x80), ""),
    ("high word set but the 64-bit feature is not", ext4_image(blocks_hi=1), ""),
    ("longer than the filesystem says", ext4_image(blocks=2, length=8192), ""),
    ("compression magic one byte off", ext4_image(head=bytes.fromhex("28b52ffe")), ""),
    ("one byte short", ext4_image(blocks=5, length=5119), "describes 5120 bytes"),
    ("short by whole blocks", ext4_image(blocks=2, log_bs=2, length=4096), "describes 8192 bytes"),
    ("block count that needs 64-bit arithmetic", ext4_image(blocks=0xFFFFFFFF, log_bs=2), "describes 17592186040320 bytes"),
    ("64-bit block count of 16 TiB and more", ext4_image(blocks=16384, log_bs=2, length=1 << 26, incompat=0x80, blocks_hi=1), "16 TiB or more"),
    ("8 KiB blocks (larger than a page of the Duo's kernel)", ext4_image(blocks=1, log_bs=3, length=8192), "larger than 4096 bytes"),
    ("64 KiB blocks", ext4_image(blocks=1, log_bs=6, length=65536), "larger than 4096 bytes"),
    ("zstd", ext4_image(head=bytes.fromhex("28b52ffd")), "zstd"),
    ("gzip", ext4_image(head=bytes.fromhex("1f8b")), "gzip"),
    ("xz", ext4_image(head=bytes.fromhex("fd377a585a00")), "xz"),
    ("Android sparse", ext4_image(head=bytes.fromhex("3aff26ed")), "Android sparse"),
    ("wrong ext4 magic", ext4_image(magic=0xEF54), "0xEF53"),
    ("all zeros", bytes(4096), "0xEF53"),
    ("text file", b"stand-in opos-os-rootfs.img\n", "too short"),
    ("empty file", b"", "too short"),
    ("magic cut off by the end of the file", ext4_image(length=1081), "too short"),
    ("impossible block size", ext4_image(log_bs=7), "impossible block size"),
    ("zero blocks", ext4_image(blocks=0), "no blocks"),
]

EXT4 = AST + r"""
. ([scriptblock]::Create((FunctionText 'Get-Ext4Problem')))
$work = Join-Path ([IO.Path]::GetTempPath()) ('opos-duo-ext4-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $got = @()
    foreach ($c in ($cases | ConvertFrom-Json)) {
        $f = Join-Path $work 'image.img'
        [IO.File]::WriteAllBytes($f, [Convert]::FromBase64String([string]$c.b64))
        $got += [pscustomobject]@{ problem = [string](Get-Ext4Problem $f) }
    }
    [pscustomobject]@{ results = @($got) } | ConvertTo-Json -Compress
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
"""

# Stage-Image on its own: the copy is hashed through a handle that stays open and shares reading only; a copy that
# does not hash to the expected value is refused and does not leave its handle behind; nothing is overwritten.
STAGE = AST + r"""
. ([scriptblock]::Create((FunctionText 'Stage-Image')))
. ([scriptblock]::Create((FunctionText 'Release-Staged')))
function Log($m) { }
$work = Join-Path ([IO.Path]::GetTempPath()) ('opos-duo-stage-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $windows = [Environment]::OSVersion.Platform -eq 'Win32NT'
    $src = Join-Path $work 'src.img'
    $bytes = New-Object byte[] 70000
    (New-Object Random 5).NextBytes($bytes)
    [IO.File]::WriteAllBytes($src, $bytes)
    $good = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash.ToLower()
    $a = Join-Path $work 'a.img'
    $fs = Stage-Image $src $a $good
    $r = [ordered]@{ windows = $windows }
    $r.is_stream = [bool]($fs -is [System.IO.FileStream])
    $r.position = $fs.Position
    $r.none_blocked = try { [IO.File]::Open($a, 'Open', 'Read', 'None').Dispose(); $false } catch { $true }
    $r.read_shared = try { [IO.File]::Open($a, 'Open', 'Read', 'Read').Dispose(); $true } catch { $false }
    $r.write_blocked = $null
    $r.delete_blocked = $null
    if ($windows) {
        $r.write_blocked = try { [IO.File]::Open($a, 'Open', 'Write', 'ReadWrite').Dispose(); $false } catch { $true }
        $r.delete_blocked = try { [IO.File]::Delete($a); $false } catch { $true }
    }
    $ro = Join-Path $work 'ro.img'
    [IO.File]::Copy($src, $ro)
    [IO.File]::SetAttributes($ro, [IO.FileAttributes]::ReadOnly)
    $c = Join-Path $work 'c.img'
    $fs2 = Stage-Image $ro $c $good
    Release-Staged @($fs2) @($c) $null
    $r.readonly_source_copy_removed = -not (Test-Path -LiteralPath $c)
    $r.same_name_again = try { Stage-Image $src $a $good | Out-Null; 'accepted' } catch { 'refused' }
    $b = Join-Path $work 'b.img'
    $r.bad_hash_message = try { Stage-Image $src $b ('0' * 64) | Out-Null; '' } catch { $_.Exception.Message }
    $r.bad_hash_copy_exists = [bool](Test-Path -LiteralPath $b)
    $r.bad_hash_handle_released = try { [IO.File]::Delete($b); $true } catch { $false }
    Release-Staged @($fs) @($a) $null
    $r.released_file_gone = -not (Test-Path -LiteralPath $a)
    [pscustomobject]$r | ConvertTo-Json -Compress
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
"""

# What the fake phone answers, in the order the script asks, for a run that goes all the way. <S> stands for the
# staging folder. The numbered names are the places a test makes a command fail.
GOLDEN = [
    "fastboot devices",
    "fastboot devices",
    "fastboot -s SER1 getvar product",
    "fastboot -s SER1 getvar current-slot",
    "fastboot -s SER1 getvar unlocked",
    "fastboot -s SER1 getvar battery-level",
    "fastboot -s SER1 erase misc",
    "fastboot -s SER1 flash misc <S>/misc-brake.img",
    "fastboot -s SER1 format:ext4 userdata",
    "fastboot -s SER1 boot <S>/surfaceduo1-twrp.img",
    "adb devices",
    "adb -s SER1 shell mountpoint -q /data || mount -t ext4 /dev/block/platform/soc/1d84000.ufshc/by-name/userdata /data",
    "adb -s SER1 shell mountpoint -q /data && echo MOUNTED || echo UNMOUNTED",
    "adb -s SER1 push <S>/rootfs.img /data/rootfs.img",
    "adb -s SER1 shell sync && sha256sum /data/rootfs.img",
    "adb -s SER1 shell sync && umount /data",
    "adb -s SER1 reboot bootloader",
    "fastboot devices",
    "fastboot -s SER1 erase misc",
    "fastboot -s SER1 flash misc <S>/misc-brake.img",
    "fastboot -s SER1 boot <S>/boot.img",
]
(GETVAR_PRODUCT, GETVAR_SLOT, GETVAR_UNLOCKED, GETVAR_BATTERY, ERASE1, FLASH1, FORMAT, TWRP, ADB_DEVICES, MOUNT,
 MOUNTED, PUSH, SHA, UMOUNT, REBOOT, FB_DEVICES2, ERASE2, FLASH2, KERNEL) = range(2, 21)
STAGED_ARGS = ["misc-brake.img", "surfaceduo1-twrp.img", "rootfs.img", "misc-brake.img", "boot.img"]
STAGE_DIR = re.compile(r"stage-\d{8}-\d{6}-[0-9a-f]{8}")


def upto(index, *extra):
    """The commands up to and including GOLDEN[index], then `extra`: the last command a run may have issued."""
    return GOLDEN[:index + 1] + list(extra)


def norm(call):
    """A logged command with the staging folder shown as <S>/ and forward slashes (the folder above it may have
    spaces in its name)."""
    return re.sub(r"(?:[A-Za-z]:)?/[^\n]*?/stage-\d{8}-\d{6}-[0-9a-f]{8}/", "<S>/", call.replace("\\", "/"))


def hand_command_words(log):
    """The by-hand brake command in a log, split the way a shell splits it: quotes kept, backslashes left alone."""
    lines = [line for line in log.splitlines() if "To put the brake back by hand" in line]
    assert len(lines) == 1, lines
    return shlex.split(lines[0].split("Boot Select: ", 1)[1], posix=False)


def rule(tool, match, mode="exit", **more):
    return dict(tool=tool, match=match, mode=mode, **more)


def run_yes(runs):
    """Run each {label, ...} with -Yes against the fake phone; returns the results with the commands normalised."""
    for run in runs:
        run.setdefault("args", {})["Yes"] = True
    got = as_list(run_powershell(fake_phone_program(runs))["runs"])
    assert len(got) == len(runs)
    for run in got:
        run["raw_calls"] = as_list(run["calls"])
        run["calls"] = [norm(c) for c in run["raw_calls"]]
        run["output"] = as_list(run["output"])
    return got


# A stop in the write phase, one per row: (label, injected failures, the exact commands the run may have issued,
# what its log must say). Whatever is not in the list afterwards (the next write, a boot) must not have happened.
# The brake rows rely on the retry: after a failed erase or flash the brake image is written ONCE more, without a
# second erase.
BRAKE_MAY_BE_ABSENT = "THE MISC BRAKE MAY BE ABSENT"
BRAKE_RETRY_OK = ["BRAKE WRITE FAILED", BRAKE_MAY_BE_ABSENT, "trying the brake image once more, without another erase",
                  "the second try succeeded"]
BRAKE_RETRY_FAILS = ["BRAKE WRITE FAILED", BRAKE_MAY_BE_ABSENT, "the misc brake could not be written",
                     "KEEP THE PHONE IN BOOT SELECT", "To put the brake back by hand"]
STOPS = [
    ("other product", [rule("fastboot", "getvar product", "output", text="product: otherphone")],
     upto(GETVAR_PRODUCT), ["not a Surface Duo 1"]),
    ("slot b", [rule("fastboot", "getvar current-slot", "output", text="current-slot: b")],
     upto(GETVAR_SLOT), ["current slot is not a"]),
    ("bootloader locked", [rule("fastboot", "getvar unlocked", "output", text="unlocked: no")],
     upto(GETVAR_UNLOCKED), ["bootloader is not unlocked"]),
    ("battery 10 percent", [rule("fastboot", "getvar battery-level", "output", text="battery-level: 10")],
     upto(GETVAR_BATTERY), ["needs 20 or more"]),
    ("first brake: erase fails (exit code)", [rule("fastboot", "erase misc")],
     upto(FLASH1), BRAKE_RETRY_OK),
    ("first brake: erase answers FAILED", [rule("fastboot", "erase misc", "failed0")],
     upto(FLASH1), BRAKE_RETRY_OK),
    ("first brake: flash fails once (exit code)", [rule("fastboot", "flash misc", times=1)],
     upto(FLASH1, GOLDEN[FLASH1]), BRAKE_RETRY_OK),
    ("first brake: flash fails once (FAILED, exit 0)", [rule("fastboot", "flash misc", "failed0", times=1)],
     upto(FLASH1, GOLDEN[FLASH1]), BRAKE_RETRY_OK),
    ("first brake: flash fails twice", [rule("fastboot", "flash misc", "failed")],
     upto(FLASH1, GOLDEN[FLASH1]), BRAKE_RETRY_FAILS),
    ("first brake: erase and flash both fail", [rule("fastboot", "erase misc"), rule("fastboot", "flash misc")],
     upto(FLASH1), BRAKE_RETRY_FAILS),
    ("format fails", [rule("fastboot", "format:ext4", "failed")],
     upto(FORMAT), ["format:ext4 userdata failed"]),
    ("TWRP does not boot", [rule("fastboot", r"boot .*twrp")],
     upto(TWRP), ["twrp.img failed"]),
    ("TWRP mount command fails", [rule("adb", "mount -t ext4")],
     upto(MOUNT), ["adb shell mountpoint -q /data || mount -t ext4"]),
    ("/data is not mounted", [rule("adb", "echo MOUNTED", "output", text="UNMOUNTED")],
     upto(MOUNTED), ["/data did not mount in TWRP"]),
    ("push fails", [rule("adb", " push ")],
     upto(PUSH), ["adb push failed"]),
    ("sha256sum fails", [rule("adb", "sha256sum")],
     upto(SHA), ["sha256sum /data/rootfs.img failed (exit 1)"]),
    ("hash on the phone differs", [rule("adb", "sha256sum", "output", text="0" * 64 + "  /data/rootfs.img")],
     upto(SHA), ["rootfs hash on the phone differs"]),
    ("umount fails", [rule("adb", "umount")],
     upto(UMOUNT), ["sync && umount /data failed (exit 1)"]),
    ("reboot bootloader fails", [rule("adb", "reboot bootloader")],
     upto(REBOOT), ["reboot bootloader failed (exit 1)"]),
    ("second brake: erase fails", [rule("fastboot", "erase misc", skip=1)],
     upto(FLASH2), BRAKE_RETRY_OK),
    ("second brake: flash fails once", [rule("fastboot", "flash misc", skip=1, times=1)],
     upto(FLASH2, GOLDEN[FLASH2]), BRAKE_RETRY_OK),
    ("second brake: flash fails twice (FAILED, exit 0)", [rule("fastboot", "flash misc", "failed0", skip=1)],
     upto(FLASH2, GOLDEN[FLASH2]), BRAKE_RETRY_FAILS),
    ("second brake: erase and flash both fail", [rule("fastboot", "erase misc", skip=1), rule("fastboot", "flash misc", skip=1)],
     upto(FLASH2), BRAKE_RETRY_FAILS),
    ("kernel boot fails", [rule("fastboot", r"\sboot\s.*boot\.img$", "failed")],
     upto(KERNEL), ["boot.img failed"]),
]

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
        # Two things answer on the port. With no preferred host in range the sweep reports both, in address order.
        self.assertEqual(as_list(got["swept"]), ["127.0.0.20", "127.0.0.82"])
        # The preferred host is probed on its own first, so it comes back alone, whichever of the two it is.
        self.assertEqual(as_list(got["preferred"]), ["127.0.0.82"])
        self.assertEqual(as_list(got["preferred_other"]), ["127.0.0.20"])
        # With the other one gone the sweep still finds the phone.
        self.assertEqual(as_list(got["only"]), ["127.0.0.82"])
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
                self.assertIn("looks like a raw ext4 image", run["log"])
                self.assertIn("dry run (no -Yes)", run["log"])
                self.assertEqual(as_list(run["stage_left"]), [], "a dry run copied images")

    def test_ssh_output_does_not_start_with_the_known_hosts_warning(self):
        for got in as_list(run_powershell(SSH)["runs"]):
            with self.subTest(log_folder=got["name"]):
                self.assertEqual(got["code"], 0)
                self.assertTrue(got["text"].startswith("state=running"), got["text"])
                self.assertNotIn("Warning", got["text"])
                args = as_list(got["args"])
                for option in ("LogLevel=ERROR", "BatchMode=yes", "IdentitiesOnly=yes", "StrictHostKeyChecking=no"):
                    self.assertIn(option, args)

    def test_ssh_runs_in_the_log_folder_and_names_known_hosts_without_a_path_or_a_space(self):
        """The late failure this prevents: ssh splits the value of UserKnownHostsFile at blanks, so a log folder under
        'C:/Duo Images' made it write the host key to a file called C:/Duo, and Show-HostKey found nothing. The name
        is now a bare file name, and ssh runs in the log folder, so it still lands there."""
        runs = as_list(run_powershell(SSH)["runs"])
        self.assertEqual(len(runs), 2)
        for got in runs:
            with self.subTest(log_folder=got["name"]):
                args = as_list(got["args"])
                options = [a for a in args if a.startswith("UserKnownHostsFile=")]
                self.assertEqual(options, ["UserKnownHostsFile=known_hosts"])
                self.assertEqual(" " in got["logdir"], got["name"] != "plain")
                # The folder ssh starts in is the log folder, whatever it is called, and the location comes back.
                self.assertEqual(got["cwd"].lower(), got["logdir"].lower())
                self.assertTrue(got["location_restored"], "Invoke-Ssh left the PowerShell location changed")
                if got["native_cwd"] is not None:
                    self.assertEqual(got["native_cwd"].rstrip("\\").lower(), got["logdir"].rstrip("\\").lower(),
                                     "a real native process did not start in the log folder")

    def test_get_ext4_problem_accepts_raw_ext4_and_names_what_is_wrong_with_anything_else(self):
        cases = [{"b64": b64(data)} for _, data, _ in EXT4_CASES]
        got = as_list(run_powershell("$cases = '" + json.dumps(cases) + "'\n" + EXT4)["results"])
        self.assertEqual(len(got), len(EXT4_CASES))
        for (label, _, wanted), result in zip(EXT4_CASES, got):
            with self.subTest(case=label):
                if wanted:
                    self.assertIn(wanted, result["problem"])
                else:
                    self.assertEqual(result["problem"], "")

    def test_a_rootfs_that_is_not_raw_ext4_stops_before_any_tool_runs_even_in_a_dry_run(self):
        """The late failure this prevents: a .zst or a sparse image with its correct hash used to pass every check,
        and the userdata format, the push and the one kernel boot were spent on it."""
        bad = [(label, data, wanted) for label, data, wanted in EXT4_CASES if wanted]
        runs = []
        for label, data, wanted in bad:
            runs.append({"label": label + " (-Yes)", "rootfs": b64(data), "args": {"Yes": True}})
            runs.append({"label": label + " (dry run)", "rootfs": b64(data), "args": {}})
        got = as_list(run_powershell(fake_phone_program(runs))["runs"])
        self.assertEqual(len(got), len(runs))
        for run, (label, data, wanted) in zip(got, [b for b in bad for _ in (0, 1)]):
            with self.subTest(run=run["label"]):
                self.assertIn("is not a raw ext4 image", run["threw"])
                self.assertIn(wanted, run["threw"])
                self.assertEqual(as_list(run["calls"]), [], "a fastboot or adb command ran")
                self.assertEqual(as_list(run["stage_left"]), [], "images were staged for a rootfs that cannot work")
                self.assertNotIn("hash ok", run["log"])

    def test_the_staged_rootfs_is_checked_again_in_case_the_original_changed(self):
        """The first look at the rootfs is at the original, which a download or a sync could replace before it is
        staged. This run skips that first look, so only the look at the staged copy is left."""
        zst = ext4_image(head=bytes.fromhex("28b52ffd"))
        got = run_yes([{"label": "replaced after the first look", "rootfs": b64(zst), "no_early_ext4": True}])[0]
        self.assertIsNone(got["threw"])
        self.assertEqual(got["exit"], 1)
        self.assertEqual(got["calls"], [], "a fastboot or adb command ran")
        self.assertIn("the staged copy of -Rootfs", got["log"])
        self.assertIn("zstd", got["log"])
        self.assertEqual(as_list(got["stage_left"]), [], "the staged copies were not removed")

    def test_a_good_run_hands_fastboot_and_adb_only_held_staged_copies(self):
        """Every path given to fastboot or adb is a copy in a new folder under the log folder, hashed through a
        handle that is still held when the tool is called (another opener asking for exclusive access is refused)
        and equal to the verified image, even though the originals were rewritten after they had been checked."""
        runs = [{"label": "plain"}, {"label": "originals rewritten after the check", "tamper": True}, {"label": "again"}]
        got = run_yes(runs)
        folders = []
        for run in got:
            with self.subTest(run=run["label"]):
                self.assertIsNone(run["threw"])
                self.assertEqual(run["exit"], 0)
                self.assertEqual(run["calls"], GOLDEN)
                self.assertIn("DONE: the rootfs is on the phone", run["log"])
                self.assertEqual(run["log"].count("staged "), 4)
                probes = [line.rsplit("|", 2) for line in as_list(run["probes"])]
                self.assertEqual(len(probes), len(STAGED_ARGS))
                expected = run["expected"]
                for (path, held, digest), name in zip(probes, STAGED_ARGS):
                    with self.subTest(run=run["label"], path=name):
                        self.assertTrue(norm(path).startswith("<S>/"), path)
                        self.assertTrue(path.endswith("/" + name), path)
                        self.assertIn("/logs/stage-", path)
                        self.assertEqual(held, "True", "nobody holds the staged copy open")
                        self.assertEqual(digest, expected[name])
                self.assertEqual(as_list(run["stage_left"]), [], "the staged copies were not released and removed")
                found = {m.group(0) for c in run["raw_calls"] for m in STAGE_DIR.finditer(c)}
                self.assertEqual(len(found), 1)
                folders.append(found.pop())
        self.assertEqual(len(set(folders)), len(folders), "a staging folder was reused")

    def test_stage_image_hashes_the_copy_through_a_held_handle_and_refuses_a_bad_one(self):
        got = run_powershell(STAGE)
        self.assertTrue(got["is_stream"])
        self.assertEqual(got["position"], 0)
        self.assertTrue(got["none_blocked"], "a second opener got exclusive access to the staged copy")
        self.assertTrue(got["read_shared"], "fastboot and adb could not read the staged copy")
        if got["windows"]:
            self.assertTrue(got["write_blocked"])
            self.assertTrue(got["delete_blocked"])
        self.assertEqual(got["same_name_again"], "refused")
        self.assertIn("hashes to", got["bad_hash_message"])
        self.assertIn("changed while it was copied", got["bad_hash_message"])
        self.assertTrue(got["bad_hash_copy_exists"])
        self.assertTrue(got["bad_hash_handle_released"], "a copy that failed its hash kept its handle open")
        self.assertTrue(got["released_file_gone"])
        self.assertTrue(got["readonly_source_copy_removed"], "a staged copy of a read-only file could not be deleted")

    def test_every_write_phase_failure_stops_at_the_failing_command(self):
        """Each row injects one failure (a nonzero exit, FAILED in the answer, a wrong answer) and compares the
        whole list of fastboot and adb commands with the last one the script was allowed to issue: no later write
        and no boot may follow. Delete the check behind a row and the run goes on, and its row fails."""
        got = run_yes([{"label": label, "fail": fail} for label, fail, _, _ in STOPS])
        for run, (label, _, calls, says) in zip(got, STOPS):
            with self.subTest(stop=label):
                self.assertIsNone(run["threw"])
                self.assertEqual(run["exit"], 1)
                self.assertEqual(run["calls"], calls)
                self.assertIn("STOPPED at:", run["log"])
                self.assertNotIn("DONE:", run["log"])
                for text_ in says:
                    self.assertIn(text_, run["log"])
                if "To put the brake back by hand" in says:
                    # Only the verified brake image is kept, with its folder, for the by-hand command.
                    self.assertEqual(len(as_list(run["stage_left"])), 2)
                    self.assertEqual(as_list(run["stage_hashes"]), ["misc-brake.img|" + run["expected"]["misc-brake.img"]])
                else:
                    self.assertEqual(as_list(run["stage_left"]), [], "the staged copies were not released and removed")
                # The kernel is only ever booted by the very last command of a full run.
                self.assertLessEqual(sum(1 for c in run["calls"] if re.search(r" boot <S>/boot\.img$", c)), 1)

    def test_a_failed_brake_write_is_loud_and_the_retry_does_not_erase(self):
        got = {r["label"]: r for r in run_yes([
            {"label": "retry works", "fail": [rule("fastboot", "flash misc", times=1)]},
            {"label": "retry fails", "fail": [rule("fastboot", "flash misc")]},
            {"label": "second arm, retry fails", "fail": [rule("fastboot", "flash misc", skip=1)]},
        ])}
        works, fails, second = got["retry works"], got["retry fails"], got["second arm, retry fails"]
        # Two flashes after one erase: the retry is a flash only.
        self.assertEqual(works["calls"].count("fastboot -s SER1 erase misc"), 1)
        self.assertEqual(works["calls"].count(GOLDEN[FLASH1]), 2)
        self.assertEqual(works["log"].count(BRAKE_MAY_BE_ABSENT), 1)
        self.assertIn("the first failure stops it", works["log"])
        # When the retry fails too, the warning comes again last, with the hand command naming the verified STAGED
        # copy (in the stage folder under the log folder), not the original in the image folder, in double quotes.
        self.assertEqual(fails["log"].count(BRAKE_MAY_BE_ABSENT), 2)
        hand = [line for line in fails["log"].splitlines() if "To put the brake back by hand" in line]
        self.assertEqual(len(hand), 1)
        self.assertRegex(hand[0], r'fastboot -s SER1 flash misc "\S+[\\/]img[\\/]logs[\\/]stage-\d{8}-\d{6}-[0-9a-f]{8}[\\/]misc-brake\.img"$')
        self.assertIn("(SHA-256 " + fails["expected"]["misc-brake.img"] + ")", fails["log"])
        self.assertEqual(second["log"].count(BRAKE_MAY_BE_ABSENT), 2)
        self.assertNotIn(" boot <S>/boot.img", " ".join(second["calls"]))
        self.assertNotIn("format:ext4", " ".join(fails["calls"]))

    def test_the_by_hand_brake_command_keeps_a_path_with_a_space_in_one_piece(self):
        """The late failure this prevents: the command printed after a double brake failure was meant to be pasted,
        and with the image folder 'C:/Duo Images' its unquoted staged path became two words."""
        got = run_yes([{"label": "spaced image folder", "imgdir": "Duo Images", "fail": [rule("fastboot", "flash misc", "failed")]}])[0]
        self.assertIsNone(got["threw"])
        self.assertEqual(got["exit"], 1)
        self.assertEqual(got["log"].count(BRAKE_MAY_BE_ABSENT), 2)
        words = hand_command_words(got["log"])
        self.assertEqual(words[:5], ["fastboot", "-s", "SER1", "flash", "misc"])
        self.assertEqual(len(words), 6, words)
        path = words[5]
        self.assertTrue(path.startswith('"') and path.endswith('"'), path)
        self.assertIn("Duo Images", path)
        self.assertRegex(path, r'stage-\d{8}-\d{6}-[0-9a-f]{8}[\\/]misc-brake\.img"$')
        # It is the very file the failed flashes were handed, and the one the run kept.
        flashed = {c.split("flash misc ", 1)[1] for c in got["raw_calls"] if "flash misc " in c}
        self.assertEqual(flashed, {path[1:-1]})
        self.assertEqual(as_list(got["stage_hashes"]), ["misc-brake.img|" + got["expected"]["misc-brake.img"]])

    def test_relative_log_and_image_folders_work_while_the_powershell_location_is_not_the_process_directory(self):
        """File.Copy and FileStream resolve a relative path against the process working directory, which Set-Location
        and Push-Location do not change. The script makes -ImageDir and -LogDir absolute before it stages anything;
        each relative run here has the PowerShell location in the work folder and the process in another one."""
        runs = [{"label": "relative -LogDir", "logdir": "relative-logs", "relative": ["LogDir"]},
                {"label": "relative -ImageDir", "relative": ["ImageDir"]},
                {"label": "both, image folder with a space", "imgdir": "relative images", "logdir": "relative-logs", "relative": ["ImageDir", "LogDir"]},
                {"label": "absolute, log folder with a space", "logdir": "absolute logs"}]
        got = run_yes([dict(r) for r in runs])
        for run, wanted in zip(got, runs):
            with self.subTest(run=run["label"]):
                self.assertIsNone(run["threw"])
                self.assertEqual(run["exit"], 0)
                self.assertEqual(run["calls"], GOLDEN)
                self.assertIn("DONE: the rootfs is on the phone", run["log"])
                self.assertEqual(as_list(run["stage_left"]), [])
                if wanted.get("relative"):
                    self.assertTrue(run["proc_cwd"] and run["ps_location"])
                    self.assertNotEqual(run["proc_cwd"].lower(), run["ps_location"].lower(), "the test did not separate the two")
                folder = "/" + wanted.get("logdir", "img/logs") + "/stage-"
                self.assertTrue(all(folder in line for line in as_list(run["probes"])), run["probes"])

    def test_an_unwritable_log_cannot_hide_the_brake_warning_or_skip_the_retry(self):
        """The late failure this prevents: Log used Add-Content unguarded, and Fb logs before it looks at the result.
        A log that became unwritable right after 'erase misc' succeeded made Fb throw from Log, the catch in
        Set-MiscBrake caught it, its next Log threw outside the try: no retry flash, no warning, no hand command.
        Here the log file is made read-only when the erase is issued; what the script printed is read from the screen."""
        locked = {"lock_log_on": "erase misc"}
        got = {r["label"]: r for r in run_yes([
            dict(label="retry works", fail=[rule("fastboot", "flash misc", times=1)], **locked),
            dict(label="retry fails", fail=[rule("fastboot", "flash misc")], **locked),
            dict(label="nothing fails", **locked),
        ])}
        screen = {label: "\n".join(run["output"]) for label, run in got.items()}
        for label, run in got.items():
            with self.subTest(run=label):
                self.assertIsNone(run["threw"], "an exception escaped the script")
                # The file stopped at the erase, the screen did not, and the broken log is mentioned once.
                self.assertIn("arming the misc brake", run["log"])
                self.assertNotIn("fastboot erase misc", run["log"])
                self.assertIn("fastboot erase misc", screen[label])
                self.assertEqual(sum("the log file could not be written" in line for line in run["output"]), 1)
        works, fails, fine = got["retry works"], got["retry fails"], got["nothing fails"]
        self.assertEqual(works["exit"], 1)
        self.assertEqual(works["calls"], upto(FLASH1, GOLDEN[FLASH1]))
        for words in (BRAKE_MAY_BE_ABSENT, "trying the brake image once more, without another erase", "the second try succeeded"):
            self.assertIn(words, screen["retry works"])
        self.assertEqual(as_list(works["stage_left"]), [])
        self.assertEqual(fails["exit"], 1)
        self.assertEqual(fails["calls"], upto(FLASH1, GOLDEN[FLASH1]))
        self.assertEqual(screen["retry fails"].count(BRAKE_MAY_BE_ABSENT), 2)
        for words in ("KEEP THE PHONE IN BOOT SELECT", "the misc brake could not be written", "To put the brake back by hand"):
            self.assertIn(words, screen["retry fails"])
        self.assertEqual(as_list(fails["stage_hashes"]), ["misc-brake.img|" + fails["expected"]["misc-brake.img"]])
        self.assertEqual(fine["exit"], 0)
        self.assertEqual(fine["calls"], GOLDEN)
        self.assertIn("DONE: the rootfs is on the phone", screen["nothing fails"])
        self.assertNotIn("DONE:", fine["log"])

    def test_an_interrupt_after_the_brake_erase_still_warns_and_keeps_the_brake_image(self):
        """The late failure this prevents: the flag 'the brake may be absent' was raised after the catch, so Ctrl+C
        between 'erase misc' and the confirmed flash (which a catch never sees) left it down, the finally deleted the
        staged brake image and nothing warned. Ctrl+C is simulated by stopping the script's output pipeline at the
        line that reports the erase: the catch blocks are skipped (no 'STOPPED at') and the finally blocks run."""
        got = {r["label"]: r for r in run_yes([
            {"label": "first arming", "stop_on": "fastboot erase misc ->"},
            {"label": "second arming", "stop_on": "fastboot erase misc ->", "stop_skip": 1},
            {"label": "after the brake was confirmed", "stop_on": "adb reboot bootloader ->"},
        ])}
        for label, last in (("first arming", ERASE1), ("second arming", ERASE2)):
            run = got[label]
            with self.subTest(run=label):
                self.assertIsNone(run["threw"])
                self.assertEqual(run["calls"], upto(last), "the script went on after the interrupt")
                self.assertNotIn("STOPPED at:", run["log"], "the interrupt reached a catch block")
                self.assertNotIn("DONE:", run["log"])
                self.assertEqual(run["log"].count(BRAKE_MAY_BE_ABSENT), 1)
                self.assertIn("KEEP THE PHONE IN BOOT SELECT", run["log"])
                words = hand_command_words(run["log"])
                self.assertEqual(words[:5], ["fastboot", "-s", "SER1", "flash", "misc"])
                self.assertRegex(words[5], r'^"\S+[\\/]stage-\d{8}-\d{6}-[0-9a-f]{8}[\\/]misc-brake\.img"$')
                # Only the verified brake image (and its folder) is left.
                self.assertEqual(len(as_list(run["stage_left"])), 2)
                self.assertEqual(as_list(run["stage_hashes"]), ["misc-brake.img|" + run["expected"]["misc-brake.img"]])
        # Interrupted later, with the brake confirmed: nothing to warn about, everything released.
        late = got["after the brake was confirmed"]
        self.assertEqual(late["calls"], upto(REBOOT))
        self.assertNotIn("STOPPED at:", late["log"])
        self.assertNotIn(BRAKE_MAY_BE_ABSENT, late["log"])
        self.assertEqual(as_list(late["stage_left"]), [])

    def test_a_key_or_log_folder_with_spaces_or_a_relative_key_still_gives_the_host_key_fingerprint(self):
        """The late failure this prevents: ssh got '-o UserKnownHostsFile=<log folder>/known_hosts' as one argument, split
        it at the blanks of the folder name and wrote the host key elsewhere (here: to the first word), so Show-HostKey
        saw no file. The fake ssh behaves that way; now it gets a bare file name and the log folder as its directory."""
        runs = [{"label": "folders with spaces", "key": True, "imgdir": "Duo Images", "logdir": "Log Folder"},
                {"label": "relative -Key", "key": True, "relative": ["Key"]},
                {"label": "everything relative, with spaces", "key": True, "imgdir": "my images", "logdir": "my logs", "relative": ["ImageDir", "LogDir", "Key"]}]
        got = run_yes([dict(r) for r in runs])
        for run, wanted in zip(got, runs):
            with self.subTest(run=run["label"]):
                self.assertIsNone(run["threw"])
                self.assertEqual(run["exit"], 0)
                self.assertEqual(run["calls"], GOLDEN)
                calls = [json.loads(line) for line in as_list(run["ssh_calls"])]
                self.assertEqual(len(calls), 1)
                argv = as_list(calls[0]["argv"])
                self.assertEqual([a for a in argv if a.startswith("UserKnownHostsFile=")], ["UserKnownHostsFile=known_hosts"])
                key = argv[argv.index("-i") + 1]
                self.assertRegex(key, r"^(?:[A-Za-z]:)?[\\/]", "the key was handed to ssh as a relative path")
                self.assertTrue(calls[0]["key_found"])
                folder = "/" + wanted.get("logdir", "img/logs")
                self.assertTrue(calls[0]["cwd"].replace("\\", "/").endswith(folder), calls[0]["cwd"])
                self.assertEqual(calls[0]["wrote"].replace("\\", "/"), calls[0]["cwd"].replace("\\", "/") + "/known_hosts")
                self.assertIn("diagnostics saved to boot-diag.txt", run["log"])
                self.assertIn("ssh accepted this host key without checking it first: 256 SHA256:TESTFINGERPRINT", run["log"])

    def test_adb_failures_are_not_swallowed(self):
        """Ab used to return text only. A failing 'umount /data' must stop the run before the phone leaves TWRP."""
        got = run_yes([{"label": "umount", "fail": [rule("adb", "umount")]},
                       {"label": "reboot", "fail": [rule("adb", "reboot bootloader")]}])
        self.assertEqual(got[0]["calls"][-1], GOLDEN[UMOUNT])
        self.assertNotIn("adb -s SER1 reboot bootloader", got[0]["calls"])
        self.assertEqual(got[1]["calls"][-1], GOLDEN[REBOOT])
        self.assertFalse([c for c in got[1]["calls"] if " boot <S>/boot.img" in c])

    def test_more_than_one_rndis_adapter_is_refused_with_the_candidates_listed(self):
        tether = {"name": "Ethernet 7", "desc": "Remote NDIS based Internet Sharing Device", "ip": "198.51.100.129", "index": 7}
        other = {"name": "Ethernet 8", "desc": "Remote NDIS Compatible Device", "ip": "198.51.100.2", "index": 8}
        got = {r["label"]: r for r in run_yes([
            {"label": "one adapter, the phone's"},
            {"label": "another adapter already up", "others": [tether, other]},
            {"label": "an adapter up that has no address yet", "others": [{"name": "Ethernet 6", "desc": "Remote NDIS Compatible Device", "ip": None, "index": 6}]},
            {"label": "two adapters after the boot", "late_others": [tether]},
            {"label": "-PhoneIp skips the search", "args": {"PhoneIp": "203.0.113.82"}, "others": [tether]},
        ])}
        good = got["one adapter, the phone's"]
        self.assertEqual(good["calls"], GOLDEN)
        self.assertIn("phone answers on TCP 22 at 203.0.113.82", good["log"])
        early = got["another adapter already up"]
        self.assertEqual(early["exit"], 1)
        self.assertEqual(early["calls"], upto(GETVAR_BATTERY), "something was written before the adapters were checked")
        for words in ("Ethernet 7", "198.51.100.129", "Ethernet 8", "198.51.100.2", "Remote NDIS", "-PhoneIp"):
            with self.subTest(words=words):
                self.assertIn(words, early["log"])
        noaddr = got["an adapter up that has no address yet"]
        self.assertEqual(noaddr["calls"], upto(GETVAR_BATTERY))
        self.assertIn("Ethernet 6", noaddr["log"])
        self.assertIn("no IPv4 address yet", noaddr["log"])
        late = got["two adapters after the boot"]
        self.assertEqual(late["exit"], 1)
        self.assertEqual(late["calls"], GOLDEN)
        for words in ("more than one RNDIS-like USB network adapter", "Ethernet 7", "198.51.100.129", "Ethernet 9", "203.0.113.147"):
            with self.subTest(words=words):
                self.assertIn(words, late["log"])
        self.assertNotIn("phone answers on TCP 22", late["log"])
        self.assertNotIn("DONE:", late["log"])
        skipped = got["-PhoneIp skips the search"]
        self.assertEqual(skipped["calls"], GOLDEN)
        self.assertIn("phone answers on TCP 22 at 203.0.113.82", skipped["log"])
        self.assertNotIn("probing TCP 22", skipped["log"])

    def test_the_host_key_fingerprint_ssh_accepted_is_printed_after_the_diagnostics(self):
        got = {r["label"]: r for r in run_yes([
            {"label": "key", "key": True},
            {"label": "key, no ssh-keygen", "key": True, "no_keygen": True},
            {"label": "no key"},
        ])}
        log = got["key"]["log"]
        self.assertEqual(got["key"]["calls"], GOLDEN)
        self.assertIn("ssh accepted this host key without checking it first: 256 SHA256:TESTFINGERPRINT", log)
        self.assertIn("ssh-keygen -l -f /etc/ssh/ssh_host_ed25519_key.pub", log)
        self.assertLess(log.index("diagnostics saved to boot-diag.txt"), log.index("SHA256:TESTFINGERPRINT"))
        self.assertLess(log.index("SHA256:TESTFINGERPRINT"), log.index("DONE:"))
        self.assertIn("ssh-keygen.exe is not on PATH", got["key, no ssh-keygen"]["log"])
        self.assertNotIn("SHA256:", got["key, no ssh-keygen"]["log"])
        self.assertNotIn("SHA256:", got["no key"]["log"])


if __name__ == "__main__":
    unittest.main()
