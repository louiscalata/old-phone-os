# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the public boundary over real sockets, with no outbound client capability."""
from contextlib import ExitStack, contextmanager
from http.cookies import SimpleCookie
import io
import json
import sys
import os
from pathlib import Path
import re
import socket
import shutil
import subprocess
import threading
import unittest
from unittest import mock

from opos import public_demo as public, secure, web


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


@contextmanager
def running(**kwargs):
    kwargs.setdefault('ip_limits', public.IPLimits(rate=10000, burst=10000))
    server = public.make_server(0, 'demo.example,localhost:8080,healthcheck.railway.app', **kwargs)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def wire_request(method, path, headers, body):
    if isinstance(headers, dict):
        headers = list(headers.items())
    headers = list(headers)
    if not any(k.lower() == 'host' for k, _ in headers):
        headers.append(('Host', 'demo.example'))
    if method == 'POST' and not any(k.lower() == 'content-length' for k, _ in headers):
        headers.append(('Content-Length', str(len(body))))
    wire = f'{method} {path} HTTP/1.1\r\n'.encode()
    wire += ''.join(f'{k}: {v}\r\n' for k, v in headers).encode()
    wire += b'Connection: close\r\n\r\n' + body
    return wire


def parse_response(stream):
    status = int(stream.readline().split()[1])
    out = {}
    while True:
        line = stream.readline().decode().rstrip('\r\n')
        if not line:
            break
        key, value = line.split(':', 1)
        out[key.lower()] = value.strip()
    payload = stream.read(int(out.get('content-length', 0)))
    return status, out, payload


def request(server, method='GET', path='/', headers=(), body=b''):
    """Raw socket client permits disabling all urllib/http.client outbound calls."""
    wire = wire_request(method, path, headers, body)
    with socket.create_connection(('127.0.0.1', server.server_port), timeout=5) as conn:
        conn.sendall(wire)
        return parse_response(conn.makefile('rb'))



class Visitor:
    def __init__(self, server, **headers):
        self.server, self.headers = server, headers
        self.cookie = self.token = ''
        status, self.last_headers, self.page = self.get('/')
        assert status == 200, (status, self.page)

    def req(self, method, path, body=b'', extra=None):
        headers = dict(self.headers, Cookie=self.cookie, **{'X-OPOS-Token': self.token})
        if method == 'POST':
            headers['Content-Type'] = 'application/json'
        headers.update(extra or {})
        status, self.last_headers, payload = request(self.server, method, path, headers, body)
        if 'set-cookie' in self.last_headers:
            self.cookie = self.last_headers['set-cookie'].split(';')[0]
        self.token = self.last_headers.get('x-opos-token', self.token)
        return status, self.last_headers, payload

    def get(self, path='/api/state', extra=None):
        return self.req('GET', path, extra=extra)

    def post(self, action, **kwargs):
        status, _, payload = self.req('POST', '/api', json.dumps(dict(action=action, **kwargs)).encode())
        return status, json.loads(payload)

    def state(self):
        status, _, payload = self.get()
        assert status == 200, (status, payload)
        return json.loads(payload)


class PublicBoundary(unittest.TestCase):
    def test_two_visitors_have_separate_state_tokens_and_locks(self):
        with running() as server:
            a, b = Visitor(server), Visitor(server)
            self.assertNotEqual(a.cookie, b.cookie)
            self.assertNotEqual(a.token, b.token)
            self.assertEqual(a.post('battery', level=20)[0], 200)
            self.assertEqual(a.post('receive', number='+15550100', text='private demo message')[0], 200)
            self.assertEqual(a.post('call', number='+15550100')[0], 200)
            self.assertEqual(a.state()['battery']['percent'], 20)
            state = b.state()
            self.assertEqual(state['battery']['percent'], 60)
            self.assertEqual(state['threads'], [])
            self.assertFalse(state['in_call'])
            self.assertEqual(a.get(extra={'X-OPOS-Token': b.token})[0], 403)
            self.assertEqual(b.get(extra={'X-OPOS-Token': a.token})[0], 403)
            a.post('undo', name='maps')
            self.assertIn('maps', b.state()['helper']['paused'])
            sessions = list(server.RequestHandlerClass.sessions.items.values())
            self.assertIsNot(sessions[0].lock, sessions[1].lock)
            self.assertIsNot(sessions[0].actions, sessions[1].actions)

    def test_expired_session_resets_without_replaying_action(self):
        clock = Clock()
        sessions = public.Sessions(clock=clock)
        with running(sessions=sessions) as server:
            a = Visitor(server)
            a.post('battery', level=20)
            old_cookie, old_token = a.cookie, a.token
            clock.now = public.IDLE_SECONDS + 1
            code, state = a.post('send', number='+15550100', text='do not replay')
            self.assertEqual(code, 200)
            self.assertEqual(state['threads'], [])
            self.assertEqual(state['battery']['percent'], 60)
            self.assertNotEqual(a.cookie, old_cookie)
            self.assertNotEqual(a.token, old_token)
            self.assertEqual(a.last_headers['x-opos-session-reset'], '1')
            self.assertEqual(a.post('send', number='+15550100', text='new message')[0], 200)
            self.assertEqual(len(a.state()['threads']), 1)
            self.assertEqual(len(sessions.items), 1)

    def test_lru_eviction_and_get_recovery(self):
        sessions = public.Sessions(capacity=2)
        with running(sessions=sessions) as server:
            a, b = Visitor(server), Visitor(server)
            a.post('battery', level=25)  # A is now most recently used.
            old = b.cookie
            c = Visitor(server)
            self.assertEqual(a.state()['battery']['percent'], 25)
            self.assertEqual(b.state()['battery']['percent'], 60)
            self.assertNotEqual(b.cookie, old)
            self.assertEqual(len(sessions.items), 2)
            self.assertNotEqual(b.cookie, c.cookie)

    def test_expired_helper_response_keeps_ui_shape(self):
        for action in ('ask', 'explain', 'plan'):
            with self.subTest(action=action):
                clock = Clock()
                with running(sessions=public.Sessions(clock=clock)) as server:
                    a = Visitor(server)
                    clock.now += public.IDLE_SECONDS + 1
                    status, reply = a.post(action, text='why?')
                    self.assertEqual(status, 200)
                    self.assertIn('battery', reply)
                    self.assertIn('order' if action == 'plan' else 'text', reply)

    def test_exact_hosts_origins_and_duplicate_headers(self):
        with running(trust_proxy=True) as server:
            a = Visitor(server, **{'X-Forwarded-Proto': 'https'})
            for host in ('evil.example', 'demo.example.evil', 'DEMO.EXAMPLE', 'demo.example:443', 'demo.example.', ''):
                self.assertEqual(request(server, headers={'Host': host})[0], 421, host)
            self.assertEqual(request(server, headers=[('Host', 'demo.example'), ('Host', 'demo.example')])[0], 421)
            for origin in ('null', 'https://evil.example', 'https://demo.example/', 'http://demo.example', 'https://demo.example:443'):
                self.assertEqual(a.get(extra={'Origin': origin})[0], 403, origin)
            self.assertEqual(a.get(extra={'Origin': 'https://demo.example'})[0], 200)
            self.assertEqual(a.get(extra={'Sec-Fetch-Site': 'cross-site'})[0], 403)
            self.assertEqual(request(server, headers=[('Origin', 'http://demo.example'), ('Origin', 'http://demo.example')])[0], 403)
            self.assertEqual(request(server, headers={'Host': 'healthcheck.railway.app'}, path='/healthz')[0], 200)

    def test_judge_can_follow_an_external_link_but_cross_site_api_is_denied(self):
        headers = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate',
                   'Sec-Fetch-Dest': 'document'}
        with running() as server:
            self.assertEqual(request(server, headers=headers)[0], 200)
            self.assertEqual(request(server, path='/?demo=1', headers=headers)[0], 200)
            self.assertEqual(request(server, path='/api/state', headers=headers)[0], 403)
            self.assertEqual(request(server, 'POST', '/api', headers, b'{}')[0], 403)
            self.assertEqual(request(server, headers=dict(headers, **{'Sec-Fetch-Dest': 'iframe'}))[0], 403)

    def test_cross_site_get_keeps_original_session(self):
        for path in ('/', '/?demo=1'):
            with self.subTest(path=path), running() as server:
                a = Visitor(server)
                self.assertEqual(a.post('battery', level=25)[0], 200)
                old_cookie, old_token = a.cookie, a.token
                cookie = SimpleCookie(a.last_headers['set-cookie'])[public.COOKIE]
                # Model browser cookie delivery on a cross-site top-level GET:
                # Strict withholds the cookie; Lax permits this safe navigation.
                headers = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate',
                           'Sec-Fetch-Dest': 'document'}
                if cookie['samesite'].lower() == 'lax':
                    headers['Cookie'] = old_cookie
                code, reply, _ = request(server, path=path, headers=headers)
                self.assertEqual(code, 200)
                self.assertEqual(reply['set-cookie'].split(';')[0], old_cookie)
                self.assertEqual(reply['x-opos-token'], old_token)
                self.assertNotIn('x-opos-session-reset', reply)
                self.assertEqual(len(server.RequestHandlerClass.sessions.items), 1)
                self.assertEqual(a.state()['battery']['percent'], 25)

    def test_ambiguous_forwarding_and_lengths_fail_closed(self):
        with running(trust_proxy=True) as server:
            for headers in ([('X-Forwarded-Proto', 'https, http')],
                            [('X-Forwarded-Proto', 'https'), ('X-Forwarded-Proto', 'https')],
                            [('X-Forwarded-For', '203.0.113.1'), ('X-Forwarded-For', '203.0.113.2')]):
                self.assertEqual(request(server, headers=headers)[0], 400)
            a = Visitor(server)
            headers = [('Cookie', a.cookie), ('X-OPOS-Token', a.token),
                       ('Content-Type', 'application/json'), ('Content-Length', '2'), ('Content-Length', '2')]
            self.assertEqual(request(server, 'POST', '/api', headers, b'{}')[0], 400)

    def test_host_configuration_fails_closed(self):
        for hosts in ('', '*', '*.up.railway.app', 'https://demo.example', 'demo.example/path',
                      'demo.example,', 'a:0', 'a:65536', 'a:xyz', 'a@b', 'a\r\nX:evil'):
            with self.subTest(hosts=hosts), self.assertRaises(ValueError):
                public.parse_hosts(hosts)
        self.assertEqual(public.parse_hosts('demo.example, localhost:8080'), {'demo.example', 'localhost:8080'})
        with mock.patch.dict(os.environ, {'OPOS_PUBLIC_HOSTS': ''}), self.assertRaises(SystemExit):
            public.main()

    def test_cookies_nonces_and_security_headers(self):
        for trusted, expected_secure in ((False, False), (True, True)):
            with running(trust_proxy=trusted) as server:
                a = Visitor(server, **{'X-Forwarded-Proto': 'https'})
                headers = a.last_headers
                c = SimpleCookie(headers['set-cookie'])[public.COOKIE]
                self.assertTrue(c['httponly'])
                self.assertEqual(bool(c['secure']), expected_secure)
                self.assertEqual(c['samesite'], 'Lax')
                self.assertEqual(c['path'], '/')
                self.assertEqual(c['max-age'], '900')
                self.assertFalse(c['domain'])
                self.assertEqual('strict-transport-security' in headers, expected_secure)
                self.assertEqual(headers['x-content-type-options'], 'nosniff')
                self.assertEqual(headers['referrer-policy'], 'no-referrer')
                self.assertEqual(headers['cache-control'], 'no-store')
                csp = headers['content-security-policy']
                self.assertIn("frame-ancestors 'none'", csp)
                nonces = re.findall(rb'<(?:script|style) nonce="([^"]+)"', a.page)
                self.assertGreater(len(nonces), 5)
                self.assertEqual(len(set(nonces)), 1)
                self.assertIn("'nonce-" + nonces[0].decode() + "'", csp)
                self.assertNotIn(b'<script>', a.page)
                self.assertNotRegex(a.page, rb'<[^>]+\son\w+=')
                self.assertNotRegex(a.page, rb'<(?:script|link)[^>]+(?:src|href)="https?://')
                second = a.get('/')[2]
                self.assertNotEqual(re.findall(rb'nonce="([^"]+)"', second)[0], nonces[0])

    def test_every_non_phone_route_and_action_is_404(self):
        routes = ('/setup', '/setup/', '/api/setup', '/api/prefs', '/prefs', '/restart_helper',
                  '/install', '/installer', '/ai-tools', '/api/models', '/api/ask', '/api/plan',
                  '/src/opos/web.py', '/.env', '/../../README.md', '/%2e%2e/',
                  '//evil.example', '/api/state?x=1', '/?setup=1')
        with running() as server:
            a = Visitor(server)
            for path in routes:
                for method in ('GET', 'POST', 'HEAD', 'OPTIONS'):
                    self.assertEqual(a.req(method, path, b'{}')[0], 404, (method, path))
            for action in ('restart_helper', 'setup', 'install', 'prefs', 'ai_tools', 'modem', 'unknown'):
                self.assertEqual(a.post(action)[0], 404, action)
            for method in ('PUT', 'PATCH', 'DELETE', 'TRACE', 'CONNECT', 'BOGUS'):
                self.assertEqual(request(server, method, '/api')[0], 404)
            self.assertEqual(a.req('POST', '/healthz', b'{}')[0], 404)

    def test_health_is_stateless_and_not_blocked_by_ip_capacity(self):
        sessions = public.Sessions()
        limits = public.IPLimits(capacity=0)
        with running(sessions=sessions, ip_limits=limits, trust_proxy=True) as server:
            code, headers, body = request(server, path='/healthz', headers={'X-Forwarded-Proto': 'https'})
            self.assertEqual((code, body), (200, b'ok'))
            self.assertNotIn('set-cookie', headers)
            self.assertNotIn('x-opos-token', headers)
            self.assertEqual(len(sessions.items), 0)
            self.assertEqual(len(limits.items), 0)
            self.assertEqual(request(server)[0], 429)
            self.assertEqual(request(server, path='/healthz', headers={'Host': 'evil.example'})[0], 421)

    def test_body_caps_types_and_shapes(self):
        with running() as server:
            a = Visitor(server)
            self.assertEqual(a.req('POST', '/api', b'x' * (secure.MAX_BODY + 1))[0], 413)
            self.assertEqual(a.req('POST', '/api', b'{}', {'Content-Type': 'text/plain'})[0], 415)
            self.assertEqual(a.req('POST', '/api', b'{}', {'Transfer-Encoding': 'chunked'})[0], 400)
            for data in (b'[]', b'{', b'{"x":NaN}', b'[' * 2000):
                self.assertEqual(a.req('POST', '/api', data)[0], 400)
            for action, args in (('send', {'number': '1' * 65, 'text': 'hi'}),
                                 ('ask', {'text': 'x' * 1001}), ('ask', {'text': []}),
                                 ('sim', {'running': True, 'speed': 61}),
                                 ('sim', {'running': True, 'speed': True}),
                                 ('tick', {'real_s': -1}), ('in_use', {'name': []})):
                self.assertEqual(a.post(action, **args)[0], 400, (action, args))
            self.assertFalse(a.state()['running'])
            self.assertEqual(a.post('sim', running=True, speed=60)[0], 200)

    def test_ip_rate_limit_ignores_untrusted_forwarding(self):
        limits = public.IPLimits(rate=0, burst=2)
        with running(ip_limits=limits) as server:
            for ip in ('203.0.113.1', '203.0.113.2'):
                self.assertEqual(request(server, headers={'X-Forwarded-For': ip})[0], 200)
            code, headers, _ = request(server, headers={'X-Forwarded-For': '203.0.113.3'})
            self.assertEqual(code, 429)
            self.assertIn('retry-after', headers)
            self.assertEqual(set(limits.items), {'127.0.0.1'})

    def test_spoofed_leftmost_forwarding_cannot_evade_rate_limit(self):
        limits = public.IPLimits(rate=0, burst=1)
        with running(ip_limits=limits, trust_proxy=True) as server:
            self.assertEqual(request(server, headers={'X-Forwarded-For': '203.0.113.1, 203.0.113.2'})[0], 200)
            for spoofed in ('203.0.113.3', '203.0.113.4, 203.0.113.5', 'not an IP'):
                code, headers, _ = request(server, headers={
                    'X-Forwarded-For': f'{spoofed}, 203.0.113.2'})
                self.assertEqual(code, 429)
                self.assertIn('retry-after', headers)
            self.assertEqual(set(limits.items), {'203.0.113.2'})
            self.assertEqual(request(server, headers={'X-Forwarded-For': '203.0.113.2, 203.0.113.1'})[0], 200)
            for bad in ('not an IP', 'fe80::1%scope', 'x' * 100,
                        '203.0.113.1, invalid', '203.0.113.1, '):
                self.assertEqual(request(server, headers={'X-Forwarded-For': bad})[0], 400)
            self.assertEqual(request(server, headers={'X-Forwarded-For': '2001:db8::1'})[0], 200)
            self.assertEqual(request(server, headers={
                'X-Forwarded-For': '203.0.113.9, 2001:0db8::1'})[0], 429)
            self.assertEqual(request(server)[0], 200)  # Missing XFF uses socket peer.
            self.assertEqual(request(server)[0], 429)

    def test_ip_table_is_bounded_reclaims_idle_and_does_not_reset_active_bucket(self):
        clock = Clock()
        limits = public.IPLimits(rate=0, burst=1, capacity=2, clock=clock)
        self.assertTrue(limits.allow('a'))
        self.assertTrue(limits.allow('b'))
        self.assertFalse(limits.allow('c'))
        self.assertFalse(limits.allow('a'))
        self.assertEqual(len(limits.items), 2)
        clock.now += public.IDLE_SECONDS + 1
        self.assertTrue(limits.allow('c'))
        self.assertEqual(len(limits.items), 1)

    def test_per_session_action_limits_survive_ip_change(self):
        with running(trust_proxy=True) as server:
            a, b = Visitor(server), Visitor(server)
            for i in range(3):
                a.headers['X-Forwarded-For'] = f'203.0.113.{i + 1}'
                self.assertEqual(a.post('ask', text='why?')[0], 200)
            self.assertEqual(a.post('ask', text='why?')[0], 429)
            self.assertEqual(b.post('ask', text='why?')[0], 200)
            self.assertEqual(a.get()[0], 200)
            session = server.RequestHandlerClass.sessions.items[a.cookie.split('=', 1)[1]]
            session.actions._buckets['post'] = secure.RateLimiter(0, 1)
            self.assertEqual(a.post('battery', level=45)[0], 200)
            self.assertEqual(a.post('battery', level=46)[0], 429)

    def test_no_outbound_or_host_side_effects_for_any_action(self):
        blocked = ['urllib.request.urlopen', 'urllib.request.OpenerDirector.open',
                   'http.client.HTTPConnection.request', 'http.client.HTTPConnection.connect',
                   'http.client.HTTPSConnection.connect', 'opos.web.prefs.load', 'opos.web.prefs.save',
                   'opos.web.assist.answer', 'opos.web.assist.ask_route', 'opos.web.assist.explain_routes',
                   'opos.web.assist.free_mb', 'opos.web.self_rss_mb',
                   'opos.web.muse.ask', 'opos.web.muse.explain', 'opos.web.muse.plan',
                   'opos.web._route_with_glimmer', 'opos.web._restart_service', 'os._exit',
                   'pathlib.Path.write_text', 'pathlib.Path.write_bytes']
        actions = [('battery', {'level': 60}), ('temp', {'temp': 30}),
                   ('call', {'number': '+15550100'}), ('hang_up', {}),
                   ('send', {'number': '+15550100', 'text': 'hi'}),
                   ('receive', {'number': '+15550100', 'text': 'hello'}),
                   ('open', {'number': '+15550100'}), ('visible', {'visible': True}),
                   ('sim', {'running': True, 'speed': 60}), ('tick', {'real_s': 5}),
                   ('charge', {'on': True}), ('goal', {'time': '16:00'}),
                   ('undo', {'name': 'maps'}), ('in_use', {'name': 'email'}),
                   ('explain', {}), ('plan', {}), ('ask', {'text': 'Search the web', 'web': True})]
        with ExitStack() as stack:
            mocks = [stack.enter_context(mock.patch(target, side_effect=AssertionError(target))) for target in blocked]
            stack.enter_context(mock.patch.dict(os.environ, {
                'OPOS_GLIMMER_ROUTING': '1', 'OPOS_GLIMMER_URL': 'http://127.0.0.1:1234/v1',
                'OPOS_LAN_URL': 'http://127.0.0.1:8080', 'OPOS_SPARK_OPEN': '1', 'INVOCATION_ID': 'test'}))
            with running() as server:
                a = Visitor(server)
                for action, args in actions:
                    code, reply = a.post(action, **args)
                    self.assertEqual(code, 200, (action, reply))
                    if action in ('explain', 'plan', 'ask'):
                        self.assertNotIn(public.NOTICE, reply.get('text', reply.get('credit', '')))
                        self.assertEqual(reply['engine'], 'rules')
                self.assertEqual(a.post('restart_helper')[0], 404)
                self.assertEqual(a.state()['lean'], {'opos_mb': None, 'free_mb': None})
            for patched in mocks:
                patched.assert_not_called()

    def test_simulation_memory_and_speed_are_bounded(self):
        phone = public.DemoPhone()
        for i in range(600):
            phone.receive('+15550100', str(i))
            phone.send('+15550100', 'hi')
            phone.call('+15550100')
            phone.hang_up()
        self.assertEqual(len(phone.essentials.log), public.MAX_MESSAGES)
        self.assertEqual(len(phone.modem.events), public.MAX_MESSAGES)
        self.assertEqual(phone.essentials.outbox, [])
        with self.assertRaises(ValueError):
            phone.set_sim(True, 1e9)
        self.assertEqual(phone.sim.speed, 1)
        self.assertFalse(phone.running)
        with self.assertRaises(RuntimeError):
            phone.explain_routes()

    def test_explicit_public_bind_preserves_loopback_rule(self):
        with self.assertRaises(ValueError):
            secure.check_bind('0.0.0.0')
        with self.assertRaises(ValueError):
            web.make_server(0, web.Phone(), host='0.0.0.0')
        with running() as server:
            self.assertEqual(server.server_address[0], '0.0.0.0')
            self.assertEqual(server.MAX_CONNS, 32)
            self.assertEqual(server.RequestHandlerClass.DEADLINE, 15)

    def test_landing_keeps_page_budget_and_required_content(self):
        self.assertLessEqual(len(web.page().encode()), 102400)
        page = public.landing('a' * 43, 'b' * 43, demo=True)
        self.assertIn('Hackathon prototype. This public demo simulates the battery, tasks, calls and messages;', page)
        self.assertIn('has not run on a physical phone.', page)
        self.assertIn('https://github.com/louiscalata/old-phone-os', page)
        self.assertIn('https://fortheloveof.studio/code/old-phone-os/', page)
        self.assertIn('<div id="demo" data-demo="1">', page)
        self.assertIn('Open ?demo=1 controls', page)
        self.assertIn('Undo a soft pause first', page)
        self.assertLess(page.index('Undo a soft pause first'), page.index('Set Battery to 20%'))
        self.assertIn('Try charging and heat', page)
        self.assertIn('See the saving steps and Why?', page)
        self.assertIn('public-device', page)
        self.assertIn('The notices below describe the separate installable OPOS prototype.', page)
        self.assertNotIn('public-about', web.page())

    def test_railway_configuration(self):
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / 'railway.json').read_text())
        self.assertEqual(config['deploy']['startCommand'], 'python3 -m opos.public_demo')
        self.assertEqual(config['deploy']['healthcheckPath'], '/healthz')
        self.assertEqual(config['build']['builder'], 'DOCKERFILE')
        docker = (root / 'Dockerfile').read_text()
        self.assertIn('FROM python:3.12-slim', docker)
        self.assertIn('USER 65532:65532', docker)
        self.assertNotIn('pip install', docker)
        self.assertNotIn('RUN ', docker)
        self.assertEqual((root / 'Procfile').read_text().strip(), 'web: python3 -m opos.public_demo')


class PublicBindWithoutDNS(unittest.TestCase):
    def test_bind_path_does_not_resolve_hostnames(self):
        server = object.__new__(public.PublicServer)
        server.server_address = ('0.0.0.0', 8080)
        server.socket = mock.Mock()
        server.socket.getsockname.return_value = server.server_address
        with mock.patch('socket.getfqdn', side_effect=AssertionError('DNS')) as fqdn, \
             mock.patch('socket.gethostbyaddr', side_effect=AssertionError('DNS')) as reverse, \
             mock.patch('socket.getaddrinfo', side_effect=AssertionError('DNS')) as resolve:
            server.server_bind()
        server.socket.bind.assert_called_once_with(('0.0.0.0', 8080))
        self.assertEqual((server.server_name, server.server_port), ('0.0.0.0', 8080))
        fqdn.assert_not_called()
        reverse.assert_not_called()
        resolve.assert_not_called()


class PublicClientScript(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js unavailable for client script check')
    def test_token_rotation_and_disabled_controls(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['node', 'tests/public_demo_client.js'], cwd=root,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS: token rotation', result.stdout)


class MemoryConnection:
    """HTTP byte transport only. This does not verify TCP, timeouts or real binding."""
    def __init__(self, wire):
        self.input = io.BytesIO(wire)
        self.output = io.BytesIO()

    def makefile(self, *args):
        return self.input

    def sendall(self, data):
        self.output.write(data)

    def settimeout(self, seconds):
        pass


@contextmanager
def memory_server(**kwargs):
    class UnboundServer:
        MAX_CONNS = web._Server.MAX_CONNS
        def __init__(self, address, handler):
            self.server_address = address
            self.server_port = address[1]
            self.RequestHandlerClass = handler
    kwargs.setdefault('ip_limits', public.IPLimits(rate=10000, burst=10000))
    with mock.patch.object(public, 'PublicServer', UnboundServer):
        server = public.make_server(0, 'demo.example,localhost:8080,healthcheck.railway.app', **kwargs)
    yield server


def memory_request(server, method='GET', path='/', headers=(), body=b''):
    conn = MemoryConnection(wire_request(method, path, headers, body))
    server.RequestHandlerClass(conn, ('127.0.0.1', 12345), server)
    conn.output.seek(0)
    return parse_response(conn.output)


class PublicProtocolWithoutSockets(PublicBoundary):
    """Same boundary assertions through real HTTP parsing, with a memory transport.

    Kept separate from PublicBoundary so a sandbox failure cannot masquerade as
    a passing TCP integration test. No production behavior is patched.
    """
    def setUp(self):
        module = sys.modules[__name__]
        self.enterContext(mock.patch.object(module, 'running', memory_server))
        self.enterContext(mock.patch.object(module, 'request', memory_request))


if __name__ == '__main__':
    unittest.main()
