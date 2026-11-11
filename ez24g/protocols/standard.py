# -*- coding: utf-8 -*-
"""标准 HID 电量：HID Usage Tables 里的 Battery Level。

能走到这里的设备都遵守同一套规范：
    UsagePage Usage
    0x0006 / 0x0020   Generic Device Controls : Battery Strength
    0x000D / 0x003B   Physical Interface     : Battery Level
    0x0085 / 0x0065   Battery System         : Battery Level
读 Feature Report 或监听 Input Report 都能拿到百分比。
"""
from __future__ import annotations

import time
from typing import Optional

from ..hidapi import HidDevice, HidIface, HidError
from ..model import Reading
from ..win32 import HIDP_FEATURE, HIDP_INPUT
from .base import DeviceProtocol, register

BATTERY_USAGES = ((0x0006, 0x0020), (0x000D, 0x003B), (0x0085, 0x0065))


def _usage(cap) -> int:
    return cap.u.NotRange.Usage if not cap.IsRange else cap.u.Range.UsageMin


def _clamp(v) -> int:
    return max(0, min(100, int(v)))


def read_feature_battery(iface: HidIface) -> Optional[Reading]:
    """读 Feature Report 里的标准电量字段。"""
    if not iface.feature_len:
        return None
    dev = HidDevice(iface.path, write=False)
    try:
        for cap in dev.value_caps(HIDP_FEATURE):
            usage = _usage(cap)
            if (cap.UsagePage, usage) not in BATTERY_USAGES:
                continue
            rep = dev.get_feature(cap.ReportID or iface.feature_len)
            if not rep:
                continue
            val = dev.usage_value(HIDP_FEATURE, rep, cap)
            if val is None:
                continue
            return Reading(ok=True, level=_clamp(val),
                           source="标准 HID 电量字段",
                           detail=f"UsagePage=0x{cap.UsagePage:04X} "
                                  f"Usage=0x{usage:04X} 原={val}", raw=rep)
    except HidError as e:
        return Reading(ok=False, source="标准 HID 电量字段", detail=str(e))
    finally:
        dev.close()
    return None


def listen_battery(iface: HidIface, seconds: float = 2.0) -> Optional[Reading]:
    """被动监听 Input Report 里的标准电量字段。"""
    if not iface.input_len:
        return None
    try:
        dev = HidDevice(iface.path, write=False)
    except HidError:
        return None
    try:
        caps = [c for c in dev.value_caps(HIDP_INPUT)
                if (c.UsagePage, _usage(c)) == (0x0006, 0x0020)]
        if not caps:
            return None
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            rep = dev.read_input(iface.input_len, 250)
            if not rep:
                continue
            for cap in caps:
                val = dev.usage_value(HIDP_INPUT, rep, cap)
                if val is not None:
                    return Reading(ok=True, level=_clamp(val),
                                   source="输入报告电量字段",
                                   detail=f"原值={val}", raw=rep)
        return Reading(ok=False, source="输入报告电量字段",
                       detail="监听期间没有收到电量字段")
    finally:
        dev.close()


@register
class StandardBattery(DeviceProtocol):
    id = "standard"
    name = "标准 HID 电量"
    priority = 10          # 规范优先：能读就绝不去猜厂商协议

    def match(self, iface: HidIface) -> bool:
        return bool(iface.feature_len)

    def read(self, iface: HidIface) -> Optional[Reading]:
        return read_feature_battery(iface)


@register
class ListenBattery(DeviceProtocol):
    """兜底：有些设备只在 Input Report 里上报电量，从不出现在 Feature 里。"""

    id = "listen"
    name = "输入报告电量"
    priority = 190

    def match(self, iface: HidIface) -> bool:
        return bool(iface.input_len)

    def read(self, iface: HidIface) -> Optional[Reading]:
        return listen_battery(iface, 0.6)
