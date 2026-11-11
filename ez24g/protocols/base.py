# -*- coding: utf-8 -*-
"""协议插件基类与注册表。

=== 加一个新设备协议，只需要三步 ===

1. 在本目录新建 `mydevice.py`；
2. 写一个类继承 `DeviceProtocol`，加上 `@register`：

       from ..hidapi import HidIface
       from ..model import Reading
       from .base import DeviceProtocol, register

       @register
       class MyProtocol(DeviceProtocol):
           id = "mydevice"
           name = "某某鼠标"
           priority = 40              # 越小越先试

           def match(self, iface):
               return iface.vid == 0x1234 and iface.pid == 0x5678

           def read(self, iface):
               with HidDevice(iface.path, write=True) as dev:
                   ...
                   return Reading(ok=True, level=lv, source=self.name)

3. 重启程序。`protocols/__init__.py` 会自动 import 本目录所有模块，
   不需要改任何别的文件。

=== 约定 ===
- `read()` 失败返回 None 或 Reading(ok=False)，不要抛异常（异常也会被兜住）。
- `match()` 尽量写窄（VID/PID 或 UsagePage），否则会拖慢每次刷新。
- 只想给自己用、不想自动试探的协议，设 `auto = False`，
  之后用 `--vendor` 或配置 `"vendor_probe": true` 打开。
"""
from __future__ import annotations

import importlib
import pkgutil
import time
from typing import Optional

from ..hidapi import HidIface
from ..model import Reading


class DeviceProtocol:
    """一个设备读取策略。子类至少实现 read()。"""

    id: str = "base"
    name: str = "未命名协议"
    priority: int = 100
    auto: bool = True
    # 同一台设备有长短两种报文接口时，先试长报文（output_len 大的那条）。
    # HID++ 2.0 只用长报文，先打短接口会白等一个超时，把预算耗光。
    long_report_first: bool = False

    def match(self, iface: HidIface) -> bool:
        return False

    def read(self, iface: HidIface) -> Optional[Reading]:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{self.id} {self.name} p={self.priority}>"


_REGISTRY: list[DeviceProtocol] = []
_LOADED = False


def register(cls):
    """类装饰器：实例化并放进注册表（按 priority 排序）。"""
    inst = cls()
    _REGISTRY.append(inst)
    _REGISTRY.sort(key=lambda p: p.priority)
    return cls


def load_plugins() -> list[DeviceProtocol]:
    """import 本包下所有模块，触发 @register。可重复调用。"""
    global _LOADED
    if _LOADED:
        return _REGISTRY
    import ez24g.protocols as pkg
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_") or info.name == "base":
            continue
        importlib.import_module(f"{pkg.__name__}.{info.name}")
    _LOADED = True
    return _REGISTRY


def registry(include_manual: bool = True) -> list[DeviceProtocol]:
    items = load_plugins()
    if include_manual:
        return list(items)
    return [p for p in items if p.auto]


# 失败退避：某台设备的某个协议连续 3 轮读不到，就 30 秒内不再试它。
# 读不到的设备（耳机没开机、接收器上没设备）每轮都要白等一个超时，
# 三台这样的设备就能把一轮刷新拖到十几秒 —— 面板看起来像卡死了。
_FAILS: dict[tuple[str, int, int], int] = {}
_SKIP_UNTIL: dict[tuple[str, int, int], float] = {}
_FAIL_LIMIT = 3
_SKIP_SECONDS = 30.0

# 轮次编号：协议内部的「本轮预算」靠它判断是不是同一轮刷新。
# 用时间窗口猜会出错——连着点两次刷新，第二次会拿到上一个已经用完的预算。
_ROUND_ID = 0


def begin_round() -> int:
    """上层每开始一轮刷新调一次；协议内部的探测预算据此重置。"""
    global _ROUND_ID
    _ROUND_ID += 1
    return _ROUND_ID


def round_id() -> int:
    return _ROUND_ID


def _backed_off(pid_: str, vid: int, pid: int) -> bool:
    return _SKIP_UNTIL.get((pid_, vid, pid), 0.0) > time.monotonic()


def _note_ok(pid_: str, vid: int, pid: int) -> None:
    key = (pid_, vid, pid)
    _FAILS[key] = 0
    _SKIP_UNTIL.pop(key, None)


def _note_fail(pid_: str, vid: int, pid: int) -> None:
    key = (pid_, vid, pid)
    n = _FAILS.get(key, 0) + 1
    _FAILS[key] = n
    if n >= _FAIL_LIMIT:
        _FAILS[key] = 0
        _SKIP_UNTIL[key] = time.monotonic() + _SKIP_SECONDS


def reset_backoff() -> None:
    """清空退避记忆（设备插拔 / 手动刷新后想立刻重试时用）。"""
    _FAILS.clear()
    _SKIP_UNTIL.clear()


def read_best(ifaces: list[HidIface], *,
              include_manual: bool = False,
              prefer_id: str | None = None) -> Optional[Reading]:
    """按优先级逐个协议尝试，返回第一个成功的 Reading。

    这就是唯一的"怎么读一台设备"的入口，UI 和 CLI 都走这里。
    prefer_id 用来把上次成功的厂商协议提到最前面，避免每次都全量探测。
    """
    items = registry(include_manual=include_manual)
    if not include_manual:
        # 手动协议默认不跑，但「上次就是它读到的那个」要保留
        items = [p for p in items if p.auto or p.id == prefer_id]
    if prefer_id:
        items = ([p for p in items if p.id == prefer_id]
                 + [p for p in items if p.id != prefer_id])
    # C：设备元数据（厂商名/产品名）命中的协议先试，减少无关试探
    hints = {getattr(i, "vendor_hint", "") for i in ifaces}
    hints.discard("")
    if hints:
        items = ([p for p in items if p.id in hints]
                 + [p for p in items if p.id not in hints])
    for proto in items:
        cand = ifaces
        if proto.long_report_first:
            cand = sorted(ifaces,
                          key=lambda d: -(getattr(d, "output_len", 0) or 0))
        tried = False
        for iface in cand:
            try:
                if not proto.match(iface):
                    continue
                if (_backed_off(proto.id, iface.vid, iface.pid)
                        and not (prefer_id and proto.id == prefer_id)):
                    continue           # 连续读不到，先放着，30 秒后再试
                                       # 已学会的专属读法例外：每轮都轻轻试一次
                tried = True
                r = proto.read(iface)
            except Exception:
                continue
            if r is not None and r.ok and r.level is not None:
                if not r.source:
                    r.source = proto.name
                r.protocol_id = proto.id      # type: ignore[attr-defined]
                _note_ok(proto.id, iface.vid, iface.pid)
                return r
        if tried:
            vid = cand[0].vid if cand else 0
            pid = cand[0].pid if cand else 0
            _note_fail(proto.id, vid, pid)
    return None


def describe() -> str:
    lines = ["id                  priority  auto  name"]
    for p in registry():
        lines.append(f"{p.id:18s}  {p.priority:>8d}  "
                     f"{'yes' if p.auto else 'no ':4s}  {p.name}")
    return "\n".join(lines)
