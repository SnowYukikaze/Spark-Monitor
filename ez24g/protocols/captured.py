# -*- coding: utf-8 -*-
"""抓包学到的协议。

配置里这样一条就是它的全部内容：
    "373B:1278": {"kind": "captured", "requests": ["08 7D 72 ..."],
                  "offset": 7, "invert": false}

读取方式：把 requests 里每一帧原样写给设备，然后在回复的第 offset 字节
取电量；全都不行时退化成被动监听输入报告。
"""
from __future__ import annotations

import time
from typing import Optional

from .. import config
from ..hidapi import HidDevice, HidIface
from ..model import Reading
from .base import DeviceProtocol, register


def _parse_requests(proto: dict) -> list[bytes]:
    raw = proto.get("requests")
    if not isinstance(raw, list):
        raw = [proto.get("request", "")]
    reqs = []
    for s in raw:
        try:
            b = bytes.fromhex(str(s).replace(" ", ""))
        except Exception:
            continue
        if len(b) >= 2:
            reqs.append(b)
    return reqs


def read_with_protocol(iface: HidIface, proto: dict,
                       timeout: float = 1.2) -> Optional[Reading]:
    off = int(proto.get("offset", 2))
    if off < 1:
        return None
    reqs = _parse_requests(proto)
    if not reqs:
        return None
    if iface.output_len < min(len(r) for r in reqs) or iface.input_len <= off:
        return None
    for req in reqs:
        if iface.output_len < len(req):
            continue
        dev = None
        try:
            dev = HidDevice(iface.path, write=True)
            buf = bytes(req) + bytes(max(0, iface.output_len - len(req)))
            dev.drain()
            dev.write_output(buf)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                rep = dev.read_input(max(64, iface.input_len), 250)
                if not rep or len(rep) <= off:
                    continue
                if 0 <= rep[off] <= 100:
                    val = rep[off]
                    if proto.get("invert"):
                        val = 100 - val
                    return Reading(ok=True, level=val, source="抓包协议",
                                   detail=f"回复 {rep[:8].hex(' ')}", raw=rep)
        except Exception:
            pass
        finally:
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass
    return None


def listen_with_protocol(iface: HidIface, proto: dict,
                         seconds: float = 1.2) -> Optional[Reading]:
    """被动监听：不发请求，只看设备自己上报的字节。"""
    off = int(proto.get("offset", 2))
    if off < 1 or iface.input_len <= off:
        return None
    reqs = _parse_requests(proto)
    req_ids = {b[0] for b in reqs}
    dev = None
    try:
        dev = HidDevice(iface.path, write=False)
    except Exception:
        return None
    try:
        deadline = time.monotonic() + seconds
        seen = {}
        first = None
        while time.monotonic() < deadline:
            rep = dev.read_input(max(64, iface.input_len), 250)
            if not rep or len(rep) <= off:
                continue
            val = rep[off]
            if not (0 <= val <= 100):
                continue
            if first is None:
                first = (val, rep)
            if req_ids and rep[0] in req_ids:
                return Reading(ok=True, level=val, source="被动监听输入报告",
                               detail=f"报告 {rep[:8].hex(' ')}", raw=rep)
            seen[val] = seen.get(val, 0) + 1
            if seen[val] >= 2:
                return Reading(ok=True, level=val, source="被动监听输入报告",
                               detail=f"连续报告 {rep[:8].hex(' ')}", raw=rep)
        if first is not None:
            return Reading(ok=True, level=first[0], source="被动监听输入报告",
                           detail=f"单次报告 {first[1][:8].hex(' ')}",
                           raw=first[1])
    except Exception:
        pass
    finally:
        if dev is not None:
            try:
                dev.close()
            except Exception:
                pass
    return None


def protocol_for(vid: int, pid: int) -> Optional[dict]:
    return config.user_protocols().get(config.proto_key(vid, pid))


@register
class CapturedProtocol(DeviceProtocol):
    id = "captured"
    name = "抓包协议"
    priority = 20

    def _proto(self, iface: HidIface):
        p = protocol_for(iface.vid, iface.pid)
        if p is None or p.get("kind") == "standard":
            return None
        return p

    def match(self, iface: HidIface) -> bool:
        return self._proto(iface) is not None

    def read(self, iface: HidIface) -> Optional[Reading]:
        proto = self._proto(iface)
        if proto is None:
            return None
        return read_with_protocol(iface, proto)
