# SPDX-License-Identifier: GPL-3.0-or-later
"""The three Duo OS pages keep the Jaryo text-button rules: two colours, square corners, words for labels (docs/jaryo-panes.md section 5)."""
import re
import unittest

from opos import firstrun_page, net_page

TOKENS = {"#1a1a1a", "#f3f0e8", "#e6e2d9", "#000"}  # the two token pairs of jaryo.css, the only colour literals a page may state
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def style_of(page):
    return re.search(r"<style[^>]*>(.*?)</style>", page, re.S).group(1)


def css_hexes(css):
    """Colour literals only: a bare #id selector is not a colour, so every colour here is one of a few known shapes."""
    return {h.lower() for h in HEX.findall(css) if re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})", h)}


class NetPage(unittest.TestCase):
    def setUp(self):
        self.page = net_page.render("tok", "nonce")
        self.css = style_of(self.page)

    def test_only_the_four_token_colours_are_written(self):
        self.assertEqual(set(), css_hexes(self.css) - TOKENS)
        self.assertNotRegex(self.css, r"\brgba?\(|\bhsla?\(")

    def test_corners_are_square_and_nothing_is_dimmed(self):
        self.assertEqual(set(), set(re.findall(r"border-radius:([^;}]*)", self.css)) - {"0"})
        self.assertEqual(set(), set(re.findall(r"opacity:([^;}]*)", self.css)) - {"1"})
        self.assertNotRegex(self.css, r"box-shadow|text-shadow|gradient|filter:|url\(|@font-face")

    def test_disabled_primary_error_and_empty_notes_follow_jaryo(self):
        self.assertIn("button:disabled{border-style:dashed}", self.css)
        self.assertIn('button:disabled::after{content:" unavailable"}', self.css)
        self.assertIn("#msg[data-bad]{border:3px double var(--fg)", self.css)
        self.assertIn(".note:empty{display:none}", self.css)
        self.assertIn("button.main{border-width:3px;font-weight:700}", self.css)
        self.assertIn(":focus-visible{outline:3px solid var(--fg)", self.css)

    def test_the_script_sets_no_colour_and_the_markup_none_either(self):
        self.assertNotIn("style.color", self.page)
        self.assertNotRegex(self.page.split("</style>", 1)[1], r'style="|#[0-9a-fA-F]{6}\b')

    def test_join_is_the_main_button_and_home_is_a_button(self):
        self.assertRegex(self.page, r'<button id="b-join" type="button" class="main">Join</button>')
        self.assertRegex(self.page, r'<a id="home" class="btn" href="/">Home</a>')
        self.assertNotIn('class="go"', self.page)

    def test_the_password_checkbox_row_is_a_big_target(self):
        self.assertIn("#srow{display:flex;align-items:center;gap:12px;min-height:48px}", self.css)

    def test_the_settings_link_states_no_colour(self):
        self.assertNotIn("#9ecbff", net_page.LINK)
        self.assertNotRegex(net_page.LINK, r"#[0-9a-fA-F]{3,6}\b")
        self.assertLess(len(net_page.LINK), 300)

    def test_the_theme_is_applied_before_the_first_paint(self):
        self.assertIn("localStorage.getItem('jaryo_theme')", self.page.split("</head>", 1)[0])

    def test_the_two_columns_start_where_the_first_run_page_starts_them(self):
        self.assertIn("@media (min-width:1000px){main{display:grid;grid-template-columns:minmax(0,1fr) var(--hinge) minmax(0,1fr)", self.css)


class FirstRunPages(unittest.TestCase):
    def test_the_shared_style_uses_only_the_four_token_colours(self):
        for page in (firstrun_page.firstrun_page("tok", "nonce"), firstrun_page.timezone_page("tok", "nonce")):
            css = style_of(page)
            self.assertEqual(set(), css_hexes(css) - TOKENS)
            self.assertEqual(set(), set(re.findall(r"border-radius:([^;}]*)", css)) - {"0"})
            self.assertEqual(set(), set(re.findall(r"opacity:([^;}]*)", css)) - {"1"})

    def test_placeholders_and_unavailable_buttons_say_so_in_words(self):
        css = firstrun_page.CSS
        self.assertIn("::placeholder{color:var(--fg);opacity:1;font-style:italic}", css)
        self.assertIn('button:disabled::after{content:" unavailable"}', css)

    def test_time_zone_rows_are_boxes_eight_pixels_apart(self):
        css = firstrun_page.CSS
        self.assertIn(".list{display:flex;flex-direction:column;gap:8px;border:0}", css)
        self.assertIn(".list button{border:1px solid var(--fg)}", css)


if __name__ == "__main__":
    unittest.main()
