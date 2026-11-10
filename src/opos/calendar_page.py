# SPDX-License-Identifier: GPL-3.0-or-later
"""The Calendar page. Events stay in calendar.ics on this phone.

Reminders use the alarm grace and event type. They do not write the alarm file.
A test may POST action "tick" only when OPOS_CALENDAR_TEST=1.
"""
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from opos import appdoc, apps, calendar_ics
from opos.store import default_root

_BOOK = None
_FAKE = None
_THREAD = None
_CODES = {"title", "date", "time", "skipped", "full", "id", "rule", "reminder", "all_day", "days", "now"}

_CSS = """
#month{display:none}
#month button{min-height:48px;min-width:48px;padding:4px}
#rows button,#today button,#tomorrow button,#later button{text-align:left}
input[type=date],input[type=time],input[type=text]{width:100%;min-height:48px;margin:8px 0;padding:8px;background:var(--bg);color:var(--fg);border:1px solid var(--fg);border-radius:0;font:inherit}
@media(min-width:1000px){#month{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px}}
"""

_LIST = """
<h1>Calendar</h1>
<p id="warn" class="note" hidden>Clock not set: reminders will not show until the time is known</p>
<p id="recovered" class="note" hidden></p>
<p id="remind" class="note" role="status" hidden></p>
<h2>Today</h2>
<ul id="today"></ul>
<h2>Tomorrow</h2>
<ul id="tomorrow"></ul>
<h2>Later this week</h2>
<ul id="later"></ul>
<div id="month"></div>
"""

_THUMB = """
<h2 id="event-title">Day</h2>
<p id="event-when"></p>
<label>Title <input id="ev-title" type="text" maxlength="80" autocomplete="off"></label>
<label>Date <input id="ev-date" type="date"></label>
<label id="time-label">Time <input id="ev-time" type="time" value="15:00"></label>
<button id="allday" type="button" aria-pressed="false">All day: Off</button>
<div id="repeat"></div>
<div id="days"></div>
<div id="reminders"></div>
<button id="save" class="main" type="button">Save</button>
<button id="fresh" type="button">Add event</button>
<button id="del" type="button" hidden>Delete</button>
<button id="dismiss" type="button" hidden>Dismiss</button>
<button id="imp" type="button">Import</button>
<button id="exp" type="button">Export</button>
<p id="file-line" role="status"></p>
"""

_SCRIPT = r"""
function G(i){return document.getElementById(i)}
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
var T=document.querySelector('meta[name=opos-token]').content;
var DAYS=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
var REM=[['none','None'],['10min','10 min'],['1h','1 hour'],['1d','1 day']];
var last=null, sel=null, rule='once', reminder='none', on={};
function api(b){return fetch('/api/calendar',{method:b?'POST':'GET',headers:Object.assign({'X-OPOS-Token':T},b?{'Content-Type':'application/json'}:{}),body:b?JSON.stringify(b):undefined}).then(function(r){return r.json()})}
function setPressed(id,word,yes){var b=G(id);b.setAttribute('aria-pressed',yes?'true':'false');b.textContent=word+(yes?'On':'Off')}
function chosen(){var d=[];DAYS.forEach(function(_,i){if(on[i])d.push(i)});return d}
function flags(){
  var box=G('repeat');box.textContent='';
  [['once','Once'],['daily','Daily'],['weekly','Weekly']].forEach(function(pair){
    var b=el('button',null,pair[1]);b.type='button';b.setAttribute('aria-pressed',rule===pair[0]?'true':'false');
    b.onclick=function(){rule=pair[0];flags()};box.appendChild(b)});
  var days=G('days');days.textContent='';
  if(rule==='weekly')DAYS.forEach(function(name,i){var b=el('button',null,name+': '+(on[i]?'On':'Off'));b.type='button';b.setAttribute('aria-pressed',on[i]?'true':'false');b.onclick=function(){on[i]=!on[i];flags()};days.appendChild(b)});
  var rem=G('reminders');rem.textContent='';
  REM.forEach(function(pair){var b=el('button',null,pair[1]);b.type='button';b.setAttribute('aria-pressed',reminder===pair[0]?'true':'false');b.onclick=function(){reminder=pair[0];flags()};rem.appendChild(b)});
}
function section(id, rows, empty){
  var box=G(id);box.textContent='';
  if(!rows||!rows.length){box.appendChild(el('li',null,empty));return}
  rows.forEach(function(row){var li=document.createElement('li'),b=el('button',null,(row.all_day?'All day':row.time)+' · '+row.title);b.type='button';b.onclick=function(){select(row.id)};li.appendChild(b);box.appendChild(li)});
}
function select(id){
  var ev=((last&&last.events)||[]).filter(function(item){return item.id===id})[0];
  if(!ev)return;
  sel=ev.id;G('ev-title').value=ev.title||'';G('ev-date').value=ev.date||'';G('ev-time').value=ev.time||'15:00';
  setPressed('allday','All day: ',!!ev.all_day);G('time-label').hidden=!!ev.all_day;
  rule=ev.rule||'once';reminder=ev.reminder||'none';on={};(ev.days||[]).forEach(function(d){on[d]=true});
  G('event-title').textContent=ev.title||'Day';G('event-when').textContent=(ev.all_day?'All day':ev.time)+' '+ev.date;
  G('del').hidden=false;G('del').dataset.sure='';G('del').textContent='Delete';flags();
}
function month(s){
  var box=G('month');box.textContent='';
  if(!s.month)return;
  box.appendChild(el('p',null,'Mon Tue Wed Thu Fri Sat Sun'));
  var i;for(i=0;i<s.month.lead;i++)box.appendChild(el('span',null,'\u00a0'));
  s.month.days.forEach(function(d){var b=el('button',null,String(d.n));b.type='button';if(d.date===s.selected)b.setAttribute('aria-pressed','true');b.onclick=function(){api({action:'show',date:d.date}).then(paint)};box.appendChild(b)});
}
function paint(s){
  if(!s||s.error){if(s&&s.error)G('file-line').textContent=s.error==='skipped'?'That minute is skipped when the clocks change.':'Could not save.';return}
  last=s;G('warn').hidden=!!s.clock_ok;var rec=G('recovered');rec.hidden=!s.recovered;rec.textContent=s.recovered||'';
  var note=G('remind');note.hidden=!(s.reminder&&s.reminder.text);note.textContent=(s.reminder&&s.reminder.text)||'';
  G('dismiss').hidden=!(s.reminder&&s.reminder.key);
  section('today',s.agenda&&s.agenda.today,'Nothing today.');
  section('tomorrow',s.agenda&&s.agenda.tomorrow,'Nothing tomorrow.');
  section('later',s.agenda&&s.agenda.later,'Nothing later this week.');
  month(s);
  if(s.file_line)G('file-line').textContent=s.file_line;
  if(!sel&&s.selected&&!G('ev-date').value)G('ev-date').value=s.selected;
}
function load(){return api().then(paint).catch(function(){})}
G('allday').onclick=function(){var yes=G('allday').getAttribute('aria-pressed')!=='true';setPressed('allday','All day: ',yes);G('time-label').hidden=yes};
G('save').onclick=function(){var body={action:sel?'edit':'add',title:G('ev-title').value,date:G('ev-date').value,time:G('ev-time').value,all_day:G('allday').getAttribute('aria-pressed')==='true',rule:rule,days:chosen(),reminder:reminder};if(sel)body.id=sel;api(body).then(function(s){if(s&&!s.error){sel=null;G('del').hidden=true}paint(s)})};
G('fresh').onclick=function(){sel=null;G('ev-title').value='';rule='once';reminder='none';on={};setPressed('allday','All day: ',false);G('time-label').hidden=false;G('del').hidden=true;G('event-title').textContent='Day';G('event-when').textContent='';flags()};
G('del').onclick=function(){if(!sel)return;if(G('del').dataset.sure!=='1'){G('del').dataset.sure='1';G('del').textContent='Delete this event?';return}api({action:'delete',id:sel}).then(function(s){sel=null;G('del').hidden=true;paint(s)})};
G('dismiss').onclick=function(){if(!last||!last.reminder)return;api({action:'dismiss',key:last.reminder.key}).then(paint)};
G('imp').onclick=function(){api({action:'import'}).then(paint)};
G('exp').onclick=function(){api({action:'export'}).then(paint)};
flags();load();setInterval(load,5000);
window.oposCalendar={refresh:load};
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
    """Same rule as Clock. The import stays here so this module does not load the web server while the server is still starting."""
    flag = os.environ.get("OPOS_CLOCK_OK")
    if flag == "0":
        return False
    if flag == "1":
        return True
    try:
        from opos.web import clock_ok
    except Exception:
        return True
    try:
        return bool(clock_ok())
    except Exception:
        return True


def current_now():
    if os.environ.get("OPOS_CALENDAR_TEST") == "1" and _FAKE is not None:
        return _FAKE
    return datetime.now(zone())


def book():
    global _BOOK
    if _BOOK is None:
        root = os.environ.get("OPOS_STATE_DIR") or default_root()
        _BOOK = calendar_ics.Book(root, zone())
    return _BOOK


def _folder():
    raw = os.environ.get("OPOS_CALENDAR_DIR")
    return Path(raw) if raw else Path(os.environ.get("OPOS_STATE_DIR") or default_root()) / "ics"


def _row(item):
    local = item.start.astimezone(zone())
    return {
        "id": item.uid, "title": item.title, "date": local.date().isoformat(),
        "time": "" if item.all_day else "%02d:%02d" % (local.hour, local.minute),
        "all_day": item.all_day, "rule": item.rule or "once", "days": list(item.days),
        "reminder": item.reminder,
    }


def snapshot(file_line=""):
    saved = book()
    now = current_now()
    known = clock_known()
    saved.tick(now, known)
    selected = saved.view or now.astimezone(zone()).date()
    due = saved.due(now, known)
    reminder = None
    if due is not None:
        reminder = {"text": due.text, "key": calendar_ics.ack_key(due.id, due.at)}
    day_rows = []
    for item in saved.events:
        start = datetime(selected.year, selected.month, selected.day, tzinfo=zone())
        for when in calendar_ics.occurrences(item, start, start + timedelta(days=1) - timedelta(seconds=1)):
            if when.astimezone(zone()).date() == selected:
                day_rows.append(_row(item))
                break
    return {
        "ok": True, "clock_ok": known, "now": now.isoformat(), "recovered": saved.recovered or "",
        "events": [_row(item) for item in saved.events],
        "agenda": calendar_ics.agenda(saved.events, now),
        "month": calendar_ics.month_days(selected.year, selected.month),
        "selected": selected.isoformat(), "day": day_rows,
        "next_lock": calendar_ics.next_lock(saved.events, now, known),
        "reminder": reminder, "notices": list(saved.notices), "file_line": file_line,
    }


def _date(text):
    if not isinstance(text, str) or len(text) != 10 or text[4] != "-" or text[7] != "-":
        raise ValueError("date")
    try:
        return date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
    except ValueError:
        raise ValueError("date") from None


def _when(text):
    if not isinstance(text, str):
        raise ValueError("now")
    made = datetime.fromisoformat(text)
    if made.tzinfo is None:
        made = made.replace(tzinfo=zone())
    return made


def _rule(value):
    if value in (None, "", "once"):
        return ""
    if value in ("daily", "weekly"):
        return value
    raise ValueError("rule")


def _time(data, all_day):
    if all_day:
        return 0, 0
    text = data.get("time")
    if not isinstance(text, str) or len(text) < 5 or text[2] != ":":
        raise ValueError("time")
    hour, minute = text[:2], text[3:5]
    if not (hour.isdigit() and minute.isdigit()):
        raise ValueError("time")
    return int(hour), int(minute)


def _fields(data):
    all_day = data.get("all_day") is True
    hour, minute = _time(data, all_day)
    days = data.get("days") if data.get("days") is not None else []
    reminder = data.get("reminder") or "none"
    return data.get("title"), _date(data.get("date")), hour, minute, all_day, _rule(data.get("rule")), days, reminder


def post(data):
    global _FAKE
    action = data.get("action") if isinstance(data, dict) else None
    try:
        saved = book()
        if action == "add":
            saved.add(*_fields(data))
            return 200, snapshot()
        if action == "edit":
            saved.edit(data.get("id"), *_fields(data))
            return 200, snapshot()
        if action == "delete":
            saved.delete(data.get("id"))
            return 200, snapshot()
        if action == "dismiss":
            saved.dismiss(data.get("key"))
            return 200, snapshot()
        if action == "show":
            saved.show(_date(data.get("date")))
            return 200, snapshot()
        if action == "import":
            return _import()
        if action == "export":
            return _export()
        if action == "tick":
            if os.environ.get("OPOS_CALENDAR_TEST") != "1":
                return 403, {"error": "forbidden"}
            _FAKE = _when(data.get("now"))
            saved.tick(_FAKE, clock_known())
            return 200, snapshot()
    except ValueError as exc:
        code = str(exc)
        return 400, {"error": code if code in _CODES else "bad request"}
    return 400, {"error": "bad request"}


def _import():
    folder = _folder()
    files = sorted(folder.glob("*.ics")) if folder.is_dir() else []
    if not files:
        return 200, snapshot("No calendar file found.")
    added = 0
    skipped = 0
    known = {item.uid for item in book().events}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            skipped += 1
            continue
        before = {item.uid for item in book().events}
        try:
            skipped += book().import_text(text)
        except ValueError:
            skipped += 1
            continue
        added += len({item.uid for item in book().events} - before)
    line = "Imported %d." % len({item.uid for item in book().events} - known)
    if skipped:
        line += " %d fields skipped." % skipped
    return 200, snapshot(line if added or skipped else "Imported 0.")


def _export():
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "calendar.ics"
    saved = book()
    path.write_text(calendar_ics.emit(saved.events, saved.notices), encoding="utf-8")
    os.chmod(path, 0o600)
    return 200, snapshot("Exported %d." % len(saved.events))


def render(token, nonce):
    return appdoc.render("Calendar", token, nonce, _LIST, _THUMB, _SCRIPT, _CSS)


def start_thread():
    """The 30 second re-check. main() starts it when Calendar is named. Tests do not."""
    global _THREAD
    if _THREAD is not None:
        return

    def loop():
        while True:
            time.sleep(30)
            try:
                book().tick(current_now(), clock_known())
            except Exception:
                pass

    _THREAD = threading.Thread(target=loop, name="opos-calendar", daemon=True)
    _THREAD.start()


apps.register(apps.App(
    "calendar", "Calendar", "/calendar",
    "A calendar kept on this phone.",
    "yes", "no", "none", "real", "calendar", "USEFUL", 0,
    render, snapshot, post,
))
