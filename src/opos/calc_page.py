# SPDX-License-Identifier: GPL-3.0-or-later
"""Calculator. The arithmetic runs in the page. Nothing is stored."""
from pathlib import Path

from opos import appdoc, apps

_JS = (Path(__file__).with_name("apps_static") / "calc.js").read_text(encoding="utf-8")
_CSS = """
#tape{list-style:none;margin:0;padding:0}
#tape li{min-height:48px;border-bottom:1px solid var(--fg);padding:8px 0}
#keys{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
#keys button{min-height:64px;min-width:48px;margin:0}
#result{font:700 2rem/1.2 Georgia,'Times New Roman',serif;margin:8px 0}
"""
_LIST = """<h1>Calculator</h1><p id="result">0</p><ul id="tape"></ul>"""
_THUMB = """
<div id="keys">
<button type="button" data-k="7">7</button><button type="button" data-k="8">8</button><button type="button" data-k="9">9</button><button type="button" data-k="/">÷</button>
<button type="button" data-k="4">4</button><button type="button" data-k="5">5</button><button type="button" data-k="6">6</button><button type="button" data-k="*">×</button>
<button type="button" data-k="1">1</button><button type="button" data-k="2">2</button><button type="button" data-k="3">3</button><button type="button" data-k="-">−</button>
<button type="button" data-k="0">0</button><button type="button" data-k=".">.</button><button type="button" data-k="(">(</button><button type="button" data-k=")">)</button>
<button type="button" id="clear">Clear</button><button type="button" id="copy">Copy</button><button type="button" id="eq" class="main" data-k="=">=</button><button type="button" data-k="+">+</button>
</div>
<p id="copy-line" role="status"></p>
"""
_SCRIPT = _JS + r"""
var expr='', tape=[];
function G(i){return document.getElementById(i)}
function show(text){G('result').textContent=text;}
function draw(){var ul=G('tape');ul.textContent='';tape.forEach(function(line){var li=document.createElement('li');li.textContent=line;ul.appendChild(li)});}
G('keys').onclick=function(e){
var b=e.target.closest&&e.target.closest('button');if(!b)return;
if(b.id==='clear'){expr='';show('0');G('copy-line').textContent='';return;}
if(b.id==='copy'){var t=G('result').textContent;try{navigator.clipboard.writeText(t).then(function(){G('copy-line').textContent='Copied'},function(){G('copy-line').textContent=t})}catch(err){G('copy-line').textContent=t}return;}
var k=b.getAttribute('data-k');if(!k)return;
if(k==='='){try{var value=oposCalc.evaluate(expr);tape.unshift(expr+' = '+value);if(tape.length>20)tape.pop();expr=value;show(value);draw();}catch(err){show(err.message||'bad expression');}return;}
expr+=k;show(expr);
};
show('0');
"""


def render(token, nonce):
    return appdoc.render("Calculator", token, nonce, _LIST, _THUMB, _SCRIPT, _CSS)


def snapshot():
    return {"ok": True}


apps.register(apps.App(
    "calc", "Calculator", "/calc",
    "Add, subtract, multiply and divide on this phone.",
    "yes", "no", "none", "real", "calculator", "USEFUL", 0,
    render, snapshot, None,
))
