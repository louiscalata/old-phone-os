# SPDX-License-Identifier: GPL-3.0-or-later
"""The Clock page: alarms, a timer, a stopwatch and a bedside clock.

The schedule is opos.alarms. This module is the page and the /api/clock actions.
A test may POST action "tick" only when OPOS_CLOCK_TEST=1.
"""
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from opos import alarms, appdoc, apps
from opos.store import default_root

_BOOK = None
_FAKE = None
_THREAD = None
_CODES = {"hour", "minute", "days", "label", "full", "id", "seconds", "now", "enabled"}

_CSS = """
#pivots button,#days button{min-height:48px}
#readout,#night-time,#timer-read,#w-read{font:700 2.25rem/1.1 Georgia,'Times New Roman',serif;margin:8px 0}
#stop,#snooze{min-height:72px;display:block;width:100%;margin:12px 0}
input[type=text]{width:100%;min-height:48px;margin:8px 0;padding:8px;background:var(--bg);color:var(--fg);border:1px solid var(--fg);border-radius:0;font:inherit}
html[data-bed=1] #editor,html[data-bed=1] #pivots,html[data-bed=1] #rows,html[data-bed=1] #timer-box,html[data-bed=1] #watch-box{display:none!important}
"""

_LIST = """
<h1>Clock</h1>
<div id="pivots" class="chips">
<button id="p-alarms" type="button" aria-pressed="true">Alarms</button>
<button id="p-timer" type="button" aria-pressed="false">Timer</button>
<button id="p-watch" type="button" aria-pressed="false">Stopwatch</button>
</div>
<p id="warn" class="note" hidden>Clock not set: alarms will not ring until the time is known</p>
<p id="recovered" class="note" hidden></p>
<ul id="rows"></ul>
<div id="night" hidden><p id="night-time" class="big">--:--</p><p id="night-next"></p></div>
"""

_THUMB = """
<div id="editor">
<p id="readout" class="big">07:00</p>
<div>
<button id="h-minus" type="button">− hour</button>
<button id="h-plus" type="button">+ hour</button>
<button id="m-minus" type="button">− 5 min</button>
<button id="m-plus" type="button">+ 5 min</button>
</div>
<label>Time, 24-hour <input id="clock-time" type="text" inputmode="numeric" value="07:00" maxlength="5" autocomplete="off" spellcheck="false"></label>
<div id="days"></div>
<label>Label <input id="clock-label" type="text" maxlength="40" autocomplete="off"></label>
<button id="save" class="main" type="button">Save</button>
<button id="fresh" type="button">New alarm</button>
<button id="del" type="button" hidden>Delete</button>
</div>
<div id="timer-box" hidden>
<p id="timer-read" class="big">00:10:00</p>
<button id="t-minus" type="button">− 1 min</button>
<button id="t-plus" type="button">+ 1 min</button>
<button id="t-start" class="main" type="button">Start</button>
<button id="t-cancel" type="button">Cancel</button>
<p id="timer-state" role="status"></p>
</div>
<div id="watch-box" hidden>
<p id="w-read" class="big">0:00.0</p>
<button id="w-go" type="button">Start</button>
<button id="w-reset" type="button">Reset</button>
</div>
<div id="ring" hidden>
<p id="ring-time" class="big"></p>
<p id="ring-label"></p>
<button id="stop" class="main" type="button">Stop</button>
<button id="snooze" type="button">Snooze 10 min</button>
</div>
<button id="bedside" type="button">Bedside</button>
"""

_SCRIPT = r"""
function G(i){return document.getElementById(i)}
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
var T=document.querySelector('meta[name=opos-token]').content;
var DAYS=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'],hour=7,minute=0,on={},sel=null,panel='alarms',tmin=10,wAcc=0,wRun=false,wT0=0,wTimer=null;
function pad(n){return (n<10?'0':'')+n}
function show(){G('readout').textContent=pad(hour)+':'+pad(minute);var t=G('clock-time');if(t)t.value=pad(hour)+':'+pad(minute)}
function dayButtons(){var box=G('days');box.textContent='';DAYS.forEach(function(name,i){var b=el('button',null,name+': '+(on[i]?'On':'Off'));b.type='button';b.setAttribute('aria-pressed',on[i]?'true':'false');b.onclick=function(){on[i]=!on[i];dayButtons()};box.appendChild(b)})}
function api(b){return fetch('/api/clock',{method:'POST',headers:{'Content-Type':'application/json','X-OPOS-Token':T},body:JSON.stringify(b)}).then(function(r){return r.json()})}
function paint(s){if(!s||s.error)return;G('warn').hidden=!!s.clock_ok;var rec=G('recovered');rec.hidden=!s.recovered;rec.textContent=s.recovered||'';
var rows=G('rows');rows.textContent='';(s.alarms||[]).forEach(function(a){var li=document.createElement('li'),b=el('button',null,pad(a.hour)+':'+pad(a.minute)+(a.label?' '+a.label:'')+' · '+(a.enabled?'On':'Off'));b.type='button';b.onclick=function(){sel=a.id;hour=a.hour;minute=a.minute;on={};(a.days||[]).forEach(function(d){on[d]=true});G('clock-label').value=a.label||'';G('del').hidden=false;G('del').dataset.sure='';G('del').textContent='Delete';show();dayButtons()};li.appendChild(b);rows.appendChild(li)});
var ring=s.ringing;G('ring').hidden=!ring;if(ring){G('ring-time').textContent=ring.text||'';G('ring-label').textContent=ring.label||''}
var next=s.next&&s.clock_ok?s.next.text:'';G('night-next').textContent=next?('Next '+next):'No alarm set';
var now=s.now?new Date(s.now):new Date();if(!s.clock_ok)G('night-time').textContent='--:--';else G('night-time').textContent=pad(now.getHours())+':'+pad(now.getMinutes());
if(s.timer&&s.timer.fires_at){var left=Math.max(0,Math.round((new Date(s.timer.fires_at)-now)/1000));G('timer-state').textContent='Timer: On';G('timer-read').textContent=pad(Math.floor(left/3600))+':'+pad(Math.floor(left/60)%60)+':'+pad(left%60)}else G('timer-state').textContent=''}
function load(){return fetch('/api/clock',{headers:{'X-OPOS-Token':T}}).then(function(r){return r.json()}).then(paint).catch(function(){})}
function chosen(){var d=[];DAYS.forEach(function(_,i){if(on[i])d.push(i)});return d}
G('h-minus').onclick=function(){hour=(hour+23)%24;show()};
G('h-plus').onclick=function(){hour=(hour+1)%24;show()};
G('m-minus').onclick=function(){minute=(minute+55)%60;show()};
G('m-plus').onclick=function(){minute=(minute+5)%60;show()};
G('clock-time').onchange=function(){var v=G('clock-time').value||'';if(!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(v)){show();return}var p=v.split(':');hour=+p[0];minute=+p[1];show()};
G('save').onclick=function(){var body={action:sel?'edit':'add',hour:hour,minute:minute,days:chosen(),label:G('clock-label').value};if(sel)body.id=sel;api(body).then(function(s){sel=null;G('del').hidden=true;paint(s)})};
G('fresh').onclick=function(){sel=null;hour=7;minute=0;on={};G('clock-label').value='';G('del').hidden=true;show();dayButtons()};
G('del').onclick=function(){if(!sel)return;if(G('del').dataset.sure!=='1'){G('del').dataset.sure='1';G('del').textContent='Delete this alarm?';return}api({action:'delete',id:sel}).then(function(s){sel=null;G('del').hidden=true;paint(s)})};
function pivot(name){panel=name;G('editor').hidden=name!=='alarms';G('timer-box').hidden=name!=='timer';G('watch-box').hidden=name!=='watch';['alarms','timer','watch'].forEach(function(n){G('p-'+n).setAttribute('aria-pressed',n===name?'true':'false')})}
G('p-alarms').onclick=function(){pivot('alarms')};G('p-timer').onclick=function(){pivot('timer')};G('p-watch').onclick=function(){pivot('watch')};
function tshow(){G('timer-read').textContent=pad(Math.floor(tmin/60))+':'+pad(tmin%60)+':00'}
G('t-minus').onclick=function(){tmin=Math.max(1,tmin-1);tshow()};G('t-plus').onclick=function(){tmin=Math.min(1440,tmin+1);tshow()};
G('t-start').onclick=function(){api({action:'timer_start',seconds:tmin*60}).then(paint)};
G('t-cancel').onclick=function(){api({action:'timer_cancel'}).then(paint)};
function wdraw(){var ms=wAcc+(wRun?Date.now()-wT0:0);G('w-read').textContent=Math.floor(ms/60000)+':'+pad(Math.floor(ms/1000)%60)+'.'+Math.floor(ms/100)%10}
G('w-go').onclick=function(){wRun=!wRun;G('w-go').textContent=wRun?'Stop':'Start';if(wRun){wT0=Date.now();wTimer=setInterval(wdraw,100)}else{wAcc+=Date.now()-wT0;clearInterval(wTimer);wdraw()}};
G('w-reset').onclick=function(){wRun=false;wAcc=0;clearInterval(wTimer);G('w-go').textContent='Start';wdraw()};
G('stop').onclick=function(){var id=document.documentElement.getAttribute('data-ring')==='1'?null:null;api({action:'stop'}).then(paint)};
G('snooze').onclick=function(){api({action:'snooze'}).then(paint)};
G('bedside').onclick=function(){var onb=document.documentElement.getAttribute('data-bed')==='1';document.documentElement.setAttribute('data-bed',onb?'0':'1');G('night').hidden=onb;G('bedside').textContent=onb?'Bedside':'Clock'};
show();dayButtons();tshow();load();setInterval(load,5000);
window.oposClock={refresh:load};
"""


def reset():
    """Tests drop the saved book and any pretended clock."""
    global _BOOK, _FAKE
    _BOOK = None
    _FAKE = None


def zone():
    name = os.environ.get("OPOS_CLOCK_ZONE") or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def clock_known():
    flag = os.environ.get("OPOS_CLOCK_OK")
    if flag == "0":
        return False
    if flag == "1":
        return True
    try:
        from opos.web import clock_ok
        return bool(clock_ok())
    except Exception:
        return False


def current_now():
    if os.environ.get("OPOS_CLOCK_TEST") == "1" and _FAKE is not None:
        return _FAKE
    return datetime.now(zone())


def book():
    global _BOOK
    if _BOOK is None:
        root = os.environ.get("OPOS_STATE_DIR") or default_root()
        _BOOK = alarms.Book(root, zone())
    return _BOOK


def snapshot():
    saved = book()
    now = current_now()
    nxt = saved.next_alarm(now) if clock_known() else None
    rows = []
    ringing = None
    for row in saved.alarms:
        rows.append({
            "id": row["id"], "hour": row["hour"], "minute": row["minute"],
            "days": list(row.get("days") or []), "label": row.get("label") or "",
            "enabled": bool(row.get("enabled")), "once": bool(row.get("once")),
        })
        if row.get("ringing") and ringing is None:
            ringing = {"id": row["id"], "label": row.get("label") or "", "text": "Alarm %02d:%02d" % (row["hour"], row["minute"])}
    timer = None
    if saved.timer and isinstance(saved.timer.get("fires_at"), str):
        timer = {"id": saved.timer.get("id") or "", "fires_at": saved.timer["fires_at"], "label": saved.timer.get("label") or ""}
    return {
        "ok": True, "clock_ok": clock_known(), "now": now.isoformat(), "alarms": rows,
        "ringing": ringing, "timer": timer, "notices": list(saved.notices), "recovered": saved.recovered or "",
        "next": None if nxt is None else {"id": nxt["id"], "text": nxt["text"], "label": nxt["label"], "at": nxt["at"].isoformat()},
    }


def _days(data):
    days = data.get("days") if data.get("days") is not None else []
    if not isinstance(days, list):
        raise ValueError("days")
    return tuple(days)


def _when(text):
    if not isinstance(text, str):
        raise ValueError("now")
    made = datetime.fromisoformat(text)
    if made.tzinfo is None:
        made = made.replace(tzinfo=zone())
    return made


def post(data):
    global _FAKE
    action = data.get("action") if isinstance(data, dict) else None
    try:
        saved = book()
        if action == "add":
            saved.add(data.get("hour"), data.get("minute"), _days(data), data.get("label") or "", current_now())
        elif action == "edit":
            saved.edit(data.get("id"), data.get("hour"), data.get("minute"), _days(data), data.get("label") or "")
        elif action == "delete":
            saved.delete(data.get("id"))
        elif action == "toggle":
            saved.toggle(data.get("id"), data.get("enabled"))
        elif action == "snooze":
            saved.snooze(current_now(), data.get("id"))
        elif action == "stop":
            saved.stop(current_now(), data.get("id"))
        elif action == "timer_start":
            saved.start_timer(data.get("seconds"), current_now(), data.get("label") or "")
        elif action == "timer_cancel":
            saved.cancel_timer()
        elif action == "tick":
            if os.environ.get("OPOS_CLOCK_TEST") != "1":
                return 403, {"error": "forbidden"}
            _FAKE = _when(data.get("now"))
            saved.tick(_FAKE, clock_known())
        else:
            return 400, {"error": "bad request"}
    except ValueError as exc:
        code = str(exc)
        return 400, {"error": code if code in _CODES else "bad request"}
    return 200, snapshot()


def render(token, nonce):
    return appdoc.render("Clock", token, nonce, _LIST, _THUMB, _SCRIPT, _CSS)


def start_thread():
    """The 30 second re-check. main() starts it when Clock is named. Tests do not."""
    global _THREAD
    if _THREAD is not None:
        return

    def loop():
        while True:
            time.sleep(30)
            try:
                events = book().tick(current_now(), clock_known())
                if events:
                    _play(events)
            except Exception:
                pass

    _THREAD = threading.Thread(target=loop, name="opos-clock", daemon=True)
    _THREAD.start()


def _play(events):
    try:
        from opos import alarm_sound
    except ImportError:
        return
    alarm_sound.handle(events)


apps.register(apps.App(
    "clock", "Clock", "/clock",
    "Alarms, a timer, a stopwatch and a bedside clock.",
    "yes", "no", "speaker, vibration", "real", "alarms", "ESSENTIAL", 0.1,
    render, snapshot, post,
))
