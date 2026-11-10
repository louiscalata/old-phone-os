# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from pathlib import Path


class RestartErrorTest(unittest.TestCase):
    def handler(self):
        src = (Path(__file__).resolve().parent.parent / "src" / "opos" / "web.py").read_text(encoding="utf-8")
        marker = "G('helperrestart').onclick"
        start = src.find(marker)
        self.assertNotEqual(start, -1)
        end = src.find("\n", start)
        return src[start:end] if end != -1 else src[start:]

    def test_restart_shows_error(self):
        handler = self.handler()
        self.assertNotIn("api({action:'restart_helper'}).catch(()=>{});", handler)
        self.assertIn(".then(", handler)
        err = handler.find("r&&r.error")
        self.assertNotEqual(err, -1)
        assign = handler.find("if(ans)ans.textContent=r.error", err)
        self.assertNotEqual(assign, -1)
        ret = handler.find("return", assign)
        self.assertNotEqual(ret, -1)
        close = handler.find("}", ret)
        self.assertNotEqual(close, -1)
        # An error reply returns before the wait starts; any other reply starts it.
        after = handler.find("back()", close)
        self.assertNotEqual(after, -1)
        self.assertLess(assign, ret)
        self.assertLess(ret, after)

    def test_restart_waits_for_the_server_instead_of_a_fixed_delay(self):
        # W1-02: the service restarts (about 5 s on the phone, and not always the same). A page that reloads after a
        # fixed 6 s lands on a connection-refused page when the server is late. The wait asks the server (healReload
        # fetches / and reloads only if it answers) every 2 s, on the reply and on a lost reply alike.
        handler = self.handler()
        self.assertNotIn("setTimeout(()=>location.reload()", handler)
        self.assertNotIn("6000", handler)
        self.assertIn("const back=()=>setInterval(healReload,2000);", handler)
        self.assertIn(".catch(back)", handler)
        self.assertGreater(handler.find(".catch(back)"), handler.find("back()"))
        self.assertEqual(handler.count("back"), 3)  # declared once, called on the reply, named in the catch
