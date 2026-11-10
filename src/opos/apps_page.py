# SPDX-License-Identifier: GPL-3.0-or-later
"""GET /apps and the More apps link. The main page is unchanged unless OPOS_APPS names an app.

A raised app handler becomes a short error page with Home. The front page and Phone are not part of that handler.
"""
import re
import secrets
from html import escape
from pathlib import Path

from opos import appdoc, apps, secure

# One short tag. The script file holds the link and later home behaviour, so the main page stays under the gate.
_INJECT = '<script nonce="{nonce}" src="/apps-home.js"></script>'
_HOME_JS = Path(__file__).with_name("apps_static") / "home.js"
_BODY_END = re.compile(r"</body\s*>", re.I)


def buckets():
    return {"apps": secure.RateLimiter(10.0, 80)}


def inject(html, nonce, env=None):
    """html plus the More apps link, or html unchanged when no app is named."""
    if not apps.switched_on(env) or 'src="/apps-home.js"' in html:
        return html
    if not isinstance(nonce, str) or not secure._nonce_ok(nonce):
        return html
    block = _INJECT.format(nonce=nonce)
    found = list(_BODY_END.finditer(html))
    if not found:
        return html + block
    at = found[-1].start()
    return html[:at] + block + html[at:]


def inject_bytes(body, nonce, env=None):
    """The same bytes object when OPOS_APPS names nothing."""
    if not apps.switched_on(env):
        return body
    return inject(body.decode("utf-8"), nonce, env).encode("utf-8")


def _li(item):
    card = item.card()
    safe_title = escape(item.title)
    safe_card = escape(card)
    summary = getattr(item, "summary", "") or ""
    extra = ""
    if summary and summary != card:
        extra = "<p>%s</p>" % escape(summary)
    if getattr(item, "path", ""):
        link = '<a href="%s" data-card="%s">%s</a>' % (escape(item.path, quote=True), escape(card, quote=True), safe_title)
    else:
        link = "<p>%s</p>" % safe_title
    return "<li>%s<p class=\"card\">%s</p>%s</li>" % (link, safe_card, extra)


def index_html(token, nonce, env=None):
    """The A to Z index. Each real app carries its disclosure card."""
    items = apps.rows(env)
    if items:
        lis = "".join(_li(item) for item in items)
    else:
        lis = "<li><p>No extra apps are on.</p></li>"
    thumb = "<h2>About these apps</h2><p id=\"card\">Each app works on this phone. Nothing here needs an account.</p>"
    script = """
var list=document.getElementById('app-list');
if(list)list.addEventListener('click',function(e){
var a=e.target.closest&&e.target.closest('a[data-card]');
var card=document.getElementById('card');
if(a&&card)card.textContent=a.getAttribute('data-card')||'';
});
"""
    return appdoc.render("Apps", token, nonce, "<h1>Apps</h1><ul id=\"app-list\">%s</ul>" % lis, thumb, script)


def _claims(path):
    if not apps.switched_on():
        return False
    if path == "/apps":
        return True
    if apps.by_path(path) is not None:
        return True
    api = _api_app(path)
    return api is not None and api.get_json is not None


def _api_app(path):
    """The app whose API is /api/<id>, or None."""
    if not path.startswith("/api/"):
        return None
    name = path[5:]
    if "/" in name:
        return None
    apps.load_enabled()
    return apps.by_id(name)


def is_post(path):
    return apps.switched_on() and _api_app(path) is not None and getattr(_api_app(path), "post", None) is not None


def _send_html(handler, html, status, nonce):
    body = html.encode("utf-8")
    try:
        from opos import osk
    except ImportError:
        osk = None
    # html from appdoc.render already carries this nonce. Do not add it again.
    if osk is not None:
        try:
            body = osk.inject_bytes(body, nonce)
        except Exception:
            pass
    handler.send_response(status)
    for key, value in secure.security_headers(nonce).items():
        handler.send_header(key, value)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(body)


def _send_json(handler, obj, status=200):
    handler._json(obj, status)


def _send_static(handler, body, content_type):
    handler.send_response(200)
    for key, value in secure.security_headers().items():
        handler.send_header(key, value)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(body)


def serve_get(handler, path):
    """Answer an app GET. False means this path is not an app path."""
    if path == "/apps-home.js":
        if not apps.switched_on():
            return False
        try:
            body = _HOME_JS.read_bytes()
        except OSError:
            return False
        _send_static(handler, body, "text/javascript; charset=utf-8")
        return True
    if not _claims(path):
        return False
    nonce = secrets.token_urlsafe(16)
    try:
        if path == "/apps":
            _send_html(handler, index_html(handler.token, nonce), 200, nonce)
            return True
        app = apps.by_path(path)
        if app is not None:
            _send_html(handler, app.render(handler.token, nonce), 200, nonce)
            return True
        api = _api_app(path)
        if api is not None and api.get_json is not None:
            if not secure.token_ok(handler.headers.get("X-OPOS-Token"), handler.token):
                handler._deny(403, "forbidden", "token")
                return True
            _send_json(handler, api.get_json())
            return True
    except Exception:
        try:
            nonce = secrets.token_urlsafe(16)
            _send_html(handler, appdoc.error_page(getattr(handler, "token", ""), nonce), 500, nonce)
        except Exception:
            return False
        return True
    return False


def post(handler, path, data):
    """Answer POST /api/<app>. The caller has already checked the token and the body."""
    app = _api_app(path)
    if app is None or app.post is None:
        handler._json({"error": "not found"}, 404)
        return
    if not handler.limiter.allow("apps"):
        handler._deny(429, "too many requests", "rate")
        return
    try:
        status, obj = app.post(data)
    except Exception:
        handler._json({"error": "internal error"}, 500)
        return
    if not isinstance(obj, dict) or not isinstance(status, int):
        handler._json({"error": "internal error"}, 500)
        return
    handler._json(obj, status)
