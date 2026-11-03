# SPDX-License-Identifier: GPL-3.0-or-later
import os

from opos.hw import store
from opos.hw.store import Battery, Profile


def detect(root: str = "/") -> Profile | None:
    try:
        candidates = [
            "proc/device-tree/model",
            "sys/firmware/devicetree/base/model",
            "sys/class/dmi/id/product_name",
        ]
        text: str | None = None
        for rel in candidates:
            path = os.path.join(root, rel)
            try:
                with open(path, "rb") as f:
                    data = f.read()
                decoded = data.decode(errors="ignore")
                decoded = decoded.replace("\x00", "").strip()
                text = decoded
                break
            except OSError:
                continue
            except Exception:
                continue
        if text is None:
            return detect_generic(root)
        lower = text.lower()
        for name, profile in store.BUILTIN.items():
            if name == "Generic phone":
                continue
            if name.lower() in lower:
                return profile
        return detect_generic(root)
    except Exception:
        return detect_generic(root)


def _entries(root: str, rel: str) -> list[str]:
    try:
        return os.listdir(os.path.join(root, rel))
    except OSError:
        return []


def detect_generic(root: str = "/") -> Profile:
    try:
        net = _entries(root, "sys/class/net")
        wifi = False
        for n in net:
            if os.path.isdir(os.path.join(root, "sys/class/net", n, "wireless")):
                wifi = True
                break
        if not wifi and _entries(root, "sys/class/ieee80211"):
            wifi = True
        bluetooth = bool(_entries(root, "sys/class/bluetooth"))
        lte = False
        for n in net:
            if n.startswith("wwan"):
                lte = True
                break
        if not lte and _entries(root, "sys/class/wwan"):
            lte = True
        found: list[str] = []
        if wifi:
            found.append("wifi")
        if bluetooth:
            found.append("bluetooth")
        if lte:
            found.append("lte")
        radios = tuple(found)
        return Profile(name="Generic phone", radios=radios, battery=Battery(None), modem="lte" in radios)
    except Exception:
        return Profile(name="Generic phone", radios=(), battery=Battery(None), modem=False)


def detect_or_generic(root: str = "/") -> Profile:
    try:
        prof = detect(root)
    except Exception:
        prof = None
    if isinstance(prof, Profile):
        return prof
    return detect_generic(root)
