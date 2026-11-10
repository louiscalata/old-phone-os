# SPDX-License-Identifier: GPL-3.0-or-later
"""The notice list and quiet hours. Alarms always ring."""
from opos import appdoc, apps, inbox

_LIST = """<h1>Notices</h1><ul id="rows"></ul><button id="clear" type="button">Clear all</button>"""
_THUMB = """
<h2>Quiet hours</h2>
<p>Alarms always ring.</p>
<p>Calls from Favorites ring.</p>
<label>From <input id="qfrom" type="time" value="22:00"></label>
<label>To <input id="qto" type="time" value="07:00"></label>
<button id="qon" type="button">Quiet hours: Off</button>
<p id="msg" role="status"></p>
"""
_SCRIPT = r"""
function G(i){return document.getElementById(i)}
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
var T=document.querySelector('meta[name=opos-token]').content;
function api(b){var o={headers:{'X-OPOS-Token':T}};if(b){o.method='POST';o.headers['Content-Type']='application/json';o.body=JSON.stringify(b)}return fetch('/api/notices',o).then(function(r){return r.json()})}
function paint(s){var rows=G('rows');rows.textContent='';(s.items||[]).forEach(function(item){var li=document.createElement('li');li.appendChild(el('span',null,item.kind+' · '+item.text));var b=el('button',null,'Dismiss');b.type='button';b.onclick=function(){api({action:'dismiss',id:item.id}).then(paint)};li.appendChild(b);rows.appendChild(li)});var q=s.quiet||{};G('qon').textContent='Quiet hours: '+(q.on?'On':'Off');G('qon').setAttribute('aria-pressed',q.on?'true':'false')}
G('clear').onclick=function(){api({action:'clear'}).then(paint)};
G('qon').onclick=function(){var on=G('qon').getAttribute('aria-pressed')!=='true';api({action:'quiet',on:on,from:G('qfrom').value||'22:00',to:G('qto').value||'07:00'}).then(paint)};
api().then(paint);
window.oposNotices={refresh:function(){return api().then(paint)}};
"""


def render(token, nonce):
    return appdoc.render(
        "Notices", token, nonce, _LIST, _THUMB, _SCRIPT,
        "#clear,#qon{min-height:48px;display:block;width:100%}",
    )


def post(data):
    action = data.get("action") if isinstance(data, dict) else None
    try:
        if action == "add":
            inbox.add(data.get("kind"), data.get("text") or "")
        elif action == "dismiss":
            inbox.dismiss(data.get("id"))
        elif action == "clear":
            inbox.clear()
        elif action == "quiet":
            inbox.set_quiet(data.get("on") is True, data.get("from") or "22:00", data.get("to") or "07:00")
        else:
            return 400, {"error": "bad request"}
    except ValueError as exc:
        return 400, {"error": str(exc) or "bad request"}
    return 200, inbox.snapshot()


apps.register(apps.App(
    "notices", "Notices", "/notices",
    "A list of notices and quiet hours. Alarms still ring.",
    "yes", "no", "speaker", "real", "notices", "USEFUL", 0,
    render, inbox.snapshot, post,
))
