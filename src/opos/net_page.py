# SPDX-License-Identifier: GPL-3.0-or-later
"""The Network page (GET /network) and its POST actions net_status, net_scan, net_join, net_forget, net_wifi.

A separate page, because the main page is at its size budget. web.py serves it with the same token, origin and
rate-limit rules as every other action and calls handle() for the actions; the logic is in opos.net. Nothing here
is part of /api/state, so no network name reaches the helper. A password is read from the request, handed to
nmcli on its standard input and dropped.
"""
import threading
from html import escape

from opos import net, secure
from opos.firstrun_page import CSS as _BASE

ACTIONS = frozenset({"net_status", "net_scan", "net_join", "net_forget", "net_wifi"})
_SERVICE = None
_LOCK = threading.Lock()


def buckets():
    """Fresh per-server rate limits (burst, then one per 2 s to one per 10 s)."""
    return {"net_status": secure.RateLimiter(0.5, 6), "net_scan": secure.RateLimiter(0.1, 2), "net_join": secure.RateLimiter(0.1, 3),
            "net_forget": secure.RateLimiter(0.2, 5), "net_wifi": secure.RateLimiter(0.2, 5)}


def service():
    global _SERVICE
    with _LOCK:
        if _SERVICE is None:
            _SERVICE = net.Net()
        return _SERVICE


def set_service(svc):
    """Tests and other callers swap the Net the page talks to."""
    global _SERVICE
    with _LOCK:
        _SERVICE = svc


def _str(data, key, n, required=False):
    v = data.get(key)
    if v is None:
        if required:
            raise ValueError(key)
        return None
    if not isinstance(v, str) or len(v) > n:
        raise ValueError(key)
    return v


def handle(action, data, svc=None):
    """(HTTP status, JSON object) for one validated net_* action."""
    n = svc or service()
    try:
        if action == "net_status":
            return 200, n.status(data.get("fresh") is True)
        if action == "net_scan":
            return 200, n.scan(data.get("rescan") is not False)
        if action == "net_join":
            hidden = data.get("hidden")
            if hidden is not None and not isinstance(hidden, bool):
                raise ValueError("hidden")
            return 200, n.join(_str(data, "ssid", 64, True), _str(data, "password", 128), hidden is True)
        if action == "net_forget":
            return 200, n.forget(_str(data, "ssid", 64, True))
        if action == "net_wifi":
            return 200, n.wifi(_str(data, "mode", 16, True))
        if action == "net_restart":
            mode = _str(data, "mode", 16, True)
            if mode == "wifi":
                return 200, n.restart_wifi()
            if mode == "modem":
                return 200, n.restart_modem()
            raise ValueError("mode")
    except ValueError:
        return 400, {"error": "bad request"}
    return 400, {"error": "unknown action"}


LINK = ('<section aria-labelledby="set-net"><h3 id="set-net">Network</h3><a id="net-link" href="/network" '
        'style="display:inline-block;min-height:48px;line-height:48px;color:inherit;text-decoration:underline">Wi-Fi and radios</a></section>')
ANCHOR = '<section aria-labelledby="set-about">'


def add_settings_link(html, env):
    """The main page with a Network link in Settings, only where OPOS_NETWORK=1; unchanged (byte for byte) otherwise."""
    if env.get("OPOS_NETWORK") != "1" or ANCHOR not in html:
        return html
    return html.replace(ANCHOR, LINK + ANCHOR, 1)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="opos-token" content="@@TOKEN@@"><title>Network - Old Phone OS Interface</title><link rel="icon" href="/favicon.ico" type="image/png"><style>@@CSS@@</style><script>try{var t=localStorage.getItem('jaryo_theme');if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t)}catch(e){}</script></head><body>
<header><a id="home" class="btn" href="/">Home</a><h1>Network</h1></header>
<main>
<section aria-labelledby="h-st"><h2 id="h-st">This phone</h2><ul id="st"><li>Reading the radios...</li></ul><p class="note" id="sim"></p><p class="note" id="ctl"></p><ul class="note" id="notes"></ul></section>
<div id="gap" aria-hidden="true"></div>
<section aria-labelledby="h-wf"><h2 id="h-wf">Wi-Fi</h2>
<div><button id="scan" type="button">Scan</button><button id="tog" type="button" disabled>Turn Wi-Fi off</button><button id="b-wifi-restart" type="button" disabled>Restart Wi-Fi</button><button id="b-modem-restart" type="button" disabled>Restart modem</button><button id="disc" type="button" hidden>Disconnect</button><button id="hid" type="button" disabled>Hidden network</button></div>
<p id="msg" role="status" aria-live="polite"></p>
<ul id="nets"></ul>
<div id="sel" hidden><h3 id="sel-t"></h3><label id="hrow" hidden>Network name<input id="ssid" type="text" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="32"></label><p class="note" id="sel-n"></p><label id="prow"><span id="pwl">Password</span><input id="pw" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="64"></label><label id="srow"><input id="show" type="checkbox"> Show the password</label><div><button id="b-join" type="button" class="main">Join</button><button id="b-forget" type="button">Forget</button><button id="b-disc" type="button">Disconnect</button><button id="b-cancel" type="button">Cancel</button></div></div>
<p class="note">The password is passed to the phone's network service and is not kept by OPOS. A failed join is not saved.</p>
</section>
</main>
<noscript><p class="note">This page needs scripts to show the radios.</p></noscript>
<script>
(function(){
var T=document.querySelector('meta[name=opos-token]').content,cur=null,can=false,wifiOn=null,busy=false;
function G(i){return document.getElementById(i)}
function api(o){return fetch('/api',{method:'POST',headers:{'Content-Type':'application/json','X-OPOS-Token':T},body:JSON.stringify(o)}).then(function(r){return r.json().then(function(j){
if(r.status===429)return{error:'Too many tries in a row. Wait a few seconds, then try again.'};
if(r.status===403)return{error:'This page is out of date. Reload it, then try again.'};
if(r.status>=500)return{error:'Something went wrong in OPOS. Try again in a minute.'};
return j})}).catch(function(){return{error:'OPOS is not responding. Wait a few seconds, then reload this page.'}})}
function say(t,bad){var m=G('msg');m.textContent=t||'';m.toggleAttribute('data-bad',!!bad)}
function li(ul,t){var l=document.createElement('li');l.textContent=t;ul.appendChild(l)}
function lock(){['tog','hid','b-join','b-forget','b-disc','b-wifi-restart','b-modem-restart'].forEach(function(i){G(i).disabled=busy||!can});G('scan').disabled=busy}
function paint(s){var u=G('st');u.textContent='';
if(s.error){li(u,s.error);return}
['wifi','bluetooth','modem','sim','data','airplane','usb'].forEach(function(k){li(u,(s[k]||{}).summary||k+': unavailable')});
G('sim').textContent=s.simulated||'';var c=s.control||{};can=!!c.allowed;G('ctl').textContent=c.reason||'';
var n=G('notes');n.textContent='';(s.notes||[]).forEach(function(t){li(n,t)});
wifiOn=(s.wifi||{}).enabled;G('tog').textContent=wifiOn===false?'Turn Wi-Fi on':'Turn Wi-Fi off';
G('disc').hidden=(s.wifi||{}).state!=='connected';lock()}
function nets(r,quiet){var u=G('nets');if(r.error){if(!quiet){u.textContent='';say(r.error,true)}return}u.textContent='';if(!quiet)say(r.message);
function add(n,t){var l=document.createElement('li'),b=document.createElement('button');b.type='button';b.textContent=t;b.onclick=function(){pick(n)};l.appendChild(b);u.appendChild(l)}
(r.networks||[]).forEach(function(n){add(n,n.ssid+' - '+n.label)});
(r.saved||[]).forEach(function(s){add({ssid:s,known:true,secure:true,supported:true},s+' - saved, not in range')});
if(!u.firstChild)li(u,'No networks found. Tap Scan.')}
function pick(n){cur=n;var k=!!n.known,a=!!n.active,sec=n.secure!==false,sup=n.supported!==false,h=!!n.hidden;
G('sel').hidden=false;G('sel-t').textContent=h?'Hidden network':n.ssid;G('hrow').hidden=!h;G('ssid').value='';
G('prow').hidden=G('srow').hidden=a||!sup||!sec;G('pwl').textContent=k?'New password (leave empty to use the saved one)':'Password';
G('b-join').hidden=a||!sup;G('b-join').textContent=k?'Connect':'Join';G('b-forget').hidden=!k;G('b-disc').hidden=!a;
G('sel-n').textContent=!sup?'This kind of network is not supported yet.':!sec&&!a?'Open network: anyone nearby can read what you send over it.':k?'Saved on this phone.':'';
G('pw').value='';G('show').checked=false;G('pw').type='password';lock();
try{G(h?'ssid':sec&&!k&&sup&&!a?'pw':'b-cancel').focus()}catch(e){}}
function work(t,o,after){busy=true;lock();say(t);return api(o).then(function(r){busy=false;lock();say(r.error||r.message,!!r.error||r.ok===false);if(after)after(r);return r})}
function scan(re,quiet){busy=re;lock();if(!quiet)say(re?'Scanning...':'');return api({action:'net_scan',rescan:re}).then(function(r){busy=false;lock();nets(r,quiet)})}
function refresh(){return api({action:'net_status'}).then(paint)}
G('b-join').onclick=function(){var pw=G('pw').value,ssid=cur.hidden?G('ssid').value:cur.ssid,o={action:'net_join',ssid:ssid};G('pw').value='';
if(pw)o.password=pw;if(cur.hidden)o.hidden=true;
work('Joining '+ssid+'. This can take up to 40 seconds...',o,function(r){if(r.ok)G('sel').hidden=true;refresh().then(function(){scan(false,true)})})};
G('b-forget').onclick=function(){work('Forgetting '+cur.ssid+'...',{action:'net_forget',ssid:cur.ssid},function(r){if(r.ok)G('sel').hidden=true;refresh().then(function(){scan(false,true)})})};
function disc(){work('Disconnecting...',{action:'net_wifi',mode:'disconnect'},function(){G('sel').hidden=true;refresh().then(function(){scan(false,true)})})}
G('b-disc').onclick=G('disc').onclick=disc;
G('b-cancel').onclick=function(){G('pw').value='';G('sel').hidden=true};
G('tog').onclick=function(){work('Changing Wi-Fi...',{action:'net_wifi',mode:wifiOn===false?'on':'off'},function(){refresh().then(function(){scan(false,true)})})};
G('b-wifi-restart').onclick=function(){work('Restarting Wi-Fi...',{action:'net_restart',mode:'wifi'},function(){refresh().then(function(){scan(false,true)})})};
G('b-modem-restart').onclick=function(){work('Restarting the modem...',{action:'net_restart',mode:'modem'},function(){refresh()})};
G('scan').onclick=function(){scan(true).then(refresh)};
G('hid').onclick=function(){pick({hidden:true,secure:true,supported:true,ssid:''})};
G('show').onchange=function(){G('pw').type=G('show').checked?'text':'password'};
G('pw').onkeydown=function(e){if(e.key==='Enter')G('b-join').click()};
refresh().then(function(){scan(false)});
setInterval(function(){if(!document.hidden&&!busy&&G('sel').hidden)refresh()},10000);
})();
</script></body></html>"""


_NET_CSS = """
header{display:flex;align-items:center;gap:16px;padding:12px 16px;border-bottom:3px solid var(--fg)}
header h1{border:0;padding:0;margin:0}
main{display:block;padding:12px 16px}
section{padding:8px 0}
section+section{border-top:1px solid var(--fg)}
#gap{display:none}
@media (min-width:1000px){main{display:grid;grid-template-columns:minmax(0,1fr) var(--hinge) minmax(0,1fr);padding:12px 0}#gap{display:block}main>section{padding:8px 16px;border-top:0}}
ul{list-style:none;margin:0;padding:0}
#st li{padding:8px 0;border-bottom:1px solid var(--fg)}
#nets button{display:block;width:100%;text-align:left;margin:8px 0}
input[type=password],input[type=text]{width:100%;min-height:48px;margin:8px 0;padding:8px 12px;background:var(--bg);color:var(--fg);border:1px solid var(--fg);border-radius:0;font:inherit}
label{display:block;margin:8px 0}
#srow{display:flex;align-items:center;gap:12px;min-height:48px}
input[type=checkbox]{-webkit-appearance:none;appearance:none;flex:none;width:24px;height:24px;margin:0;border:2px solid var(--fg);border-radius:0;background:var(--bg);color:var(--fg);accent-color:var(--fg)}
input[type=checkbox]:checked{background:var(--fg)}
#sel{border:1px solid var(--fg);padding:12px;margin-top:12px}
#msg{min-height:1.4em;font-weight:700}#msg[data-bad]{border:3px double var(--fg);padding:8px 12px}
button:disabled::after{content:" unavailable"}
.note:empty{display:none}
"""


def render(token, nonce):
    """The page, with the session token and the CSP nonce on its inline style and script."""
    return secure.add_nonce(PAGE.replace("@@CSS@@", _BASE + _NET_CSS).replace("@@TOKEN@@", escape(token, quote=True)), nonce)
