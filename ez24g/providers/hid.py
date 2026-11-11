# -*- coding: utf-8 -*-
"""HID 来源：2.4G 接收器 + 蓝牙 HID（走标准 HID 电量字段的设备）。

枚举规则（和旧 discover_extras 的差别只有第 3 条）：
    1. 厂商页（UsagePage >= 0xFF00）的接口照旧算一台设备；
    2. HIDI2C / ACPI 继续跳过（笔记本内置触控板、传感器）；
    3. **新增**：非厂商页但带标准电量字段（0x0006/0x0020、0x000D/0x003B、
       0x0085/0x0065）的设备也要算一台 —— 蓝牙 HID 鼠标/键盘就是这种。

读取仍然全部交给 protocols.read_best()，这一层不碰任何协议细节。
"""
from __future__ import annotations

from typing import Optional

from .. import config
from ..hidapi import HidDevice, HidIface, enumerate_hid
from ..model import Reading
from ..protocols import read_best
from ..win32 import HIDP_FEATURE, HIDP_INPUT
from . import bt_pnp
from .base import DeviceRef

_SKIP_HINTS = ("HIDI2C", "ACPI")

# 标准 HID 电量字段：必须 Page 和 Usage 都对上，光看 Page 会误判
# （0x000D 是 PID 页，触控板也有一堆，但那不是电量）
_BATTERY_USAGES = ((0x0006, 0x0020), (0x000D, 0x003B), (0x0085, 0x0065))

# 每个接口路径探一次就够（插拔后路径不变）
_caps_cache: dict[str, bool] = {}


def _usage(cap) -> int:
    return cap.u.NotRange.Usage if not cap.IsRange else cap.u.Range.UsageMin


def _has_battery_usage(cap) -> bool:
    """描述符语义扩展：Range 型 ValueCap 也要覆盖到，别只看 UsageMin。"""
    up = cap.UsagePage
    if not cap.IsRange:
        return (up, cap.u.NotRange.Usage) in _BATTERY_USAGES
    lo = cap.u.Range.UsageMin
    hi = min(cap.u.Range.UsageMax, lo + 256)
    return any((up, u) in _BATTERY_USAGES for u in range(lo, hi + 1))


def _skipped(iface: HidIface) -> bool:
    inst = (iface.instance or "").upper()
    prod = (iface.product or "").upper()
    parent = (iface.parent or "").upper()
    return any(h in inst or h in prod or h in parent for h in _SKIP_HINTS)


def is_internal_iface(iface: HidIface) -> bool:
    """主板内置 HID（HIDI2C / ACPI）：不是外接设备，手动添加清单要跳过。"""
    return _skipped(iface)


def _caps_battery(iface: HidIface) -> bool:
    """这个接口的报文描述符里有没有标准电量字段（Feature 或 Input）。"""
    key = iface.path
    hit = _caps_cache.get(key)
    if hit is not None:
        return hit
    found = False
    if iface.feature_len or iface.input_len:
        try:
            dev = HidDevice(iface.path, write=False)
        except Exception:
            return False
        try:
            for kind in (HIDP_FEATURE, HIDP_INPUT):
                for cap in dev.value_caps(kind):
                    if _has_battery_usage(cap):
                        found = True
                        break
                if found:
                    break
        except Exception:
            found = False
        finally:
            dev.close()
    _caps_cache[key] = found
    return found


def _meta_iface(cand: list[HidIface]) -> HidIface:
    """优先拿厂商使用页那条接口，界面上的 UP 值才是设备真实身份。"""
    return next((d for d in cand if d.is_vendor), cand[0])


def _wanted(cand: list[HidIface], caps_fn=None) -> bool:
    """这台设备值不值得出现在列表里。

    厂商页 → 值得；否则看报文描述符里有没有标准电量字段
    （蓝牙 HID 鼠标/键盘就是这一类）。
    """
    if any(d.is_vendor and (d.output_len >= 2 or d.input_len >= 2) for d in cand):
        return True
    probe = caps_fn or _caps_battery
    return any(probe(d) for d in cand)


def enumerate_refs(ifaces: Optional[list[HidIface]] = None,
                   caps_fn=None) -> list[DeviceRef]:
    ifaces = ifaces if ifaces is not None else enumerate_hid()
    # 拔掉的接口路径清出缓存，避免长期运行时缓存无限增长
    alive = {d.path for d in ifaces}
    for stale in [k for k in _caps_cache if k not in alive]:
        _caps_cache.pop(stale, None)
    groups: dict[str, list[HidIface]] = {}
    for d in ifaces:
        if _skipped(d):
            continue
        groups.setdefault(config.device_key(d.vid, d.pid), []).append(d)

    refs: list[DeviceRef] = []
    for key, cand in groups.items():
        if not _wanted(cand, caps_fn):
            continue
        meta = _meta_iface(cand)
        # 挂在 BTHENUM / BTHLEDEVICE 下的 HID 就是蓝牙设备，卡片要标「蓝牙」
        bluetooth = any(d.is_bluetooth for d in cand)
        mac = ""
        if bluetooth:
            # 记下 MAC：上层用它和「蓝牙身份」的那条交叉去重，
            # 否则同一副耳机会以 HID + 蓝牙两种身份各出一张卡
            for d in cand:
                if not d.is_bluetooth:
                    continue
                try:
                    mac = bt_pnp.mac_from_instance(d.parent or d.instance or "")
                except Exception:
                    mac = ""
                if mac:
                    break
        refs.append(DeviceRef(
            key=key, kind="hid",
            transport="bt" if bluetooth else "hid",
            name=(meta.product or cand[0].product or ""),
            product=meta.product, vid=meta.vid, pid=meta.pid,
            usage_page=meta.usage_page, ifaces=cand,
            addr=mac, bt_linked=mac))
    return refs


def read_ref(ref: DeviceRef, include_manual: bool = False,
             prefer_id: Optional[str] = None) -> Optional[Reading]:
    # G：挂在蓝牙下的 HID 接口，先复用系统已经缓存好的电量属性（零新增读取）
    if ref.transport == "bt":
        for d in ref.ifaces:
            if not d.is_bluetooth:
                continue
            addr = bt_pnp.mac_from_instance(d.parent or d.instance or "")
            if not addr:
                continue
            try:
                level = bt_pnp.read_level(addr)
            except Exception:
                level = None
            if level is not None:
                return Reading(ok=True, level=int(level),
                               status="discharging", transport="bt",
                               source="蓝牙 HID（系统电量属性）",
                               detail=f"系统属性电量 = {level}",
                               name=ref.name, addr=addr,
                               vid=d.vid, pid=d.pid)
    return read_best(ref.ifaces, include_manual=include_manual,
                     prefer_id=prefer_id)
