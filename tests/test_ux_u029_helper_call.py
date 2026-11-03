# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests.web_source import web_text


class TestHelperCountdown(unittest.TestCase):
    def test_countdown_names_activity(self):
        text = web_text()
        start = text.index("async function helperCall(o,ans)")
        end = text.index("function helperReset()", start)
        snippet = text[start:end]
        default_decl = "let act='Thinking'"
        web_sel = "if(o.web){act='Searching Wikipedia and thinking'}"
        plan_sel = "else if(o.action==='plan'){act='Planning'}"
        explain_sel = "else if(o.action==='explain'){act='Explaining'}"
        first_assign = "(act==='Thinking'?'Thinking... about ':act+'... about ')+(expected-elapsed)+' s left'"
        second_assign = "(act==='Thinking'?'Still thinking... ':'Still '+act.toLowerCase()+'... ')+elapsed+' s so far'"
        self.assertIn(default_decl, snippet)
        self.assertIn(web_sel, snippet)
        self.assertIn(plan_sel, snippet)
        self.assertIn(explain_sel, snippet)
        self.assertIn(first_assign, snippet)
        self.assertIn(second_assign, snippet)
        self.assertIn("'Thinking... about '", snippet)
        self.assertIn("'Still thinking... '", snippet)
        i_default = snippet.index(default_decl)
        i_web = snippet.index(web_sel)
        i_plan = snippet.index(plan_sel)
        i_explain = snippet.index(explain_sel)
        i_tick = snippet.index("function tick()")
        i_first = snippet.index(first_assign)
        i_second = snippet.index(second_assign)
        self.assertLess(i_default, i_web)
        self.assertLess(i_web, i_plan)
        self.assertLess(i_plan, i_explain)
        self.assertLess(i_explain, i_tick)
        self.assertLess(i_tick, i_first)
        self.assertLess(i_first, i_second)


if __name__ == "__main__":
    unittest.main()
