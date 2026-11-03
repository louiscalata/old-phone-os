# SPDX-License-Identifier: GPL-3.0-or-later;
import unittest
from opos.web import page

class TestApiOffline(unittest.TestCase):
    def setUp(self):
        html = page()
        marker = 'async function api(o){'
        assert marker in html
        start = html.find(marker)
        depth = 0
        end = None
        for idx in range(start, len(html)):
            c = html[idx]
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = idx + 1
                    break
        self.api_src = html[start:end]

    def test_api_has_try_catch(self):
        src = self.api_src
        assert 'try{' in src
        assert 'catch(' in src
        assert src.find('try{') < src.find('fetch(')

    def test_api_returns_offline_on_failure(self):
        src = self.api_src
        err = "error:'offline'"
        assert err in src
        assert src.find(err) > src.find('catch(')

    def test_api_awaits_json(self):
        src = self.api_src
        assert 'await r.json()' in src
        try_idx = src.find('try{')
        catch_idx = src.find('catch(')
        await_idx = src.find('await r.json()')
        assert try_idx < await_idx < catch_idx
        assert 'return r.json()' not in src

    def test_request_unchanged(self):
        src = self.api_src
        assert "fetch('/api'" in src
        assert "method:'POST'" in src
        assert "'Content-Type':'application/json'" in src
        header_name = "'X-OPOS-" + "Token'"
        assert header_name in src
        lookup = "document.querySelector('meta[name=opos-" + "token]').content"
        assert lookup in src
        assert "JSON.stringify(o)" in src
