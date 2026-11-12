# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only, explicitly simulated volume state; no mixer is connected."""
import json
import os
import re
import tempfile
from pathlib import Path

STATE = Path("/run/opos-volume/state.json")
DEFAULT = 60


def enabled():
    return os.environ.get("OPOS_VOLUME_KEYS") == "1"


def read_percent(path):
    """A missing or invalid report is unknown, never an invented observation."""
    try:
        with Path(path).open(encoding="ascii") as stream:
            data = json.loads(stream.read(128))
        if (isinstance(data, dict) and data.get("simulated") is True
                and type(data.get("percent")) is int and 0 <= data["percent"] <= 100):
            return data["percent"]
    except (OSError, ValueError):
        pass
    return None


def write_percent(path, percent):
    """Atomic publication in the service's root-owned RuntimeDirectory."""
    if type(percent) is not int or not 0 <= percent <= 100:
        raise ValueError("invalid simulated volume")
    path = Path(path)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", dir=path.parent,
                                         prefix=".volume-", delete=False) as stream:
            name = stream.name
            json.dump({"simulated": True, "percent": percent}, stream)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
        os.replace(name, path)
    finally:
        if name is not None:
            Path(name).unlink(missing_ok=True)


def snapshot():
    return {"simulated": True, "percent": read_percent(STATE)}


def inject_bytes(body, nonce):
    """Keep the base page frozen; opt in to the live simulated-volume painter."""
    if not enabled():
        return body
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", nonce) is None:
        raise ValueError("bad nonce")
    # The base page captures barPaint before volume.js wraps it. Only this
    # consumer needs those callbacks to dispatch through the current painter.
    body = body.replace(b"e.addEventListener('input',barPaint)",
                        b"e.addEventListener('input',()=>barPaint())")
    body = body.replace(b"new MutationObserver(barPaint)",
                        b"new MutationObserver(()=>barPaint())")
    tag = f'<script src="/volume.js" nonce="{nonce}" defer></script>'.encode()
    return body.replace(b"</body>", tag + b"</body>")


def serve(handler, path):
    if path != "/volume.js" or not enabled():
        return False
    from opos import secure
    body = (Path(__file__).with_name("web_static") / "volume.js").read_bytes()
    handler.send_response(200)
    for key, value in secure.security_headers().items():
        handler.send_header(key, value)
    handler.send_header("Content-Type", "text/javascript; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(body)
    return True
