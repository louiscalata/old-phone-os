# SPDX-License-Identifier: GPL-3.0-or-later
import os
import subprocess
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), '..', 'deploy', 'duo-rootfs-install.sh')


class TestDuoRootfsInstall(unittest.TestCase):
    def test_default_prints_plan_and_runs_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            bin_dir = os.path.join(d, 'bin')
            os.makedirs(bin_dir)

            fastboot_path = os.path.join(bin_dir, 'fastboot')
            with open(fastboot_path, 'w') as f:
                f.write('#!/bin/sh\n')
                f.write('echo "fastboot $*" >> calls.log\n')
                f.write('exit 0\n')
            os.chmod(fastboot_path, 0o755)

            adb_path = os.path.join(bin_dir, 'adb')
            with open(adb_path, 'w') as f:
                f.write('#!/bin/sh\n')
                f.write('echo "adb $*" >> calls.log\n')
                f.write('exit 0\n')
            os.chmod(adb_path, 0o755)

            for name in ('twrp.img', 'boot.img', 'rootfs.img'):
                open(os.path.join(d, name), 'wb').close()

            env = os.environ.copy()
            env['PATH'] = bin_dir + os.pathsep + env['PATH']

            result = subprocess.run(
                ['sh', SCRIPT,
                 os.path.join(d, 'twrp.img'),
                 os.path.join(d, 'boot.img'),
                 os.path.join(d, 'rootfs.img')],
                capture_output=True, text=True, env=env, cwd=d
            )
            self.assertEqual(result.returncode, 0)
            self.assertFalse(os.path.exists(os.path.join(d, 'calls.log')))
            stdout = result.stdout
            self.assertIn('ERASES userdata', stdout)
            self.assertIn('20 percent', stdout)

            lines = [l for l in stdout.splitlines() if l.startswith('+ ')]
            idx_format = next(i for i, l in enumerate(lines) if 'format:ext4 userdata' in l)
            idx_boot_twrp = next(i for i, l in enumerate(lines) if 'boot' in l and 'twrp.img' in l)
            idx_push = next(i for i, l in enumerate(lines) if 'adb push' in l)
            idx_boot_boot = next(i for i, l in enumerate(lines) if 'boot.img' in l and l.startswith('+ fastboot boot'))
            self.assertTrue(idx_format < idx_boot_twrp < idx_push < idx_boot_boot)

            boot_lines = [l for l in lines if l.startswith('+ fastboot boot') and l.rstrip().endswith('boot.img')]
            self.assertEqual(len(boot_lines), 1)
            # Only TWRP may be left for the bootloader, before the one RAM-boot; the RAM-booted kernel
            # itself is never rebooted (docs/duo-usb-install.md).
            reboots = [i for i, l in enumerate(lines) if 'reboot' in l]
            self.assertEqual([lines[i].strip() for i in reboots], ['+ adb reboot bootloader'])
            self.assertLess(reboots[0], lines.index(boot_lines[0]))

            result_n = subprocess.run(['sh', '-n', SCRIPT], capture_output=True, text=True)
            self.assertEqual(result_n.returncode, 0)

    def test_yes_stops_at_first_failure(self):
        with tempfile.TemporaryDirectory() as d:
            bin_dir = os.path.join(d, 'bin')
            os.makedirs(bin_dir)

            fastboot_path = os.path.join(bin_dir, 'fastboot')
            with open(fastboot_path, 'w') as f:
                f.write('#!/bin/sh\n')
                f.write('echo "fastboot $*" >> calls.log\n')
                f.write('exit 0\n')
            os.chmod(fastboot_path, 0o755)

            adb_path = os.path.join(bin_dir, 'adb')
            with open(adb_path, 'w') as f:
                f.write('#!/bin/sh\n')
                f.write('echo "adb $*" >> calls.log\n')
                f.write('[ "$1" = push ] && exit 1\n')
                f.write('exit 0\n')
            os.chmod(adb_path, 0o755)

            for name in ('twrp.img', 'boot.img', 'rootfs.img'):
                open(os.path.join(d, name), 'wb').close()

            env = os.environ.copy()
            env['PATH'] = bin_dir + os.pathsep + env['PATH']

            result = subprocess.run(
                ['sh', SCRIPT, '--yes',
                 os.path.join(d, 'twrp.img'),
                 os.path.join(d, 'boot.img'),
                 os.path.join(d, 'rootfs.img')],
                capture_output=True, text=True, env=env, cwd=d
            )
            self.assertNotEqual(result.returncode, 0)

            calls_log = os.path.join(d, 'calls.log')
            self.assertTrue(os.path.exists(calls_log))
            with open(calls_log) as f:
                lines = f.read().splitlines()

            self.assertEqual(len(lines), 5)
            self.assertEqual(lines[0], 'fastboot format:ext4 userdata')
            self.assertTrue(lines[1].startswith('fastboot boot ') and lines[1].endswith('twrp.img'))
            self.assertEqual(lines[2], 'adb wait-for-recovery')
            self.assertEqual(lines[3], 'adb shell mountpoint -q /data || mount -t ext4 /dev/block/platform/soc/1d84000.ufshc/by-name/userdata /data')
            self.assertTrue(lines[4].startswith('adb push '))
            self.assertTrue(lines[4].endswith('/data/rootfs.img'))

            for l in lines:
                if l.startswith('fastboot boot'):
                    self.assertFalse(l.endswith('boot.img'))


if __name__ == '__main__':
    unittest.main()
