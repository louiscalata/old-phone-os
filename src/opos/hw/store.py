# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import dataclass

from opos.hw.device import SURFACE_DUO, DeviceFacts


@dataclass(frozen=True)
class Battery:
    health: float | None


@dataclass(frozen=True)
class Profile:
    name: str
    radios: tuple[str, ...]
    battery: Battery
    modem: bool
    facts: DeviceFacts | None = None  # the declared hardware facts (opos.hw.device); None when none are declared


# Battery health is None because it is not measured
BUILTIN: dict[str, Profile] = {
    "Surface Duo": Profile(
        name="Surface Duo",
        radios=("wifi", "bluetooth", "lte", "nfc"),
        battery=Battery(None),
        modem=True,
        facts=SURFACE_DUO,
    ),
    "Generic phone": Profile(
        name="Generic phone",
        radios=("wifi", "bluetooth"),
        battery=Battery(None),
        modem=False,
    ),
}


def builtin_names() -> list[str]:
    return list(BUILTIN.keys())


def builtin_profile(name: str) -> Profile:
    return BUILTIN[name]
