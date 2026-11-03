# SPDX-License-Identifier: GPL-3.0-or-later;
import pathlib
import re
import unittest

class TestDuoDocs(unittest.TestCase):
    def test_usb_doc_covers_link_local_setup(self):
        readme_path = pathlib.Path(__file__).resolve().parent.parent / 'README.md'
        text = readme_path.read_text(encoding='utf-8')
        self.assertIn('(docs/duo-usb-install.md)', text)
        guide = readme_path.parent / 'docs' / 'duo-usb-install.md'
        text = guide.read_text(encoding='utf-8')
        usb_heading = '## Surface Duo USB link'
        usb_idx = text.index(usb_heading)
        self.assertIn('historical Droidian development guide', text[:usb_idx])
        self.assertIn('Duo device work remains paused', text[:usb_idx])
        next_heading_pos = text.find('\n## ', usb_idx + len(usb_heading))
        if next_heading_pos == -1:
            subsection = text[usb_idx:]
        else:
            subsection = text[usb_idx:next_heading_pos]
        markers = [
            '169.254',
            'fe80::1%8',
            '[fe80::1%8]',
            '%%8',
            'ifIndex',
            'ssh',
            'scp',
            'Get-NetIPConfiguration',
            'Get-NetNeighbor',
            'DHCP',
            'mDNS',
            'replug',
        ]
        for m in markers:
            self.assertIn(m, subsection)
        p1 = '10'
        p2 = '192'
        p3 = '168'
        p4 = '172'
        pattern = '(' + p1 + '|' + p2 + '\\.' + p3 + '|' + p4 + '\\.(1[6-9]|2[0-9]|3[01])' + ')(\\.[0-9]+){2,3}'
        self.assertIsNone(re.search(pattern, subsection))

    def test_doc_covers_reverse_tunnel(self):
        guide_path = pathlib.Path(__file__).resolve().parent.parent / 'docs' / 'duo-usb-install.md'
        text = guide_path.read_text(encoding='utf-8')
        markers = [
            'Model access through a reverse tunnel',
            'ssh -N -R 1236:127.0.0.1:1236',
            'OPOS_LAN_URL=http://127.0.0.1:1236/v1',
            'reboot',
            'replug',
            '10 seconds',
        ]
        for m in markers:
            self.assertIn(m, text)

    def test_doc_covers_no_curl_health_check(self):
        guide_path = pathlib.Path(__file__).resolve().parent.parent / 'docs' / 'duo-usb-install.md'
        text = guide_path.read_text(encoding='utf-8')
        heading = 'No curl on the phone'
        self.assertIn(heading, text)
        section = text[text.find(heading):]
        cut = section.find('\n## ', 1)
        if cut != -1:
            section = section[:cut]
        quad = '127.0.0.' + '1'
        base = 'http://' + quad + ':8765/'
        demo = base + '?demo=1'
        self.assertIn(base, section)
        self.assertIn(demo, section)
        cmds = [line for line in section.splitlines() if 'python3 -c' in line and 'urllib.request' in line]
        self.assertEqual(len(cmds), 2)
        for line in cmds:
            self.assertIn('urlopen', line)
            self.assertIn('print', line)
            self.assertIn('.status', line)
        self.assertIn(base, cmds[0] + cmds[1])
        self.assertIn(demo, cmds[0] + cmds[1])

if __name__ == '__main__':
    unittest.main()
