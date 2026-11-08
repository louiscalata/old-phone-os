# SPDX-License-Identifier: GPL-3.0-or-later
"""The Duo OS pieces together: the keyboard, the Network page, the first run and the time zone in one real server, the
acceptance script's expectations of that server, and the image wiring that has to agree with them.

Each piece has its own tests (test_osk, test_net, test_firstrun, test_timezone, test_selftest). These check what none of
them can alone: that the pages one track adds carry the keyboard another adds, that the three switches work in one
process, that the selftest accepts what the real server does, and that the files, units and docs name the same things.
Nothing here touches a phone; what only the phone can show is in docs/duo-os-final.md and docs/duo-acceptance.md.
"""
import contextlib
import http.client
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from opos import firstrun, osk, web  # noqa: E402
from tests.test_selftest import Fake, SERVER_ENV  # noqa: E402

ORIG_PAGE = web.page  # before any run of the first-run hook
DUO = ROOT / "packaging/duo-os"
PAGE_CAP = 100 * 1024  # tests/test_uxp30_budget.py measures page(); the Duo OS adds links and two tags on top, by injection
DUO_ALLOWANCE = 650  # bytes the three tracks may inject into the Duo OS home page (measured: about 590)
ALL_ON = {"OPOS_OSK": "1", "OPOS_NETWORK": "1", "OPOS_FIRSTRUN": "1"}


def get(port, path, headers=None, method="GET"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        c.request(method, path, headers=headers or {})
        r = c.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        c.close()


def nonce_of(headers):
    return re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)'", headers["content-security-policy"]).group(1)


def clean_env(**extra):
    e = {k: v for k, v in os.environ.items() if not k.startswith("OPOS_") and k != "TZ"}
    e.update(extra)
    return e


@contextlib.contextmanager
def duo_server(**env):
    """The real server in a thread with these switches (and a private config directory); hooks and environment put back after."""
    cfg = tempfile.mkdtemp(prefix="opos-final-")
    with mock.patch.dict(os.environ, clean_env(OPOS_CONFIG_DIR=cfg, **env), clear=True):
        undo = firstrun.install(web)
        srv = web.make_server(0, web.Phone())
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        try:
            yield srv.server_address[1], cfg
        finally:
            srv.shutdown()
            srv.server_close()
            if undo:
                undo()
            shutil.rmtree(cfg, ignore_errors=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TogetherInOneServer(unittest.TestCase):
    def test_every_page_the_tracks_add_carries_the_keyboard_once(self):
        with duo_server(**ALL_ON) as (port, cfg):
            st, h, home = get(port, "/")
            self.assertEqual(st, 200)
            n = nonce_of(h)
            tags = osk.tags(n)
            self.assertEqual(home.decode().count(tags), 1)
            for path in ("/network", "/firstrun", "/timezone"):
                st, h, body = get(port, path)
                self.assertEqual(st, 200, path)
                self.assertEqual(body.decode().count(osk.tags(nonce_of(h))), 1, path + " has the keyboard once, under its own nonce")
                self.assertEqual(int(h["content-length"]), len(body), path)
                self.assertIn("script-src 'nonce-", h["content-security-policy"])
            for path in ("/osk.js", "/osk.css"):
                self.assertEqual(get(port, path)[0], 200, path)
                self.assertEqual(get(port, path, method="HEAD")[0], 200, path)

    def test_settings_links_to_the_network_page_and_the_time_zone_page(self):
        with duo_server(**ALL_ON) as (port, cfg):
            home = get(port, "/")[2].decode()
            self.assertIn('href="/network"', home)
            self.assertIn('href="/timezone"', home)
            self.assertLess(home.index('href="/network"'), home.index('aria-labelledby="set-about"'))
            self.assertLess(home.index('href="/timezone"'), home.index('aria-labelledby="set-about"'))
            self.assertEqual(home.count('aria-labelledby="set-about"'), 1)

    def test_a_browser_is_sent_to_the_first_run_and_a_script_is_not(self):
        with duo_server(**ALL_ON) as (port, cfg):
            st, h, _ = get(port, "/", {"Accept": "text/html,application/xhtml+xml"})
            self.assertEqual((st, h["location"]), (302, "/firstrun"))
            self.assertEqual(get(port, "/")[0], 200, "the kiosk's wait loop and the update health check get the home page")
            self.assertEqual(get(port, "/", {"Accept": "application/json"})[0], 200)

    def test_the_first_run_goes_away_once_it_is_done_and_the_pages_stay(self):
        with duo_server(**ALL_ON) as (port, cfg):
            Path(cfg, "firstrun.json").write_text('{"version": 1, "skipped": true}\n')
            self.assertEqual(get(port, "/", {"Accept": "text/html"})[0], 200)
            for path in ("/firstrun", "/timezone", "/network"):
                self.assertEqual(get(port, path)[0], 200, path)

    def test_unset_means_the_pages_as_they_were(self):
        with duo_server() as (port, cfg):
            st, h, home = get(port, "/")
            token = re.search(rb'name="opos-token" content="([^"]+)"', home).group(1).decode()
            self.assertEqual(home, ORIG_PAGE(token, nonce_of(h)).encode(), "byte-identical home page with nothing set")
            self.assertEqual(get(port, "/", {"Accept": "text/html"})[0], 200, "no first run")
            for path in ("/firstrun", "/timezone", "/osk.js", "/osk.css", "/api/firstrun"):
                self.assertEqual(get(port, path)[0], 404, path)
            st, h, net = get(port, "/network")
            self.assertEqual(st, 200)
            self.assertNotIn(b"/osk.js", net)

    def test_each_switch_alone_adds_only_its_piece(self):
        for env, has, hasnt in (({"OPOS_OSK": "1"}, ('src="/osk.js"',), ('href="/network"', 'href="/timezone"')),
                                ({"OPOS_NETWORK": "1"}, ('href="/network"',), ('src="/osk.js"', 'href="/timezone"')),
                                ({"OPOS_FIRSTRUN": "1"}, ('href="/timezone"',), ('src="/osk.js"', 'href="/network"'))):
            with duo_server(**env) as (port, cfg):
                home = get(port, "/")[2].decode()
                for x in has:
                    self.assertIn(x, home, (env, x))
                for x in hasnt:
                    self.assertNotIn(x, home, (env, x))

    def test_the_duo_home_page_is_larger_than_page_by_the_injections_only(self):
        with duo_server(**ALL_ON) as (port, cfg):
            st, h, home = get(port, "/")
            token = re.search(rb'name="opos-token" content="([^"]+)"', home).group(1).decode()
            with mock.patch.dict(os.environ, clean_env(), clear=True):  # page() itself adds the Network link when OPOS_NETWORK=1
                plain = len(ORIG_PAGE(token, nonce_of(h)).encode())
            extra = len(home) - plain
            self.assertLessEqual(plain, PAGE_CAP, "web.page() itself stays within the 100 KiB cap")
            self.assertLessEqual(extra, DUO_ALLOWANCE, "the links and tags the three tracks inject: %d bytes, so the Duo OS home page is %d" % (extra, len(home)))
            self.assertGreater(extra, 300, "all three injected something")

    def test_the_selftest_accepts_what_the_real_server_does(self):
        with duo_server(**ALL_ON) as (port, cfg):
            f = Fake("http://127.0.0.1:%d/" % port)
            self.addCleanup(f.cleanup)
            f.put("usr/libexec/opos/opos-kiosk", (DUO / "opos-kiosk").read_text(encoding="utf-8"))  # the image's own script
            r = f.run("--only", "osk", "--only", "net.page", "--only", "net.permissions", "--only", "firstrun", "--only", "clock.kiosk-tz")
            for cid in ("osk.keyboard", "net.page", "net.permissions", "firstrun.flag", "firstrun.routes"):
                self.assertEqual(r.status(cid), "PASS", "%s: %s" % (cid, r.rows.get(cid)))
            self.assertEqual(r.status("clock.kiosk-tz"), "SKIP", "the real kiosk script exports the zone; none is chosen yet: " + str(r.rows.get("clock.kiosk-tz")))
            self.assertIn("PENDING", r.ev("firstrun.routes"))
            self.assertIn("keyboard loaded on it: yes", r.ev("net.page"))
            self.assertEqual(r.rc, 0, r.text)

    def test_the_selftest_notices_what_a_real_server_without_the_switches_lacks(self):
        with duo_server() as (port, cfg):  # the image was built with the pieces (the Fake has their modules), but the server got no switches
            f = Fake("http://127.0.0.1:%d/" % port)
            self.addCleanup(f.cleanup)
            f.proc(300, ["/usr/bin/python3", "-m", "opos.web", "--port", "8765"], "python3", env={"OPOS_ON_PHONE": "1"})
            r = f.run("--only", "osk", "--only", "net.page", "--only", "firstrun")
            self.assertEqual((r.status("osk.keyboard"), r.status("net.page"), r.status("firstrun.flag")), ("FAIL", "FAIL", "FAIL"), r.text)
            self.assertEqual(r.rc, 1)


NODE = os.environ.get("OPOS_TEST_NODE") or shutil.which("node")


class FirstRunHandOff(unittest.TestCase):
    """The first run's Wi-Fi step leaves for /network, whose Home link goes back through "/" to /firstrun: the answers so far
    must survive that, or the owner starts again at Welcome every time (seen in a desktop browser before this was fixed)."""

    JS = None

    @classmethod
    def setUpClass(cls):
        from opos import firstrun_page
        js = firstrun_page._FIRSTRUN_JS
        cls.JS = js
        cls.seg = js[js.index("var KEEP="):js.index("function pad(n)")]

    def test_the_page_keeps_forgets_and_restores(self):
        js = self.JS
        self.assertIn("sessionStorage.setItem(KEEP", js)
        self.assertIn("if(i>0)keep()", js, "every step after Welcome is kept")
        self.assertIn("var back=recall();", js)
        self.assertIn("show(back)", js)
        self.assertEqual(js.count("forget();"), 2, "forgotten when the setup is finished and when it is skipped")
        self.assertIn("A.zone||Z.saved", js, "the zone picker starts from the owner's choice")
        self.assertNotIn("localStorage", self.seg, "kept for this tab only")

    @unittest.skipUnless(NODE, "needs node (OPOS_TEST_NODE or PATH)")
    def test_recall_takes_back_only_what_it_trusts(self):
        script = r"""
const seg = require('fs').readFileSync(0, 'utf8');
function run(stored, now) {
  const store = {opos_firstrun: stored};
  const f = new Function('sessionStorage', 'Date', 'var cur=3,last=5,A={zone:null,goal:null,helper:null},g={on:true,min:1380},help="on";' + seg +
    'var r=recall();return {r:r,A:A,g:g,help:help}');
  return f({getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; }}, {now: () => now});
}
const t = 1000000, ok = {t: t - 5000, cur: 4, A: {zone: 'Europe/Berlin', goal: {enabled: true, time: '21:30'}, helper: 'off'}, g: {on: true, min: 1290}, help: 'off'};
const out = {};
out.good = run(JSON.stringify(ok), t);
out.stale = run(JSON.stringify(Object.assign({}, ok, {t: t - 1900000})), t).r;
out.future = run(JSON.stringify(Object.assign({}, ok, {t: undefined})), t).r;
out.none = run(null, t).r;
out.garbage = run('{not json', t).r;
out.array = run('[1,2]', t).r;
out.welcome = run(JSON.stringify(Object.assign({}, ok, {cur: 0})), t).r;
out.beyond = run(JSON.stringify(Object.assign({}, ok, {cur: 6})), t).r;
out.hostile = run(JSON.stringify({t: t - 1, cur: 4, A: {zone: '../../etc/passwd', goal: {enabled: 'yes', time: '25:99'}, helper: '<script>'}, g: {on: 'x', min: -5}, help: 'maybe'}), t);
out.floor = run(JSON.stringify(Object.assign({}, ok, {cur: 4.9, g: {on: false, min: 100.7}})), t);
console.log(JSON.stringify(out));
"""
        r = subprocess.run([NODE, "-e", script], input=self.seg, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        import json
        out = json.loads(r.stdout)
        self.assertEqual(out["good"]["r"], 4)
        self.assertEqual(out["good"]["A"], {"zone": "Europe/Berlin", "goal": {"enabled": True, "time": "21:30"}, "helper": "off"})
        self.assertEqual((out["good"]["g"], out["good"]["help"]), ({"on": True, "min": 1290}, "off"))
        for k in ("stale", "future", "none", "garbage", "array", "welcome", "beyond"):
            self.assertEqual(out[k], 0, k)
        h = out["hostile"]
        self.assertEqual(h["r"], 4)
        self.assertEqual(h["A"], {"zone": None, "goal": None, "helper": None}, "nothing hostile is taken")
        self.assertEqual((h["g"], h["help"]), ({"on": True, "min": 1380}, "on"))
        self.assertEqual((out["floor"]["r"], out["floor"]["g"]), (4, {"on": False, "min": 100}))

    # The whole page script, as shipped, on a small stand-in for the DOM and fetch: the walk the owner does.
    WALK = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
class El {
  constructor(tag, id) { this.tag = tag; this.id = id || ''; this.children = []; this.attrs = {}; this.dataset = {}; this._t = '';
    this.hidden = false; this.disabled = false; this.className = ''; this.tabIndex = 0; this.onclick = null; this.oninput = null; this.value = ''; }
  get textContent() { return this._t + this.children.map(c => c.textContent).join(''); }
  set textContent(v) { this._t = String(v); this.children = []; }
  appendChild(c) { this.children.push(c); return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  focus() {}
  scrollIntoView() {}
  all(pred, out = []) { for (const c of this.children) { if (pred(c)) out.push(c); c.all(pred, out); } return out; }
  querySelectorAll(sel) {
    return this.all(sel === '[aria-pressed=true]' ? e => e.attrs['aria-pressed'] === 'true' : e => e.tag === sel);
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || (sel === 'h2' ? new El('h2') : null); }
}
const settle = async () => { for (let i = 0; i < 30; i++) await new Promise(r => setImmediate(r)); };
async function load(store) {
  const ids = {};
  for (const m of input.body.matchAll(/<(\w+)([^>]*)>/g)) {
    const idm = /\bid="([^"]+)"/.exec(m[2]);
    if (!idm) continue;
    const e = new El(m[1], idm[1]);
    const rest = m[2].replace(/"[^"]*"/g, '');
    e.hidden = /\bhidden\b/.test(rest);
    e.disabled = /\bdisabled\b/.test(rest);
    ids[idm[1]] = e;
  }
  const posts = [], nav = [];
  const sessionStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } };
  const reply = o => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(o) });
  const fetchStub = (url, o) => {
    o = o || {};
    if (url === '/network') return reply({});
    if (url === '/api/firstrun' && o.method === 'POST') { posts.push(JSON.parse(o.body)); return reply({ ok: true, done: true, saved: {}, errors: [], restart: false }); }
    if (url === '/api/firstrun') return reply({ pending: true, clock: { plausible: true, text: 'Fri 06 Nov 2026, 10:00', note: '' }, goal: { enabled: true, time: '23:00' }, helper: 'on', timezone: { saved: null } });
    if (url === '/api/timezone') return reply({ zones: input.zones, source: 'system', saved: null, active: '', clock: { plausible: true, text: '', note: '' } });
    return Promise.reject(new Error('unexpected fetch ' + url));
  };
  const doc = { getElementById: id => ids[id] || null, createElement: t => new El(t), querySelector: s => (s === 'meta[name=opos-token]' ? { content: 'tok' } : null) };
  const loc = { set href(v) { nav.push(v); } };
  new Function('document', 'window', 'sessionStorage', 'fetch', 'location', 'setInterval', 'setTimeout', input.js)(doc, {}, sessionStorage, fetchStub, loc, () => 0, () => 0);
  await settle();
  return { ids, posts, nav };
}
const text = (p, id) => p.ids[id].textContent;
const shown = (p, id) => !p.ids[id].hidden;
async function press(p, id) { if (p.ids[id].disabled) throw new Error(id + ' is disabled'); p.ids[id].onclick({ preventDefault() {} }); await settle(); }
function pickZone(p, region, city) {
  const box = p.ids.tzbox;
  box.all(e => e.tag === 'button' && e.dataset.r === region)[0].onclick();
  box.all(e => e.tag === 'button' && e.dataset.z === region + '/' + city)[0].onclick();
}
(async () => {
  const out = {}, store = {};
  let p = await load(store);
  out.first = { prog: text(p, 'prog'), welcome: shown(p, 's-welcome') };
  await press(p, 'next');                                   // Welcome -> Time zone
  pickZone(p, 'Europe', 'Berlin');
  await press(p, 'next');                                   // -> Daily goal
  await press(p, 'hm');                                     // 23:00 -> 22:00
  await press(p, 'next');                                   // -> Helper
  await press(p, 'help-off');
  await press(p, 'next');                                   // -> Wi-Fi
  out.atWifi = { prog: text(p, 'prog'), link: shown(p, 'wifi-link'), step: shown(p, 's-wifi') };
  // "Open Wi-Fi setup" leaves the page; Home in /network comes back through "/" to a NEW /firstrun in the same tab
  p = await load(store);
  out.back = { prog: text(p, 'prog'), wifi: shown(p, 's-wifi'), welcome: shown(p, 's-welcome'), backButton: shown(p, 'back') };
  await press(p, 'next');                                   // -> Review
  out.review = p.ids.sum.children.map(c => c.textContent);
  await press(p, 'next');                                   // Finish
  out.posts = p.posts;
  out.nav = p.nav;
  out.cleared = !('opos_firstrun' in store);
  // Skip setup forgets too
  const store2 = {};
  let q = await load(store2);
  await press(q, 'next'); await press(q, 'next');
  out.keptOnTz = 'opos_firstrun' in store2;
  q = await load(store2);
  await press(q, 'skipall');
  out.skipPosts = q.posts; out.skipNav = q.nav; out.skipCleared = !('opos_firstrun' in store2);
  // A tab that was left for more than 30 minutes starts again at Welcome
  const store3 = { opos_firstrun: JSON.stringify({ t: Date.now() - 31 * 60 * 1000, cur: 4, A: { zone: 'Europe/Berlin', goal: null, helper: null }, g: { on: true, min: 1320 }, help: 'off' }) };
  const r = await load(store3);
  out.stale = { prog: text(r, 'prog'), welcome: shown(r, 's-welcome') };
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e.stack || e); process.exit(1); });
"""

    @unittest.skipUnless(NODE, "needs node (OPOS_TEST_NODE or PATH)")
    def test_answers_survive_the_wifi_handoff_in_the_real_page_script(self):
        """Choose a time zone, a goal and the Helper, take the Wi-Fi link, come back: the page is on the Wi-Fi step with every
        answer, and Finish sends them all."""
        import json
        from opos import firstrun_page
        payload = json.dumps({"js": firstrun_page._COMMON_JS + firstrun_page._FIRSTRUN_JS, "body": firstrun_page._FIRSTRUN_BODY,
                              "zones": ["Africa/Cairo", "Asia/Kolkata", "Europe/Berlin", "Europe/London", "UTC"]})
        r = subprocess.run([NODE, "-e", self.WALK], input=payload, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        out = json.loads(r.stdout)
        self.assertEqual(out["first"], {"prog": "Step 1 of 5", "welcome": True})
        self.assertEqual(out["atWifi"], {"prog": "Step 5 of 5", "link": True, "step": True})
        # the new page, in the same tab: not Welcome
        self.assertEqual(out["back"], {"prog": "Step 5 of 5", "wifi": True, "welcome": False, "backButton": True})
        self.assertEqual(out["review"], ["Time zone: Europe/Berlin", "Daily goal: 10:00 PM", "Helper: hidden"])
        self.assertEqual(len(out["posts"]), 1)
        sent = out["posts"][0]
        sent.pop("client_tz")
        self.assertEqual(sent, {"action": "finish", "zone": "Europe/Berlin", "goal": {"enabled": True, "time": "22:00"}, "helper": "off"})
        self.assertEqual(out["nav"], ["/"])
        self.assertTrue(out["cleared"], "finishing forgets the kept answers")
        self.assertTrue(out["keptOnTz"])
        self.assertEqual((out["skipPosts"], out["skipNav"], out["skipCleared"]), ([{"action": "skip"}], ["/"], True))
        self.assertEqual(out["stale"], {"prog": "Step 1 of 5", "welcome": True}, "a tab left for half an hour starts over")


class VerifyBlock(unittest.TestCase):
    def test_the_integrate_block_passes_against_a_real_server_started_the_way_the_image_starts_it(self):
        text = (DUO / "verify-image.sh").read_text(encoding="utf-8")
        m = re.search(r"^[ \t]*python3 -I - \"\$port2\" > \"\$work/opos-verify-duo\.txt\" 2>&1 <<'EOF'\n(.*?)^EOF\n", text, re.S | re.M)
        self.assertIsNotNone(m, "verify-image.sh has the track integrate probe")
        cfg = tempfile.mkdtemp(prefix="opos-final-")
        self.addCleanup(shutil.rmtree, cfg, True)
        port = free_port()
        env = clean_env(OPOS_CONFIG_DIR=cfg, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1", HOME=cfg, **ALL_ON)
        srv = subprocess.Popen([sys.executable, "-m", "opos.web", "--port", str(port)], env=env, cwd=cfg, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(srv.wait, 20)
        self.addCleanup(srv.terminate)
        p = subprocess.run([sys.executable, "-c", m.group(1), str(port)], capture_output=True, text=True, timeout=120)
        self.assertEqual(p.stdout.strip(), "duo pieces ok", p.stdout + p.stderr)

    def test_the_block_is_one_delimited_block_before_the_qa_block(self):
        text = (DUO / "verify-image.sh").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"^# --- track integrate: BEGIN", text, re.M)), 1)
        self.assertEqual(len(re.findall(r"^# --- track integrate: END", text, re.M)), 1)
        self.assertLess(text.index("# --- track integrate: BEGIN"), text.index("# --- qa: BEGIN"))
        self.assertGreater(text.index("# --- track integrate: BEGIN"), text.index('head -5 "$work/opos-verify.log"'))
        self.assertEqual([ln for ln in text.splitlines() if "pkill" in ln and not ln.startswith("check ") and not ln.lstrip().startswith("#")], [],
                         "the second server is stopped by the pid serve() recorded (see test_duo_verify_image.py), not by its command line")
        self.assertIn('stop_server "$duo_pid"', text)


class ImageWiring(unittest.TestCase):
    def read(self, name):
        return (DUO / name).read_text(encoding="utf-8")

    def test_every_script_parses(self):
        for n in ("build-image.sh", "verify-image.sh", "opos-diag", "opos-osk", "opos-kiosk", "opos-session"):
            r = subprocess.run(["sh", "-n", str(DUO / n)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, n + ": " + r.stderr)

    def test_the_build_has_each_tracks_block_once(self):
        b = self.read("build-image.sh")
        for marker in (r"^# --- track osk: the on-screen keyboard", r"^# --- end track osk", r"^# --- track: network \(Wi-Fi and radio status\)",
                       r"^# --- end track: network ---", r"^# --- qa: BEGIN", r"^# --- qa: END"):
            self.assertEqual(len(re.findall(marker, b, re.M)), 1, marker)
        for must in ("opos-osk.service", "20-opos-network.conf", "opos-selftest", "opos-diag"):
            self.assertIn(must, b)

    def test_the_server_unit_sets_the_switches_and_the_drop_in_sets_the_network(self):
        u = self.read("opos-server.service")
        self.assertRegex(u, r"(?m)^Environment=OPOS_OSK=1$")
        self.assertRegex(u, r"(?m)^EnvironmentFile=-/etc/default/opos-osk$")
        self.assertLess(u.index("Environment=OPOS_OSK=1"), u.index("EnvironmentFile=-/etc/default/opos-osk"), "the file overrides the default")
        self.assertRegex(u, r"(?m)^Environment=OPOS_FIRSTRUN=1$")
        self.assertRegex(u, r"(?m)^Environment=OPOS_BATTERY=live$")
        self.assertRegex(u, r"(?m)^Environment=OPOS_ON_PHONE=1$")
        self.assertRegex(self.read("opos-network.conf"), r"(?m)^Environment=OPOS_NETWORK=1$")

    def test_the_kiosk_keeps_what_the_restart_and_the_time_zone_need(self):
        k = self.read("opos-kiosk")
        self.assertIn("dir=$HOME/.cache/opos-kiosk", k)  # the profile directory firstrun.restart_kiosk finds Chromium by ...
        self.assertIn('--user-data-dir="$dir"', k)  # ... passed as Chromium's profile
        self.assertIn("track firstboot", k)
        self.assertLess(k.index("while :; do"), k.index("track firstboot"), "the zone is read before every Chromium start")

    def test_no_secret_or_private_address_is_in_the_new_files(self):
        for n in ("opos-server.service", "opos-network.conf", "opos-osk.service", "opos-osk", "opos-selftest", "50-opos-network.rules"):
            t = self.read(n)
            self.assertNotRegex(t, r"(?i)(password|psk|secret)\s*=\s*['\"]?\w{6,}", n)
            self.assertNotRegex(t, r"\b(192\.168|10\.\d{1,3}|172\.(1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b", n)


class RuntimeInventory(unittest.TestCase):
    def test_the_pmos_inventory_lists_every_module_the_duo_pieces_add(self):
        listed = (ROOT / "packaging/pmos/runtime-files.txt").read_text(encoding="utf-8").splitlines()
        paths = [x for x in listed if x and not x.startswith("#")]
        self.assertEqual(paths, sorted(paths))
        for f in ("osk.py", "osk_static/osk.css", "osk_static/osk.js", "net.py", "net_page.py", "firstrun.py", "firstrun_page.py", "timezone.py", "clockcheck.py"):
            self.assertIn("src/opos/" + f, paths)
            self.assertTrue((ROOT / "src/opos" / f).is_file(), f)

    def test_web_imports_only_what_the_inventory_or_a_guard_covers(self):
        # Every module web.py imports at its top level is either listed for the pmos runtime or imported inside try/except ImportError.
        listed = {x for x in (ROOT / "packaging/pmos/runtime-files.txt").read_text(encoding="utf-8").splitlines() if x and not x.startswith("#")}
        src = (ROOT / "src/opos/web.py").read_text(encoding="utf-8")
        mods = set()
        for m in re.finditer(r"^from opos import ([\w, ]+?)[ \t]*(?:#.*)?$", src, re.M):
            mods |= {x.strip() for x in m.group(1).split(",")}
        mods |= set(re.findall(r"^from opos\.(\w+) import", src, re.M)) | set(re.findall(r"^import opos\.(\w+)", src, re.M))
        found = {m for m in mods if (ROOT / "src/opos" / (m + ".py")).is_file()}
        self.assertTrue({"net_page", "muse", "secure"} <= found, found)
        for mod in sorted(found):
            self.assertIn("src/opos/%s.py" % mod, listed, "web.py imports opos.%s at its top level" % mod)


class DocsAgree(unittest.TestCase):
    def test_every_selftest_check_is_in_the_acceptance_doc(self):
        ids = set(re.findall(r'c\.emit\("(?:PASS|FAIL|WARN|SKIP)", "([a-z]+\.[a-z-]+)"', (DUO / "opos-selftest").read_text(encoding="utf-8")))
        ids |= {"battery.sysfs", "battery.api-match", "battery.temp"}
        doc = (ROOT / "docs/duo-acceptance.md").read_text(encoding="utf-8")
        self.assertGreater(len(ids), 30)
        missing = sorted(i for i in ids if i.startswith(("selftest.",)) is False and "`" + i + "`" not in doc)
        self.assertEqual(missing, [], "checks the acceptance doc does not name")

    def test_the_final_doc_names_every_switch_the_image_uses(self):
        doc = (ROOT / "docs/duo-os-final.md").read_text(encoding="utf-8")
        used = set()
        for n in ("opos-server.service", "opos-network.conf", "opos-osk.service", "opos-kiosk", "opos-osk", "build-image.sh"):
            used |= set(re.findall(r"\bOPOS_[A-Z_]+\b", (DUO / n).read_text(encoding="utf-8")))
        used |= {"OPOS_OSK_HINGE", "OPOS_KIOSK_RESTART", "OPOS_NET_TTY", "OPOS_NET_POLKIT"}
        used -= {"OPOS_DEV_SSH_PUBKEY"}  # dev-image build input, described in the README
        missing = sorted(u for u in used if u not in doc)
        self.assertEqual(missing, [])

    def test_the_indexes_link_the_final_doc(self):
        self.assertIn("duo-os-final.md", (ROOT / "docs/README.md").read_text(encoding="utf-8"))
        self.assertIn("duo-os-final.md", (DUO / "README.md").read_text(encoding="utf-8"))

    def test_the_final_doc_does_not_claim_what_was_not_tested(self):
        doc = (ROOT / "docs/duo-os-final.md").read_text(encoding="utf-8").lower()
        for must in ("simulated", "nothing is measured", "ram-boot", "never seen on the phone", "duo-acceptance.md"):
            self.assertIn(must, doc)
        for never in ("battery life is", "guaranteed", "production-ready", "fully tested"):
            self.assertNotIn(never, doc)


if __name__ == "__main__":
    unittest.main()
