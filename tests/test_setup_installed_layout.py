# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep package-managed setup separate from the retained-checkout CLI."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opos.setup import cli, services


class InstalledCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.module = self.root / 'usr/lib/python/site-packages/opos/setup/cli.py'
        self.module.parent.mkdir(parents=True)
        self.module.write_text('# installed fixture\n')
        self.home = self.root / 'home'
        self.home.mkdir()

    def run_cli(self, arguments):
        output = io.StringIO()
        with mock.patch.object(cli, '__file__', str(self.module)):
            code = cli.main(arguments, env={}, home=self.home, platform='linux', out=output)
        return code, output.getvalue()

    def test_defaults_rejected_before_detection_or_applying_configuration(self):
        with mock.patch.object(cli, 'reboot_warning') as warning, \
             mock.patch('opos.setup.config.default_config') as config, \
             mock.patch('opos.setup.device.detect_device') as device, \
             mock.patch('opos.setup.apply.apply', return_value=SimpleNamespace(snapshot=list)) as apply:
            code, output = self.run_cli(['run', '--defaults'])
        self.assertEqual(code, 2)
        for operation in (warning, config, device, apply):
            operation.assert_not_called()
        self.assertIn('source checkout', output)
        self.assertIn('kiosk Settings', output)
        self.assertIn('available preferences', output)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_browser_run_and_reconfigure_do_not_launch_a_competing_service(self):
        for arguments in ([], ['run'], ['run', '--reconfigure'], ['run', '--dry-run']):
            with self.subTest(arguments=arguments), \
                 mock.patch('opos.setup.server.main', return_value=0) as server, \
                 mock.patch('opos.setup.config.load', return_value=None) as load:
                code, output = self.run_cli(arguments)
                self.assertEqual(code, 2)
                server.assert_not_called()
                load.assert_not_called()
                self.assertIn('system service', output)

    def test_package_status_does_not_report_a_different_per_user_configuration(self):
        with mock.patch('opos.setup.config.load', return_value=None) as load:
            code, output = self.run_cli(['status'])
        self.assertEqual(code, 2)
        load.assert_not_called()
        self.assertNotIn('configured:', output)
        self.assertIn('source checkout', output)

    def test_package_uninstall_leaves_configuration_and_services_untouched(self):
        prefs = self.home / 'prefs.json'
        prefs.write_text('{"existing": true}\n')
        with mock.patch('opos.setup.apply.uninstall', return_value=[]) as uninstall:
            code, output = self.run_cli(['uninstall'])
        self.assertEqual(code, 2)
        uninstall.assert_not_called()
        self.assertEqual(prefs.read_text(), '{"existing": true}\n')
        self.assertIn('package manager', output)

    def test_help_and_version_still_work_without_checkout_resources(self):
        for argument in ('--help', '--version'):
            with self.subTest(argument=argument), contextlib.redirect_stdout(io.StringIO()), \
                 self.assertRaises(SystemExit) as raised:
                self.run_cli([argument])
            self.assertEqual(raised.exception.code, 0)


class CheckoutResolutionTests(unittest.TestCase):
    def test_current_checkout_resolves_from_module_not_working_directory(self):
        expected = Path(cli.__file__).resolve().parents[3]
        self.assertEqual(services.checkout_root(Path(cli.__file__)), expected)

    def test_site_packages_is_never_treated_as_a_checkout_even_with_a_deploy_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / 'lib/python/site-packages/opos/setup/cli.py'
            module.parent.mkdir(parents=True)
            module.write_text('# fixture\n')
            wrong_root = module.parents[3]
            (wrong_root / 'deploy').mkdir()
            (wrong_root / 'deploy/opos-server.service').write_text('[Service]\n')
            with self.assertRaisesRegex(services.ServiceError, 'source checkout'):
                services.checkout_root(module)

    def test_missing_source_template_fails_before_configuration_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            module = Path(directory) / 'src/opos/setup/cli.py'
            module.parent.mkdir(parents=True)
            module.write_text('# retained checkout fixture\n')
            with mock.patch.object(cli, '__file__', str(module)), \
                 mock.patch('opos.setup.apply.apply') as apply, \
                 mock.patch('opos.setup.config.default_config') as config:
                output = io.StringIO()
                code = cli.main(['run', '--defaults'], env={}, home=Path(directory), platform='linux', out=output)
            self.assertEqual(code, 2)
            apply.assert_not_called()
            config.assert_not_called()
            self.assertIn('deploy/opos-server.service', output.getvalue())

    def test_status_and_uninstall_do_not_require_a_template_in_a_retained_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / 'src/opos/setup/cli.py'
            module.parent.mkdir(parents=True)
            module.write_text('# retained checkout fixture\n')
            self.assertEqual(services.checkout_root(module, require_template=False), root.resolve())
            with mock.patch.object(cli, '__file__', str(module)), \
                 mock.patch('opos.setup.apply.uninstall', return_value=[]) as uninstall:
                self.assertEqual(cli.main(['uninstall'], env={}, home=root, platform='linux', out=io.StringIO()), 0)
            uninstall.assert_called_once()

    def test_source_defaults_pass_the_validated_checkout_to_apply(self):
        with mock.patch.object(cli, 'reboot_warning', return_value=None), \
             mock.patch('opos.setup.device.detect_device', return_value={'id': 'unknown', 'source': 'stub'}), \
             mock.patch('opos.setup.apply.apply', return_value=SimpleNamespace(snapshot=list)) as apply:
            self.assertEqual(cli.main(['run', '--defaults'], env={}, home=Path('/unused'), platform='linux', out=io.StringIO()), 0)
        self.assertEqual(apply.call_args.kwargs['repo_root'], Path(cli.__file__).resolve().parents[3])
