# -*- coding: utf-8 -*-
"""罗技 HID++ 2.0（Unifying / Lightspeed 接收器）。

短报文 0x10（7 字节）/ 长报文 0x11（20 字节）。
先查特性索引，再按特性读：
    0x1004 Unified Battery  fn0=能力(档位掩码/标志) fn1=状态(电量/档位/状态码)
    0x1000 Battery Status   fn0=电量+状态（旧特性，rep[4]=电量 rep[6]=状态）
    0x1001 Battery Voltage  毫伏，不给百分比

0x1004 的 fn0 曾长期被误当成电量：fn0 的第 1 字节其实是「支持哪些档位」
的掩码（本机 G517 = 0x0f = 15），所以键盘常年显示 15%，而厂商软件显示 87%。
按 Logitech HID++ 2.0 规范，必须读 fn1 的 params[0] 才是真实百分比，且要
先用 fn0 的 flags bit1 确认设备确实有百分比电量计，否则只报档位、绝不编造
数字。

协议细节只采用公开资料里的事实，未复制任何第三方代码或文档原文；
来源与署名见项目根目录 LICENSE.md「第三方资料来源与致谢」。

资料范围说明：HID++ 1.0（老 Unifying 接收器）用固定编号寄存器读电量
（0x07 只给 4 档、0x0D 给百分比），但启用上报需要往设备写寄存器配置。
本软件坚持「只读、绝不显示假电量」，所以这里只记录资料、不实现，
避免为老设备引入写设备配置的副作用。旧特性 0x1000 已支持。

双模设备（2.4G / 蓝牙）切换时，接收器会有一小段时间不应答。这一层保证：
① 没问到配对名时绝不把「空」记死；② 读不到时仍带着已知身份，卡片不会
闪回「USB Receiver」；③ 一轮里多次刷新不重复轰报文。
"""
from __future__ import annotations

import copy
import time
from typing import Optional

from ..hidapi import (HidDevice, HidIface, HidError, WRITE_BUSY_HINT)
from ..model import Reading
from .base import DeviceProtocol, register


def _hidpp_call(dev: HidDevice, rid: int, devidx: int, featidx: int,
                func: int, params: bytes, length: int,
                timeout: float = 0.15) -> Optional[bytes]:
    body = bytes([rid, devidx, featidx, ((func & 0x0F) << 4) | 0x01]) + params
    body = (body + bytes(length))[:length]
    dev.drain()
    dev.write_output(body)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        rep = dev.read_input(length, 100)
        if not rep or len(rep) < 5:
            continue
        if rep[0] != rid or rep[1] != devidx or rep[2] != featidx:
            continue
        if ((rep[3] >> 4) & 0x0F) != func:
            continue
        return rep
    return None


# 0x1004 fn1 的充电状态码。对照罗技官方公开的 HID++ 2.0 规范、
# OpenLogi HID++ Reference（0x1004 unifiedBattery）与 Linux
# hid-logitech-hidpp：0 放电 / 1 充电 / 2 即将充满 / 3 已充满 / 4 慢充 /
# 5 电池类型无效 / 6 温度异常 / 7 充电错误。
# 内部状态只用 discharging / charging / full / unknown 四个值，异常码写进
# detail，既说得清又不碰界面已有的状态配色。
_UNIFIED_STATUS = {
    0: ("放电中", "discharging"),
    1: ("充电中", "charging"),
    2: ("即将充满", "charging"),
    3: ("已充满", "full"),
    4: ("慢速充电", "charging"),
    5: ("电池类型异常", "unknown"),
    6: ("电池温度异常", "unknown"),
    7: ("充电异常", "unknown"),
}


def _status_name_unified(code: int) -> str:
    """0x1004 fn1 的充电状态码 → 内部状态名（不认识的一律 unknown）。"""
    return _UNIFIED_STATUS.get(code, ("", "unknown"))[1]


def _status_note_unified(code: int) -> str:
    """给卡片悬停看的中文说明；不认识的值明确说「未知状态码」。"""
    return _UNIFIED_STATUS.get(code, ("", ""))[0] or f"未知状态码 {code}"


def _capability_note(cap: bytes) -> str:
    """0x1004 fn0 能力字节 → 人话说明（只描述，不改变读数判定）。

    params[0] bit0..3 = 声明支持的粗档位；params[1] bit0 = 可充电，
    bit1 = 有百分比电量计。这里只把原因写清楚，不做硬校验，免得个别
    固件声明不全时把本来能读的设备判成读不到。
    """
    levels = cap[4] if len(cap) > 4 else 0
    flags = cap[5] if len(cap) > 5 else 0
    names = [n for bit, n in ((1, "极低"), (2, "低"), (4, "正常"), (8, "充足"))
             if levels & bit]
    bits = []
    if flags & 0x01:
        bits.append("可充电")
    if flags & 0x02:
        bits.append("有百分比电量计")
    return (f"支持档位 {'/'.join(names) or '未声明'}"
            + ("；" + "、".join(bits) if bits else ""))


def _bucket_name(code: int) -> str:
    """0x1004 的粗档位（设备没有百分比电量计时才用）。"""
    names = {1: "电量极低", 2: "电量低", 4: "电量正常", 8: "电量充足"}
    return names.get(code, "未知档位")


def _status_name(code: int) -> str:
    """HID++ 电池状态码 → 内部状态名。

    只认语义明确的值：0 放电 / 1 充电 / 2 充满。3/4（慢充/快充）与 5（故障）
    以及其它未定义值一律归 unknown —— 以前把 1/2/4 全当 charging，接收器上
    换槽位时会 1→2→4 来回跳，卡片就在「充电中 / 已连接」之间闪。
    """
    if code == 1:
        return "charging"
    if code == 2:
        return "full"
    if code == 0:
        return "discharging"
    return "unknown"


# 一轮探测的时间预算：接收器正常 <50ms 一次往返，慢到 1.2 秒还没结果，
# 说明设备不在这个接收器上或处于休眠，这一轮先放弃（下一轮再试）。
PROBE_BUDGET = 1.2
CALL_TIMEOUT = 0.15
# 上次命中的 (devidx, feature_id)。接收器通常只有一个槽位有设备，
# 记住它能省掉整轮 7 槽 × 3 特性的试探。按 VID:PID 分开记。
_LAST: dict[tuple[int, int], tuple[int, int]] = {}
# 预算要按「设备」算，不能按「接口」算：一台接收器有长/短两个可写接口，
# 按接口各给 1.2 秒，读不到时就会白白多花一倍时间。
_ROUND: dict[tuple[int, int], tuple[int, float]] = {}
# 连续读不到的设备退避 30 秒再试：否则每轮刷新都要为它白等一个预算。
_FAILS: dict[tuple[int, int], int] = {}
_SKIP_UNTIL: dict[tuple[int, int], float] = {}
_SLOTS = (0xFF, 1, 2, 3, 4, 5, 6)
_FEATURES = ((0x1004, "unified"), (0x1000, "status"), (0x1001, "voltage"))
# 接收器上配对的真实设备名（0x0005 Device Name）。用于把「USB Receiver」
# 与同一台键盘的蓝牙卡认成一张，例如 G517 / G517 LS TKL。
_PAIR_NAMES: dict[tuple[int, int, int], str] = {}
# 按设备（VID:PID）记住「曾经问到的名字」。本轮读不到、或处于退避时也要
# 带着它，否则切换模式那一瞬间卡片会退回「USB Receiver」，且要重启才恢复。
_KNOWN_PAIR: dict[tuple[int, int], str] = {}
# 短节流：一轮刷新里 read_hid 会跑两遍，连点刷新还会连着跑。同一台接收器
# 1 秒内只发一次 HID++ 报文，避免报文打多了设备不应答（表现为「断连」）。
_RECENT: dict[tuple[int, int], tuple[float, Reading]] = {}
_MIN_GAP = 1.0


def _deadline_for(key: tuple[int, int]) -> float:
    """同一轮刷新里多个接口共用一份预算（轮次由 protocols.begin_round 推进）。"""
    from .base import round_id
    rid = round_id()
    now = time.monotonic()
    hit = _ROUND.get(key)
    if hit is None or hit[0] != rid:
        dl = now + PROBE_BUDGET
        _ROUND[key] = (rid, dl)
        return dl
    return hit[1]


def _pair_name(dev: HidDevice, rid: int, devidx: int, length: int,
               key: tuple[int, int]) -> str:
    """读 HID++ 0x0005 设备名；失败返回空串，绝不拖累电量读取。

    只在真读到名字时缓存：切换模式那一瞬间设备往往还没准备好，以前把空串
    也写进缓存，之后就永远不再重问，卡片一直显示「USB Receiver」，
    只能重启软件才能恢复。
    """
    ck = (key[0], key[1], devidx)
    hit = _PAIR_NAMES.get(ck)
    if hit:
        return hit
    name = ""
    try:
        q = _hidpp_call(dev, rid, devidx, 0x00, 0x00, b"\x00\x05",
                        length, timeout=0.08)
        fi = q[4] if q and len(q) >= 5 else 0
        if fi:
            r = _hidpp_call(dev, rid, devidx, fi, 0x01, b"\x00",
                            length, timeout=0.08)
            if r and len(r) >= 5:
                name = (bytes(r[4:20]).split(b"\x00")[0]
                        .decode("utf-8", "replace").strip())
    except Exception:
        name = ""
    if name:
        _PAIR_NAMES[ck] = name
        _KNOWN_PAIR[(key[0], key[1])] = name
    return name


def known_pair(vid: int, pid: int) -> str:
    """本轮没读到也能拿回的配对名（只读内存，不发任何报文）。"""
    return _KNOWN_PAIR.get((vid, pid), "")


def reset_state() -> None:
    """手动刷新 / 设备变化：清掉会累积的退避与节流，保留已学到的身份。

    以前 protocols.reset_backoff() 只清通用层，本模块自己的 30 秒退避
    清不掉，于是「多次刷新后读不到」只能靠重启软件恢复。
    """
    _FAILS.clear()
    _SKIP_UNTIL.clear()
    _ROUND.clear()
    _RECENT.clear()


def _recent(key: tuple[int, int]) -> Optional[Reading]:
    hit = _RECENT.get(key)
    if hit and (time.monotonic() - hit[0]) < _MIN_GAP:
        return copy.copy(hit[1])
    return None


def read_logitech(iface: HidIface) -> Reading:
    """统一入口：短节流 + 无论哪条返回分支都补上已知配对名。

    抽成一层是为了不把 pair_name= 散到十几个 return 里；以前只要有一轮
    没读到，读数里就没有配对名，合并改名随之失效，卡片与「添加设备」
    菜单一起退回「USB Receiver」。
    """
    key = (iface.vid, iface.pid)
    r = _recent(key)
    if r is None:
        r = _read_logitech_raw(iface)
        if r.ok and r.level is not None:
            _RECENT[key] = (time.monotonic(), copy.copy(r))
    if not r.pair_name:
        r.pair_name = known_pair(iface.vid, iface.pid)
    return r


def _read_logitech_raw(iface: HidIface) -> Reading:
    key = (iface.vid, iface.pid)
    now = time.monotonic()
    if _SKIP_UNTIL.get(key, 0.0) > now:
        return Reading(ok=False, source="罗技 HID++",
                       detail="连续读不到，稍后自动重试")
    rid = 0x11 if iface.output_len >= 20 else 0x10
    length = 20 if rid == 0x11 else 7
    try:
        dev = HidDevice(iface.path, write=True)
    except HidError as e:
        return Reading(ok=False, source="罗技 HID++", detail=str(e))
    try:
        deadline = _deadline_for(key)
        hit = _LAST.get(key)
        slots = ([hit[0]] + [s for s in _SLOTS if s != hit[0]]
                 if hit else list(_SLOTS))
        feats = ([f for f in _FEATURES if hit and f[0] == hit[1]]
                 + [f for f in _FEATURES if not hit or f[0] != hit[1]])
        for devidx in slots:
            for fid, kind in feats:
                left = deadline - time.monotonic()
                if left < 0.05:
                    _note_fail(key)
                    return Reading(ok=False, source="罗技 HID++",
                                   detail="探测超时（这一轮先跳过，下轮再试）")
                rep = _hidpp_call(dev, rid, devidx, 0x00, 0x00,
                                  bytes([fid >> 8, fid & 0xFF]), length,
                                  timeout=min(CALL_TIMEOUT, left))
                if not rep or (len(rep) > 3 and rep[3] & 0x80):
                    continue
                featidx = rep[4]
                if not featidx:
                    continue
                left = deadline - time.monotonic()
                if left < 0.05:
                    _note_fail(key)
                    return Reading(ok=False, source="罗技 HID++",
                                   detail="探测超时（这一轮先跳过，下轮再试）")
                if kind == "unified":
                    # 0x1004：fn0 = 能力，fn1 = 当前电量/档位/状态。
                    cap = _hidpp_call(dev, rid, devidx, featidx, 0x00, b"",
                                      length, timeout=min(CALL_TIMEOUT, left))
                    if not cap or len(cap) < 6:
                        continue
                    has_pct = bool(cap[5] & 0x02)   # flags bit1：有百分比电量计
                    cap_note = _capability_note(cap)
                    rep = _hidpp_call(dev, rid, devidx, featidx, 0x01, b"",
                                      length, timeout=min(CALL_TIMEOUT, left))
                    if not rep or len(rep) < 7:
                        # 设备没回状态就不猜数字（用户红线：绝不显示假电量）
                        _note_fail(key)
                        return Reading(ok=False, present=True,
                                       source="罗技 HID++（0x1004 统一电池）",
                                       detail="设备没回电量状态，这一轮不猜数字")
                    if has_pct and 0 <= rep[4] <= 100:
                        _note_ok(key, devidx, fid)
                        return Reading(ok=True, level=int(rep[4]),
                                       status=_status_name_unified(rep[6]),
                                       source="罗技 HID++（0x1004 统一电池）",
                                       detail=(f"索引 {devidx} 电量 {rep[4]} · "
                                               f"{_status_note_unified(rep[6])}"
                                               f" · {cap_note}"),
                                       pair_name=_pair_name(dev, rid, devidx,
                                                            length, key),
                                       raw=rep)
                    _note_ok(key, devidx, fid)
                    return Reading(ok=False, present=True,
                                   source="罗技 HID++（0x1004 统一电池）",
                                   detail=(f"该设备只报档位"
                                           f"（{_bucket_name(rep[5])}），不提供百分比"
                                           f" · {cap_note}"),
                                   raw=rep)
                rep = _hidpp_call(dev, rid, devidx, featidx, 0x00, b"", length,
                                  timeout=min(CALL_TIMEOUT, left))
                if not rep or len(rep) < 6:
                    continue
                if kind == "status" and len(rep) >= 7:
                    _note_ok(key, devidx, fid)
                    return Reading(ok=True, level=max(0, min(100, rep[4])),
                                   status=_status_name(rep[6]),
                                   source="罗技 HID++（0x1000 电池状态）",
                                   detail=f"索引 {devidx} 状态 {rep[6]}",
                                   raw=rep)
                if kind == "voltage":
                    mv = rep[4] | (rep[5] << 8)
                    if 2000 <= mv <= 5000:
                        return Reading(ok=False,
                                       source="罗技 HID++（0x1001 电压）",
                                       detail=f"{mv} mV（该机型不直接给百分比）",
                                       raw=rep)
        _note_fail(key)
        return Reading(ok=False, source="罗技 HID++", detail="没有找到电池特性")
    except HidError as e:
        _note_fail(key)
        busy = str(e) == WRITE_BUSY_HINT
        return Reading(ok=False,
                       status="busy" if busy else "unknown",
                       source="罗技 HID++", detail=str(e))
    finally:
        dev.close()


def _note_ok(key: tuple[int, int], devidx: int, fid: int) -> None:
    _LAST[key] = (devidx, fid)
    _FAILS[key] = 0
    _SKIP_UNTIL.pop(key, None)


def _note_fail(key: tuple[int, int]) -> None:
    """连续读不到就先别每轮都试，免得一张读不到的卡拖住整个面板。

    但「已经成功读到过」的设备（_LAST 里有槽位记录）不歇 30 秒：双模
    切换、驱动临时占用都只是偶尔一轮不应答，一歇就把「偶尔读不到」变成
    「长时间读不到」，只能重启软件恢复。这类设备每轮仍只问一次，由
    read_logitech 的短节流保证一轮内不重复发报文。
    """
    if key in _LAST:
        _FAILS[key] = 0
        _SKIP_UNTIL.pop(key, None)
        return
    n = _FAILS.get(key, 0) + 1
    _FAILS[key] = n
    if n >= 3:
        _SKIP_UNTIL[key] = time.monotonic() + 30.0
        _FAILS[key] = 0


@register
class LogitechHIDPP(DeviceProtocol):
    id = "logitech"
    name = "罗技 HID++ 2.0"
    priority = 46
    auto = False
    long_report_first = True

    def match(self, iface: HidIface) -> bool:
        return iface.vid == 0x046D and iface.output_len >= 7

    def read(self, iface: HidIface) -> Optional[Reading]:
        return read_logitech(iface)
