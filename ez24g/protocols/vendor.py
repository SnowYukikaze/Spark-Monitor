# -*- coding: utf-8 -*-
"""另外三家的小协议，都靠 VID 精确匹配，互不干扰。

Endgame Gear   Feature 0xA1 / 64B，rep[16] = 电量
VAXEE / ZYGEN  Feature 0x0E / 64B，rep[5] * 5 = 电量（5% 步进）
Nordic nRF     Feature 0x05 / 32B，rep[9] = 电量
"""
from __future__ import annotations

import time
from typing import Optional

from ..hidapi import HidDevice, HidIface, HidError
from ..model import Reading
from .base import DeviceProtocol, register


def read_endgame(iface: HidIface) -> Reading:
    try:
        dev = HidDevice(iface.path, write=True)
    except HidError as e:
        return Reading(ok=False, source="Endgame 协议", detail=str(e))
    try:
        for _ in range(3):
            dev.set_feature(bytes([0xA1, 0xB4]) + bytes(62))
            time.sleep(0.3)
            rep = dev.get_feature_report(0xA1, 64)
            if (rep and len(rep) > 17 and rep[1] == 0x01
                    and 0 <= rep[16] <= 100):
                return Reading(ok=True, level=rep[16],
                               source="Endgame 协议（Feature 0xA1）",
                               detail=f"原始 {rep[:20].hex(' ')}", raw=rep)
        return Reading(ok=False, source="Endgame 协议",
                       detail="没有有效回复（鼠标可能休眠）")
    except HidError as e:
        return Reading(ok=False, source="Endgame 协议", detail=str(e))
    finally:
        dev.close()


def read_vaxee(iface: HidIface) -> Reading:
    try:
        dev = HidDevice(iface.path, write=True)
    except HidError as e:
        return Reading(ok=False, source="VAXEE 协议", detail=str(e))
    try:
        for _ in range(2):
            dev.set_feature(bytes([0x0E, 0xA5, 0x0B, 0x01, 0x01]) + bytes(59))
            time.sleep(0.15)
            rep = dev.get_feature_report(0x0E, 64)
            if rep and len(rep) > 5 and 0 <= rep[5] <= 20:
                return Reading(ok=True, level=rep[5] * 5,
                               source="VAXEE 协议（Feature 0x0E）",
                               detail=f"原始 {rep[:10].hex(' ')}", raw=rep)
        return Reading(ok=False, source="VAXEE 协议", detail="没有有效回复")
    except HidError as e:
        return Reading(ok=False, source="VAXEE 协议", detail=str(e))
    finally:
        dev.close()


def read_nordic(iface: HidIface) -> Reading:
    try:
        dev = HidDevice(iface.path, write=True)
    except HidError as e:
        return Reading(ok=False, source="Nordic 协议", detail=str(e))
    try:
        for _ in range(2):
            dev.set_feature(bytes([0x05, 0x15, 0x00, 0x00, 0x01]) + bytes(27))
            time.sleep(0.1)
            rep = dev.get_feature_report(0x05, 32)
            if rep and len(rep) > 9 and 0 <= rep[9] <= 100:
                return Reading(ok=True, level=rep[9],
                               source="Nordic 协议（Feature 0x05）",
                               detail=f"原始 {rep[:12].hex(' ')}", raw=rep)
        return Reading(ok=False, source="Nordic 协议", detail="没有有效回复")
    except HidError as e:
        return Reading(ok=False, source="Nordic 协议", detail=str(e))
    finally:
        dev.close()


@register
class Endgame(DeviceProtocol):
    id = "endgame"
    name = "Endgame 协议（Feature 0xA1）"
    priority = 47
    auto = False

    def match(self, iface: HidIface) -> bool:
        return iface.vid == 0x3367 and iface.feature_len >= 64

    def read(self, iface: HidIface) -> Optional[Reading]:
        return read_endgame(iface)


@register
class Vaxee(DeviceProtocol):
    id = "vaxee"
    name = "VAXEE / ZYGEN（Feature 0x0E）"
    priority = 48
    auto = False

    def match(self, iface: HidIface) -> bool:
        return iface.vid == 0x3057 and iface.feature_len >= 64

    def read(self, iface: HidIface) -> Optional[Reading]:
        return read_vaxee(iface)


@register
class Nordic(DeviceProtocol):
    id = "nordic"
    name = "Nordic nRF（Feature 0x05）"
    priority = 49
    auto = False

    def match(self, iface: HidIface) -> bool:
        return iface.vid == 0x1915 and iface.feature_len >= 32

    def read(self, iface: HidIface) -> Optional[Reading]:
        return read_nordic(iface)
