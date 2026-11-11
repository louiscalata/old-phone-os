# SPDX-License-Identifier: GPL-3.0-or-later
"""Local contacts. The practice phone still refuses a real call. Demo rows appear only in an empty book."""
import os
import uuid
from pathlib import Path

from opos import appdoc, apps, vcard
from opos.essentials import normalize_number
from opos.practice import CALL
from opos.store import Store, default_root

_BOOK = None
_MAX = 500
_LABELS = ("Mobile", "Home", "Work")
_DEMO = (
    {"name": "Mom", "numbers": [{"number": "+15550100", "label": "Mobile"}], "note": "", "favorite": False, "ice": False, "demo": True},
    {"name": "Clinic", "numbers": [{"number": "+15550142", "label": "Work"}], "note": "", "favorite": False, "ice": False, "demo": True},
    {"name": "Work", "numbers": [{"number": "+15550199", "label": "Work"}], "note": "", "favorite": False, "ice": False, "demo": True},
)

_CSS = """
#find,#cname,#cnum,#cnote{width:100%;min-height:48px;margin:8px 0;padding:8px;background:var(--bg);color:var(--fg);border:1px solid var(--fg);border-radius:0;font:inherit}
#rows button{text-align:left}
#letters{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:8px 0}
#letters button{margin:0;min-height:48px;min-width:48px}
#rows .letter-head{font:.875rem/1.3 'Arial Narrow','Roboto Condensed',sans-serif-condensed,sans-serif;text-transform:uppercase;letter-spacing:.04em;border-top:1px solid var(--fg);padding:8px 0}
"""

_LIST = """
<h1>Contacts</h1>
<label>Search <input id="find" type="search" autocomplete="off"></label>
<div id="letters" aria-label="Letters"></div>
<p id="practice" class="note" hidden>Practice contacts. These are examples.</p>
<p id="skipped" class="note" hidden></p>
<ul id="rows"></ul>
"""

_THUMB = """
<div id="view">
<h2 id="who">People</h2>
<p id="nums"></p>
<p id="note-line"></p>
<div>
<a id="call" class="btn" href="#">Call</a>
<a id="text" class="btn" href="#">Text</a>
<button id="edit" type="button">Edit</button>
</div>
<p id="call-line" role="status"></p>
</div>
<div id="form" hidden>
<label>Name <input id="cname" type="text" maxlength="80" autocomplete="off"></label>
<label>Number <input id="cnum" type="tel" maxlength="32" autocomplete="off"></label>
<div id="clabel"></div>
<button id="fav" type="button" aria-pressed="false">Favorite: Off</button>
<button id="ice" type="button" aria-pressed="false">In case of emergency: Off</button>
<label>Note <input id="cnote" type="text" maxlength="200" autocomplete="off"></label>
<button id="save" class="main" type="button">Save</button>
<button id="cancel" type="button">Cancel</button>
<button id="del" type="button" hidden>Delete</button>
</div>
<button id="fresh" type="button">Add</button>
<button id="imp" type="button">Import</button>
<button id="exp" type="button">Export</button>
<p id="file-line" role="status"></p>
"""

_SCRIPT = r"""
function G(i){return document.getElementById(i)}
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
var T=document.querySelector('meta[name=opos-token]').content, people=[], sel=null, label='Mobile';
function api(b){return fetch('/api/contacts',{method:b?'POST':'GET',headers:Object.assign({'X-OPOS-Token':T},b?{'Content-Type':'application/json'}:{}),body:b?JSON.stringify(b):undefined}).then(function(r){return r.json()})}
function letterOf(name){var c=(name||'').charAt(0).toUpperCase();return (c>='A'&&c<='Z')?c:'#'}
function letterKey(letter){return letter==='#'?'other':letter}
function practiceUrl(which, number){return '/?practice='+which+'&n='+encodeURIComponent(number||'')}
function paint(s){if(!s||s.error)return;people=s.people||[];G('practice').hidden=!s.practice;G('skipped').hidden=!s.skipped;G('skipped').textContent=s.skipped?('Skipped '+s.skipped+' fields.'):'';
var q=(G('find').value||'').toLowerCase(), rows=G('rows'), letters=G('letters');rows.textContent='';letters.textContent='';
var shown=people.filter(function(p){return !q||(p.name+' '+(p.numbers||[]).map(function(n){return n.number}).join(' ')).toLowerCase().indexOf(q)>=0});
shown.sort(function(a,b){var x=letterOf(a.name),y=letterOf(b.name);if(x!==y)return x<y?-1:1;x=(a.name||'').toLowerCase();y=(b.name||'').toLowerCase();return x<y?-1:x>y?1:0});
var prev='';
shown.forEach(function(p){
var letter=letterOf(p.name);
if(letter!==prev){prev=letter;var head=el('li','letter-head',letter);head.id='letter-'+letterKey(letter);rows.appendChild(head);
var jump=el('button',null,letter);jump.type='button';jump.setAttribute('aria-label','Jump to '+letter);jump.onclick=function(){var h=G('letter-'+letterKey(letter));if(!h)return;var n=h.nextElementSibling,b=n&&n.querySelector('button');if(b&&b.focus){try{b.focus({preventScroll:true})}catch(e){b.focus()}}if(h.scrollIntoView){try{h.scrollIntoView({block:'start'})}catch(e){h.scrollIntoView()}}};letters.appendChild(jump)}
var li=document.createElement('li'),b=el('button',null,p.name+(p.favorite?' · Favorite':''));b.type='button';b.onclick=function(){select(p)};li.appendChild(b);rows.appendChild(li)});
if(sel){var cur=people.filter(function(p){return p.id===sel.id})[0];if(cur)select(cur)}}
function select(p){sel=p;G('form').hidden=true;G('view').hidden=false;G('who').textContent=p.name;G('nums').textContent=(p.numbers||[]).map(function(n){return n.label+' '+n.number}).join(', ');G('note-line').textContent=p.note||'';var num=(p.numbers&&p.numbers[0]&&p.numbers[0].number)||'';G('call').setAttribute('href',practiceUrl('phone',num));G('text').setAttribute('href',practiceUrl('messages',num));G('del').hidden=!!p.demo}
function setFlag(id,on,word){G(id).setAttribute('aria-pressed',on?'true':'false');G(id).textContent=word+(on?'On':'Off')}
G('find').oninput=function(){api().then(paint)};
function stay(id){G(id).addEventListener('click',function(e){if((G(id).getAttribute('href')||'#')==='#')e.preventDefault()})}
stay('call');stay('text');
G('edit').onclick=function(){if(!sel||sel.demo)return;G('form').hidden=false;G('view').hidden=true;G('cname').value=sel.name;G('cnum').value=(sel.numbers[0]||{}).number||'';label=(sel.numbers[0]||{}).label||'Mobile';setFlag('fav',!!sel.favorite,'Favorite: ');setFlag('ice',!!sel.ice,'In case of emergency: ');G('cnote').value=sel.note||'';G('del').hidden=false;G('del').dataset.sure='';G('del').textContent='Delete'};
G('fresh').onclick=function(){sel=null;G('form').hidden=false;G('view').hidden=true;G('cname').value='';G('cnum').value='';label='Mobile';setFlag('fav',false,'Favorite: ');setFlag('ice',false,'In case of emergency: ');G('cnote').value='';G('del').hidden=true};
G('fav').onclick=function(){setFlag('fav',G('fav').getAttribute('aria-pressed')!=='true','Favorite: ')};
G('ice').onclick=function(){setFlag('ice',G('ice').getAttribute('aria-pressed')!=='true','In case of emergency: ')};
G('cancel').onclick=function(){G('form').hidden=true;G('view').hidden=false};
G('save').onclick=function(){var body={action:sel&&!sel.demo?'edit':'add',name:G('cname').value,number:G('cnum').value,label:label,favorite:G('fav').getAttribute('aria-pressed')==='true',ice:G('ice').getAttribute('aria-pressed')==='true',note:G('cnote').value};if(sel&&!sel.demo)body.id=sel.id;api(body).then(function(s){G('form').hidden=true;G('view').hidden=false;paint(s)})};
G('del').onclick=function(){if(!sel||sel.demo)return;if(G('del').dataset.sure!=='1'){G('del').dataset.sure='1';G('del').textContent='Delete this person?';return}api({action:'delete',id:sel.id}).then(function(s){sel=null;paint(s)})};
G('imp').onclick=function(){api({action:'import'}).then(function(s){G('file-line').textContent=s.file_line||'';paint(s)})};
G('exp').onclick=function(){api({action:'export'}).then(function(s){G('file-line').textContent=s.file_line||''})};
api().then(paint);
window.oposContacts={refresh:function(){return api().then(paint)}};
"""


def reset():
    global _BOOK
    _BOOK = None


def _store():
    global _BOOK
    if _BOOK is None:
        root = os.environ.get("OPOS_STATE_DIR") or default_root()
        _BOOK = Store(root)
    return _BOOK


def _folder():
    raw = os.environ.get("OPOS_CONTACTS_DIR")
    path = Path(raw) if raw else Path(os.environ.get("OPOS_STATE_DIR") or default_root()) / "vcards"
    return path


def _load():
    data, note = _store().read_json("contacts.json", {"people": []})
    people = data.get("people") if isinstance(data.get("people"), list) else []
    return [row for row in people if isinstance(row, dict) and isinstance(row.get("id"), str)], note


def _save(people):
    _store().write_json("contacts.json", {"people": people})


def _public(row):
    return {
        "id": row.get("id") or "", "name": row.get("name") or "", "numbers": list(row.get("numbers") or []),
        "note": row.get("note") or "", "favorite": bool(row.get("favorite")), "ice": bool(row.get("ice")),
        "demo": bool(row.get("demo")),
    }


def snapshot(note="", skipped=0, file_line=""):
    people, recovered = _load()
    practice = False
    if not people:
        people = [dict(row, id="demo-%d" % i) for i, row in enumerate(_DEMO)]
        practice = True
    people = sorted(people, key=lambda row: (not row.get("favorite"), (row.get("name") or "").lower()))
    return {
        "ok": True, "people": [_public(row) for row in people], "practice": practice,
        "recovered": recovered or note, "skipped": skipped, "file_line": file_line,
        "call": CALL,
    }


def _clean_person(data, existing=None):
    name = data.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise ValueError("name")
    number = normalize_number(data.get("number") or "")
    label = data.get("label") if data.get("label") in _LABELS else "Mobile"
    note = data.get("note") if isinstance(data.get("note"), str) else ""
    favorite = data.get("favorite") is True
    ice = data.get("ice") is True
    people, _ = _load()
    if favorite and not (existing and existing.get("favorite")):
        if sum(1 for row in people if row.get("favorite")) >= 6:
            raise ValueError("favorites")
    row = {
        "id": existing["id"] if existing else uuid.uuid4().hex[:8],
        "name": " ".join(name.split()),
        "numbers": [{"number": number, "label": label}],
        "note": note.strip()[:200],
        "favorite": favorite,
        "ice": ice,
    }
    return row


def post(data):
    action = data.get("action") if isinstance(data, dict) else None
    people, note = _load()
    try:
        if action == "add":
            if len(people) >= _MAX:
                raise ValueError("full")
            people.append(_clean_person(data))
            _save(people)
            return 200, snapshot(note)
        if action == "edit":
            found = next((row for row in people if row["id"] == data.get("id")), None)
            if found is None:
                raise ValueError("id")
            people = [_clean_person(data, found) if row["id"] == found["id"] else row for row in people]
            _save(people)
            return 200, snapshot(note)
        if action == "delete":
            if data.get("id") not in {row["id"] for row in people}:
                raise ValueError("id")
            _save([row for row in people if row["id"] != data.get("id")])
            return 200, snapshot(note)
        if action == "import":
            return _import(people, note)
        if action == "export":
            return _export(people, note)
    except ValueError as exc:
        code = str(exc)
        return 400, {"error": code if code in {"name", "id", "full", "favorites"} else "bad request"}
    return 400, {"error": "bad request"}


def _import(people, note):
    folder = _folder()
    files = sorted(folder.glob("*.vcf")) if folder.is_dir() else []
    if not files:
        return 200, dict(snapshot(note), file_line="No vCard file found.")
    added = 0
    skipped = 0
    known = {item["number"] for row in people for item in row.get("numbers") or []}
    for path in files:
        parsed, skip = vcard.parse(path.read_text(encoding="utf-8"))
        skipped += skip
        for person in parsed:
            number = person["numbers"][0]["number"]
            if number in known or len(people) >= _MAX:
                continue
            people.append({
                "id": uuid.uuid4().hex[:8], "name": person["name"], "numbers": person["numbers"],
                "note": person.get("note") or "", "favorite": False, "ice": False,
            })
            known.add(number)
            added += 1
    _save(people)
    line = "Imported %d." % added
    if skipped:
        line += " %d fields skipped." % skipped
    return 200, dict(snapshot(note, skipped), file_line=line)


def _export(people, note):
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "contacts.vcf"
    path.write_text(vcard.emit(people), encoding="utf-8")
    os.chmod(path, 0o600)
    return 200, dict(snapshot(note), file_line="Exported %d." % len(people))


def render(token, nonce):
    return appdoc.render("Contacts", token, nonce, _LIST, _THUMB, _SCRIPT, _CSS)


apps.register(apps.App(
    "contacts", "Contacts", "/contacts",
    "The people kept on this phone.",
    "yes", "no", "none", "real", "contacts", "USEFUL", 0,
    render, snapshot, post,
))
