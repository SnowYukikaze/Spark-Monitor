# -*- coding: utf-8 -*-
"""ATK（VXE / Dragonfly 系列）私有协议，两套并存：

协议 2  0xFF05 使用页，输出 64 字节
    请求  08 7D 72 02 00 01 07 01 （补齐 64）
    回复  ?? 72 ... 07 ... ，电量在 rep[7]

协议 1  0xFF02 使用页，输出 17 字节
    请求  08 04 00*14 <校验>
    回复  08 04 ...，电量 rep[6]，rep[7]!=0 表示有线模式
    校验  (0x55 - (REPORT_ID + sum(cmd[0..14]))) & 0xFF
"""
from __future__ import annotations

import time
from typing import Optional

from ..hidapi import HidDevice, HidIface, HidError
from ..model import Reading
from .base import DeviceProtocol, register

ATK_VIDS = (0x373B, 0x3554)

ATK2_PAYLOAD = bytes([0x7D, 0x72, 0x02, 0x00, 0x01, 0x07, 0x01])
ATK1_PAYLOAD = bytes([0x04] + [0x00] * 15)   # 16 字节命令，cmd[15] 由校验填


def atk1_checksum(report_id: int, cmd: bytes) -> int:
    total = report_id + sum(cmd[:15])
    return (0x55 - (total & 0xFF)) & 0xFF


def atk_request2(report_id: int = 0x08, total: int = 64) -> bytes:
    body = bytes([report_id]) + ATK2_PAYLOAD
    return body + bytes(total - len(body))


def atk_request1(report_id: int = 0x08, total: int = 17) -> bytes:
    cmd = bytearray(ATK1_PAYLOAD)
    cmd[15] = atk1_checksum(report_id, bytes(cmd))
    body = bytes([report_id]) + bytes(cmd)
    return body + bytes(max(0, total - len(body)))


def atk1_valid(rep: bytes) -> bool:
    if len(rep) < 17 or rep[0] != 0x08 or rep[1] != 0x04:
        return False
    return (sum(rep[:16]) + rep[16]) & 0xFF == 0x55


def read_atk(iface: HidIface, kind: str) -> Reading:
    total = iface.output_len or (64 if kind == "atk2" else 17)
    if kind == "atk2":
        if total < 64:
            return Reading(ok=False, source="ATK 协议 2",
                           detail=f"输出报文只有 {total} 字节，不是 64 字节接口")
        req, want = atk_request2(0x08, total), 64
    else:
        if total < 17:
            return Reading(ok=False, source="ATK 协议 1",
                           detail=f"输出报文只有 {total} 字节")
        req, want = atk_request1(0x08, total), 17
    dev = HidDevice(iface.path, write=True)
    try:
        last = b""
        for _ in range(3):
            dev.drain()
            dev.write_output(req)
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline:
                rep = dev.read_input(max(want, iface.input_len), 250)
                if not rep:
                    continue
                last = rep
                if kind == "atk2":
                    if len(rep) > 7 and rep[1] == 0x72 and rep[5] == 0x07:
                        return Reading(ok=True,
                                       level=max(0, min(100, rep[7])),
                                       source="ATK 协议 2（0xFF05/64B）",
                                       detail=f"回复 {rep[:12].hex(' ')}",
                                       raw=rep)
                else:
                    if atk1_valid(rep):
                        wired = len(rep) > 7 and rep[7] != 0
                        return Reading(ok=True,
                                       level=max(0, min(100, rep[6])),
                                       status="wired" if wired else "discharging",
                                       source="ATK 协议（0xFF02/17B，0x55 校验）",
                                       detail=f"电量字节=0x{rep[6]:02X} "
                                              f"校验=0x{rep[16]:02X}", raw=rep)
        return Reading(ok=False, source=f"ATK {kind}",
                       detail=("设备没有给出有效回复（鼠标可能在休眠，动一下鼠标）"
                               if not last else f"回复无效：{last[:12].hex(' ')}"))
    except HidError as e:
        return Reading(ok=False, source=f"ATK {kind}", detail=str(e))
    finally:
        dev.close()


@register
class ATK2(DeviceProtocol):
    id = "atk2"
    name = "ATK 协议 2（0xFF05/64B）"
    priority = 40
    auto = False

    def match(self, iface: HidIface) -> bool:
        return iface.vid in ATK_VIDS and iface.output_len >= 64

    def read(self, iface: HidIface) -> Optional[Reading]:
        return read_atk(iface, "atk2")


@register
class ATK1(DeviceProtocol):
    id = "atk1"
    name = "ATK 协议 1（0xFF02/17B）"
    priority = 41
    auto = False

    def match(self, iface: HidIface) -> bool:
        return iface.vid in ATK_VIDS and iface.output_len >= 17

    def read(self, iface: HidIface) -> Optional[Reading]:
        return read_atk(iface, "atk1")
