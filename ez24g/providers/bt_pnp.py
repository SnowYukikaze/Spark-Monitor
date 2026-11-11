# -*- coding: utf-8 -*-
"""Win32 兼容蓝牙识别：用 SetupAPI 枚举蓝牙设备节点（不依赖 WinRT）。

背景（2026-11 定位到的根因）
--------------------------
旧实现只认一种实例 ID：

    BTHENUM\\{0000111E-...}_HCIBYPASS_...\\7&A3FF356&0&84AC602D6A2D_C00000000

于是有两类设备永远进不来：

1. **BLE 设备（蓝牙键盘 / BLE 鼠标）** 的节点是 ``BTHLE\\DEV_<MAC>`` 或
   ``BTHLEDEVICE\\{0000180F-...}_DEV_VID&..._REV&..._<MAC>``，
   既不含 ``bthenum\\``，也没有 ``&0&<12 位十六进制>_`` 这段，
   所以 MAC 解析必定失败 → 整台设备被丢弃。
   实测这台机器（七彩虹 P16 + LANGTU BT5.0 键盘）：
   ``BTHLE\\DEV_FCCC15723F33`` 上就有 DEVPKEY_BT_Battery = 28%。

2. **非音频的经典蓝牙设备（HID 键盘/鼠标）** 只有在 Hands-Free AG 节点
   上才有名字，而 AG 节点只有耳机才有；HID 服务节点（00001124）名字为空，
   MAC 解析同样依赖上面的旧格式。

现在改成：`mac_from_instance()` 依次尝试三种实例 ID 形态，`enumerate_paired()`
同时收 BTHENUM / BTHLE / BTHLEDEVICE 节点，按 MAC 去重，优先保留
「有名字 + 有电量」的那条。识别范围变大，但读取顺序与回退契约不变：

    首选（WinRT 枚举 / Hands-Free AG）能给出结果时，本模块的读数不覆盖它。

全程不抛异常；失败返回空 dict / None。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import re
import time
from typing import Optional

from .. import config
from ..win32 import (DEVPKEY_BT_Battery, DEVPKEY_Device_FriendlyName,
                     DIGCF_PRESENT, INVALID_HANDLE_VALUE, SP_DEVINFO_DATA,
                     setupapi)
from . import bt_system

# 枚举所有类别（不只 System 类），才能覆盖非音频的 BLE / HID 蓝牙设备
DIGCF_ALL_CLASSES = 0x04
_CACHE_TTL = 2.0
_cache: dict = {"ts": 0.0, "data": {}}

# 复用 bt_system 里已实测过的属性读取与名字清洗，避免两套实现漂移
_prop_str = bt_system._prop_str
_prop_byte = bt_system._prop_byte
strip_ag_suffix = bt_system.strip_ag_suffix

# 三种实例 ID 形态（顺序 = 优先级，先匹配到的就是设备 MAC）
_MAC_PATTERNS = (
    # BTHENUM\...&0&84AC602D6A2D_C00000000
    re.compile(r"&0&([0-9A-Fa-f]{12})_"),
    # BTHLE\DEV_FCCC15723F33\7&37886678&0&FCCC15723F33
    re.compile(r"\\DEV_([0-9A-Fa-f]{12})(?:\\|$)", re.I),
    # BTHLEDEVICE\{0000180F-...}_DEV_VID&021A2C_PID&7FFF_REV&0015_FCCC15723F33\8&...
    re.compile(r"_REV&[0-9A-Fa-f]{4}_([0-9A-Fa-f]{12})", re.I),
)

# 认得出的蓝牙设备节点前缀
_BT_NODE_TAGS = ("bthenum\\", "bthle\\", "bthledevice\\")

# 这些不是真实设备 MAC：本机虚拟串口节点（&0&000000000000）与广播伪地址
_BAD_MACS = {"000000000000", "FFFFFFFFFFFF"}


def mac_from_instance(instance: str) -> str:
    """从任意一种蓝牙设备实例 ID 解析 MAC；解析不出返回空串。"""
    text = (instance or "").strip()
    if not text:
        return ""
    low = text.lower()
    if "localmfg" in low:              # 本机虚拟节点，没有对端设备
        return ""
    for pat in _MAC_PATTERNS:
        m = pat.search(text)
        if m:
            raw = m.group(1).upper()
            if raw in _BAD_MACS:
                continue
            return config.normalize_addr(raw)
    return ""


def is_bt_node(instance: str) -> bool:
    low = (instance or "").lower()
    return any(tag in low for tag in _BT_NODE_TAGS)


def _better(new: dict, old: dict) -> bool:
    """同一 MAC 的多条节点里，哪条更值得留下。

    BTHLEDEVICE 的服务节点没有名字、也没有电量，不能拿它当设备；
    BTHLE\\DEV_x 有名字、有电量，是首选。所以排序是：
        电量有 + 名字有 > 电量有 > 名字有 > 其它
    """
    def score(d: dict) -> int:
        name = (d.get("name") or "")
        named = bool(name) and not name.startswith("蓝牙设备 ")
        return (2 if d.get("level") is not None else 0) + (1 if named else 0)
    return score(new) > score(old)


def enumerate_paired(fresh: bool = False) -> dict[str, dict]:
    """列出当前存在的蓝牙设备节点，按 MAC 去重。

    返回 {bt_key: {"name", "addr", "level", "present"}}；level 可能是 None。
    """
    now = time.monotonic()
    if not fresh and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["data"]
    out: dict[str, dict] = {}
    if ctypes.sizeof(SP_DEVINFO_DATA) != 32:
        _cache.update(ts=now, data=out)
        return out
    hdev = setupapi.SetupDiGetClassDevsW(
        None, None, None, DIGCF_PRESENT | DIGCF_ALL_CLASSES)
    if not hdev or hdev == INVALID_HANDLE_VALUE:
        _cache.update(ts=now, data=out)
        return out
    try:
        idx = 0
        while idx < 4096:                 # 上限，防止驱动异常时死循环
            info = SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(info)
            if not setupapi.SetupDiEnumDeviceInfo(hdev, idx,
                                                  ctypes.byref(info)):
                break
            idx += 1
            try:
                inst = ctypes.create_unicode_buffer(512)
                need = wt.DWORD(0)
                if not setupapi.SetupDiGetDeviceInstanceIdW(
                        hdev, ctypes.byref(info), inst, 512,
                        ctypes.byref(need)):
                    continue
                instance = inst.value or ""
                if not is_bt_node(instance):
                    continue
                addr = mac_from_instance(instance)
                if not addr:
                    continue
                key = config.bt_key(addr)
                name = strip_ag_suffix(
                    _prop_str(hdev, info, DEVPKEY_Device_FriendlyName))
                level = _prop_byte(hdev, info, DEVPKEY_BT_Battery)
                item = {"name": name, "addr": addr, "level": level,
                        "present": True}
                prev = out.get(key)
                if prev is None or _better(item, prev):
                    if not name:
                        # 服务节点的名字是空的，别用它覆盖已知名字
                        item["name"] = prev.get("name", "") if prev else ""
                    out[key] = item
            except Exception:
                continue
    except Exception:
        pass
    finally:
        try:
            setupapi.SetupDiDestroyDeviceInfoList(hdev)
        except Exception:
            pass
    for key, item in out.items():
        if not item.get("name"):
            item["name"] = f"蓝牙设备 {item['addr']}"
    _cache.update(ts=time.monotonic(), data=out)
    return out


def read_level(addr: str) -> Optional[int]:
    """第三级电量来源：蓝牙设备节点上的 DEVPKEY_BT_Battery。

    Win11 把 BLE 设备（蓝牙键盘/鼠标）的电量缓存在 ``BTHLE\\DEV_<MAC>``
    节点上；经典 HFP 耳机则在 Hands-Free AG 节点上（见 bt_system）。
    """
    if not addr:
        return None
    item = enumerate_paired().get(config.bt_key(addr))
    return item["level"] if item else None


def is_present(addr: str) -> bool:
    """WinRT 不可用时的连接状态兜底：PnP 里当前存在这台设备。"""
    if not addr:
        return False
    return config.bt_key(addr) in enumerate_paired()


def available() -> bool:
    return bool(enumerate_paired())


def reset() -> None:
    """丢掉缓存（装完 winrt / 设备刚插拔时用）。"""
    _cache.update(ts=0.0, data={})
