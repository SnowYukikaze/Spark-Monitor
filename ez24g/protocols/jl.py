# -*- coding: utf-8 -*-
"""杰理（JieLi）JL_RCSP 协议，常见于耳机 / 部分键鼠 dongle。

帧格式（帧体从 0xFE 开始）：
    FE seq ch 00 sub 00 00 len_lo len_hi payload... crc_hi crc_lo
    crc = CRC16-CCITT(0xFFFF, 0x1021)，覆盖 FE..payload
电量在 TLV 里：type=0x01 的第一个字节就是 0~100。
"""
from __future__ import annotations

import dataclasses
import time
from typing import Optional

from ..hidapi import HidDevice, HidIface, HidError
from ..model import Reading
from .base import DeviceProtocol, register


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 \
                else (crc << 1) & 0xFFFF
    return crc


def build_jl_frame(seq: int, payload: bytes, report_id: int = 0x00,
                   include_zero: bool = True, ch: int = 0x10,
                   sub: int = 0x02, total: int = 64) -> bytes:
    """组一帧。

    include_zero=True  帧体前有个 0x00 头（常见于 65 字节报告）
    include_zero=False Report ID 占第 0 字节，帧体直接从 0xFE 开始
    """
    f = bytearray(total)
    f[0] = report_id
    base = 1
    if include_zero:
        f[base] = 0x00
        base += 1
    start = base
    f[base] = 0xFE
    f[base + 1] = seq & 0xFF
    f[base + 2] = ch
    f[base + 3] = 0x00
    f[base + 4] = sub
    f[base + 5] = 0x00
    f[base + 6] = 0x00
    f[base + 7] = len(payload) & 0xFF
    f[base + 8] = (len(payload) >> 8) & 0xFF
    f[base + 9:base + 9 + len(payload)] = payload
    crc = crc16_ccitt(bytes(f[start:base + 9 + len(payload)]))
    f[base + 9 + len(payload)] = (crc >> 8) & 0xFF
    f[base + 10 + len(payload)] = crc & 0xFF
    return bytes(f)


@dataclasses.dataclass
class JLFrame:
    seq: int
    ch: int
    sub: int
    payload: bytes
    crc_ok: bool
    raw: bytes

    def tlvs(self):
        out, i, p = [], 0, self.payload
        while i + 2 <= len(p):
            t, ln = p[i], p[i + 1]
            out.append((t, p[i + 2:i + 2 + ln]))
            i += 2 + ln
        return out


def parse_jl_frame(report: bytes) -> Optional[JLFrame]:
    """在报文里找 0xFE 帧头（允许前 1~3 字节的 Report ID / 0x00）。"""
    for start in range(0, 4):
        if start + 12 > len(report):
            break
        if report[start] != 0xFE:
            continue
        ln = report[start + 7] | (report[start + 8] << 8)
        if ln > 60 or start + 9 + ln + 2 > len(report):
            continue
        payload = report[start + 9:start + 9 + ln]
        crc = (report[start + 9 + ln] << 8) | report[start + 10 + ln]
        return JLFrame(seq=report[start + 1], ch=report[start + 2],
                       sub=report[start + 4], payload=payload,
                       crc_ok=(crc == crc16_ccitt(report[start:start + 9 + ln])),
                       raw=report)
    return None


# 一轮尝试的总预算：真耳机 100ms 内就会回，等 1.2 秒还是空就说明没开机
# 或者根本不是杰理。不设上限的话一台设备要 4 种组合 × 1.2 秒，两三个
# 接口叠起来能把一轮刷新拖到十秒。
BUDGET = 1.5
_ROUND: dict[tuple[int, int], tuple[int, float]] = {}


def _deadline_for(key: tuple[int, int]) -> float:
    """同一轮刷新里多个接口共用一份预算（轮次由 protocols.begin_round 推进）。"""
    from .base import round_id
    rid = round_id()
    now = time.monotonic()
    hit = _ROUND.get(key)
    if hit is None or hit[0] != rid:
        dl = now + BUDGET
        _ROUND[key] = (rid, dl)
        return dl
    return hit[1]


def read_jl(iface: HidIface, seconds: float = 1.2) -> Reading:
    """在 64 字节厂商接口上尝试杰理 TLV 协议，自动探测 Report ID。"""
    deadline = _deadline_for((iface.vid, iface.pid))
    dev = HidDevice(iface.path, write=True)
    try:
        ids = [0x55, 0x00]
        for rid in range(0, 0x100):
            if rid in ids:
                continue
            if time.monotonic() > deadline:
                break
            try:
                if dev.set_output_report(bytes([rid]) + bytes(63)):
                    ids.append(rid)
                    break
            except HidError:
                continue
        for rid in ids:
            for include_zero in (True, False):
                left = deadline - time.monotonic()
                if left < 0.1:
                    return Reading(ok=False, source="杰理 JL_RCSP",
                                   detail="探测超时（这一轮先跳过，下轮再试）")
                frame = build_jl_frame(0, b"\x01\x00", report_id=rid,
                                       include_zero=include_zero, total=64)
                try:
                    dev.drain()
                    dev.write_output(frame)
                except HidError:
                    continue
                wait_until = time.monotonic() + min(seconds, left)
                while time.monotonic() < wait_until:
                    rep = dev.read_input(64, 200)
                    if not rep:
                        continue
                    fr = parse_jl_frame(rep)
                    if not fr:
                        continue
                    for t, v in fr.tlvs():
                        if t == 0x01 and v:
                            return Reading(ok=True,
                                           level=max(0, min(100, v[0])),
                                           source="杰理 JL_RCSP（TLV 0x01）",
                                           detail=f"ReportID=0x{rid:02X} "
                                                  f"回复 {rep[:16].hex(' ')}",
                                           raw=rep)
        return Reading(ok=False, source="杰理 JL_RCSP",
                       detail="耳机没有上报电量（确认耳机已开机并与 dongle 连上）")
    except HidError as e:
        return Reading(ok=False, source="杰理 JL_RCSP", detail=str(e))
    finally:
        dev.close()


@register
class JieLi(DeviceProtocol):
    id = "jieli"
    name = "杰理 JL_RCSP"
    priority = 45
    auto = False

    def match(self, iface: HidIface) -> bool:
        return iface.is_vendor and iface.output_len >= 64

    def read(self, iface: HidIface) -> Optional[Reading]:
        return read_jl(iface)
