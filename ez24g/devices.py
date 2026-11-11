# -*- coding: utf-8 -*-
"""设备服务：把「配置里的设备清单」翻译成「界面要显示的 Reading 列表」。

职责边界：
    这一层只管业务流程（谁算有协议、休眠怎么显示、上次读数怎么兜底），
    具体怎么跟设备说话全部交给 providers/（HID / 蓝牙 BLE），
    具体用什么协议读 HID 交给 protocols/。

UI 只需要三个函数：
    read_all(ifaces)        读所有设备（HID + 蓝牙）
    add_device(...) / remove_device(...) / rename_device(...)

两类设备键：
    HID    x{VID}{PID}     例如 x373B1278
    蓝牙   bt:{MAC}        例如 bt:DE:1B:B4:46:93:66
"""
from __future__ import annotations

import copy
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from . import config, providers
from .hidapi import HidIface, enumerate_hid
from .model import Reading
from .providers import TRANSPORT_BT, DeviceRef, xmos
from .protocols import begin_round, read_best


# ---------------------------------------------------------------------------
# 设备清单
# ---------------------------------------------------------------------------
def add_device(vid: int, pid: int, name: str,
               kind: str = "hid", addr: str = "") -> bool:
    devs = config.load_saved_devices()
    key = (config.bt_key(addr) if kind == TRANSPORT_BT
           else config.device_key(vid, pid))
    if any(config.key_for(d) == key for d in devs):
        return False
    entry = {"kind": kind, "vid": int(vid), "pid": int(pid),
             "name": name}
    if kind == TRANSPORT_BT:
        entry["addr"] = config.normalize_addr(addr)
    devs.append(entry)
    config.save_saved_devices(devs)
    return True


def remove_device(key: str) -> Optional[dict]:
    """删除设备，同时清掉它的协议、上次读数、自定义名。"""
    devs = config.load_saved_devices()
    removed = next((d for d in devs if config.key_for(d) == key), None)
    if removed is None:
        return None
    config.save_saved_devices([d for d in devs if config.key_for(d) != key])
    vid, pid = int(removed.get("vid", 0) or 0), int(removed.get("pid", 0) or 0)
    if vid or pid:
        config.forget_protocol(vid, pid)
        config.forget_last(vid, pid)
    last = config.last_store()
    last.pop(key, None)
    config.save_last(last)
    config.set_custom_name(key, "")
    # 手动档位也随设备一起清掉，免得删了再加回来还显示旧的估算值
    config.set_manual_level(key, None)
    # 蓝牙的手动绑定 / 识别来源记忆也随设备一起清掉，不留脏配置
    if str(removed.get("kind", "")).lower() == "bt":
        addr = config.normalize_addr(removed.get("addr", ""))
        config.set_bt_manual(addr, None)
        config.set_bt_source(addr, "")
    return removed


def rename_device(key: str, name: str) -> None:
    # 卡片上改的是显示名，存到 names；devices 里的 name 只作为添加时的初值
    config.set_custom_name(key, name)


def display_name(vid: int, pid: int, ifaces: list[HidIface],
                 fallback: str = "") -> str:
    key = config.device_key(vid, pid)
    custom = config.custom_names().get(key)
    if custom:
        return custom
    prod = next((d.product for d in ifaces
                 if d.vid == vid and d.pid == pid and d.product), "")
    return prod or fallback or f"未知设备 {vid:04X}:{pid:04X}"


def display_name_bt(addr: str, fallback: str = "") -> str:
    key = config.bt_key(addr)
    return (config.custom_names().get(key) or fallback
            or f"蓝牙设备 {config.normalize_addr(addr)}")


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------
def _meta_iface(cand: list[HidIface]) -> HidIface:
    """优先拿厂商使用页那条接口，界面上的 UP 值才是设备真实身份。"""
    return next((d for d in cand if d.is_vendor), cand[0])


def _mac_of(ifaces) -> str:
    """从挂在蓝牙下的 HID 接口反查 MAC（拿不到就返回空串）。"""
    for d in ifaces or []:
        if not getattr(d, "is_bluetooth", False):
            continue
        try:
            from .providers import bt_pnp
            mac = bt_pnp.mac_from_instance(d.parent or d.instance or "")
        except Exception:
            mac = ""
        if mac:
            return mac
    return ""


def has_protocol(vid: int, pid: int) -> bool:
    return config.proto_key(vid, pid) in config.user_protocols()


def _remember_protocol(vid: int, pid: int, protocol_id: str) -> None:
    """读到过就把协议记下来，下一轮直接从它开始，不用全量试探。

    以前只有「已添加设备」会存协议，自动发现的接收器（046D:C547）每轮都
    把几十种读法从头试一遍，单它一台就能拖 6 秒，整轮刷新跟着卡死。
    """
    if not protocol_id:
        return
    if config.proto_key(vid, pid) in config.user_protocols():
        return
    try:
        if protocol_id == "standard":
            config.save_standard_protocol(vid, pid)
        elif protocol_id not in ("listen", "captured"):
            protos = config.user_protocols()
            protos[config.proto_key(vid, pid)] = {"kind": "vendor",
                                                 "id": protocol_id}
            config.update_config(protocols=protos)
    except Exception:
        pass


def _prefer_id(vid: int, pid: int) -> Optional[str]:
    """已保存的厂商协议 id（read_best 会把它提到最前面）。"""
    proto = config.user_protocols().get(config.proto_key(vid, pid)) or {}
    return proto.get("id") if proto.get("kind") == "vendor" else None


def _from_preferred(r, prefer: Optional[str]) -> bool:
    """本轮读数是不是来自「已学会的专属读法」。

    设备已经记住某条厂商协议时，专属读法这一轮没应答，通用读法（标准
    HID 电量等）顶上来的数字不能采信 —— 罗技接收器就会给出一个 15，
    用户会看到「电量固定 15」。宁可这一轮沿用上次读数，也不显示假数字。
    只影响已学会专属读法的设备，其它设备一律放行。
    """
    if r is None or not r.ok or not prefer:
        return True
    got = getattr(r, "protocol_id", "") or ""
    return got in ("", prefer)


def _refs(ifaces: Optional[list[HidIface]], include_bt: bool) -> list[DeviceRef]:
    return providers.enumerate_refs(ifaces, include_bt=include_bt)


def _parallel(fn, items, workers: int = 4) -> list:
    """保序并发执行（结果顺序与输入一致）。

    几台设备串行读时，耗时是「最慢那台 × 台数」；并发后≈最慢那台。
    每台设备各开各的 HID 句柄，互不影响。
    """
    items = list(items)
    if len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        futs = [pool.submit(fn, x) for x in items]
        out = []
        for f in futs:
            try:
                out.append(f.result())
            except Exception:
                out.append(None)
        return out


def _reading_bt(dev: dict, refs: list[DeviceRef],
                hid_refs: Optional[list[DeviceRef]] = None) -> Reading:
    """蓝牙设备：读 GATT 电量，或者明确说清为什么读不了。"""
    key = config.key_for(dev)
    addr = config.normalize_addr(dev.get("addr", ""))
    ref = next((r for r in refs if r.key == key), None)
    name = display_name_bt(addr, (ref.name if ref else "") or dev.get("name", ""))

    if ref is None:
        # 什么都枚举不到时才给「装 winrt」的提示；经典蓝牙走系统属性，不需要 winrt
        if (not providers.bt_available()
                and not providers.bt_system_available()
                and not providers.bt_pnp_available()):
            return Reading(ok=False, present=True, status="no_support",
                           detail=providers.bt_install_hint(), name=name,
                           transport="bt", addr=addr)
        return Reading(ok=False, present=False, status="unplugged",
                       detail="未连接", name=name, transport="bt", addr=addr)

    r = providers.read_ref(ref, hid_refs=hid_refs)
    if r is None:
        r = Reading(ok=False, present=True, status="asleep",
                    detail="蓝牙读取失败", transport="bt", addr=addr)
    r.name = name
    r.transport = "bt"
    r.addr = addr
    return r


def read_device(dev: dict, ifaces: Optional[list[HidIface]] = None,
                include_manual: bool = False,
                refs: Optional[list[DeviceRef]] = None) -> Reading:
    """读一台已添加的设备（HID 或蓝牙，按配置里的 kind 分派）。"""
    if str(dev.get("kind", "")).lower() == TRANSPORT_BT:
        if refs is None:
            refs = _refs(None, include_bt=True)
        return _reading_bt(dev, refs)

    vid = int(dev["vid"])
    pid = int(dev["pid"])
    name = dev.get("name") or ""
    if refs is None:
        refs = _refs(ifaces, include_bt=False)
    cand = next((r.ifaces for r in refs if r.vid == vid and r.pid == pid), None)
    if cand is None:
        cand = [d for d in (ifaces if ifaces is not None else enumerate_hid())
                if d.vid == vid and d.pid == pid]
    if not cand:
        return Reading(ok=False, present=False,
                       name=name or f"{vid:04X}:{pid:04X}",
                       status=("unplugged" if has_protocol(vid, pid)
                               else "unknown_proto"),
                       vid=vid, pid=pid, detail="未连接")

    name = display_name(vid, pid, cand, name)
    proto = config.user_protocols().get(config.proto_key(vid, pid)) or {}
    prefer = proto.get("id") if proto.get("kind") == "vendor" else None

    r = read_best(cand, include_manual=include_manual, prefer_id=prefer)
    if not _from_preferred(r, prefer):
        r = None                 # 别的读法顶上来的数字不可信，本轮算没读到

    if r is not None and r.ok:
        # 第一次读到就记下来，之后休眠也能判断"有协议"，也省掉全量试探
        _remember_protocol(vid, pid, getattr(r, "protocol_id", ""))

    supported = has_protocol(vid, pid)
    if r is None:
        r = Reading(ok=False, present=True, name=name,
                    status="asleep" if supported else "unknown_proto",
                    detail=("设备休眠" if supported
                            else "协议未知，无法读取电量"))
    elif not r.ok and supported:
        r.status = "asleep"
        r.detail = "设备休眠"
        r.source = "标准 HID 电量" if proto.get("kind") == "standard" else "抓包协议"

    r.name = name
    r.present = True
    meta = _meta_iface(cand)
    r.vid, r.pid, r.usage_page = vid, pid, meta.usage_page
    r.hid_known = True
    r.transport = "bt" if any(d.is_bluetooth for d in cand) else "hid"
    if r.transport == "bt" and not r.addr:
        r.addr = _mac_of(cand)          # 供跨来源去重
    return r


def discover_extras(ifaces: Optional[list[HidIface]] = None,
                    skip_keys: set[str] = frozenset(),
                    include_manual: bool = False,
                    refs: Optional[list[DeviceRef]] = None) -> dict[str, Reading]:
    """把未添加的设备聚合成额外卡片，每个 VID:PID 一张。

    以前只收厂商页（usage_page >= 0xFF00），所以走标准 HID 电量的蓝牙设备
    在 --scan 里完全看不到。现在 providers.hid 会把「带标准电量字段」的设备
    也列出来，这里只是照单收下。
    """
    refs = refs if refs is not None else _refs(ifaces, include_bt=False)
    # 删除过的设备不再自动发现回来（+ 添加设备里手动选才恢复）
    skipped = set(skip_keys) | config.ignored_devices()

    out: dict[str, Reading] = {}
    targets = [ref for ref in refs if ref.key not in skipped]
    for item in _parallel(lambda ref: _extra_reading(ref, include_manual),
                          targets):
        if item:
            out[item[0]] = item[1]
    return out


def _extra_reading(ref: DeviceRef, include_manual: bool):
    """读一台「未添加」的设备，返回 (key, Reading)。"""
    cand = ref.ifaces
    prefer = _prefer_id(ref.vid, ref.pid)
    r = providers.read_ref(ref, include_manual=include_manual,
                           prefer_id=prefer)
    if not _from_preferred(r, prefer):
        r = None                 # 同上：不采信别的读法顶上来的数字
    name = display_name(ref.vid, ref.pid, cand, ref.product)
    if r is None:
        # 协议早就存好了、这一轮只是没应答，和「协议未知」不是一回事。
        # 与 read_device（已添加设备）的判定对齐，后者一直用 asleep。
        supported = has_protocol(ref.vid, ref.pid)
        r = Reading(ok=False, present=True,
                    status="asleep" if supported else "unknown_proto",
                    source="HID 通用",
                    detail=("设备暂时没回应" if supported
                            else "协议未知，无法读取电量"))
    elif r.ok and r.level is not None:
        # 自动发现的设备也把协议存下来：下一轮直接用，不再全量试探
        _remember_protocol(ref.vid, ref.pid, getattr(r, "protocol_id", ""))
    r.name = name
    meta = _meta_iface(cand)
    r.vid, r.pid, r.usage_page = ref.vid, ref.pid, meta.usage_page
    r.hid_known = True
    r.transport = ref.transport
    if ref.addr:
        r.addr = ref.addr              # 蓝牙 HID 身份：供跨来源去重
    return ref.key, r


def _dedupe_bt_hid(res: dict[str, Reading],
                   saved: Optional[list[dict]] = None) -> dict[str, Reading]:
    """同一台蓝牙设备只留一张卡（HID 身份 vs 蓝牙身份）。

    现象：只连两台蓝牙却显示三张 —— 多出来的那张是同一台设备的 HID 接口
    被自动发现（键是 VID:PID，transport 却是 bt），和 bt:MAC 那条重复。

    规则（保守）：用户手动添加过的 HID 设备一律不动；只有「自动发现且 MAC
    与某条蓝牙卡相同」的那条才丢；保留蓝牙那条（连接状态和原因更准）。
    """
    saved = saved if saved is not None else config.load_saved_devices()
    addrs = {config.normalize_addr(d.get("addr", "")) for d in saved
             if str(d.get("kind", "")).lower() == TRANSPORT_BT}
    addrs |= {r.addr for k, r in res.items() if k.startswith("bt:") and r.addr}
    addrs.discard("")
    if not addrs:
        return res
    kept = {config.key_for(d) for d in saved}
    for key in [k for k, r in res.items()
                if not k.startswith(("bt:", "g:"))
                and getattr(r, "transport", "") == "bt"
                and getattr(r, "addr", "") in addrs
                and k not in kept]:
        res.pop(key, None)
    return res


def read_hid(ifaces: Optional[list[HidIface]] = None,
             include_manual: bool = False,
             refs: Optional[list[DeviceRef]] = None,
             saved: Optional[list[dict]] = None) -> dict[str, Reading]:
    """只读 HID 部分：快，几毫秒到一两百毫秒。"""
    begin_round()          # 推进轮次：各协议的「本轮预算」跟着重置
    ifaces = ifaces if ifaces is not None else enumerate_hid()
    refs = refs if refs is not None else _refs(ifaces, include_bt=False)
    saved = saved if saved is not None else config.load_saved_devices()
    res: dict[str, Reading] = {}

    def one(dev: dict):
        key = config.key_for(dev)
        try:
            return key, read_device(dev, ifaces, include_manual, refs=refs)
        except Exception as e:
            return key, Reading(ok=False, present=True,
                                name=dev.get("name", ""),
                                vid=int(dev.get("vid", 0) or 0),
                                pid=int(dev.get("pid", 0) or 0),
                                status="unknown_proto",
                                detail=f"读取失败：{e}")

    targets = [d for d in saved
               if str(d.get("kind", "")).lower() != TRANSPORT_BT]
    skip = {config.key_for(d) for d in targets} | config.ignored_devices()
    extra_refs = [r for r in refs if r.key not in skip]

    def run(job):
        kind, obj = job
        if kind == "saved":
            return one(obj)
        try:
            return _extra_reading(obj, include_manual)
        except Exception:
            return None

    jobs = ([("saved", d) for d in targets]
            + [("extra", r) for r in extra_refs])
    # 已添加的和自动发现的一起并发：分两段跑等于多等最慢那段一次
    for item in _parallel(run, jobs, workers=6):
        if item:
            res[item[0]] = item[1]
    return _dedupe_bt_hid(res, saved)


def _bt_reading_or_error(dev: dict, bt_refs: list[DeviceRef],
                         hid_refs: Optional[list[DeviceRef]] = None) -> Reading:
    try:
        # 没带本轮 HID 清单时保持原来两参数调用，兼容自检里的替换实现。
        if hid_refs:
            return _reading_bt(dev, bt_refs, hid_refs)
        return _reading_bt(dev, bt_refs)
    except Exception as e:
        return Reading(
            ok=False, present=True, status="no_support",
            detail=f"蓝牙读取失败：{e}", transport="bt",
            addr=config.normalize_addr(dev.get("addr", "")))


def read_bt(include_manual: bool = False,
            refs: Optional[list[DeviceRef]] = None,
            saved: Optional[list[dict]] = None) -> dict[str, Reading]:
    """只读蓝牙部分：慢（GATT），所以单独一次，能失败就失败。

    多台蓝牙并发读：每台 GATT 最坏 4 秒，4 台串行就是 16 秒，整轮刷新
    全被它们拖住（面板看起来像「点了刷新没反应」）。并发后总耗时≈最慢那台。
    """
    saved = saved if saved is not None else config.load_saved_devices()
    refs = refs if refs is not None else _refs(None, include_bt=True)
    bt_refs = [r for r in refs if r.kind == TRANSPORT_BT]
    # 同一轮里已经数过的 HID 清单：蓝牙 HID 联动直接查它，不再逐台重新枚举。
    # 调用方只传了蓝牙清单时这里是空列表，_linked_hid 会退回原行为。
    hid_refs = [r for r in refs if r.kind != TRANSPORT_BT]
    res: dict[str, Reading] = {}
    targets = [d for d in saved
               if str(d.get("kind", "")).lower() == TRANSPORT_BT]
    if len(targets) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
            futs = {pool.submit(_bt_reading_or_error, d, bt_refs, hid_refs): d
                    for d in targets}
            for fut in as_completed(futs):
                dev = futs[fut]
                try:
                    res[config.key_for(dev)] = fut.result()
                except Exception:
                    res[config.key_for(dev)] = _bt_reading_or_error(
                        dev, bt_refs, hid_refs)
    else:
        for dev in targets:
            res[config.key_for(dev)] = _bt_reading_or_error(
                dev, bt_refs, hid_refs)

    # 没添加过、但确实能读到电量的蓝牙设备 → 直接显示成额外卡片
    ignored = config.ignored_devices()
    for ref in bt_refs:
        if ref.key in res or not ref.connected or ref.key in ignored:
            continue
        r = providers.read_ref(ref, hid_refs=hid_refs)
        if r is None or not r.ok:
            continue
        r.name = display_name_bt(ref.addr, ref.name)
        r.transport = "bt"
        res[ref.key] = r
    return res


def merge_paired_links(res: dict[str, Reading]) -> dict[str, Reading]:
    """同一台双模设备（2.4G 接收器 + 蓝牙）只留一张卡。

    罗技接收器通过 HID++ 0x0005 上报配对设备名（例如 G517），蓝牙侧的
    卡片名通常是更完整的型号（G517 LS TKL）。名字对得上时合成一张：
      · 数值优先用 2.4G（实时、和厂商软件一致），2.4G 读不到才用蓝牙；
      · 名字优先用户自定义，其次蓝牙完整型号，最后配对名；
      · 只有带 pair_name 的接收器卡会参与，其它设备一律不动。
    没有蓝牙卡时也把接收器卡名改成配对名，避免仍显示「USB Receiver」。
    """
    paired = [(k, r) for k, r in res.items()
              if getattr(r, "pair_name", "") and not k.startswith(("g:", "bt:"))]
    if not paired:
        return res
    out = dict(res)
    custom = config.custom_names()
    groups = config.load_groups()
    in_group = {m for g in groups.values() for m in g["members"]}
    for hid_key, hr in paired:
        model = (hr.pair_name or "").strip()
        if len(model) < 3 or hid_key in in_group:
            continue          # 用户手动分过组 / 型号太短，不插手
        bt_keys = [k for k, r in out.items()
                   if k.startswith("bt:") and k != hid_key
                   and model.lower() in (r.name or "").lower()]
        bt_name = next((out[k].name for k in bt_keys if out[k].name), "")
        label = custom.get(hid_key) or bt_name or model
        if hr.ok and hr.level is not None:
            merged = copy.copy(hr)
            merged.name = label
        else:
            fallback = next((out[k] for k in bt_keys
                             if out[k].ok and out[k].level is not None), None)
            merged = copy.copy(fallback) if fallback is not None else copy.copy(hr)
            merged.name = label
            merged.group = ""
        out[hid_key] = merged
        for bk in bt_keys:
            out.pop(bk, None)
    return out


def merge_groups(res: dict[str, Reading]) -> dict[str, Reading]:
    """把配置里的设备组并成一张卡（同一台设备的 2.4G / 蓝牙两条链路）。

    组键 g:{id}；组内按 prefer 选一条链路：auto = 谁有新鲜电量用谁，
    都不行就用在线的那条并标注「暂不支持读取」，全断才显示未连接。
    """
    groups = config.load_groups()
    if not groups:
        return res
    out: dict[str, Reading] = {}
    used: set[str] = set()
    for gid, g in groups.items():
        have = [m for m in g["members"] if m in res]
        if not have:
            continue
        chosen = _pick_member(res, g["members"], g["prefer"])
        if chosen is None:
            continue
        merged = copy.copy(chosen)
        merged.name = g["name"] or chosen.name
        merged.present = any(res[m].present for m in have)
        merged.group = gid
        if not merged.present:
            merged.ok, merged.level, merged.stale = False, None, False
            merged.status, merged.detail = "unplugged", "未连接"
        elif not merged.ok:
            merged.level, merged.stale = None, False
            merged.status = "unknown_proto"
            merged.detail = f"{merged.transport_text} · 暂不支持读取"
        out["g:" + gid] = merged
        used.update(have)
    for key, r in res.items():
        if key not in used:
            out[key] = r
    return out


def _pick_member(readings: dict[str, Reading], members: list,
                 prefer: str) -> Optional[Reading]:
    cand = [(m, readings[m]) for m in members if m in readings]
    if not cand:
        return None
    if prefer == "bt":
        cand.sort(key=lambda mr: (0 if mr[1].transport == "bt" else 1,
                                  0 if mr[1].ok else 1, -mr[1].ts))
    elif prefer == "2.4g":
        cand.sort(key=lambda mr: (0 if mr[1].transport != "bt" else 1,
                                  0 if mr[1].ok else 1, -mr[1].ts))
    else:
        cand.sort(key=lambda mr: (0 if (mr[1].ok and not mr[1].stale) else 1,
                                  0 if mr[1].ok else 1, -mr[1].ts))
    return cand[0][1]


def order_readings(res: dict[str, Reading],
                   saved: Optional[list[dict]] = None) -> dict[str, Reading]:
    """能读到电量的排最上面；同档内已添加设备按配置顺序，其余按设备键。

    排序键刻意不含电量数值：只有「能否读到 / 是否在线」变化才移动卡片，
    否则每刷新一次就跳一下。
    """
    saved = saved if saved is not None else config.load_saved_devices()
    # 双模设备（2.4G + 蓝牙）在发布前合成一张卡；三段式刷新的每一段
    # 都走这里，所以中途不会出现「两张卡闪一下」。构造数据无 pair_name
    # 时此调用是空操作，不影响既有排序与自检。
    res = merge_paired_links(res)
    idx = {config.key_for(d): i for i, d in enumerate(saved)}
    groups = config.load_groups()

    def saved_index(key: str) -> int:
        if key.startswith("g:"):
            gid = key[2:]
            members = groups.get(gid, {}).get("members", [])
            vals = [idx[m] for m in members if m in idx]
            return min(vals) if vals else -1
        return idx.get(key, -1)

    order = config.get_card_order()
    pos = {k: i for i, k in enumerate(order)}

    def sort_key(item):
        key, r = item
        # stale（沿用旧值）不进「有实时电量」那一档，避免设备其实已经关闭
        # 还顶在第一张；它仍排在真正拔掉的设备前面。
        readable = 0 if (r.ok and r.level is not None and not r.stale) else 1
        alive = 0 if r.present else 1
        si = saved_index(key)
        added = 0 if si >= 0 else 1
        idx = pos.get(key, len(order))     # 用户拖出来的区内顺序，新设备排末尾
        # 红线 R1：readable 必须第一位；R4：用户顺序 idx 必须压过 alive/added，
        # 否则同区内 present/added 不同的两张卡永远换不了位。
        # card_order 为空时 idx 全为 len(order)=0，行为与旧键完全一致。
        return (readable, idx, alive, added, key)

    return dict(sorted(res.items(), key=sort_key))


def read_all(ifaces: Optional[list[HidIface]] = None,
             include_manual: bool = False,
             include_bt: bool = True) -> dict[str, Reading]:
    """读所有已添加设备 + 自动发现的新设备。"""
    saved = config.load_saved_devices()
    res = read_hid(ifaces, include_manual, saved=saved)
    if include_bt:
        try:
            res.update(read_bt(include_manual, saved=saved))
        except Exception:
            pass
    res = _dedupe_bt_hid(res, saved)
    res = merge_groups(res)
    res = apply_xmos_levels(res)       # 先补设备自己上报的真实值
    res = apply_manual_levels(res)     # 再用手动档位兜底，真实值永远优先
    return order_readings(apply_last_known(res), saved)


def apply_xmos_levels(res: dict[str, Reading]) -> dict[str, Reading]:
    """觅声这类 USB 音频设备：电量挂在音频驱动的控制通道上，HID 读不到。

    只在「这一轮没读到 + 设备还在」时补，绝不覆盖任何真实读数。
    补上去的是设备自己上报的百分比（不是估算值），所以状态写 unknown
    （卡片显示「已连接」）——设备不告诉我们充电状态，就不编一个。
    白名单之外的厂商直接跳过，读不到也一律不动原结果。
    """
    for r in res.values():
        if r.ok and r.level is not None:
            continue
        if not r.present:
            continue
        try:
            vid = int(getattr(r, "vid", 0) or 0)
            pid = int(getattr(r, "pid", 0) or 0)
        except Exception:
            continue
        if not xmos.supported(vid):
            continue
        level = xmos.read_level(vid, pid)
        if level is None:
            continue
        r.ok = True
        r.level = level
        r.status = "unknown"          # 设备不报充电状态，就不编一个
        r.source = "觅声音频通道"
        r.detail = "设备上报（音频控制通道 0x87），未提供充电状态"
        r.stale = False
    return res


def apply_manual_levels(res: dict[str, Reading]) -> dict[str, Reading]:
    """设备只给「低/中/高」、拿不到精确值时的兜底。

    只在「这一轮没读到 + 接口还在」时补，绝不覆盖真实读数；
    真断开的设备也不补，避免把已关机设备假装成在线。
    数值是用户指定的估算值，source/detail 必须写清「非实测」。
    """
    levels = config.manual_levels()
    if not levels:
        return res
    for key, r in res.items():
        if r.ok and r.level is not None:
            continue
        if not r.present:
            continue
        try:
            lv = int(levels.get(key))
        except Exception:
            continue
        if not 0 <= lv <= 100:
            continue
        r.ok = True
        r.level = lv
        r.status = "manual"
        r.source = "手动档位"
        r.detail = "手动档位 · 非实测（高 99 / 中 66 / 低 33）"
        r.stale = False
    return res


# ---------------------------------------------------------------------------
# 上次读数兜底
# ---------------------------------------------------------------------------
# 旧值只在「短暂丢失识别」时沿用：接收器还插着、协议已知、这一轮没应答。
# 超过这个时间还没读到，就判定设备已关闭 —— 卡片同步显示未连接并沉到底，
# 绝不拿几天前的数字冒充在线（用户明确要求「关闭即归位」不能被兜底影响）。
STALE_TTL = 120.0


def apply_last_known(res: dict[str, Reading]) -> dict[str, Reading]:
    """设备休眠读不到时，沿用上次成功的数值，并标明"上次更新 HH:MM"。"""
    last = config.last_store()
    changed = False
    out: dict[str, Reading] = {}
    for key, r in res.items():
        if r.ok and r.level is not None:
            rec = {"level": r.level, "ts": r.ts, "name": r.name,
                   "source": r.source, "status": r.status,
                   "vid": r.vid, "pid": r.pid,
                   "usage_page": r.usage_page,
                   "transport": r.transport, "addr": r.addr,
                   "hid_known": r.hid_known}
            # 只在「电量/状态/名字/来源变了」或「显示的分钟变了」时才落盘：
            # 以前每轮刷新都整份重写 config.json（手动刷新、热插拔通知都会触发），
            # 而「上次更新 HH:MM」只精确到分钟，秒级差异写下去也没人看得到。
            old = last.get(key) or {}
            same_value = (old.get("level") == rec["level"]
                          and old.get("status") == rec["status"]
                          and old.get("name") == rec["name"]
                          and old.get("source") == rec["source"])
            try:
                same_minute = (
                    time.strftime("%H:%M", time.localtime(
                        float(old.get("ts", 0) or 0)))
                    == time.strftime("%H:%M", time.localtime(r.ts)))
            except Exception:
                same_minute = False
            if not (same_value and same_minute):
                last[key] = rec
                changed = True
            out[key] = r
            continue
        if (not r.ok) and key in last and r.status not in ("unknown_proto",
                                                           "unplugged",
                                                           "no_support"):
            item = last[key]
            try:
                level = int(item.get("level"))
            except Exception:
                level = None
            if level is not None:
                ts = float(item.get("ts", time.time()))
                if time.time() - ts > STALE_TTL:
                    # 太久没读到 → 当作设备已关闭，同步归位为「未连接」。
                    # 这里不写 stale，卡片就不会留在「有电量」那一档。
                    out[key] = Reading(
                        ok=False, present=False, status="unplugged",
                        detail="未连接（已关机或超出范围）",
                        name=r.name, source=r.source,
                        transport=r.transport, addr=r.addr,
                        vid=r.vid, pid=r.pid,
                        usage_page=r.usage_page, hid_known=r.hid_known)
                    continue
                stamp = time.strftime("%H:%M", time.localtime(ts))
                stale = Reading(
                    ok=True, level=level,
                    status=item.get("status", "unknown"),
                    source=item.get("source", ""),
                    name=r.name or item.get("name", ""),
                    ts=ts, stale=True,
                    detail=f"上次更新 {stamp} · {r.detail}")
                stale.vid = int(item.get("vid", 0) or 0)
                stale.pid = int(item.get("pid", 0) or 0)
                stale.usage_page = int(item.get("usage_page", 0) or 0)
                stale.hid_known = bool(item.get("hid_known", False))
                stale.transport = r.transport or item.get("transport", "hid")
                stale.addr = r.addr or item.get("addr", "")
                out[key] = stale
                continue
        out[key] = r
    if changed:
        config.save_last(last)
    return out
