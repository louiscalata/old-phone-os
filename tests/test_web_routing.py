# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
import opos.router as router
from opos.advisor import GlimmerAdvisor
from opos.web import Phone, _route_with_glimmer


class TestWebRouting(unittest.TestCase):
    def test_enabled_wraps_with_glimmer(self):
        phone = Phone(visible=True)
        self.assertIsNone(_route_with_glimmer(phone, {}))
        adv = phone.runtime.governor.advisor
        self.assertIsInstance(adv, GlimmerAdvisor)
        base = getattr(adv, "base", None)
        if base is None:
            base = getattr(adv, "advisor", None)
        if base is None:
            base = getattr(adv, "wrapped", None)
        if base is None:
            base = getattr(adv, "_advisor", None)
        if base is None:
            for v in getattr(adv, "__dict__", {}).values():
                if isinstance(v, router.Router):
                    base = v
                    break
        self.assertIsInstance(base, router.Router)
        visible = getattr(adv, "visible", None)
        if visible is None:
            visible = getattr(base, "visible", None) if base is not None else None
        self.assertEqual(visible, True)

    def test_disabled_keeps_router(self):
        phone = Phone(visible=True)
        self.assertIsNone(_route_with_glimmer(phone, {"OPOS_GLIMMER_ROUTING": "0"}))
        adv = phone.runtime.governor.advisor
        self.assertIsInstance(adv, router.Router)
        self.assertNotIsInstance(adv, GlimmerAdvisor)


if __name__ == "__main__":
    unittest.main()
