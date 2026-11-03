# SPDX-License-Identifier: GPL-3.0-or-later
"""Battery sysfs reader."""
from __future__ import annotations

import contextlib
from pathlib import Path


def _read_text(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return None


def _read_int(path: Path) -> int | None:
    txt = _read_text(path)
    if txt is None or txt == "":
        return None
    try:
        return int(txt)
    except Exception:
        return None


def read_battery(root: str | Path = "/") -> dict:
    root_path = Path(root)
    base = root_path / "sys/class/power_supply"
    present = False
    statuses: list[str] = []
    capacities: list[int] = []
    health_ratios: list[float] = []
    ch_full_sum = 0
    ch_design_sum = 0
    en_full_sum = 0
    en_design_sum = 0
    conv_full_sum = 0.0
    conv_design_sum = 0.0
    ch_cnt = 0
    en_cnt = 0
    conv_possible = True
    present_cnt = 0
    energy_now_sum = 0
    energy_full_sum2 = 0
    charge_now_sum = 0
    charge_full_sum2 = 0
    energy_direct_possible = True
    charge_direct_possible = True
    w_cap: list[int | None] = []
    w_en: list[int | None] = []
    w_ef: list[int | None] = []
    w_cn: list[int | None] = []
    w_cf: list[int | None] = []
    with contextlib.suppress(Exception):
        if base.is_dir():
            for entry in base.iterdir():
                try:
                    t = _read_text(entry / "type")
                    if t != "Battery":
                        continue
                    present_attr = _read_text(entry / "present")
                    if present_attr == "0":
                        continue
                    present = True
                    s = _read_text(entry / "status")
                    if s:
                        statuses.append(s)
                    cap = _read_int(entry / "capacity")
                    if cap is not None:
                        if cap < 0:
                            cap = 0
                        elif cap > 100:
                            cap = 100
                        capacities.append(cap)
                    cf = _read_int(entry / "charge_full")
                    cfd = _read_int(entry / "charge_full_design")
                    ef = _read_int(entry / "energy_full")
                    efd = _read_int(entry / "energy_full_design")
                    if ef is not None and efd is not None and efd > 0:
                        full = ef
                        if full < 0:
                            full = 0
                        elif full > efd:
                            full = efd
                        health_ratios.append(float(full / efd))
                        en_full_sum += full
                        en_design_sum += efd
                        en_cnt += 1
                    elif (
                        cf is not None
                        and cfd is not None
                        and cf > 0
                        and cfd > 0
                        and cf <= cfd
                    ):
                        health_ratios.append(float(cf / cfd))
                        ch_full_sum += cf
                        ch_design_sum += cfd
                        ch_cnt += 1
                        volt = _read_int(entry / "voltage_min_design")
                        if volt is None or volt <= 0:
                            volt = _read_int(entry / "voltage_now")
                        if volt is not None and volt > 0:
                            conv_full_sum += float(cf * volt / 1000000)
                            conv_design_sum += float(cfd * volt / 1000000)
                        else:
                            conv_possible = False
                    present_cnt += 1
                    en = _read_int(entry / "energy_now")
                    cn = _read_int(entry / "charge_now")
                    if en is not None and ef is not None and ef != 0:
                        energy_now_sum += en
                        energy_full_sum2 += ef
                    else:
                        energy_direct_possible = False
                    if cn is not None and cf is not None and cf != 0:
                        charge_now_sum += cn
                        charge_full_sum2 += cf
                    else:
                        charge_direct_possible = False
                    w_cap.append(cap)
                    w_en.append(en)
                    w_ef.append(ef)
                    w_cn.append(cn)
                    w_cf.append(cf)
                except Exception:
                    continue
    health: float | None = None
    health_coverage: float | None = None
    try:
        if ch_cnt > 0 and en_cnt == 0 and ch_design_sum > 0:
            health = float(ch_full_sum / ch_design_sum)
        elif en_cnt > 0 and ch_cnt == 0 and en_design_sum > 0:
            health = float(en_full_sum / en_design_sum)
        elif ch_cnt > 0 and en_cnt > 0 and conv_possible:
            total_full = conv_full_sum + float(en_full_sum)
            total_design = conv_design_sum + float(en_design_sum)
            if total_design > 0:
                health = float(total_full / total_design)
            elif health_ratios:
                health = float(sum(health_ratios) / len(health_ratios))
        elif health_ratios:
            health = float(sum(health_ratios) / len(health_ratios))
        if health is not None:
            if health < 0.0:
                health = 0.0
            elif health > 1.0:
                health = 1.0
        if present_cnt > 0:
            health_coverage = float(len(health_ratios) / present_cnt)
    except Exception:
        health = None
        health_coverage = None
    status: str | None = None
    if statuses:
        status = statuses[0]
    capacity_pct: int | None = None
    try:
        if present_cnt > 0 and energy_direct_possible and energy_full_sum2 != 0:
            v = int(energy_now_sum / energy_full_sum2 * 100)
            if v < 0:
                v = 0
            elif v > 100:
                v = 100
            capacity_pct = v
        elif present_cnt > 0 and charge_direct_possible and charge_full_sum2 != 0:
            v = int(charge_now_sum / charge_full_sum2 * 100)
            if v < 0:
                v = 0
            elif v > 100:
                v = 100
            capacity_pct = v
        elif present_cnt > 0 and len(w_cap) == present_cnt:
            pcts: list[int] = []
            complete = True
            for ca, en2, ef2, cn2, cf2 in zip(w_cap, w_en, w_ef, w_cn, w_cf):
                if ca is not None:
                    pcts.append(ca)
                elif en2 is not None and ef2 is not None and ef2 != 0:
                    q = int(en2 / ef2 * 100)
                    if q < 0:
                        q = 0
                    elif q > 100:
                        q = 100
                    pcts.append(q)
                elif cn2 is not None and cf2 is not None and cf2 != 0:
                    q = int(cn2 / cf2 * 100)
                    if q < 0:
                        q = 0
                    elif q > 100:
                        q = 100
                    pcts.append(q)
                else:
                    complete = False
                    break
            if complete and len(pcts) == present_cnt:
                if all(x is not None and x != 0 for x in w_ef):
                    total_w = sum(w_ef)  # type: ignore[arg-type]
                    total_c = sum(c * w for c, w in zip(pcts, w_ef))  # type: ignore[arg-type]
                    v = int(total_c / total_w) if total_w != 0 else 0
                    if v < 0:
                        v = 0
                    elif v > 100:
                        v = 100
                    capacity_pct = v
                elif all(x is not None and x != 0 for x in w_cf):
                    total_w = sum(w_cf)  # type: ignore[arg-type]
                    total_c = sum(c * w for c, w in zip(pcts, w_cf))  # type: ignore[arg-type]
                    v = int(total_c / total_w) if total_w != 0 else 0
                    if v < 0:
                        v = 0
                    elif v > 100:
                        v = 100
                    capacity_pct = v
                else:
                    capacity_pct = min(pcts)
    except Exception:
        capacity_pct = None
    return {"present": present, "status": status, "capacity_pct": capacity_pct, "health": health, "health_coverage": health_coverage}


def _net_ifaces(root_path: Path) -> list[str]:
    result: list[str] = []
    with contextlib.suppress(Exception):
        p = root_path / "sys/class/net"
        if p.is_dir():
            for e in p.iterdir():
                try:
                    result.append(e.name)
                except Exception:
                    continue
    return result


def _rfkill_types(root_path: Path) -> list[str]:
    result: list[str] = []
    with contextlib.suppress(Exception):
        p = root_path / "sys/class/rfkill"
        if p.is_dir():
            for e in p.iterdir():
                try:
                    t = _read_text(e / "type")
                    if t:
                        result.append(t)
                except Exception:
                    continue
    return result


def read_radios(root: str | Path = "/") -> dict:
    root_path = Path(root)
    net = _net_ifaces(root_path)
    rfkill = _rfkill_types(root_path)
    radios: list[str] = []
    with contextlib.suppress(Exception):
        wifi_by_net = any(n.lower().startswith(("wlan", "wlp", "wifi", "wl")) for n in net)
        wifi_by_rfkill = any("wlan" in t.lower() or "wifi" in t.lower() for t in rfkill)
        if wifi_by_net or wifi_by_rfkill:
            radios.append("wifi")
        bt_by_rfkill = any("bluetooth" in t.lower() for t in rfkill)
        if bt_by_rfkill:
            radios.append("bluetooth")
        wwan_by_net = any(n.lower().startswith(("wwan", "rmnet")) for n in net)
        wwan_by_rfkill = any("wwan" in t.lower() for t in rfkill)
        if wwan_by_net or wwan_by_rfkill:
            radios.append("wwan")
    return {"net_ifaces": net, "rfkill_types": rfkill, "radios_present": radios}
