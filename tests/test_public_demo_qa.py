# SPDX-License-Identifier: GPL-3.0-or-later
"""Outside QA regressions through the real public dispatcher, without sockets."""
from html.parser import HTMLParser
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from opos import public_demo as public, web
from tests import test_public_demo as transport


class PublicHelperQA(unittest.TestCase):
    def test_missed_deadline_answers_no_with_the_panel_forecast(self):
        phone = public.DemoPhone()
        phone.set_battery(30)
        panel = phone.state()['battery']
        answer = phone.reply('ask', 'Will calls last until 4 PM?')['text']
        self.assertFalse(panel['goal_met'])
        self.assertTrue(answer.startswith('No.'), answer)
        forecast = 'calls and messages until ~' + panel['calls_until']
        self.assertIn(forecast, panel['goal_note'])
        self.assertIn(forecast, answer)

    def test_met_deadline_answers_yes_with_the_same_forecast(self):
        phone = public.DemoPhone()
        panel = phone.state()['battery']
        self.assertTrue(panel['goal_met'])
        answer = phone.reply('ask', 'Will calls last until 4 PM?')['text']
        self.assertTrue(answer.startswith('Yes.'), answer)
        self.assertIn('calls and messages until ~' + panel['calls_until'], answer)
        self.assertEqual(panel['goal_note'], 'Calls and messages until ~' + panel['calls_until'])

    def test_question_time_is_used_without_changing_the_battery_goal(self):
        phone = public.DemoPhone()
        phone.set_battery(30)
        for question, expected in (
                ('Will calls last until 1 PM?', 'Yes.'),
                ('Will messages last until 16:00?', 'No.'),
                ('Will calls last by 12 AM?', 'No.'),
                ('Will calls last until 1 PM tomorrow?', 'No.')):
            with self.subTest(question=question):
                self.assertTrue(phone.reply('ask', question)['text'].startswith(expected))
                self.assertEqual(phone.dash.goal, '16:00')

    def test_answer_tracks_updated_level_and_undo(self):
        phone = public.DemoPhone()
        first = phone.reply('ask', 'Will calls last until 4 PM?')['text']
        phone.undo('maps')
        panel = phone.state()['battery']
        changed = phone.reply('ask', 'Will calls last until 4 PM?')['text']
        self.assertNotEqual(first, changed)
        self.assertIn('calls and messages until ~' + panel['calls_until'], changed)
        phone.set_battery(20)
        panel = phone.state()['battery']
        changed = phone.reply('ask', 'Will calls last until 4 PM?')['text']
        self.assertTrue(changed.startswith('No.'))
        self.assertIn('calls and messages until ~' + panel['calls_until'], changed)

    def test_charging_and_empty_battery_do_not_claim_a_discharge_forecast(self):
        phone = public.DemoPhone()
        phone.charging = True
        answer = phone.reply('ask', 'Will calls last until 4 PM?')['text']
        self.assertTrue(answer.startswith('Yes, while it stays plugged in.'))
        self.assertNotIn('until ~', answer)
        phone.set_battery(0)
        self.assertTrue(phone.reply('ask', 'Will calls last until 4 PM?')['text'].startswith('No.'))

    def test_invalid_time_requests_clarification_instead_of_guessing(self):
        phone = public.DemoPhone()
        for clock in ('4', '24:00', '4:99 PM', '13 PM', 'lunchtime'):
            with self.subTest(clock=clock):
                answer = phone.reply('ask', 'Will calls last until ' + clock + '?')['text']
                self.assertIn('Use a time like 16:00 or 4 PM', answer)

    def test_empty_and_unrelated_questions_get_honest_guidance(self):
        phone = public.DemoPhone()
        self.assertEqual(phone.reply('ask', '  '), {'error': 'Type a question first.'})
        self.assertIn('I can explain this simulated battery', phone.reply('ask', 'What is the weather?')['text'])
        phone.set_visible(False)
        self.assertEqual(phone.reply('ask', 'Will calls last until 4 PM?'), {'error': 'helper hidden'})

    def test_disclosure_is_not_repeated_in_answers_or_plan_credit(self):
        phone = public.DemoPhone()
        for action in ('ask', 'explain', 'plan'):
            with self.subTest(action=action):
                reply = phone.reply(action, 'Why is maps paused?')
                self.assertNotIn(public.NOTICE, reply.get('text', reply.get('credit', '')))
                self.assertEqual((reply['engine'], reply['where']), ('rules', 'battery rules'))
        self.assertEqual(phone.reply('explain')['text'], phone.state()['helper']['why_paused'])

    def test_http_dispatch_passes_the_actual_question(self):
        with transport.memory_server() as server, \
                mock.patch.object(transport, 'request', transport.memory_request):
            visitor = transport.Visitor(server)
            visitor.post('battery', level=30)
            code, yes = visitor.post('ask', text='Will calls last until 1 PM?')
            self.assertEqual(code, 200)
            code, no = visitor.post('ask', text='Will calls last until 4 PM?')
            self.assertEqual(code, 200)
            self.assertTrue(yes['text'].startswith('Yes.'))
            self.assertTrue(no['text'].startswith('No.'))
            self.assertEqual(visitor.state()['battery']['goal'], '16:00')


class PublicHeadAndIconQA(unittest.TestCase):
    def test_head_root_and_demo_have_get_headers_without_a_body(self):
        with transport.memory_server(trust_proxy=True) as server:
            for path in ('/', '/?demo=1'):
                with self.subTest(path=path):
                    headers = {'X-Forwarded-Proto': 'https'}
                    get_code, get_headers, body = transport.memory_request(server, path=path, headers=headers)
                    code, head_headers, head_body = transport.memory_request(server, 'HEAD', path, headers)
                    self.assertEqual((get_code, code), (200, 200))
                    self.assertEqual(head_body, b'')
                    self.assertEqual(int(head_headers['content-length']), len(body))
                    self.assertEqual(head_headers['content-type'], get_headers['content-type'])
                    self.assertIn("'nonce-", head_headers['content-security-policy'])
                    self.assertEqual(head_headers['strict-transport-security'], 'max-age=31536000')

    def test_favicon_get_and_head_work_without_creating_a_session(self):
        with transport.memory_server() as server:
            code, headers, body = transport.memory_request(server, path='/favicon.ico')
            self.assertEqual((code, body), (200, web.FAVICON))
            self.assertTrue(body.startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertEqual(headers['content-type'], 'image/png')
            self.assertNotIn('set-cookie', headers)
            self.assertEqual(len(server.RequestHandlerClass.sessions.items), 0)
            code, head, body = transport.memory_request(server, 'HEAD', '/favicon.ico')
            self.assertEqual((code, body), (200, b''))
            self.assertEqual(head['content-length'], headers['content-length'])
            self.assertEqual(transport.memory_request(server, 'POST', '/favicon.ico')[0], 404)

    def test_head_preserves_host_token_path_and_rate_boundaries(self):
        with transport.memory_server() as server:
            for path, headers, expected in (
                    ('/', {'Host': 'evil.example'}, 421),
                    ('/', {'Origin': 'https://evil.example'}, 403),
                    ('/setup', {}, 404),
                    ('/api/state', {'Cookie': 'invalid'}, 200)):
                # A missing session gets a fresh token, exactly as GET does.
                with self.subTest(path=path, headers=headers):
                    code, _, body = transport.memory_request(server, 'HEAD', path, headers)
                    self.assertEqual((code, body), (expected, b''))
            code, headers, _ = transport.memory_request(server)
            cookie = headers['set-cookie'].split(';')[0]
            code, _, body = transport.memory_request(server, 'HEAD', '/api/state', {'Cookie': cookie})
            self.assertEqual((code, body), (403, b''))
        with transport.memory_server(ip_limits=public.IPLimits(rate=0, burst=0)) as server:
            code, _, body = transport.memory_request(server, 'HEAD')
            self.assertEqual((code, body), (429, b''))
            code, headers, body = transport.memory_request(server, 'HEAD', '/healthz')
            self.assertEqual((code, body, headers['content-length']), (200, b'', '2'))


class LocalHeadAndIconQA(unittest.TestCase):
    def setUp(self):
        class Bound(web.Handler):
            port = 8765
            token = 't' * 43
            phone = mock.Mock()
            lock = threading.Lock()
        Bound.phone.state.return_value = {'status': 'ready'}
        self.server = SimpleNamespace(RequestHandlerClass=Bound)

    def request(self, method='GET', path='/', **headers):
        headers.setdefault('Host', '127.0.0.1:8765')
        return transport.memory_request(self.server, method, path, headers)

    def test_root_head_matches_get_headers_and_has_no_body(self):
        get_code, get_headers, body = self.request()
        code, headers, head_body = self.request('HEAD')
        self.assertEqual((get_code, code, head_body), (200, 200, b''))
        self.assertEqual(int(headers['content-length']), len(body))
        self.assertEqual(headers['content-type'], get_headers['content-type'])
        self.assertEqual(headers['content-type'], 'text/html; charset=utf-8')
        for values in (get_headers, headers):
            self.assertIn("'nonce-", values['content-security-policy'])
            self.assertIn("frame-ancestors 'none'", values['content-security-policy'])

    def test_head_enforces_host_and_state_token_and_omits_error_bodies(self):
        code, _, body = self.request('HEAD', Host='evil.example')
        self.assertEqual((code, body), (403, b''))
        code, _, body = self.request('HEAD', '/api/state', **{'X-OPOS-Token': 'wrong'})
        self.assertEqual((code, body), (403, b''))
        self.server.RequestHandlerClass.phone.state.assert_not_called()
        code, headers, body = self.request('HEAD', '/api/state', **{'X-OPOS-Token': self.server.RequestHandlerClass.token})
        self.assertEqual((code, body), (200, b''))
        self.assertEqual(headers['content-type'], 'application/json')
        self.assertGreater(int(headers['content-length']), 0)
        self.server.RequestHandlerClass.phone.state.assert_called_once_with()
        code, _, body = self.request('HEAD', '/missing')
        self.assertEqual((code, body), (404, b''))

    def test_local_favicon_get_and_head_return_png(self):
        code, headers, body = self.request(path='/favicon.ico')
        self.assertEqual((code, body), (200, web.FAVICON))
        self.assertTrue(body.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(headers['content-type'], 'image/png')
        code, head_headers, body = self.request('HEAD', '/favicon.ico')
        self.assertEqual((code, body), (200, b''))
        self.assertEqual(head_headers['content-length'], headers['content-length'])
        self.assertEqual(head_headers['content-type'], headers['content-type'])


class HeadTags(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.in_head = False
        self.meta = {}
        self.links = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        if tag == 'head':
            self.in_head = True
        if not self.in_head:
            return
        attrs = dict(attrs)
        if tag == 'meta':
            key = attrs.get('name', attrs.get('property'))
            self.meta.setdefault(key, []).append(attrs.get('content', ''))
        elif tag == 'link':
            self.links.append(attrs)

    def handle_endtag(self, tag):
        if tag == 'head':
            self.in_head = False


class PublicMetadataQA(unittest.TestCase):
    def test_public_share_metadata_has_titles_and_honest_descriptions(self):
        tags = HeadTags(public.landing('t' * 43, 'n' * 43))
        for key in ('og:title', 'twitter:title'):
            self.assertEqual(tags.meta[key], ['Old Phone OS Interface'])
        for key in ('description', 'og:description', 'twitter:description'):
            description = tags.meta[key]
            self.assertEqual(len(description), 1)
            self.assertIn('simulat', description[0].lower())
            self.assertIn('not an operating system', description[0].lower())
            self.assertIn('not run on a physical phone', description[0].lower())
        for key in ('og:image', 'twitter:image'):
            for url in tags.meta.get(key, []):
                self.assertTrue(url.startswith('/') and not url.startswith('//'), url)

    def test_public_and_local_pages_link_to_the_served_local_icon(self):
        for page in (web.page(), public.landing('t' * 43, 'n' * 43)):
            with self.subTest(public='public-demo' in page):
                links = [a for a in HeadTags(page).links if 'icon' in a.get('rel', '').split()]
                self.assertEqual(len(links), 1)
                self.assertEqual(links[0]['href'], '/favicon.ico')


if __name__ == '__main__':
    unittest.main()
