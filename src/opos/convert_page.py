# SPDX-License-Identifier: GPL-3.0-or-later
"""Converter. Length, mass, volume and temperature, on this phone. Nothing is stored."""
from opos import appdoc, apps, convert

_CSS = """
html,body,button,input{font-family:"Inter Tight",sans-serif}
h1,h2,.big{font-family:Newsreader,serif}
#amount{width:100%;min-height:48px;margin:8px 0;padding:8px;background:var(--bg);color:var(--fg);border:1px solid var(--fg);border-radius:0;font:inherit}
"""

_LIST = """
<h1>Converter</h1>
<p id="result" class="big">0</p>
<p id="line"></p>
<div id="kinds" class="chips"></div>
<p id="err" role="status"></p>
"""

_THUMB = """
<label>Amount <input id="amount" inputmode="decimal" value="1" autocomplete="off"></label>
<p>From</p>
<div id="from" class="chips"></div>
<p>To</p>
<div id="to" class="chips"></div>
"""

_SCRIPT = r"""
function G(i){return document.getElementById(i)}
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
var T=document.querySelector('meta[name=opos-token]').content, kinds=[], kind='length', src='', dst='';
var ERR={value:'Enter a number.',sign:'Length, mass and volume stay at zero or more.',cold:'That is below absolute zero.',unit:'Choose a unit.',kind:'Choose a measure.'};
function api(b){return fetch('/api/convert',{method:b?'POST':'GET',headers:Object.assign({'X-OPOS-Token':T},b?{'Content-Type':'application/json'}:{}),body:b?JSON.stringify(b):undefined}).then(function(r){return r.json().then(function(j){j._code=r.status;return j})})}
function current(){for(var i=0;i<kinds.length;i++)if(kinds[i].id===kind)return kinds[i];return kinds[0]}
function chips(box,units,picked,set){box.textContent='';units.forEach(function(u){var b=el('button',null,u.title);b.type='button';b.setAttribute('aria-pressed',u.id===picked?'true':'false');b.onclick=function(){set(u.id)};box.appendChild(b)})}
function paint(){var spec=current();chips(G('kinds'),kinds.map(function(k){return {id:k.id,title:k.title}}),kind,function(id){var next=kinds.filter(function(k){return k.id===id})[0];kind=id;src=next.src;dst=next.dst;paint()});chips(G('from'),spec.units,src,function(id){src=id;paint()});chips(G('to'),spec.units,dst,function(id){dst=id;paint()});run()}
function run(){api({kind:kind,value:G('amount').value,src:src,dst:dst}).then(function(s){if(!s||s._code!==200){G('err').textContent=ERR[s&&s.error]||'That conversion is not available.';G('line').textContent='';return}G('err').textContent='';G('result').textContent=s.text;G('line').textContent=s.line})}
G('amount').oninput=function(){run()};
api().then(function(s){kinds=s.kinds||[];if(!kinds.length)return;kind=kinds[0].id;src=kinds[0].src;dst=kinds[0].dst;paint()});
"""


def render(token, nonce):
    return appdoc.render("Converter", token, nonce, _LIST, _THUMB, _SCRIPT, _CSS)


def post(data):
    if not isinstance(data, dict):
        return 400, {"error": "bad request"}
    try:
        return 200, convert.convert(data.get("kind"), data.get("value"), data.get("src"), data.get("dst"))
    except ValueError as exc:
        code = str(exc)
        if code not in {"kind", "value", "unit", "sign", "cold"}:
            code = "bad request"
        return 400, {"error": code}


apps.register(apps.App(
    "convert", "Converter", "/convert",
    "Length, mass, volume and temperature.",
    "yes", "no", "none", "real", "converter", "USEFUL", 0,
    render, convert.snapshot, post,
))
