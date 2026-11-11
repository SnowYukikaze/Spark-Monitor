# -*- coding: utf-8 -*-
"""MCHOSE V9 Turbo（C-Media dongle）。

向 Report ID 0x55 发 55 65 01 ...，回 55 65 <电量> <状态> ...
电量 = resp[2]（0x3C = 60%）。
"""
from __future__ import annotations

import time
from typing import Optional

from ..hidapi import HidDevice, HidIface, HidError
from ..model import Reading
from .base import DeviceProtocol, register

MCHOSE_IDS = ((0x3837, 0x600D), (0x1837, 0x600D), (0x3654, 0x6901))


def read_mchose(iface: HidIface, timeout: float = 1.0) -> Reading:
    dev = HidDevice(iface.path, write=True)
    try:
        req = bytes([0x55, 0x65, 0x01]) + bytes(max(0, iface.output_len - 3))
        dev.drain()
        dev.write_output(req)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rep = dev.read_input(max(64, iface.input_len), 250)
            if not rep or len(rep) < 3:
                continue
            if rep[0] == 0x55 and rep[1] == 0x65 and 0 <= rep[2] <= 100:
                return Reading(ok=True, level=rep[2],
                               source="MCHOSE 协议（0x55/0x65）",
                               detail=f"回复 {rep[:8].hex(' ')}", raw=rep)
        return Reading(ok=False, source="MCHOSE 协议",
                       detail="没有收到 0x55/0x65 应答（耳机可能没开机）")
    except HidError as e:
        return Reading(ok=False, source="MCHOSE 协议", detail=str(e))
    finally:
        dev.close()


@register
class MCHOSE(DeviceProtocol):
    id = "mchose"
    name = "MCHOSE 协议（0x55/0x65）"
    priority = 42
    auto = False

    def match(self, iface: HidIface) -> bool:
        return ((iface.vid, iface.pid) in MCHOSE_IDS
                and iface.output_len >= 3)

    def read(self, iface: HidIface) -> Optional[Reading]:
        return read_mchose(iface)
