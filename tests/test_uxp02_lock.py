# SPDX-License-Identifier: GPL-3.0-or-later
import re
import unittest

from opos.web import page


class TestLock(unittest.TestCase):
    def lock_script(self):
        return page().split("var cover=document.getElementById('lock')", 1)[1].split('</script>', 1)[0]

    def test_lock_markup_hidden_by_default(self):
        html = page()
        self.assertRegex(html, r'<div id="lock"[^>]*\bhidden\b')
        block = html.split('<div id="lock"', 1)[1].split('</button></div>', 1)[0]
        self.assertIn('aria-modal="true"', block)
        for element in ('unlock', 'lock-ask', 'lock-hangup'):
            self.assertIn('id="%s"' % element, block)
        self.assertNotRegex(block, r'<input\b[^>]*type="(?:password|text)"')

    def test_lock_has_independent_exit(self):
        html = self.lock_script()
        for text in ('Escape', 'lock=0', 'inert'):
            self.assertIn(text, html)
        body = html.split('// unlock definition start', 1)[1].split('// unlock definition end', 1)[0]
        self.assertIn('cover.hidden=true', body)
        self.assertIn('phone.inert=false', body)
        self.assertNotIn('fetch', body)
        self.assertNotIn('api(', body)
        self.assertIn("document.getElementById('unlock').onclick=safe(function(){window.oposUI.unlock();})", html)
        self.assertIn("if(e.key==='Escape')window.oposUI.unlock()", html)
        self.assertNotIn("cover.addEventListener('click'", html)
        self.assertIn("startY-e.clientY>40", html)

    def test_lock_first_render_and_bypass(self):
        script = self.lock_script()
        self.assertIn('if(first){\n        first=false;', script)
        pattern = re.search(r'if\(!/(.+)/\.test\(location.search\)\)window\.oposUI\.lock\(\)', script).group(1)
        for query in ('?lock=0', '?demo=1', '?app=full', '?x=1&lock=0&y=2'):
            self.assertRegex(query, pattern)
        for query in ('', '?block=0', '?lock=01', '?demo=10', '?app=fuller', '?x=lock=0'):
            self.assertNotRegex(query, pattern)

    def test_lock_focus_and_fail_open(self):
        script = self.lock_script()
        lock = script.split('window.oposUI.lock=function()', 1)[1].split('function safe', 1)[0]
        self.assertIn('cover.hidden=false;', lock)
        self.assertIn('phone.inert=true;', lock)
        self.assertIn("document.getElementById('unlock').focus();", lock)
        self.assertIn('catch(e){window.oposUI.unlock();}', lock)
        self.assertIn('catch(err){window.oposUI.unlock();}', script)
        self.assertIn("phone.inert=!document.getElementById('lock').hidden;", script)
        self.assertIn('if(lockFallback)lockFallback.hidden=true;', script)
        self.assertIn('if(phoneFallback)phoneFallback.inert=false;', script)

    def test_lock_css_covers_screen(self):
        html = page()
        css = re.search(r'#lock\{([^}]+)\}', html).group(1)
        for declaration in ('position:fixed', 'top:0', 'right:0', 'bottom:0', 'left:0', 'z-index:100'):
            self.assertIn(declaration, css)
        self.assertIn('#lock[hidden]{display:none}', html)


if __name__ == '__main__':
    unittest.main()
