# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import ast
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from opos.setup.device import (
    STUB_ID,
    calibration_present,
    choose_profile,
    detect_device,
    list_profiles,
    profile_to_device,
    slugify,
    stub_device,
    to_config_device,
)


class FakeRunner:
    def __init__(self, dry_run: bool = True) -> None:
        self.dry_run = dry_run
        self.commands: list[list[str]] = []

    def run(self, command: list[str]) -> None:
        self.commands.append(list(command))


def make_profile(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "name": "Test Phone 5G",
        "battery": SimpleNamespace(
            capacity_mah=4500, health=0.87, voltage_v=3.9
        ),
        "cpu": SimpleNamespace(name="CPU"),
        "screen": SimpleNamespace(width=1080, height=2400),
        "radios": ("wifi", "bluetooth"),
        "modem": True,
        "sensors": ("accelerometer",),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def fake_hw_modules(
    *,
    detect_impl: object = None,
    names: tuple[str, ...] = (),
    profiles: dict[str, object] | None = None,
    names_impl: object = None,
    profile_impl: object = None,
) -> dict[str, object]:
    hw = types.ModuleType("opos.hw")
    hw.__path__ = []  # type: ignore[attr-defined]
    detect_module = types.ModuleType("opos.hw.detect")
    store_module = types.ModuleType("opos.hw.store")
    profile_map = profiles or {}

    detect_module.detect = detect_impl or (  # type: ignore[attr-defined]
        lambda root="/": make_profile()
    )
    store_module.builtin_names = names_impl or (  # type: ignore[attr-defined]
        lambda: names
    )
    store_module.builtin_profile = profile_impl or (  # type: ignore[attr-defined]
        lambda name: profile_map[name]
    )
    return {
        "opos.hw": hw,
        "opos.hw.detect": detect_module,
        "opos.hw.store": store_module,
    }


def missing_hw_modules() -> dict[str, object]:
    return {"opos.hw": None, "opos.hw.detect": None, "opos.hw.store": None}


class SlugifyTests(unittest.TestCase):
    def test_constant_and_basic(self) -> None:
        self.assertEqual(STUB_ID, "generic-phone")
        self.assertEqual(slugify("Generic Phone"), "generic-phone")
        self.assertEqual(slugify("Pixel 8 Pro"), "pixel-8-pro")

    def test_empty_and_only_invalid_become_stub(self) -> None:
        self.assertEqual(slugify(""), STUB_ID)
        self.assertEqual(slugify("!!! ???"), STUB_ID)
        self.assertEqual(slugify("---"), STUB_ID)

    def test_runs_collapse_and_ends_are_stripped(self) -> None:
        self.assertEqual(slugify("  Foo@@Bar##Baz  "), "foo-bar-baz")
        self.assertEqual(slugify("--Foo Bar--"), "foo-bar")
        self.assertEqual(slugify("a \t\n b"), "a-b")

    def test_allowed_separators_are_preserved(self) -> None:
        self.assertEqual(slugify("Foo.Bar_Baz-Qux 2"), "foo.bar_baz-qux-2")
        self.assertEqual(slugify("A.B_C-D"), "a.b_c-d")

    def test_non_ascii_is_replaced(self) -> None:
        self.assertEqual(slugify("Télé Phone"), "t-l-phone")

    def test_truncates_to_64_chars(self) -> None:
        self.assertEqual(slugify("a" * 100), "a" * 64)
        self.assertEqual(len(slugify("word " * 40)), 64)

    def test_idempotent(self) -> None:
        for value in ("", "Foo Bar!", "a" * 100, "Foo.Bar_Baz"):
            self.assertEqual(slugify(slugify(value)), slugify(value))


class StubDeviceTests(unittest.TestCase):
    def test_exact_stub(self) -> None:
        self.assertEqual(
            stub_device(),
            {
                "id": "generic-phone",
                "name": "Generic phone (stub)",
                "source": "stub",
                "battery_health": None,
                "radios": [],
                "modem": False,
                "calibrated": False,
            },
        )

    def test_returns_independent_copies(self) -> None:
        first = stub_device()
        first["radios"].append("wifi")
        first["name"] = "Changed"
        self.assertEqual(stub_device()["radios"], [])
        self.assertEqual(stub_device()["name"], "Generic phone (stub)")
        self.assertIsNot(stub_device(), stub_device())
        self.assertIsNot(stub_device()["radios"], stub_device()["radios"])


class ProfileToDeviceTests(unittest.TestCase):
    def test_valid_profile(self) -> None:
        self.assertEqual(
            profile_to_device(make_profile()),
            {
                "id": "test-phone-5g",
                "name": "Test Phone 5G",
                "source": "hw-adapt",
                "battery_health": 0.87,
                "radios": ["wifi", "bluetooth"],
                "modem": True,
                "calibrated": False,
            },
        )

    def test_name_is_truncated_and_control_chars_removed(self) -> None:
        device = profile_to_device(make_profile(name="A\x00B\x1fC\nD"))
        self.assertEqual(device["name"], "ABCD")
        long_device = profile_to_device(make_profile(name="a" * 100))
        self.assertEqual(long_device["name"], "a" * 80)

    def test_id_uses_slugified_name(self) -> None:
        device = profile_to_device(make_profile(name="My Phone! (2024)"))
        self.assertEqual(device["id"], "my-phone-2024")

    def test_battery_health_boundaries(self) -> None:
        for health in (0, 0.0, 1, 1.0, 0.5):
            profile = make_profile(
                battery=SimpleNamespace(health=health)
            )
            self.assertEqual(
                profile_to_device(profile)["battery_health"], float(health)
            )

    def test_invalid_battery_health_becomes_none(self) -> None:
        for health in (-0.1, 1.01, "0.8", None, float("nan"), object()):
            profile = make_profile(
                battery=SimpleNamespace(health=health)
            )
            device = profile_to_device(profile)
            self.assertIsNone(device["battery_health"])
            self.assertEqual(device["source"], "hw-adapt")

    def test_int_health_is_converted_to_float(self) -> None:
        profile = make_profile(battery=SimpleNamespace(health=1))
        device = profile_to_device(profile)
        self.assertIsInstance(device["battery_health"], float)

    def test_radios_become_list_and_modem_becomes_bool(self) -> None:
        device = profile_to_device(
            make_profile(radios=("lte",), modem=1)
        )
        self.assertEqual(device["radios"], ["lte"])
        self.assertIs(device["modem"], True)
        device = profile_to_device(make_profile(radios=(), modem=""))
        self.assertEqual(device["radios"], [])
        self.assertIs(device["modem"], False)

    def test_malformed_profiles_return_stub_and_never_raise(self) -> None:
        bad_profiles = (
            None,
            object(),
            SimpleNamespace(),
            make_profile(name=None),
            make_profile(battery=None),
            make_profile(radios=None),
            make_profile(name=object()),
        )
        for profile in bad_profiles:
            with self.subTest(profile=type(profile).__name__):
                self.assertEqual(profile_to_device(profile), stub_device())

    def test_does_not_mutate_profile_radios(self) -> None:
        profile = make_profile()
        device = profile_to_device(profile)
        device["radios"].append("nfc")
        self.assertEqual(profile.radios, ("wifi", "bluetooth"))


class CalibrationPresentTests(unittest.TestCase):
    def test_missing_file(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertFalse(calibration_present(Path(tmp)))

    def test_present_file(self) -> None:
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "drain-calibration.json").write_text(
                "{}", encoding="utf-8"
            )
            self.assertTrue(calibration_present(config_dir))

    def test_directory_with_that_name_is_not_a_file(self) -> None:
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "drain-calibration.json").mkdir()
            self.assertFalse(calibration_present(config_dir))

    def test_missing_config_dir_and_unrelated_file(self) -> None:
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "missing"
            self.assertFalse(calibration_present(config_dir))
            other = Path(tmp)
            (other / "other.json").write_text("{}", encoding="utf-8")
            self.assertFalse(calibration_present(other))


class DetectDeviceTests(unittest.TestCase):
    def test_missing_hw_returns_stub(self) -> None:
        runner = FakeRunner(dry_run=True)
        with mock.patch.dict(sys.modules, missing_hw_modules()):
            self.assertEqual(detect_device(), stub_device())
        self.assertEqual(runner.commands, [])

    def test_missing_hw_with_calibration(self) -> None:
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            with mock.patch.dict(sys.modules, missing_hw_modules()):
                self.assertFalse(detect_device(config_dir=config_dir)["calibrated"])
                (config_dir / "drain-calibration.json").write_text(
                    "{}", encoding="utf-8"
                )
                device = detect_device(config_dir=config_dir)
            expected = stub_device()
            expected["calibrated"] = True
            self.assertEqual(device, expected)

    def test_hw_profile_is_used_and_root_is_passed(self) -> None:
        calls: list[str] = []

        def detect(root: str = "/") -> SimpleNamespace:
            calls.append(root)
            return make_profile(name="Detected Phone")

        with TemporaryDirectory() as tmp:
            with mock.patch.dict(
                sys.modules, fake_hw_modules(detect_impl=detect)
            ):
                device = detect_device(root=tmp)
            self.assertEqual(calls, [tmp])
        self.assertEqual(device["id"], "detected-phone")
        self.assertEqual(device["source"], "hw-adapt")
        self.assertFalse(device["calibrated"])

    def test_hw_profile_with_calibration(self) -> None:
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "drain-calibration.json").write_text(
                "{}", encoding="utf-8"
            )
            with mock.patch.dict(sys.modules, fake_hw_modules()):
                device = detect_device(config_dir=config_dir)
            self.assertTrue(device["calibrated"])
            self.assertEqual(device["source"], "hw-adapt")

    def test_hw_exception_returns_stub(self) -> None:
        def broken_detect(root: str = "/") -> SimpleNamespace:
            raise RuntimeError("detect failed")

        with mock.patch.dict(
            sys.modules, fake_hw_modules(detect_impl=broken_detect)
        ):
            self.assertEqual(detect_device(), stub_device())

    def test_hw_exception_still_applies_calibration(self) -> None:
        def broken_detect(root: str = "/") -> SimpleNamespace:
            raise ValueError("bad hardware")

        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "drain-calibration.json").write_text(
                "{}", encoding="utf-8"
            )
            with mock.patch.dict(
                sys.modules, fake_hw_modules(detect_impl=broken_detect)
            ):
                device = detect_device(config_dir=config_dir)
            expected = stub_device()
            expected["calibrated"] = True
            self.assertEqual(device, expected)

    def test_malformed_hw_profile_returns_stub(self) -> None:
        with mock.patch.dict(
            sys.modules,
            fake_hw_modules(detect_impl=lambda root="/": object()),
        ):
            self.assertEqual(detect_device(), stub_device())

    def test_idempotent_and_dry_run_creates_no_files(self) -> None:
        runner = FakeRunner(dry_run=True)
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            with mock.patch.dict(sys.modules, fake_hw_modules()):
                first = detect_device(root=tmp, config_dir=config_dir)
                second = detect_device(root=tmp, config_dir=config_dir)
            self.assertEqual(first, second)
            self.assertEqual(list(config_dir.iterdir()), [])
        self.assertEqual(runner.commands, [])

    def test_f025_inconclusive_detection_preserves_phone_essentials(self) -> None:
        from opos.runtime import Runtime

        def check_essentials(device: dict[str, object]) -> None:
            health = device["battery_health"]
            runtime = Runtime(visible=False)
            runtime.step(0, 50, health=health)
            tick = runtime.step(60, 50, health=health)
            self.assertIn("calls", tick.decision.allowed)
            self.assertIn("messages", tick.decision.allowed)
            self.assertNotIn("calls", tick.decision.deferred)
            self.assertNotIn("messages", tick.decision.deferred)
            self.assertAlmostEqual(tick.reserve, 0.7, places=2)

        with self.subTest("confirmed-modem-survives-radio-failure"):
            with TemporaryDirectory() as tmp:
                with mock.patch(
                    "opos.hw.detect.detect",
                    return_value=make_profile(modem=True),
                ):
                    with mock.patch(
                        "opos.hw.battery.read_battery",
                        return_value={"health": 0.87},
                    ):
                        with mock.patch(
                            "opos.hw.battery.read_radios",
                            side_effect=OSError("radio unavailable"),
                        ):
                            device = detect_device(root=tmp)
            self.assertIs(device["modem"], True)
            self.assertEqual(device["battery_health"], 0.87)
            check_essentials(device)
        with self.subTest("none-detection-keeps-battery-and-wifi"):
            with TemporaryDirectory() as tmp:
                with mock.patch(
                    "opos.hw.detect.detect", return_value=None
                ):
                    with mock.patch(
                        "opos.hw.battery.read_battery",
                        return_value={"health": 0.87},
                    ):
                        with mock.patch(
                            "opos.hw.battery.read_radios",
                            return_value={"radios_present": ["wifi"]},
                        ):
                            device = detect_device(root=tmp)
            self.assertEqual(device["source"], "stub")
            self.assertEqual(device["battery_health"], 0.87)
            self.assertEqual(device["hw_radios"], ["wifi"])
            self.assertIs(device["modem"], False)
            check_essentials(device)
        with self.subTest("exception-detection-keeps-battery-and-wifi"):
            with TemporaryDirectory() as tmp:
                with mock.patch(
                    "opos.hw.detect.detect",
                    side_effect=RuntimeError("detect failed"),
                ):
                    with mock.patch(
                        "opos.hw.battery.read_battery",
                        return_value={"health": 0.87},
                    ):
                        with mock.patch(
                            "opos.hw.battery.read_radios",
                            return_value={"radios_present": ["wifi"]},
                        ):
                            device = detect_device(root=tmp)
            self.assertEqual(device["source"], "stub")
            self.assertEqual(device["battery_health"], 0.87)
            self.assertEqual(device["hw_radios"], ["wifi"])
            self.assertIs(device["modem"], False)
            check_essentials(device)
        with self.subTest("explicit-absence-stays-absent"):
            with TemporaryDirectory() as tmp:
                with mock.patch(
                    "opos.hw.detect.detect",
                    return_value=make_profile(modem=False),
                ):
                    with mock.patch(
                        "opos.hw.battery.read_battery",
                        return_value={"health": 0.87},
                    ):
                        with mock.patch(
                            "opos.hw.battery.read_radios",
                            return_value={"radios_present": ["wifi"]},
                        ):
                            device = detect_device(root=tmp)
            self.assertIs(device["modem"], False)
            check_essentials(device)

    def test_f025_regression_rejects_corrupted_fallback_modem(self) -> None:
        real = detect_device

        def wrapper(*args: object, **kwargs: object) -> dict[str, object]:
            device = real(*args, **kwargs)  # type: ignore[arg-type]
            if device.get("source") == "stub":
                device["modem"] = True
            return device

        case = DetectDeviceTests(
            "test_f025_inconclusive_detection_preserves_phone_essentials"
        )
        outcome = unittest.TestResult()
        with mock.patch.object(
            sys.modules[__name__], "detect_device", wrapper
        ):
            case.run(outcome)
        self.assertEqual(len(outcome.failures), 2)
        self.assertEqual(len(outcome.errors), 0)


class ListProfilesTests(unittest.TestCase):
    def test_missing_hw_returns_empty_list(self) -> None:
        with mock.patch.dict(sys.modules, missing_hw_modules()):
            self.assertEqual(list_profiles(), [])

    def test_profiles_are_sorted_by_name_and_slugified(self) -> None:
        modules = fake_hw_modules(
            names=("Zeta Phone", "Alpha Phone!", "mid phone")
        )
        with mock.patch.dict(sys.modules, modules):
            self.assertEqual(
                list_profiles(),
                [
                    {"id": "alpha-phone", "name": "Alpha Phone!"},
                    {"id": "mid-phone", "name": "mid phone"},
                    {"id": "zeta-phone", "name": "Zeta Phone"},
                ],
            )

    def test_empty_builtin_list(self) -> None:
        with mock.patch.dict(sys.modules, fake_hw_modules(names=())):
            self.assertEqual(list_profiles(), [])

    def test_store_failure_returns_empty_list(self) -> None:
        def broken_names() -> tuple[str, ...]:
            raise RuntimeError("store failed")

        with mock.patch.dict(
            sys.modules, fake_hw_modules(names_impl=broken_names)
        ):
            self.assertEqual(list_profiles(), [])

    def test_idempotent(self) -> None:
        with mock.patch.dict(
            sys.modules, fake_hw_modules(names=("B Phone", "A Phone"))
        ):
            self.assertEqual(list_profiles(), list_profiles())


class ChooseProfileTests(unittest.TestCase):
    def test_choose_by_slugified_id(self) -> None:
        profiles = {
            "Fancy Phone X": make_profile(name="Fancy Phone X"),
            "Other": make_profile(name="Other"),
        }
        with mock.patch.dict(
            sys.modules,
            fake_hw_modules(names=tuple(profiles), profiles=profiles),
        ):
            device = choose_profile("fancy-phone-x")
        self.assertIsNotNone(device)
        assert device is not None
        self.assertEqual(device["id"], "fancy-phone-x")
        self.assertEqual(device["name"], "Fancy Phone X")
        self.assertEqual(device["source"], "manual")
        self.assertEqual(device["battery_health"], 0.87)
        self.assertFalse(device["calibrated"])

    def test_unknown_id_returns_none(self) -> None:
        profiles = {"Only Phone": make_profile(name="Only Phone")}
        with mock.patch.dict(
            sys.modules,
            fake_hw_modules(names=tuple(profiles), profiles=profiles),
        ):
            self.assertIsNone(choose_profile("missing"))
            self.assertIsNone(choose_profile(""))

    def test_missing_hw_returns_none(self) -> None:
        with mock.patch.dict(sys.modules, missing_hw_modules()):
            self.assertIsNone(choose_profile(STUB_ID))
            self.assertIsNone(choose_profile("generic-phone"))

    def test_store_failures_return_none(self) -> None:
        def broken_names() -> tuple[str, ...]:
            raise RuntimeError("names failed")

        def broken_profile(name: str) -> SimpleNamespace:
            raise KeyError(name)

        with mock.patch.dict(
            sys.modules, fake_hw_modules(names_impl=broken_names)
        ):
            self.assertIsNone(choose_profile("anything"))
        profiles = {"A Phone": make_profile(name="A Phone")}
        with mock.patch.dict(
            sys.modules,
            fake_hw_modules(
                names=tuple(profiles),
                profiles=profiles,
                profile_impl=broken_profile,
            ),
        ):
            self.assertIsNone(choose_profile("a-phone"))

    def test_idempotent(self) -> None:
        profiles = {"A Phone": make_profile(name="A Phone")}
        with mock.patch.dict(
            sys.modules,
            fake_hw_modules(names=tuple(profiles), profiles=profiles),
        ):
            self.assertEqual(
                choose_profile("a-phone"), choose_profile("a-phone")
            )


class ToConfigDeviceTests(unittest.TestCase):
    def test_keeps_only_config_keys(self) -> None:
        self.assertEqual(
            to_config_device(profile_to_device(make_profile())),
            {
                "id": "test-phone-5g",
                "name": "Test Phone 5G",
                "source": "hw-adapt",
                "battery_health": 0.87,
            },
        )

    def test_stub_config(self) -> None:
        self.assertEqual(
            to_config_device(stub_device()),
            {
                "id": STUB_ID,
                "name": "Generic phone (stub)",
                "source": "stub",
                "battery_health": None,
            },
        )

    def test_extra_keys_are_dropped_and_input_is_unchanged(self) -> None:
        device = stub_device()
        device["extra"] = "value"
        original = dict(device)
        result = to_config_device(device)
        self.assertNotIn("extra", result)
        self.assertNotIn("radios", result)
        self.assertNotIn("modem", result)
        self.assertNotIn("calibrated", result)
        self.assertEqual(device, original)

    def test_idempotent(self) -> None:
        config = to_config_device(profile_to_device(make_profile()))
        self.assertEqual(to_config_device(config), config)
        self.assertEqual(to_config_device(to_config_device(config)), config)


class NoSideEffectsTests(unittest.TestCase):
    def test_read_only_operations_create_no_files_or_commands(self) -> None:
        runner = FakeRunner(dry_run=True)
        profiles = {"A Phone": make_profile(name="A Phone")}
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            with mock.patch.dict(
                sys.modules,
                fake_hw_modules(names=tuple(profiles), profiles=profiles),
            ):
                detect_device(root=tmp, config_dir=config_dir)
                list_profiles()
                device = choose_profile("a-phone")
                self.assertIsNotNone(device)
                to_config_device(device or stub_device())
                calibration_present(config_dir)
            self.assertEqual(list(config_dir.iterdir()), [])
        self.assertEqual(runner.commands, [])


class TestNoSilentExceptPass(unittest.TestCase):
    def test_no_except_handler_with_only_pass(self) -> None:
        target = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "opos"
            / "setup"
            / "device.py"
        )
        tree = ast.parse(target.read_text(encoding="utf-8"))
        lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            and len(node.body) == 1
            and isinstance(node.body[0], ast.Pass)
        ]
        self.assertEqual(lines, [], f"silent except-pass at lines {lines}")


if __name__ == "__main__":
    unittest.main()
