# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest


class TestDuoLanEnv(unittest.TestCase):
    def _doc_text(self):
        doc_path = pathlib.Path(__file__).resolve().parent.parent / 'docs' / 'duo-usb-install.md'
        return doc_path.read_text(encoding='utf-8')

    def test_no_lan_conf(self):
        self.assertNotIn('lan.conf', self._doc_text())

    def test_drop_in_steps(self):
        text = self._doc_text()
        self.assertIn('systemctl --user edit opos-server.service', text)
        self.assertIn('[Service]', text)
        self.assertIn('Environment=OPOS_LAN_URL=http://127.0.0.1:1236/v1', text)
        self.assertIn('systemctl --user restart opos-server.service', text)
        edit_pos = text.index('systemctl --user edit opos-server.service')
        env_pos = text.index('Environment=OPOS_LAN_URL=http://127.0.0.1:1236/v1')
        restart_pos = text.index('systemctl --user restart opos-server.service')
        self.assertLess(edit_pos, env_pos)
        self.assertLess(env_pos, restart_pos)


if __name__ == '__main__':
    unittest.main()
