# -*- coding: utf-8 -*-
"""应用编排：线程、刷新、热插拔、命令行入口。

线程模型（很简单，别搞复杂）：
    主线程     Tk 事件循环，只画界面
    工作线程   每次刷新起一个，枚举 HID + 读设备，结果写进 _readings 快照
    托盘线程   Win32 消息循环，跨线程只用 PostMessage
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time

from . import config, devices, providers
from .hidapi import enumerate_hid
from .model import Reading
from .protocols import describe
from .syswin import (install_desktop_links, install_startup, is_light_theme,
                     migrate_startup_link, startup_lnk)
from .win32 import (ERROR_ALREADY_EXISTS, WM_APP_INTERVAL, WM_APP_REFRESH,
                    WM_APP_SHOW, WM_CLOSE, kernel32, user32, wt)

UI_SCALE = 1.0

USAGE = """星火 Monitor

用法：
  python my_devices.py                托盘常驻
  python my_devices.py --once         读一次并打印
  python my_devices.py --list         列出所有 HID 接口
  python my_devices.py --scan         列出厂商设备 / 标准 HID 电量设备 / 蓝牙设备
  python my_devices.py --protocols    列出已注册的协议插件
  python my_devices.py --panel        只开面板，不常驻托盘
  python my_devices.py --install      创建开机自启 + 桌面入口
  python my_devices.py --install-deps 自动装可选依赖（Pillow / winrt）
  python my_devices.py --doctor       打印依赖与环境自检
  python my_devices.py --vendor       额外尝试厂商私有协议（可能写设备）

配置文件：%APPDATA%\\SparkMonitor\\config.json
"""


class TrayApp:
    def __init__(self, panel_only: bool = False):
        self.panel_only = panel_only
        self.theme_light = is_light_theme()
        self.interval = config.get_interval()
        self.ui_scale = UI_SCALE
        self.devices = config.load_saved_devices()
        try:
            config.prune_last()      # 清掉 MAC 为空的脏记录，别让它冒充一张卡
        except Exception:
            pass
        self.tray = None
        self.panel = None
        self.root = None

        self._readings: dict[str, Reading] = {}
        self._lock = threading.Lock()
        self._refreshing = False
        self._queued = False       # 刷新中又被点了一次 → 本轮结束后补跑
        self._ever = False         # 是否至少发布过一次（区分"还在读"和"真的没设备"）
        self._stop = False
        self._quitting = False
        self._dirty = False
        self._show_requested = False
        self._full: dict[str, bool] = {}
        self._seen = None
        self._asked: set = set()
        self._pending_add = None
        self._pending_add_result = None
        self._last_poll = 0.0
        self._hotplug_requested = False
        self._menu_requested = False
        self._deps_changed = False     # 后台装完依赖 → 由 tick 回主线程收尾
        self._busy_shown = False       # 上一次画到界面上的「读取中」状态

    # -- 数据 -------------------------------------------------------------
    def snapshot(self) -> dict[str, Reading]:
        with self._lock:
            return dict(self._readings)

    def refresh(self) -> bool:
        """起一轮刷新。已经有一轮在跑时记一次「排队」，本轮结束立刻补跑。

        返回 True = 这一轮由本次调用启动；False = 上一轮还没结束（已排队）。
        以前这里直接 return，面板上点「立即刷新」等于没接线。
        """
        with self._lock:
            if self._refreshing:
                self._queued = True
                return False
            self._refreshing = True
        threading.Thread(target=self._worker, daemon=True).start()
        return True

    @property
    def busy(self) -> bool:
        """有没有一轮刷新正在跑（按钮/空态用）。"""
        with self._lock:
            return self._refreshing

    @property
    def loaded(self) -> bool:
        """是否至少完成过一次发布。False 表示首轮还没回来，界面该写「正在读取…」。"""
        return self._ever

    def _worker(self):
        """三段式刷新，每段结束都发布一次，界面先快后全：

            ① 标准 HID（几十毫秒）→ 2.4G / 蓝牙 HID 卡片先出来
            ② 厂商协议（可能几秒，C547 / 杰理这类要发报文等回复）
            ③ 蓝牙 GATT（慢，几台并发）

        以前只分「HID / 蓝牙」两段，厂商探测混在第一段里，于是启动时
        界面上什么都没有，用户以为「没设备」，其实在读。
        """
        try:
            ifaces = enumerate_hid()
            auto = config.vendor_probe_enabled()
            # 一轮只数一遍：HID 接口清单在这里算好，第一、二段直接复用。
            # 蓝牙枚举（约 94ms）仍留到第三段做，首屏出卡时间不受影响。
            hid_refs = providers.enumerate_refs(ifaces, include_bt=False)
            res = self._finish(
                devices.read_hid(ifaces, include_manual=False,
                                 saved=self.devices, refs=hid_refs))
            self._publish(devices.order_readings(res, self.devices))
            if auto:
                try:
                    full = self._finish(
                        devices.read_hid(ifaces, include_manual=True,
                                         saved=self.devices, refs=hid_refs))
                except Exception:
                    full = None
                if full:
                    res = full
                    self._publish(devices.order_readings(res, self.devices))
            try:
                # refs 带上本轮已算好的 HID 清单，read_bt 内部不用再数一遍；
                # 蓝牙 HID 联动也复用它，不再每台设备各枚举一次。
                bt = devices.read_bt(
                    include_manual=auto,
                    refs=providers.enumerate_refs(ifaces, include_bt=True),
                    saved=self.devices)
            except Exception:
                bt = {}
            if bt:
                res.update(devices.apply_last_known(bt))
                self._publish(devices.order_readings(res, self.devices))
        except Exception:
            pass
        finally:
            # 无论如何都要解锁，否则一次异常就再也不刷新了
            with self._lock:
                self._refreshing = False
                again = self._queued
                self._queued = False
            if again:
                self.refresh()       # 刷新期间被点过：立刻补一轮
        self._notify_tray()

    @staticmethod
    def _finish(res: dict[str, Reading]) -> dict[str, Reading]:
        """一轮结果的统一收尾，顺序不能换：

            ① apply_xmos_levels   设备自己上报的真实值（音频通道，毫秒级）
            ② apply_manual_levels 用户手选的档位，只在①没读到、设备还在时兜底
            ③ apply_last_known    前面都没有，才沿用上次成功读数并标「上次更新」

        以前这里只调了 ③，于是「手动档位」在界面上其实一直没生效——
        它只挂在 read_all() 上，而软件的主刷新走的是三段式，不经过 read_all。
        顺手在这里补齐，顺手也把觅声那条通道接上。
        """
        return devices.apply_last_known(
            devices.apply_manual_levels(
                devices.apply_xmos_levels(res)))

    def _notify_tray(self) -> None:
        """让托盘按当前快照重画图标。

        `_publish`（读到新数据）和 `reorder_now`（拖动排序）都必须调：
        托盘那几圈同心的顺序完全来自快照顺序，不通知它就只能等下一轮
        刷新（默认 60 秒），表现就是「拖完卡片，右下角图标还是旧顺序」。
        """
        if self.tray and self.tray.hwnd:
            user32.PostMessageW(self.tray.hwnd, WM_APP_REFRESH, 0, 0)

    def _publish(self, res: dict[str, Reading]):
        with self._lock:
            self._readings = res
        self._ever = True
        self._check_full(res)
        self._dirty = True
        self._notify_tray()

    def reorder_now(self):
        """按 ui.card_order 重排内存快照，不重读设备。

        拖动排序写完配置必须立刻调一次，否则面板按旧快照重画 → 松手就复位，
        要等下一次 _worker 刷新（默认 60s）才生效。托盘图标同理：
        它画的是快照顺序，所以这里也要通知它，不能只让面板重画。
        """
        with self._lock:
            if not self._readings:
                return
            self._readings = devices.order_readings(self._readings,
                                                    self.devices)
        self._dirty = True
        self._notify_tray()

    def _check_full(self, res):
        """电量首次到 100% 弹一次通知；掉到 97% 以下重新布防。"""
        for key, r in res.items():
            if not r.ok or r.level is None or r.stale:
                continue
            if r.level >= 100:
                if not self._full.get(key):
                    self._full[key] = True
                    if self.tray:
                        try:
                            self.tray.notify(f"{r.name} 已经充满电啦")
                        except Exception:
                            pass
            elif r.level < 97:
                self._full[key] = False

    # -- 交互 -------------------------------------------------------------
    def request_show(self):
        self._show_requested = True

    def set_interval(self, sec) -> int:
        sec = config.set_interval(sec)
        if sec == self.interval:
            return sec
        self.interval = sec
        self._dirty = True
        if self.tray and self.tray.hwnd:
            user32.PostMessageW(self.tray.hwnd, WM_APP_INTERVAL, 0, 0)
        return sec

    def on_theme_changed(self):
        self.theme_light = is_light_theme()
        self._dirty = True
        self._notify_tray()

    # -- 热插拔 -----------------------------------------------------------
    def _poll_hotplug(self, force: bool = False):
        now = time.time()
        if not force and now - self._last_poll < 10.0:
            return
        self._last_poll = now
        try:
            present = {(d.vid, d.pid) for d in enumerate_hid()}
        except Exception:
            return
        if self._seen is None:
            self._seen = present
            self._asked = set(present)
            return
        new = present - self._seen
        self._seen = present
        self._asked &= present
        for vid, pid in sorted(new):
            if (vid, pid) in self._asked:
                continue
            if any(d["vid"] == vid and d["pid"] == pid for d in self.devices):
                continue
            if config.device_key(vid, pid) in config.ignored_devices():
                continue             # 删除过的设备只从「+ 添加设备」手动加回
            self._asked.add((vid, pid))
            self._pending_add = (vid, pid)
            break

    def _show_add(self, vid: int, pid: int):
        """热插拔发现新设备后的「是否添加」弹窗。

        读设备是最慢的一步（单协议 HID 读超时 800ms，多协议串起来 1–2 秒），
        以前直接跑在 Tk 主线程上，插上接收器的瞬间窗口会假死。
        现在读取放工作线程，读到的结果用 root.after 回主线程弹窗（红线 #1）。
        """
        if self.root is None:
            return

        def work():
            name = f"{vid:04X}:{pid:04X}"
            level = None
            try:
                ifaces = enumerate_hid()
                r = devices.read_device({"vid": vid, "pid": pid, "name": ""},
                                        ifaces)
                name = r.name or name
                if r.ok and r.level is not None:
                    level = r.level
            except Exception:
                pass
            # 跨线程只写字段，由主线程 tick() 取走弹窗（红线 #4：子线程不碰 Tk）
            self._pending_add_result = (vid, pid, name, level)

        threading.Thread(target=work, daemon=True,
                         name="hotplug-probe").start()

    def _ask_add(self, vid: int, pid: int, name: str, level=None):
        """主线程：弹窗确认是否添加（只在拿到读取结果后调用）。

        注意：`messagebox` 以前没有导入，旧代码靠外层 `except Exception`
        把 NameError 吞掉了 —— 这个「发现新设备」弹窗其实从来没弹出来过。
        """
        from tkinter import messagebox
        # H：当场读到电量、且没有被删除过 → 直接加入，不再弹窗打断
        if level is not None and config.device_key(vid, pid) not in config.ignored_devices():
            self.add_device(vid, pid, name)
            return
        msg = f"设备：{name}\nVID:PID：{vid:04X}:{pid:04X}"
        if level is not None:
            msg += f"\n当前电量：{level}%"
        msg += "\n\n添加设备后，双击设备名可以改名\n\n是否添加？"
        try:
            if messagebox.askyesno("发现新设备", msg, parent=self.panel.root):
                self.add_device(vid, pid, name)
        except Exception:
            pass

    # -- 设备增删改 -------------------------------------------------------
    def rename_device(self, key: str, name: str):
        devices.rename_device(key, name)
        for d in self.devices:
            if config.key_for(d) == key:
                d["name"] = name
                break
        config.save_saved_devices(self.devices)
        # 立刻改内存里的名字，不用等下一次刷新
        with self._lock:
            r = self._readings.get(key)
            if r is not None:
                r.name = name
        self._dirty = True

    def add_device(self, vid: int, pid: int, name: str):
        if not devices.add_device(vid, pid, name):
            return
        self.devices = config.load_saved_devices()
        self.refresh()

    def add_device_by_key(self, key: str, r: Reading):
        """面板「+ 添加设备」用：把已发现但没保存的设备写进配置。"""
        config.unignore_device(key)      # 用户手动选回来 → 解除忽略
        if r.transport == "bt" and r.addr:
            ok = devices.add_device(0, 0, r.name, kind="bt", addr=r.addr)
        else:
            ok = devices.add_device(r.vid, r.pid, r.name)
        if ok:
            self.devices = config.load_saved_devices()
            self.refresh()

    def request_device_menu(self):
        self._menu_requested = True

    def _maybe_tray_hint(self):
        """首次运行提示一次：托盘图标可能被收进 ^ 里。"""
        if config.tray_hint_shown():
            return
        config.set_tray_hint_shown()
        from tkinter import messagebox
        try:
            yes = messagebox.askyesno(
                "托盘图标",
                "Windows 可能会把托盘图标收进 ^ 里。\n\n"
                "想让它在任务栏一直可见：设置 → 个性化 → 任务栏 → "
                "「其他系统托盘图标」，把「星火 Monitor」打开。\n\n现在打开设置页吗？",
                parent=self.panel.root)
            if yes:
                from .ui.panel import open_tray_settings
                open_tray_settings()
        except Exception:
            pass

    def remove_device(self, key: str):
        devices.remove_device(key)
        self.devices = config.load_saved_devices()
        with self._lock:
            self._readings.pop(key, None)
        self.refresh()

    # -- 生命周期 ---------------------------------------------------------
    def quit(self):
        # hide() 在 panel_only 模式下会回调 quit()，必须防重入，
        # 否则 隐藏 → 退出 → 隐藏 → … 递归到 RecursionError
        if self._quitting:
            return
        self._quitting = True
        self._stop = True
        if self.tray and self.tray.hwnd:
            user32.PostMessageW(self.tray.hwnd, WM_CLOSE, 0, 0)
        if self.panel:
            try:
                self.panel.hide()
            except Exception:
                pass
        try:
            self.root.quit()
        except Exception:
            pass

    # -- 可选依赖 ---------------------------------------------------------
    def _needs_deps(self) -> list:
        """启动时该自动补装哪些可选依赖。

        只在「用户真的用到了」才装，避免纯 2.4G 用户白等一次联网：
          * 配过背景图 / 背景透明度 > 0 → 需要 Pillow；
          * 配过蓝牙设备 → 需要 winrt（读 BLE GATT 电量）。
        """
        from . import deps
        need = []
        try:
            if not deps.have("Pillow"):
                ui = config.load_config().get("ui") or {}
                card_bg = ui.get("card_bg") or {}
                if (config.get_app_bg() or any(card_bg.values())
                        or config.get_opacity()["app"] > 0
                        or config.get_opacity()["card"] > 0):
                    need.append("Pillow")
            if not deps.have("winrt"):
                if any(str(d.get("kind", "")).lower() == "bt"
                       for d in self.devices):
                    need.append("winrt")
        except Exception:
            pass
        return need

    def _auto_install_deps(self):
        """后台补装缺失的可选依赖；装完置标志，让 tick 在主线程收尾。"""
        from . import deps
        need = self._needs_deps()
        if not need:
            return

        def done(ok):
            if ok:
                self._deps_changed = True      # 只置标志，Tk 只在主线程碰

        deps.install_async(need, done=done)

    def _on_deps_ready(self):
        from . import providers
        from .ui import customize
        try:
            customize.reload()
        except Exception:
            pass
        try:
            providers.reset_bt()
        except Exception:
            pass
        self.panel._img_cache.clear()
        self._dirty = True
        self.refresh()

    def run(self):
        from .ui.panel import Panel
        from .ui.tray import Tray

        migrate_startup_link()
        self.panel = Panel(self)
        self.root = self.panel.root
        if not self.panel_only:
            self.tray = Tray(self)
            self.tray.start()
        self.refresh()

        def tick():
            if self._deps_changed:
                self._deps_changed = False
                self._on_deps_ready()
            if self._hotplug_requested:
                self._hotplug_requested = False
                self._poll_hotplug(force=True)
            else:
                self._poll_hotplug()
            if self._pending_add:
                vid, pid = self._pending_add
                self._pending_add = None
                self._show_add(vid, pid)
            if self._pending_add_result:
                # 工作线程读完设备后的结果：在主线程弹「是否添加」
                vid, pid, name, level = self._pending_add_result
                self._pending_add_result = None
                self._ask_add(vid, pid, name, level)
            if self._show_requested:
                self._show_requested = False
                self.panel.show()
            if self._menu_requested:
                self._menu_requested = False
                try:
                    self.panel.popup_devices()
                except Exception:
                    pass
            if self._dirty and self.root.winfo_viewable():
                # 先判可见再清标志：不可见时保留脏标志，下次显示立刻补画
                self._dirty = False
                self.panel.render()
            busy = self.busy
            if busy != self._busy_shown:
                self._busy_shown = busy
                if self.root.winfo_viewable():
                    self.panel.render()     # 按钮「立即刷新 ↔ 读取中…」跟着变
            if self._stop:
                self.root.quit()
                return
            self.root.after(250, tick)

        self.root.after(250, tick)
        # 完整模式也直接显示面板：双击 启动.bat 就能看到窗口
        self.panel.show()
        self._auto_install_deps()          # 缺 Pillow / winrt 时后台补装
        if not self.panel_only:
            self.root.after(1200, self._maybe_tray_hint)
        self.root.mainloop()
        try:
            if self.tray:
                self.tray.destroy()
        except Exception:
            pass
        import os
        os._exit(0)


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------
def print_once(vendor: bool = False) -> int:
    """读一次已添加的设备并打印（和界面看到的完全一致）。"""
    devs = config.load_saved_devices()
    if not devs:
        print("还没有添加设备。")
        print("  python my_devices.py --scan    看看现在能识别哪些设备")
        print("  或直接启动程序，插入设备后在弹窗里点「添加」")
        return 0
    res = devices.read_all(include_manual=vendor)
    for key, r in res.items():
        tag = f"[{r.transport_text}]"
        if r.ok and r.level is not None:
            suffix = (f"  ← 上次更新 {r.detail}" if r.stale
                      else f"  [{r.source or '未知来源'}]")
            print(f"{r.name or key}：{r.level}%  {tag}{suffix}")
        else:
            print(f"{r.name or key}：{r.detail or r.text}  {tag}")
    return 0


def list_hid() -> int:
    for d in enumerate_hid():
        print(d.label, "|", d.manufacturer, "|", d.product, "|", d.instance)
    return 0


def _print_rows(rows: dict[str, Reading], saved: set[str],
                prefix: str = "") -> None:
    for key, r in sorted(rows.items()):
        mark = "已添加" if key in saved else "未添加"
        tag = r.transport_text
        if r.ok and r.level is not None:
            state = f"{r.level}%  [{r.source}]"
        else:
            state = r.detail or r.text
        print(f"{prefix}{key}  {r.name:26s} {tag}  {mark}  {state}")


def scan_vendor(vendor: bool = False) -> int:
    """列出所有能读到电量的设备：厂商 HID / 标准 HID / 蓝牙 BLE。

    厂商协议跟着「设置 → 自动识别」走（默认开），否则 --scan 的输出会和
    面板对不上：面板读得到、--scan 说「协议未知」，等于没法用来定位问题。
    """
    ifaces = enumerate_hid()
    saved = {config.key_for(d) for d in config.load_saved_devices()}
    refs = providers.enumerate_refs(ifaces)
    hid_refs = [r for r in refs if r.kind != "bt"]
    bt_refs = [r for r in refs if r.kind == "bt"]
    auto = vendor or config.vendor_probe_enabled()

    print(f"== HID（2.4G 接收器 / 标准 HID 电量）  {len(hid_refs)} 台"
          f"　自动识别：{'开' if auto else '关'}")
    if not ifaces:
        print("没有枚举到任何 HID 接口。")
    else:
        found = devices.discover_extras(ifaces, skip_keys=set(),
                                        include_manual=auto, refs=hid_refs)
        if not found:
            print("没有可读的 HID 设备（HIDI2C / ACPI 已跳过）。")
        _print_rows(found, saved)

    print()
    print(f"== 蓝牙（HFP 系统属性 / BLE GATT 0x180F-0x2A19）  {len(bt_refs)} 台")
    if not providers.bt_available():
        # WinRT 不可用只影响 BLE GATT 读数；PnP / HFP 两条路照样能识别与读数，
        # 所以这里只提示、不再 return（以前直接把下面整段列表吞掉了）。
        print(f"WinRT 不可用：{providers.bt_hint()}")
        print("  仍可用：经典蓝牙（HFP 系统属性）+ 蓝牙 PnP 节点电量")
        print("  想读 BLE GATT 电量可执行：python my_devices.py --install-deps winrt")
    if not bt_refs:
        print("Windows 里没有已配对 / 在线的蓝牙设备。")
    else:
        rows: dict[str, Reading] = {}
        for ref in bt_refs:
            r = providers.read_ref(ref)
            if r is None:
                r = Reading(ok=False, present=ref.connected,
                            status="unknown_proto", detail="协议未知",
                            name=ref.name, transport="bt", addr=ref.addr)
            r.name = devices.display_name_bt(ref.addr, ref.name)
            r.transport = "bt"
            rows[ref.key] = r
        _print_rows(rows, saved)
        lost = [d for d in config.load_saved_devices()
                if str(d.get("kind", "")) == "bt"
                and config.key_for(d) not in rows]
        for d in lost:
            key = config.key_for(d)
            print(f"{key}  {(d.get('name') or '蓝牙设备'):26s} 蓝牙  "
                  f"{'已添加' if key in saved else '未添加'}  未连接")
    return 0


def print_protocols() -> int:
    print(describe())
    print("\n带 auto=no 的协议默认不跑（可能给设备发写请求）；"
          "用 --vendor 或配置 \"vendor_probe\": true 打开。")
    return 0


def install_deps(names: list) -> int:
    """--install-deps [Pillow] [winrt]：把可选依赖装进用户私有目录。"""
    from . import deps
    targets = [n for n in names if n in deps.PACKAGES] or list(deps.PACKAGES)
    empty = [n for n in targets if deps.have(n)]
    if empty:
        print(f"已安装，无需处理：{', '.join(empty)}")
    todo = [n for n in targets if n not in empty]
    if not todo:
        return 0
    print(f"安装目录：{deps.vendor_dir()}")
    print(f"解释器：{sys.executable}")

    def progress(stage, msg):
        print(f"  [{stage}] {msg}")

    ok = deps.ensure(todo, progress)
    if ok:
        print("完成：", ", ".join(todo))
        print("（Pillow 已装好，背景图 / 透明度可直接使用）")
        return 0
    print(f"失败：{deps.last_error()}")
    print(f"日志：{deps.log_path()}")
    return 1


def doctor() -> int:
    """--doctor：依赖 / 解释器 / 蓝牙 / 渲染环境的一页自检。"""
    from . import deps
    from .providers import bt_pnp
    st = deps.status()
    print("== 环境 ==")
    print(f"Python      : {sys.version.split()[0]}  ({st['python']})")
    print(f"打包版      : {st['frozen']}")
    print(f"依赖目录    : {st['vendor']}")
    print(f"离线轮子    : {st['wheels'] or '（无，需要联网）'}")
    print(f"pip 可用    : {st['pip']}")
    print(f"Pillow      : {'有' if st['Pillow'] else '无'}")
    print(f"winrt       : {'有' if st['winrt'] else '无'}")
    if st["error"]:
        print(f"最近错误    : {st['error']}")
    print("\n== 蓝牙 ==")
    print(f"WinRT       : {providers.bt_hint()}")
    try:
        nodes = bt_pnp.enumerate_paired(fresh=True)
    except Exception as e:                       # noqa: BLE001
        nodes = {}
        print(f"PnP 枚举失败：{e}")
    print(f"PnP 节点    : {len(nodes)} 台")
    for key, item in sorted(nodes.items()):
        lv = item.get("level")
        print(f"  {key}  {item.get('name', ''):24s} "
              f"{'电量 ' + str(lv) + '%' if lv is not None else '无电量属性'}")
    print("\n== 渲染 ==")
    from .win32 import set_capture_exclusion
    print("抓屏排除 API : 可用（Win10 2004+）")
    print("说明：窗口「透明」靠桌面快照伪装，抓屏排除是零闪烁的前提；"
          "截屏工具在前台时会临时取消，保证截图里能看到面板。")
    return 0


def tray_debug() -> int:
    """--tray-debug：把托盘运行期状态打出来（图标没了先跑这个）。"""
    from .ui.tray import Tray

    app = TrayApp(panel_only=False)
    app.tray = Tray(app)
    app.tray.start()
    app.refresh()
    deadline = time.time() + 2.5
    while time.time() < deadline:
        time.sleep(0.1)
    t = app.tray
    print(f"托盘线程存活：{bool(t.thread and t.thread.is_alive())}")
    print(f"托盘窗口 hwnd：{t.hwnd}"
          f"（FindWindow={bool(user32.FindWindowW(Tray.CLASS_NAME, None))}）")
    print(f"最近一次 Shell_NotifyIconW：{t.last_nim or '（还没调用）'}")
    print(f"nids：{list(t.nids)}  icons：{list(t.icons)}")
    print(f"_tray_keys：{t._tray_keys()}")
    print(f"_visible_keys：{t._visible_keys()}")
    snap = app.snapshot()
    print(f"snapshot：{len(snap)} 台")
    for key, r in snap.items():
        print(f"  {key}: ok={r.ok} level={r.level} present={r.present} "
              f"status={r.status} name={r.name}")
    for uid, nid in t.nids.items():
        print(f"  nid[{uid}] hIcon={nid.hIcon} tip={nid.szTip}")
    log = os.path.join(config.config_dir(), "tray_error.log")
    if os.path.isfile(log):
        print(f"tray_error.log：{log}（存在，说明出过异常）")
    else:
        print("tray_error.log：无")
    try:
        t.destroy()
    except Exception:
        pass
    return 0


def install_all() -> int:
    ok = install_startup()
    links = install_desktop_links()
    print("开机自启", startup_lnk(), "（已创建）" if ok else "（失败）")
    for link in links:
        print("桌面链接", link)
    if not links:
        print("桌面链接：已存在，未重复创建")
    return 0


def register_app_identity():
    """让 Windows 通知显示「星火 Monitor」而不是 Python。"""
    try:
        import winreg
        path = r"Software\Classes\AppUserModelId\%s" % config.APP_ID
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ,
                              config.APP_NAME)
    except Exception:
        pass
    try:
        from .win32 import shell32
        shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wt.LPCWSTR]
        shell32.SetCurrentProcessExplicitAppUserModelID(config.APP_ID)
    except Exception:
        pass


def run_gui(panel_only: bool = False) -> int:
    global UI_SCALE
    register_app_identity()
    user32.SetProcessDPIAware()
    try:
        user32.GetDpiForSystem.restype = wt.UINT
        UI_SCALE = max(1.0, user32.GetDpiForSystem() / 96.0)
    except Exception:
        UI_SCALE = 1.0
    ctypes.set_last_error(0)
    mutex = kernel32.CreateMutexW(None, False, "Local\\BatteryTrayDSH_Mutex")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS and mutex:
        hwnd = user32.FindWindowW("BatteryTrayWndDSH", None)
        if hwnd:
            user32.PostMessageW(hwnd, WM_APP_SHOW, 0, 0)
            return 0
        # 老进程还在（mutex 在）但托盘窗口没了 → 它已经废了，继续建新实例。
        # 以前这里直接 return 0：用户重启毫无变化，任务管理器里还只有一个进程。
    app = TrayApp(panel_only=panel_only)
    app.run()
    return 0


def main(argv: list) -> int:
    from .capture import helper

    if helper.is_helper_invocation(argv):
        return helper.run(argv)
    if "--help" in argv or "-h" in argv:
        print(USAGE)
        return 0
    if "--once" in argv:
        return print_once(vendor="--vendor" in argv)
    if "--list" in argv:
        return list_hid()
    if "--scan" in argv:
        return scan_vendor(vendor="--vendor" in argv)
    if "--protocols" in argv:
        return print_protocols()
    if "--install-deps" in argv:
        i = argv.index("--install-deps")
        return install_deps(argv[i + 1:])
    if "--doctor" in argv:
        return doctor()
    if "--install" in argv:
        return install_all()
    if "--tray-debug" in argv:
        return tray_debug()
    return run_gui(panel_only="--panel" in argv)
