# SPDX-License-Identifier: GPL-3.0-or-later
"""Credit actual Glimmer replies without mistaking configuration for activity."""
import io
import json
import shutil
import subprocess
import threading
import unittest
from pathlib import Path
from unittest import mock

from opos import assist, muse
from opos.advisor import GlimmerAdvisor
from opos.web import Handler, Phone, page


STATIC = Path(__file__).resolve().parents[1] / "src/opos/web_static"
GLIMMER = muse.glimmer_route({})
LOCAL = assist.local_route({"OPOS_LOCAL_URL": "http://127.0.0.1:8080/v1"}, free=2000)  # configured: no /models check
STEPS = ["dim screen", "pause email"]


def post_plan(phone):
    """Exercise real POST validation and dispatch with no server or sockets."""
    handler = Handler.__new__(Handler)
    handler.path = "/api"
    handler.port = 8765
    handler.token = "t" * 32
    body = json.dumps({"action": "plan"}).encode()
    handler.headers = {"Host": "127.0.0.1:8765", "Content-Type": "application/json",
                       "X-OPOS-Token": handler.token, "Content-Length": str(len(body))}
    handler.rfile = io.BytesIO(body)
    handler.phone = phone
    handler.lock = threading.Lock()
    handler.limiter = mock.Mock()
    handler.limiter.allow.return_value = True
    handler._json = mock.Mock()
    handler._post()
    handler._json.assert_called_once()
    return handler._json.call_args.args[0]


def plan_reply(answers, steps=STEPS, routes=None):
    """Keep muse.plan and response parsing real; replace only network I/O."""
    phone = Phone(visible=True)
    replies = [answer if isinstance(answer, Exception) else io.BytesIO(json.dumps(
        {"choices": [{"message": {"content": answer}}]}).encode()) for answer in answers]
    with mock.patch.object(phone, "plan_routes", return_value=({}, steps, routes or [GLIMMER])), \
            mock.patch.object(muse, "_open_no_redirect", side_effect=replies) as opener:
        result = post_plan(phone)
    return result, opener


class TestPlanCredit(unittest.TestCase):
    def test_same_order_credits_the_glimmer_reply(self):
        result, opener = plan_reply([json.dumps(STEPS)])
        self.assertEqual(result, {"order": STEPS, "engine": "Muse Glimmer",
                                 "where": "on this device", "credit": "Muse Glimmer kept this order"})
        self.assertEqual(opener.call_count, 1)

    def test_changed_order_says_changed(self):
        result, _ = plan_reply([json.dumps(STEPS[::-1])])
        self.assertEqual(result["order"], STEPS[::-1])
        self.assertEqual(result["credit"], "Muse Glimmer changed this order")

    def test_timeout_keeps_battery_rules_credit(self):
        result, _ = plan_reply([TimeoutError("timed out")])
        self.assertEqual(result, {"order": STEPS, "engine": "fixed order",
                                 "where": "battery rules", "credit": "Battery rules kept this order"})

    def test_unusable_answers_do_not_claim_glimmer_kept_the_order(self):
        for answer in ("no list", "[]", '["unknown"]', "[1, 2]"):
            with self.subTest(answer=answer):
                result, _ = plan_reply([answer])
                self.assertEqual(result["order"], STEPS)
                self.assertEqual(result["credit"], "Battery rules kept this order")
                self.assertEqual(result["engine"], "fixed order")

    def test_failed_first_route_credits_the_route_that_answers(self):
        result, opener = plan_reply([OSError("unavailable"), json.dumps(STEPS)], routes=[LOCAL, GLIMMER])
        self.assertEqual(result["engine"], "Muse Glimmer")
        self.assertEqual(result["credit"], "Muse Glimmer kept this order")
        self.assertEqual([call.args[0].full_url for call in opener.call_args_list],
                         [r.base_url + "/chat/completions" for r in (LOCAL, GLIMMER)])

    def test_unusable_first_route_can_fall_back_to_glimmer(self):
        result, opener = plan_reply(['["unknown"]', json.dumps(STEPS)], routes=[LOCAL, GLIMMER])
        self.assertEqual(result["credit"], "Muse Glimmer kept this order")
        self.assertEqual(opener.call_count, 2)

    def test_a_different_answering_route_is_not_called_glimmer(self):
        result, _ = plan_reply([json.dumps(STEPS)], routes=[LOCAL, GLIMMER])
        self.assertEqual(result["engine"], LOCAL.name)
        self.assertEqual(result["credit"], LOCAL.name + " kept this order")
        self.assertEqual(result["where"], LOCAL.where)

    def test_empty_steps_do_not_call_a_helper(self):
        result, opener = plan_reply([], steps=[])
        opener.assert_not_called()
        self.assertEqual(result["order"], [])
        self.assertEqual(result["credit"], "Battery rules kept this order")

    def test_hidden_helper_does_not_plan_or_claim_a_reply(self):
        with mock.patch.object(muse, "plan") as plan:
            result = post_plan(Phone(visible=False))
        plan.assert_not_called()
        self.assertEqual(result, {"error": "helper hidden"})

    def test_one_step_reply_still_receives_credit(self):
        result, opener = plan_reply(['["dim screen"]'], steps=["dim screen"])
        self.assertEqual(result["credit"], "Muse Glimmer kept this order")
        self.assertEqual(opener.call_count, 1)


HARNESS = r"""
const fs=require('fs'),vm=require('vm'),input=JSON.parse(fs.readFileSync(0,'utf8'));
const ids={};for(const id of ['askbtn','planbtn','answer','glimmer-credit'])ids[id]={textContent:''};
const window={},G=id=>ids[id],events={addEventListener(){}};let reply,pending,release;
const ctx={window,document:{getElementById:G},G,meterEl:events,hitEl:events,vis:true,helperCall:async()=>reply};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
function state(s){ctx.vis=!!s.helper_visible;window.oposHooks.forEach(f=>f(s));return ids['glimmer-credit'].textContent}
(async()=>{
 const lines=input.states.map(state),plans=[],planLines=[],eventLines=[];
 for(const r of input.plans){reply=r;await ids.planbtn.onclick();plans.push(ids.answer.textContent);planLines.push(ids['glimmer-credit'].textContent)}
 for(const event of input.events){
  if(event.state)state(event.state);
  if('plan' in event){reply=event.plan;await ids.planbtn.onclick()}
  if(event.start){reply=new Promise(r=>release=r);pending=ids.planbtn.onclick()}
  if('finish' in event){release(event.finish);await pending}
  eventLines.push(ids['glimmer-credit'].textContent);
 }
 console.log(JSON.stringify({lines,plans,planLines,eventLines}));
})().catch(e=>{console.error(e);process.exitCode=1});
"""


@unittest.skipUnless(shutil.which("node"), "node missing")
class TestContributionLine(unittest.TestCase):
    def render(self, states=(), plans=(), events=()):
        result = subprocess.run(["node", "-e", HARNESS, str(STATIC / "js-2.js")],
                                input=json.dumps({"states": states, "plans": plans, "events": events}),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_actual_state_transitions_and_undo_keep_the_line_honest(self):
        phone = Phone(visible=True)
        base = phone.runtime.governor.advisor
        advisor = GlimmerAdvisor(base, route=GLIMMER, ask=lambda *a, **k: '{"defer":["email"]}',
                                 spawn=lambda fn: fn())
        phone.runtime.governor.advisor = advisor
        with mock.patch.object(assist, "free_mb", return_value=None):
            before = phone.state()
            extra = phone.state()
            self.assertIn("email", extra["helper"]["paused"])
            self.assertTrue(extra["battery"]["why"].startswith("router+glimmer"))
            phone.dash.undo("email")
            undone = phone.state()
            self.assertNotIn("email", undone["helper"]["paused"])
            phone.runtime.governor.advisor = base
            rules = phone.state()
            phone.set_visible(False)
            hidden = phone.state()
        self.assertEqual(self.render([before, extra, undone, rules, hidden])["lines"], [
            "Glimmer: no extra pauses", "Glimmer: suggested extra pauses",
            "Glimmer: no extra pauses", "Glimmer: no extra pauses", "Glimmer: helper hidden"])

    def test_configuration_and_unrelated_reason_do_not_claim_a_contribution(self):
        helper = {"engine": "Muse Glimmer, on this device", "source": "router"}
        states = [{"helper": helper}, {"helper": helper, "battery": {}},
                  {"helper": helper, "battery": {"why": "router+glimmerish"}}, {}]
        self.assertEqual(self.render(states)["lines"],
                         ["Glimmer: no extra pauses"] * 3 + ["Glimmer: helper hidden"])

    def test_plan_ui_uses_server_credit_for_kept_changed_and_rules(self):
        kept, _ = plan_reply([json.dumps(STEPS)])
        changed, _ = plan_reply([json.dumps(STEPS[::-1])])
        fallback, _ = plan_reply([TimeoutError("timed out")])
        self.assertEqual(self.render(plans=[kept, changed, fallback, {"error": "helper hidden"}])["plans"], [
            "Plan: dim screen > pause email. Muse Glimmer kept this order (on this device)",
            "Plan: pause email > dim screen. Muse Glimmer changed this order (on this device)",
            "Plan: dim screen > pause email. Battery rules kept this order (battery rules)",
            "Tap Show helper first"])

    def test_plan_line_survives_unchanged_polls_and_resets_on_a_new_decision(self):
        kept, _ = plan_reply([json.dumps(STEPS)])
        changed, _ = plan_reply([json.dumps(STEPS[::-1])])
        phone = Phone(visible=True)
        with mock.patch.object(assist, "free_mb", return_value=None):
            before = phone.state()
            phone.runtime.governor.advisor = GlimmerAdvisor(
                phone.runtime.governor.advisor, route=GLIMMER,
                ask=lambda *a, **k: '{"defer":["email"]}', spawn=lambda fn: fn())
            phone.state()
            after = phone.state()
        events = [{"state": before}, {"plan": kept}, {"state": before},
                  {"plan": changed}, {"state": before}, {"state": after}]
        self.assertEqual(self.render(events=events)["eventLines"], [
            "Glimmer: no extra pauses", "Glimmer: kept the plan order", "Glimmer: kept the plan order",
            "Glimmer: changed the plan order", "Glimmer: changed the plan order", "Glimmer: suggested extra pauses"])

    def test_hiding_clears_the_previous_plan_line(self):
        kept, _ = plan_reply([json.dumps(STEPS)])
        phone = Phone(visible=True)
        visible = phone.state()
        phone.set_visible(False)
        hidden = phone.state()
        events = [{"state": visible}, {"plan": kept}, {"state": hidden}, {"state": visible}]
        self.assertEqual(self.render(events=events)["eventLines"], [
            "Glimmer: no extra pauses", "Glimmer: kept the plan order",
            "Glimmer: helper hidden", "Glimmer: no extra pauses"])

    def test_a_plan_finishing_after_hiding_does_not_replace_the_hidden_line(self):
        kept, _ = plan_reply([json.dumps(STEPS)])
        phone = Phone(visible=True)
        visible = phone.state()
        phone.set_visible(False)
        hidden = phone.state()
        events = [{"state": visible}, {"start": True}, {"state": hidden},
                  {"finish": kept}, {"state": visible}]
        self.assertEqual(self.render(events=events)["eventLines"], [
            "Glimmer: no extra pauses", "Glimmer: no extra pauses", "Glimmer: helper hidden",
            "Glimmer: helper hidden", "Glimmer: no extra pauses"])

    def test_rules_and_other_routes_do_not_claim_a_glimmer_plan_reply(self):
        kept, _ = plan_reply([json.dumps(STEPS)])
        fallback, _ = plan_reply([TimeoutError("timed out")])
        local, _ = plan_reply([json.dumps(STEPS)], routes=[LOCAL])
        self.assertEqual(self.render(plans=[kept, fallback, kept, local])["planLines"], [
            "Glimmer: kept the plan order", "Glimmer: no plan reply",
            "Glimmer: kept the plan order", "Glimmer: no plan reply"])

    def test_contribution_line_is_present_before_first_state(self):
        with mock.patch("opos.notices.BUILD", "f" * 12):
            markup = page("t" * 43, "n" * 22, demo=True)
        self.assertIn('id="glimmer-credit"', markup)
        self.assertIn("Glimmer: waiting for phone</p>", markup)


if __name__ == "__main__":
    unittest.main()
