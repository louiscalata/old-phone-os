# SPDX-License-Identifier: GPL-3.0-or-later
"""The two small pages of the Duo OS first run: /firstrun (a few choices, all skippable) and /timezone.

Separate pages, not part of the main page (which is at its size budget). Touch only: every choice is a button, nothing
needs the keyboard. On the 1392 px wide two-panel screen the page keeps its text in the left panel and its controls in
the right one, so nothing sits on the 42 px hinge. Styled with the main page's Jaryo colours and type.
"""
from html import escape

from opos import secure

CSS = """
:root{--fg:#1a1a1a;--bg:#f3f0e8;--hinge:42px;color-scheme:light}
@media(prefers-color-scheme:dark){:root:not([data-theme]){--fg:#e6e2d9;--bg:#000;color-scheme:dark}}
:root[data-theme=light]{--fg:#1a1a1a;--bg:#f3f0e8;color-scheme:light}
:root[data-theme=dark]{--fg:#e6e2d9;--bg:#000;color-scheme:dark}
*{box-sizing:border-box}
[hidden]{display:none!important}
html,body{margin:0;background:var(--bg);color:var(--fg);font:1rem/1.45 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}
h1,h2{font-family:Georgia,'Iowan Old Style','Times New Roman',serif;line-height:1.15;margin:0 0 .5rem}
h1{font-size:2.25rem;border-bottom:3px solid var(--fg);padding-bottom:.4rem}
h2{font-size:1.5rem}
h2:focus{outline:none}
.kick{font:.875rem/1.3 'Arial Narrow','Roboto Condensed',sans-serif-condensed,sans-serif;text-transform:uppercase;letter-spacing:.04em;border-top:1px solid var(--fg);padding-top:.5rem;margin:0 0 .5rem}
.muted{font-size:.9rem}
.cols{max-width:1392px;margin:0 auto;padding:16px}
.note{border:3px double var(--fg);padding:8px 12px;margin:12px 0}
button,.btn{display:inline-block;min-height:48px;min-width:48px;margin:4px 8px 4px 0;padding:8px 16px;background:var(--bg);color:var(--fg);border:1px solid var(--fg);border-radius:0;font:inherit;text-decoration:none;text-align:center;cursor:pointer}
button:active,button[aria-pressed=true],.btn:active{background:var(--fg);color:var(--bg)}
button:disabled{border-style:dashed}
button.main{border-width:3px;font-weight:700}
:focus-visible{outline:3px solid var(--fg);outline-offset:3px}
input[type=search]{width:100%;min-height:48px;margin:8px 0;padding:8px;background:var(--bg);color:var(--fg);border:1px solid var(--fg);border-radius:0;font:inherit}
.chips{display:flex;flex-wrap:wrap}
.list{max-height:42vh;overflow-y:auto;border:1px solid var(--fg);margin-top:8px;touch-action:pan-y}
.list button{display:block;width:100%;margin:0;text-align:left;border:0;border-bottom:1px solid var(--fg)}
.big{font:700 2.25rem/1.1 Georgia,'Times New Roman',serif;margin:8px 0}
.nav{margin-top:16px;border-top:1px solid var(--fg);padding-top:8px}
ul.sum{padding-left:1.2rem}
@media(min-width:1000px){.cols{display:grid;grid-template-columns:minmax(0,1fr) var(--hinge) minmax(0,1fr)}.l{grid-column:1;padding-right:16px}.r{grid-column:3;padding-left:16px}}
::placeholder{color:var(--fg);opacity:1;font-style:italic}
button:disabled::after{content:" unavailable"}
.list{display:flex;flex-direction:column;gap:8px;border:0}
.list button{border:1px solid var(--fg)}
"""

_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="opos-token" content="@@TOKEN@@">
<title>@@TITLE@@</title>
<style>@@CSS@@</style>
<script>try{var t=localStorage.getItem('jaryo_theme');if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t)}catch(e){}</script>
</head>
<body>
"""

# Shared by both pages. Every string that comes from the server or the zone list is set with textContent.
_COMMON_JS = """
var T=document.querySelector('meta[name=opos-token]').content;
function $(i){return document.getElementById(i)}
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
function api(p,b){var o={headers:{'X-OPOS-Token':T}};if(b){o.method='POST';o.headers['Content-Type']='application/json';o.body=JSON.stringify(b)}
return fetch(p,o).then(function(r){return r.json().then(function(j){if(!r.ok)throw new Error(j&&j.error||String(r.status));return j},function(){throw new Error(String(r.status))})})}
function mytz(){try{return Intl.DateTimeFormat().resolvedOptions().timeZone||''}catch(e){return ''}}
function nice(z){return z.replace(/_/g,' ')}
function there(z){if(window.badClock)return '';try{return new Intl.DateTimeFormat([],{timeZone:z,weekday:'short',hour:'2-digit',minute:'2-digit'}).format(new Date())}catch(e){return ''}}
function tzPicker(box,zones,source,cur,onpick){
var R={},order=[],pick=cur||null;
zones.forEach(function(z){var i=z.indexOf('/'),r=i<0?z:z.slice(0,i);if(!R[r]){R[r]=[];order.push(r)}R[r].push(z)});
var sel=el('p','big'),note=el('p','note'),regs=el('div','chips'),q=el('input'),list=el('div','list');
note.hidden=source!=='builtin';
note.textContent='The phone\\'s time zone database was not found, so this is a short built-in list. Daylight saving rules in it are fixed and can go out of date.';
q.type='search';q.placeholder='Search (optional)';q.autocomplete='off';q.setAttribute('aria-label','Search time zones');
list.setAttribute('role','group');list.setAttribute('aria-label','Time zones');
function paint(){var t=pick?there(pick):'';sel.textContent=pick?nice(pick)+(t?' - '+t:''):'Nothing chosen yet'}
function mark(){[].forEach.call(list.querySelectorAll('button'),function(b){b.setAttribute('aria-pressed',b.dataset.z===pick?'true':'false')})}
function rows(items,full){list.textContent='';items.forEach(function(z){var b=el('button',null,full?nice(z):nice(z.slice(z.indexOf('/')+1)));b.type='button';b.dataset.z=z;
b.onclick=function(){pick=z;onpick(z);paint();mark()};list.appendChild(b)});if(!items.length)list.appendChild(el('p','muted','No match.'));mark()}
function region(r){q.value='';[].forEach.call(regs.children,function(b){b.setAttribute('aria-pressed',b.dataset.r===r?'true':'false')});rows(R[r],R[r].length===1&&R[r][0].indexOf('/')<0)}
order.forEach(function(r){var b=el('button',null,r);b.type='button';b.dataset.r=r;b.onclick=function(){region(r)};regs.appendChild(b)});
q.oninput=function(){var s=q.value.trim().toLowerCase();if(!s){region(pick&&R[pick.split('/')[0]]?pick.split('/')[0]:order[0]);return}
[].forEach.call(regs.children,function(b){b.setAttribute('aria-pressed','false')});
rows(zones.filter(function(z){return nice(z).toLowerCase().indexOf(s)>=0}).slice(0,60),true)};
box.textContent='';box.appendChild(sel);box.appendChild(note);box.appendChild(regs);box.appendChild(q);box.appendChild(list);
region(pick&&R[pick.split('/')[0]]?pick.split('/')[0]:order[0]);paint();
var sel0=list.querySelector('[aria-pressed=true]');if(sel0&&sel0.scrollIntoView)sel0.scrollIntoView({block:'nearest'});
setInterval(paint,20000);
return {get:function(){return pick}}}
"""

_FIRSTRUN_BODY = """<main class="cols">
<header class="l">
<p class="kick">First run</p>
<h1>Set up this phone</h1>
<p id="prog" role="status"></p>
<p id="clock" class="muted"></p>
<p id="clock-note" class="note" role="status" hidden></p>
<button type="button" id="skipall">Skip setup</button>
<p class="muted">Every choice can be skipped and changed later.</p>
</header>
<section class="r">
<p id="err" class="note" role="alert" hidden></p>
<div id="s-welcome"><h2>Welcome</h2>
<p>A few quick choices: your time zone, when the battery should last until, and whether to show the Helper. Nothing here is needed for calls and messages.</p></div>
<div id="s-tz" hidden><h2>Time zone</h2>
<p>Tap your region, then your city.</p><div id="tzbox"></div></div>
<div id="s-goal" hidden><h2>Daily goal</h2>
<p>The time you want the battery to last until. The phone pauses less important work to get there.</p>
<button type="button" id="goal-on" aria-pressed="true"></button>
<p class="big" id="goal-time"></p>
<div><button type="button" id="hm">Hour -</button><button type="button" id="hp">Hour +</button><button type="button" id="mm">15 min -</button><button type="button" id="mp">15 min +</button></div></div>
<div id="s-helper" hidden><h2>Helper</h2>
<p>The Helper answers questions about your battery and why something was paused. You can change this later in the Shade.</p>
<button type="button" id="help-on" aria-pressed="true">Show the Helper</button><button type="button" id="help-off" aria-pressed="false">Hide the Helper</button></div>
<div id="s-wifi" hidden><h2>Wi-Fi</h2>
<p id="wifi-none">Joining a network is not available on this screen yet. You can skip this; calls, messages and the battery rules do not need Wi-Fi.</p>
<p id="wifi-yes" hidden>Join a network now, or skip this and do it later.</p>
<a class="btn" id="wifi-link" href="/network" hidden>Open Wi-Fi setup</a></div>
<div id="s-done" hidden><h2>Ready</h2><ul class="sum" id="sum"></ul>
<p id="saving" role="status"></p></div>
<div class="nav"><button type="button" id="back">Back</button><button type="button" id="skip">Skip this step</button><button type="button" id="next" class="main" disabled>Start</button><a class="btn" id="home" href="/?firstrun=later" hidden>Open Home</a></div>
</section>
</main>
"""

_FIRSTRUN_JS = """
var steps=['welcome','tz','goal','helper','wifi','done'],cur=0,last=steps.length-1,S=null,Z=null,pk=null;
var A={zone:null,goal:null,helper:null},g={on:true,min:1380},help='on',busy=false;
var KEEP='opos_firstrun';
function keep(){try{sessionStorage.setItem(KEEP,JSON.stringify({t:Date.now(),cur:cur,A:A,g:g,help:help}))}catch(e){}}
function forget(){try{sessionStorage.removeItem(KEEP)}catch(e){}}
function recall(){try{var v=JSON.parse(sessionStorage.getItem(KEEP)||'null'),a,b;
if(!v||typeof v!=='object'||!(Date.now()-v.t<1800000)||!(v.cur>=1&&v.cur<=last))return 0;
a=v.A||{};if(typeof a.zone==='string'&&/^[A-Za-z0-9_+/-]{1,64}$/.test(a.zone))A.zone=a.zone;
b=a.goal;if(b&&typeof b.enabled==='boolean'&&/^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(b.time))A.goal={enabled:b.enabled,time:b.time};
if(a.helper==='on'||a.helper==='off')A.helper=a.helper;
b=v.g;if(b&&typeof b.on==='boolean'&&b.min>=0&&b.min<1440)g={on:b.on,min:Math.floor(b.min)};
if(v.help==='on'||v.help==='off')help=v.help;
return Math.floor(v.cur)}catch(e){return 0}}
function pad(n){return(n<10?'0':'')+n}
function hhmm(m){return pad(Math.floor(m/60))+':'+pad(m%60)}
function say(m){var e=$('err');e.hidden=!m;e.textContent=m||''}
function fmt(m){var h=Math.floor(m/60),ap=h<12?'AM':'PM';return(h%12||12)+':'+pad(m%60)+' '+ap}
function goalPaint(){$('goal-on').textContent=g.on?'Aim to last until a time: On':'Aim to last until a time: Off';$('goal-on').setAttribute('aria-pressed',g.on?'true':'false');
$('goal-time').textContent=fmt(g.min);['hm','hp','mm','mp'].forEach(function(i){$(i).disabled=!g.on})}
function helpPaint(){$('help-on').setAttribute('aria-pressed',help==='on'?'true':'false');$('help-off').setAttribute('aria-pressed',help==='off'?'true':'false')}
function summary(){var u=$('sum');u.textContent='';
function li(a,b){var e=el('li',null,a+': '+b);u.appendChild(e)}
li('Time zone',A.zone?nice(A.zone):'unchanged');
li('Daily goal',A.goal?(A.goal.enabled?fmt(g.min):'off'):'unchanged');
li('Helper',A.helper?(A.helper==='on'?'shown':'hidden'):'unchanged')}
function show(i){cur=i;steps.forEach(function(s,k){$('s-'+s).hidden=k!==i});
$('prog').textContent=i<last?'Step '+(i+1)+' of '+last:'Review';
$('back').hidden=i===0;$('skip').hidden=i===0||i===last;$('next').textContent=i===0?'Start':i===last?'Finish':'Next';
var h=$('s-'+steps[i]).querySelector('h2');if(h){h.tabIndex=-1;h.focus()}if(i===last)summary();say('');if(i>0)keep()}
function done(r){var e=r&&r.errors&&r.errors.length;
if(e){say('Some choices could not be saved ('+r.errors.join(', ')+'). You can change them later.');$('home').hidden=false;$('next').hidden=true;return}
forget();
if(r&&r.restart){$('saving').textContent='Saved. The screen restarts in a few seconds to use the new time zone.';$('next').hidden=true;$('back').hidden=true;
setTimeout(function(){location.href='/'},15000);return}
location.href='/'}
function fail(x){busy=false;$('next').disabled=false;say('Setup could not be saved ('+x.message+'). Try again, or open Home and do this later.');$('home').hidden=false}
function finish(){if(busy)return;busy=true;$('next').disabled=true;say('');
var b={action:'finish',client_tz:mytz()};if(A.zone)b.zone=A.zone;if(A.goal)b.goal=A.goal;if(A.helper)b.helper=A.helper;
api('/api/firstrun',b).then(function(r){busy=false;done(r)},fail)}
function go(d){if(cur===1&&d>0&&pk&&pk.get())A.zone=pk.get();
if(cur===2&&d>0)A.goal={enabled:g.on,time:hhmm(g.min)};
if(cur===3&&d>0)A.helper=help;
if(d>0&&cur===last)return finish();show(Math.max(0,Math.min(last,cur+d)))}
$('next').onclick=function(){go(1)};$('back').onclick=function(){go(-1)};
$('skip').onclick=function(){if(cur===1)A.zone=null;if(cur===2)A.goal=null;if(cur===3)A.helper=null;show(cur+1)};
$('skipall').onclick=function(){api('/api/firstrun',{action:'skip'}).then(function(){forget();location.href='/'},function(){location.href='/?firstrun=later'})};
$('home').onclick=function(e){e.preventDefault();var out=function(){location.href='/?firstrun=later'};api('/api/firstrun',{action:'later'}).then(out,out)};
$('goal-on').onclick=function(){g.on=!g.on;goalPaint()};
$('hm').onclick=function(){g.min=(g.min+1440-60)%1440;goalPaint()};$('hp').onclick=function(){g.min=(g.min+60)%1440;goalPaint()};
$('mm').onclick=function(){g.min=(g.min+1440-15)%1440;goalPaint()};$('mp').onclick=function(){g.min=(g.min+15)%1440;goalPaint()};
$('help-on').onclick=function(){help='on';helpPaint()};$('help-off').onclick=function(){help='off';helpPaint()};
fetch('/network',{method:'HEAD'}).then(function(r){if(r.ok){$('wifi-none').hidden=true;$('wifi-yes').hidden=false;$('wifi-link').hidden=false}},function(){});
Promise.all([api('/api/firstrun'),api('/api/timezone')]).then(function(v){S=v[0];Z=v[1];window.badClock=S.clock.plausible===false;
$('clock').textContent=S.clock.text?'Phone clock: '+S.clock.text:'';
if(S.clock.note){$('clock-note').textContent=S.clock.note;$('clock-note').hidden=false}
if(S.goal){g.on=!!S.goal.enabled;var p=(S.goal.time||'23:00').split(':');g.min=(+p[0])*60+(+p[1])}
help=S.helper==='off'?'off':'on';var back=recall();goalPaint();helpPaint();
pk=tzPicker($('tzbox'),Z.zones,Z.source,A.zone||Z.saved,function(){});show(back);$('next').disabled=false},
function(x){$('prog').textContent='';say('Setup could not load ('+x.message+'). Open Home and set things up later in Settings.');$('home').hidden=false;$('next').hidden=true;$('back').hidden=true;$('skip').hidden=true;$('s-welcome').hidden=false});
"""

_TIMEZONE_BODY = """<main class="cols">
<header class="l">
<p class="kick">Settings</p>
<h1>Time zone</h1>
<p id="cur" role="status"></p>
<p id="clock" class="muted"></p>
<p id="clock-note" class="note" role="status" hidden></p>
<a class="btn" href="/">Back to Home</a>
</header>
<section class="r">
<p id="msg" class="note" role="status" hidden></p>
<div id="tzbox"></div>
<div class="nav"><button type="button" id="save" class="main" disabled>Save time zone</button></div>
</section>
</main>
"""

_TIMEZONE_JS = """
var pk=null,saving=false;
function msg(m){var e=$('msg');e.hidden=!m;e.textContent=m||''}
$('save').onclick=function(){var z=pk&&pk.get();if(!z||saving)return;saving=true;$('save').disabled=true;msg('');
api('/api/timezone',{zone:z,client_tz:mytz()}).then(function(r){saving=false;$('cur').textContent='Now using: '+nice(r.zone);
msg(r.restart?'Saved. The screen restarts in a few seconds to use the new time zone.':'Saved.');$('save').disabled=false},
function(x){saving=false;$('save').disabled=false;msg('Could not save ('+x.message+').')})};
api('/api/timezone').then(function(Z){
window.badClock=!!Z.clock&&Z.clock.plausible===false;
$('cur').textContent=Z.saved?'Now using: '+nice(Z.saved):'No time zone chosen yet; the phone uses its default ('+(Z.active||'UTC')+').';
$('clock').textContent=Z.clock&&Z.clock.text?'Phone clock: '+Z.clock.text:'';
if(Z.clock&&Z.clock.note){$('clock-note').textContent=Z.clock.note;$('clock-note').hidden=false}
pk=tzPicker($('tzbox'),Z.zones,Z.source,Z.saved,function(){$('save').disabled=false});$('save').disabled=!Z.saved},
function(x){msg('The time zones could not load ('+x.message+').')});
"""


def _page(title, token, body, script, nonce):
    head = _HEAD.replace("@@TOKEN@@", escape(token, quote=True)).replace("@@TITLE@@", escape(title)).replace("@@CSS@@", CSS)
    return secure.add_nonce(head + body + "<script>" + _COMMON_JS + script + "</script>\n</body>\n</html>\n", nonce)


def firstrun_page(token, nonce):
    """The first-run page: welcome, time zone, daily goal, Helper, Wi-Fi, review. Needs /api/firstrun and /api/timezone."""
    return _page("OPOS setup", token, _FIRSTRUN_BODY, _FIRSTRUN_JS, nonce)


def timezone_page(token, nonce):
    """The time zone page reached from Settings. Needs /api/timezone."""
    return _page("OPOS time zone", token, _TIMEZONE_BODY, _TIMEZONE_JS, nonce)
