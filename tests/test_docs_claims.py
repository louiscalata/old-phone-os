# SPDX-License-Identifier: GPL-3.0-or-later
"""Policy, simulation and package claims in the public docs, checked against the code."""
import re
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from opos import eol, profiles, runtime, web
from opos.battery import essentials_reserve
from opos.evaluate import EVAL_TASKS, router_policy, rules_policy
from opos.governor import Mode, PowerState, rule_decide
from opos.reserve import apply_reserve
from opos.router import HOT_C
from tests.test_published_numbers import published_profile_minutes, readme_section, readme_table_values

ROOT = Path(__file__).resolve().parents[1]
SAFETY = "docs/design-safety-floor.md"
POLICY_DOCS = ("README.md", SAFETY, "docs/META.md")
# The words each policy doc uses for every hard floor in runtime.HARD_FLOORS.
FLOOR_WORDS = {
    "README.md": (
        "at 45 C or more only essential tasks run, charging or not",
        "on battery, with less than 15% of the charge usable (the battery rules' CRITICAL mode), only essentials run",
        "below a small reserve kept for calls (at least 0.7% charge) only essentials run, even while charging",
        "the worn-battery reserve, which engages only when battery health is below 50%",
    ),
    SAFETY: (
        "At or above 45 C (`HOT_C` in `src/opos/router.py`) only essential tasks run, charging or not.",
        "On battery, when less than 15 percent of the charge is usable (charge times battery health), "
        "the battery rules' mode is CRITICAL and only essential tasks run.",
        "Below the reserve kept for calls, only essential tasks run, even while charging. "
        "The reserve is 0.7 percent of charge",
        "It engages only when battery health is below 50 percent",
    ),
    "docs/META.md": (
        "at or above 45 C (HOT_C) only ESSENTIAL tasks run, charging or not",
        "on battery, when less than 15 percent of the charge is usable (rule_decide's CRITICAL mode), only ESSENTIAL tasks run",
        "below the reserve kept for calls (opos.battery.essentials_reserve, at least 0.7 percent of charge) "
        "only ESSENTIAL tasks run, even while charging",
        "which engages only when battery health is below 50 percent",
    ),
}
# Present exactly when a goal or affordability CRITICAL stays soft in the runtime.
SOFT_CRITICAL = "Those pauses are soft: only the battery rules' CRITICAL mode is a hard floor."
RESERVE_IN_TABLE = ("The router figures in the table come from the full runtime, so in the worn and "
                    "end-of-life profiles, where battery health can be below 50%, they include the "
                    "worn-battery reserve's task pauses.")
ROUTER_BEFORE = "(the router's runs include the task pauses both times)"
DESCRIPTION = ("Old Phone OS Interface: a prototype phone interface that pauses less essential work "
               "so the battery can last until a time the owner chooses")


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def flat(text):
    return " ".join(text.split())


def demo_undo_at_40():
    """Demo battery at 40% with the 16:00 goal: the mode before and after Undo of email."""
    phone = web.Phone()
    phone.dash.set_goal(web.DEMO_GOAL)
    phone.sim.level = 40.0
    before = phone._tick()
    phone.undo("email")
    return before, phone._tick()


def no_reserve():
    """Run the runtime without the worn-battery reserve."""
    return (patch.object(runtime, "apply_reserve", lambda decision, *args, **kwargs: decision),
            patch.object(runtime, "reserve_keeps", lambda *args, **kwargs: None))


class TestPolicyDocs(unittest.TestCase):
    def test_every_hard_floor_is_named_with_the_code_threshold(self):
        # A new floor in the runtime must be described before this passes again.
        self.assertEqual(runtime.HARD_FLOORS, ("reserve", "hot", "low"))
        self.assertEqual((HOT_C, eol.WORN_HEALTH), (45, 0.5))
        self.assertAlmostEqual(essentials_reserve(None), 0.7)
        self.assertEqual(rule_decide(PowerState(30.0, False, 0.5, 25.0), ()).mode, Mode.SAVER)
        self.assertEqual(rule_decide(PowerState(29.9, False, 0.5, 25.0), ()).mode, Mode.CRITICAL)
        self.assertEqual(rule_decide(PowerState(5.0, True, 0.5, 25.0), ()).mode, Mode.NORMAL)
        for name, phrases in FLOOR_WORDS.items():
            text = flat(read(name))
            for phrase in phrases:
                with self.subTest(doc=name, phrase=phrase[:40]):
                    self.assertIn(phrase, text)

    def test_low_floor_holds_against_undo_and_foreground_use(self):
        tick = runtime.Runtime().step(0, 20.0, health=0.6, undone=("email",), in_use="maps")
        self.assertEqual(tick.decision.allowed, ("calls", "messages"))
        self.assertEqual(dict(tick.causes), {"email": "low", "maps": "low", "photos backup": "low"})

    def test_critical_wording_follows_the_runtime(self):
        before, after = demo_undo_at_40()
        self.assertEqual(before.decision.mode, Mode.CRITICAL)
        self.assertEqual(before.held, ())
        soft_critical = after.decision.mode == Mode.CRITICAL and "email" in after.decision.allowed
        for name in POLICY_DOCS:
            text = flat(read(name))
            with self.subTest(doc=name):
                if soft_critical:
                    self.assertNotRegex(text, r"(?i)CRITICAL mode runs only essential")
                    self.assertNotIn("CRITICAL (only essentials)", text)
        self.assertEqual(SOFT_CRITICAL in flat(read(SAFETY)), soft_critical)
        for name in ("README.md", "docs/META.md"):
            with self.subTest(doc=name):
                self.assertEqual("(even when the router reports critical)" in flat(read(name)).lower(), soft_critical)

    def test_calls_reserve_holds_while_charging(self):
        phone = web.Phone()
        phone.dash.set_goal(web.DEMO_GOAL)
        phone.charging = True
        phone.sim.level = 0.5
        phone.undo("email")
        tick = phone._tick()
        self.assertEqual(tick.decision.mode, Mode.NORMAL)
        self.assertEqual(tick.decision.allowed, ("calls", "messages"))
        self.assertEqual(tick.held, ("email", "maps", "photos backup"))
        # A hard floor can pause every non-essential task while the mode reads NORMAL.
        self.assertNotIn("SAVER (something paused) or NORMAL", flat(read("README.md")))
        self.assertIn("so the worn-battery reserve appears only in the simulations", flat(read(SAFETY)))


class TestSimulationDocs(unittest.TestCase):
    def test_profile_table_includes_worn_reserve(self):
        readme = read("README.md")
        section = flat(readme_section(readme))
        self.assertIn(RESERVE_IN_TABLE, section)
        self.assertNotIn("router alone", section)
        table = readme_table_values(readme)
        first, second = no_reserve()
        with first, second:
            for profile in ("worn", "eol"):
                result = profiles.benchmark(router_policy, profile)
                with self.subTest(profile=profile):
                    self.assertNotEqual(published_profile_minutes(result["important_min"]),
                                        table[profile, "router", "important_min"])

    def test_worn_simulation_router_runs_include_task_pauses(self):
        state = PowerState(5.0, False, 0.35, 25.0)
        router = router_policy()(state, EVAL_TASKS)
        rules = rules_policy(state, EVAL_TASKS)
        shaped = apply_reserve(router, 5.0, 25.0, 0.35, EVAL_TASKS)
        self.assertEqual(set(shaped.allowed), set(router.allowed))
        self.assertNotEqual(set(apply_reserve(rules, 5.0, 25.0, 0.35, EVAL_TASKS).allowed), set(rules.allowed))
        self.assertIn(ROUTER_BEFORE, flat(readme_section(read("README.md"))))
        self.assertIn("(the router's runs include the reserve's task pauses both times)", flat(read("docs/META.md")))
        for name in ("README.md", "docs/META.md"):
            with self.subTest(doc=name):
                self.assertNotIn("Without the reserve every simulated phone browned out", read(name))


class TestReleaseDocs(unittest.TestCase):
    def test_physical_phone_claim_is_scoped_to_this_source(self):
        roadmap = flat(read("ROADMAP.md"))
        self.assertIn("This source has not been tested on a physical phone.", roadmap)
        self.assertNotIn("OPOS has not been tested on a physical phone", roadmap)
        if "earlier Surface Duo installation report" in flat(read("docs/pmos.md")):
            self.assertIn("Earlier Surface Duo installs ran an older snapshot on Droidian", roadmap)

    def test_package_metadata(self):
        with open(ROOT / "pyproject.toml", "rb") as handle:
            data = tomllib.load(handle)
        project = data["project"]
        self.assertEqual(data["build-system"]["build-backend"], "setuptools.build_meta")
        self.assertEqual((project["name"], project["version"], project["requires-python"], project["license"]),
                         ("opos", "0.2.0", ">=3.11", "GPL-3.0-or-later"))
        self.assertEqual(project["dependencies"], [])
        # The package says what the README says, no more.
        self.assertEqual(project["description"], DESCRIPTION)
        intro = flat(read("README.md"))
        self.assertIn("It pauses less essential work", intro)
        self.assertIn("toward a time the owner chooses", intro)


if __name__ == "__main__":
    unittest.main()
