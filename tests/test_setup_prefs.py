# SPDX-License-Identifier: GPL-3.0-or-later
import unittest, tempfile
import io
import json
from pathlib import Path
import os
from types import SimpleNamespace
from unittest import mock
from opos.setup import prefs
from opos.setup.server import SetupContext, make_server

class PrefsTest(unittest.TestCase):
    def test_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'prefs.json'
            prefs.answer('theme','dark',p)
            self.assertEqual(prefs.load(p)['answers'].get('theme'),'dark')

    def test_resume(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'prefs.json'
            prefs.answer('theme','dark',p)
            prefs.answer('layout',{'favourites':['phone']},p)
            self.assertEqual(prefs.resume_step(prefs.load(p)), 'text_size')
            stray = Path(td)/'tmp123'
            stray.write_text('garbage')
            self.assertEqual(prefs.resume_step(prefs.load(p)), 'text_size')

    def test_finish(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'prefs.json'
            prefs.answer('theme','dark',p)
            prefs.answer('text_size','large',p)
            eff = prefs.finish(p)
            self.assertEqual(eff['theme'],'dark')
            self.assertEqual(eff['text_size'],'large')
            state = prefs.load(p)
            self.assertTrue(state['applied'])
            eff2 = prefs.finish(p)
            self.assertEqual(eff, eff2)

    def test_skip_all(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'prefs.json'
            prefs.skip_all(p)
            state = prefs.load(p)
            self.assertEqual(prefs.effective(state), prefs.defaults())

    def test_skip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'prefs.json'
            prefs.answer('theme','light',p)
            prefs.skip('layout',p)
            state = prefs.load(p)
            self.assertIn('layout', state['skipped'])
            self.assertEqual(prefs.resume_step(state), 'text_size')

    def test_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'prefs.json'
            with self.assertRaises(ValueError):
                prefs.answer('theme','purple',p)
            with self.assertRaises(ValueError):
                prefs.answer('wifi',{'ssid':'a','password':'x'},p)
            with self.assertRaises(ValueError):
                prefs.answer('wifi',{'ssid':'a'*33},p)
            with self.assertRaises(ValueError):
                prefs.answer('unknown','x',p)

    def test_mode(self):
        if os.name == 'nt':
            return
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'prefs.json'
            prefs.answer('theme','dark',p)
            self.assertEqual(oct(p.stat().st_mode & 0o777), oct(0o600))

    def test_wifi_secret(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'prefs.json'
            prefs.answer('wifi',{'ssid':'Home'},p)
            text = p.read_text()
            self.assertNotIn('password', text)

    def test_pin_record_only(self):
        # JRY34: the lock PIN step records only 'later' or a skip. Digits, in any shape, are refused
        # before anything is written, so no credential ever reaches prefs.json.
        # The step follows Wi-Fi (approved decisions, item 6); Helper keeps its place as the last step.
        self.assertEqual(prefs.STEPS.index('pin'), prefs.STEPS.index('wifi') + 1)
        self.assertEqual(prefs.STEPS[-1], 'helper')
        self.assertEqual(prefs.defaults()['pin'], 'later')
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'prefs.json'
            for bad in ('1234', 1234, '', None, True, {'pin': '1234'}, {'digits': '1234'}, ['1', '2', '3', '4'], 'LATER'):
                with self.assertRaises(ValueError):
                    prefs.answer('pin', bad, p)
            self.assertFalse(p.exists())
            prefs.answer('pin', 'later', p)
            self.assertEqual(prefs.load(p)['answers']['pin'], 'later')
            prefs.answer('pin', 'skip', p)
            self.assertEqual(prefs.load(p)['answers']['pin'], 'skip')
            self.assertNotIn('1234', p.read_text())
            prefs.skip('pin', p)
            state = prefs.load(p)
            self.assertIn('pin', state['skipped'])
            self.assertNotIn('pin', state['answers'])
            self.assertEqual(prefs.effective(state)['pin'], 'later')
            # Resume order: once Wi-Fi and everything before it is settled, the wizard resumes at pin, then Helper.
            through_wifi = list(prefs.STEPS[:prefs.STEPS.index('wifi') + 1])
            self.assertEqual(prefs.resume_step({'answers': {}, 'skipped': through_wifi}), 'pin')
            self.assertEqual(prefs.resume_step({'answers': {'pin': 'later'}, 'skipped': through_wifi}), 'helper')
            self.assertEqual(prefs.resume_step({'answers': {}, 'skipped': through_wifi + ['pin']}), 'helper')
            prefs.skip_all(p)
            self.assertIsNone(prefs.resume_step(prefs.load(p)))

    def test_corrupt(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'prefs.json'
            p.write_text('not json')
            state = prefs.load(p)
            self.assertEqual(state, {'schema':1,'answers':{},'skipped':[],'applied':False})


class PreferenceRoutesTest(unittest.TestCase):
    """Exercise real preference routing and persistence without binding a socket."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.selected = self.root / 'selected' / 'prefs.json'
        self.ambient = self.root / 'ambient' / 'prefs.json'
        self.env = {'OPOS_CONFIG_DIR': str(self.selected.parent)}
        environment = mock.patch.dict(os.environ, {'OPOS_CONFIG_DIR': str(self.ambient.parent)})
        environment.start()
        self.addCleanup(environment.stop)
        prefs.answer('theme', 'light', self.ambient)
        prefs.skip('layout', self.ambient)
        self.ambient_before = self.ambient.read_bytes()
        prefs.answer('theme', 'dark', self.selected)
        self.server = self.make_server(self.env)

    def tearDown(self):
        self.assertEqual(self.ambient.read_bytes(), self.ambient_before)

    def make_server(self, env, platform='linux'):
        context = SetupContext(repo_root=self.root, python='python3', home=self.root,
                               platform=platform, env=env, dry_run=True)

        def capture_handler(address, handler):
            return SimpleNamespace(server_address=address, RequestHandlerClass=handler)

        with mock.patch('opos.setup.server.ThreadingHTTPServer', side_effect=capture_handler):
            return make_server(8766, context)

    def request(self, method, route='/setup/api/prefs', body=None, server=None):
        server = server or self.server
        handler = server.RequestHandlerClass.__new__(server.RequestHandlerClass)
        raw = json.dumps(body or {}).encode()
        handler.command = method
        handler.path = route
        handler.client_address = ('127.0.0.1', 12345)
        handler.headers = {
            'Host': '127.0.0.1:8766', 'Origin': 'http://127.0.0.1:8766',
            'X-OPOS-Token': server.token, 'Content-Type': 'application/json',
            'Content-Length': str(len(raw)),
        }
        handler.rfile = io.BytesIO(raw)
        handler._shrink_timeout = mock.Mock()
        handler._json = mock.Mock()
        handler._handle()
        handler._json.assert_called_once()
        status, payload = handler._json.call_args.args
        self.assertEqual(status, 200, payload)
        return payload

    def assert_payload(self, payload, path=None):
        state = prefs.load(path or self.selected)
        self.assertEqual(payload['state'], state)
        self.assertEqual(payload['effective'], prefs.effective(state))
        self.assertEqual(payload['resume'], prefs.resume_step(state))

    def test_get_reads_selected_profile(self):
        payload = self.request('GET')
        self.assert_payload(payload)
        self.assertEqual(payload['effective']['theme'], 'dark')
        self.assertEqual(payload['resume'], 'layout')

    def test_answer_writes_selected_profile_and_returns_its_state(self):
        payload = self.request('POST', body={'step': 'text_size', 'value': 'large'})
        self.assertEqual(prefs.load(self.selected)['answers'], {'theme': 'dark', 'text_size': 'large'})
        self.assert_payload(payload)

    def test_skip_updates_selected_profile(self):
        self.assertEqual(self.request('POST', '/setup/api/prefs/skip', {'step': 'theme'}), {})
        state = prefs.load(self.selected)
        self.assertEqual(state['answers'], {})
        self.assertEqual(state['skipped'], ['theme'])

    def test_skip_all_updates_selected_profile(self):
        self.assertEqual(self.request('POST', '/setup/api/prefs/skip-all'), {})
        state = prefs.load(self.selected)
        self.assertEqual(state['answers'], {'theme': 'dark'})
        self.assertEqual(state['skipped'], sorted(set(prefs.STEPS) - {'theme'}))

    def test_finish_updates_selected_profile_and_returns_its_state(self):
        payload = self.request('POST', '/setup/api/prefs/finish')
        self.assertTrue(prefs.load(self.selected)['applied'])
        self.assert_payload(payload)
        self.assertEqual(payload['effective']['theme'], 'dark')

    def test_missing_selected_profile_starts_empty(self):
        self.selected.unlink()
        payload = self.request('GET')
        self.assertEqual(payload['state']['answers'], {})
        self.assertEqual(payload['effective'], prefs.defaults())
        self.assertEqual(payload['resume'], 'theme')
        self.assertFalse(self.selected.exists())
        self.request('POST', body={'step': 'theme', 'value': 'dark'})
        self.assertEqual(prefs.load(self.selected)['answers'], {'theme': 'dark'})

    def test_context_platform_and_home_choose_default_directory(self):
        for platform, suffix in (('darwin', 'Library/Application Support/opos'), ('linux', '.config/opos')):
            with self.subTest(platform=platform):
                home = self.root / platform
                path = home / suffix / 'prefs.json'
                server = self.make_server({'HOME': str(home)}, platform)
                payload = self.request('POST', body={'step': 'theme', 'value': 'dark'}, server=server)
                self.assertEqual(prefs.load(path)['answers'], {'theme': 'dark'})
                self.assert_payload(payload, path)

    def test_preference_directory_is_resolved_when_server_is_created(self):
        other = self.root / 'other'
        self.env['OPOS_CONFIG_DIR'] = str(other)
        payload = self.request('POST', body={'step': 'helper', 'value': 'off'})
        self.assertEqual(prefs.load(self.selected)['answers']['helper'], 'off')
        self.assert_payload(payload)
        self.assertFalse(other.exists())
