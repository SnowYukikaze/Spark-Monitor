# -*- coding: utf-8 -*-
"""主面板（760×560，两列卡片网格）。

结构：
    __init__    建窗口、圆角、阴影、位置
    render      清空重画：卡片网格 → 头部遮罩与内容
    _build_*    头部 / 网格 / 滚动条
    编辑/菜单   改名、右键菜单、标题修改

所有坐标集中在 LAYOUT 里（逻辑像素，绘制后整体乘 DPI 缩放）。
"""
from __future__ import annotations

import os
import queue
import threading
import time
import traceback
import tkinter as tk
from tkinter import messagebox, simpledialog

from .. import config, syswin
from ..model import Reading
from ..syswin import install_startup, remove_startup, startup_enabled
from ..win32 import (CS_DROPSHADOW, GCL_STYLE, imm32, set_capture_exclusion,
                     set_round_region, user32, wt)
from . import backdrop, cards, customize
from .draw import rgb
from .menu import PopupMenu
from .shadow import ShadowWindow
from .theme import get_theme, with_colors
from .widgets import Button, ScrollBar

# ---------------------------------------------------------------------------
# 版式（逻辑像素）
# ---------------------------------------------------------------------------
LAYOUT = {
    "pad": 24,
    "header_h": 88,
    "list_top": 104,
    "list_bottom": -24,        # 相对 H
    "card_gap": 16,
    "scroll_x": -14,
    "title_xy": (24, 30),
    "subtitle_xy": (24, 58),
    "btn_y": 28,
    "btn_h": 32,
    "radius": 16,
}


class Panel:
    # 窗口缩放的内部尺度范围（§3.9）
    ZOOM_MIN, ZOOM_MAX = 0.82, 1.40
    EDGE = 6                              # 边缘热区（物理像素）

    def __init__(self, app):
        self.app = app
        self.S = max(1.0, getattr(app, "ui_scale", 1.0))
        self.theme = self._theme()
        t = self.theme
        self.LW, self.LH = t.panel_w, t.panel_h
        self.W, self.H = int(self.LW * self.S), int(self.LH * self.S)
        self._base_S = self.S               # DPI 缩放，缩放窗口时不动它
        # 先按保存的尺寸算一遍纯数值，Canvas 才能用正确的物理尺寸建出来
        size = config.get_panel_size()
        w0, h0 = size if size else (self.W, self.H)
        self._compute_metrics(w0, h0)

        self.root = tk.Tk()
        self.root.title(config.APP_NAME)
        self.root.overrideredirect(True)
        self.root.configure(bg=rgb(t.pal.window))
        self.cv = tk.Canvas(self.root, width=self.W, height=self.H,
                            highlightthickness=0, bd=0,
                            bg=rgb(t.pal.window))
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Button-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)
        self.cv.bind("<ButtonRelease-1>", self._on_release)
        self.cv.bind("<Button-3>", self._on_right_click)
        self.cv.bind("<Motion>", self._on_motion)
        self.cv.bind("<MouseWheel>", self._on_wheel)
        self.root.bind("<MouseWheel>", self._on_wheel)
        self.root.bind("<Escape>", self._on_escape)

        self._drag_window = None
        self._moved = False
        self._name_press = None       # 昵称按下状态（单击改名 / 长按拖动）
        self._name_dragging = False   # 昵称拖动期间冻结外部 render()
        self._help_win = None
        self._menu = None
        self._card_rects = {}
        self._card_w = ((t.panel_w - 2 * LAYOUT["pad"]
                         - LAYOUT["card_gap"]) / 2)   # 首帧前的兜底
        self._scroll = 0.0
        self._max_scroll = 0.0
        self._bar_drag = None
        self._scrollbar = None
        self._startup_var = startup_enabled()
        self._visible = False
        self._order_sig = None
        self._flash_key = None
        self._flash_until = 0.0
        self._queued_until = 0.0     # 刷新被排队时的提示截止时间（副标题用）
        # 拖动排序 / 背景图
        self._press = None            # (key, x, y, x_root, y_root)
        self._press_tags = ()         # 按下位置命中的 tag（判断是不是点在子链上）
        self._drag = None             # 正在拖的卡片 key
        self._drag_zone = ""
        self._drag_home = None        # 起始槽位下标
        self._drag_slot = None        # 当前目标槽位下标
        self._drag_layout = {}        # key -> 当前视觉左上角
        self._slots = {}              # key -> (x1,y1)
        self._zones = {"top": [], "bottom": []}
        self._anim_frames = 0
        self._anim_gen = {}           # key -> 动画代际号（旧链自动作废）
        self._cross_zone = False
        self._dragging = False        # 拖动期间冻结外部 render
        self._render_pending = False
        self._img_cache = {}
        self._frame_new_keys = set()
        self._add_dev_q = queue.Queue()   # 枚举结果从工作线程回主线程（只走队列）
        self._add_dev_busy = False
        self._app_bg_img = None
        self._app_layer_img = None
        self._dlg = None
        # 背景合成（§3.1）：桌面快照 + 三层实色合成，不再用窗口色键
        self._backdrop_img = None
        self._backdrop_key = None
        self._backdrop_epoch = 0
        self._grabbing = False
        # 窗口拖动期：首帧抓一张整屏桌面快照，之后按窗口坐标实时裁切
        self._drag_snapshot = None
        self._drag_xy = None           # 拖动中的目标左上角（geometry 尚未生效时用它）
        self._drag_last_render = 0.0
        # 背景逐帧同步（§3.1.6）：把本程序所有窗口设为「不进截图」后，
        # 可见状态下可直接 BitBlt 到窗口背后的桌面，无需隐藏窗口 → 零闪烁。
        self._capture_excl = False     # 抓屏排除是否真的生效（Win10 2004+）
        self._excl_hwnds = set()       # 已经设过排除的 HWND，避免重复调用
        self._sync_job = None          # 逐帧同步的自调度句柄
        self._sync_last = None         # 上一帧用于比对的整窗快照
        self._sync_stats = None        # 上一帧的明暗统计（与 _sync_last 严格配对）
        self._sync_misses = 0          # 连续「没变化」次数（用于退让）
        self._bg_item = None           # 整窗背景图元 id（逐帧只换这张图）
        self._bg_photo = None          # 当前整窗图（防 PhotoImage 被回收）
        self._last_render = 0.0        # 上次「交互触发」的 render 时刻（同步避让用）
        self._in_sync = False          # 当前这帧是不是逐帧同步自己画的
        # 卡片层原地换图（§3.1.7）：key → Canvas image 图元 id。
        # 快路径靠它只换 image、不 delete("all")，卡片半透明时也不会整窗闪。
        self._card_img = {}
        self._last_card_key = ""       # 最近一次 _card_layer 的缓存键（live 保护用）
        self._live_photo_keys = set()  # 仍被 Canvas 引用的 PhotoImage 缓存键
        self._capture_paused = False   # 检测到截屏工具 → 临时取消抓屏排除
        self._cap_check_at = 0.0       # 上次检查截屏工具的时刻（节流用）
        # 方案5：手动「截图 / 录屏模式」。不持久化，重启默认关闭，
        # 避免用户忘了关导致背景长期不跟随桌面。
        self._manual_capture_mode = False

        self.root.update_idletasks()
        self.hwnd = self.root.winfo_id()
        self._round_key = None
        # 圆角只按「窗口实际生效的尺寸」设置：<Configure> 会在系统真正
        # 改完窗口后触发，render() 末尾也会做一次幂等校验。以前在拖动里
        # geometry() 刚发请求就立刻 SetWindowRgn，窗口仍是旧尺寸，两者
        # 对不上 → 四个角被裁成方的，松手重设一次才「恢复正常」。
        self.root.bind("<Configure>",
                       lambda e: self._sync_round_region(e.width, e.height))
        set_round_region(self.hwnd, self.W, self.H,
                         int(LAYOUT["radius"] * 2 * self.S))
        self._round_key = (self.W, self.H, int(LAYOUT["radius"] * 2 * self.S))
        self._drop_shadow_style()
        self._drop_ime()
        self._shadow = None
        try:
            self._shadow = ShadowWindow(self)
        except Exception:
            self._shadow = None
        # 顺序很关键：_apply_widgets 要用 self.hwnd 和 self._shadow，
        # 在它们创建之前调用会静默失败（尺寸变了但圆角/阴影没变）
        self._resize = None
        self._resize_job = None
        self._resize_scale = None     # 拖动中上一次等比缩放的基准尺寸
        self._apply_metrics(render=False)
        self._place()
        self.root.withdraw()

    # -- 窗口装饰 ---------------------------------------------------------
    def _drop_shadow_style(self):
        try:
            style = user32.GetClassLongPtrW(self.hwnd, GCL_STYLE)
            user32.SetClassLongPtrW(self.hwnd, GCL_STYLE, style | CS_DROPSHADOW)
        except Exception:
            pass

    def _drop_ime(self):
        """面板没有输入框：摘掉 IME，避免候选条弹在窗口上方。"""
        try:
            imm32.ImmAssociateContext.argtypes = [wt.HWND, wt.HANDLE]
            imm32.ImmAssociateContext(self.hwnd, None)
        except Exception:
            pass

    def _place(self):
        """有上次位置就回到原位；没有就放屏幕右下角（托盘上方）。"""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        pos = config.get_panel_pos()
        w, h = self.W, self.H                 # _compute_metrics 已按屏幕夹紧
        if pos and -40 <= pos[0] <= sw - 160 and 0 <= pos[1] <= sh - 120:
            x, y = pos
        else:
            x = max(0, sw - w - 24)
            y = max(0, sh - h - 72)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # -- 尺寸 / 缩放（§3.9）------------------------------------------------
    def _compute_metrics(self, w: int, h: int):
        """纯数值：由物理宽高反算缩放与逻辑尺寸，不碰任何控件（构造期也能调）。"""
        root = getattr(self, "root", None)
        sw = root.winfo_screenwidth() if root is not None else 1920
        sh = root.winfo_screenheight() if root is not None else 1080
        self.W = max(560, min(int(w), max(560, sw - 40)))
        self.H = max(400, min(int(h), max(400, sh - 80)))
        zoom = min(self.W / (760 * self._base_S), self.H / (560 * self._base_S))
        self.S = self._base_S * max(self.ZOOM_MIN, min(self.ZOOM_MAX, zoom))
        self.LW = self.W / self.S
        self.LH = self.H / self.S

    def _sync_round_region(self, w: int = 0, h: int = 0):
        """按窗口「实际生效」的宽高重设圆角；尺寸没变就不重复调用 Win32。

        <Configure> 会带上系统刚改完的新宽高：拖动 / 缩放那一帧 winfo_*()
        可能还返回旧值，直接拿事件里的尺寸才不会让圆角慢半拍、把方形
        背景露出来。render() 末尾与手动刷新走无参数分支，自己查当前尺寸。
        """
        try:
            if w <= 0 or h <= 0:
                w, h = self.root.winfo_width(), self.root.winfo_height()
            if w <= 1 or h <= 1:
                return
            radius = int(LAYOUT["radius"] * 2 * self.S)
            key = (w, h, radius)
            if key == self._round_key:
                return
            set_round_region(self.hwnd, w, h, radius)
            self._round_key = key
        except Exception:
            pass

    def _apply_widgets(self, save: bool = False, render: bool = False):
        """尺寸 → 窗口 / 圆角 / 阴影 / 缓存。必须在 hwnd、_shadow 建好之后调用。"""
        try:
            self.root.geometry(f"{self.W}x{self.H}")
            self.cv.configure(width=self.W, height=self.H)
            self._sync_round_region()
        except Exception:
            pass
        shadow = getattr(self, "_shadow", None)
        if shadow is not None:
            shadow.invalidate()              # 缓存位图按旧尺寸，必须重建
        self._img_cache.clear()
        self._backdrop_epoch += 1
        if save:
            config.set_panel_size(self.W, self.H)
        if render:
            self.render()

    def _apply_metrics(self, w=None, h=None, save=False, render=True):
        self._compute_metrics(self.W if w is None else w,
                              self.H if h is None else h)
        self._apply_widgets(save=save, render=render)

    def _edge_at(self, x, y) -> str:
        """指针在窗口哪条边/角上（物理像素）："" / "n" / "se" / ...

        先纵向再横向，保证角上的键是 "nw"/"ne"/"sw"/"se"（和 _CURSORS 对齐）。
        """
        m = self.EDGE
        v = ""
        if y <= m:
            v = "n"
        elif y >= self.H - m:
            v = "s"
        h = ""
        if x <= m:
            h = "w"
        elif x >= self.W - m:
            h = "e"
        return v + h

    _CURSORS = {"n": "size_ns", "s": "size_ns", "e": "size_we", "w": "size_we",
                "ne": "size_ne_sw", "sw": "size_ne_sw",
                "nw": "size_nw_se", "se": "size_nw_se"}

    def _on_motion(self, e):
        if self._resize or self._drag_window or self._bar_drag is not None:
            return
        bar = self._scrollbar
        if bar is not None and bar.thumb_h > 0 and \
                bar.hit(e.x / self.S, e.y / self.S):
            self.cv.configure(cursor="sb_v_double_arrow")
            return
        self.cv.configure(cursor=self._CURSORS.get(self._edge_at(e.x, e.y), ""))

    def _resize_to(self, e):
        r = self._resize
        dx = e.x_root - r["x"]
        dy = e.y_root - r["y"]
        edge, wx, wy = r["edge"], r["wx"], r["wy"]
        w, h = r["w"], r["h"]
        nx, ny = wx, wy
        if "e" in edge:
            w = r["w"] + dx
        if "s" in edge:
            h = r["h"] + dy
        if "w" in edge:
            w = r["w"] - dx
            nx = wx + dx
        if "n" in edge:
            h = r["h"] - dy
            ny = wy + dy
        w = max(config.PANEL_MIN[0], min(config.PANEL_MAX[0], w))
        h = max(config.PANEL_MIN[1], min(config.PANEL_MAX[1], h))
        # 被夹紧时原点要跟着回退，否则窗口会「越拖越跑」
        if "w" in edge:
            nx = wx + (r["w"] - w)
        if "n" in edge:
            ny = wy + (r["h"] - h)
        self.root.geometry(f"{w}x{h}+{nx}+{ny}")
        self.W, self.H = w, h
        # 拖动中按新尺寸重算缩放比例，让背景图和卡片都按当前窗口重新生成。
        # 只挪坐标（cv.scale）不会放大图片本身，旧尺寸的整窗图 + 旧坐标的
        # 卡片裁片错位叠在一起，就是「背景图出现多个副本重叠」的根因。
        self._compute_metrics(w, h)
        try:
            self.cv.configure(width=self.W, height=self.H)
            # 圆角不在这里抢设：<Configure> 与 render() 会按实际尺寸同步
        except Exception:
            pass
        # 外发光按尺寸逐像素算，拖动中先藏起来，松手再按新尺寸重建
        if self._shadow:
            try:
                self._shadow.hide()
            except Exception:
                pass
        if self._resize_job is None:
            self._resize_job = self.root.after(30, self._flush_resize)

    def _flush_resize(self):
        self._resize_job = None
        self.render()

    def _save_pos(self):
        try:
            # 窗口还没映射时 winfo_x/y 是 0，别把 (0,0) 当成用户选择的位置
            if not self.root.winfo_viewable():
                return
            x, y = self.root.winfo_x(), self.root.winfo_y()
            if x == 0 and y == 0:
                return
            config.set_panel_pos(x, y)
        except Exception:
            pass

    def _f(self, size, bold=False):
        return self.theme.f(size, bold, self.S)

    def _bg_fill(self) -> str:
        """纯色兜底：没装 Pillow / 不透明 / 抓不到桌面时用窗口底色。

        注意：这里只是「底色」，中间透明度的混合在 _compose_app_layer 里
        对着真实快照做，绝不拿窗口底色当混合对象（§3.1 白雾根因）。
        """
        return rgb(self.theme.pal.window)

    def _theme(self):
        """默认主题 + 用户选的三组颜色（深/浅各自独立）。"""
        from . import theme as theme_mod
        theme_mod.set_font_scale(config.get_font_scale())   # 字体缩放（§3.8）
        t = get_theme(self.app.theme_light)
        return with_colors(t, config.get_colors(not self.app.theme_light))

    # -- 显隐 -------------------------------------------------------------
    def show(self):
        self._visible = True
        self._backdrop_epoch += 1            # 刚显示：背后换了内容，快照作废
        self.theme = self._theme()
        self.render()
        self.root.deiconify()
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        self.root.lift()
        if self._shadow:
            try:
                self.root.update_idletasks()
                self._shadow.show(self.root.winfo_x(), self.root.winfo_y())
            except Exception:
                pass
        self._apply_capture_exclusion()       # 先让本程序的窗口退出截图
        self._start_backdrop_tick()
        self._start_backdrop_sync()

    def _start_backdrop_tick(self):
        """只挂一条定时链，反复 show() 不要叠加。"""
        if getattr(self, "_tick_on", False):
            return
        self._tick_on = True
        try:
            self.root.after(1200, self._tick_backdrop)
        except Exception:
            self._tick_on = False

    def _start_backdrop_sync(self):
        """启动逐帧背景同步（只在抓屏排除可用时才有意义）。

        不可用时保持「事件驱动 + 隐藏抓取」的老路径：老路径每抓一次都要把
        窗口隐藏一帧，做成逐帧会肉眼可见地闪，不能挂定时器。
        """
        if not self._capture_excl:
            return
        if self._sync_job is not None:
            try:
                self.root.after_cancel(self._sync_job)
            except Exception:
                pass
        self._sync_job = self.root.after(16, self._backdrop_sync)

    def _on_escape(self, e=None):
        """Esc 先关菜单，再关面板（菜单不再抢焦点，按键只会到面板）。"""
        if self._menu:
            self._close_menu()
            return
        self.hide()

    def hide(self):
        self._close_menu()
        self._visible = False
        self._stop_backdrop_sync()
        self._close_help()
        self._save_pos()
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass
        if getattr(self.app, "panel_only", False):
            self.app.quit()
            return
        if self._shadow:
            try:
                self._shadow.hide()
            except Exception:
                pass
        self.root.withdraw()

    def close(self):
        self._close_menu()
        self._close_help()
        self._save_pos()
        self._stop_backdrop_sync()
        self._apply_capture_exclusion(False)   # 退出前还原，别留系统级副作用
        if self._shadow:
            try:
                self._shadow.destroy()
            except Exception:
                pass
            self._shadow = None
        try:
            self.root.destroy()
        except Exception:
            pass

    # -- 拖拽 -------------------------------------------------------------
    # 这些 Canvas item 自己会开菜单。Tk 先派发 item 绑定、再执行 widget 绑定，
    # 所以 _on_press 里「无条件关旧菜单」会把同一击刚开出来的菜单关掉。
    _MENU_TRIGGER_TAGS = ("btn", "chk")

    def _on_press(self, e):
        self._drag_snapshot = None      # 新一轮交互，交回正常快照流程
        self._drag_xy = None
        hits = self.cv.find_overlapping(e.x - 1, e.y - 1, e.x + 1, e.y + 1)
        under = tuple(t for i in hits for t in self.cv.gettags(i))
        opens_menu = any(t.startswith(self._MENU_TRIGGER_TAGS) for t in under)
        if self._menu and not opens_menu:
            try:
                self._menu.unpost()
            except Exception:
                pass
            self._menu = None
        if self._shadow:
            try:
                self._shadow.show(self.root.winfo_x(), self.root.winfo_y())
            except Exception:
                pass
        self._drag = None
        self._name_press = None
        self._press_tags = ()
        edge = self._edge_at(e.x, e.y)
        if edge:
            self._resize = {"edge": edge, "x": e.x_root, "y": e.y_root,
                            "wx": self.root.winfo_x(), "wy": self.root.winfo_y(),
                            "w": self.W, "h": self.H}
            self._drag_window = None
            self._press = None
            return
        # 滚动条：必须排在「拖动窗口」之前，否则按下把手会同时开始拖窗口。
        # 命中判定走 geometry，不依赖 Canvas 图元绑定（render 会重建图元）。
        if self._sbar_press(e):
            return
        # 昵称 / 编号：单击改名、长按拖动，两行文字吸附联动。
        # 放在卡片判断之前，否则文字会被当成「整卡拖动排序」的起点。
        for item in hits:
            for tg in self.cv.gettags(item):
                if not tg.startswith(("cardname:", "cardid:")):
                    continue
                prefix, key = tg.split(":", 1)
                kind = "name" if prefix == "cardname" else "id"
                bb = self.cv.bbox(item)
                if not bb:
                    continue
                cx, cy = self.cv.coords(item)[:2]   # anchor=center，画布物理像素
                peer_tag = ("cardid:" if kind == "name" else "cardname:") + key
                peers = self.cv.find_withtag(peer_tag)
                peer = peers[0] if peers else None
                if peer is not None:
                    pc = self.cv.coords(peer)
                    pcx, pcy = pc[:2] if pc else (cx, cy)
                else:
                    pcx, pcy = cx, cy
                self._name_press = {
                    "key": key, "kind": kind, "item": item, "peer": peer,
                    "t": time.time(),
                    "x": e.x_root, "y": e.y_root,
                    "cx": cx, "cy": cy, "pcx": pcx, "pcy": pcy,
                    "w": bb[2] - bb[0], "h": bb[3] - bb[1],
                    "drag": False, "moved": False,
                }
                self.cv.configure(cursor="fleur")   # 立刻给反馈
                return
        if e.y >= LAYOUT["header_h"] * self.S:
            # 卡片区：按下先记候选，移动 ≥6px 才算拖动排序
            lx, ly = e.x / self.S, e.y / self.S
            for ck, (cx1, cy1, cx2, cy2) in self._card_rects.items():
                if cx1 <= lx <= cx2 and cy1 <= ly <= cy2:
                    self._press = (ck, e.x, e.y, e.x_root, e.y_root)
                    self._press_tags = under
                    return
            # 卡片之外的空白（列间缝、卡片上方）：拖窗口。
            # 以前这里直接 return，色键区域又收不到消息，于是「拖不动窗口」。
            self._drag_window = (e.x_root, e.y_root,
                                 self.root.winfo_x(), self.root.winfo_y())
            return
        if any(t.startswith(self._MENU_TRIGGER_TAGS) for t in under):
            return
        self._drag_window = (e.x_root, e.y_root,
                             self.root.winfo_x(), self.root.winfo_y())

    def _on_drag(self, e):
        if self._bar_drag is not None:
            self._sbar_move(e)             # 滚动条拖动：绝不落到窗口拖动
            return
        if self._resize:
            self._resize_to(e)
            return
        if self._name_press:
            self._drag_name(e)
            return
        if self._press and not self._drag:
            key, px, py, _rx, _ry = self._press
            if abs(e.x - px) < 6 and abs(e.y - py) < 6:
                return
            self._start_card_drag(key, e)
            return
        if self._drag:
            self._drag_card(e)
            return
        if not self._drag_window:
            return
        x0, y0, wx, wy = self._drag_window
        dx, dy = e.x_root - x0, e.y_root - y0
        if abs(dx) < 3 and abs(dy) < 3:
            return
        self._moved = True
        nx, ny = wx + dx, wy + dy
        self.root.geometry(f"+{nx}+{ny}")
        # 记下本次目标位置：geometry() 要等事件循环空闲才真正生效，
        # 立刻用 winfo_rootx() 读到的还是上一帧坐标 → 背景会慢半拍。
        self._drag_xy = (nx, ny)
        if self._shadow:
            try:
                self._shadow.move(nx, ny)
            except Exception:
                pass
        # 软件背景透明度 > 0 时 L1 依赖「窗口所在位置」的桌面像素。
        # 逐帧隐藏窗口抓屏会闪烁、并让 root.update() 重入干扰鼠标拖动，
        # 因此首帧只抓一张整屏快照，之后按坐标实时裁切并节流完整重绘；
        # 完整 render 保证 L1 与依赖它的卡片层 L2 同步更新。
        if config.get_opacity()["app"] > 0 and customize.available():
            if self._drag_snapshot is None:
                self._drag_snapshot = self._grab_drag_snapshot()
            if self._drag_snapshot is not None:
                _now = time.monotonic()
                if _now - self._drag_last_render >= 0.02:
                    self._drag_last_render = _now
                    self.render()

    def _on_release(self, e):
        if self._bar_drag is not None:
            self._sbar_release(e)          # 先清状态，别让它落到 _save_pos
            return
        if self._resize:
            self._resize = None
            if self._resize_job is not None:
                try:
                    self.root.after_cancel(self._resize_job)
                except Exception:
                    pass
                self._resize_job = None
            self.W = self.root.winfo_width()
            self.H = self.root.winfo_height()
            self._resize_scale = None
            self._apply_metrics(save=True, render=True)
            # geometry 请求可能还没被系统处理完，等空闲时再按最终实际尺寸兜底
            try:
                self.root.after_idle(self._sync_round_region)
            except Exception:
                pass
            # 拖动中藏起来的外发光，按最终尺寸重建并显示
            if self._shadow and self._visible:
                try:
                    self._shadow.show(self.root.winfo_x(),
                                      self.root.winfo_y())
                except Exception:
                    pass
            return
        if self._name_press:
            np = self._name_press
            self._drag_name(e, final=True)        # 以释放点为准，最后一次 motion 不算数
            self._name_press = None
            self._name_dragging = False
            self._render_pending = False
            self.cv.configure(cursor="")
            if np["drag"] or np["moved"]:
                self._store_text_pos(np)           # 昵称 + 编号一起落盘
                self.render()
            elif np["kind"] == "name":
                self._open_rename(np["key"])       # 单击昵称 = 改名
            else:
                self._open_rename_id(np["key"])    # 单击编号 = 改编号
            return
        if self._drag:
            try:
                self._finish_card_drag(e)
            finally:
                self._dragging = False
                self._press = None
            return
        if self._press:
            # 单击 = 打开这张卡的菜单（以前这里把单击行为删空了 → 点了没反应）
            key = self._press[0]
            tags = self._press_tags or ()
            self._press = None
            self._press_tags = ()
            self._drag_window = None
            self._moved = False
            if not any(t.startswith(("cardfix", "cardname", "cardid")) for t in tags):
                self._card_menu(key, e.x_root, e.y_root)
            return
        if self._drag_window and self._moved:
            self._drag_snapshot = None       # 交回正常流程，下面会真实重抓
            self._save_pos()
            self._backdrop_epoch += 1        # 位置变了，快照作废（§3.1.5）
            if config.get_opacity()["app"] > 0:
                self.render()
        self._drag_snapshot = None
        self._drag_xy = None
        self._drag_window = None
        self._press = None
        self._moved = False

    # -- 昵称：单击改名 / 长按拖动改位置（§3.10）-------------------------
    NAME_HOLD = 0.18          # 0.35 太长：用户「按住就拖」会被判成单击
    NAME_MOVE = 4             # 逻辑像素：超过就算拖动，不必等满时长
    NAME_BASE_Y = 158         # 与 cards.render_card 里的默认位置一致
    ID_BASE_Y = 180           # 编号默认行（cards.render_card 的 y1 + 180）
    NAME_PAD = 8

    def _open_rename(self, key: str):
        from . import dialogs
        r = self.app.snapshot().get(key)
        if r is None:
            return
        if self._dlg is not None:
            try:
                self._dlg.close()
            except Exception:
                pass
        self._dlg = dialogs.RenameDialog(self, key, r.name)

    def _open_rename_id(self, key: str):
        from . import dialogs
        r = self.app.snapshot().get(key)
        if r is None:
            return
        model = (r.addr.upper() if r.transport == "bt" else r.ident) or "----"
        current = config.custom_ids().get(key,
                                          f"{model} · {r.transport_text}")
        if self._dlg is not None:
            try:
                self._dlg.close()
            except Exception:
                pass
        self._dlg = dialogs.RenameIdDialog(self, key, current)

    def _store_text_pos(self, np):
        """昵称与编号一起落盘：拖动任一行，另一行保持相对位置。"""
        rect = self._card_rects.get(np["key"])
        if not rect:
            return
        S = self.S
        xmid = (rect[0] + rect[2]) / 2
        if np["kind"] == "name":
            ncx, ncy, icx, icy = np["cx"], np["cy"], np["pcx"], np["pcy"]
        else:
            ncx, ncy, icx, icy = np["pcx"], np["pcy"], np["cx"], np["cy"]
        config.set_name_pos(np["key"], ncx / S - xmid,
                            ncy / S - (rect[1] + self.NAME_BASE_Y))
        config.set_id_pos(np["key"], icx / S - xmid,
                          icy / S - (rect[1] + self.ID_BASE_Y))

    def _drag_name(self, e, final=False):
        """绝对定位：文字中心 = 当前帧中心 + (本帧鼠标 - 上帧鼠标)。

        不再用「从 0 起算的增量」：已有的 name_pos 会被丢掉，拖动期间任何
        一次重绘又会把基准打回旧值，实测表现就是松手落到卡片随机位置。
        cx/cy 与 x/y 必须成对更新，否则同一段位移会被算两遍。
        """
        np = self._name_press
        if not np:
            return
        dxp = e.x_root - np["x"]
        dyp = e.y_root - np["y"]
        moved = (abs(dxp) >= self.NAME_MOVE * self.S
                 or abs(dyp) >= self.NAME_MOVE * self.S)
        if moved:
            np["moved"] = True
        if not np["drag"]:
            if not moved and time.time() - np["t"] < self.NAME_HOLD:
                return
            if not moved and not final:
                return                       # 按住还没动：等第一个位移或松手
            if not moved and final:
                return                       # 单击：交给 _on_release 走改名
            np["drag"] = True
            self._name_dragging = True        # 冻住外部 render
        cx, cy = self._clamp_name_center(np["key"], np["w"], np["h"],
                                         np["cx"] + dxp, np["cy"] + dyp)
        try:
            self.cv.coords(np["item"], cx, cy)
        except Exception:
            return
        # 吸附联动：另一行文字按主元素的「实际位移」同步移动。
        # 用夹取后的实际位移，触到卡片边界时两行相对位置仍不变。
        dx, dy = cx - np["cx"], cy - np["cy"]
        if np["peer"] is not None:
            try:
                self.cv.move(np["peer"], dx, dy)
                np["pcx"] += dx
                np["pcy"] += dy
            except Exception:
                pass
        np["cx"], np["cy"] = cx, cy
        np["x"], np["y"] = e.x_root, e.y_root

    def _clamp_name_center(self, key, w, h, cx, cy):
        """把文字中心夹在卡片内、下边界不越过电池框上沿；全部用物理像素。"""
        rect = self._card_rects.get(key)
        if not rect:
            return cx, cy
        S = self.S
        x1, y1, x2, y2 = [v * S for v in rect]
        pad = self.NAME_PAD * S
        left = max(x1 + pad, min(cx - w / 2, x2 - pad - w))
        top_min = y1 + pad
        top_max = cards.battery_top(rect[3]) * S - h - 2
        top = max(top_min, min(cy - h / 2, top_max))
        return left + w / 2, top + h / 2

    # -- 渲染 -------------------------------------------------------------
    def render(self):
        self._frame_new_keys.clear()   # 本帧新建的缓存条目，清理时跳过
        if self._grabbing:
            return                       # 抓快照期间的重入直接丢弃
        if self._dragging or self._name_dragging:
            # 卡片拖动中重画会把卡片画回原槽位；昵称拖动中重画会把文字
            # 拉回落盘位置，下一帧位移在错位上继续累加 → 落点漂移
            self._render_pending = True
            return
        if not self._in_sync:
            # 只记交互触发的重绘：同步自己画的那帧不算，否则会把自己饿死
            self._last_render = time.monotonic()
        self.theme = self._theme()
        t = self.theme
        cv = self.cv
        layer = self._compose_app_layer()
        self._app_layer_img = layer
        cards.hide_status_tip()       # 重绘会清掉状态行图元，先收起悬浮窗
        cv.delete("all")
        self._card_img.clear()         # 图元已随 delete("all") 消失，映射必须同步清
        cv.configure(bg=rgb(t.pal.window))
        if layer is not None:
            photo = self._photo_for(self._layer_key(), layer)
        else:
            photo = None
        # 本帧画完后，画布上还挂着哪些缓存的 PhotoImage —— 淘汰时必须绕开它们，
        # 否则 Tk 图元会指向一个已被回收的 image name，表现为「某张卡偶尔闪白」
        self._live_photo_keys = {self._layer_key()} if photo is not None else set()
        if photo is not None:
            self._bg_item = cv.create_image(0, 0, image=photo, anchor="nw")
            self._bg_photo = photo
            self._app_bg_img = layer
        else:
            self._bg_item = None           # 没有整窗图 → 逐帧同步只能走完整 render
            self._app_bg_img = None
            cv.create_rectangle(0, 0, self.LW, self.LH,
                                fill=self._bg_fill(), outline="")

        snap = self.app.snapshot()
        keys = list(snap.keys())
        # 惰性清理：配置里的 card_order 可能还留着早就删掉的设备键（§4.3）
        order = config.get_card_order()
        if order:
            alive = [k for k in order if k in snap]
            if len(alive) != len(order):
                config.prune_card_order(keys)
        self._build_grid(snap, keys)
        self._build_header()
        cv.scale("all", 0, 0, self.S, self.S)
        # 任意重绘之后都做一次幂等校验：圆角永远跟当前窗口尺寸一致
        self._sync_round_region()

    # -- 背景合成（§3.1）--------------------------------------------------
    def _app_windows(self) -> list:
        """本程序所有可能压在面板上、且会被抓进快照的窗口（Tk 顶层 + 阴影）。"""
        wins = [self.root]
        try:
            wins += [w for w in self.root.winfo_children()
                     if isinstance(w, tk.Toplevel)]
        except Exception:
            pass
        hwnds = []
        for w in wins:
            try:
                hw = w.winfo_id()
            except Exception:
                continue
            if hw:
                hwnds.append(int(hw))
        if self._shadow is not None:
            try:
                if self._shadow.hwnd:
                    hwnds.append(int(self._shadow.hwnd))
            except Exception:
                pass
        return hwnds

    def _apply_capture_exclusion(self, on: bool = True) -> None:
        """让本程序的窗口「屏幕上照常显示、但不进截图」。

        这是逐帧同步的前提：BitBlt 能直接读到窗口背后的桌面，不用隐藏窗口，
        也就没有闪烁和 90ms 的等待。抓不到排除的系统（Win10 2004 之前）
        自动退回老路径，行为与改动前一致。

        截屏工具在前台时（_capture_paused）拒绝重新打开：那时我们**需要**
        自己出现在截图里，见 _watch_capture_tool()。
        """
        if on and (self._capture_paused or self._manual_capture_mode):
            return
        hwnds = self._app_windows() if on else list(self._excl_hwnds)
        if on and set(hwnds) == self._excl_hwnds:
            return                       # 没有新窗口，别重复调 API
        self._excl_hwnds = set(hwnds) if on else set()
        root_hwnd = None
        try:
            root_hwnd = int(self.root.winfo_id())
        except Exception:
            pass
        ok = False
        for hw in hwnds:
            r = set_capture_exclusion(hw, on)
            if hw == root_hwnd:
                ok = r
        self._capture_excl = bool(on and ok)

    def _freeze_backdrop_for_capture(self) -> None:
        """进入「截屏让路」前，用**仍然生效的抓屏排除**补抓一帧当前桌面。

        为什么必须补这一帧：暂停后不能再抓屏（那时排除已撤，BitBlt 会抓到
        面板自己，形成自反馈），整段截屏期间的背景只能是一张冻结帧。
        不补的话冻结帧可能是 200ms 前、甚至窗口移动前的旧帧 —— 截图里面板
        背后的壁纸和周围的真实桌面不一致，就是「截屏时透明背景显示异常」。
        这里抓完立刻原地重画一次，保证「撤掉排除」那一刻屏幕上已经是新背景。
        """
        try:
            if not customize.available() or self._grabbing:
                return
            if not self._capture_excl:
                # 没有抓屏排除（Win10 2004 之前）时，此刻 BitBlt 抓到的是
                # 面板自己 —— 那会把「自己套自己」叠进背景。宁可不冻，保持原样。
                return
            if config.get_opacity()["app"] <= 0:
                return                      # 软件背景不透明：没有可冻结的层
            if self._drag_snapshot is not None:
                return                      # 拖动期的整屏帧更贴近当前，别覆盖
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
            frame = backdrop.grab(x, y, self.W, self.H)
            if frame is None or not backdrop.usable(frame):
                return
            self._push_backdrop(frame)       # epoch += 1，并写入 _backdrop_key
            self._sync_last = frame
            self._sync_stats = backdrop.extremes(frame)
            self._in_sync = True
            try:
                self._repaint_backdrop()     # 原地换图，不 delete、不闪
            finally:
                self._in_sync = False
        except Exception:
            pass

    def set_capture_mode(self, on: bool) -> None:
        """手动「截图 / 录屏模式」（方案5 核心）。

        开启：先补抓一帧并原地重画（背景不跳），再撤掉抓屏排除、冻结背景、
        停掉逐帧抓屏。此后窗口在 PrtSc、第三方截屏和**长时间录屏**里都正常
        可见，且状态一直保持到手动关闭，不会被自动检测逻辑改回去。
        关闭：恢复抓屏排除 + 立刻重抓一帧 + 重启逐帧同步，背景恢复实时跟随。
        """
        if bool(on) == self._manual_capture_mode:
            return
        if on:
            self._freeze_backdrop_for_capture()
            self._manual_capture_mode = True
            self._capture_paused = True       # 复用已有冻结逻辑：不抓屏、不闪
            self._apply_capture_exclusion(False)
            self._stop_backdrop_sync()
            self._sync_last = None
            self._sync_stats = None
        else:
            self._manual_capture_mode = False
            self._capture_paused = False
            self._backdrop_epoch += 1
            self._apply_capture_exclusion(True)
            self._start_backdrop_sync()
        try:
            self.render()
        except Exception:
            pass

    def toggle_capture_mode(self):
        self.set_capture_mode(not self._manual_capture_mode)

    def _settings_changed(self):
        """外观设置（背景 / 透明度）被改动后的统一入口。

        以前这些回调只写配置 + render()，而 render 会命中「截屏让路」的
        冻结分支，直接返回上一张 L1 —— 于是换背景、调透明度全都不生效，
        只有点一下截图模式强制重算才突然应用。现在：改动一律先让冻结帧
        失效，并自动退出截图模式，然后立刻用新设置重画。
        """
        self._app_layer_img = None
        self._backdrop_img = None
        self._backdrop_key = None
        self._backdrop_epoch += 1
        self._img_cache.clear()
        if self._manual_capture_mode:
            self.set_capture_mode(False)      # 内部已 render
            return
        if self._capture_paused:
            self._capture_paused = False
            try:
                self._apply_capture_exclusion(True)
            except Exception:
                pass
        try:
            self.render()
        except Exception:
            pass

    def toggle_vendor_probe(self):
        """设置菜单「自动识别」：开关已知厂商协议的自动只读试探。"""
        config.set_vendor_probe(not config.vendor_probe_enabled())
        try:
            self.app.refresh()
        except Exception:
            pass

    def _open_capture_switch(self):
        """菜单 → 独立的截图模式开关窗口（等菜单收起后再弹，避免抢焦点）。"""
        from .switchwin import open_switch
        self.root.after(0, lambda: open_switch(self, "capture"))

    def _open_auto_switch(self):
        from .switchwin import open_switch
        self.root.after(0, lambda: open_switch(self, "auto"))

    def _watch_capture_tool(self) -> None:
        """正在截屏 / 录屏时，把窗口「交出去」，让截图里能看到面板本身。

        根因：面板的透明是「桌面快照 + 实色混合」伪装的，快照依赖
        WDA_EXCLUDEFROMCAPTURE 把自己从抓屏里排除。副作用是任何截屏工具
        拍到的都是「没有面板」的画面，用户看到的就是「一按截屏窗口就变透明」。
        这里检测到截屏前端在前台（接近全屏）时，临时取消排除并停掉逐帧同步；
        工具退出后自动恢复。整段只改窗口的截屏可见性，不动画面内容。

        顺序很重要（2026-11 修）：先补抓一帧 + 原地重画，**再**撤排除。
        反过来的话，冻结帧一定是旧的，截图里背景就会和周围桌面不一致。
        """
        if self._manual_capture_mode:
            return                       # 手动模式优先，自动检测不得插手
        now = time.monotonic()
        if now - self._cap_check_at < 0.25:
            return
        self._cap_check_at = now
        try:
            active = syswin.capture_tool_active()
        except Exception:
            active = False
        if active == self._capture_paused:
            return
        if active:
            # 顺序（缺一不可）：
            #   1. 补抓一帧并重画 —— 此时抓屏排除还在，抓到的是干净桌面，
            #      而且 `_capture_paused` 尚未置位，不会命中「暂停期复用旧层」
            #      那条短路，新帧才真的被画上去；
            #   2. 进入暂停态；
            #   3. 最后才撤掉抓屏排除，让面板进得了别人的截图。
            self._freeze_backdrop_for_capture()
            self._capture_paused = True
            self._apply_capture_exclusion(False)
            self._sync_last = None
            self._sync_stats = None
        else:
            self._capture_paused = False
            self._backdrop_epoch += 1              # 恢复后立刻重抓一帧
            self._apply_capture_exclusion(True)

    def _stop_backdrop_sync(self):
        if self._sync_job is not None:
            try:
                self.root.after_cancel(self._sync_job)
            except Exception:
                pass
            self._sync_job = None

    def _sync_ready(self) -> bool:
        """逐帧同步是否该跑：可见、背景真的透出、当前没有别的拖拽在用鼠标。"""
        if not self._visible or not self._capture_excl:
            return False
        if self._manual_capture_mode:
            return False                 # 手动模式：全程不抓屏，窗口留在截图里
        if not customize.available():
            return False
        if config.get_opacity()["app"] <= 0:
            return False
        if self._grabbing or self._dragging or self._name_dragging or self._resize:
            return False
        if self._bar_drag is not None or self._drag_window is not None:
            return False
        return self._drag_snapshot is None

    def _backdrop_sync(self):
        """桌面背景逐帧同步：窗口不动时背景也实时跟着桌面/下一层变。

        一帧的实测成本（本机 760×560）：读屏 11ms（其中 BitBlt 8ms 是硬下限）
        + 合成 6ms + 上图 2.5ms ≈ 20ms。因此 60fps 在这里物理上做不到，
        但也不设「刷新间隔」这种人为等待：每帧跑完就按实测耗时排下一帧
        （`cost × 1.2`，单帧最多占约 45% 时间片），本机约 20fps、机器越快越高。

        分支：
            有变化   → 立刻换图（`_repaint_backdrop`），下一帧马上来
            没变化   → 40 → 65 → 90 … 最长 200ms 退让，静止开销 <5% 单核
            刚重绘过 → 让 32ms，别和交互重绘抢时间
            抓不到   → 200ms，绝不空转（远程桌面等场景）
        """
        self._sync_job = None
        if not self._visible:
            return
        t0 = time.monotonic()
        delay = 16
        try:
            self._apply_capture_exclusion()      # 新开的弹窗/菜单也要退出截图
            self._watch_capture_tool()           # 正在截屏 → 先把自己交出去
            # 刚画过一帧就让一让：交互重绘优先，别让同步在 root.update() 里
            # 叠加重绘，把滑动/拖动拖慢。
            if time.monotonic() - self._last_render < 0.15:
                delay = 32
            elif self._sync_ready():
                frame = backdrop.grab(
                    self.root.winfo_rootx(), self.root.winfo_rooty(),
                    self.W, self.H)
                bad = False
                stats = None
                if frame is not None:
                    if not backdrop.usable(frame):
                        frame = None
                    else:
                        # 新画面只统计一次；上一帧的统计直接复用缓存，
                        # 不再每帧把 prev 重新缩小、转灰、算直方图。
                        stats = backdrop.extremes(frame)
                        if not backdrop.sane(frame, self._sync_last,
                                             frame_stats=stats,
                                             prev_stats=self._sync_stats):
                            # 明显抓坏的帧（GPU 动态壁纸偶发的整片黑/白）：丢掉，
                            # 画面保持上一张。下一帧立刻重试，不退让到 200ms，
                            # 否则用户会看到「背景停住」。
                            frame, bad, stats = None, True, None
                if frame is not None:
                    if backdrop.changed(frame, self._sync_last):
                        self._sync_last = frame
                        self._sync_stats = stats
                        self._sync_misses = 0
                        self._push_backdrop(frame)
                        self._in_sync = True
                        try:
                            # 只换 Canvas 图元上的图；换不了就跳过这一帧。
                            # 2026-11 修复（闪白/闪黑）：这里以前会退回
                            # render()，而完整 render 是 delete("all") +
                            # 重建 300+ 图元（实测单帧 60ms），同步循环最高
                            # 20fps 地跑它，屏幕上看就是整窗闪一下，闪的
                            # 正是画布底色（浅色主题闪白、深色闪黑）。
                            self._repaint_backdrop()
                        finally:
                            self._in_sync = False
                        delay = 16
                    else:
                        self._sync_misses += 1
                        delay = min(200, 40 + 25 * self._sync_misses)
                else:
                    delay = 16 if bad else 200   # 坏帧马上重试；抓不到才退让
            else:
                delay = 60
        except Exception:
            delay = 500
        finally:
            # 按「本帧实际耗时」定下一帧的间隔，让单帧最多占约 45% 时间片：
            # 本机一帧约 20ms（读屏 11 + 合成 6 + 上传 2.5），因此约 20fps；
            # 机器越快帧率越高，直到 8ms 下限（≈60fps）。静止时走退让分支，
            # 每 200ms 才探一次，开销降到 5% 以下。
            cost = (time.monotonic() - t0) * 1000
            self._sync_cost = cost
            if cost > 1:
                delay = max(8, min(250, int(cost * 1.2)))
            if self._visible:
                try:
                    self._sync_job = self.root.after(delay, self._backdrop_sync)
                except Exception:
                    self._sync_job = None

    def _push_backdrop(self, frame) -> None:
        """把刚抓到的桌面帧挂进缓存，接着的 render() 直接复用、不再重抓。"""
        self._backdrop_epoch += 1
        self._backdrop_key = (self.root.winfo_rootx(), self.root.winfo_rooty(),
                              self.W, self.H, self._backdrop_epoch)
        self._backdrop_img = frame

    def _repaint_backdrop(self) -> bool:
        """只换掉依赖桌面那几层：整窗 L1 图 + 每张卡片的 L2 裁片，原地换图。

        完整 render() 要 45–60ms（Canvas 全清 + 字体排版 + 全量重绘），
        逐帧跑不动；这里只重算 L1（必要时连卡片裁片一起重算）并替换图元，
        不 delete、不重建文字，所以画面是连续的，不会闪。
        返回 False = 这一帧没画成，调用方直接跳过（绝不退回完整 render）。

        条件：卡片层不依赖 L1 时（卡片完全不透明）只换整窗图；卡片半透明时
        连卡片裁片一起换 —— 2026-11 之前这里直接放弃并让上层跑完整 render，
        正是「设置透明度后偶尔闪一下」的根因。
        """
        if self._bg_item is None or not self._sync_ready():
            return False
        layer = self._compose_app_layer()
        if layer is None:
            return False
        photo = customize.to_photo(layer)
        if photo is None:
            return False
        self._frame_new_keys.clear()       # 快路径不整窗重画，按帧重新统计
        live = {self._layer_key()}
        try:
            self.cv.itemconfigure(self._bg_item, image=photo)
            # 先把本帧 L1 落到位，**再**重算卡片层：卡片的 L2 是从
            # `self._app_layer_img` 上按真实坐标裁的，晚一步赋值就会用上一帧的
            # 背景去裁 —— 桌面一变（视频、窗口切换、截屏遮罩变暗），卡片区域的
            # 背景就比窗口背景慢一帧，边界处能看出错位；截屏让路时窗口背景是
            # 刚冻结的新帧、卡片还是旧帧，于是「面板里有几块背景不对劲」。
            self._bg_photo = photo             # Canvas 引用之外再兜一层
            self._app_layer_img = layer
            self._app_bg_img = layer
            if config.get_alpha("card") < 0.999:
                live |= self._repaint_cards()
            else:
                live |= self._live_photo_keys
        except Exception:
            return False
        self._live_photo_keys = live
        return True

    def _repaint_cards(self) -> set:
        """卡片背景层原地换图：只 itemconfigure，不删图元、不重排文字。

        每张卡片的 L2 = 已合成 L1 的同位置裁片再与卡内容混合，所以桌面一变，
        卡片就必须跟着变；用「换 image」而不是「重画整卡」，画面才连续。
        返回本帧仍被卡片图元引用的缓存键（供 _photo_for 淘汰时避开）。
        """
        live: set = set()
        for key, item in list(self._card_img.items()):
            rect = self._card_rects.get(key)
            if not rect:
                continue
            photo = self._card_layer(key, *rect)
            if photo is None:
                continue
            try:
                self.cv.itemconfigure(item, image=photo)
            except Exception:
                self._card_img.pop(key, None)
                continue
            live.add(self._last_card_key)
        return live

    def _grab_drag_snapshot(self):
        """拖动首帧抓一张整屏桌面快照，供拖动中按窗口坐标实时裁切。

        有抓屏排除时直接抓（窗口不用消失，首帧也不闪）；没有时退回
        「alpha=0 + 等一帧」的老路径。

        截屏期间（_capture_paused）直接放弃：那条兜底路径要把窗口 alpha 设成 0
        再抓，此刻窗口正好在别人的截图里 —— 一藏一现，截图上就是闪一下/缺一块。
        放弃后 `_drag_backdrop()` 返回 None，上层会用冻结帧继续画，画面不跳。
        """
        if not customize.available() or self._grabbing:
            return None
        if self._capture_paused:
            return None
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
        except Exception:
            return None
        self._grabbing = True
        img = None
        try:
            if self._capture_excl:
                img = backdrop.grab(0, 0, sw, sh)     # 窗口不进截图，直接抓
            else:
                if self._shadow:
                    try:
                        self._shadow.hide()
                    except Exception:
                        pass
                self.root.attributes("-alpha", 0.0)
                self.root.update_idletasks()
                time.sleep(0.02)
                img = backdrop.grab(0, 0, sw, sh)
        except Exception:
            img = None
        finally:
            if not self._capture_excl:
                try:
                    self.root.attributes("-alpha", 1.0)
                    self.root.update_idletasks()
                except Exception:
                    pass
            if self._shadow and self._visible and not self._capture_excl:
                try:
                    self._shadow.show(self.root.winfo_x(),
                                      self.root.winfo_y())
                except Exception:
                    pass
            self._grabbing = False
        return img if backdrop.usable(img) else None

    def _drag_backdrop(self):
        """从整屏拖动快照按窗口当前屏幕坐标裁出 W×H 桌面底图。

        复用 aligned_crop：窗口贴边时越界部分用屏幕边缘像素延展填满，
        不出现未渲染块。
        """
        if self._drag_snapshot is None:
            return None
        try:
            x, y = (self._drag_xy if self._drag_xy is not None
                    else (self.root.winfo_rootx(), self.root.winfo_rooty()))
        except Exception:
            return None
        return customize.aligned_crop(self._drag_snapshot, x, y,
                                      self.W, self.H)

    def _grab_backdrop(self):
        """窗口背后的桌面快照。软件背景不透明时不抓，零开销。

        有抓屏排除（_capture_excl）时：可见状态下直接 BitBlt，窗口不用隐藏，
        约 12ms、零闪烁；缓存键里的 epoch 只用来标识「这一帧是不是刚推过来的」，
        逐帧同步（_push_backdrop）会带着新帧进来复用。
        没有抓屏排除时：退回「隐藏窗口等稳定帧」的老路径（约 90ms，会闪一帧），
        只由真实事件触发，不做逐帧。
        """
        if config.get_opacity()["app"] <= 0:
            return None
        if not customize.available() or self._grabbing:
            return None
        if self._capture_paused:
            # 正在截屏：不能隐藏窗口去抓（会闪，而且抓到的是没有面板的桌面），
            # 直接沿用冻结帧。正常路径下 `_compose_app_layer` 已经先返回屏幕上
            # 那张 L1，走不到这里；只有「还没合成过 / 尺寸对不上」才会落到这。
            img = self._backdrop_img
            if img is not None and img.size != (self.W, self.H):
                # 暂停期间窗口尺寸变了：旧尺寸的帧交给 blend_layer 会被
                # cover_image 居中裁切，背景会被拉伸/裁掉。宁可不给，退回纯色。
                return None
            return img
        key = (self.root.winfo_rootx(), self.root.winfo_rooty(),
               self.W, self.H, self._backdrop_epoch)
        if key == self._backdrop_key:
            return self._backdrop_img
        self._grabbing = True               # 抓快照会 root.update()，防重入 render
        try:
            if self._capture_excl:
                img = backdrop.grab(self.root.winfo_rootx(),
                                    self.root.winfo_rooty(),
                                    self.W, self.H)
            else:
                img = self._grab_hidden()
        finally:
            self._grabbing = False
        if not backdrop.usable(img):
            img = None
        self._backdrop_key, self._backdrop_img = key, img
        return img

    def _grab_hidden(self):
        """隐藏窗口后等 DWM 重绘完，连抓多帧取稳定帧。

        只 update 一次就 BitBlt 会抓到面板消失前的旧帧或尚未重绘的白底；
        浅色旧帧混进软件背景后，30% 透明度看起来像 90%（本轮 Bug2）。
        最多抓 3 帧，相邻两帧稳定即采用后一帧；全程只在窗口位置/尺寸变化、
        首次需要透出桌面时发生，拖动滑块复用缓存、不重抓。
        """
        x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
        try:
            if self._shadow:
                self._shadow.hide()
            self.root.attributes("-alpha", 0.0)
            for _ in range(3):
                self.root.update_idletasks()
                self.root.update()
            time.sleep(0.03)
            self.root.update()
        except Exception:
            pass
        try:
            prev = None
            last = None
            for i in range(3):
                if i:
                    time.sleep(0.02)
                    self.root.update()
                cur = backdrop.grab(x, y, self.W, self.H)
                last = cur
                if prev is not None and backdrop.stable(prev, cur):
                    return cur
                prev = cur
            return last
        finally:
            try:
                self.root.attributes("-alpha", 1.0)
                self.root.update_idletasks()
                self.root.update()
            except Exception:
                pass
            if self._shadow and self._visible:
                try:
                    self._shadow.show(self.root.winfo_x(),
                                      self.root.winfo_y())
                except Exception:
                    pass

    def _compose_app_layer(self):
        """整窗背景层 L1：软件背景内容按「软件背景透明度」透出桌面 L0。

        层级固定为 桌面(L0) → 软件背景(L1) → 卡片(L2)，只在相邻两层之间做
        一次连续 alpha 混合；每层都只有自己那一张图，不重复贴 slice、不生成
        延展底图，所以不会回到上一轮「两张不相关图硬叠」的分层问题。

        app=0   → 完全不透明：只显示裁剪图（或窗口底色）
        app=100 → 完全让出：只显示桌面快照，而非纯色或自定义背景
        中间值  → 只在 L0/L1 之间线性混合，一次重采样、无第二张图。

        截屏让路期间（_capture_paused）直接返回**屏幕上正在显示的那张 L1**：
        那段时间已经在别人的截图里，每重算一次都可能让画面与「用户按下截图
        键时看到的」产生差异（桌面变了 / 窗口几何变了 / 冻结帧过期）。
        复用同一张图既零差异，也省掉一次抓屏 + 一次合成。
        """
        op = config.get_opacity()["app"]
        name = config.get_app_bg()
        if op <= 0 and not name:
            return None                      # 完全不透明且没图：直接纯色，最省
        if not customize.available():
            return None
        if self._capture_paused:
            frozen = self._app_layer_img
            if frozen is not None and frozen.size == (self.W, self.H):
                return frozen                # 冻结：截图看到的就是屏幕上的样子
        content = (customize.card_bg(name, self.W, self.H,
                                    self.theme.pal.window,
                                    mode=customize.FIT_MODE) if name
                   else customize.solid(self.W, self.H, self.theme.pal.window))
        if content is None:
            content = customize.solid(self.W, self.H, self.theme.pal.window)
        if content is None:
            return None
        opaque = 1.0 - op / 100.0             # 不透明度：0%→1，100%→0
        # 整窗背景自带圆角：Win32 区域裁剪在拖动 / 缩放时可能慢半拍，
        # 让四角直接是桌面画面（完全不透明时退化为窗口底色），方形的
        # 背景图就永远不会从圆角外露出来。两条路径（render / 逐帧换图）
        # 都走 _compose_app_layer，所以这里加一道就都覆盖。
        radius = max(1, int(LAYOUT["radius"] * self.S))
        if opaque >= 0.999:
            return customize.round_window(content, None, radius,
                                          self.theme.pal.window)
        if content.size != (self.W, self.H):
            content = content.resize((self.W, self.H), customize.Image.LANCZOS)
        if self._drag_snapshot is not None:
            base = self._drag_backdrop()     # 拖动中：整屏快照按坐标裁切
        else:
            base = self._grab_backdrop()
        if base is None:
            base = customize.solid(self.W, self.H, self.theme.pal.window)
        out = customize.blend_layer(base, content, opaque)
        return customize.round_window(out, base, radius,
                                      self.theme.pal.window)

    def _layer_key(self) -> str:
        name = config.get_app_bg()
        return (f"applayer:{self.W}x{self.H}:{self.root.winfo_rootx()},"
                f"{self.root.winfo_rooty()}:{config.get_opacity()['app']}:"
                f"{name}:{customize.file_stamp(name) if name else ''}:"
                f"{self._backdrop_epoch}")

    def _tick_backdrop(self):
        """兜底定时链：只在「抓屏排除不可用」时存在。

        那条路径每抓一次都要把窗口隐藏一帧（见 _grab_hidden），做成逐帧会
        肉眼可见地闪，所以这里只续期、不重抓 —— 快照由真实事件作废：
        窗口位置/尺寸变化、面板 show()、拖动松手。等到系统支持
        WDA_EXCLUDEFROMCAPTURE 时，改由 _backdrop_sync 逐帧同步（§3.1.6）。
        """
        if not self._visible:
            self._tick_on = False
            return
        try:
            self.root.after(1500, self._tick_backdrop)
        except Exception:
            self._tick_on = False

    # -- 背景图 -----------------------------------------------------------
    def _photo_for(self, key: str, image):
        """PhotoImage 缓存：同一张图同一尺寸只转一次，省掉每帧的重编码。"""
        if image is None:
            return None
        hit = self._img_cache.get(key)
        if hit is not None:
            return hit
        photo = customize.to_photo(image)
        if photo is not None:
            if len(self._img_cache) > 96:
                # 淘汰规则（两重保护，缺一就会出现「卡片/背景闪白」）：
                #   1. 本帧新建的不淘汰 —— 本帧正在画它；
                #   2. 画布上还挂着的不淘汰 —— 图元持有的是 Tk image name，
                #      Python 对象一旦被 GC，Tk 侧 name 被删，图元就变空白，
                #      用户看到的就是「某张卡偶尔闪一下白」。
                keep = self._frame_new_keys | self._live_photo_keys
                victims = [k for k in self._img_cache if k not in keep][:32]
                for k in victims:
                    self._img_cache.pop(k, None)
            self._img_cache[key] = photo
            self._frame_new_keys.add(key)
        return photo

    def _paint_app_slice(self, x, y, w, h, box):
        """从已合成层裁一条贴到头部/底部，不再二次混合。"""
        if self._app_bg_img is None:
            self.cv.create_rectangle(x, y, x + w, y + h,
                                     fill=self._bg_fill(), outline="")
            return
        key = f"slice:{box}:{self._layer_key()}"
        photo = self._img_cache.get(key)
        if photo is None:
            photo = self._photo_for(
                key, customize.crop(self._app_bg_img, box))
        if photo is None:
            self.cv.create_rectangle(x, y, x + w, y + h,
                                     fill=self._bg_fill(), outline="")
            return
        self.cv.create_image(x, y, image=photo, anchor="nw")

    def _card_layer(self, key: str, x1, y1, x2, y2):
        """卡片背景层 L2：卡片内容按「卡片背景透明度」透出软件背景层 L1。

        base 一律取已合成的 L1 裁片（L1 为空时取窗口底色纯色），不再对设了
        背景图的卡片硬编码 alpha=1.0 + 纯色垫层 —— 那正是滑块失效的根因。

        card=0   → 只显示卡片内容（裁剪图或卡片底色）
        card=100 → 只显示 L1 裁片，完全透出软件背景；软件背景也是 100 时
                   透出的就是桌面，两级串联，没有中间纯色层。
        """
        if not customize.available():
            return None
        w = int(round((x2 - x1) * self.S))
        h = int(round((y2 - y1) * self.S))
        if w < 8 or h < 8:
            return None
        alpha = config.get_alpha("card")      # 1 = 实心，0 = 全透
        name = config.get_card_bg(key)
        if alpha >= 0.999 and not name:
            return None                      # 不透明纯色：照旧画多边形，最省
        # 裁片必须严格对应卡片在窗口中的**真实位置**，滑动时才能与周围
        # 软件背景连续。钳制整块裁片会让卡片显示 L1 顶部/底部的另一处图案
        # （非纯色场景下同 y 行色差可达 67）；越界退回纯色又会造成整卡白块。
        # aligned_crop 按真实坐标取交集，交集外用 L1 边缘像素延展填满，
        # 所以既不会割裂，也不会出现未渲染区域。
        bx = int(round(x1 * self.S))
        by = int(round(y1 * self.S))          # 允许负值 / 越界，不再钳制
        base = None
        if self._app_layer_img is not None:
            base = customize.aligned_crop(self._app_layer_img, bx, by, w, h)
        if base is None:
            base = customize.solid(w, h, self.theme.pal.window)
        # 缓存键用真实物理坐标（含负值）+ 亚像素精度：缓慢滑动时不会
        # 命中上一位置的旧裁片。
        ck = ("cardlayer", key, bx, by, round(y1, 3), w, h,
              round(alpha, 2), name,
              customize.file_stamp(name) if name else "", self._layer_key())
        self._last_card_key = ck        # 快路径换图后要把它记进 live 集合
        hit = self._img_cache.get(ck)
        if hit is not None:
            return hit
        img = customize.card_layer(name, w, h,
                                   int(self.theme.card_radius * self.S),
                                   alpha, base, self.theme.pal.card)
        return self._photo_for(ck, img)

    def _card_bg_photo(self, key: str):
        """兼容旧调用：卡片背景层（含透明度合成）。"""
        rect = self._card_rects.get(key)
        if not rect:
            return None
        return self._card_layer(key, *rect)

    # -- 头部 -------------------------------------------------------------
    def _build_header(self):
        t = self.theme
        cv = self.cv
        W = self.LW
        top = LAYOUT["header_h"]
        # render() 已把合成层整窗铺满；有背景图时不再重复贴 slice，
        # 否则同一区域叠两张图，窗口缩放时会出现上下分层。
        if self._app_bg_img is None:
            bg = self._bg_fill()
            cv.create_rectangle(0, 0, W, top, fill=bg, outline="")
            cv.create_rectangle(0, self.LH + LAYOUT["list_bottom"], W, self.LH,
                                fill=bg, outline="")

        # 右侧三个控件（右对齐，先算它们的位置，再决定标题可用宽度）
        by, bh = LAYOUT["btn_y"], LAYOUT["btn_h"]
        more_w, add_w, ref_w, rnd = 58, 104, 92, 28
        gap = 8
        right = W - LAYOUT["pad"]
        close_x = right - rnd
        min_x = close_x - 6 - rnd
        more_x = min_x - gap - more_w
        add_x = more_x - gap - add_w
        ref_x = add_x - gap - ref_w
        self._btn_close = Button(cv, t, self.S, close_x, by + 2, rnd, rnd,
                                 "✕", self.app.quit, kind="close").draw()
        self._btn_min = Button(cv, t, self.S, min_x, by + 2, rnd, rnd,
                               "−", self.hide, kind="circle").draw()
        self._btn_more = Button(cv, t, self.S, more_x, by, more_w, bh, "设置",
                                self._header_menu, kind="secondary").draw()
        self._btn_add = Button(cv, t, self.S, add_x, by, add_w, bh,
                               "+ 添加设备", self._add_device,
                               kind="secondary").draw()
        self._btn_refresh = Button(cv, t, self.S, ref_x, by, ref_w, bh,
                                   "读取中…" if self.app.busy else "立即刷新",
                                   self._refresh_now,
                                   kind="primary").draw()

        # 标题（右键可改），长标题截断，绝不压到按钮
        title = config.get_title()
        f20 = self._f(20, True)
        limit_px = max(120.0, (ref_x - LAYOUT["pad"] - 16) * self.S)
        title = _ellipsis(title, f20, limit_px)
        tag = "title_btn"
        cv.create_text(*LAYOUT["title_xy"], text=title, anchor="w",
                       fill=rgb(t.pal.text), font=f20, tags=(tag,))
        cv.tag_bind(tag, "<Button-1>", lambda e: self._rename_title())
        cv.tag_bind(tag, "<Button-3>", lambda e: self._title_menu(e))
        cv.tag_bind(tag, "<Enter>", lambda e: cv.configure(cursor="hand2"))
        cv.tag_bind(tag, "<Leave>", lambda e: cv.configure(cursor=""))
        self._title_tag = tag

        # 副标题：N 台 · M 有电量 · 60s 刷新 · HH:MM:SS（字号放大后也要截断）
        sub = _ellipsis(self._summary(), self._f(9), limit_px)
        cv.create_text(*LAYOUT["subtitle_xy"], text=sub,
                       anchor="w", fill=rgb(t.pal.sub), font=self._f(9),
                       tags=("iv_btn",))
        cv.tag_bind("iv_btn", "<Button-1>", lambda e: self._ask_interval())
        cv.tag_bind("iv_btn", "<Enter>",
                    lambda e: cv.configure(cursor="hand2"))
        cv.tag_bind("iv_btn", "<Leave>",
                    lambda e: cv.configure(cursor=""))

    def _summary(self) -> str:
        snap = self.app.snapshot()
        if not snap and not self.app.loaded:
            return "正在读取设备…" if not self.app.busy else "正在读取设备，请稍等…"
        total = len(snap)
        have = sum(1 for r in snap.values() if r.ok and r.level is not None)
        times = [r.ts for r in snap.values() if r.ts]
        stamp = time.strftime("%H:%M:%S", time.localtime(max(times))) if times else "--:--:--"
        tail = " · 已排队再刷一次" if time.time() < self._queued_until else ""
        return (f"{total} 台 · {have} 有电量 · "
                f"{self.app.interval}s 刷新 · {stamp}{tail}")

    def _refresh_now(self):
        try:
            from ..protocols import reset_backoff
            reset_backoff()         # 手动点刷新 = 想真的重试，清掉失败退避
        except Exception:
            pass
        if not self.app.refresh():
            # 上一轮还在跑：记一次排队，本轮结束立刻补跑
            self._queued_until = time.time() + 3.0
            self.root.after(3200, self._render_if_visible)
        self.render()               # 按钮立刻变「读取中…」

    def _render_if_visible(self):
        try:
            if self._visible:
                self.render()
        except Exception:
            pass

    # -- 卡片网格 ---------------------------------------------------------
    def _build_grid(self, snap: dict, keys: list):
        t = self.theme
        cv = self.cv
        W = self.LW
        pad = LAYOUT["pad"]
        gap = LAYOUT["card_gap"]
        x1, x2 = pad, W - pad
        card_w = (x2 - x1 - gap) / 2
        self._card_w = card_w               # 348 那个硬编码的替代品（§3.9）
        step = t.card_h + gap
        list_top = LAYOUT["list_top"]
        list_bottom = self.LH + LAYOUT["list_bottom"]

        # 顺序变化不再动视口：排序不会让当前视口失效，下面已有 clamp 兜底。
        # （以前一改顺序就回顶，拖完手一松列表就跳到最上面。）
        self._order_sig = tuple(keys)

        self._card_rects = {}
        rows = (len(keys) + 1) // 2
        content = rows * step - gap if rows else t.card_h
        self._max_scroll = max(0.0, content - (list_bottom - list_top))
        self._scroll = max(0.0, min(self._scroll, self._max_scroll))

        self._slots = {}
        self._zones = {"top": [], "bottom": []}
        if keys:
            for i, key in enumerate(keys):
                r, c = divmod(i, 2)
                cx1 = x1 + c * (card_w + gap)
                cx2 = cx1 + card_w
                top = list_top + r * step - self._scroll
                bottom = top + t.card_h
                rd = snap.get(key)
                zone = ("top" if (rd and rd.ok and rd.level is not None
                                  and not getattr(rd, "stale", False))
                        else "bottom")
                self._zones[zone].append(key)
                self._slots[key] = (cx1, top)
                if (self._drag is None or key != self._drag) and \
                        bottom >= list_top - t.card_h and top <= list_bottom + gap:
                    self._draw_card(key, cx1, top, cx2, bottom, rd)
        else:
            if not self.app.loaded:
                # 首轮还没回来：写「正在读取…」而不是「还没有设备」，
                # 否则启动瞬间会误报没设备（实际是还没读完）
                cards.render_empty(cv, t, self.S, x1, list_top, x2,
                                   list_top + t.card_h,
                                   title="正在读取设备…",
                                   hint="第一次启动要枚举一遍，通常几秒内出卡片")
            else:
                cards.render_empty(cv, t, self.S, x1, list_top, x2,
                                   list_top + t.card_h)
        self._draw_scrollbar()

    def _draw_card(self, key: str, x1, y1, x2, y2, r: Reading):
        r = r or Reading(ok=False, name="设备")
        self._card_rects[key] = (x1, y1, x2, y2)
        if key == self._flash_key and time.time() < self._flash_until:
            from .draw import draw_round_rect
            draw_round_rect(self.cv, x1 - 1, y1 - 1, x2 + 1, y2 + 1,
                            self.theme.card_radius + 1, fill="",
                            outline=rgb(self.theme.pal.primary), width=2)
        alpha = config.get_alpha("card")          # 1 = 实心，0 = 全透明
        pal = self.theme.pal
        layer = self._card_layer(key, x1, y1, x2, y2)
        if layer is not None:
            fill = ""                              # 颜色在合成图里，多边形只留边
        elif alpha <= 0.01:
            fill = ""                              # 不填充：透出已合成的软件背景
        else:
            fill = customize.blend(pal.card, pal.window, alpha)   # 无 Pillow 兜底
        # 描边始终保留（哪怕很淡），否则卡片边界消失、用户不知道点哪儿
        border = customize.blend(pal.border, pal.window, max(0.15, alpha))
        cards.render_card(
            self.cv, self.theme, self.S, x1, y1, x2, y2, r,
            on_rename=lambda k=key: self._open_rename(k),
            on_fix=lambda k=key: self._open_fix_chooser(k), key=key,
            card_fill=fill, bg_photo=layer,
            bg_is_photo=bool(config.get_card_bg(key)),
            battery_rgb=config.get_device_color(key),
            name_offset=config.get_name_pos(key),
            id_text=config.custom_ids().get(key, ""),
            id_offset=config.get_id_pos(key),
            on_rename_id=lambda k=key: self._open_rename_id(k),
            card_border=border)
        # 记下卡片背景图元，快路径靠它原地换图（不重画整卡、不闪）
        self._card_img.pop(key, None)
        if layer is not None:
            for item in self.cv.find_withtag(f"cardbg:{key}"):
                if self.cv.type(item) == "image":
                    self._card_img[key] = item
                    if self._last_card_key:
                        self._live_photo_keys.add(self._last_card_key)
                    break

    def _draw_scrollbar(self):
        t = self.theme
        x1 = self.LW + LAYOUT["scroll_x"]
        top = LAYOUT["list_top"]
        bottom = self.LH + LAYOUT["list_bottom"]
        bar = ScrollBar(self.cv, t, self.S, x1, top, bottom)
        self._scrollbar = bar
        bar.draw(self._scroll, self._max_scroll, bottom - top)

    # -- 卡片拖动排序 -----------------------------------------------------
    def _start_card_drag(self, key: str, e):
        r = self.app.snapshot().get(key)
        if r is None or key not in self._slots:
            self._press = None
            return
        zone = ("top" if (r.ok and r.level is not None
                          and not getattr(r, "stale", False))
                else "bottom")
        self._drag = key
        self._drag_zone = zone
        self._drag_home = self._zones[zone].index(key) if key in self._zones[zone] else 0
        self._drag_slot = self._drag_home
        self._drag_press = (e.x, e.y)
        self._drag_layout = dict(self._slots)
        self._anim_frames = 0
        self._anim_gen.clear()
        self._dragging = True          # 拖动期间冻结外部 render
        self._render_pending = False
        try:
            self.cv.addtag_withtag("card_dragging", f"card:{key}")
            self.cv.tag_raise("card_dragging")
            self.cv.configure(cursor="fleur")
        except Exception:
            pass
        self._drag_card(e)            # 别等下一帧，按下就开始跟手

    def _drag_card(self, e):
        key = self._drag
        dx = e.x - self._drag_press[0]
        dy = e.y - self._drag_press[1]
        home = self._slots[key]
        target = (home[0] + dx, home[1] + dy)
        cur = self._drag_layout.get(key, home)
        self.cv.move(f"card:{key}", target[0] - cur[0], target[1] - cur[1])
        self._drag_layout[key] = target
        self.cv.tag_raise("card_dragging")

        slot = self._slot_under(e)
        self._cross_zone = self._in_other_zone(e)
        if slot is not None and slot != self._drag_slot:
            self._drag_slot = slot
            self._relayout_zone()

    def _in_other_zone(self, e) -> bool:
        """指针是不是压到了另一个分区的卡片上（跨区拖动要弹回）。"""
        lx, ly = e.x / self.S, e.y / self.S
        other = "bottom" if self._drag_zone == "top" else "top"
        for k in self._zones.get(other, []):
            x1, y1 = self._slots.get(k, (0, 0))
            cw = self._card_w
            if x1 <= lx <= x1 + cw and y1 <= ly <= y1 + self.theme.card_h:
                return True
        return False

    def _shake(self, key: str, frames: int = 4):
        """跨区拖动被拒绝时抖一下，别静默失败。"""
        seq = [5, -5, 3, -3][:frames]

        def step(i=0):
            if i >= len(seq):
                self._render_pending = False
                self.render()
                return
            self.cv.move(f"card:{key}", seq[i], 0)
            self.root.after(40, lambda: step(i + 1))
        step()

    def _slot_under(self, e):
        """指针落在本分区第几个槽位；落在别的分区返回 None（不允许跨区）。"""
        lx, ly = e.x / self.S, e.y / self.S
        gap = LAYOUT["card_gap"]
        step = self.theme.card_h + gap
        zone_keys = self._zones[self._drag_zone]
        for i, k in enumerate(zone_keys):
            if k == self._drag:
                continue
            x1, y1 = self._slots[k]
            if (x1 <= lx <= x1 + self._card_w
                    and y1 <= ly <= y1 + self.theme.card_h):
                return zone_keys.index(k) if k in zone_keys else i
        # 不在任何卡上：按指针所在行估算（仍限制在本分区内）
        rows = {}
        for k in zone_keys:
            rows.setdefault(round(self._slots[k][1]), []).append(k)
        if not rows:
            return None
        best = min(rows, key=lambda y: abs(y - ly))
        if abs(best - ly) > self.theme.card_h:
            return None
        row = sorted(rows[best], key=lambda k: self._slots[k][0])
        half = self._card_w / 2
        idx = 0 if lx < self._slots[row[-1]][0] + half else len(row) - 1
        return zone_keys.index(row[idx])

    def _relayout_zone(self):
        """把分区内除被拖卡片外的其它卡按目标顺序挪到新槽位（带缓动）。"""
        zone = self._zones[self._drag_zone]
        rest = [k for k in zone if k != self._drag]
        order = list(rest)
        order.insert(self._drag_slot, self._drag)
        # 其它卡片按 order 里去掉被拖卡片后的顺序，依次占原槽位
        for i, k in enumerate(order):
            if k == self._drag:
                continue
            slot_key = zone[i]
            tx, ty = self._slots[slot_key]
            self._move_card_to(k, tx, ty)

    def _move_card_to(self, key: str, tx: float, ty: float):
        """开始一条新的让位动画，顺手把这张卡之前的链作废。

        斜向拖动时指针会连着跨好几个槽位，_relayout_zone 会被反复调用；
        没有代际号就会有 N 条 after 链同时 cv.move 同一张卡 → 位移累加、
        互相拉扯，视觉上就是"糊成一团"。
        """
        gen = self._anim_gen.get(key, 0) + 1
        self._anim_gen[key] = gen
        self._anim_step(key, tx, ty, gen, 0)

    def _anim_step(self, key: str, tx: float, ty: float, gen: int, depth: int):
        if self._anim_gen.get(key) != gen:      # 有更新的动画接管了，这条链退出
            return
        cur = self._drag_layout.get(key)
        if cur is None:
            return
        dx, dy = tx - cur[0], ty - cur[1]
        if abs(dx) < 0.6 and abs(dy) < 0.6:
            self._drag_layout[key] = (tx, ty)
            return
        sx, sy = dx * 0.35, dy * 0.35
        self.cv.move(f"card:{key}", sx, sy)
        self._drag_layout[key] = (cur[0] + sx, cur[1] + sy)
        self._anim_frames += 1
        if depth < 40:
            self.root.after(
                16, lambda: self._anim_step(key, tx, ty, gen, depth + 1))

    def _finish_card_drag(self, e=None):
        key, zone, slot = self._drag, self._drag_zone, self._drag_slot

        # 释放点重算必须在清空 _drag 之前：_slot_under() 靠
        # ``k == self._drag`` 跳过被拖的那张卡，先清空会把自己原来的槽位
        # 当成目标，算出来的 slot 整体错一位。
        if e is not None and key:
            if self._in_other_zone(e):
                self._cross_zone = True
            else:
                self._cross_zone = False
                s2 = self._slot_under(e)
                if s2 is not None:
                    slot = s2

        # 先解锁：后面任何一步抛异常都不能让面板永远停在「拖动中不重绘」的状态
        self._dragging = False
        self._drag = None
        self._anim_gen.clear()
        try:
            self.cv.configure(cursor="")
            self.cv.dtag("card_dragging", "card_dragging")
        except Exception:
            pass
        cross = getattr(self, "_cross_zone", False)
        if cross and zone:
            self._cross_zone = False
            self._shake(key)
            return
        if zone and slot is not None and slot != self._drag_home:
            keys = list(self._order_sig or self._slots)
            zk = [k for k in keys if k in self._zones[zone]]
            if key in zk:
                zk.remove(key)
                zk.insert(max(0, min(slot, len(zk))), key)
            it = iter(zk)
            new = [next(it) if k in self._zones[zone] else k for k in keys]
            config.set_card_order(new)
            self.app.reorder_now()          # 立刻重排，别等 60 秒（§4）
            self._order_sig = None
        self._render_pending = False
        self.render()                  # 视口保持原样（_scroll 不动，只 clamp）

    def _card_item_count(self, key: str) -> int:
        return len(self.cv.find_withtag(f"card:{key}"))

    def scroll_to_top(self):
        self._scroll = 0.0

    # -- 滚动 -------------------------------------------------------------
    def _on_wheel(self, e):
        if self._drag:
            return                       # 拖动中禁用滚轮，避免坐标错乱
        if self._max_scroll <= 0:
            return
        self._scroll = max(0.0, min(self._max_scroll,
                                    self._scroll + (-48 if e.delta > 0 else 48)))
        self.render()

    # -- 滚动条（§3.11）----------------------------------------------------
    # 拖动状态放在 panel 上，而不是 Canvas 图元的 tag_bind 上：render() 每帧
    # delete("all") 会把图元连同绑定一起销毁，旧实现拖一下之后就再也收不到
    # <B1-Motion>；同时 _on_press 里没有滚动条这一类别，右槽空白被当成「拖窗口」，
    # 于是同一次按下既拖把手又拖窗口。这里改成 widget 级几何命中，两个问题一起没。
    def _sbar_press(self, e) -> bool:
        bar = self._scrollbar
        if bar is None or self._max_scroll <= 0 or bar.thumb_h <= 0:
            return False
        lx, ly = e.x / self.S, e.y / self.S
        mode = bar.hit(lx, ly)
        if not mode:
            return False
        # 命中滚动条：清掉窗口拖动/卡片拖动候选，保证拖拽只有一个主人
        self._drag_window = None
        self._press = None
        self._name_press = None
        if mode == "track":
            # 点轨道空白：翻一屏（0.9 视口），然后接着从新位置拖
            page = max(1.0, bar.track_h * 0.9)
            self._scroll = max(0.0, min(self._max_scroll,
                                        self._scroll +
                                        (page if ly > bar.thumb_center() else -page)))
            self.render()
        self._bar_drag = (e.y, self._scroll)
        try:
            self.cv.configure(cursor="sb_v_double_arrow")
        except Exception:
            pass
        return True

    def _sbar_move(self, e):
        bar = getattr(self, "_scrollbar", None)
        if self._bar_drag is None or not bar or bar.thumb_h <= 0:
            return
        y0, s0 = self._bar_drag
        travel = max(1.0, bar.track_h - bar.thumb_h)
        dy = (e.y - y0) / self.S
        self._scroll = max(0.0, min(self._max_scroll,
                                    s0 + dy * (self._max_scroll / travel)))
        self.render()

    def _sbar_release(self, e=None):
        self._bar_drag = None
        try:
            self.cv.configure(cursor="")
        except Exception:
            pass

    # -- 菜单（自绘，圆角 + 阴影 + 半透明）---------------------------------
    def _popup(self, items: list, x: int, y: int):
        """统一入口：先关旧菜单，再开一个新的自绘菜单。"""
        self._close_menu()
        menu = PopupMenu(self, items)
        self._menu = menu
        try:
            menu.tk_popup(x, y)
        except Exception:
            _log_menu_error()
            self._menu = None
        return menu

    def _add_bg_menu_item(self, items: list, key: str = ""):
        """「自定义背景…」只占一行：卡片 / 软件在弹窗里再选。"""
        items.append(("自定义背景…", lambda k=key: self._open_bg_picker(k)))

    def _on_right_click(self, e):
        self._close_menu()
        lx, ly = e.x / self.S, e.y / self.S
        for key, (x1, y1, x2, y2) in self._card_rects.items():
            if x1 <= lx <= x2 and y1 <= ly <= y2:
                self._card_menu(key, e.x_root, e.y_root)
                return

    def _card_menu(self, key: str, mx: int, my: int):
        items = [("重命名", lambda k=key: self._open_rename(k))]
        if key.startswith("g:"):
            items.append(("拆开分组", lambda k=key: self.split_group(k)))
        else:
            items.append(("与另一台设备合并…",
                          lambda k=key: self._merge_menu(k, mx, my)))
        self._add_bg_menu_item(items, key)
        if config.get_card_bg(key):
            items.append(("恢复默认卡片背景",
                          lambda k=key: self._clear_card_bg(k)))
        if self._bt_addr_of(key):
            items.append(("手动绑定电量…",
                          lambda k=key: self._open_bt_manual(k)))
        # 设备只给「低/中/高」三格时的人工兜底；读得到真实电量就不出现，
        # 免得把估算值盖到真实读数上。
        _r = self.app.snapshot().get(key)
        _cur = config.manual_level_of(key)
        if (not key.startswith("g:")
                and (_cur is not None
                     or not (_r and _r.ok and _r.level is not None))):
            items.append(None)
            for _label, _lv in (("高 99%", 99), ("中 66%", 66), ("低 33%", 33)):
                items.append((f"手动档位：{_label}",
                              lambda k=key, v=_lv: self._set_manual(k, v)))
            if _cur is not None:
                items.append(("清除手动档位",
                              lambda k=key: self._set_manual(k, None)))
        items.append(None)
        items.append(("删除设备", lambda k=key: self.delete_device(k)))
        self._popup(items, mx, my)

    def _set_manual(self, key: str, level):
        """写入/清除手动档位；立即重读一次让卡片换成新值。"""
        self._close_menu()
        config.set_manual_level(key, level)
        try:
            self.app.refresh()
        except Exception:
            pass
        self.render()

    def _merge_menu(self, key: str, mx: int, my: int):
        snap = self.app.snapshot()
        others = [(k, r) for k, r in snap.items()
                  if k != key and not k.startswith("g:")]
        if not others:
            messagebox.showinfo("合并设备", "没有其它设备可以合并。",
                                parent=self.root)
            return
        items = [(f"{r.name}（{r.transport_text}）",
                  lambda a=key, b=k: self.merge_devices(a, b))
                 for k, r in others]
        self._popup(items, mx, my)

    def merge_devices(self, key_a: str, key_b: str):
        self._close_menu()
        snap = self.app.snapshot()
        ra = snap.get(key_a)
        rb = snap.get(key_b)
        name = (ra.name if ra else "") or (rb.name if rb else "") or "合并设备"
        config.add_group(name, [key_a, key_b])
        self.app.refresh()
        self.render()

    def split_group(self, group_key: str):
        self._close_menu()
        config.remove_group(group_key[2:])
        self.app.refresh()
        self.render()

    def _reset_text_positions(self):
        """把卡片编号与设备昵称一起恢复到卡片内各自的默认位置。"""
        self._close_menu()
        config.reset_text_positions()
        self.render()

    def _header_menu_items(self) -> list:
        items = [("设置刷新间隔…", self._ask_interval), None]
        self._add_bg_menu_item(items)
        if config.get_app_bg():
            items.append(("恢复默认软件背景…", self._clear_app_bg))
        items += [("透明度…", self._open_opacity), ("颜色…", self._open_colors),
                  ("字体大小…", self._open_font),
                  ("恢复默认位置", self._reset_text_positions)]
        items.append(("check", "开机自启", self._startup_var,
                      self._toggle_startup))
        # 用普通菜单项（不是 check 项）：状态直接写在文字里，
        # 既不改变既有「勾选项只有 1 个」的断言，用户也一眼看得出开关状态。
        # 点击后菜单照常收起，下次打开时 _header_menu_items() 按最新状态重建文字。
        # 状态改由各自的开关窗口显示，菜单只留功能名
        items.append(("截图模式", self._open_capture_switch))
        items.append(("自动识别", self._open_auto_switch))
        items += [None, ("更改标题…", self._rename_title),
                  ("恢复默认标题", self._reset_title), None,
                  ("关于 " + config.APP_NAME + "…", self._open_about),
                  ("打开托盘设置", self._open_tray_settings)]
        return items

    def _header_menu(self):
        """在鼠标位置弹出（用户明确要求，别再算按钮几何）。

        PopupMenu._place() 自带翻转 + 夹紧，贴着屏幕边缘也不会跑到外面。
        """
        x, y = self.root.winfo_pointerxy()
        self._popup(self._header_menu_items(), x, y)

    def _title_menu(self, e):
        self._popup([("更改标题…", self._rename_title),
                     ("恢复默认标题", self._reset_title)], e.x_root, e.y_root)

    def _open_about(self):
        self._close_menu()
        from .about import AboutWindow
        if self._dlg is not None:
            try:
                self._dlg.close()
            except Exception:
                pass
        self._dlg = AboutWindow(self)

    def _rename_title(self):
        self._close_menu()
        cur = config.get_title()
        v = simpledialog.askstring("更改标题", "窗口标题：", parent=self.root,
                                   initialvalue=cur)
        if v is None:
            return
        config.set_title(v)
        self.render()

    def _reset_title(self):
        self._close_menu()
        config.set_title("")
        self.render()

    def _open_tray_settings(self):
        self._close_menu()
        open_tray_settings()

    def _close_menu(self):
        m = self._menu
        self._menu = None
        if m is None:
            return
        try:
            m.unpost()
        except Exception:
            pass

    def _clear_menu(self):
        self._menu = None

    def _bt_addr_of(self, key: str) -> str:
        """这张卡背后是不是蓝牙设备；是就返回 MAC，不是返回空串。

        不能只看键名前缀：蓝牙键盘/鼠标常常以 HID 身份被添加（键是
        VID:PID，transport 却是 bt），以前这类卡片右键没有「手动绑定电量…」。
        """
        if key.startswith("g:"):
            members = config.load_groups().get(key[2:], {}).get("members", [])
        else:
            members = [key]
        snap = self.app.snapshot()
        for m in members:
            r = snap.get(m)
            if r is not None and r.addr:
                return r.addr
            if m.startswith("bt:"):
                return config.normalize_addr(m[3:])
        saved = {config.key_for(d): d for d in config.load_saved_devices()}
        for m in members:
            d = saved.get(m)
            if d and str(d.get("kind", "")).lower() == "bt":
                return config.normalize_addr(d.get("addr", ""))
        return ""

    def _open_bt_manual(self, key: str):
        """右键蓝牙卡片 → 有完整步骤引导的手动识别窗口。"""
        self._close_menu()
        from .manual import ChoiceWindow
        dlg = getattr(self, "_dlg", None)
        if isinstance(dlg, ChoiceWindow) and dlg.is_alive():
            dlg.bring_front()
            return
        addr = self._bt_addr_of(key)
        if not addr:
            messagebox.showinfo("手动绑定电量",
                                "这张卡片不是蓝牙设备，无法手动绑定电量。",
                                parent=self.root)
            return
        from .manual import BtManualWindow
        if self._dlg is not None:
            try:
                self._dlg.close()
            except Exception:
                pass
        r = self.app.snapshot().get(key)
        self._dlg = BtManualWindow(self, addr, (r.name if r else "") or "")

    def _open_fix_chooser(self, key: str):
        """卡片「点我解决」→ 先选设备类型，再进对应的手动识别窗口。"""
        self._close_menu()
        from .manual import ChoiceWindow, BtManualWindow
        dlg = getattr(self, "_dlg", None)
        if isinstance(dlg, ChoiceWindow) and dlg.is_alive():
            dlg.bring_front()
            return
        if self._dlg is not None:
            try:
                self._dlg.close()
            except Exception:
                pass

        def pick(kind):
            if kind == "bt":
                addr = self._bt_addr_of(key)
                if not addr:
                    messagebox.showinfo(
                        "手动识别",
                        "这张卡片没有蓝牙地址，请改用「2.4GHz 设备」，\n"
                        "或先在系统蓝牙里连上这台设备。", parent=self.root)
                    return
                r = self.app.snapshot().get(key)
                self._dlg = BtManualWindow(
                    self, addr, (r.name if r else "") or "")
            else:
                self.open_help(key)

        self._dlg = ChoiceWindow(
            self, "这台设备是哪一类？",
            "选完会打开对应的手动识别窗口", pick)

    def _open_unknown_chooser(self):
        """「添加设备」→「添加未识别设备…」：软件完全没读到信息的设备。"""
        self._close_menu()
        from .manual import ChoiceWindow, AddUnknownWindow
        dlg = getattr(self, "_dlg", None)
        if isinstance(dlg, ChoiceWindow) and dlg.is_alive():
            dlg.bring_front()
            return
        if self._dlg is not None:
            try:
                self._dlg.close()
            except Exception:
                pass

        def pick(kind):
            self._dlg = AddUnknownWindow(self, kind)

        self._dlg = ChoiceWindow(
            self, "要添加哪一类设备？",
            "软件完全没发现它时，在这里手动加一张卡片", pick)

    def delete_device(self, key: str):
        self._close_menu()
        try:
            if key.startswith("g:"):
                config.remove_group(key[2:])
            else:
                name = config.get_card_bg(key)
                if name:
                    customize.remove_media(name)
                    config.clear_card_bg(key)
                config.clear_device_color(key)      # §3.3
                config.clear_name_pos(key)          # §3.10
                self.app.remove_device(key)
                config.ignore_device(key)   # 删过就不再自动冒出来
            config.prune_card_order(list(self.app.snapshot().keys()))
            self._scroll = 0.0
            self.render()
        except Exception:
            pass

    # -- 帮助 / 抓包 ------------------------------------------------------
    def open_help(self, key=None):
        old = getattr(self, "_help_win", None)
        if old is not None:
            if old.is_alive():
                if getattr(old, "key", None) == key:
                    old.bring_front()      # 同一台设备已开着 → 提到前面
                    return
                try:
                    old.close()
                except Exception:
                    pass
            self._help_win = None
        from .help import HelpWindow
        self._help_win = HelpWindow(self, key)

    def _close_help(self):
        hw = getattr(self, "_help_win", None)
        if hw:
            try:
                hw.close()
            except Exception:
                pass
        self._help_win = None

    # -- 设置 -------------------------------------------------------------
    def _toggle_startup(self):
        try:
            if self._startup_var:
                remove_startup()
                self._startup_var = False
            else:
                self._startup_var = install_startup()
            self.render()
        except Exception as e:
            messagebox.showerror("开机自启", f"设置失败：{e}")

    def _ask_interval(self):
        """手填刷新间隔（秒）。取消/关闭弹窗什么都不改。"""
        v = simpledialog.askinteger(
            "刷新间隔", "每多少秒刷新一次电量？\n范围 30–1800 秒",
            parent=self.root, initialvalue=self.app.interval,
            minvalue=30, maxvalue=1800)
        if v is None:
            return
        self.app.set_interval(v)
        self.render()

    # -- 添加设备 / 托盘设备菜单 -------------------------------------------
    def _add_device(self):
        """「+ 添加设备」：枚举放工作线程，主线程只负责弹菜单。

        `providers.enumerate_refs(include_bt=True)` 里的 BLE 枚举走 WinRT，
        设备半死不活时最长阻塞 4 秒（providers/bt.py 的 ENUM_TIMEOUT）。
        这一步以前直接在 Tk 主线程跑，点一下菜单界面整块冻住（红线 #1）。

        跨线程只走 queue（红线 #4）：工作线程只 put，主线程用 after 轮询取，
        绝不从子线程调 Tk。
        """
        if getattr(self, "_add_dev_busy", False):
            return
        self._add_dev_busy = True

        def work():
            refs = []
            try:
                from .. import providers
                refs = providers.enumerate_refs(None, include_bt=True)
            except Exception:
                refs = []
            self._add_dev_q.put(refs)

        threading.Thread(target=work, daemon=True,
                         name="add-device-enum").start()
        self._poll_add_device()

    def _poll_add_device(self):
        """主线程：等枚举结果（队列空了就 50ms 后再问一次）。"""
        try:
            refs = self._add_dev_q.get_nowait()
        except queue.Empty:
            try:
                self.root.after(50, self._poll_add_device)
            except Exception:
                self._add_dev_busy = False
            return
        self._add_dev_busy = False
        self._add_device_menu(refs)

    def _add_device_menu(self, refs):
        """主线程：用枚举结果构建「添加设备」菜单（不遗漏、不过滤）。

        以前只从 snapshot() 里挑「未保存且非分组」的项：蓝牙设备若没读到
        电量服务、或已被合并成分组卡片，就会从菜单里消失（G517KL 即如此）。
        现在以 providers 枚举到的设备引用为主，联合读数快照与分组成员，
        已添加的也照样列出并标注，缺读数的用枚举信息补全。
        """
        snap = self.app.snapshot()
        saved_list = config.load_saved_devices()
        saved = {config.key_for(d) for d in saved_list}
        by_key = {config.key_for(d): d for d in saved_list}

        cand: dict[str, Reading] = {}
        for ref in refs or []:
            r = snap.get(ref.key)
            if r is None:
                r = Reading(ok=False, present=bool(ref.connected),
                            name=(ref.name or ref.product or ref.addr
                                  or f"{ref.vid:04X}:{ref.pid:04X}"),
                            transport=ref.transport, addr=ref.addr,
                            vid=ref.vid, pid=ref.pid)
            cand[ref.key] = r

        for key, r in snap.items():
            if not key.startswith("g:"):
                cand.setdefault(key, r)

        # 被合并成分组卡片的成员同样是已识别设备，展开避免被吞掉
        for _gid, g in config.load_groups().items():
            for member in (g.get("members") or []):
                if member in cand:
                    continue
                if member in snap:
                    cand[member] = snap[member]
                    continue
                d = by_key.get(member)
                if not d:
                    continue
                is_bt = str(d.get("kind", "")).lower() == "bt"
                cand[member] = Reading(
                    ok=False, present=False, name=d.get("name") or member,
                    transport="bt" if is_bt else "hid",
                    addr=config.normalize_addr(d.get("addr", "")),
                    vid=int(d.get("vid", 0) or 0), pid=int(d.get("pid", 0) or 0))

        # 「添加未识别设备」始终在菜单最上面：软件完全没发现设备时，
        # 恰恰要靠这个入口手动加，不能因为 cand 为空就把整个菜单关掉。
        items = [("添加未识别设备…", self._open_unknown_chooser), None]
        if not cand:
            items.append(("暂时没发现新设备 · 可用上面那项手动加",
                          lambda: None))
        for key, r in sorted(cand.items(),
                             key=lambda kv: (kv[1].name or kv[0]).lower()):
            label = (f"{r.name or key}（{r.transport_text}）"
                     + (" · 已添加" if key in saved else ""))
            if key in saved:
                items.append((label, lambda: messagebox.showinfo(
                    "添加设备", "该设备已在设备列表中。", parent=self.root)))
            else:
                items.append((label, lambda a=key, b=r:
                              self.app.add_device_by_key(a, b)))
        x, y = self.root.winfo_pointerxy()
        self._popup(items, x, y)

    def popup_devices(self):
        """托盘右键：在鼠标位置弹设置菜单（和右上角「设置」同一份内容）。

        show() 不能省：托盘右键后要先亮面板，否则没有前台父窗口。
        """
        self.show()
        x, y = self.root.winfo_pointerxy()
        self._popup(self._header_menu_items(), x, y)

    # -- 背景 / 透明度 / 颜色 ---------------------------------------------
    def _open_bg_picker(self, key: str = ""):
        """「自定义背景…」：一个弹窗两个按钮，卡片背景 / 软件背景。

        key 非空（从某张卡右键进来）时默认就作用于这张卡，不用再选一遍设备。
        """
        self._close_menu()
        from . import dialogs
        if self._dlg is not None:
            try:
                self._dlg.close()
            except Exception:
                pass
        self._dlg = dialogs.BackgroundDialog(self, key)

    def _open_image(self, path: str):
        """打开成 PIL Image（裁剪用）。失败把原因写进 customize.last_error()。"""
        try:
            im = customize.Image.open(path)
            # 先按文件头的尺寸挡一次，再 load()：100MPix 的图解码要 300MB，
            # 以前是「先 load 后判 6000」——限流发生在内存峰值之后。
            if im.size[0] * im.size[1] > customize.MAX_DECODE_PIXELS:
                customize.set_last_error(
                    f"图片太大（{im.size[0]}×{im.size[1]} = "
                    f"{im.size[0]*im.size[1]/1e6:.0f} 百万像素），请换一张小一些的图")
                return None
            im.load()
            im = im.convert("RGB")
            if max(im.size) > 6000:            # 极端大图先压一下，裁剪仍够用
                im.thumbnail((6000, 6000), customize.Image.LANCZOS)
            return im
        except Exception as e:
            customize.set_last_error(f"{type(e).__name__}: {e}")
            return None

    def _open_crop(self, img, target, on_done):
        from . import crop as crop_mod
        if self._dlg is not None:
            try:
                self._dlg.close()
            except Exception:
                pass
        self._dlg = crop_mod.CropDialog(self, img, target, on_done)

    # -- 可选依赖：自动安装（§3.4）----------------------------------------
    def offer_install(self, dep: str, title: str = "", then=None) -> None:
        """缺 Pillow / winrt 时不再让用户自己去敲 pip，问一句就在后台装。

        装到 %APPDATA%\\SparkMonitor\\vendor（用户级，不要管理员），装完自动
        重新挂载模块，然后接着执行用户原本要做的事（then）。
        """
        from .. import deps
        if deps.have(dep):
            if then:
                then()
            return
        hint = (customize.install_hint() if dep == "Pillow" else
                "读取 BLE 蓝牙电量需要 winrt 组件（Windows 运行时接口）。\n"
                "程序会自动装到用户目录，不需要管理员权限，也不改系统 Python。")
        try:
            yes = messagebox.askyesno(
                title or dep,
                f"{hint}\n\n现在自动安装吗？\n"
                f"（需要联网一次，通常 10–60 秒；装好后立即生效）",
                parent=self.root)
        except Exception:
            yes = False
        if not yes:
            return
        self._start_dep_install(dep, then)

    def _start_dep_install(self, dep: str, then=None) -> None:
        from .. import deps
        import queue as _q
        import tkinter as tk
        win = tk.Toplevel(self.root)
        win.title(f"安装 {dep}")
        try:
            x = max(0, self.root.winfo_rootx() + (self.W - 380) // 2)
            y = max(0, self.root.winfo_rooty() + (self.H - 130) // 2)
            win.geometry(f"380x130+{x}+{y}")
            win.resizable(False, False)
            win.transient(self.root)
            win.attributes("-topmost", True)
        except Exception:
            pass
        lbl = tk.Label(win, text=f"正在准备安装 {dep}…", anchor="w",
                       justify="left", wraplength=340)
        lbl.pack(fill="both", expand=True, padx=16, pady=16)
        q: _q.Queue = _q.Queue()

        def progress(_stage: str, msg: str) -> None:
            q.put(msg)                       # 只入队，绝不直接碰 Tk

        def done(ok: bool) -> None:
            q.put(("__done__", bool(ok)))

        def finish(ok: bool) -> None:
            try:
                win.destroy()
            except Exception:
                pass
            self._finish_dep_install(dep, ok, then)

        def poll() -> None:
            try:
                while True:
                    msg = q.get_nowait()
                    if isinstance(msg, tuple) and msg and msg[0] == "__done__":
                        finish(bool(msg[1]))
                        return
                    try:
                        lbl.configure(text=str(msg))
                    except Exception:
                        pass
            except _q.Empty:
                pass
            try:
                self.root.after(150, poll)
            except Exception:
                pass

        deps.install_async([dep], done=done, progress=progress)
        self.root.after(150, poll)

    def _finish_dep_install(self, dep: str, ok: bool, then=None) -> None:
        from .. import deps
        if not ok:
            messagebox.showerror(
                f"安装 {dep} 失败",
                f"{deps.last_error() or '未知原因'}\n\n"
                f"日志：{deps.log_path()}\n"
                f"也可以手动执行：python -m pip install --user "
                f"{' '.join(deps.PACKAGES.get(dep, [dep]))}",
                parent=self.root)
            return
        if dep == "Pillow":
            customize.reload()
            self._img_cache.clear()
        elif dep == "winrt":
            try:
                from ..providers import bt as _bt
                _bt.reset()                  # 清掉「不可用」缓存，否则装了也白装
            except Exception:
                pass
            try:
                self.app.refresh()
            except Exception:
                pass
        deps.reset_cache()
        self.render()
        messagebox.showinfo(f"{dep} 已就绪",
                            f"{dep} 安装完成，功能已生效。", parent=self.root)
        if then:
            try:
                then()
            except Exception:
                pass

    def _pick_card_bg(self, key: str):
        if not customize.available():
            self.offer_install("Pillow", "卡片背景",
                               then=lambda k=key: self._pick_card_bg(k))
            return
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self.root, title="选择卡片背景",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"),
                       ("所有文件", "*.*")])
        if not path:
            return
        img = self._open_image(path)
        if img is None:
            messagebox.showerror("卡片背景",
                                 _import_error_text(path), parent=self.root)
            return
        # 输出必须是物理像素：逻辑坐标 × S 才是卡片在屏幕上的真实尺寸，
        # 否则 125%/150% 下拿小图放大，糊且容易错位。
        target = (max(64, int(round(self._card_w * self.S))),
                  max(64, int(round(self.theme.card_h * self.S))))
        self._open_crop(img, target,
                        lambda im, k=key: self._apply_card_bg(k, im))

    def _apply_card_bg(self, key: str, img):
        name = f"card_{config.safe_key(key)}.png"
        fn = customize.import_image_pil(img, name)
        if not fn:
            messagebox.showerror("卡片背景",
                                 f"裁剪结果没能保存。\n\n"
                                 f"{customize.last_error() or '未知原因'}",
                                 parent=self.root)
            return
        config.set_card_bg(key, fn)
        customize.clear_cache()
        self._settings_changed()

    def _clear_card_bg(self, key: str):
        self._close_menu()
        name = config.get_card_bg(key)
        config.clear_card_bg(key)
        if name:
            customize.remove_media(name)
        customize.clear_cache()
        self._settings_changed()

    def _pick_app_bg(self):
        self._close_menu()
        if not customize.available():
            self.offer_install("Pillow", "软件背景", then=self._pick_app_bg)
            return
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self.root, title="选择软件背景",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"),
                       ("所有文件", "*.*")])
        if not path:
            return
        img = self._open_image(path)
        if img is None:
            messagebox.showerror("软件背景",
                                 _import_error_text(path), parent=self.root)
            return
        # 整窗背景同理：W/H 已是物理像素，LW/LH 是逻辑像素。
        target = (max(64, self.W), max(64, self.H))
        self._open_crop(img, target, self._apply_app_bg)

    def _apply_app_bg(self, img):
        fn = customize.import_image_pil(img, "app_bg.png")
        if not fn:
            messagebox.showerror("软件背景",
                                 f"裁剪结果没能保存。\n\n"
                                 f"{customize.last_error() or '未知原因'}",
                                 parent=self.root)
            return
        config.set_app_bg(fn)
        customize.clear_cache()
        self._settings_changed()

    def _clear_app_bg(self):
        self._close_menu()
        customize.remove_media(config.get_app_bg())
        config.set_app_bg("")
        customize.clear_cache()
        self._settings_changed()

    def _open_opacity(self):
        self._close_menu()
        from . import dialogs
        if self._dlg is not None:
            try:
                self._dlg.close()
            except Exception:
                pass
        self._dlg = dialogs.OpacityDialog(self)

    def _open_colors(self):
        self._close_menu()
        if not customize.available():
            self.offer_install("Pillow", "颜色", then=self._open_colors)
            return
        from . import dialogs
        if self._dlg is not None:
            try:
                self._dlg.close()
            except Exception:
                pass
        self._dlg = dialogs.ColorDialog(self)

    def _open_font(self):
        """字体大小：不需要 Pillow，异常也要能开。"""
        self._close_menu()
        from . import dialogs
        if self._dlg is not None:
            try:
                self._dlg.close()
            except Exception:
                pass
        self._dlg = dialogs.FontDialog(self)

    # -- 定位到某张卡（托盘菜单用）----------------------------------------
    def reveal(self, key: str):
        """把某台设备的卡片滚到视野里并闪一下。"""
        keys = list(self.app.snapshot().keys())
        if key not in keys:
            return
        idx = keys.index(key)
        row = idx // 2
        gap = LAYOUT["card_gap"]
        top = LAYOUT["list_top"] + row * (self.theme.card_h + gap)
        view_h = (self.LH + LAYOUT["list_bottom"]) - LAYOUT["list_top"]
        if top < LAYOUT["list_top"] or top + self.theme.card_h > LAYOUT["list_top"] + view_h:
            self._scroll = max(0.0, min(self._max_scroll,
                                        top - LAYOUT["list_top"]))
        self._flash_key = key
        self._flash_until = time.time() + 1.2
        self.show()


def _log_menu_error() -> None:
    """菜单弹不出来时落日志：pythonw 没有 stderr，静默吞异常等于无法排查。"""
    try:
        d = config.config_dir()
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "menu_error.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S]\n"))
            traceback.print_exc(file=f)
    except Exception:
        pass


def _import_error_text(path: str) -> str:
    """把具体原因摊开给用户看，而不是笼统的「打不开」。"""
    reason = customize.last_error() or "未知原因"
    name = os.path.basename(path or "")
    return f"这张图没能导入：{name}\n\n{reason}"


def _ellipsis(text: str, font, limit_px: float) -> str:
    """按实际像素宽度截断，超出补省略号。"""
    if not text:
        return ""
    import tkinter.font as tkfont
    f = tkfont.Font(font=font)
    if f.measure(text) <= limit_px:
        return text
    out = text
    while out and f.measure(out + "…") > limit_px:
        out = out[:-1]
    return out + "…"


def open_tray_settings() -> None:
    try:
        os.startfile("ms-settings:taskbar")
    except Exception:
        pass
