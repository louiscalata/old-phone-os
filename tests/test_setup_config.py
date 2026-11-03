# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import base64
import copy
import json
import math
import os
import stat
import struct
import tempfile
import unittest
from pathlib import Path

from opos.setup.config import (
    AI_TOOLS_MODES,
    BATTERY_MODES,
    SCHEMA_VERSION,
    SERVICES,
    ConfigError,
    config_dir,
    config_path,
    default_config,
    ensure_token,
    load,
    save,
    token_path,
    validate,
)

ROOT = Path(os.path.abspath(os.sep))

def _p(*parts: str) -> Path:
    """Build absolute path from ROOT."""
    return ROOT.joinpath(*parts)


def _b64(*parts: bytes) -> str:
    return base64.b64encode(b"".join(struct.pack(">I", len(p)) + p for p in parts)).decode("ascii")


def _rsa_modulus() -> bytes:
    return b"\x00" + b"\xcc" * 127 + b"\xcd"


def _p256_point() -> bytes:
    x_hex = "6b17" + "d1f2" + "e12c" + "4247" + "f8bc" + "e6e5" + "63a4" + "40f2" + "7703" + "7d81" + "2deb" + "33a0" + "f4a1" + "3945" + "d898" + "c296"
    y_hex = "4fe3" + "42e2" + "fe1a" + "7f9b" + "8ee7" + "eb4a" + "7c0f" + "9e16" + "2bce" + "3357" + "6b31" + "5ece" + "cbb6" + "4068" + "37bf" + "51f5"
    return b"\x04" + bytes.fromhex(x_hex) + bytes.fromhex(y_hex)


SSH_PUBLIC_KEYS = {
    "ssh-ed25519": "ssh-ed25519 " + _b64(b"ssh-ed25519", b"\x11" * 32) + " test@example",
    "ssh-rsa": "ssh-rsa " + _b64(b"ssh-rsa", b"\x01\x00\x01", _rsa_modulus()) + " user@host",
    "ecdsa-sha2-nistp256": "ecdsa-sha2-nistp256 " + _b64(b"ecdsa-sha2-nistp256", b"nistp256", _p256_point()) + " user@host",
    "sk-ssh-ed25519@openssh.com": "sk-ssh-ed25519@openssh.com " + _b64(b"sk-ssh-ed25519@openssh.com", b"\x11" * 32, b"ssh:test") + " comment",
}


def valid_config() -> dict:
    return copy.deepcopy(default_config())


class ConfigDirTests(unittest.TestCase):
    def test_override_wins_on_all_platforms(self) -> None:
        env = {"OPOS_CONFIG_DIR": str(_p("tmp", "opos-custom")), "HOME": str(_p("home", "u")), "APPDATA": str(_p("fake", "win-home", "AppData", "Roaming")), "XDG_CONFIG_HOME": str(_p("xdg"))}
        for platform in ("linux", "darwin", "win32"):
            with self.subTest(platform=platform):
                self.assertEqual(config_dir(env, platform), _p("tmp", "opos-custom"))

    def test_override_empty_is_ignored(self) -> None:
        env = {"OPOS_CONFIG_DIR": "", "HOME": str(_p("home", "u"))}
        self.assertEqual(config_dir(env, "linux"), _p("home", "u", ".config", "opos"))

    def test_linux_xdg(self) -> None:
        env = {"HOME": str(_p("home", "u")), "XDG_CONFIG_HOME": str(_p("xdg", "cfg"))}
        self.assertEqual(config_dir(env, "linux"), _p("xdg", "cfg", "opos"))

    def test_linux_fallback_to_home_config(self) -> None:
        self.assertEqual(config_dir({"HOME": str(_p("home", "u"))}, "linux"), _p("home", "u", ".config", "opos"))
        self.assertEqual(config_dir({"HOME": str(_p("home", "u")), "XDG_CONFIG_HOME": ""}, "linux"), _p("home", "u", ".config", "opos"))

    def test_darwin(self) -> None:
        env = {"HOME": str(_p("fake", "mac-home")), "XDG_CONFIG_HOME": str(_p("ignored"))}
        self.assertEqual(config_dir(env, "darwin"), _p("fake", "mac-home", "Library", "Application Support", "opos"))

    def test_win32_appdata(self) -> None:
        env = {"HOME": str(_p("fake", "home")), "APPDATA": str(_p("fake", "AppData", "Roaming"))}
        self.assertEqual(config_dir(env, "win32"), _p("fake", "AppData", "Roaming", "opos"))

    def test_win32_fallback(self) -> None:
        self.assertEqual(config_dir({"HOME": str(_p("fake", "home"))}, "win32"), _p("fake", "home", "AppData", "Roaming", "opos"))
        self.assertEqual(config_dir({"HOME": str(_p("fake", "home")), "APPDATA": ""}, "win32"), _p("fake", "home", "AppData", "Roaming", "opos"))

    def test_unknown_platform_uses_xdg_logic(self) -> None:
        self.assertEqual(config_dir({"HOME": str(_p("home", "u"))}, "freebsd"), _p("home", "u", ".config", "opos"))

    def test_never_relative(self) -> None:
        cases = [
            ({"HOME": str(_p("home", "u"))}, "linux"),
            ({"HOME": str(_p("fake", "mac-home"))}, "darwin"),
            ({"HOME": str(_p("fake", "win-home")), "APPDATA": "C:/AppData"}, "win32"),
            ({"OPOS_CONFIG_DIR": str(_p("abs", "dir")), "HOME": str(_p("home", "u"))}, "linux"),
        ]
        for env, platform in cases:
            with self.subTest(platform=platform):
                self.assertTrue(config_dir(env, platform).is_absolute())

    def test_config_and_token_path(self) -> None:
        env = {"HOME": str(_p("home", "u"))}
        self.assertEqual(config_path(env, "linux"), _p("home", "u", ".config", "opos", "config.json"))
        self.assertEqual(token_path(env, "linux"), _p("home", "u", ".config", "opos", "session.token"))


class DefaultConfigTests(unittest.TestCase):
    def test_validates(self) -> None:
        self.assertEqual(validate(default_config()), [])

    def test_exact_shape(self) -> None:
        cfg = default_config()
        self.assertEqual(cfg["version"], SCHEMA_VERSION)
        self.assertEqual(SCHEMA_VERSION, 1)
        self.assertEqual(cfg["device"], {"id": "unknown", "name": "Unknown device", "source": "stub", "battery_health": None})
        self.assertEqual(cfg["services"], list(SERVICES))
        self.assertEqual(cfg["goal"], {"enabled": True, "time": "23:00"})
        self.assertEqual(cfg["battery_mode"], "auto")
        self.assertEqual(cfg["security"], {"ssh_public_key": None})
        self.assertIs(cfg["applied"], False)
        self.assertEqual(cfg["ai_tools"], {"tools": [], "mode": "lite"})
        self.assertIn(cfg["battery_mode"], BATTERY_MODES)
        self.assertIn(cfg["ai_tools"]["mode"], AI_TOOLS_MODES)

    def test_returns_independent_copies(self) -> None:
        first, second = default_config(), default_config()
        first["services"].append("bogus")
        first["device"]["id"] = "changed"
        self.assertEqual(second, default_config())


class ValidateTests(unittest.TestCase):
    def assert_invalid(self, cfg: object) -> None:
        errors = validate(cfg)
        self.assertIsInstance(errors, list)
        self.assertTrue(errors)
        self.assertTrue(all(isinstance(e, str) and e for e in errors))

    def test_non_dict(self) -> None:
        for bad in (None, [], "cfg", 1, True):
            with self.subTest(bad=bad):
                self.assert_invalid(bad)

    def test_unknown_and_missing_top_level_keys(self) -> None:
        cfg = valid_config(); cfg["extra"] = 1; self.assert_invalid(cfg)
        cfg = valid_config(); del cfg["goal"]; self.assert_invalid(cfg)
        cfg = valid_config(); cfg["token"] = "x" * 40; self.assert_invalid(cfg)
        cfg = valid_config(); cfg["session_token"] = "x" * 40; self.assert_invalid(cfg)

    def test_version(self) -> None:
        for bad in (0, 2, "1", None, 1.0, True):
            cfg = valid_config(); cfg["version"] = bad
            with self.subTest(bad=bad):
                self.assert_invalid(cfg)

    def test_device_not_dict_and_extra_keys(self) -> None:
        cfg = valid_config(); cfg["device"] = "x"; self.assert_invalid(cfg)
        cfg = valid_config(); cfg["device"]["extra"] = 1; self.assert_invalid(cfg)
        cfg = valid_config(); del cfg["device"]["name"]; self.assert_invalid(cfg)

    def test_device_id(self) -> None:
        for bad in ("", "a" * 65, "Upper", "has space", "bad/slash", "bad@id", 123, None):
            cfg = valid_config(); cfg["device"]["id"] = bad
            with self.subTest(bad=bad):
                self.assert_invalid(cfg)
        for good in ("a", "device-1_x.y", "a" * 64):
            cfg = valid_config(); cfg["device"]["id"] = good
            with self.subTest(good=good):
                self.assertEqual(validate(cfg), [])

    def test_device_name(self) -> None:
        for bad in ("", "x" * 81, "bad\nname", "bad\x00name", "bad\x1fname", 42, None):
            cfg = valid_config(); cfg["device"]["name"] = bad
            with self.subTest(bad=repr(bad)):
                self.assert_invalid(cfg)
        cfg = valid_config(); cfg["device"]["name"] = "x" * 80; self.assertEqual(validate(cfg), [])

    def test_device_source(self) -> None:
        for bad in ("", "other", None, 1):
            cfg = valid_config(); cfg["device"]["source"] = bad
            with self.subTest(bad=bad):
                self.assert_invalid(cfg)
        for good in ("hw-adapt", "stub", "manual"):
            cfg = valid_config(); cfg["device"]["source"] = good
            with self.subTest(good=good):
                self.assertEqual(validate(cfg), [])

    def test_battery_health(self) -> None:
        for bad in (True, False, math.nan, float("inf"), -0.1, 1.1, "0.5", None.__class__):
            cfg = valid_config(); cfg["device"]["battery_health"] = bad
            with self.subTest(bad=bad):
                self.assert_invalid(cfg)
        for good in (None, 0, 0.0, 0.5, 1, 1.0):
            cfg = valid_config(); cfg["device"]["battery_health"] = good
            with self.subTest(good=good):
                self.assertEqual(validate(cfg), [])

    def test_services(self) -> None:
        for bad in ([], ["sms"], ["calls", "calls"], ["calls", "bogus"], ["calls", 1], "calls", None, ("calls",)):
            cfg = valid_config(); cfg["services"] = bad
            with self.subTest(bad=bad):
                self.assert_invalid(cfg)
        cfg = valid_config(); cfg["services"] = ["calls"]; self.assertEqual(validate(cfg), [])
        cfg = valid_config(); cfg["services"] = list(reversed(SERVICES)); self.assertEqual(validate(cfg), [])

    def test_goal(self) -> None:
        cfg = valid_config(); cfg["goal"] = "x"; self.assert_invalid(cfg)
        cfg = valid_config(); cfg["goal"]["extra"] = 1; self.assert_invalid(cfg)
        for bad in (1, "true", None):
            cfg = valid_config(); cfg["goal"]["enabled"] = bad
            with self.subTest(bad=bad):
                self.assert_invalid(cfg)
        for bad in ("24:00", "23:60", "9:00", "23:0", "2300", "ab:cd", "", "23:00:00", 2300, None):
            cfg = valid_config(); cfg["goal"]["time"] = bad
            with self.subTest(bad=bad):
                self.assert_invalid(cfg)
        for good in ("00:00", "09:05", "23:59"):
            cfg = valid_config(); cfg["goal"]["time"] = good
            with self.subTest(good=good):
                self.assertEqual(validate(cfg), [])

    def test_battery_mode(self) -> None:
        for bad in ("", "eco", None, 1):
            cfg = valid_config(); cfg["battery_mode"] = bad
            with self.subTest(bad=bad):
                self.assert_invalid(cfg)
        for good in BATTERY_MODES:
            cfg = valid_config(); cfg["battery_mode"] = good
            with self.subTest(good=good):
                self.assertEqual(validate(cfg), [])

    def test_security_shape(self) -> None:
        cfg = valid_config(); cfg["security"] = None; self.assert_invalid(cfg)
        cfg = valid_config(); cfg["security"]["token"] = "x"; self.assert_invalid(cfg)
        cfg = valid_config(); del cfg["security"]["ssh_public_key"]; self.assert_invalid(cfg)

    def test_ssh_public_key_valid(self) -> None:
        keys = list(SSH_PUBLIC_KEYS.values())
        for key in keys:
            cfg = valid_config(); cfg["security"]["ssh_public_key"] = key
            with self.subTest(key=key.split()[0]):
                self.assertEqual(validate(cfg), [])

    def test_ssh_public_key_invalid(self) -> None:
        bad_keys = [
            "",
            "ssh-ed25519",
            "ssh-ed25519 ",
            "ssh-dss AAAAB3NzaC1kc3MAAACB",
            "ed25519 AAAAC3NzaC1lZDI1NTE5",
            "ssh-ed25519 !!!not-base64!!!",
            "ssh-ed25519 AAAA\nssh-ed25519 AAAA",
            "ssh-ed25519 AAAA extra\nline",
            " ssh-ed25519 AAAAC3NzaC1lZDI1NTE5",
            "ssh-ed25519 " + "A" * 2001,
            123,
        ]
        for bad in bad_keys:
            cfg = valid_config(); cfg["security"]["ssh_public_key"] = bad
            with self.subTest(bad=repr(bad)[:40]):
                self.assert_invalid(cfg)

    def test_ssh_public_key_control_characters_rejected(self) -> None:
        prefix = "ssh-" + "ed25519 "
        body = SSH_PUBLIC_KEYS["ssh-ed25519"].split()[1]
        for code in (0, 9, 11, 12, 27, 31, 127):
            with self.subTest(code=code):
                cfg = valid_config()
                key = prefix + body + " owner" + chr(code) + "comment"
                cfg["security"]["ssh_public_key"] = key
                errors = validate(cfg)
                self.assertEqual(errors, ["security.ssh_public_key must not contain control characters"])
                for err in errors:
                    self.assertNotIn(key, err)
                cfg = valid_config()
                cfg["security"]["ssh_public_key"] = prefix + body + " owner" + " " + "comment"
                self.assertEqual(validate(cfg), [])

    def test_ssh_public_key_fixtures_have_complete_blobs(self) -> None:
        expected = {
            "ssh-ed25519": [b"ssh-ed25519", b"\x11" * 32],
            "ssh-rsa": [b"ssh-rsa", b"\x01\x00\x01", _rsa_modulus()],
            "ecdsa-sha2-nistp256": [b"ecdsa-sha2-nistp256", b"nistp256", _p256_point()],
            "sk-ssh-ed25519@openssh.com": [b"sk-ssh-ed25519@openssh.com", b"\x11" * 32, b"ssh:test"],
        }
        for algo, key in SSH_PUBLIC_KEYS.items():
            with self.subTest(algo=algo):
                parts = key.split()
                self.assertEqual(parts[0], algo)
                raw = base64.b64decode(parts[1], validate=True)
                fields = []
                pos = 0
                while pos + 4 <= len(raw):
                    (size,) = struct.unpack_from(">I", raw, pos)
                    pos += 4
                    self.assertLessEqual(pos + size, len(raw))
                    fields.append(raw[pos:pos + size])
                    pos += size
                self.assertEqual(fields, expected[algo])
                self.assertEqual(pos, len(raw))
                cfg = valid_config(); cfg["security"]["ssh_public_key"] = key
                self.assertEqual(validate(cfg), [])

    def test_applied(self) -> None:
        for bad in (1, 0, "false", None):
            cfg = valid_config(); cfg["applied"] = bad
            with self.subTest(bad=bad):
                self.assert_invalid(cfg)
        cfg = valid_config(); cfg["applied"] = True; self.assertEqual(validate(cfg), [])


class AiToolsTests(unittest.TestCase):
    def assert_invalid(self, cfg: object) -> None:
        errors = validate(cfg)
        self.assertIsInstance(errors, list)
        self.assertTrue(errors)
        self.assertTrue(all(isinstance(e, str) and e for e in errors))

    def test_older_config_without_ai_tools_validates(self) -> None:
        cfg = valid_config()
        del cfg["ai_tools"]
        self.assertEqual(validate(cfg), [])

    def test_valid_ai_tools(self) -> None:
        cases = [
            {"tools": [], "mode": "lite"},
            {"tools": [], "mode": "full"},
            {"tools": ["a"], "mode": "lite"},
            {"tools": ["my-tool", "tool-2"], "mode": "full"},
            {"tools": ["a" * 32], "mode": "lite"},
            {"tools": [f"t{i}" for i in range(8)], "mode": "full"},
        ]
        for ai in cases:
            with self.subTest(ai=ai):
                cfg = valid_config()
                cfg["ai_tools"] = ai
                self.assertEqual(validate(cfg), [])

    def test_mode_invalid(self) -> None:
        for bad in ("", "LITE", "Full", "lite ", " lite", "auto", "worn", None, 1, True):
            with self.subTest(bad=bad):
                cfg = valid_config()
                cfg["ai_tools"]["mode"] = bad
                self.assert_invalid(cfg)

    def test_tools_not_a_list(self) -> None:
        for bad in (None, "", "a", {"a": 1}, (), True, 123):
            with self.subTest(bad=bad):
                cfg = valid_config()
                cfg["ai_tools"]["tools"] = bad
                self.assert_invalid(cfg)

    def test_duplicate_ids(self) -> None:
        cfg = valid_config()
        cfg["ai_tools"]["tools"] = ["dup", "dup"]
        self.assert_invalid(cfg)
        cfg = valid_config()
        cfg["ai_tools"]["tools"] = ["a", "b", "a"]
        self.assert_invalid(cfg)

    def test_id_with_uppercase_or_slash(self) -> None:
        for bad in ("Upper", "UPPER", "Tool", "a/B", "bad/slash", "has/Slash", "A", "a/b/c"):
            with self.subTest(bad=bad):
                cfg = valid_config()
                cfg["ai_tools"]["tools"] = [bad]
                self.assert_invalid(cfg)

    def test_more_than_8(self) -> None:
        cfg = valid_config()
        cfg["ai_tools"]["tools"] = [f"t{i}" for i in range(9)]
        self.assert_invalid(cfg)
        cfg = valid_config()
        cfg["ai_tools"]["tools"] = [f"x{i}" for i in range(10)]
        self.assert_invalid(cfg)

    def test_extra_key(self) -> None:
        cfg = valid_config()
        cfg["ai_tools"]["extra"] = 1
        self.assert_invalid(cfg)
        cfg = valid_config()
        cfg["ai_tools"]["unknown"] = "x"
        self.assert_invalid(cfg)

    def test_missing_key(self) -> None:
        cfg = valid_config()
        del cfg["ai_tools"]["tools"]
        self.assert_invalid(cfg)
        cfg = valid_config()
        del cfg["ai_tools"]["mode"]
        self.assert_invalid(cfg)
        cfg = valid_config()
        cfg["ai_tools"] = {}
        self.assert_invalid(cfg)

    def test_non_string_entries(self) -> None:
        for bad in (1, None, True, 3.14, {}, [], b"a"):
            with self.subTest(bad=bad):
                cfg = valid_config()
                cfg["ai_tools"]["tools"] = [bad]
                self.assert_invalid(cfg)
        cfg = valid_config()
        cfg["ai_tools"]["tools"] = ["ok", 123]
        self.assert_invalid(cfg)

    def test_save_load_roundtrip_with_ai_tools(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            cfg = valid_config()
            cfg["ai_tools"] = {"tools": ["tool-a", "tool-b"], "mode": "full"}
            save(cfg, path)
            self.assertEqual(load(path), cfg)
            cfg2 = valid_config()
            cfg2["ai_tools"] = {"tools": [f"t{i}" for i in range(8)], "mode": "lite"}
            save(cfg2, path)
            self.assertEqual(load(path), cfg2)

    def test_token_like_key_inside_ai_tools_rejected(self) -> None:
        cfg = valid_config()
        cfg["ai_tools"]["api_token"] = "x" * 40
        self.assert_invalid(cfg)
        cfg = valid_config()
        cfg["ai_tools"]["secret_token"] = "secret"
        self.assert_invalid(cfg)
        cfg = valid_config()
        cfg["ai_tools"]["myToken"] = "value"
        self.assert_invalid(cfg)


class SaveLoadTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sub" / "config.json"
            cfg = valid_config()
            cfg["device"]["name"] = "Pixel 8"
            returned = save(cfg, path)
            self.assertEqual(returned, path)
            self.assertEqual(load(path), cfg)
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.endswith("\n"))
            self.assertEqual(json.loads(text), cfg)
            self.assertEqual(text, json.dumps(cfg, indent=2, sort_keys=True) + "\n")

    def test_save_invalid_raises_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            cfg = valid_config(); cfg["version"] = 99
            with self.assertRaises(ConfigError):
                save(cfg, path)
            self.assertFalse(path.exists())

    def test_atomic_write_leaves_no_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            save(valid_config(), path)
            save(valid_config(), path)
            self.assertEqual([p.name for p in Path(td).iterdir()], ["config.json"])

    @unittest.skipUnless(os.name == "posix", "posix modes")
    def test_save_modes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nested" / "config.json"
            save(valid_config(), path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_load_missing_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(load(Path(td) / "missing.json"))

    def test_load_not_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text("not json {", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load(path)

    def test_load_invalid_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            cfg = valid_config(); cfg["battery_mode"] = "bogus"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load(path)
            path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load(path)

    def test_load_directory_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ConfigError):
                load(Path(td))


class TokenTests(unittest.TestCase):
    def test_creates_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sub" / "session.token"
            token = ensure_token(path)
            self.assertGreaterEqual(len(token), 32)
            self.assertEqual(path.read_text(encoding="utf-8").strip(), token)
            self.assertEqual([p.name for p in path.parent.iterdir()], ["session.token"])

    def test_reuse_existing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "session.token"
            original = "x" * 40
            path.write_text(original + "\n", encoding="utf-8")
            self.assertEqual(ensure_token(path), original)
            self.assertEqual(ensure_token(path), original)

    def test_reuse_from_first_creation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "session.token"
            self.assertEqual(ensure_token(path), ensure_token(path))

    def test_regenerates_short_empty_or_blank(self) -> None:
        for content in ("", "   \n", "short", "x" * 31):
            with tempfile.TemporaryDirectory() as td, self.subTest(content=repr(content)):
                path = Path(td) / "session.token"
                path.write_text(content, encoding="utf-8")
                token = ensure_token(path)
                self.assertGreaterEqual(len(token), 32)
                self.assertNotEqual(token, content.strip())
                self.assertEqual(path.read_text(encoding="utf-8").strip(), token)

    @unittest.skipUnless(os.name == "posix", "posix modes")
    def test_token_modes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nested" / "session.token"
            ensure_token(path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_token_not_in_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.json"
            save(valid_config(), cfg_path)
            ensure_token(Path(td) / "session.token")
            self.assertNotIn("token", cfg_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
