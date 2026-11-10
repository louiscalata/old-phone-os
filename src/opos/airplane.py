# SPDX-License-Identifier: GPL-3.0-or-later
"""Airplane mode for phy0 and hci0, plus a confirm when Wi-Fi is the only route.

USB and other radios are left alone. Previous soft-block states are remembered
so a later restore can put them back. Not measured on a phone.
"""
from __future__ import annotations

TARGETS = ("phy0", "hci0")


def parse_rfkill(text: str) -> list[dict]:
    devices = []
    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line and not line.startswith((" ", "\t")) and ":" in line:
            number, _, rest = line.partition(":")
            name = rest.split(":")[0].strip()
            current = {"index": number.strip(), "name": name, "soft": False}
            devices.append(current)
            continue
        if current is not None and "soft blocked" in line.lower():
            current["soft"] = "yes" in line.lower()
    return devices


def engage_plan(text: str, modem_path: str = "/ril_0") -> dict:
    """Commands that block phy0 and hci0 and take the modem offline. Others are untouched."""
    saved = []
    commands = []
    for device in parse_rfkill(text):
        if device["name"] not in TARGETS:
            continue
        saved.append({"index": device["index"], "name": device["name"], "soft": device["soft"]})
        if not device["soft"]:
            commands.append(["rfkill", "block", device["index"]])
    commands.append([
        "gdbus", "call", "--system", "--dest", "org.ofono", "--object-path", modem_path,
        "--method", "org.ofono.Modem.SetProperty", "Online", "false",
    ])
    return {"saved": saved, "commands": commands}


def restore_plan(saved, modem_path: str = "/ril_0") -> list[list[str]]:
    """Unblock only the radios this mode blocked, then set the modem online."""
    commands = []
    for device in saved or []:
        if device.get("name") in TARGETS and not device.get("soft"):
            commands.append(["rfkill", "unblock", device["index"]])
    commands.append([
        "gdbus", "call", "--system", "--dest", "org.ofono", "--object-path", modem_path,
        "--method", "org.ofono.Modem.SetProperty", "Online", "true",
    ])
    return commands


def confirm_reason(action: str, status: dict) -> str | None:
    """A sentence when Forget or Wi-Fi off would drop the only route. USB counts as another route."""
    if action not in ("forget", "wifi-off"):
        return None
    wifi = (status.get("wifi") or {}).get("state") == "connected"
    usb_up = (status.get("usb") or {}).get("state") == "up"
    data_on = (status.get("data") or {}).get("state") == "on"
    if wifi and not usb_up and not data_on:
        return "Wi-Fi is the only route. Confirm before turning it off."
    return None
