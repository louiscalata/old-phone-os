# SPDX-License-Identifier: GPL-3.0-or-later
"""Recents. A list of calls the dialer wrote. This page does not place a call."""
from opos import appdoc, apps, calllog

_CSS = """
html,body,button,input{font-family:"Inter Tight",sans-serif}
h1,h2,.big{font-family:Newsreader,serif}
"""

_LIST = """
<h1>Recents</h1>
<p class="note">Practice calls are marked. This list does not place a call.</p>
<p id="recovered" class="note" hidden></p>
<p id="empty">No calls yet.</p>
<ul id="rows"></ul>
"""

_THUMB = """
<h2 id="who">Call</h2>
<p id="meta"></p>
<p id="num"></p>
<button id="again" type="button" hidden>Call</button>
<p id="again-line" role="status"></p>
"""

_SCRIPT = r"""
function G(i){return document.getElementById(i)}
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
var T=document.querySelector('meta[name=opos-token]').content;
function line(row){return row.way+' · '+row.title+' · '+row.length+(row.practice?' · Practice':'')}
function select(row){G('who').textContent=row.title;G('meta').textContent=line(row);G('num').textContent=row.number;G('again').hidden=false;G('again-line').textContent=''}
function paint(s){if(!s||s.error)return;var rec=G('recovered');rec.hidden=!s.recovered;rec.textContent=s.recovered||'';
var calls=s.calls||[];G('empty').hidden=calls.length>0;var ul=G('rows');ul.textContent='';
calls.forEach(function(row,i){var li=document.createElement('li'),b=el('button',null,line(row));b.type='button';b.onclick=function(){select(row)};li.appendChild(b);ul.appendChild(li);if(i===0)select(row)});
if(!calls.length){G('who').textContent='Call';G('meta').textContent='';G('num').textContent='Select a call.';G('again').hidden=true}}
G('again').onclick=function(){G('again-line').textContent='No call was placed. Use Phone to dial.'};
fetch('/api/calls',{headers:{'X-OPOS-Token':T}}).then(function(r){return r.json()}).then(paint).catch(function(){});
"""


def render(token, nonce):
    return appdoc.render("Recents", token, nonce, _LIST, _THUMB, _SCRIPT, _CSS)


apps.register(apps.App(
    "calls", "Recents", "/calls",
    "A list of calls placed on this phone.",
    "yes", "no", "none", "practice", "calls", "ESSENTIAL", 0,
    render, calllog.snapshot, None,
))
