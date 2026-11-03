# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import re

_OPOS = pathlib.Path(__file__).resolve().parent.parent / "src" / "opos"

_pattern = re.compile(r"@@OPOS:([A-Za-z0-9_.-]+)@@")

def web_text():
    text = (_OPOS / "web.py").read_text(encoding="utf-8")
    return re.sub(_pattern, lambda m: (_OPOS / "web_static" / m.group(1)).read_text(encoding="utf-8"), text)
