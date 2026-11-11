# -*- coding: utf-8 -*-
"""集中配置：路径、读写、默认值。

配置文件：%APPDATA%\\SparkMonitor\\config.json
结构（全部可选，缺省即默认）：
{
  "devices":   [{"kind": "hid", "vid": 14139, "pid": 4728, "name": "鼠标"},
                {"kind": "bt", "addr": "AA:BB:CC:DD:EE:FF", "name": "蓝牙鼠标"}],
  "protocols": {"373B:1278": {"kind": "standard"}},             # 学到的协议
  "names":     {"x373B1278": "我的鼠标"},                        # 自定义名
  "last":      {"x373B1278": {"level": 80, "ts": 1750000000}},   # 上次读数
  "interval":  60,                                              # 刷新秒数
  "usbpcap":   "C:\\...\\USBPcapCMD.exe",
  "vendor_probe": true                                          # 自动识别已知厂商协议（设置里可关）
  "ignored_devices": [],                                       # 删除过的设备，不再自动加回
  "bt_manual": {}                                              # 蓝牙手动绑定电量来源
}

旧配置里的 {"vid": ..., "pid": ...} 仍然按 HID 设备读取，一台都不会丢。
"""
from __future__ import annotations

import copy
import functools
import json
import os
import shutil
import sys
import threading
from typing import Optional

APP_NAME = "星火 Monitor"
APP_NAME_EN = "Spark Monitor"
APP_ID = "SparkMonitor.BatteryPanel"
CONFIG_DIR_NAME = "SparkMonitor"   # 方案隔离：本交付单元独立配置目录
LEGACY_DIR_NAME = "BatteryTray_NO_MIGRATE"  # 旧配置目录（首次运行自动迁移，设备一台不少）

INTERVAL_MIN = 30
INTERVAL_MAX = 1800
INTERVAL_DEFAULT = 60


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
def project_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_DIR_READY = False


def config_dir() -> str:
    """独立配置目录；首次运行从旧目录复制 config.json，保证旧设备/设置不丢。

    两个方案同时运行也不会互相覆盖配置：各自写各自的 dirname。
    """
    global _DIR_READY
    base = os.environ.get("APPDATA") or project_dir()
    d = os.path.join(base, CONFIG_DIR_NAME)
    if not _DIR_READY:
        _DIR_READY = True
        try:
            os.makedirs(d, exist_ok=True)
            dst = os.path.join(d, "config.json")
            if not os.path.isfile(dst):
                legacy = os.path.join(base, LEGACY_DIR_NAME, "config.json")
                if os.path.isfile(legacy):
                    shutil.copy2(legacy, dst)
        except Exception:
            pass
    return d


def config_file() -> str:
    return os.path.join(config_dir(), "config.json")


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------
# 进程内缓存：render() 一帧会调几十次 getter，每次都 open + json.load 会把重绘
# 拖慢一倍（19.5KB 配置单次 0.44ms × 64 次 ≈ 28ms/帧，实测）。这里按
# 「路径 + mtime_ns + 文件大小」判新鲜度：外部直接改文件仍会被读到，
# 只有同一份内容才复用解析结果。
#
# 一把可重入锁同时解决两件事：
#   1. 缓存一致性（保存后立刻回填，读到的永远是刚写的那份）；
#   2. 读-改-写并发覆盖（工作线程写 last、UI 线程写 card_order 时，
#      两者原本会拿对方更新前的旧副本互相覆盖，实测 5/5 轮复现）。
_LOCK = threading.RLock()
_CACHE: dict = {"path": None, "sig": None, "data": None}
_SAVE_ERROR = ""


def _file_sig(path: str):
    """文件的 (mtime_ns, size)；取不到（不存在/无权限）返回 None。"""
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except Exception:
        return None


def last_save_error() -> str:
    """最近一次 save_config 的失败原因（成功时为空串）。

    以前 save_config 把异常整个吞掉，「设置重启就没了」永远查不出原因；
    这里只记录不改变行为，`--doctor` / 托盘日志可以取它。
    """
    return _SAVE_ERROR


def load_config() -> dict:
    """读配置 dict；同一份文件只解析一次。

    返回的是**副本**：调用方历来会就地改再 save_config，共享同一对象会串味。
    """
    with _LOCK:
        path = config_file()
        sig = _file_sig(path)
        if (_CACHE["data"] is not None and _CACHE["path"] == path
                and _CACHE["sig"] == sig):
            return copy.deepcopy(_CACHE["data"])
        data: dict = {}
        if sig is not None:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                data = {}
        _CACHE["path"] = path
        _CACHE["sig"] = sig
        _CACHE["data"] = data
        return copy.deepcopy(data)


def save_config(data: dict) -> None:
    global _SAVE_ERROR
    with _LOCK:
        path = config_file()
        try:
            os.makedirs(config_dir(), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as e:                      # noqa: BLE001
            _SAVE_ERROR = f"{type(e).__name__}: {e}"
            return
        _SAVE_ERROR = ""
        # 保存后回填缓存，省掉下一次读盘的解析
        _CACHE["path"] = path
        _CACHE["sig"] = _file_sig(path)
        _CACHE["data"] = data


def update_config(**kwargs) -> dict:
    """读-改-写一步完成，整段持锁（避免并发方用旧副本覆盖新值）。"""
    with _LOCK:
        cfg = load_config()
        cfg.update(kwargs)
        save_config(cfg)
    return cfg


def _atomic(fn):
    """把「读 → 改 → 写」整段放进同一把锁。

    下面这些 setter 都是 `读子字典 → 改 → update_config()` 两步，
    中间若被别的线程插进一次写，就会用读于更早时刻的副本覆盖对方的新值
    （实测：UI 线程写 card_order、工作线程写 last 时互相覆盖，5/5 轮复现）。
    RLock 可重入，所以包住整个函数体不会与内部的 update_config 死锁。
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# 具体字段
# ---------------------------------------------------------------------------
def clamp_interval(sec) -> int:
    try:
        sec = int(sec)
    except Exception:
        return INTERVAL_DEFAULT
    return max(INTERVAL_MIN, min(INTERVAL_MAX, sec))


def get_interval() -> int:
    return clamp_interval(load_config().get("interval", INTERVAL_DEFAULT))


def set_interval(sec) -> int:
    sec = clamp_interval(sec)
    update_config(interval=sec)
    return sec


def custom_names() -> dict:
    names = load_config().get("names")
    return names if isinstance(names, dict) else {}


@_atomic
def set_custom_name(key: str, name: str) -> None:
    names = custom_names()
    name = (name or "").strip()
    if name:
        names[key] = name
    else:
        names.pop(key, None)
    update_config(names=names)


def usbpcap_path() -> str:
    p = load_config().get("usbpcap")
    if isinstance(p, str) and os.path.isfile(p):
        return p
    return ""


def set_usbpcap_path(path: str) -> None:
    update_config(usbpcap=path)


def user_protocols() -> dict:
    v = load_config().get("protocols")
    return v if isinstance(v, dict) else {}


def proto_key(vid: int, pid: int) -> str:
    return f"{vid:04X}:{pid:04X}"


@_atomic
def save_user_protocol(vid: int, pid: int, proto: dict) -> None:
    protos = user_protocols()
    protos[proto_key(vid, pid)] = {
        "kind": "captured",
        "invert": bool(proto.get("invert", False)),
        "requests": list(proto.get("requests", [])),
        "offset": int(proto.get("offset", 2)),
    }
    update_config(protocols=protos)


@_atomic
def save_standard_protocol(vid: int, pid: int) -> None:
    protos = user_protocols()
    key = proto_key(vid, pid)
    if key not in protos:
        protos[key] = {"kind": "standard"}
        update_config(protocols=protos)


@_atomic
def forget_protocol(vid: int, pid: int) -> None:
    protos = user_protocols()
    protos.pop(proto_key(vid, pid), None)
    update_config(protocols=protos)


def vendor_probe_enabled() -> bool:
    """「自动识别」开关：默认开启。只影响已知厂商协议的自动只读试探。"""
    return bool(load_config().get("vendor_probe", True))


def set_vendor_probe(on: bool) -> None:
    update_config(vendor_probe=bool(on))


# ---------------------------------------------------------------------------
# 删除过的设备：不再自动出现在卡片里，只能从「+ 添加设备」手动选回来
# ---------------------------------------------------------------------------
def ignored_devices() -> set:
    v = load_config().get("ignored_devices")
    return set(v) if isinstance(v, list) else set()


def ignore_device(key: str) -> None:
    ks = ignored_devices()
    ks.add(key)
    update_config(ignored_devices=sorted(ks))


def unignore_device(key: str) -> None:
    ks = ignored_devices()
    ks.discard(key)
    update_config(ignored_devices=sorted(ks))


# ---------------------------------------------------------------------------
# 蓝牙手动绑定：固定电量 / 指定 GATT 读取位置
# ---------------------------------------------------------------------------
def bt_manual() -> dict:
    v = load_config().get("bt_manual")
    return v if isinstance(v, dict) else {}


def bt_manual_of(addr: str) -> dict:
    item = bt_manual().get(normalize_addr(addr))
    return dict(item) if isinstance(item, dict) else {}


# ---------------------------------------------------------------------------
# 蓝牙识别优先准则：记住上次成功的来源，连续失败就自动降回默认顺序
# ---------------------------------------------------------------------------
def bt_source_all() -> dict:
    v = load_config().get("bt_source")
    return v if isinstance(v, dict) else {}


def bt_source_of(addr: str) -> str:
    item = bt_source_all().get(normalize_addr(addr))
    return str(item.get("kind", "")) if isinstance(item, dict) else ""


def set_bt_source(addr: str, kind: str) -> None:
    all_items = bt_source_all()
    key = normalize_addr(addr)
    if kind:
        all_items[key] = {"kind": kind, "fails": 0}
    else:
        all_items.pop(key, None)
    update_config(bt_source=all_items)


def note_bt_source_fail(addr: str) -> None:
    """记住的来源连续 2 次读不到 → 清掉记忆，自动切回默认顺序。"""
    all_items = bt_source_all()
    key = normalize_addr(addr)
    item = all_items.get(key)
    if not isinstance(item, dict):
        return
    n = int(item.get("fails", 0)) + 1
    if n >= 2:
        all_items.pop(key, None)
    else:
        item["fails"] = n
    update_config(bt_source=all_items)


def parse_bt_manual(text: str):
    """把用户填的一行文字解析成绑定项。返回 (item, 错误信息)。

    支持：
      "85"                  → 固定电量 85%，卡片标「手动」
      "gatt:180F:2A19"      → 从指定蓝牙服务/特征读取
      ""                    → 取消绑定
    """
    t = (text or "").strip()
    if not t:
        return None, ""
    if t.isdigit():
        lv = int(t)
        if not 0 <= lv <= 100:
            return None, "电量必须是 0 到 100 之间的数字"
        return {"kind": "fixed", "level": lv}, ""
    low = t.lower()
    if low.startswith("gatt:"):
        parts = [x.strip() for x in t[5:].split(":")]
        if len(parts) == 2 and all(parts):
            return ({"kind": "gatt", "service": parts[0],
                     "characteristic": parts[1]}, "")
        return None, "格式应为 gatt:服务:特征，例如 gatt:180F:2A19"
    return None, "只能填 0-100 的数字，或 gatt:服务:特征；留空表示取消绑定"


def bt_manual_text(addr: str) -> str:
    """把已绑定的项还原成输入框里能看懂的文字。"""
    item = bt_manual_of(addr)
    if item.get("kind") == "fixed":
        return str(item.get("level", ""))
    if item.get("kind") == "gatt":
        return f"gatt:{item.get('service','')}:{item.get('characteristic','')}"
    return ""


def set_bt_manual(addr: str, item) -> None:
    all_items = bt_manual()
    key = normalize_addr(addr)
    if item:
        all_items[key] = item
    else:
        all_items.pop(key, None)
    update_config(bt_manual=all_items)


# ---------------------------------------------------------------------------
# 设备清单
# ---------------------------------------------------------------------------
# 手动档位：设备只给「低/中/高」三格、拿不到精确百分比时用。
# 数值是用户指定的估算值，界面必须标注「非实测」。
MANUAL_LEVELS = {"high": 99, "mid": 66, "low": 33}


def manual_levels() -> dict:
    v = load_config().get("manual_levels")
    return v if isinstance(v, dict) else {}


def manual_level_of(key: str) -> Optional[int]:
    try:
        v = manual_levels().get(key)
        return int(v) if v is not None else None
    except Exception:
        return None


def set_manual_level(key: str, level) -> None:
    """level=None 表示清除。只存 0–100 的整数，避免脏值进配置。"""
    items = manual_levels()
    if level is None:
        items.pop(key, None)
    else:
        try:
            items[key] = max(0, min(100, int(level)))
        except Exception:
            return
    update_config(manual_levels=items)


def device_key(vid: int, pid: int) -> str:
    return f"x{vid:04X}{pid:04X}"


def normalize_addr(addr: str) -> str:
    """蓝牙地址统一成 AA:BB:CC:DD:EE:FF（大写、冒号分隔）。"""
    raw = "".join(c for c in str(addr or "") if c in "0123456789abcdefABCDEF")
    if len(raw) != 12:
        return str(addr or "").upper()
    raw = raw.upper()
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2))


def bt_key(addr: str) -> str:
    return f"bt:{normalize_addr(addr)}"


def key_for(dev: dict) -> str:
    """一台已保存设备的键：HID 用 x{VID}{PID}，蓝牙用 bt:{MAC}。"""
    if str(dev.get("kind", "")).lower() == "bt":
        return bt_key(dev.get("addr", ""))
    return device_key(int(dev.get("vid", 0) or 0), int(dev.get("pid", 0) or 0))


def load_saved_devices() -> list:
    """读设备清单，旧配置（只有 vid/pid）继续可用。

    返回统一结构：{"kind","vid","pid","addr","name"}。
    蓝牙条目没有 vid/pid，以前会被直接丢掉，现在按 kind/addr 保留。
    """
    raw = load_config().get("devices")
    if not isinstance(raw, list):
        return []
    out = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        kind = str(d.get("kind", "")).lower()
        addr = normalize_addr(d.get("addr", ""))
        if not kind:
            kind = "bt" if (addr and not d.get("vid") and not d.get("pid")) else "hid"
        if kind == "bt":
            if not addr or len(addr) != 17:
                continue
            try:
                vid = int(d.get("vid", 0) or 0)
                pid = int(d.get("pid", 0) or 0)
            except Exception:
                vid = pid = 0
            out.append({"kind": "bt", "vid": vid, "pid": pid,
                        "addr": addr, "name": str(d.get("name", ""))})
            continue
        try:
            vid = int(d["vid"])
            pid = int(d["pid"])
        except Exception:
            continue
        out.append({"kind": "hid", "vid": vid, "pid": pid,
                    "addr": "", "name": str(d.get("name", ""))})
    return out


def save_saved_devices(devs: list) -> None:
    update_config(devices=devs)


# ---------------------------------------------------------------------------
# 上次读数
# ---------------------------------------------------------------------------
def last_store() -> dict:
    last = load_config().get("last")
    return last if isinstance(last, dict) else {}


@_atomic
def save_last(last: dict) -> None:
    update_config(last=last)


@_atomic
def forget_last(vid: int, pid: int) -> None:
    last = last_store()
    last.pop(device_key(vid, pid), None)
    update_config(last=last)


def _key_ok(key: str) -> bool:
    """设备键格式是否合法：x{8位HEX} 或 bt:{17位MAC}。"""
    key = str(key or "")
    if key.startswith("bt:"):
        return len(normalize_addr(key[3:])) == 17
    if key.startswith("x"):
        body = key[1:]
        return (len(body) == 8
                and all(c in "0123456789abcdefABCDEF" for c in body))
    return False


@_atomic
def prune_last() -> int:
    """清掉 last 里格式非法的记录（典型是 MAC 为空的 "bt:"）。

    这类是历史脏数据（删除设备时没清干净 / 早期版本写入空地址），
    会让界面凭空多出一张没名字却有电量的卡。只删格式明显不对的键，
    正常的自动发现记录（xVIDPID / bt:MAC）一条都不动。
    """
    last = last_store()
    bad = [k for k in last if not _key_ok(k)]
    if not bad:
        return 0
    for k in bad:
        last.pop(k, None)
    update_config(last=last)
    return len(bad)


# ---------------------------------------------------------------------------
# UI 设置（标题、位置、托盘提示）
# ---------------------------------------------------------------------------
def _ui() -> dict:
    ui = load_config().get("ui")
    return ui if isinstance(ui, dict) else {}


def ui_get(key: str, default=None):
    return _ui().get(key, default)


@_atomic
def ui_set(key: str, value) -> None:
    ui = _ui()
    ui[key] = value
    update_config(ui=ui)


def get_title(fallback: str = APP_NAME) -> str:
    t = str(_ui().get("title", "") or "").strip()
    return t or fallback


def set_title(title: str) -> None:
    ui_set("title", (title or "").strip())


def get_panel_pos():
    pos = _ui().get("pos")
    if (isinstance(pos, (list, tuple)) and len(pos) == 2
            and all(isinstance(v, int) for v in pos)):
        return int(pos[0]), int(pos[1])
    return None


def set_panel_pos(x: int, y: int) -> None:
    ui_set("pos", [int(x), int(y)])


def tray_hint_shown() -> bool:
    return bool(_ui().get("tray_hint_shown", False))


def set_tray_hint_shown() -> None:
    ui_set("tray_hint_shown", True)


# ---------------------------------------------------------------------------
# 设备组（同一台设备的多条链路合并成一张卡）
# ---------------------------------------------------------------------------
def load_groups() -> dict:
    raw = load_config().get("groups")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for gid, g in raw.items():
        if not isinstance(g, dict):
            continue
        members = [str(m) for m in (g.get("members") or []) if m]
        if len(members) < 2:
            continue
        prefer = str(g.get("prefer", "auto")).lower()
        if prefer not in ("auto", "2.4g", "bt"):
            prefer = "auto"
        out[str(gid)] = {"name": str(g.get("name", "") or "合并设备"),
                         "members": members, "prefer": prefer}
    return out


def save_groups(groups: dict) -> None:
    update_config(groups=groups)


def group_of(key: str) -> str:
    for gid, g in load_groups().items():
        if key in g["members"]:
            return gid
    return ""


@_atomic
def add_group(name: str, members: list) -> str:
    groups = load_groups()
    members = [m for m in dict.fromkeys(members) if m]
    # 先把手动加入的成员从旧组里摘掉，避免一台设备挂在两个组
    for gid in list(groups):
        groups[gid]["members"] = [m for m in groups[gid]["members"]
                                  if m not in members]
        if len(groups[gid]["members"]) < 2:
            groups.pop(gid)
    gid = "g%d" % (len(groups) + 1)
    while gid in groups:
        gid += "x"
    groups[gid] = {"name": name or "合并设备",
                   "members": members, "prefer": "auto"}
    save_groups(groups)
    return gid


@_atomic
def remove_group(gid: str) -> None:
    groups = load_groups()
    groups.pop(gid, None)
    save_groups(groups)


@_atomic
def set_group_name(gid: str, name: str) -> None:
    groups = load_groups()
    if gid in groups:
        groups[gid]["name"] = (name or "").strip() or groups[gid]["name"]
        save_groups(groups)


# ---------------------------------------------------------------------------
# 卡片顺序 / 背景图 / 透明度 / 颜色
# ---------------------------------------------------------------------------
def media_dir() -> str:
    return os.path.join(config_dir(), "media")


def get_card_order() -> list:
    order = _ui().get("card_order")
    return [str(k) for k in order] if isinstance(order, list) else []


def set_card_order(order: list) -> None:
    ui_set("card_order", [str(k) for k in order])


@_atomic
def prune_card_order(alive: list) -> None:
    """删设备后清掉它在新顺序里的条目。"""
    order = get_card_order()
    keep = [k for k in order if k in set(alive)]
    if keep != order:
        set_card_order(keep)


def _card_bg_map() -> dict:
    m = _ui().get("card_bg")
    return m if isinstance(m, dict) else {}


def get_card_bg(key: str) -> str:
    return str(_card_bg_map().get(key, "") or "")


def set_card_bg(key: str, filename: str) -> None:
    m = _card_bg_map()
    m[key] = filename
    ui_set("card_bg", m)


def clear_card_bg(key: str) -> None:
    m = _card_bg_map()
    m.pop(key, None)
    ui_set("card_bg", m)


def get_app_bg() -> str:
    return str(_ui().get("app_bg", "") or "")


def set_app_bg(filename: str) -> None:
    ui_set("app_bg", filename)


OPACITY_KEY = "opacity_v2"


@_atomic
def _migrate_opacity() -> None:
    """把旧语义（100 = 不透明）翻成新语义（100 = 全透明）。

    只做一次，用 ui.opacity_v2 标记。不迁移的话，老用户升级后
    card=100 会被当成「完全透明」，卡片直接消失。
    """
    ui = _ui()
    if ui.get(OPACITY_KEY):
        return
    op = ui.get("opacity") if isinstance(ui.get("opacity"), dict) else {}

    def flip(key: str) -> int:
        try:
            return 100 - max(0, min(100, int(op.get(key, 100))))
        except Exception:
            return 0

    ui["opacity"] = {"card": flip("card"), "app": flip("app"),
                     "menu": flip("menu")}
    ui[OPACITY_KEY] = True
    update_config(ui=ui)


def get_opacity() -> dict:
    """两档**透明度**（不是不透明度）：卡片背景 / 软件背景。

    0 = 完全不透明，100 = 完全透明。旧配置第一次读会被 _migrate_opacity() 取反。
    `menu` 键保留仅为旧配置兼容（迁移代码在写它），已没有任何 UI 读取它。
    """
    _migrate_opacity()
    v = _ui().get("opacity")
    if not isinstance(v, dict):
        v = {}

    def _one(key: str) -> int:
        try:
            return max(0, min(100, int(v.get(key, 0))))
        except Exception:
            return 0

    return {"card": _one("card"), "app": _one("app"), "menu": _one("menu")}


def get_alpha(key: str) -> float:
    """透明度百分比 → 叠色用的 alpha（1 = 完全实心）。"""
    return 1.0 - get_opacity().get(key, 0) / 100.0


@_atomic
def set_opacity(card: int, app: int, menu: int = None) -> None:
    """写入的是**透明度**；menu 省略时保留原值。"""
    if menu is None:                     # 兼容旧调用：不动菜单那一档
        menu = get_opacity()["menu"]
    ui = _ui()
    ui["opacity"] = {"card": int(card), "app": int(app), "menu": int(menu)}
    ui[OPACITY_KEY] = True
    update_config(ui=ui)


def get_colors(dark: bool = False) -> dict:
    key = "colors_dark" if dark else "colors_light"
    v = _ui().get(key)
    return v if isinstance(v, dict) else {}


# ---------------------------------------------------------------------------
# 窗口尺寸（§3.9）
# ---------------------------------------------------------------------------
PANEL_MIN = (620, 440)          # 物理像素
PANEL_MAX = (1200, 900)


def get_panel_size():
    """上次拖出来的窗口尺寸；没记录/脏数据返回 None。"""
    v = _ui().get("panel_size")
    try:
        w, h = int(v[0]), int(v[1])
    except Exception:
        return None
    return (max(PANEL_MIN[0], min(PANEL_MAX[0], w)),
            max(PANEL_MIN[1], min(PANEL_MAX[1], h)))


def set_panel_size(w: int, h: int) -> None:
    ui_set("panel_size", [int(w), int(h)])


# ---------------------------------------------------------------------------
# 字体缩放（§3.8）
# ---------------------------------------------------------------------------
FONT_MIN, FONT_MAX = 0.85, 1.25


def get_font_scale() -> float:
    try:
        v = float(_ui().get("font_scale", 1.0))
    except Exception:
        v = 1.0
    return max(FONT_MIN, min(FONT_MAX, v))


def set_font_scale(v) -> float:
    try:
        v = float(v)
    except Exception:
        v = 1.0
    v = max(FONT_MIN, min(FONT_MAX, v))
    ui_set("font_scale", round(v, 3))
    return v


def set_colors(data: dict, dark: bool = False) -> None:
    ui_set("colors_dark" if dark else "colors_light", data)


# ---------------------------------------------------------------------------
# 每设备电池色（§3.3）：设备身份色，与深/浅主题无关
# ---------------------------------------------------------------------------
def get_device_colors() -> dict:
    v = _ui().get("device_colors")
    return v if isinstance(v, dict) else {}


def get_device_color(key: str):
    """某台设备的电池色；没设返回 None（= 用全局/按电量）。"""
    rgbv = get_device_colors().get(key)
    try:
        r, g, b = [max(0, min(255, int(x))) for x in rgbv]
        return (r, g, b)
    except Exception:
        return None


@_atomic
def set_device_color(key: str, rgbv) -> None:
    try:
        vals = [max(0, min(255, int(x))) for x in rgbv]
    except Exception:
        return                          # 脏数据直接忽略，别把配置写坏
    if len(vals) < 3:
        return
    m = get_device_colors()
    m[key] = vals[:3]
    ui_set("device_colors", m)


@_atomic
def clear_device_color(key: str) -> None:
    m = get_device_colors()
    if m.pop(key, None) is not None:
        ui_set("device_colors", m)


# ---------------------------------------------------------------------------
# 昵称位置（§3.10）：逻辑像素偏移，相对默认位置（卡片水平中心, y1+158）
# ---------------------------------------------------------------------------
def get_name_pos(key: str):
    v = _ui().get("name_pos")
    if isinstance(v, dict):
        p = v.get(key)
        if isinstance(p, (list, tuple)) and len(p) == 2:
            try:
                return float(p[0]), float(p[1])
            except Exception:
                pass
    return 0.0, 0.0


@_atomic
def set_name_pos(key: str, dx: float, dy: float) -> None:
    m = _ui().get("name_pos")
    m = m if isinstance(m, dict) else {}
    m[key] = [round(float(dx), 1), round(float(dy), 1)]
    ui_set("name_pos", m)


def clear_name_pos(key: str) -> None:
    m = _ui().get("name_pos")
    if isinstance(m, dict) and m.pop(key, None) is not None:
        ui_set("name_pos", m)


def safe_key(key: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in key)


# ---------------------------------------------------------------------------
# 编号自定义：卡片上的「蓝牙地址 / 2.4G 设备号」显示文本与位置
# 只影响显示，不写回设备列表；逻辑像素偏移，相对默认位置 (cx, y1+180)
# ---------------------------------------------------------------------------
def custom_ids() -> dict:
    ids = load_config().get("ids")
    return ids if isinstance(ids, dict) else {}


@_atomic
def set_custom_id(key: str, text: str) -> None:
    ids = custom_ids()
    text = (text or "").strip()
    if text:
        ids[key] = text
    else:
        ids.pop(key, None)
    update_config(ids=ids)


@_atomic
def clear_custom_id(key: str) -> None:
    ids = custom_ids()
    if ids.pop(key, None) is not None:
        update_config(ids=ids)


def get_id_pos(key: str):
    v = _ui().get("id_pos")
    if isinstance(v, dict):
        p = v.get(key)
        if isinstance(p, (list, tuple)) and len(p) == 2:
            try:
                return float(p[0]), float(p[1])
            except Exception:
                pass
    return 0.0, 0.0


@_atomic
def set_id_pos(key: str, dx: float, dy: float) -> None:
    m = _ui().get("id_pos")
    m = m if isinstance(m, dict) else {}
    m[key] = [round(float(dx), 1), round(float(dy), 1)]
    ui_set("id_pos", m)


def clear_id_pos(key: str) -> None:
    m = _ui().get("id_pos")
    if isinstance(m, dict) and m.pop(key, None) is not None:
        ui_set("id_pos", m)


@_atomic
def reset_text_positions() -> None:
    """「恢复默认位置」：编号与昵称偏移一起清空，各自回默认锚点。"""
    ui = _ui()
    ui["name_pos"] = {}
    ui["id_pos"] = {}
    update_config(ui=ui)
