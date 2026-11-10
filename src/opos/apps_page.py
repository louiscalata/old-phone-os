# SPDX-License-Identifier: GPL-3.0-or-later
"""GET /apps and the More apps link. The main page is unchanged unless OPOS_APPS names an app.

A raised app handler becomes a short error page with Home. The front page and Phone are not part of that handler.
"""
import hashlib
import hmac
import re
import secrets
import urllib.parse
from html import escape
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

from opos import appdoc, apps, secure

# One short tag. The script file holds the link and later home behaviour, so the main page stays under the gate.
_INJECT = '<script nonce="{nonce}" src="/apps-home.js"></script>'
_HOME_JS = Path(__file__).with_name("apps_static") / "home.js"
_BODY_END = re.compile(r"</body\s*>", re.I)
_MEDIA_COOKIE = "opos_media"


def _media_grant(token):
    """A media capability for this session. It is not the API token."""
    if not isinstance(token, str) or not token:
        return ""
    try:
        key = token.encode("utf-8")
    except UnicodeError:
        return ""
    return hmac.new(key, b"opos-media", hashlib.sha256).hexdigest()


def _media_cookie_header(token):
    grant = _media_grant(token)
    if not grant:
        return ""
    return "%s=%s; HttpOnly; SameSite=Strict; Path=/media/" % (_MEDIA_COOKIE, grant)


def _cookie(handler, name):
    raw = handler.headers.get("Cookie")
    if not isinstance(raw, str) or not raw:
        return ""
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except CookieError:
        return ""
    found = jar.get(name)
    return found.value if found is not None else ""


def _query_has_api_token(handler):
    """True when any query value is the long-lived API token."""
    expected = getattr(handler, "token", "")
    query = urllib.parse.urlsplit(handler.path).query
    for values in urllib.parse.parse_qs(query, keep_blank_values=True).values():
        for value in values:
            if secure.token_ok(value, expected):
                return True
    return False


def _media_authorized(handler):
    """Header or same-origin media cookie. The API token in the query never counts."""
    expected = getattr(handler, "token", "")
    if secure.token_ok(handler.headers.get("X-OPOS-Token"), expected):
        return True
    grant = _media_grant(expected)
    supplied = _cookie(handler, _MEDIA_COOKIE)
    if not grant or not supplied:
        return False
    try:
        return hmac.compare_digest(supplied.encode("utf-8"), grant.encode("utf-8"))
    except (TypeError, ValueError):
        return False


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
    apps.load_enabled()
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


def _send_html(handler, html, status, nonce, app=None):
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
    headers = secure.security_headers(nonce)
    tweak = getattr(app, "headers", None)
    if callable(tweak):
        try:
            headers = tweak(dict(headers)) or headers
        except Exception:
            pass
    if app is not None and callable(getattr(app, "open_file", None)):
        cookie = _media_cookie_header(getattr(handler, "token", ""))
        if cookie:
            headers["Set-Cookie"] = cookie
    handler.send_response(status)
    for key, value in headers.items():
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


def _observe(handler, app):
    fn = getattr(app, "observe", None)
    if not callable(fn):
        return
    try:
        fn(handler)
    except Exception:
        pass


def _media(handler, path):
    """GET /media/<app>/<id> for a file an app already listed. Anything else is not ours.

    The long-lived API token is refused in the query string. Callers send X-OPOS-Token or the media cookie.
    """
    if not path.startswith("/media/"):
        return False
    if not apps.switched_on():
        return False
    parts = path.strip("/").split("/")
    if len(parts) != 3 or parts[0] != "media":
        return False
    apps.load_enabled()
    app = apps.by_id(parts[1])
    opener = getattr(app, "open_file", None)
    if app is None or not callable(opener):
        return False
    if _query_has_api_token(handler) or not _media_authorized(handler):
        handler._deny(403, "forbidden", "token")
        return True
    opened = opener(parts[2])
    if not opened:
        handler._json({"error": "not found"}, 404)
        return True
    body, content_type = opened
    _send_static(handler, body, content_type)
    return True


def serve_get(handler, path):
    """Answer an app GET. False means this path is not an app path."""
    if _media(handler, path):
        return True
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
            _send_html(handler, app.render(handler.token, nonce), 200, nonce, app)
            return True
        api = _api_app(path)
        if api is not None and api.get_json is not None:
            if not secure.token_ok(handler.headers.get("X-OPOS-Token"), handler.token):
                handler._deny(403, "forbidden", "token")
                return True
            _observe(handler, api)
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
    _observe(handler, app)
    try:
        status, obj = app.post(data)
    except Exception:
        handler._json({"error": "internal error"}, 500)
        return
    if not isinstance(obj, dict) or not isinstance(status, int):
        handler._json({"error": "internal error"}, 500)
        return
    handler._json(obj, status)
