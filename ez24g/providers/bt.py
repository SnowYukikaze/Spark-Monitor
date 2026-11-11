# -*- coding: utf-8 -*-
"""蓝牙来源：经典蓝牙（HFP 系统属性）+ BLE（WinRT GATT）。

读取顺序（关键）：
    1. 经典音频耳机 → providers/bt_system.py 读 Hands-Free AG 节点的 PnP 属性，
       毫秒级、不需要 winrt、不连设备；
    2. 读不到再走 BLE GATT 0x180F / 0x2A19（Redmi Buds 这类真 BLE）；
    3. 都读不到 → unknown_proto，detail「系统未上报该设备电量」。

设计要点（都是踩过的坑）：
    - **可选依赖**：这里不 import winrt 到模块顶层。没装时可用性检查直接返回
      False，卡片显示「需要安装 winrt 才能读蓝牙电量」，启动不变慢、不报错。
      经典蓝牙不受影响：它走系统属性，没有 winrt 也能读。
    - **不主动连设备**：只有 Windows 认为「已连接」的才去读 GATT。设备关掉后
      连接状态变 False，卡片就走「未连接」，绝不会拿上次的缓存数字充数。
    - **有超时**：WinRT 的 IAsyncOperation 没有超时参数，设备半死不活时会一直
      等（实测 7 秒以上）。这里统一放到后台线程并限时，超时就放弃这一轮。
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Optional

from .. import config
from ..model import Reading
from . import bt_pnp, bt_system
from .base import DeviceRef

INSTALL_HINT = "蓝牙组件（winrt）未安装，无法读取"

BATTERY_SERVICE = uuid.UUID("0000180f-0000-1000-8000-00805f9b34fb")
BATTERY_LEVEL = uuid.UUID("00002a19-0000-1000-8000-00805f9b34fb")

ENUM_TIMEOUT = 4.0
READ_TIMEOUT = 4.0
CONN_TIMEOUT = 1.5          # 查连接状态最多等 1.5 秒
CONN_TTL = 3.0              # 一次刷新里 enumerate + read 共用同一份结果

_state: Optional[bool] = None
_state_detail = ""
_api: dict = {}


def _load() -> bool:
    """一次性探测 winrt 是否可用（结果缓存，失败也不重复 import）。"""
    global _state, _state_detail
    if _state is not None:
        return _state
    try:
        from winrt.windows.devices.bluetooth import (BluetoothCacheMode,
                                                     BluetoothConnectionStatus,
                                                     BluetoothDevice,
                                                     BluetoothLEDevice)
        from winrt.windows.devices.bluetooth.genericattributeprofile import (
            GattCommunicationStatus)
        from winrt.windows.devices.enumeration import DeviceInformation
    except Exception as e:                       # 没装 / 版本不兼容
        _state = False
        _state_detail = f"{INSTALL_HINT}（{type(e).__name__}）"
        return _state
    _api.update(BluetoothLEDevice=BluetoothLEDevice,
                BluetoothDevice=BluetoothDevice,
                BluetoothConnectionStatus=BluetoothConnectionStatus,
                BluetoothCacheMode=BluetoothCacheMode,
                GattCommunicationStatus=GattCommunicationStatus,
                DeviceInformation=DeviceInformation)
    _state = True
    _state_detail = "WinRT 可用"
    return _state


def available() -> bool:
    return _load()


def hint() -> str:
    _load()
    return _state_detail


def reset() -> None:
    """清空所有探测/连接缓存。

    装完 winrt 之后必须调一次：_load() 的结果是缓存的，不清就永远是
    「不可用」，用户会以为装了没用。设备刚插拔时也可以用来强制重枚举。
    """
    global _state, _state_detail
    _state = None
    _state_detail = ""
    _api.clear()
    _conn_cache.clear()
    try:
        bt_system.reset()
    except Exception:
        pass
    try:
        bt_pnp.reset()
    except Exception:
        pass


# addr → (查询时刻, 是否已连接)。None = 查不到（winrt 不可用 / 超时 / 异常）
_conn_cache: dict[str, tuple[float, bool | None]] = {}


def _query_conn(addr: str) -> Optional[bool]:
    """真正去问 WinRT 一次（单次约 180ms），失败返回 None。"""
    api = _api
    dev = api["BluetoothDevice"].from_bluetooth_address_async(
        int(addr.replace(":", ""), 16)).get()
    if dev is None:
        return None
    try:
        return (dev.connection_status
                == api["BluetoothConnectionStatus"].CONNECTED)
    finally:
        try:
            dev.close()
        except Exception:
            pass


def _cached_conn(addr: str) -> Optional[bool]:
    hit = _conn_cache.get(addr)
    if hit is not None and (time.monotonic() - hit[0]) < CONN_TTL:
        return hit[1]
    return None


def connection_status(addr: str) -> Optional[bool]:
    """这台蓝牙设备现在是否真的连着。

    为什么不看 AG 节点：Hands-Free AG 节点配对后就一直在，设备关机也不消失，
    Status/IsPresent/DevNodeStatus 全是「正常」。只有 BluetoothDevice 的连接
    状态会随开关机变化（实测 Misiom=1、A6=1、Redmi=0）。
    """
    if not addr or not _load():
        return None
    fresh = _cached_conn(addr)
    if fresh is not None:
        return fresh
    try:
        value = _call_with_deadline(lambda: _query_conn(addr), CONN_TIMEOUT)
    except Exception:
        value = None
    if value is _TIMEOUT:
        value = None
    _conn_cache[addr] = (time.monotonic(), value)
    return value


def _prime_conn(addrs: list[str]) -> None:
    """一次刷新里可能有几台经典设备：并发查，总耗时压在单次查询的量级。"""
    todo = [a for a in dict.fromkeys(addrs) if a and _cached_conn(a) is None]
    if not todo or not _load():
        return
    box: dict[str, Optional[bool]] = {}
    threads = []
    for addr in todo:
        def work(a=addr):
            try:
                box[a] = _query_conn(a)
            except Exception:
                box[a] = None
        t = threading.Thread(target=work, daemon=True)
        t.start()
        threads.append(t)
    deadline = time.monotonic() + CONN_TIMEOUT
    for t in threads:
        t.join(max(0.01, deadline - time.monotonic()))
    stamp = time.monotonic()
    for addr in todo:
        _conn_cache[addr] = (stamp, box.get(addr))


def _call_with_deadline(fn, timeout: float):
    """在后台线程里跑 WinRT 调用；超时返回 _TIMEOUT。"""
    box = {}

    def run():
        try:
            box["v"] = fn()
        except Exception as e:                  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return _TIMEOUT
    if "e" in box:
        raise box["e"]
    return box.get("v")


class _Timeout:
    def __repr__(self) -> str:
        return "<timeout>"


_TIMEOUT = _Timeout()


def _mac_from_id(device_id: str) -> str:
    """BluetoothLE#BluetoothLE10:68:38:a3:f8:86-de:1b:b4:46:93:66 → MAC。"""
    tail = (device_id or "").split("-")[-1]
    return config.normalize_addr(tail)


def enumerate_refs() -> list[DeviceRef]:
    """蓝牙识别按优先级联动回退，首选失败自动切下一种，已有成功结果不覆盖。

    枚举优先级：BLE（WinRT） → Hands-Free AG（SetupAPI） → 蓝牙 PnP（兼容层）。

    2026-11 修复：第三级以前只在「WinRT 不可用 或 一台都没连上」时才跑，
    于是只要耳机连着（任何一台 connected=True），蓝牙键盘就永远进不来 ——
    这就是「朋友机器能认出基本蓝牙设备、我这台只认耳机」的根因。
    现在第三级**无条件补位**，只填空缺、不覆盖前两级的任何结果。
    """
    out: list[DeviceRef] = _enumerate_ble()
    seen = {r.key for r in out}
    ag = bt_system.enumerate_ag()
    _prime_conn([item["addr"] for item in ag.values()])
    winrt_ok = _load()
    # 经典蓝牙耳机不在 BLE 枚举里，靠 Hands-Free AG 节点补名字；
    # 但「节点存在」不等于「已连接」（关机后节点还在），连接状态单独查。
    for key, item in ag.items():
        addr = item["addr"]
        conn = connection_status(addr)
        if conn is None and not winrt_ok:     # WinRT 不可用 → PnP 兼容兜底
            conn = bt_pnp.is_present(addr)
        existing = next((r for r in out if r.key == key), None)
        if existing is not None:
            # 双模耳机（如 Redmi Buds 5 Pro）：BLE 侧那条记录可能报「未连接」，
            # 但普通蓝牙侧其实连着，Windows 也缓存着电量。只在经典侧**明确**
            # 说连着时才修正；查不到 / 明确没连一律保持原样，设备真关机时
            # 连接查询会返回 False，断开归位这条红线不受影响。
            if conn is True:
                existing.connected = True
                # 身份仍按「BLE 优先」保持原样（不改 classic）：
                # 只纠正「是否连着」，不动这条记录从哪来、该走哪条读取链。
                if not existing.name and item["name"]:
                    existing.name = item["name"]
            continue
        out.append(DeviceRef(
            key=key, kind="bt", transport="bt", name=item["name"],
            addr=addr, classic=True, connected=conn is True))
        seen.add(key)

    # 第三级（无条件）：WinRT 枚举不到、又没有 HFP 节点的设备都在这里补上，
    # 典型就是 BLE 蓝牙键盘 / BLE 鼠标（节点 BTHLE\DEV_<MAC>）。
    # 只补没识别到的 MAC，不改动任何已有条目。
    for key, item in bt_pnp.enumerate_paired().items():
        if key in seen:
            continue
        seen.add(key)
        addr = item["addr"]
        conn = connection_status(addr) if winrt_ok else None
        # PnP 节点「存在」不等于「已连接」：设备关机后节点还在，IsPresent
        # 依然是 True。所以只有在 WinRT 完全不可用时才拿它兜底；
        # WinRT 可用但这次查不到（超时/异常）就当未知，卡片走「未连接」，
        # 下一轮查询恢复后自动回来。以前这里无条件兜底，耳机一关就顶着
        # 旧电量继续显示，还排在第一位。
        if conn is None and not winrt_ok:
            conn = bool(item.get("present"))
        out.append(DeviceRef(
            key=key, kind="bt", transport="bt", name=item["name"],
            addr=addr, classic=False, connected=bool(conn),
            detail="蓝牙 PnP（兼容模式）"))
    return out


def _enumerate_ble() -> list[DeviceRef]:
    if not _load():
        return []
    api = _api

    def work():
        sel = api["BluetoothLEDevice"].get_device_selector()
        return list(api["DeviceInformation"].find_all_async(sel, []).get())

    try:
        infos = _call_with_deadline(work, ENUM_TIMEOUT)
    except Exception:
        return []
    if infos is _TIMEOUT or not infos:
        return []

    out: list[DeviceRef] = []
    for info in infos:
        try:
            addr = _mac_from_id(info.id)
            if len(addr) != 17:
                continue
            dev = _call_with_deadline(
                lambda i=info: api["BluetoothLEDevice"].from_id_async(i.id).get(),
                ENUM_TIMEOUT)
            if dev is _TIMEOUT or dev is None:
                continue
            connected = (dev.connection_status
                         == api["BluetoothConnectionStatus"].CONNECTED)
            try:
                dev.close()
            except Exception:
                pass
            out.append(DeviceRef(
                key=config.bt_key(addr), kind="bt", transport="bt",
                name=info.name or "", addr=addr, connected=bool(connected)))
        except Exception:
            continue
    return out


def _read_level(addr: str, service=None, characteristic=None):
    """真正读 GATT。返回 (状态, 值)：
    ("ok", 87) / ("no_battery", None) / ("disconnected", None) / ("error", msg)
    """
    api = _api
    bt_addr = int(addr.replace(":", ""), 16)
    dev = api["BluetoothLEDevice"].from_bluetooth_address_async(bt_addr).get()
    if dev is None:
        return "error", "打不开蓝牙设备"
    try:
        if dev.connection_status != api["BluetoothConnectionStatus"].CONNECTED:
            return "disconnected", None
        svc_res = dev.get_gatt_services_for_uuid_async(
            service or BATTERY_SERVICE).get()
        if svc_res.status != api["GattCommunicationStatus"].SUCCESS:
            return "error", f"取服务失败（{svc_res.status}）"
        services = list(svc_res.services)
        if not services:
            return "no_battery", None
        for svc in services:
            chr_res = svc.get_characteristics_for_uuid_async(
                characteristic or BATTERY_LEVEL).get()
            if chr_res.status != api["GattCommunicationStatus"].SUCCESS:
                continue
            for ch in chr_res.characteristics:
                rr = ch.read_value_async(
                    api["BluetoothCacheMode"].UNCACHED).get()
                if rr.status != api["GattCommunicationStatus"].SUCCESS:
                    continue
                raw = bytes(rr.value) if rr.value is not None else b""
                if raw:
                    return "ok", max(0, min(100, raw[0]))
        return "no_battery", None
    finally:
        try:
            dev.close()
        except Exception:
            pass


def _as_uuid(text, default):
    """16 位短 UUID 补成蓝牙基础 UUID；完整 UUID 直接用；坏值返回 None。"""
    t = (text or "").strip()
    if not t:
        return default
    try:
        if len(t) == 4:
            return uuid.UUID(f"0000{t}-0000-1000-8000-00805f9b34fb")
        return uuid.UUID(t)
    except Exception:
        return None


def read_manual(ref: DeviceRef) -> Optional[Reading]:
    """手动「指定读取位置」：优先按用户给的蓝牙服务/特征读一次。"""
    item = config.bt_manual_of(ref.addr)
    if item.get("kind") != "gatt" or not ref.connected or not _load():
        return None
    svc = _as_uuid(item.get("service"), BATTERY_SERVICE)
    ch = _as_uuid(item.get("characteristic"), BATTERY_LEVEL)
    if svc is None or ch is None:
        return None
    try:
        res = _call_with_deadline(
            lambda: _read_level(ref.addr, svc, ch), READ_TIMEOUT)
    except Exception:
        return None
    if res is _TIMEOUT or not isinstance(res, tuple) or res[0] != "ok":
        return None
    return Reading(ok=True, level=int(res[1]), status="discharging",
                   source="手动绑定（指定位置）",
                   detail=f"指定位置读数 = {res[1]}", transport="bt",
                   addr=ref.addr, name=ref.name)


def manual_fixed(ref: DeviceRef) -> Optional[Reading]:
    """自动链路全失败后的兜底：用户手填的固定电量，明确标注不是实时读数。"""
    item = config.bt_manual_of(ref.addr)
    if item.get("kind") != "fixed":
        return None
    try:
        lv = int(item.get("level", -1))
    except Exception:
        return None
    if not 0 <= lv <= 100:
        return None
    return Reading(ok=True, level=lv, status="discharging",
                   source="手动绑定", detail="手动指定电量（非实时读数）",
                   transport="bt", addr=ref.addr, name=ref.name)


def read_ref(ref: DeviceRef) -> Optional[Reading]:
    addr = ref.addr
    if not addr:
        return None
    if not ref.connected:
        # 设备关机 / 断开：先判连接状态，绝不去读那个「上次上报」的缓存属性
        return Reading(ok=False, present=False, status="unplugged",
                       detail="未连接（已关机或超出范围）", transport="bt",
                       addr=addr, name=ref.name)

    # 1) 经典蓝牙（HFP）：Windows 缓存在 Hands-Free AG 节点上的电量，毫秒级，不需要 winrt
    try:
        sys_level = bt_system.read_level(addr)
    except Exception:
        sys_level = None
    if sys_level is not None:
        return Reading(ok=True, level=int(sys_level), status="discharging",
                       source="蓝牙 HFP（系统属性）",
                       detail=f"系统属性电量 = {sys_level}", transport="bt",
                       addr=addr, name=ref.name)

    # 2) Win11 兼容：首选 HFP 属性没给值时，回退到 BTHENUM PnP 节点电量。
    #    只在首选失败后触发，HFP 有读数时结果与之前完全一致。
    try:
        pnp_level = bt_pnp.read_level(addr)
    except Exception:
        pnp_level = None
    if pnp_level is not None:
        return Reading(ok=True, level=int(pnp_level), status="discharging",
                       source="蓝牙 PnP（兼容模式）",
                       detail=f"PnP 设备属性电量 = {pnp_level}",
                       transport="bt", addr=addr, name=ref.name)

    # 3) 经典设备到此为止：没有 BLE 电量服务可读，不要拿 GATT 去打它
    if ref.classic:
        return Reading(ok=False, present=True, status="unknown_proto",
                       detail="系统未上报该设备电量（缓存/PnP 均为空）", transport="bt",
                       addr=addr, name=ref.name, source="蓝牙 HFP（系统属性）")

    # 4) BLE：GATT 0x180F / 0x2A19
    if not _load():
        return Reading(ok=False, present=True, status="no_support",
                       detail=INSTALL_HINT, transport="bt", addr=addr,
                       name=ref.name, source="蓝牙 GATT")

    try:
        res = _call_with_deadline(lambda: _read_level(addr), READ_TIMEOUT)
    except Exception as e:                       # noqa: BLE001
        return Reading(ok=False, present=True, status="asleep",
                       detail=f"蓝牙读取失败：{e}", transport="bt",
                       addr=addr, name=ref.name, source="蓝牙 GATT")

    if res is _TIMEOUT:
        return Reading(ok=False, present=True, status="asleep",
                       detail="蓝牙读取超时，稍后重试", transport="bt",
                       addr=addr, name=ref.name, source="蓝牙 GATT")

    state, value = res
    if state == "ok":
        return Reading(ok=True, level=int(value), status="discharging",
                       source="蓝牙 GATT (0x180F/0x2A19)",
                       detail=f"GATT 0x2A19 = {value}", transport="bt",
                       addr=addr, name=ref.name)
    if state == "disconnected":
        return Reading(ok=False, present=False, status="unplugged",
                       detail="未连接（已关机或超出范围）", transport="bt",
                       addr=addr, name=ref.name)
    if state == "no_battery":
        return Reading(ok=False, present=True, status="unknown_proto",
                       detail="该蓝牙设备没有电量服务", transport="bt",
                       addr=addr, name=ref.name, source="蓝牙 GATT")
    return Reading(ok=False, present=True, status="asleep",
                   detail=f"蓝牙读取失败：{value}", transport="bt",
                   addr=addr, name=ref.name, source="蓝牙 GATT")
