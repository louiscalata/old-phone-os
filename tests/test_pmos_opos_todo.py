# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from pathlib import Path

class TestOposPublishTodo(unittest.TestCase):
    def test_publish_todo_present(self):
        apkbuild_path = Path(__file__).resolve().parents[1] / "packaging" / "pmos" / "opos" / "APKBUILD"
        content = apkbuild_path.read_text(encoding="utf-8")
        line = content.splitlines()[1]
        self.assertEqual(
            (line.startswith("# TODO(publish):"), "release-tarball" in line, "sha512" in line),
            (True, True, True)
        )

if __name__ == "__main__":
    unittest.main()
