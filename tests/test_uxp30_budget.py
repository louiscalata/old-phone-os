# SPDX-License-Identifier: GPL-3.0-or-later
"""UXP30: audit the effective inline CSS and the optional browser motion path."""
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from opos import firstrun, web
from opos.web import page
from tests import test_uxr_acceptance as acceptance

# Page cap: 100 KiB of UTF-8 (Louis, 2026-11-03). It was 67,370 bytes plus 30 KiB (98,090)
# until the Settings top-bar, brightness and volume controls needed the room.
PAGE_BUDGET = 100 * 1024

# The interface plan's wave rule (section 3): after every item, page() and the home page the server really sends with every switch
# on must both be at most 102,000 bytes. The 400 bytes under the 102,400 cap above are the reserve for what the server adds around
# page(): the keyboard's and the panes' tags, the Settings links, and the first-run decoration.
WAVE_GATE = 102000
SWITCHES = {"OPOS_OSK": "1", "OPOS_NETWORK": "1", "OPOS_FIRSTRUN": "1", "OPOS_MONITOR": "1", "OPOS_PANES": "1", "OPOS_SPAN": "675,42"}


def served_home_page(switches):
    """What GET / returns from a server built the way web.main() builds it, with the given switches (the others are off).

    main() lets opos.firstrun hook page() before it makes the Phone; make_server() alone does not, so a measurement that skips
    that step leaves out what OPOS_FIRSTRUN=1 adds (253 bytes at the time of writing)."""
    env = dict.fromkeys(SWITCHES, "")
    env.update(switches)
    with tempfile.TemporaryDirectory() as config, mock.patch.dict(os.environ, dict(env, OPOS_CONFIG_DIR=config)):
        undo = firstrun.install(web)
        server = web.make_server(0, web.Phone(), span=web.parse_span(switches["OPOS_SPAN"]) if "OPOS_SPAN" in switches else None)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=20)
            conn.request("GET", "/")
            response = conn.getresponse()
            body = response.read()
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(10)
            if undo is not None:
                undo()
    assert response.status == 200 and int(response.getheader("Content-Length")) == len(body), response.status
    return body


def rules(css, context=()):
    """Walk nested at-rules without treating keyframes as ordinary selectors."""
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    pos = 0
    while pos < len(css):
        start = css.find('{', pos)
        if start < 0:
            break
        selector = css[pos:start].strip()
        end, depth = start + 1, 1
        while depth:
            if end >= len(css):
                raise AssertionError('Unbalanced CSS')
            depth += (css[end] == '{') - (css[end] == '}')
            end += 1
        body = css[start + 1:end - 1]
        if selector.startswith('@'):
            yield from rules(body, context + (selector,))
        else:
            declarations = {}
            for declaration in body.split(';'):
                if ':' in declaration:
                    name, value = declaration.split(':', 1)
                    declarations[name.strip()] = value.strip()
            for name in selector.split(','):
                yield context, name.strip(), declarations
        pos = end


def transition_properties(value):
    # Commas inside timing functions do not separate transitions.
    value = re.sub(r'[\w-]+\([^)]*\)', 'TIMING', value)
    for item in value.split(','):
        tokens = [t for t in item.split() if not re.fullmatch(r'[.\d]+m?s', t)
                  and t not in ('ease', 'linear', 'ease-in', 'ease-out', 'ease-in-out',
                                'step-start', 'step-end', 'TIMING', '!important')]
        yield tokens[0] if tokens else 'all'


class Budget(unittest.TestCase):
    def test_motion_css(self):
        css = '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>', page(), re.S))
        effective, frames, animations = {}, {}, set()
        for context, selector, declarations in rules(css):
            self.assertNotIn('will-change', declarations, selector)
            if any(c.startswith('@keyframes') for c in context):
                frames.setdefault(context[-1].split()[-1], set()).update(declarations)
                continue
            # Shared source is immutable: audit the final declarations after the
            # later phone-a overrides, including each selector in grouped rules.
            effective.setdefault((context, selector), {}).update(declarations)
        count = 0
        for (context, selector), declarations in effective.items():
            for name in ('transition', 'transition-property'):
                if name in declarations:
                    count += 1
                    for prop in transition_properties(declarations[name]):
                        self.assertIn(prop, ('transform', 'opacity', 'visibility', 'none'),
                                      (context, selector, name, declarations[name]))
            for name in ('animation', 'animation-name', 'animation-iteration-count'):
                if name in declarations and declarations[name] != 'none':
                    # Nothing animates at idle: a looping animation is a defect. (The charging pulse that used to be the
                    # fixture here was a dead rule, retired by B-03 of the interface plan.)
                    self.assertNotIn('infinite', declarations[name], (context, selector, name))
                    if name != 'animation-iteration-count':
                        animations.update(item.strip().split()[0]
                                          for item in declarations[name].split(','))
        self.assertGreater(count, 0)
        self.assertNotIn('pulse', frames)
        for name in animations:  # whatever animation may be added later: declared frames, and only transform and opacity
            self.assertIn(name, frames)
        for name, props in frames.items():
            self.assertTrue(props <= {'transform', 'opacity'}, (name, props))

    def test_sheet_keeps_base_transition(self):
        # An override here once dropped the base `visibility 0s .25s` delay.
        css = (Path(__file__).parents[1] / 'src/opos/web_static/phone-a.css').read_text()
        for context, selector, declarations in rules(css):
            if selector.startswith('#sheet'):
                self.assertNotIn('transition', declarations, selector)
        rendered_css = '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>', page(), re.S))
        base = {}
        for context, selector, declarations in rules(rendered_css):
            if not context and selector == '#sheet':
                base.update(declarations)
        self.assertRegex(base.get('transition', ''), r'\bvisibility\s+0s\s+\.25s\b')

    def test_keeps_uxp24_layout_rules(self):
        # A whole-file copy of phone-a.css once reverted these UXP24 rules.
        css = (Path(__file__).parents[1] / 'src/opos/web_static/phone-a.css').read_text()
        heights = [int(m) for context, selector, declarations in rules(css)
                   if selector == 'summary' and not context
                   for m in re.findall(r'^(\d+)px$', declarations.get('min-height', ''))]
        self.assertTrue(heights and max(heights) >= 48, heights)
        self.assertTrue(any(selector == 'html:root[data-ff] #phone' and 'max-width:839px' in ''.join(context)
                            and declarations.get('max-width') == 'none'
                            for context, selector, declarations in rules(css)))

    def test_reduced_motion(self):
        html = page()
        compact = re.sub(r'\s+', '', html)
        self.assertIn('@media(prefers-reduced-motion:reduce){*,*::before,*::after{'
                      'transition-duration:.01ms!important;animation-duration:.01ms!important;'
                      'animation-iteration-count:1!important;scroll-behavior:auto!important}}', compact)

    def test_normal_served_demo_with_release_metadata_fits_page_budget(self):
        # The server generates a 43-character token and 22-character nonce.
        # A valid release build identifier is at most 12 characters.
        with mock.patch("opos.notices.BUILD", "f" * 12):
            html = page("t" * 43, "n" * 22, demo=True)
        self.assertLessEqual(len(html.encode("utf-8")), PAGE_BUDGET)

    def test_media_and_page_size(self):
        html = page()
        self.assertNotRegex(html.lower(), r'\.gif\b|<(?:video|canvas)\b')
        for data in re.findall(r'data:image[^\s\x22\x27<>)]*', html, re.I):
            self.assertLessEqual(len(data), 20000)
        size = len(html.encode('utf-8'))
        message = f'page(): {size:,} bytes; PAGE_BUDGET: {PAGE_BUDGET:,} bytes'
        print(message)
        self.assertLessEqual(size, PAGE_BUDGET, message)


class WaveGate(unittest.TestCase):
    def test_page_and_the_served_home_page_with_every_switch_fit_the_wave_gate(self):
        sizes = {
            "page()": len(page().encode("utf-8")),
            "page(), OPOS_SPAN=675,42": len(page(span=web.parse_span(SWITCHES["OPOS_SPAN"])).encode("utf-8")),
            "served, no switch": len(served_home_page({})),
            "served, every switch": len(served_home_page(SWITCHES)),
        }
        message = "; ".join(f"{name}: {size:,}" for name, size in sizes.items()) + f"; wave gate: {WAVE_GATE:,} bytes"
        print(message)
        for name, size in sizes.items():
            self.assertLessEqual(size, WAVE_GATE, f"{name} is over the wave gate ({message})")

    def test_the_measurement_includes_what_each_switch_adds(self):
        # A measurement that silently drops a switch would pass the gate for the wrong reason.
        plain = len(served_home_page({}))
        for switch, value in SWITCHES.items():
            if switch == "OPOS_SPAN":
                continue  # the squeeze makes the page smaller; it is in the every-switch figure
            with self.subTest(switch=switch):
                self.assertGreater(len(served_home_page({switch: value})), plain)


CHECK = r"""
CHECKS.budget = async function() {
  const out=[];
  for (const motion of ['no-preference','reduce']) {
    await send('Emulation.setEmulatedMedia', {features:[{name:'prefers-reduced-motion',value:motion}]});
    await load('home',390,844,false);
    // Read in the same evaluation so even a reduced-motion event cannot race it.
    if(!await ev("opos.open();document.getElementById('sheet').style.willChange==='transform'"))out.push(motion+' opening hint missing');
    if(!await ev("document.getElementById('sheet').dispatchEvent(new TransitionEvent('transitionend',{propertyName:'transform'}));document.getElementById('sheet').style.willChange===''"))out.push(motion+' transition cleanup missing');
    await sleep(450);
    // Suppress transitions to exercise the timer independently of transitionend.
    if(!await ev("opos.close();document.getElementById('sheet').style.transition='none';document.getElementById('sheet').style.willChange==='transform'"))out.push(motion+' closing hint missing');
    await sleep(450);
    if(!await ev("document.getElementById('sheet').style.willChange===''"))out.push(motion+' fallback cleanup missing');
    if(!await ev("opos.open();document.getElementById('sheet').style.willChange==='transform'"))out.push(motion+' reopening hint missing');
    await sleep(450);
    if(!await ev("document.getElementById('sheet').style.willChange===''"))out.push(motion+' opening cleanup missing');
  }
  await send('Emulation.setEmulatedMedia', {features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  await load('home',390,844,false);
  for (const open of [true,false]) {
    await ev(open ? 'opos.open()' : 'opos.close()');
    const durations=await ev("getComputedStyle(document.getElementById('sheet')).transitionDuration.split(',').map(parseFloat)");
    if(!durations.length||durations.some(d=>!Number.isFinite(d)||d>0.01))out.push('reduced motion duration '+durations);
  }
  await send('Emulation.setEmulatedMedia', {features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  await ev('opos.open()');
  await sleep(450);
  await ev('opos.close()');
  if(!await ev("getComputedStyle(document.getElementById('sheet')).visibility==='visible'"))out.push('closing slide hidden');
  await sleep(450);
  if(!await ev("getComputedStyle(document.getElementById('sheet')).visibility==='hidden'"))out.push('closed sheet retained');
  return out;
};
"""


class BudgetBrowser(unittest.TestCase):
    skip_reason = None
    mode = 'port'
    setUpClass = classmethod(acceptance.TestUxrAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(acceptance.TestUxrAcceptance.tearDownClass.__func__)
    setUp = acceptance.TestUxrAcceptance.setUp
    tearDown = acceptance.TestUxrAcceptance.tearDown

    def test_reduced_motion(self):
        driver = Path(self.run_dir) / 'budget.js'
        source = acceptance.DRIVER.read_text()
        self.assertIn('(async () => {', source)
        driver.write_text(source.replace('(async () => {', CHECK + '\n(async () => {', 1))
        target = str(self.port) if self.proc is not None else 'pipe'
        env = dict(os.environ, UXR_CHROME=self.chrome, UXR_PIDFILE=self.pidfile, TMPDIR=self.run_dir)
        result = subprocess.run([self.node, str(driver), target, self.url, 'budget', self.state],
                                capture_output=True, text=True, timeout=40, env=env)
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])['violations'], [])


if __name__ == '__main__':
    unittest.main()
