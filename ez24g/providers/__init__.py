# -*- coding: utf-8 -*-
"""数据源抽象层：HID（2.4G / 蓝牙 HID）和 BLE（蓝牙 GATT）。

上层（devices.py / app.py）只认两个动作：

    enumerate_refs()         现在系统上有哪些设备 → list[DeviceRef]
    read_ref(ref)            读一台设备的电量     → Reading | None

为什么要有这一层：
    protocols/ 的 read_best() 只接受 HidIface，蓝牙 BLE 走的是 WinRT，
    压根没有 HID 接口。加这一层之后，新的数据源只要实现
    enumerate_refs / read_ref 两件事就能接进来，protocols/ 一行不用改。

约定：
    - 任何 provider 的 enumerate/read 都不许抛异常，失败返回空列表 / None。
    - 蓝牙是可选能力：没装 WinRT 时 enumerate_refs 返回空，read 返回
      status="no_support" 的 Reading，界面显示提示而不是空白或崩溃。
"""
from __future__ import annotations

from typing import Optional

from .. import config
from ..model import Reading
from . import bt, bt_pnp, bt_system, hid
from .base import TRANSPORT_BT, TRANSPORT_HID, DeviceRef

__all__ = ["DeviceRef", "TRANSPORT_HID", "TRANSPORT_BT", "enumerate_refs",
           "read_ref", "bt_available", "bt_hint", "bt_install_hint",
           "bt_pnp_available", "reset_bt"]


def enumerate_refs(ifaces=None, include_bt: bool = True) -> list[DeviceRef]:
    """列出现有设备。蓝牙部分失败绝不影响 HID。"""
    refs: list[DeviceRef] = []
    try:
        refs.extend(hid.enumerate_refs(ifaces))
    except Exception:
        pass
    if include_bt:
        try:
            refs.extend(bt.enumerate_refs())
        except Exception:
            pass
    return refs


def _linked_hid(addr: str, hid_refs=None):
    """找挂在同一个蓝牙 MAC 下的 HID 接口（蓝牙键盘/鼠标常走这条路）。

    hid_refs 是本轮刷新开头已经数好的 HID 清单。以前每台蓝牙设备都在这里
    重新枚举一次全部 HID 接口（约 64ms/次），一轮下来白数好几遍。
    没传或是空清单时保持原来的行为，CLI / 单台读取不受影响。
    """
    if not addr:
        return None
    try:
        refs = hid_refs if hid_refs else hid.enumerate_refs()
    except Exception:
        return None
    for hr in refs:
        for d in hr.ifaces:
            try:
                mac = bt_pnp.mac_from_instance(d.parent or d.instance or "")
            except Exception:
                mac = ""
            if mac and mac == addr:
                return hr
    return None


def _read_linked_hid(ref: DeviceRef, hid_refs=None) -> Optional[Reading]:
    # 只有拿到本轮清单才传第二个参数；没拿到时保持原来单参数调用，
    # CLI 与自检里对 _linked_hid(addr) 的用法才不会被改坏。
    hr = (_linked_hid(ref.addr, hid_refs) if hid_refs
          else _linked_hid(ref.addr))
    if hr is None:
        return None
    try:
        r = hid.read_ref(hr)
    except Exception:
        return None
    if r is None or not r.ok:
        return None
    r.source = "蓝牙 HID 联动（标准电量字段）"
    r.transport = "bt"
    r.addr = ref.addr
    return r


def read_ref(ref: DeviceRef, include_manual: bool = False,
             prefer_id: Optional[str] = None,
             hid_refs=None) -> Optional[Reading]:
    """按来源分派读取。返回 None = 这台设备当前没有可用的读取方式。"""
    try:
        if ref.kind == TRANSPORT_BT:
            # 设备没连上时绝不套用手动固定值 / 指定位置：否则一张「未连接」
            # 的卡片会显示旧数字并顶到第一位，与「断开归位」冲突。
            if not ref.connected:
                r = bt.read_ref(ref)
                if r is not None and (config.bt_manual_of(ref.addr) or {}).get(
                        "kind") == "gatt":
                    r.detail = "已绑定指定位置，但设备未连接，暂不读取"
                return r
            # 优先顺序：手动指定位置 → 记住的来源 → 系统缓存/GATT
            #          → 蓝牙 HID 联动 → 手动固定值
            manual = bt.read_manual(ref)
            if manual is not None:
                return manual
            if config.bt_source_of(ref.addr) == "hid":
                linked = _read_linked_hid(ref, hid_refs)
                if linked is not None:
                    return linked
                config.note_bt_source_fail(ref.addr)
            r = bt.read_ref(ref)
            if r is not None and r.ok:
                if config.bt_source_of(ref.addr):
                    config.set_bt_source(ref.addr, "")
                return r
            linked = _read_linked_hid(ref, hid_refs)
            if linked is not None:
                config.set_bt_source(ref.addr, "hid")
                return linked
            fixed = bt.manual_fixed(ref)
            if fixed is not None:
                return fixed
            return r
        return hid.read_ref(ref, include_manual=include_manual,
                            prefer_id=prefer_id)
    except Exception:
        return None


def bt_available() -> bool:
    return bt.available()


def bt_hint() -> str:
    return bt.hint()


def bt_system_available() -> bool:
    """系统里有没有经典蓝牙 HFP 节点（不依赖 winrt）。"""
    return bt_system.available()


def bt_pnp_available() -> bool:
    """Win11 兼容识别方式是否可用（BTHENUM PnP 节点，不依赖 winrt）。"""
    return bt_pnp.available()


def bt_install_hint() -> str:
    return bt.INSTALL_HINT


def reset_bt() -> None:
    """清空蓝牙探测缓存（装完 winrt / 设备插拔后调用）。"""
    try:
        bt.reset()
    except Exception:
        pass


# 需求里约定的两个入口名（providers.enumerate() / providers.read(ref)），
# 与上面两个函数完全等价，只是短一点。
enumerate = enumerate_refs          # noqa: A001  故意遮住 builtins.enumerate
read = read_ref
