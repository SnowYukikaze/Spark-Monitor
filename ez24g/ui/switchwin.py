# -*- coding: utf-8 -*-
"""两个独立的开关小窗：截图模式 / 自动识别。

无边框圆角，风格跟主面板一致；点滑块切换，带一小段滑动动画。
窗口上写明「这个功能是干什么的」和「开启之后的不利影响」。
"""
from __future__ import annotations

import tkinter as tk

from .. import config
from ..win32 import set_round_region
from .draw import draw_round_rect, rgb, vcenter_text
from .theme import get_theme

LW, LH = 420, 282
_TITLE_H = 40

CAPTURE_TEXT = (
    "开启后，本程序窗口能被系统截图、第三方截图和录屏正常拍到。",
    "开启期间背景会冻结，不再实时跟随桌面；用完记得关掉。",
)
AUTO_TEXT = (
    "自动尝试已知的 2.4G 厂商读法，让更多设备显示电量。",
    "每轮刷新会稍慢一点；少数设备偶尔会读到不稳定的状态。",
)


class SwitchWindow:
    """一个开关窗口。state() 读当前开关，toggle() 负责切换。"""

    def __init__(self, panel, title: str, caption: str, downside: str,
                 state, toggle):
        self.panel = panel
        self.title = title
        self.caption = caption
        self.downside = downside
        self.state = state
        self.toggle = toggle
        self.S = min(getattr(panel, "S", 1.0), 1.35)
        self.theme = get_theme(panel.app.theme_light)
        self.W, self.H = int(LW * self.S), int(LH * self.S)
        self._pos = 1.0 if state() else 0.0   # 滑块位置 0..1（动画用）
        self._job = None
        self._press_pos = None

        self.root = tk.Toplevel(panel.root)
        self.root.overrideredirect(True)
        self.root.configure(bg=rgb(self.theme.pal.window))
        self.cv = tk.Canvas(self.root, width=self.W, height=self.H,
                            highlightthickness=0, bd=0,
                            bg=rgb(self.theme.pal.card))
        self.cv.pack()
        self.root.update_idletasks()
        self.hwnd = self.root.winfo_id()
        set_round_region(self.hwnd, self.W, self.H, int(20 * self.S))
        self._place()
        self.cv.bind("<Button-1>", self._click)
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonRelease-1>",
                     lambda e: setattr(self, "_press_pos", None))
        self.root.bind("<Escape>", lambda e: self.close())
        self.draw()
        self.root.transient(panel.root)
        from .ontop import raise_above
        raise_above(self.root, panel.root.winfo_id())

    def _place(self):
        sw, sh = (self.root.winfo_screenwidth(),
                  self.root.winfo_screenheight())
        try:
            px = self.panel.root.winfo_rootx()
            py = self.panel.root.winfo_rooty()
            pw = self.panel.root.winfo_width()
            ph = self.panel.root.winfo_height()
        except Exception:
            px = py = pw = ph = 0
        x = px - self.W - 12
        if x < 8:
            x = min(max(8, px + (pw - self.W) // 2), sw - self.W - 8)
        y = min(max(8, py + (ph - self.H) // 2), sh - self.H - 48)
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")

    # -- 绘制 -------------------------------------------------------------
    def _wrap(self, text: str, width: int = 27):
        """中文按字数折行，够用且不依赖字体测量。"""
        out, line = [], ""
        for ch in text:
            line += ch
            if len(line) >= width:
                out.append(line)
                line = ""
        if line:
            out.append(line)
        return out

    def draw(self):
        t = self.theme
        cv = self.cv
        cv.delete("all")
        draw_round_rect(cv, 0, 0, LW, LH, 16,
                        fill=rgb(t.pal.card), outline=rgb(t.pal.border))
        cv.create_text(22, 24, text=self.title, anchor="w",
                       fill=rgb(t.pal.text), font=t.f(12, True, self.S))
        cv.create_line(22, 46, LW - 22, 46, fill=rgb(t.pal.border))
        self._close_x()

        # 作用
        cv.create_text(22, 66, text="作用", anchor="w",
                       fill=rgb(t.pal.sec_text), font=t.f(9, True, self.S))
        y = 86
        for line in self._wrap(self.caption):
            cv.create_text(22, y, text=line, anchor="w",
                           fill=rgb(t.pal.text), font=t.f(9, False, self.S))
            y += 19

        # 开启后的不利影响
        y += 10
        cv.create_text(22, y, text="开启后的不利影响", anchor="w",
                       fill=rgb(t.pal.sec_text), font=t.f(9, True, self.S))
        y += 20
        for line in self._wrap(self.downside):
            cv.create_text(22, y, text=line, anchor="w",
                           fill=rgb(t.pal.sec_text), font=t.f(9, False, self.S))
            y += 19

        # 开关行
        ty = LH - 74
        draw_round_rect(cv, 22, ty, LW - 22, ty + 52, 10,
                        fill=rgb(t.pal.sec_bg), outline=rgb(t.pal.sec_border))
        cv.create_text(36, ty + 26, text="开 / 关", anchor="w",
                       fill=rgb(t.pal.text), font=t.f(10, True, self.S))
        on = self._pos > 0.5
        cv.create_text(LW - 108, ty + 26,
                       text="已开启" if on else "已关闭", anchor="e",
                       fill=rgb(t.pal.text), font=t.f(9, False, self.S))
        self._switch(ty + 26)

        cv.scale("all", 0, 0, self.S, self.S)

    def _close_x(self):
        t = self.theme
        x1, y1 = LW - 30, 16
        self.cv.create_line(x1, y1, x1 + 10, y1 + 10,
                            fill=rgb(t.pal.sec_text), tags=("sw_close",))
        self.cv.create_line(x1 + 10, y1, x1, y1 + 10,
                            fill=rgb(t.pal.sec_text), tags=("sw_close",))

    def _switch(self, cy):
        """圆角轨道 + 圆形滑块；开启用主色，关闭用灰。"""
        t = self.theme
        x2, w, h = LW - 36, 66, 30
        x1 = x2 - w
        on_color = (rgb(t.pal.accent) if self._pos > 0.5
                    else rgb(t.pal.border))
        draw_round_rect(self.cv, x1, cy - h / 2, x2, cy + h / 2, h / 2,
                        fill=on_color, outline="")
        r = 11
        cx = x1 + r + 3 + (w - 2 * r - 6) * self._pos
        self.cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                            fill="#ffffff", outline=rgb(t.pal.sec_border),
                            width=1, tags=("sw_toggle",))
        # 命中区放大一点，整行右侧都能点
        self.cv.create_rectangle(x1 - 12, cy - 20, x2 + 6, cy + 20,
                                 outline="", fill="", tags=("sw_toggle",))

    # -- 交互 -------------------------------------------------------------
    def _hit(self, e, tag=None):
        """逻辑坐标命中（绘制时整体 cv.scale 过，事件坐标是物理像素）。"""
        x, y = e.x / self.S, e.y / self.S
        if tag is None:
            return (24 <= x <= LW - 24 and LH - 96 <= y <= LH - 20)
        if tag == "sw_close":
            return LW - 38 <= x <= LW - 12 and 8 <= y <= 34
        return False

    def _click(self, e):
        if self._hit(e, "sw_close"):
            self.close()
            return
        if self._hit(e):
            try:
                self.toggle()
            except Exception:
                pass
            self._animate(1.0 if self.state() else 0.0)
            return
        self._press_pos = (e.x_root, e.y_root,
                           self.root.winfo_x(), self.root.winfo_y())

    def _drag(self, e):
        if not self._press_pos:
            return
        x0, y0, wx, wy = self._press_pos
        self.root.geometry(f"+{wx + e.x_root - x0}+{wy + e.y_root - y0}")

    def _animate(self, target):
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

        def step(i):
            if not self.root.winfo_exists():
                return
            self._pos += (target - self._pos) * 0.45
            if abs(target - self._pos) < 0.02:
                self._pos = target
            self.draw()
            if self._pos != target and i < 8:
                self._job = self.root.after(16, lambda: step(i + 1))
            else:
                self._job = None

        step(0)

    def close(self):
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass
        if getattr(self.panel, "_dlg", None) is self:
            self.panel._dlg = None
        try:
            self.panel._backdrop_epoch += 1
            self.panel.render()
        except Exception:
            pass


def open_switch(panel, kind: str):
    """打开（或提现）对应开关窗口；同一时刻只留一个。"""
    old = getattr(panel, "_dlg", None)
    if old is not None and getattr(old, "_switch_kind", "") == kind:
        try:
            old.root.lift()
        except Exception:
            pass
        return old
    if old is not None:
        try:
            old.close()
        except Exception:
            pass
    if kind == "capture":
        win = SwitchWindow(panel, "截图模式", *CAPTURE_TEXT,
                           state=lambda: bool(panel._manual_capture_mode),
                           toggle=panel.toggle_capture_mode)
    else:
        win = SwitchWindow(panel, "自动识别", *AUTO_TEXT,
                           state=config.vendor_probe_enabled,
                           toggle=panel.toggle_vendor_probe)
    win._switch_kind = kind
    panel._dlg = win
    return win
