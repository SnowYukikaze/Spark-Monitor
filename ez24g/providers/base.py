# -*- coding: utf-8 -*-
"""provider 共用的数据结构（单独放一个文件，避免包内循环 import）。"""
from __future__ import annotations

import dataclasses

TRANSPORT_HID = "hid"
TRANSPORT_BT = "bt"


@dataclasses.dataclass
class DeviceRef:
    """一台可读设备的引用。HID 带 ifaces，BLE 带 addr。"""

    key: str
    kind: str = TRANSPORT_HID
    name: str = ""
    transport: str = TRANSPORT_HID
    vid: int = 0
    pid: int = 0
    addr: str = ""
    product: str = ""
    usage_page: int = 0
    ifaces: list = dataclasses.field(default_factory=list)
    connected: bool = True
    classic: bool = False        # True = 经典蓝牙（HFP），电量走系统属性而不是 GATT
    bt_linked: str = ""          # 非空 = 这台 HID 其实就是某个 MAC 的蓝牙设备
    detail: str = ""
