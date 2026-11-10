# SPDX-License-Identifier: GPL-3.0-or-later
"""Newsreader and Inter Tight, both SIL Open Font License.

The files are /fonts.css and /fonts/<file>. They are not inlined into page(). The sheet uses font-display:swap
and a size-adjusted local face. The stacks in jaryo.css still name a system font, so the page is readable
when the files are missing.
"""
from pathlib import Path

from opos import secure

_STATIC = Path(__file__).with_name("web_static")
_DIR = _STATIC / "fonts"
_TYPE = {".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}


def serve(handler, path):
    """Answer GET/HEAD for the font sheet and the font files. False for every other path."""
    if path == "/fonts.css":
        body, ctype = (_STATIC / "fonts.css").read_bytes(), "text/css; charset=utf-8"
    elif path.startswith("/fonts/"):
        name = path[len("/fonts/"):]
        if not name or "/" in name or name.startswith("."):
            return False
        file = _DIR / name
        if not file.is_file():
            return False
        body, ctype = file.read_bytes(), _TYPE.get(file.suffix, "application/octet-stream")
    else:
        return False
    handler.send_response(200)
    for key, value in secure.security_headers().items():
        handler.send_header(key, value)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(body)
    return True


def inject(body, nonce):
    """The page bytes plus one stylesheet link. The same bytes object when the link is already there."""
    if not isinstance(body, (bytes, bytearray)) or b'href="/fonts.css"' in body or not secure._nonce_ok(nonce):
        return body
    tag = f'<link rel="stylesheet" href="/fonts.css" nonce="{nonce}">'.encode()
    at = body.find(b"</head>")
    if at < 0:
        return body
    return body[:at] + tag + body[at:]
