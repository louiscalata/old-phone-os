# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from pathlib import Path


class RestartErrorTest(unittest.TestCase):
    def test_restart_shows_error(self):
        src = (Path(__file__).resolve().parent.parent / "src" / "opos" / "web.py").read_text(encoding="utf-8")
        marker = "document.getElementById('helperrestart').onclick"
        start = src.find(marker)
        self.assertNotEqual(start, -1)
        end = src.find("\n", start)
        handler = src[start:end] if end != -1 else src[start:]
        self.assertNotIn("api({action:'restart_helper'}).catch(()=>{});setTimeout(()=>location.reload(),6000)", handler)
        self.assertIn(".then(", handler)
        err = handler.find("r&&r.error")
        self.assertNotEqual(err, -1)
        assign = handler.find("if(ans)ans.textContent=r.error", err)
        self.assertNotEqual(assign, -1)
        ret = handler.find("return", assign)
        self.assertNotEqual(ret, -1)
        close = handler.find("}", ret)
        self.assertNotEqual(close, -1)
        frag = "setTimeout(()=>location.reload(),6000)"
        after = handler.find(frag, close)
        self.assertNotEqual(after, -1)
        catch = handler.find(".catch(", err)
        self.assertNotEqual(catch, -1)
        self.assertLess(after, catch)
        self.assertNotEqual(handler.find(frag, catch), -1)
