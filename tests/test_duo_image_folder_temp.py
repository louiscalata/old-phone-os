# SPDX-License-Identifier: GPL-3.0-or-later
"""The phone images must not live in a temp folder on the PC.

Windows Storage Sense deletes old files under the temp folders; the PC session reported on November 12, 2026 that it
had deleted the boot and recovery images kept there. deploy/duo-opos-boot.ps1 names an -ImageDir or -LogDir (the
staged copies of the multi-GB rootfs live under -LogDir) that sits under a temp folder, before anything touches the
phone, and the install docs say where to keep the images instead. The text tests read the files; the PowerShell test
runs the folder check itself with pwsh or powershell when one is installed, and skips otherwise.
"""
import json
import unittest
from pathlib import Path

from tests.test_duo_boot_script import AST, code, function_text, header, powershell, run_powershell

ROOT = Path(__file__).resolve().parents[1]
PERSIST = ROOT / "docs" / "duo-persistent-install.md"
README = ROOT / "packaging" / "duo-os" / "README.md"
ROADMAP = ROOT / "ROADMAP.md"


def flat(s):
    return " ".join(s.replace("#", " ").split())


class BootScriptNamesATempImageFolder(unittest.TestCase):
    def test_header_says_to_keep_the_folder_out_of_temp(self):
        h = flat(header())
        self.assertIn("never under a temp folder", h)
        self.assertIn("Storage Sense", h)
        self.assertIn("opos-duo-images", h)

    def test_the_check_is_code_and_covers_both_folders(self):
        body = function_text("Get-TempFolder")
        # Every temp root Windows uses, plus a path segment named Temp or tmp (an 8.3 short TEMP keeps that segment).
        for needle in ("$env:TEMP", "$env:TMP", "GetTempPath()", "$env:SystemRoot", "'temp'", "'tmp'"):
            self.assertIn(needle, body)
        c = code()
        self.assertIn("Get-TempFolder $ImageDir", c)
        self.assertIn("Get-TempFolder $LogDir", c)

    def test_the_warning_comes_before_the_dry_run_stop_and_names_storage_sense(self):
        c = code()
        warn = c.index("Get-TempFolder $ImageDir")
        self.assertLess(c.index("$log = Join-Path $LogDir 'boot-chain.log'"), warn)   # Log can write to the file
        self.assertLess(warn, c.index("if (-not $Yes) {"))                          # the dry run shows it too
        message = c[warn:c.index("if (-not $Yes) {")]
        self.assertIn("Storage Sense", message)
        self.assertIn("opos-duo-images", message)
        # The fake-phone tests count "staged " in the log; the warning must not add to that count.
        self.assertNotIn("staged ", message)


TEMP_CHECK = AST + r"""
. ([scriptblock]::Create((FunctionText 'Get-TempFolder')))
$t = [System.IO.Path]::GetTempPath()
$sep = [System.IO.Path]::DirectorySeparatorChar
$home_ = [Environment]::GetFolderPath('UserProfile')
$cases = [ordered]@{
    temp_root_child = (Join-Path $t 'opos-duo-images')
    temp_root_deep  = (Join-Path (Join-Path $t 'a') 'b')
    temp_segment    = ('D:' + $sep + 'profile' + $sep + 'SHORTN~1' + $sep + 'Local' + $sep + 'Temp' + $sep + 'imgs')
    tmp_segment     = ($sep + 'var' + $sep + 'tmp' + $sep + 'imgs')
    profile_folder  = (Join-Path $home_ 'opos-duo-images')
    temperature     = (Join-Path $home_ 'temperature-logs')
}
$out = [ordered]@{}
foreach ($k in $cases.Keys) { $out[$k] = [bool](Get-TempFolder $cases[$k]) }
[pscustomobject]@{ got = $out; tempPath = $t; home = $home_ } | ConvertTo-Json -Compress -Depth 4
"""


@unittest.skipUnless(powershell(), "needs pwsh or powershell to run the folder check")
class BootScriptTempCheckRuns(unittest.TestCase):
    def test_temp_folders_are_named_and_profile_folders_are_not(self):
        got = run_powershell(TEMP_CHECK)
        detail = json.dumps(got)
        for k in ("temp_root_child", "temp_root_deep", "temp_segment", "tmp_segment"):
            self.assertTrue(got["got"][k], k + " " + detail)
        if not str(got["home"]).lower().startswith(str(got["tempPath"]).lower()):
            self.assertFalse(got["got"]["profile_folder"], detail)
            self.assertFalse(got["got"]["temperature"], detail)


class DocsKeepTheImagesOutOfTemp(unittest.TestCase):
    def check(self, path, folder):
        s = flat(path.read_text(encoding="utf-8"))
        self.assertIn("never under a temp folder", s, path)
        self.assertIn("Windows Storage Sense", s, path)
        self.assertIn(folder, s, path)
        self.assertIn("SHA-256", s, path)

    def test_persistent_install_prerequisites_and_setup(self):
        self.check(PERSIST, "$env:USERPROFILE\\opos-duo-images")
        setup = PERSIST.read_text(encoding="utf-8")
        line = next(l for l in setup.splitlines() if l.startswith("$images "))
        self.assertIn("never a temp folder", line)
        probe = next(l for l in setup.splitlines() if l.startswith("$probe "))
        self.assertIn("never a temp folder", probe)

    def test_install_chain_readme(self):
        self.check(README, "%USERPROFILE%\\opos-duo-images")

    def test_roadmap_records_it_without_a_phone_claim(self):
        line = next(l for l in ROADMAP.read_text(encoding="utf-8").split("\n- ") if "Storage Sense" in l)
        s = flat(line)
        self.assertIn("NOT_RUN", s)
        self.assertIn("not re-verified on the Mac", s)


if __name__ == "__main__":
    unittest.main()
