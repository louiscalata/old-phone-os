# SPDX-License-Identifier: GPL-3.0-or-later
"""The monitor page against OPOS's CSP and page rules, plus a Node smoke run of the script when Node exists."""
import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "opos" / "web_static"
MONITOR_BUDGET = 40 * 1024  # bytes for the assembled document


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def assemble():
    """The same @@OPOS:name@@ inlining page() uses; the route adds the token meta and the nonce."""
    return re.sub(r"@@OPOS:([A-Za-z0-9_.-]+)@@", lambda m: read(m.group(1)), read("monitor.html"))


class TemplateTests(unittest.TestCase):
    def test_bare_style_and_script_tags_with_the_placeholders(self):
        html = read("monitor.html")
        self.assertEqual(html.count("<style>"), 1)
        self.assertEqual(html.count("<script>"), 2)
        self.assertEqual(len(re.findall(r"<style\b", html)), 1)
        self.assertEqual(len(re.findall(r"<script\b", html)), 2)
        self.assertIn("<style>\n@@OPOS:monitor.css@@\n</style>", html)
        self.assertIn("<script>\n@@OPOS:monitor-theme.js@@\n</script>", html)
        self.assertIn("<script>\n@@OPOS:monitor.js@@\n</script>", html)
        self.assertEqual(sorted(re.findall(r"@@OPOS:([A-Za-z0-9_.-]+)@@", html)), ["monitor-theme.js", "monitor.css", "monitor.js"])
        # The theme script runs before the first paint, so it is in the head, before the styles.
        self.assertLess(html.index("monitor-theme.js"), html.index("monitor.css"))
        self.assertLess(html.index("monitor.css"), html.index("<body>"))

    def test_the_route_has_an_anchor_for_the_token_meta(self):
        # page()-style insertion replaces the title; this document's title is its own, and must stay that exact string.
        self.assertEqual(read("monitor.html").count("<title>Inference - OPOS</title>"), 1)
        self.assertNotIn("opos-token", read("monitor.html"), "the route adds the token meta; the template must not carry one")

    def test_nothing_the_csp_blocks(self):
        document = assemble()
        for pattern in (r"<script[^>]+src", r"<link\b", r"<iframe", r"<object", r"<embed", r"<img\b", r"<form\b", r"\son[a-z]+\s*=",
                        r"javascript:", r"@import", r"url\(", r"https?://", r"//[a-z0-9.-]+\.[a-z]{2,}/"):
            self.assertIsNone(re.search(pattern, document, re.I), pattern)

    def test_the_page_stays_inside_its_own_budget(self):
        size = len(assemble().encode("utf-8"))
        self.assertLess(size, MONITOR_BUDGET)
        # Room for the token meta, the nonces and the route's own additions.
        self.assertLess(size, MONITOR_BUDGET - 4096)

    def test_the_script_is_read_only_and_renders_with_textcontent(self):
        js = read("monitor.js")
        self.assertEqual(js.count("fetch("), 1)
        self.assertIn("'/api/monitor'", js)
        self.assertIn("method:'GET'", js)
        self.assertIn("X-OPOS-Token", js)
        self.assertIn("meta[name=opos-token]", js)
        self.assertIn("textContent", js)
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "import(",
                       "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "localStorage", "sessionStorage", "document.cookie",
                       "'POST'", '"POST"', "'PUT'", "'DELETE'", "onclick", "setAttribute('on", "location.href", "window.open"):
            self.assertNotIn(banned, js, banned)

    def test_the_theme_script_only_reads_two_known_keys_inside_a_try(self):
        js = read("monitor-theme.js")
        self.assertEqual(sorted(re.findall(r"getItem\('([a-z_]+)'\)", js)), ["jaryo_theme", "opos_text"])
        for banned in ("setItem", "removeItem", "innerHTML", "eval(", "fetch(", "document.cookie", "sessionStorage", "location", "src="):
            self.assertNotIn(banned, js, banned)
        self.assertTrue(js.lstrip().startswith("(function(){try{") and "catch(e){}" in js)
        # Only whitelisted values ever reach the document element.
        self.assertIn("t==='light'||t==='dark'", js)
        self.assertIn("['s','n','l','xl'].indexOf(x)>=0", js)

    def test_the_css_has_both_panes_both_themes_and_the_saved_preferences(self):
        css = read("monitor.css")
        self.assertIn("(horizontal-viewport-segments:2)", css)
        self.assertIn("prefers-color-scheme:dark", css)
        self.assertIn("prefers-reduced-motion:reduce", css)
        self.assertIn("--hinge:42px", css)
        self.assertIn("min-height:48px", css)
        # The gap keeps text clear of the hinge on both sides and absorbs a classic scrollbar.
        self.assertIn("column-gap:calc(var(--hinge) + 24px)", css)
        # An explicit Light or Dark choice beats the system theme, as in OPOS's own jaryo.css.
        self.assertIn('html:not([data-theme])', css)
        self.assertIn('html[data-theme="light"]', css)
        self.assertIn('html[data-theme="dark"]', css)
        for size, percent in (("s", "87.5%"), ("l", "125%"), ("xl", "150%")):
            self.assertIn(f'html[data-text="{size}"]{{font-size:{percent}}}', css)
        self.assertIn("html{font-size:100%}", css)
        self.assertNotRegex(css, r"font-size:\s*\d+px")
        self.assertNotRegex(css, r"font:\s*[^;}]*?\b\d+px")
        self.assertIn("position:sticky", css.split("#mon-status")[1].split("}")[0])
        self.assertIn("#mon-body.mon-stale{opacity:.7}", css)
        # Facts: the label column is at most half the pane and may wrap, so nothing scrolls sideways at 200% text.
        self.assertIn(".mon-facts{display:grid;grid-template-columns:fit-content(50%) minmax(0,1fr)", css)
        self.assertIn(".mon-facts dt{font-weight:700;overflow-wrap:anywhere}", css)
        self.assertNotIn("max-content", css)
        # The status line spans the page, so in two panes it is held to one column; long unbroken text wraps.
        segments = css.split("@media(horizontal-viewport-segments:2){")[1].split("\n}")[0]
        self.assertIn("#mon-status{max-width:calc((100% - var(--hinge) - 24px)/2)}", segments)
        for selector in (".mon-line,.mon-word,h2{overflow-wrap:anywhere}", ".mon-facts dd{margin:0;overflow-wrap:anywhere}"):
            self.assertIn(selector, css)
        self.assertIn("overflow-wrap:anywhere;position:sticky", css)
        live = css.split(".mon-live{")[1].split("}")[0]
        self.assertIn("clip-path:inset(50%)", live)

    def test_the_back_link_is_same_origin(self):
        self.assertIn('<a href="/">', read("monitor.html"))

    def test_the_age_is_not_in_a_live_region(self):
        html = read("monitor.html")
        self.assertIn('<p id="mon-status">', html)
        self.assertIn('<p id="mon-live" class="mon-live" role="status"></p>', html)
        self.assertEqual(html.count("role="), 1)
        self.assertNotIn("aria-live", html)


class SourceRuleTests(unittest.TestCase):
    def test_license_headers(self):
        for path in (ROOT / "src" / "opos" / "monitor.py", *sorted((ROOT / "tests").glob("test_monitor_*.py"))):
            self.assertTrue(path.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: GPL-3.0-or-later\n"), path.name)

    def test_the_repository_privacy_guard_would_find_nothing(self):
        # The patterns of guard/check.py (PRIVATE), built from parts so this file does not trip them itself.
        octet = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
        patterns = [re.compile(p) for p in (
            r"(?<![\d.])10\." + octet + r"\." + octet + r"\." + octet + r"(?![\d.])",
            r"(?<![\d.])172\.(?:1[6-9]|2[0-9]|3[01])\." + octet + r"\." + octet + r"(?![\d.])",
            r"(?<![\d.])192\.168\." + octet + r"\." + octet + r"(?![\d.])",
            "/" + "Users/[A-Za-z]", r"(?<![\w.-])/" + "home/[A-Za-z][A-Za-z0-9._-]",
            r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*[\"'][^\"']{8,}",
            r"[A-Za-z]:\\{1,2}" + "Users" + r"\\{1,2}", r"-----BEGIN [A-Z ]*PRIVATE" + " KEY",
            r"\b(sk|pk|ghp|gho|xox[bp])[-_][A-Za-z0-9_]{16,}", r"AKIA[0-9A-Z]{16}")]
        non_meta = re.compile(r"\b(gpt-?[0-9o]|claude|gemini|mistral|mixtral|qwen|deepseek|phi-[0-9]|gemma|command-r|grok|codex|openai|anthropic|chatgpt)", re.I)
        files = [ROOT / "src" / "opos" / "monitor.py", *sorted((ROOT / "src" / "opos" / "web_static").glob("monitor*")),
                 *sorted((ROOT / "tests").glob("test_monitor_*.py"))]
        self.assertGreaterEqual(len(files), 8)
        for path in files:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for pattern in patterns:
                    self.assertIsNone(pattern.search(line), f"{path.name}:{number}: {line.strip()[:80]}")
                if "src" in path.parts:
                    self.assertIsNone(non_meta.search(line), f"{path.name}:{number}: non-Meta model name under src/")
                    for url in re.findall(r"https?://[^\s'\"<>]+", line):
                        host = (urllib.parse.urlparse(url).hostname or "").lower()
                        self.assertIn(host, ("127.0.0.1", "localhost"), f"{path.name}:{number}: host {host}")

    def test_standard_library_only_and_no_other_opos_imports(self):
        tree = ast.parse((ROOT / "src" / "opos" / "monitor.py").read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                names.add((node.module or "").split(".")[0])
        self.assertTrue(names)
        self.assertLessEqual(names, set(sys.stdlib_module_names), names - set(sys.stdlib_module_names))

    def test_no_fstring_syntax_that_python_3_11_rejects(self):
        # PEP 701 (3.12): the quote of the f-string, or a backslash, inside a replacement field. ast.parse with
        # feature_version does not reject these, so look at each field's source text.
        for path in (ROOT / "src" / "opos" / "monitor.py", *sorted((ROOT / "tests").glob("test_monitor_*.py"))):
            source = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.JoinedStr):
                    continue
                whole = ast.get_source_segment(source, node) or ""
                quote = re.match(r"[rbfRBF]*(\"\"\"|\'\'\'|\"|\')", whole)
                quote = quote.group(1) if quote else '"'
                for part in node.values:
                    if isinstance(part, ast.FormattedValue):
                        field = ast.get_source_segment(source, part.value) or ""
                        self.assertNotIn(quote[0], field, f"{path.name}:{node.lineno}: quote reused inside an f-string field")
                        self.assertNotIn("\\", field, f"{path.name}:{node.lineno}: backslash inside an f-string field")
        # The detector sees what 3.11 would refuse.
        bad = 'row = {"a": 1}\nx = f"{row["a"]}"\n'
        node = next(n for n in ast.walk(ast.parse(bad)) if isinstance(n, ast.JoinedStr))
        field = ast.get_source_segment(bad, node.values[0].value)
        self.assertIn('"', field)

    def test_no_python_3_12_only_syntax(self):
        # The phone runs 3.14 but CI may run 3.11: no PEP 695 generics, no f-string nesting tricks.
        source = (ROOT / "src" / "opos" / "monitor.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"^\s*type\s+\w+\s*=", "PEP 695 alias")
        self.assertNotRegex(source, r"def \w+\[|class \w+\[", "PEP 695 generics")
        ast.parse(source, feature_version=(3, 11))


NODE = shutil.which("node")

HARNESS = r"""
const fs=require('fs'),vm=require('vm');
const src=fs.readFileSync(process.argv[2],'utf8');
const scenario=JSON.parse(process.argv[3]);
let created=0;
class N{constructor(t){this.tag=t;this.children=[];this.className='';this.t='';this.writes=0;created++;}
 appendChild(c){this.children.push(c);return c}
 removeChild(c){this.children=this.children.filter(x=>x!==c);return c}
 get firstChild(){return this.children[0]||null}
 set textContent(v){this.t=String(v);this.children=[];this.writes++}
 get textContent(){return this.t}}
const ids={'mon-body':new N('div'),'mon-status':new N('p'),'mon-live':new N('p')};
created=0;
const calls=[],intervals=[],cleared=[],listeners={};
const document={visibilityState:scenario.visibility||'visible',getElementById:i=>ids[i]||null,createElement:t=>new N(t),
 querySelector:q=>q==='meta[name=opos-token]'&&scenario.token!==null?{content:scenario.token}:null,
 addEventListener:(e,f)=>{listeners[e]=f}};
let step=0;
const ctx={document,console,performance:{now:()=>scenario.clock?scenario.clock[Math.min(step,scenario.clock.length-1)]:0},
 setInterval:(f,ms)=>{intervals.push({f,ms});return intervals.length},clearInterval:id=>{cleared.push(id)},
 setTimeout:()=>1,clearTimeout:()=>{},AbortController,
 fetch:(url,opt)=>{calls.push({url,opt:{method:opt.method,headers:opt.headers}});
  const reply=scenario.replies[Math.min(calls.length-1,scenario.replies.length-1)];
  if(reply.hang)return new Promise(()=>{});
  if(reply.reject)return Promise.reject(new Error('net'));
  return Promise.resolve({status:reply.status,ok:reply.status>=200&&reply.status<300,
    json:()=>reply.badjson?Promise.reject(new SyntaxError('x')):Promise.resolve(reply.doc)})}};
vm.createContext(ctx);vm.runInContext(src,ctx);
const settle=()=>new Promise(r=>setTimeout(r,15));
const dump=n=>({tag:n.tag,cls:n.className,t:n.t,w:n.writes,c:n.children.map(dump)});
(async()=>{
 await settle();
 const shots=[];
 const shot=()=>shots.push({status:ids['mon-status'].t,live:ids['mon-live'].t,liveWrites:ids['mon-live'].writes,bodyClass:ids['mon-body'].className,
   body:ids['mon-body'].children.map(dump),created});
 shot();
 for(let i=1;i<(scenario.polls||1);i++){step=i;if(intervals[0])intervals[0].f();await settle();shot()}
 let final=null;
 if(scenario.visibleAgain&&listeners.visibilitychange){if(scenario.visibleStep!==undefined)step=scenario.visibleStep;document.visibilityState='visible';listeners.visibilitychange();await settle();
   final={status:ids['mon-status'].t,live:ids['mon-live'].t,bodyClass:ids['mon-body'].className}}
 console.log(JSON.stringify({final,calls,intervals:intervals.map(x=>x.ms),cleared,listeners:Object.keys(listeners),shots,total:calls.length}));
})();
"""


def doc(*sections, ago=1):
    return {"updatedAgoSeconds": ago, "sections": list(sections)}


def sec(id="duo", title="This Duo", status="NO MODEL", glyph="[ ]", lines=("No model.",), facts=(("Battery", "54%"),), pre=None):
    return {"id": id, "title": title, "status": status, "glyph": glyph, "lines": list(lines), "facts": [list(f) for f in facts], "pre": pre}


OK = lambda d: {"status": 200, "doc": d}


@unittest.skipUnless(NODE, "node is not installed")
class ScriptSmokeTests(unittest.TestCase):
    def run_script(self, replies, **scenario):
        scenario.setdefault("token", "tok-123")
        scenario["replies"] = replies if isinstance(replies, list) else [replies]
        with tempfile.TemporaryDirectory() as scratch:
            harness = Path(scratch) / "harness.js"
            harness.write_text(HARNESS, encoding="utf-8")
            result = subprocess.run([NODE, str(harness), str(STATIC / "monitor.js"), json.dumps(scenario)],
                                    capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def flat(self, nodes):
        for node in nodes:
            yield node
            yield from self.flat(node["c"])

    def test_one_get_with_the_token_header_and_a_two_second_poll(self):
        out = self.run_script(OK(doc(sec())))
        call = out["calls"][0]
        self.assertEqual((call["url"], call["opt"]["method"]), ("/api/monitor", "GET"))
        self.assertEqual(call["opt"]["headers"], {"X-OPOS-Token": "tok-123"})
        self.assertEqual(out["intervals"], [2000])
        self.assertIn("visibilitychange", out["listeners"])

    def test_a_hostile_document_renders_only_as_text(self):
        evil = "<img src=x onerror=alert(1)>"
        out = self.run_script(OK({"updatedAgoSeconds": 3, "sections": [
            {"id": "duo", "title": evil, "status": evil, "glyph": evil * 5, "lines": [evil] * 30, "facts": [[evil, evil], "bad", None, [1, 2]] * 20, "pre": evil * 500},
            "not a section", None, 7, {}, {}, {}]}))
        shot = out["shots"][0]
        nodes = list(self.flat(shot["body"]))
        self.assertEqual({n["tag"] for n in nodes} - {"section", "h2", "p", "span", "dl", "dt", "dd", "pre"}, set())
        self.assertTrue(any(n["t"] == evil for n in nodes), "the text is shown as text")
        self.assertLessEqual(len(shot["body"]), 4)
        for section in shot["body"]:
            self.assertLessEqual(len([n for n in section["c"] if n["cls"] == "mon-line"]), 12)
            self.assertLessEqual(sum(1 for n in self.flat(section["c"]) if n["tag"] == "dt"), 40)
        self.assertTrue(all(len(n["t"]) <= 2000 for n in nodes))
        self.assertEqual(shot["status"], "Updated 3 s ago.")

    def test_a_good_document_shows_glyph_word_lines_and_facts(self):
        out = self.run_script(OK(doc(sec(), ago=0.4)))
        shot = out["shots"][0]
        texts = [n["t"] for n in self.flat(shot["body"]) if n["t"]]
        self.assertEqual(texts, ["This Duo", "[ ]", "NO MODEL", "No model.", "Battery", "54%"])
        self.assertEqual(shot["status"], "Updated 0 s ago.")
        self.assertEqual(shot["live"], "", "the age is never put in the live region")

    def test_a_line_is_cut_only_past_600_characters_and_the_cut_shows(self):
        longest = "x" * 600
        cut = "y" * 601
        out = self.run_script(OK(doc(sec(lines=(longest, cut)))))
        texts = [n["t"] for n in self.flat(out["shots"][0]["body"]) if n["cls"] == "mon-line"]
        self.assertEqual(texts[0], longest)
        self.assertEqual(texts[1], "y" * 599 + "\u2026")
        self.assertEqual(len(texts[1]), 600)

    def test_the_longest_line_the_module_can_emit_is_shown_whole(self):
        from opos import monitor
        many = [{"name": "m" * 48, "loaded": True} for _ in range(monitor.MAX_MODELS)]
        view = monitor.Monitor({"OPOS_AGIW_URL": "http://" + ".".join(["127", "0", "0", "1"]) + ":1"},
                               fetch=lambda _r: {"machines": [], "models": many}, probe=lambda _e: ("none", []))
        remote = next(s for s in view.snapshot()["sections"] if s["id"] == "remote")
        out = self.run_script(OK({"updatedAgoSeconds": 0, "sections": [remote]}))
        shown = [n["t"] for n in self.flat(out["shots"][0]["body"]) if n["cls"] == "mon-line"]
        self.assertEqual(shown, remote["lines"])
        self.assertTrue(all("\u2026" not in t for t in shown))

    def test_the_same_shape_is_updated_in_place_and_only_changed_text_is_written(self):
        first = doc(sec(), sec(id="remote", title="AGIW monitor", status="LIVE", glyph="[x]", lines=("fetched 0 s ago.", "same"),
                              facts=(("MAC", "UP"), ("MAC age", "1 s")), pre="MAC  UP\n |==="), ago=0)
        second = doc(sec(), sec(id="remote", title="AGIW monitor", status="LIVE", glyph="[x]", lines=("fetched 2 s ago.", "same"),
                               facts=(("MAC", "UP"), ("MAC age", "3 s")), pre="MAC  UP\n |==="), ago=2)
        out = self.run_script([OK(first), OK(second)], polls=2)
        before, after = out["shots"]
        self.assertEqual(before["created"], after["created"], "no node is created when the shape is the same")
        self.assertEqual(after["status"], "Updated 2 s ago.")
        by_text = lambda shot: {n["t"]: n["w"] for n in self.flat(shot["body"]) if n["t"]}
        after_writes = by_text(after)
        before_writes = by_text(before)
        for unchanged in ("This Duo", "AGIW monitor", "same", "MAC", "UP", "MAC  UP\n |==="):
            self.assertEqual(after_writes[unchanged], before_writes[unchanged], f"{unchanged!r} was rewritten")
        self.assertIn("fetched 2 s ago.", after_writes)
        self.assertIn("3 s", after_writes)

    def test_a_changed_shape_rebuilds_and_a_changed_pre_is_a_rebuild_of_that_shape(self):
        out = self.run_script([OK(doc(sec(), sec(id="remote", pre="a"))), OK(doc(sec(), sec(id="remote", pre=None))),
                               OK(doc(sec(lines=("a", "b")), sec(id="remote", pre=None)))], polls=3)
        created = [shot["created"] for shot in out["shots"]]
        self.assertGreater(created[1], created[0])
        self.assertGreater(created[2], created[1])
        self.assertEqual([len(shot["body"]) for shot in out["shots"]], [2, 2, 2])

    def test_a_failure_says_what_failed_and_keeps_the_last_answer_marked_stale(self):
        out = self.run_script([OK(doc(sec())), {"reject": True}, OK(doc(sec()))], polls=3, clock=[1000, 6000, 9000])
        good, bad, back = out["shots"]
        self.assertEqual(good["bodyClass"], "")
        self.assertEqual(bad["status"], "Could not reach OPOS. Last good answer 5 s ago.")
        self.assertEqual(bad["bodyClass"], "mon-stale")
        self.assertTrue(bad["body"], "the last answer stays, marked, instead of vanishing")
        self.assertEqual(bad["live"], "Could not reach OPOS.", "the change of state is announced once")
        self.assertEqual(back["bodyClass"], "")
        self.assertEqual(back["live"], "Connected again.")
        self.assertEqual(good["liveWrites"], 0)

    def test_the_terminal_out_of_date_state_is_announced_even_after_an_earlier_failure(self):
        out = self.run_script([OK(doc(sec())), {"reject": True}, {"status": 403, "doc": {}}], polls=3, clock=[0, 2000, 4000])
        first, second, third = out["shots"]
        self.assertEqual(second["live"], "Could not reach OPOS.")
        self.assertEqual(third["live"], "This page is out of date. Tap Back to the phone, then open Inference again.")
        self.assertEqual(out["cleared"], [1])

    def test_the_same_failure_is_not_announced_twice_but_a_different_one_is(self):
        out = self.run_script([{"reject": True}, {"reject": True}, {"status": 500, "doc": {}}, {"status": 500, "doc": {}}], polls=4,
                              clock=[0, 2000, 4000, 6000])
        self.assertEqual([s["liveWrites"] for s in out["shots"]], [1, 1, 2, 2])
        self.assertEqual(out["shots"][2]["live"], "OPOS answered with an error.")

    def test_the_live_region_is_written_once_per_change_of_state_not_per_poll(self):
        out = self.run_script([{"reject": True}] * 3 + [OK(doc(sec()))] * 3, polls=6, clock=[0, 2000, 4000, 6000, 8000, 10000])
        writes = [shot["liveWrites"] for shot in out["shots"]]
        self.assertEqual(writes, [1, 1, 1, 2, 2, 2])

    def test_failures_without_a_last_answer_have_no_age(self):
        out = self.run_script({"reject": True})
        self.assertEqual((out["shots"][0]["status"], out["shots"][0]["body"]), ("Could not reach OPOS.", []))
        self.assertEqual(out["shots"][0]["bodyClass"], "")

    def test_an_error_answer_is_not_reported_as_unreachable(self):
        for reply in ({"status": 500, "doc": {}}, {"status": 429, "doc": {}}, {"status": 200, "doc": {"sections": []}},
                      {"status": 200, "doc": None}, {"status": 200, "badjson": True, "doc": None}, {"status": 200, "doc": {"sections": [None, 5]}}):
            out = self.run_script(reply)
            self.assertEqual(out["shots"][0]["status"], "OPOS answered with an error.", reply)

    def test_a_refused_token_stops_the_polling_and_says_what_the_page_can_do(self):
        out = self.run_script(OK(doc(sec())) | {"status": 403}, polls=1)
        shot = out["shots"][0]
        self.assertEqual(shot["status"], "This page is out of date. Tap Back to the phone, then open Inference again.")
        self.assertNotIn("Reload", shot["status"])
        self.assertEqual(out["cleared"], [1], "the 2 s timer is cleared")

    def test_after_a_refused_token_becoming_visible_again_does_not_poll(self):
        out = self.run_script({"status": 403, "doc": {}}, visibleAgain=True)
        self.assertEqual(out["total"], 1)

    def test_a_visible_page_polls_again_when_it_returns_to_the_foreground(self):
        out = self.run_script(OK(doc(sec())), visibleAgain=True)
        self.assertEqual(out["total"], 2)

    def test_a_request_is_called_slow_only_after_ten_seconds_measured_from_its_own_start(self):
        # Poll 2 starts at t=2 s and never answers. A tick at 9 s (a 7 s old request) says nothing; one at 13 s (11 s old) does.
        out = self.run_script([OK(doc(sec())), {"hang": True}, {"hang": True}, {"hang": True}], polls=4, clock=[0, 2000, 9000, 13000])
        first, second, third, fourth = out["shots"]
        for shot in (second, third):
            self.assertEqual(shot["live"], "", "a slow request is not an announcement")
            self.assertEqual(shot["bodyClass"], "")
        self.assertEqual(fourth["live"], "OPOS is slow to answer.")
        self.assertEqual(fourth["status"], "OPOS is slow to answer. Last good answer 13 s ago.")
        self.assertEqual(fourth["bodyClass"], "mon-stale")

    def test_the_old_rule_measured_from_the_last_good_answer_would_have_cried_wolf(self):
        # The last good answer is 9 s old at the tick, but the request in flight is only 1 s old: not slow.
        out = self.run_script([OK(doc(sec())), {"hang": True}, {"hang": True}], polls=3, clock=[0, 8000, 9000])
        self.assertEqual(out["shots"][2]["live"], "")
        self.assertEqual(out["shots"][2]["bodyClass"], "")

    def test_an_old_answer_is_shown_as_old_even_when_the_request_replacing_it_is_young(self):
        """R3-1: the answer on screen is judged by its own age, not by the age of the request in flight."""
        out = self.run_script([OK(doc(sec())), {"hang": True}], polls=2, clock=[0, 20000])
        second = out["shots"][1]
        self.assertEqual(second["status"], "Last good answer 20 s ago.")
        self.assertEqual(second["bodyClass"], "mon-stale")
        self.assertEqual(second["live"], "", "marking an answer old is not an announcement")

    def test_a_young_answer_is_not_marked_old(self):
        out = self.run_script([OK(doc(sec())), {"hang": True}], polls=2, clock=[0, 14000])
        self.assertEqual(out["shots"][1]["bodyClass"], "")
        self.assertEqual(out["shots"][1]["status"], "Updated 1 s ago.")

    def test_coming_back_to_the_foreground_marks_an_old_answer_before_the_new_one_arrives(self):
        out = self.run_script([OK(doc(sec())), {"hang": True}], polls=1, visibleAgain=True, visibleStep=1, clock=[0, 60000])
        self.assertEqual(out["final"]["status"], "Last good answer 60 s ago.")
        self.assertEqual(out["final"]["bodyClass"], "mon-stale")

    def test_slow_is_not_announced_again_after_a_failure_with_no_good_answer_since(self):
        """R3-9: 'Could not reach OPOS.' was announced; a later slow request must not talk over it."""
        out = self.run_script([OK(doc(sec())), {"reject": True}, {"hang": True}, {"hang": True}], polls=4,
                              clock=[0, 2000, 4000, 20000])
        self.assertEqual(out["shots"][1]["live"], "Could not reach OPOS.")
        fourth = out["shots"][3]
        self.assertEqual(fourth["live"], "Could not reach OPOS.")
        self.assertTrue(fourth["status"].startswith("OPOS is slow to answer."))

    def test_the_failure_text_stays_in_front_of_the_age_when_the_answer_gets_old(self):
        """r4 regression: the 2 s timer rewrote the whole status line and wiped the failure about three polls in four."""
        out = self.run_script([OK(doc(sec())), {"reject": True}, {"hang": True}], polls=3, clock=[0, 2000, 20000])
        self.assertEqual(out["shots"][1]["status"], "Could not reach OPOS. Last good answer 2 s ago.")
        self.assertEqual(out["shots"][2]["status"], "Could not reach OPOS. Last good answer 20 s ago.")
        self.assertEqual(out["shots"][2]["bodyClass"], "mon-stale")

    def test_after_a_refused_token_no_counted_age_is_shown_because_nothing_will_refresh_it(self):
        """r5 low: 'Last good answer 2 s ago' must not sit frozen on a page that will never update."""
        out = self.run_script([OK(doc(sec())), {"status": 403, "doc": {}}, {"hang": True}], polls=3, clock=[0, 2000, 38000])
        message = "This page is out of date. Tap Back to the phone, then open Inference again."
        self.assertEqual(out["shots"][1]["status"], message)
        self.assertEqual(out["shots"][2]["status"], message, "the timer must not append an age later either")
        self.assertEqual(out["shots"][1]["bodyClass"], "mon-stale", "the old answer is still dimmed")

    def test_a_good_answer_clears_the_remembered_failure(self):
        out = self.run_script([OK(doc(sec())), {"reject": True}, OK(doc(sec())), {"hang": True}], polls=4, clock=[0, 2000, 4000, 40000])
        self.assertEqual(out["shots"][3]["status"], "Last good answer 36 s ago.", "the old failure text must not come back")

    def test_the_request_is_given_up_on_well_after_the_servers_own_deadlines(self):
        js = read("monitor.js")
        self.assertIn("var SLOW_MS=10000,GIVE_UP_MS=15000,STALE_MS=15000;", js)
        self.assertIn("setTimeout(function(){if(ctl)ctl.abort()},GIVE_UP_MS)", js)
        self.assertIn("now()-reqAt>SLOW_MS", js)
        from opos import monitor
        worst = monitor.LOCAL_TOTAL + monitor.TOTAL_DEADLINE
        self.assertGreater(10, worst, "SLOW_MS (10 s) must sit above the server's own worst case")

    def test_the_page_title_may_break_anywhere_so_it_cannot_overflow_at_200_percent(self):
        """R3-6"""
        import re
        css = read("monitor.css")
        rule = re.search(r"(?m)^h1\{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        self.assertIn("overflow-wrap:anywhere", rule.group(1))

    def test_a_missing_or_empty_token_sends_nothing_and_says_so(self):
        for token in (None, ""):
            out = self.run_script(OK(doc(sec())), token=token)
            self.assertEqual(out["total"], 0, repr(token))
            self.assertEqual(out["intervals"], [])
            self.assertEqual(out["shots"][0]["status"], "This page has no access token. Tap Back to the phone, then open Inference again.")

    def test_a_hidden_page_does_not_poll_on_the_timer(self):
        out = self.run_script(OK(doc(sec())), visibility="hidden", polls=3)
        self.assertEqual(out["total"], 1, "only the first look; the timer ticks do nothing while hidden")


if __name__ == "__main__":
    unittest.main()
