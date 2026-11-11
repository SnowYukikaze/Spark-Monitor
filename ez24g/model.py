# -*- coding: utf-8 -*-
"""数据模型：一次电量读取的结果。"""
from __future__ import annotations

import dataclasses
import time
from typing import Optional


STATUS_TEXT = {
    "charging": "充电中",
    "discharging": "放电中",
    "full": "已充满",
    "wired": "有线模式",
    "unknown": "未知",
    "asleep": "设备休眠",
    "unplugged": "未连接",
    "unknown_proto": "协议未知",
    "no_support": "蓝牙功能不可用",
}

# 卡片副行上的来源标签
TRANSPORT_TEXT = {
    "hid": "2.4G",
    "bt": "蓝牙",
}


@dataclasses.dataclass
class Reading:
    """一次读取的结果。UI 只依赖这个结构，不直接碰 HID。"""

    ok: bool = False
    level: Optional[int] = None
    status: str = "unknown"
    source: str = ""
    detail: str = ""
    name: str = ""
    raw: bytes = b""
    ts: float = dataclasses.field(default_factory=time.time)
    present: bool = True      # 接口是否存在（False = 真的没插）
    stale: bool = False       # True = 本次没读到，用的是上次成功值
    vid: int = 0
    pid: int = 0
    usage_page: int = 0
    hid_known: bool = False
    protocol_id: str = ""      # 哪个协议插件读到的，方便调试
    transport: str = "hid"     # hid = 2.4G 接收器 / 蓝牙 HID；bt = 蓝牙 BLE
    addr: str = ""             # 蓝牙 MAC，HID 设备为空
    group: str = ""            # 非空 = 这条读数代表一个设备组（g:{id}）
    pair_name: str = ""        # 罗技接收器上报的配对设备名（HID++ 0x0005）

    @property
    def text(self) -> str:
        return f"{self.level}%" if self.ok and self.level is not None else "未连接"

    @property
    def transport_text(self) -> str:
        return TRANSPORT_TEXT.get(self.transport, "2.4G")

    @property
    def ident(self) -> str:
        if self.transport == "bt" and self.addr:
            return self.addr.upper()
        return f"{self.vid:04X}:{self.pid:04X}" if self.vid else "----:----"

    def clamped(self, level: int) -> "Reading":
        self.ok = True
        self.level = max(0, min(100, int(level)))
        return self
