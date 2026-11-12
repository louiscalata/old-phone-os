# SPDX-License-Identifier: GPL-3.0-or-later
"""Call log page and read/clear API. Call events enter only at the modem."""
from opos import appdoc, apps, calllog

_CSS = """
#find{width:100%;min-height:48px;margin:8px 0;padding:8px;background:var(--bg);color:var(--fg);border:1px solid var(--fg);border-radius:0;font:inherit}
#rows .day{border-top:1px solid var(--fg);padding-top:8px}
"""
_LIST = """
<h1>Call log</h1>
<label>Search calls <input id="find" type="search" autocomplete="off" maxlength="80"></label>
<p id="recovered" class="note" role="status"></p>
<p id="empty">No calls recorded.</p>
<ul id="rows" aria-label="Calls, newest first"></ul>
"""
_THUMB = """
<h2>About this log</h2>
<p>Works offline. Stored on this phone. No data is sent by this log.</p>
<p>Practice means a simulated modem. No real call was made. Real means a real modem event.</p>
<p>Tap a call to open its number in Phone, then use Call. Duration unavailable means the call has not ended or its duration was not recorded.</p>
<p id="missed"></p>
<p id="status" role="status"></p>
<button id="refresh" type="button">Refresh</button>
<button id="clear" type="button">Clear all calls</button>
<div id="confirm" hidden>
<p>Clear all recorded calls from this phone?</p>
<button id="clear-yes" type="button">Confirm clear all calls</button>
<button id="clear-no" type="button">Cancel</button>
</div>
"""
_SCRIPT = r"""
function G(i){return document.getElementById(i)}
function el(tag,text){var e=document.createElement(tag);if(text!=null)e.textContent=text;return e}
var T=document.querySelector('meta[name=opos-token]').content, last={calls:[]}, revision=0, busy=false;
function api(body){return fetch('/api/calls',{method:body?'POST':'GET',headers:Object.assign({'X-OPOS-Token':T},body?{'Content-Type':'application/json'}:{}),body:body?JSON.stringify(body):undefined}).then(function(r){return r.json().then(function(s){if(!r.ok||!s||s.error)throw new Error('The call log could not be updated.');return s})})}
function failed(){G('status').textContent='The call log could not be updated.'}
function duration(row){if(row.duration==null)return 'Duration unavailable';var n=Math.floor(row.duration);return Math.floor(n/60)+' min '+(n%60)+' sec'}
function dayOf(date){return date.getFullYear()+'-'+String(date.getMonth()+1).padStart(2,'0')+'-'+String(date.getDate()).padStart(2,'0')}
function paint(s){
last=s;G('recovered').textContent=s.recovered||'';G('missed').textContent='Missed calls in this log: '+(s.missed_count||0);
var q=G('find').value.toLowerCase(), rows=G('rows'), previous='', count=0;rows.textContent='';
(s.calls||[]).forEach(function(row){
if(q&&(row.name+' '+row.number).toLowerCase().indexOf(q)<0)return;
var date=new Date(row.start_time),day=dayOf(date);
if(day!==previous){previous=day;var heading=el('li');heading.className='day';heading.appendChild(el('h2',day));rows.appendChild(heading)}
var text=(row.name?row.name+' · ':'')+row.number+' · '+row.direction.charAt(0).toUpperCase()+row.direction.slice(1)+' · '+date.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})+' · '+duration(row)+' · '+(row.practice?'Practice':'Real');
var li=el('li'),a=el('a',text);a.setAttribute('href','/?practice=phone&n='+encodeURIComponent(row.number));a.setAttribute('aria-label','Call back: '+text);li.appendChild(a);rows.appendChild(li);count++;
});
G('empty').hidden=count>0;G('empty').textContent=q?'No matching calls.':'No calls recorded.';
G('clear').disabled=busy||!(s.calls||[]).length;
}
function load(){if(busy)return Promise.resolve();var rev=++revision;return api().then(function(s){if(rev===revision){paint(s);G('status').textContent=''}}).catch(function(){if(rev===revision)failed()})}
function setBusy(on){busy=on;['clear','clear-yes','clear-no','refresh'].forEach(function(id){G(id).disabled=on});if(!on)G('clear').disabled=!(last.calls||[]).length}
G('find').oninput=function(){paint(last)};
G('refresh').onclick=load;
G('clear').onclick=function(){G('confirm').hidden=false;G('clear-yes').focus()};
G('clear-no').onclick=function(){G('confirm').hidden=true;G('clear').focus()};
G('clear-yes').onclick=function(){
if(busy)return;setBusy(true);++revision;
api({action:'clear',confirm:true}).then(function(s){paint(s);G('confirm').hidden=true;G('status').textContent='Call log cleared.';G('refresh').focus()}).catch(failed).then(function(){setBusy(false)});
};
load();
"""


def post(data):
    if not isinstance(data, dict) or data != {"action": "clear", "confirm": True} or data.get("confirm") is not True:
        return 400, {"error": "confirmation required"}
    return 200, calllog.clear()


def render(token, nonce):
    return appdoc.render("Call log", token, nonce, _LIST, _THUMB, _SCRIPT, _CSS)


apps.register(apps.App(
    "calls", "Call log", "/calls", "Recent calls kept on this phone.",
    "yes", "no", "none", "each call is labelled", "call-log", "ESSENTIAL", 0,
    render, calllog.snapshot, post,
))
