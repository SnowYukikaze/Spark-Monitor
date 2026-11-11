# -*- coding: utf-8 -*-
"""系统托盘：一个设备一个电量圆环图标。

Tk 不能把回调放进原生消息循环，所以托盘自己开一个线程跑 Win32 消息循环，
跨线程只通过 PostMessage 通知（WM_APP_*）。
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
import traceback
from typing import Optional

from .. import config
from ..win32 import (DBT_DEVNODES_CHANGED, NIF_ICON, NIF_INFO, NIF_MESSAGE,
                     NIF_TIP, NIIF_LARGE_ICON, NIIF_USER, NIM_ADD, NIM_DELETE,
                     NIM_MODIFY, NOTIFYICONDATAW, WM_APP_INTERVAL,
                     WM_APP_REFRESH, WM_APP_SHOW, WM_CLOSE, WM_DEVICECHANGE,
                     WM_DESTROY, WM_LBUTTONUP, WM_RBUTTONUP,
                     WM_SETTINGCHANGE, WM_TIMER, WM_TRAYICON, WNDCLASSW,
                     WNDPROC, kernel32, shell32, user32, wt)
from .icon import make_battery_icon


def promote_tray_icon() -> bool:
    """把本程序的托盘图标标成「始终显示」（Win11 NotifyIconSettings）。

    键名是系统算的 hash，只能按 ExecutablePath 反查。pythonw.exe 是所有
    Python 程序共用的，可能命中别的脚本——打包成 exe 后就没这个问题。
    """
    import sys
    try:
        import winreg
    except Exception:
        return False
    try:
        exe = os.path.normcase(os.path.abspath(sys.executable))
        base = r"Control Panel\NotifyIconSettings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as root:
            i = 0
            hit = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub, 0,
                                        winreg.KEY_READ | winreg.KEY_WRITE) as k:
                        path, _ = winreg.QueryValueEx(k, "ExecutablePath")
                        if os.path.normcase(path or "") != exe:
                            continue
                        winreg.SetValueEx(k, "IsPromoted", 0, winreg.REG_DWORD, 1)
                        hit += 1
                except Exception:
                    continue
        return hit > 0
    except Exception:
        return False


class Tray:
    CLASS_NAME = "BatteryTrayWndDSH"

    def __init__(self, app):
        self.app = app
        self.hwnd = 0
        self.icons: dict[int, int] = {}
        self.icon_keys: dict[int, tuple] = {}
        self.thread: Optional[threading.Thread] = None
        self.nids: dict[int, NOTIFYICONDATAW] = {}
        self.enabled = True
        self.last_nim = ""            # 最近一次 Shell_NotifyIconW 结果（--tray-debug 用）
        self.taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self._wndproc = WNDPROC(self._proc)
        self._big_icon = 0
        try:
            promote_tray_icon()
        except Exception:
            pass

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _make_nid(self, uid: int) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = uid
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        return nid

    def _run(self):
        """托盘线程主体：异常不能静默死掉（pythonw 下没 stderr 根本看不见）。"""
        while not getattr(self.app, "_stop", False):
            try:
                self._loop()
                return
            except Exception:
                self._log("托盘线程异常，1 秒后重建")
                try:
                    self.hwnd = 0
                    for nid in list(self.nids.values()):
                        try:
                            shell32.Shell_NotifyIconW(NIM_DELETE,
                                                      ctypes.byref(nid))
                        except Exception:
                            pass
                    self.nids.clear()
                    for h in list(self.icons.values()):
                        try:
                            user32.DestroyIcon(h)
                        except Exception:
                            pass
                    self.icons.clear()
                    self.icon_keys.clear()
                except Exception:
                    pass
                time.sleep(1.0)

    def _log(self, msg: str) -> None:
        """落日志：pythonw 没有 stderr，吞异常等于永远查不到。"""
        try:
            d = config.config_dir()
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "tray_error.log"), "a",
                      encoding="utf-8") as f:
                f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
                traceback.print_exc(file=f)
        except Exception:
            pass

    def _loop(self):
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = self.CLASS_NAME
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, self.CLASS_NAME, config.APP_NAME, 0,
                                           0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            self._log("创建托盘窗口失败")
            return
        self._add_icons()
        user32.SetTimer(self.hwnd, 1, max(30, self.app.interval) * 1000, None)

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        # 消息循环结束：通知主线程退出，绝不留「窗口没了进程还活着」
        self.app._stop = True

    # -- 图标 -------------------------------------------------------------
    def _add_icons(self):
        self.icons.clear()
        self.nids.clear()
        self.icon_keys.clear()
        self.sync_icons()

    def _tray_rings(self) -> list:
        """UI 顺序、最多 3 个「有电量」设备；没有电量的返回空列表。"""
        out = []
        for key, r in self.app.snapshot().items():     # 已是 UI 顺序
            if r and r.ok and r.level is not None:
                out.append((key, r.level))
            if len(out) >= 3:
                break
        return out

    def _tray_keys(self) -> list:
        return [k for k, _lvl in self._tray_rings()]

    def _visible_keys(self) -> list[str]:
        """将要显示在图标里的键（最多 3 个）。

        一台有电设备都没有、但有设备在线时，退回 1 个键 → 画灰色空环，
        保留「系统里有设备但读不到电量」的反馈。
        """
        keys = self._tray_keys()
        if keys:
            return keys
        snap = self.app.snapshot()
        alive = [k for k, r in snap.items() if r and r.present]
        return alive[:1]

    def sync_icons(self):
        """一个托盘图标（uid 恒为 1），环数跟着设备数走。

        只有「一台设备都没有」时才删图标；某一刻读不到电量时必须留一个
        灰环 —— 以前 keys 为空就 NIM_DELETE，之后没人重建，
        用户看到的就是「托盘图标没了」。
        """
        snap = self.app.snapshot()
        if not snap:
            for uid, nid in list(self.nids.items()):
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                old = self.icons.pop(uid, 0)
                if old:
                    user32.DestroyIcon(old)
            self.nids.clear()
            self.icon_keys.clear()
            return
        keys = self._visible_keys() or [""]      # 空 → 灰环，不能画 0 个环
        uid = 1
        if uid not in self.nids:
            nid = self._make_nid(uid)
            self._fill_icon(nid, keys)    # 先填图标再 NIM_ADD，避免闪一个空图标
            ok = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
            self.last_nim = f"ADD:{int(bool(ok))}"
            if not ok:
                self._log("NIM_ADD 失败")
                return
            self.nids[uid] = nid
        else:
            nid = self.nids[uid]
            self._fill_icon(nid, keys)
            ok = shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            self.last_nim = f"MODIFY:{int(bool(ok))}"
            if not ok:
                self._log("NIM_MODIFY 失败，下轮重建")
                self.nids.pop(uid, None)
                old = self.icons.pop(uid, 0)
                if old:
                    user32.DestroyIcon(old)
                self.icon_keys.pop(uid, None)
                return
        # 多余的 uid（老版本可能留下 2/3 号）要清掉
        for extra in [u for u in self.nids if u != uid]:
            shell32.Shell_NotifyIconW(NIM_DELETE,
                                      ctypes.byref(self.nids[extra]))
            old = self.icons.pop(extra, 0)
            if old:
                user32.DestroyIcon(old)
            self.nids.pop(extra, None)
            self.icon_keys.pop(extra, None)

    def _fill_icon(self, nid: NOTIFYICONDATAW, keys):
        snap = self.app.snapshot()
        rings = []
        names, levels = [], []
        for key in keys:
            r = snap.get(key)
            lvl = r.level if (r and r.ok) else None
            color = config.get_device_color(key)          # §3.3，没有则 None
            if color is None and r and r.status == "charging":
                color = (34, 197, 94)                     # 充电中：绿色
            rings.append((lvl, tuple(color) if color else None))
            name = (r.name if (r and r.name) else (key or "无设备"))
            names.append(name)
            levels.append(f"{lvl}%" if lvl is not None else "未连接")
        cache_key = (tuple((lvl, c) for lvl, c in rings), self.app.theme_light)
        old = self.icons.get(nid.uID)
        if self.icon_keys.get(nid.uID) != cache_key or not old:
            icon = make_battery_icon(light=self.app.theme_light, rings=rings)
            self.icons[nid.uID] = icon
            self.icon_keys[nid.uID] = cache_key
            nid.hIcon = icon
            if old:
                user32.DestroyIcon(old)
        else:
            nid.hIcon = old
        # 悬停提示两排：第一排「标题 · 设备昵称…」，第二排「电量…」。
        # 标题取 get_title()，用户改了窗口标题这里立刻跟着变，不用重启。
        # szTip 只有 128 个字符，超了系统会截断，所以先拼再切。
        title = config.get_title()
        line1 = f"{title} · " + " / ".join(names)
        line2 = " / ".join(levels)
        nid.szTip = f"{line1}\n{line2}"[:127]

    def refresh_icons(self):
        self.sync_icons()

    # -- 通知 -------------------------------------------------------------
    def notify(self, text: str, title: str = config.APP_NAME) -> bool:
        """托盘气泡通知（Windows 通知中心也会收到）。"""
        nid = self.nids.get(1)
        if nid is None:
            return False
        if not self._big_icon:
            self._big_icon = make_battery_icon(100, self.app.theme_light,
                                               False, size=64)
        old_icon = nid.hIcon
        if self._big_icon:
            nid.hIcon = self._big_icon
        nid.uFlags = NIF_INFO | NIF_ICON
        nid.uVersion = 10000           # 10 = NOTIFYICON_VERSION_4
        nid.szInfo = text[:255]        # ← 原来这行被写进注释里了，通知没有正文
        nid.szInfoTitle = title[:63]
        nid.dwInfoFlags = NIIF_USER | NIIF_LARGE_ICON
        nid.hBalloonIcon = self._big_icon
        ok = shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.hIcon = old_icon
        nid.hBalloonIcon = 0
        return bool(ok)

    def destroy(self):
        for uid, nid in list(self.nids.items()):
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        self.nids.clear()
        for h in list(self.icons.values()):
            user32.DestroyIcon(h)
        self.icons.clear()
        if self._big_icon:
            user32.DestroyIcon(self._big_icon)
            self._big_icon = 0

    # -- 消息 -------------------------------------------------------------
    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAYICON:
            cmd = lparam & 0xFFFF
            if cmd == WM_RBUTTONUP:
                # 右键：设备列表（点一台跳到那张卡），而不是直接开面板
                self.app.request_device_menu()
            elif cmd == WM_LBUTTONUP:
                self.app.request_show()
            return 0
        if msg == WM_TIMER:
            self.app.refresh()
            return 0
        if msg == self.taskbar_created:
            self._add_icons()
            return 0
        if msg == WM_SETTINGCHANGE:
            if lparam:
                try:
                    if ctypes.wstring_at(lparam) == "ImmersiveColorSet":
                        self.app.on_theme_changed()
                except Exception:
                    pass
            return 0
        if msg == WM_DEVICECHANGE:
            if wparam == DBT_DEVNODES_CHANGED:
                self.app._hotplug_requested = True
                # 蓝牙耳机连上/断开也会发这个通知：立刻重读一次，
                # 否则要等下一个刷新间隔（默认 60 秒）才恢复显示
                self.app.refresh()
            return 0
        if msg == WM_APP_REFRESH:
            self.refresh_icons()
            return 0
        if msg == WM_APP_SHOW:
            self.app.request_show()
            return 0
        if msg == WM_APP_INTERVAL:
            user32.KillTimer(hwnd, 1)
            user32.SetTimer(hwnd, 1, max(30, self.app.interval) * 1000, None)
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            self.destroy()
            user32.KillTimer(hwnd, 1)
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
