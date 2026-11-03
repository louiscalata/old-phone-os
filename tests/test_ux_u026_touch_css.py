# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests.web_source import web_text


class HistoryLabelTest(unittest.TestCase):
    def test_history_label_14px(self):
        src = web_text()
        self.assertIn("#history-label{font-size:14px;opacity:.6}", src)
        self.assertNotIn("#history-label{font-size:12px;opacity:.6}", src)


if __name__ == "__main__":
    unittest.main()
