# -*- coding: utf-8 -*-
"""ITON 方案（机械师 L8 Air / L8 Max 等）的电量读取。

协议不是猜的：来自厂家驱动 `L8 Driver` 自带的通信库日志
（`D:\\L8Driver\\logs\\Iton_HIDMOU_Com_x86\\async_log*.log`，一万多条样本），
里面把每一次收发都按十六进制记下来了：

    发送  a0 50 00 00 …     64 字节，report id 0xa0，命令 0x50
    回应  a1 50 01 03 0d 02 04 25 45 44 …

电量在回应第 8 字节（0 起算）：
    索引  0   1   2  3  4  5  6  7   8   9
    值   a1  50  01 03 0d 02 04 25  45  44
                                    ↑ 电量 0x45 = 69%

判定依据（不是拿单个数字凑的）：
- 历史日志里这一列随时间单调下降，例如 2026-11-05 是
  80 → 79 → 77 → 75 → 73 → 71 → 70 → 67，充完电又从 88 往下走；
- 多次停在 100 封顶，正好是电量计的行为；
- 其余字节长期是固定值，只有这一列在缓慢变化。

实测 L8 Air（369B:0054）：连读 10 次全部一致、平均 14.7ms、最大 30.7ms；
读到的值与同一时刻驱动日志里的值完全相同。

安全边界：0x50 是「报告设备状态」，纯查询；本模块不发送任何设置类命令。
"""
from __future__ import annotations

import time
from typing import Optional

from ..hidapi import HidDevice, HidError, HidIface
from ..model import Reading
from .base import DeviceProtocol, register

__all__ = ["read_itons", "parse_status", "build_request", "ITON_VID",
           "CMD_STATUS", "LEVEL_OFFSET"]

# 只认 ITON 这家（机械师 L8 系列）。换别家发同样的命令可能被当成写操作。
ITON_VID = 0x369B
VENDOR_USAGE_PAGE = 0xFFA0

WRITE_REPORT_ID = 0xA0
READ_REPORT_ID = 0xA1
CMD_STATUS = 0x50
LEVEL_OFFSET = 8
MAX_LEVEL = 100


def build_request(output_len: int = 64, cmd: int = CMD_STATUS) -> bytes:
    """构造查询包：report id + 命令 + 补零。纯函数，方便自检逐字节校验。"""
    return bytes([WRITE_REPORT_ID, int(cmd) & 0xFF]) + bytes(max(0, output_len - 2))


def parse_status(rep) -> Optional[int]:
    """从状态报告里取电量。

    要求首两字节正好是「a1 50」回显，且第 8 字节在 0–100 内。
    不满足一律返回 None —— 宁可显示读不到，也不显示假数字。
    """
    if not rep:
        return None
    try:
        buf = bytes(rep)
    except Exception:
        return None
    if len(buf) <= LEVEL_OFFSET:
        return None
    if buf[0] != READ_REPORT_ID or buf[1] != CMD_STATUS:
        return None
    level = buf[LEVEL_OFFSET]
    if not 0 <= level <= MAX_LEVEL:
        return None
    return int(level)


def read_itons(iface: HidIface, timeout: float = 0.6) -> Reading:
    """发一次状态查询，读回电量。失败返回 ok=False，不抛异常。"""
    dev = HidDevice(iface.path, write=True)
    try:
        req = build_request(iface.output_len or 64)
        dev.drain()
        dev.write_output(req)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rep = dev.read_input(max(64, iface.input_len), 200)
            if not rep:
                continue
            level = parse_status(rep)
            if level is not None:
                return Reading(ok=True, level=level,
                               source="ITON 状态报告（0x50）",
                               detail=f"设备上报 {level}%",
                               raw=bytes(rep))
        return Reading(ok=False, source="ITON 状态报告",
                       detail="没有收到 0x50 状态报告（设备可能休眠或已断开）")
    except HidError as e:
        return Reading(ok=False, source="ITON 状态报告", detail=str(e))
    except Exception as e:
        return Reading(ok=False, source="ITON 状态报告", detail=f"读取失败：{e}")
    finally:
        dev.close()


@register
class ItonStatus(DeviceProtocol):
    id = "itons"
    name = "ITON 状态报告（0x50）"
    priority = 45          # 排在标准读法之后，标准读得到就不打扰设备
    auto = True            # 开箱即用，不需要先开「厂商探测」

    def match(self, iface: HidIface) -> bool:
        # 写窄：同一厂商 + 厂商页 + 收发都是长报文，避免误碰别的接口
        return (iface.vid == ITON_VID
                and iface.usage_page == VENDOR_USAGE_PAGE
                and (iface.output_len or 0) >= 2
                and (iface.input_len or 0) > LEVEL_OFFSET)

    def read(self, iface: HidIface) -> Optional[Reading]:
        return read_itons(iface)
