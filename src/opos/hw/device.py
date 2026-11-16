# SPDX-License-Identifier: GPL-3.0-or-later
"""Hardware facts of a supported phone, declared once.

This is OPOS's device layer: the geometry and devices the image assumes, in one place. A second phone is a second
DeviceFacts, not a fork of the tree. Each value says whether it was measured on the phone; a value that was not comes
from the port's files or from mainline Linux and stays "not yet measured" until someone confirms it on the phone and
marks it measured here. A boot's opos-diag (python3 -m opos.hw.facts) compares eight of these with what the kernel
reports: model, compatible, panels, panel_px, lid_name, fuel_gauge_driver, fuel_gauges and backlights. It does not read output_px,
scale, span, hinge or session_uid. The copies already written by hand elsewhere (the server unit's OPOS_SPAN, opos-selftest's
OUTPUT_PX and OUTPUT_SCALE, posture.LID_NAME, the brightness unit's runtime directory) are checked against this file by
tests/test_hw_facts.py; they are not read from it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fact:
    value: object
    measured: bool
    source: str


@dataclass(frozen=True)
class DeviceFacts:
    model: Fact | None              # a part of /proc/device-tree/model
    compatible: Fact | None         # one entry of /proc/device-tree/compatible
    panels: Fact | None             # DRM connectors that list a mode
    panel_px: Fact | None           # (width, height) of each panel, device px
    output_px: Fact | None          # (width, height) of the one composed output, device px
    scale: Fact | None              # the compositor's output scale
    span: Fact | None               # one pane's width in CSS px
    hinge: Fact | None              # the hinge's width in CSS px
    lid_name: Fact | None           # the switch input device that reports the lid
    fuel_gauge_driver: Fact | None  # the power_supply name prefix of the fuel gauges
    fuel_gauges: Fact | None        # how many fuel gauges
    backlights: Fact | None         # how many /sys/class/backlight entries
    session_uid: Fact | None        # the uid of the session user (droidian)


PORT_PANELS = "the port's display-panels/qcom,sm8150-mtp.json (packaging/duo-os/README.md)"

SURFACE_DUO = DeviceFacts(
    model=Fact("Surface Duo", False, "the name opos.hw.detect looks for"),
    compatible=Fact("qcom,sm8150", False, "the SoC, Snapdragon 855 (docs/duo-android.md)"),
    panels=Fact(2, False, PORT_PANELS),
    panel_px=Fact((1350, 1800), False, PORT_PANELS),
    output_px=Fact((2784, 1800), False, "two 1350 px panels and an 84 px hinge, " + PORT_PANELS),
    scale=Fact(2.0, False, "phoc.ini output scale"),
    span=Fact(675, False, "1350 px at scale 2"),
    hinge=Fact(42, False, "84 px at scale 2"),
    lid_name=Fact("Surface Duo Lid", False, "src/opos/native/posture.py"),
    fuel_gauge_driver=Fact("bq27742", False, "mainline sm8150-microsoft-surface-duo.dts (packaging/duo-os/README.md)"),
    fuel_gauges=Fact(2, False, "mainline sm8150-microsoft-surface-duo.dts (packaging/duo-os/README.md)"),
    backlights=Fact(2, True, "reported by the PC session (packaging/duo-os/POWER.md)"),
    session_uid=Fact(32011, False, "the base rootfs's droidian user"),
)
