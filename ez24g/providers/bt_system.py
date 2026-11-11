# -*- coding: utf-8 -*-
"""经典蓝牙（HFP）电量：读 Windows 缓存在 Hands-Free AG 节点上的未公开属性。

为什么是这个数据源：
    经典蓝牙耳机（BTHENUM：A2DP 110B / AVRCP 110E / HFP 111E）没有 BLE 的
    0x180F 电量服务，Windows 也不把电量放在 DEV_* 主节点或 HFP 音频节点上。
    它把「上次 HFP 上报的电量」缓存在 **Hands-Free AG** 这个 System 类设备的
    PnP 属性 {104EA319-6EE2-4701-BD47-8DDBF425BBE5}, 2（DEVPROP_TYPE_BYTE）里。

    实测（本机）：Misiom-Aries Pro = 90、A6 = 90、Redmi Buds 5 Pro 属性为空
    （后者是真 BLE，走 providers/bt.py 的 GATT 0x180F/0x2A19）。

用法：
    enumerate_ag()  → {"bt:AA:BB:CC:DD:EE:FF": {"name", "addr", "level"}}
    read_level(addr) → int | None

约束：全程不抛异常；属性为空就返回 None（= 设备没上报，绝不拿上次值冒充）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import re
import time
from typing import Optional

from .. import config
from ..win32 import (DEVPKEY_BT_Battery, DEVPKEY_Device_FriendlyName,
                     DEVPROP_TYPE_BYTE, DIGCF_PRESENT, GUID_DEVCLASS_SYSTEM,
                     INVALID_HANDLE_VALUE, SP_DEVINFO_DATA, setupapi)

# Hands-Free AG 服务的 GUID 片段（HFP = 0x111E）
AG_SERVICE = "0000111e"
# 节点名后缀，去掉后就是设备名
AG_SUFFIX = re.compile(r"\s*hands-free\s*ag\s*$", re.I)
# 实例 ID 尾部：...&0&<12 位十六进制>_C00000000
MAC_RE = re.compile(r"&0&([0-9A-Fa-f]{12})_", re.I)

# SP_DEVINFO_DATA 在 x64 上是 32 字节（DWORD + GUID + DWORD + 指针）
_SIZEOF_OK = ctypes.sizeof(SP_DEVINFO_DATA) == 32

# 一次刷新最多枚举一次：短缓存供 read_level 与 enumerate_ag 共用
_CACHE_TTL = 2.0
_cache: dict = {"ts": 0.0, "data": {}}


def _prop(hdev, info: SP_DEVINFO_DATA, key) -> tuple[bool, int, bytes]:
    """读一个设备属性，返回 (是否存在, 类型, 原始字节)。

    取字节必须用 ``ctypes.string_at``：缓冲区是 ``c_byte`` 数组，元素带符号，
    直接 ``bytes(buf[:n])`` 在属性值含 >= 0x80 的字节时（例如设备名是中文）
    会抛 ValueError，调用方只能 continue —— 表现就是「设备明明在，却识别不到」。
    """
    buf = (ctypes.c_byte * 512)()
    ptype = wt.ULONG(0)
    need = wt.DWORD(0)
    ok = setupapi.SetupDiGetDevicePropertyW(
        hdev, ctypes.byref(info), ctypes.byref(key), ctypes.byref(ptype),
        buf, 512, ctypes.byref(need), 0)
    if not ok:
        return False, 0, b""
    size = max(0, min(512, need.value))
    try:
        raw = ctypes.string_at(buf, size)
    except Exception:
        return False, 0, b""
    return True, ptype.value, raw


def _prop_str(hdev, info: SP_DEVINFO_DATA, key) -> str:
    ok, _ptype, raw = _prop(hdev, info, key)
    if not ok or not raw:
        return ""
    try:
        return ctypes.wstring_at(raw).rstrip("\x00")
    except Exception:
        return ""


def _prop_byte(hdev, info: SP_DEVINFO_DATA, key) -> Optional[int]:
    ok, ptype, raw = _prop(hdev, info, key)
    if not ok or ptype != DEVPROP_TYPE_BYTE or not raw:
        return None
    value = raw[0]
    return value if 0 <= value <= 100 else None


def mac_from_instance(instance: str) -> str:
    """BTHENUM\\...&0&50C0F005A67D_C00000000 → 50:C0:F0:05:A6:7D"""
    m = MAC_RE.search(instance or "")
    return config.normalize_addr(m.group(1)) if m else ""


def strip_ag_suffix(name: str) -> str:
    """「Misiom-Aries Pro Hands-Free AG」→「Misiom-Aries Pro」"""
    return AG_SUFFIX.sub("", (name or "").strip()).strip()


def is_ag_instance(instance: str) -> bool:
    inst = (instance or "").lower()
    return "bthenum\\{" in inst and AG_SERVICE in inst


def enumerate_ag(fresh: bool = False) -> dict[str, dict]:
    """枚举 Hands-Free AG 节点。失败/不存在一律返回 {}。"""
    now = time.monotonic()
    if not fresh and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["data"]
    out: dict[str, dict] = {}
    if not _SIZEOF_OK:
        _cache.update(ts=now, data=out)
        return out

    hdev = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(GUID_DEVCLASS_SYSTEM), None, None, DIGCF_PRESENT)
    if not hdev or hdev == INVALID_HANDLE_VALUE:
        _cache.update(ts=now, data=out)
        return out
    try:
        idx = 0
        while True:
            info = SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(info)
            if not setupapi.SetupDiEnumDeviceInfo(hdev, idx, ctypes.byref(info)):
                break
            idx += 1
            try:
                inst = ctypes.create_unicode_buffer(512)
                need = wt.DWORD(0)
                if not setupapi.SetupDiGetDeviceInstanceIdW(
                        hdev, ctypes.byref(info), inst, 512,
                        ctypes.byref(need)):
                    continue
                if not is_ag_instance(inst.value):
                    continue
                addr = mac_from_instance(inst.value)
                if not addr:
                    continue
                name = strip_ag_suffix(
                    _prop_str(hdev, info, DEVPKEY_Device_FriendlyName))
                level = _prop_byte(hdev, info, DEVPKEY_BT_Battery)
                out[config.bt_key(addr)] = {
                    "name": name or f"蓝牙设备 {addr}",
                    "addr": addr,
                    "level": level,
                }
            except Exception:
                continue
    except Exception:
        pass
    finally:
        try:
            setupapi.SetupDiDestroyDeviceInfoList(hdev)
        except Exception:
            pass
    _cache.update(ts=time.monotonic(), data=out)
    return out


def read_level(addr: str) -> Optional[int]:
    """只读电量。属性为空或不在 0~100 → None。"""
    if not addr:
        return None
    item = enumerate_ag().get(config.bt_key(addr))
    return item["level"] if item else None


def name_for(addr: str) -> str:
    item = enumerate_ag().get(config.bt_key(addr)) if addr else None
    return item["name"] if item else ""


def available() -> bool:
    """系统里有没有任何 AG 节点（用于界面提示，不作为读取前提）。"""
    return bool(enumerate_ag())


def reset() -> None:
    """丢掉枚举缓存（设备刚插拔时用）。"""
    _cache.update(ts=0.0, data={})
