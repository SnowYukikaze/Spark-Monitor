# -*- coding: utf-8 -*-
"""「协议未知 → 抓包学习」向导窗口。

改文案只改 GUIDE 列表；改交互只改 start/stop/_finish_capture 三段。
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, simpledialog

from .. import config
from ..capture import infer, pcap
from ..syswin import helper_entry
from ..win32 import SHELLEXECUTEINFOW, set_round_region
from .draw import rgb
from .shadow import ShadowWindow
from .theme import get_theme
from .widgets import Button

GUIDE = [
    ("h", "第 1 步 · 先做准备"),
    ("b", "点下方「打开下载页」装好 USBPcap（已装就点「选择 USBPcap 路径」），装完重启一次电脑。"),
    ("b", "要按的键：安装时弹出权限提示，点「是 / 允许」。"),
    ("h", "第 2 步 · 开始记录"),
    ("b", "先关掉厂家驱动，再点窗口里的「开始抓包」。"),
    ("b", "要按的键：若弹出管理员权限窗口点「是」；系统设为静默授权时不会弹窗，属正常。"),
    ("h", "第 3 步 · 让设备报一次电量"),
    ("b", "打开厂家驱动让它显示电量，然后按一下设备本身："),
    ("b", "鼠标点一下左键 / 键盘敲任意键 / 耳机按一下功能键。"),
    ("b", "按完等 2~3 秒，设备才会把电量数据发给电脑。"),
    ("h", "第 4 步 · 收尾，怎样算完成"),
    ("b", "点「停止」后会弹出一个小输入框，把厂商软件里刚显示的那次电量填进去（例如显示 85 就填 85）。"),
    ("b", "填的是厂商驱动 / 网页驱动上显示的百分比，不是本软件卡片上的数字；设备要保持唤醒。"),
    ("b", "只有一个字节对得上就保存；多个就换个电量再抓一次。"),
    ("b", "完成标志：卡片直接显示电量数字，不再写「协议未知」。"),
    ("h", "如果多次都抓不到数据"),
    ("b", "可能厂家把协议加密了，这种情况需要用户自行解决。"),
    ("h", "最终方法（都不行时）"),
    ("b", "试完所有办法仍不出电量，就把本项目源码拷给大肥鱼或任意AI，"),
    ("b", "指出读不到电量的设备让他修好即可（项目地址在「关于」里）。"),
]


# 后台最长多久不给任何状态就算没起来：探测最多 6 个接口 × 4 秒，
# 留足余量，正常慢设备不会被误判。点「停止」后另给 10 秒自己收尾。
CAP_WAIT_TIMEOUT = 60.0
# 已经进入 capturing 后不再用 60 秒判死（用户想录多久都行），
# 只保留一个总上限，和 helper 自己的 180 秒对齐并留余量。
CAP_RUN_TIMEOUT = 240.0
STOP_TIMEOUT = 10.0


def _uac_silent() -> bool:
    """系统是否设为「管理员提权不提示」（这时确实提权了，只是不弹窗）。"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System")
        try:
            value, _ = winreg.QueryValueEx(key, "ConsentPromptBehaviorAdmin")
            return int(value) == 0
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False


def _process_exited(handle) -> bool:
    """提权后台是否已经退出；句柄无效时当作还在跑，交给超时兜底。"""
    if not handle:
        return False
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(
            ctypes.c_void_p(int(handle)), 0) == 0
    except Exception:
        return False


def _readable(path: str) -> bool:
    """提权进程产出的文件可能只认管理员；先探一下，别让界面静默卡死。"""
    try:
        with open(path, "rb") as f:
            f.read(1)
        return True
    except Exception:
        return False


class HelpWindow:
    LW, LH = 520, 730

    def __init__(self, parent, key=None):
        self.parent = parent
        self.key = key
        r = parent.app.snapshot().get(key) if key else None
        self.dev_name = (r.name if r and r.name else "")
        self.target_vid = r.vid if r else 0
        self.target_pid = r.pid if r else 0
        self.S = parent.S
        self.theme = get_theme(parent.app.theme_light)
        self.W = int(self.LW * self.S)
        self.H = int(self.LH * self.S)

        self.root = tk.Toplevel(parent.root)
        self.root.overrideredirect(True)
        self.root.configure(bg=rgb(self.theme.pal.window))
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        self.cv = tk.Canvas(self.root, width=self.W, height=self.H,
                            highlightthickness=0, bd=0,
                            bg=rgb(self.theme.pal.window))
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Button-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)
        self.cv.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Escape>", lambda e: self.close())
        self.root.update_idletasks()
        self.hwnd = self.root.winfo_id()
        set_round_region(self.hwnd, self.W, self.H, int(32 * self.S))
        from .ontop import raise_above
        raise_above(self.root, parent.root.winfo_id())
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - self.W) // 2)
        y = max(0, (sh - self.H) // 2 - 20)
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")

        # 正文滚动（内容比一屏高时才有滚动条）
        self._scroll_range = 0.0
        self._scroll_off = 0.0
        self._thumb = 0
        self._sbar = (0, 0, 0)
        self._hbody_ids: list = []

        self.shadow = None
        try:
            self.shadow = ShadowWindow(self)
        except Exception:
            self.shadow = None

        self._drag = None
        self._capturing = False
        self._cap_file = None
        self._cap_proc = None
        self._cap_dir = ""
        self._cap_status_file = ""
        self._cap_stop = ""
        self._cap_status = "点击「开始抓包」，抓包时请打开厂家软件显示电量"
        self._cap_txt = ""
        self._cap_started = 0.0         # 本轮后台启动时刻，用于超时兜底
        self._stop_at = 0.0             # 点「停止」的时刻，0 表示没在停
        self._cap_read_retry = 0        # 结果文件暂时读不了时的重试次数
        self._ambiguous = False
        self._cap_a = None
        self._purge_capture_files()
        self.render()
        try:
            self.root.update_idletasks()
            if self.shadow:
                self.shadow.show(self.root.winfo_x(), self.root.winfo_y())
        except Exception:
            pass

    def _f(self, size, bold=False):
        return self.theme.f(size, bold, self.S)

    # -- 绘制 -------------------------------------------------------------
    def render(self):
        t = self.theme
        cv = self.cv
        cv.delete("all")
        W, H = self.LW, self.LH
        cv.create_rectangle(0, 0, W, H, fill=rgb(t.pal.window), outline="")

        cv.create_text(28, 34, text="使用说明", anchor="w",
                       fill=rgb(t.pal.text), font=self._f(16, True))
        sub = "协议未知 · 如何让软件读到这款设备"
        if self.dev_name:
            sub = f"{self.dev_name} · 如何让软件读到这款设备"
        cv.create_text(28, 56, text=sub[:44], anchor="w",
                       fill=rgb(t.pal.sub), font=self._f(9))

        # 圆形关闭按钮，悬停变红
        cv.create_oval(W - 44, 16, W - 16, 44, fill=rgb(t.pal.window),
                       outline=rgb(t.pal.window), tags=("help_close",))
        cv.create_text(W - 30, 30, text="✕", fill=rgb(t.pal.sub),
                       font=self._f(11), tags=("help_close",))
        cv.tag_bind("help_close", "<Button-1>", lambda e: self.close())
        cv.tag_bind("help_close", "<Enter>", self._close_hover)
        cv.tag_bind("help_close", "<Leave>", self._close_leave)

        # ---- 底部固定区：从窗口底边往上排，按钮永远压不到提示文字 ----
        has_btn2 = not self._find_usbpcap()
        hint_y = H - 16
        before = set(cv.find_all())
        cv.create_text(28, hint_y,
                       text="每一步都写在这里，不用跳步 · 点右上角 ✕ 或按 Esc 退出",
                       anchor="sw", fill=rgb(t.pal.muted), font=self._f(8))
        status_item = cv.create_text(
            28, hint_y - 22, text=self._cap_status, anchor="sw",
            width=(W - 56), justify="left",
            fill=rgb(t.pal.sub), font=self._f(8))
        try:
            bb = cv.bbox(status_item)
            status_top = bb[1] if bb else hint_y - 20
        except Exception:
            status_top = hint_y - 20
        if has_btn2:
            btn2_y = status_top - 12 - 28
            btn_y = btn2_y - 8 - 30
        else:
            btn2_y = 0
            btn_y = status_top - 12 - 30
        Button(cv, t, self.S, 28, btn_y, 110, 30, "开始抓包", self.start_capture,
               kind="solid" if not self._capturing else "ghost").draw()
        Button(cv, t, self.S, 150, btn_y, 80, 30, "停止",
               self.stop_capture).draw()
        # 「清理抓包」一直显示：失败提示里让用户点它，不能只在
        # 「检测到多个电量字节」时才画出来，否则提示成了空头支票。
        Button(cv, t, self.S, 242, btn_y, 100, 30, "清理抓包",
               self._clear_capture).draw()
        if has_btn2:
            Button(cv, t, self.S, 28, btn2_y, 160, 28, "选择 USBPcap 路径",
                   self._choose_usbpcap).draw()
            Button(cv, t, self.S, 198, btn2_y, 140, 28, "打开下载页",
                   self._open_site).draw()
        footer_items = [i for i in cv.find_all() if i not in before]

        # ---- 正文区：装不下就滚动，超出的部分被遮罩挡住，不会压到按钮 ----
        body_top = 92
        body_bottom = max(body_top + 40, btn_y - 12)
        y = body_top
        for kind, text in GUIDE:
            item = cv.create_text(
                28, y, text=text, anchor="nw", width=(W - 56),
                justify="left",
                fill=rgb(t.pal.text if kind == "h" else t.pal.sub),
                font=self._f(10 if kind == "h" else 9, kind == "h"),
                tags=("hbody",))
            try:
                bb = cv.bbox(item)
                h = (bb[3] - bb[1]) if bb else 21
            except Exception:
                h = 21
            y += max(26 if kind == "h" else 21, h + 5)

        # 上下两条遮罩：把滚出可视区的正文挡掉（Canvas 没有裁剪，只能遮）
        cv.create_rectangle(0, 74, W, body_top,
                            fill=rgb(t.pal.window), outline="")
        cv.create_rectangle(0, body_bottom, W, H,
                            fill=rgb(t.pal.window), outline="")
        for i in footer_items:
            cv.tag_raise(i)

        cv.scale("all", 0, 0, self.S, self.S)
        self._setup_help_scroll(body_top, body_bottom)

    # -- 正文滚动 ---------------------------------------------------------
    def _setup_help_scroll(self, body_top, body_bottom):
        """正文比可视区高时挂滚轮 + 右侧细条；装得下就什么都不做。"""
        self._scroll_range = 0.0
        self._scroll_off = 0.0
        self._thumb = 0
        ids = list(self.cv.find_withtag("hbody"))
        self._hbody_ids = ids
        if not ids:
            return
        try:
            bb = self.cv.bbox(*ids)
        except Exception:
            return
        if not bb:
            return
        top = int(body_top * self.S)
        bottom = int(body_bottom * self.S)
        view = bottom - top
        need = bb[3] - top
        if view <= 0 or need <= view + 2:
            return
        self._scroll_range = need - view
        t = self.theme
        x = self.W - int(9 * self.S)
        wdt = max(3, int(3 * self.S))
        self.cv.create_line(x, top, x, bottom, width=wdt,
                            fill=rgb(t.pal.border), capstyle="round",
                            tags=("hscroll",))
        total = view + self._scroll_range
        h = max(int(28 * self.S), int(view * view / max(1, total)))
        self._thumb = self.cv.create_line(x, top, x, top + h, width=wdt,
                                          fill=rgb(t.pal.sub), capstyle="round",
                                          tags=("hscroll",))
        self._sbar = (top, bottom, h)
        self.cv.tag_raise("hscroll")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.cv.bind(seq, self._on_help_wheel)

    def _on_help_wheel(self, e):
        if self._scroll_range <= 0:
            return None
        num = getattr(e, "num", None)
        if num == 4:
            step = 1
        elif num == 5:
            step = -1
        else:
            step = 1 if getattr(e, "delta", 0) > 0 else -1
        self._scroll_help_by(step * int(46 * self.S))
        return "break"

    def _scroll_help_by(self, dy):
        if self._scroll_range <= 0:
            return
        lo = -self._scroll_range - self._scroll_off
        hi = -self._scroll_off
        dy = max(lo, min(hi, dy))
        if not dy:
            return
        self._scroll_off += dy
        for i in self._hbody_ids:
            try:
                self.cv.move(i, 0, dy)
            except Exception:
                pass
        self._move_help_thumb()

    def _move_help_thumb(self):
        if not self._thumb:
            return
        top, bottom, h = self._sbar
        view = bottom - top
        total = view + self._scroll_range
        if total <= 0:
            return
        pos = top + int(view * (-self._scroll_off) / total)
        x = self.W - int(9 * self.S)
        try:
            self.cv.coords(self._thumb, x, pos, x, pos + h)
            self.cv.tag_raise("hscroll")
        except Exception:
            pass

    def _close_hover(self, e=None):
        t = self.theme
        for item in self.cv.find_withtag("help_close"):
            if self.cv.type(item) == "oval":
                self.cv.itemconfigure(item, fill=rgb(t.pal.danger),
                                      outline=rgb(t.pal.danger))
            else:
                self.cv.itemconfigure(item, fill=rgb(t.pal.on_danger))
        self.cv.configure(cursor="hand2")

    def _close_leave(self, e=None):
        t = self.theme
        for item in self.cv.find_withtag("help_close"):
            if self.cv.type(item) == "oval":
                self.cv.itemconfigure(item, fill=rgb(t.pal.window),
                                      outline=rgb(t.pal.window))
            else:
                self.cv.itemconfigure(item, fill=rgb(t.pal.sub))
        self.cv.configure(cursor="")

    # -- 拖拽 -------------------------------------------------------------
    def _on_press(self, e):
        try:
            if self.shadow:
                self.shadow.show(self.root.winfo_x(), self.root.winfo_y())
        except Exception:
            pass
        self._drag = None
        if e.y >= 64 * self.S:
            return
        if "help_close" in self.cv.gettags("current"):
            return
        self._drag = (e.x_root, e.y_root,
                      self.root.winfo_x(), self.root.winfo_y())

    def _on_drag(self, e):
        if not self._drag:
            return
        x0, y0, wx, wy = self._drag
        dx, dy = e.x_root - x0, e.y_root - y0
        if abs(dx) < 3 and abs(dy) < 3:
            return
        nx, ny = wx + dx, wy + dy
        self.root.geometry(f"+{nx}+{ny}")
        if self.shadow:
            try:
                self.shadow.move(nx, ny)
            except Exception:
                pass

    def _on_release(self, e):
        self._drag = None

    # -- USBPcap ---------------------------------------------------------
    @staticmethod
    def _is_usbpcap_cmd(p: str) -> bool:
        """这个路径能不能当 USBPcapCMD.exe 用。

        为什么必须校验：它会被放进**提权**子进程的命令行。路径来自
        `config.json`（同用户可写的纯文本），只判「文件存在」等于把
        「写一个配置文件」升级成「以管理员身份执行任意程序」。
        这里同时挡住引号/换行等能改变命令行解析的字符。
        """
        if not p or not os.path.isfile(p):
            return False
        if os.path.basename(p).lower() != "usbpcapcmd.exe":
            return False
        return not any(ch in p for ch in ('"', "\n", "\r"))

    def _find_usbpcap(self):
        p = config.usbpcap_path()
        if self._is_usbpcap_cmd(p):
            return p
        for c in (r"C:\Program Files\USBPcap\USBPcapCMD.exe",
                  r"C:\Program Files (x86)\USBPcap\USBPcapCMD.exe"):
            if self._is_usbpcap_cmd(c):
                config.set_usbpcap_path(c)
                return c
        return ""

    def _choose_usbpcap(self):
        try:
            p = filedialog.askopenfilename(
                parent=self.root, title="选择 USBPcapCMD.exe",
                filetypes=[("USBPcapCMD.exe", "USBPcapCMD.exe"),
                           ("可执行文件", "*.exe")])
        except Exception:
            p = ""
        if p and not self._is_usbpcap_cmd(p):
            self._cap_status = "文件名必须是 USBPcapCMD.exe，已忽略所选项"
            self.render()
            return
        if p:
            config.set_usbpcap_path(p)
            self._cap_status = "已选择 USBPcapCMD.exe"
            self.render()

    def _open_site(self):
        try:
            webbrowser.open("https://desowin.org/usbpcap/")
        except Exception:
            pass

    def _outdir(self) -> str:
        """本次抓包的工作目录：每次随机建一个，不再用固定名。

        固定名（`%TEMP%\\BatteryTrayCapture`）+ 提权子进程 = 可被同用户进程
        抢先创建同名文件/联接来重定向写入。mkdtemp 的随机名没有这个问题，
        DACL 也只给当前用户。
        """
        if not getattr(self, "_cap_dir", ""):
            try:
                self._cap_dir = tempfile.mkdtemp(prefix="BatteryTrayCapture-")
            except Exception:
                self._cap_dir = os.path.join(tempfile.gettempdir(),
                                             "BatteryTrayCapture")
        return self._cap_dir

    def _purge_capture_files(self):
        """清掉本次工作目录里的抓包文件（顺带清历史遗留的固定目录）。"""
        dirs = [self._outdir(),
                os.path.join(tempfile.gettempdir(), "BatteryTrayCapture")]
        for d in dict.fromkeys(dirs):
            for name in ("capture.pcap", "status.json", "stop.flag"):
                try:
                    os.remove(os.path.join(d, name))
                except Exception:
                    pass

    # -- 抓包 -------------------------------------------------------------
    def start_capture(self):
        if self._capturing:
            return
        cmd = self._find_usbpcap()
        if not cmd:
            self._cap_status = "未找到 USBPcapCMD.exe，请先安装 USBPcap"
            self.render()
            return
        self._cap_dir = ""                 # 每次抓包重开一个随机工作目录
        outdir = self._outdir()
        try:
            os.makedirs(outdir, exist_ok=True)
        except Exception:
            pass
        # 目录先给当前用户授权：后台是提权进程，它新建的 pcap 会继承这条
        # ACE；即使后台被强杀、来不及补权限，抓包文件也仍然读得到。
        try:
            _user = os.environ.get("USERNAME") or ""
            if _user:
                subprocess.run(
                    ["icacls", outdir, "/grant", f"{_user}:(OI)(CI)F", "/Q"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    timeout=8)
        except Exception:
            pass
        status_file = os.path.join(outdir, "status.json")
        stop_file = os.path.join(outdir, "stop.flag")
        final = os.path.join(outdir, "capture.pcap")
        # 状态文件由本进程（普通权限）先建好，提权后台只覆写、不新建，
        # 访问权限就仍是本进程的，主界面才读得到进度；否则会一直停在
        # 「正在查找设备…」。抓包文件不预先建（USBPcapCMD 要自己新建，
        # 事先放一个空文件会让它直接退出），读权限由后台结束时统一补。
        try:
            with open(status_file, "w", encoding="utf-8") as f:
                json.dump({"state": "starting", "hub": 0, "file": ""}, f)
        except Exception:
            pass
        if getattr(sys, "frozen", False):
            argv = ["--capture-helper", cmd,
                    f"{self.target_vid:04X}", f"{self.target_pid:04X}",
                    outdir, stop_file, status_file]
        else:
            # 固定用产品入口，不跟 sys.argv[0] 走：从临时入口 / 其它宿主
            # 脚本启动时，argv[0] 可能是个完全没有 helper 分发的脚本，
            # 提权后只会再开一个界面，后台永远不写状态文件。
            argv = [helper_entry(), "--capture-helper", cmd,
                    f"{self.target_vid:04X}", f"{self.target_pid:04X}",
                    outdir, stop_file, status_file]
        # 用 list2cmdline 生成命令行，而不是手工拼引号：
        # 参数里出现引号/空格/换行时，手工拼接会改变 argv 的切分结果。
        params = subprocess.list2cmdline(argv)
        sei = SHELLEXECUTEINFOW()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = 0x00000040          # SEE_MASK_NOCLOSEPROCESS
        sei.lpVerb = "runas"
        sei.lpFile = sys.executable
        sei.lpParameters = params
        # 工作目录必须是**不可被普通用户写**的目录：Windows 的 DLL 搜索顺序
        # 里当前目录优先于 PATH，提权进程若以用户可写目录为 CWD，
        # 就能被同用户进程放同名 DLL 劫持（提权后加载用户代码）。
        sei.lpDirectory = os.environ.get("SystemRoot", r"C:\Windows") + r"\System32"
        sei.nShow = 0
        try:
            ok = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
        except Exception:
            ok = 0
        if not ok:
            self._cap_status = "启动失败（可能取消了管理员授权）"
            self.render()
            return
        self._cap_proc = sei.hProcess
        self._cap_dir = outdir
        self._cap_status_file = status_file
        self._cap_stop = stop_file
        self._cap_file = final
        self._capturing = True
        self._cap_started = time.monotonic()
        self._stop_at = 0.0
        self._cap_read_retry = 0
        hint = "（本系统静默提权，不会弹授权窗）" if _uac_silent() else ""
        self._cap_status = (("正在查找设备所在的 USBPcap 接口…"
                             if self.target_vid else "正在抓包…") + hint)
        self.render()
        self.root.after(400, self._poll_capture)

    def _force_stop(self, reason: str):
        """后台不给结论时的统一收尾：写停止标记、结束进程、给出明确文字。"""
        self._capturing = False
        self._stop_at = 0.0
        self._cap_read_retry = 0
        try:
            if self._cap_stop:
                with open(self._cap_stop, "w", encoding="utf-8") as f:
                    f.write("1")
        except Exception:
            pass
        if self._cap_proc:
            try:
                ctypes.windll.kernel32.TerminateProcess(self._cap_proc, 0)
                ctypes.windll.kernel32.CloseHandle(self._cap_proc)
            except Exception:
                pass
            self._cap_proc = None
        self._cap_status = reason
        self.render()

    def _poll_capture(self):
        if not self._capturing:
            return
        state, hub, path = "", 0, ""
        try:
            with open(self._cap_status_file, "r", encoding="utf-8") as f:
                info = json.load(f)
            state = info.get("state", "")
            hub = int(info.get("hub", 0) or 0)
            path = info.get("file", "")
        except Exception:
            pass
        # 状态没到终局时，三种「后台没动静」都要有明确结局：
        # 停止无回应、后台提前退出、一直不写状态。以前这里只做
        # after(400) 空转，界面就永远钉在「正在查找设备…」上。
        if state not in ("done", "stopped", "busy", "notraffic",
                         "notfound", "error"):
            if (self._stop_at and
                    time.monotonic() - self._stop_at > STOP_TIMEOUT):
                self._force_stop("已强制停止（后台没有回应停止指令）")
                return
            if _process_exited(self._cap_proc):
                self._force_stop("抓包后台提前退出，没有生成状态文件")
                return
            # 60 秒只用来判断「后台还没起来」；capturing 是正常状态，
            # 用户录多久都行，不能因为超过 60 秒就把正在录的杀掉。
            _elapsed = time.monotonic() - self._cap_started
            if state != "capturing" and _elapsed > CAP_WAIT_TIMEOUT:
                self._force_stop("抓包后台长时间没有回应，已结束本次记录")
                return
            if state == "capturing" and _elapsed > CAP_RUN_TIMEOUT:
                self._force_stop("本次记录已达上限，已自动停止")
                return
        if state == "capturing":
            self._cap_status = f"正在抓包：USBPcap{hub}"
            self.render()
        elif state == "busy":
            self._capturing = False
            self._cap_status = "抓包设备打不开（可能被其它抓包占用，或需要管理员权限）"
            self.render()
            return
        elif state == "notraffic":
            self._capturing = False
            self._cap_status = "抓到了流量但没发现目标设备，请在抓包时拔插一次接收器"
            self.render()
            return
        elif state == "notfound":
            self._capturing = False
            self._cap_status = "没有在任何 USBPcap 接口上找到这台设备"
            self.render()
            return
        elif state == "error":
            self._capturing = False
            self._cap_status = "USBPcap 启动失败"
            self.render()
            return
        elif state == "stopped":
            self._capturing = False
            self._stop_at = 0.0
            self._cap_status = "已停止（探测阶段收到停止指令）"
            self.render()
            return
        elif state == "done":
            if self._accept_result_path(path) and _readable(path):
                self._capturing = False
                self._stop_at = 0.0
                self._cap_read_retry = 0
                self._cap_file = path
                self._finish_capture()
            elif (path and os.path.isfile(path) and not _readable(path)
                  and self._cap_read_retry < 5):
                # 后台刚宣布完成、权限还没补上的那几百毫秒，别急着报
                # 「被管理员阻挡」：等几轮再读，绝大多数会自己好。
                self._cap_read_retry += 1
                self._cap_status = "正在读取抓包结果…（文件权限生效中，稍等）"
                self.render()
                self.root.after(400, self._poll_capture)
            else:
                self._capturing = False
                self._stop_at = 0.0
                if path and os.path.isfile(path):
                    self._cap_status = "抓包文件被管理员权限锁住，暂时读不了；点「清理抓包」后重试"
                elif path:
                    # status.json 由提权子进程写、由本进程读，路径必须落在本次
                    # 工作目录内；否则一份伪造的 status.json 就能让程序去解析
                    # 任意文件、并在它旁边写 .txt。
                    self._cap_status = "抓包文件位置异常，已忽略（可能是临时目录被其它程序改写）"
                else:
                    self._cap_status = "抓包结束，但没有生成文件"
                self.render()
            return
        self.root.after(400, self._poll_capture)

    def _accept_result_path(self, path: str) -> bool:
        """抓包结果必须就在本次工作目录里（防伪造 status.json 的路径采信）。"""
        if not path:
            return False
        try:
            base = os.path.realpath(self._cap_dir or self._outdir())
            real = os.path.realpath(path)
            return os.path.dirname(real) == base and os.path.isfile(real)
        except Exception:
            return False

    def stop_capture(self):
        if not self._capturing:
            return
        try:
            with open(self._cap_stop, "w", encoding="utf-8") as f:
                f.write("1")
        except Exception:
            pass
        self._stop_at = time.monotonic()   # 看门狗开始计时，超时就强制收尾
        self._cap_status = "正在停止…"
        self.render()

    def _clear_capture(self):
        self._capturing = False
        self._stop_at = 0.0
        self._cap_read_retry = 0
        try:
            if self._cap_stop:
                with open(self._cap_stop, "w", encoding="utf-8") as f:
                    f.write("1")
        except Exception:
            pass
        if self._cap_proc:
            try:
                ctypes.windll.kernel32.TerminateProcess(self._cap_proc, 0)
                ctypes.windll.kernel32.CloseHandle(self._cap_proc)
            except Exception:
                pass
            self._cap_proc = None
        for p in (self._cap_file, self._cap_txt,
                  self._cap_status_file, self._cap_stop):
            try:
                if p and os.path.isfile(p):
                    os.remove(p)
            except Exception:
                pass
        self._purge_capture_files()
        d = getattr(self, "_cap_dir", "")
        if d and os.path.basename(d).startswith("BatteryTrayCapture-"):
            try:
                import shutil
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
        self._cap_dir = ""
        self._cap_file = None
        self._cap_txt = ""
        self._ambiguous = False
        self._cap_a = None
        self._cap_status = "已清理抓包，可以重新开始"
        self.render()

    def _finish_capture(self):
        if not self._cap_file:
            self._cap_status = "抓包结束，但没有生成文件"
            self.render()
            return
        try:
            size = os.path.getsize(self._cap_file)
            detail = f"（{max(1, size // 1024)} KB）"
        except Exception:
            detail = "（没有生成文件）"

        # 单次解析最多取这么多帧：推断电量只用开头一小段，不设上限时
        # 256MB 抓包会吃掉约 470MB 内存并在主线程卡十几秒。
        _FRAME_LIMIT = 40000
        if self.target_vid:
            frames, _mapping = pcap.extract_for(self._cap_file,
                                               self.target_vid,
                                               self.target_pid,
                                               limit=_FRAME_LIMIT)
        else:
            frames = pcap.extract_reports(self._cap_file, limit=_FRAME_LIMIT)
        self._cap_txt = ""
        if frames:
            txt = os.path.splitext(self._cap_file)[0] + ".txt"
            try:
                with open(txt, "w", encoding="utf-8") as f:
                    f.write(pcap.format_reports(frames))
                self._cap_txt = txt
            except Exception:
                self._cap_txt = ""

        devs = pcap.scan_devices(self._cap_file)
        if self.target_vid:
            devs = [d for d in devs if d == (self.target_vid, self.target_pid)]
        if not devs and self.target_vid:
            self._ambiguous = False
            self._cap_status = "抓包里没有这台设备，请确认接收器已插好"
            self.render()
            return

        if frames and len(devs) == 1:
            try:
                lv = simpledialog.askinteger(
                    "填入电量", "填厂商软件（驱动 / 网页驱动）刚显示的电量百分比，例如 85：",
                    parent=self.root, minvalue=0, maxvalue=100)
            except Exception:
                lv = None
            if lv is None:
                self._cap_status = "已停止，但没有填写电量，未记录本次抓包"
                self.render()
                return
            second = self._cap_a is not None
            if second:
                lv_a, frames_a = self._cap_a
                if int(lv) == int(lv_a):
                    self._ambiguous = True
                    self._cap_status = "两次电量相同，请换一个相差至少 10% 的电量再抓一次"
                    self.render()
                    return
                proto, why = infer.infer_two(lv_a, frames_a, int(lv), frames)
            else:
                proto, why = infer.infer_single_ex(int(lv), frames)

            if proto:
                vid, pid = devs[0]
                mode, verified = infer.validate_capture(proto, vid, pid, int(lv))
                self._ambiguous = False
                self._cap_a = None
                if mode:
                    saved = dict(proto)
                    if mode == "invert":
                        saved["invert"] = True
                    config.save_user_protocol(vid, pid, saved)
                    config.forget_last(vid, pid)
                    if mode == "invert":
                        self._cap_status = (
                            f"已保存反向协议 {vid:04X}:{pid:04X}"
                            f"（回读 {verified.level}% → {100 - verified.level}%）")
                    else:
                        self._cap_status = (
                            f"已保存协议 {vid:04X}:{pid:04X}"
                            f"（回读 {verified.level}%）")
                else:
                    # 绝不保存：回读校验没过，界面上不能出现假电量
                    if verified is None:
                        self._cap_status = ("已推断出协议，但设备现在没回应；"
                                            "唤醒设备或重插接收器后重抓，暂不保存")
                    else:
                        self._cap_status = ("回读电量与填写值不符，未保存；"
                                            "核对厂商软件显示的数字再抓一次")
                try:
                    self.parent.app.refresh()
                except Exception:
                    pass
                self.render()
                return

            if why == "ambiguous":
                self._cap_a = (int(lv), frames)
                self._ambiguous = True
                self._cap_status = ("两次仍匹配到多个字节，请再换一个电量抓取"
                                    if second else
                                    "检测到多个电量字节，请换一个相差至少 10% 的电量再抓一次")
                self.render()
                return

            if second:
                self._cap_a = None
                self._ambiguous = False
                self._cap_status = "两次抓包没有找到随电量变化的字节，请重新抓取"
            else:
                self._ambiguous = False
                self._cap_status = "没有匹配的电量数据，可能是操作不规范，请重复操作一遍"
            self.render()
            return

        self._ambiguous = False
        parts = [f"已停止：{os.path.basename(self._cap_file)}{detail}"]
        if devs:
            parts.append("、".join(f"{v:04X}:{p:04X}" for v, p in devs[:2]))
        if frames:
            parts.append(f"帧 {len(frames)}")
        elif not devs:
            parts.append("未识别到设备")
        self._cap_status = " · ".join(parts)
        self.render()

    def is_alive(self) -> bool:
        try:
            return bool(self.root.winfo_exists())
        except Exception:
            return False

    def bring_front(self):
        try:
            from .ontop import raise_above
            raise_above(self.root, self.parent.root.winfo_id())
            self.root.lift()
        except Exception:
            pass

    def close(self):
        # 没在抓包时顺手收掉本次的空工作目录：以前每开一次窗就留一个
        # BatteryTrayCapture-* 文件夹，时间一长临时目录里能堆几百个。
        if not self._capturing:
            try:
                d = getattr(self, "_cap_dir", "")
                if (d and os.path.basename(d).startswith("BatteryTrayCapture-")
                        and os.path.isdir(d) and not os.listdir(d)):
                    os.rmdir(d)
            except Exception:
                pass
        self._stop_at = 0.0
        try:
            if self._capturing:
                self._capturing = False
                if self._cap_stop:
                    with open(self._cap_stop, "w", encoding="utf-8") as f:
                        f.write("1")
            if self._cap_proc:
                try:
                    ctypes.windll.kernel32.TerminateProcess(self._cap_proc, 0)
                    ctypes.windll.kernel32.CloseHandle(self._cap_proc)
                except Exception:
                    pass
                self._cap_proc = None
        except Exception:
            pass
        if self.shadow:
            try:
                self.shadow.destroy()
            except Exception:
                pass
            self.shadow = None
        try:
            self.root.destroy()
        except Exception:
            pass
        if getattr(self.parent, "_help_win", None) is self:
            self.parent._help_win = None
