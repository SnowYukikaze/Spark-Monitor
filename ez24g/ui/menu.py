# -*- coding: utf-8 -*-
"""自绘浮动菜单：圆角 + 阴影 + 半透明背景，替代原生 tk.Menu。

原生 Menu 只能改 bg/fg，画不出圆角、阴影、毛玻璃，跟圆角浅色面板不搭。
这里用 Toplevel + Canvas 自己画，接口刻意保留了 `tk_popup / unpost`，
调用方（以及自检脚本）不用改写法：

    PopupMenu(panel, [("重命名", fn), None, ("删除设备", fn)]).tk_popup(x, y)

items 支持三种写法：
    (label, command)             普通项
    ("check", label, on, command) 带勾选项
    None                          分隔线
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

from .. import config
from ..win32 import set_acrylic, set_round_region, user32
from . import customize
from . import theme as theme_mod
from .draw import draw_round_rect, rgb
from .ontop import raise_above

ITEM_H = 32
SEP_H = 9
PAD_X = 14
RADIUS = 10
MENU_ALPHA = 0.96          # 菜单不透明度固定，用户不再能调（2026-11 需求）


class _ShadowTarget:
    """ShadowWindow 只用到这几个属性，菜单借它复用同一套阴影实现。"""

    def __init__(self, w, h, s, hwnd):
        self.W, self.H, self.S = w, h, s
        self.hwnd = hwnd
        self._visible = True


class PopupMenu:
    """一次性菜单：构造后调 tk_popup(x, y) 显示，点外面 / Esc / 选中都会关闭。"""

    def __init__(self, panel, items=None, theme=None):
        self.panel = panel
        self.app = panel.app
        # 菜单可能先于面板重绘打开，字号得自己先同步一次（§3.8）
        theme_mod.set_font_scale(config.get_font_scale())
        self.S = max(1.0, panel.S)
        self.theme = theme or panel.theme
        self.items = list(items or [])
        self.root = None
        self.acrylic_ok = False
        self._rows = []              # [(kind, y1, y2, label, command, on)]
        self._hover = -1
        self._shadow = None
        self._shadow_job = None
        self._watch_job = None
        self._armed = False           # 只认「开菜单那一下松开之后」的新按下
        self._on_close = []

    # -- 兼容 tk.Menu 的最小接口 -----------------------------------------
    def index(self, which="end") -> int:
        return len(self.items) - 1

    def entrycget(self, i: int, key: str) -> str:
        if not (0 <= i < len(self.items)):
            raise tk.TclError("bad index")
        item = self.items[i]
        if item is None:
            if key == "label":
                return "---"          # 自检脚本靠这个数项，分隔线也要能取
            raise tk.TclError("separator")
        if key == "label":
            return item[1] if item[0] == "check" else item[0]
        raise tk.TclError(key)

    def bind(self, seq: str, func, add=None):
        if seq in ("<Unmap>", "<Destroy>"):
            self._on_close.append(func)

    def unpost(self):
        self._close()

    def destroy(self):
        self._close()

    # -- 显示 -------------------------------------------------------------
    def tk_popup(self, x: int = 0, y: int = 0, entry: str = ""):
        self.popup(x, y)

    def popup(self, x: int = 0, y: int = 0):
        self._x, self._y = int(x), int(y)
        self._armed = False           # 开菜单这一击还按着，不能被 _watch 当成点外
        self.root = tk.Toplevel(self.panel.root)
        self.root.overrideredirect(True)
        self.root.configure(bg=rgb(self.theme.pal.card))
        try:
            self.root.transient(self.panel.root)     # owner：永远在面板之上
        except Exception:
            pass
        self.cv = tk.Canvas(self.root, width=10, height=10,
                            highlightthickness=0, bd=0,
                            bg=rgb(self.theme.pal.card))
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Motion>", self._motion)
        self.cv.bind("<Button-1>", self._click)
        self.cv.bind("<ButtonRelease-1>", lambda e: None)
        self.root.bind("<Escape>", lambda e: self._close())
        self._draw()
        self._place()
        raise_above(self.root, self.panel.root.winfo_id())
        # 不 focus_force、不绑 FocusOut：抢焦点会让面板掉激活，
        # 焦点一回到面板菜单就自毁 —— 那就是「点了设置没反应」。
        self._shadow_job = self.root.after(90, self._add_shadow)
        self._watch_job = self.root.after(120, self._watch)

    # -- 关闭（轮询点外，不用焦点事件）------------------------------------
    def _cursor(self):
        try:
            return self.root.winfo_pointerx(), self.root.winfo_pointery()
        except Exception:
            return -1, -1

    def _inside(self, x: int, y: int, pad: int = 0) -> bool:
        try:
            rx = self.root.winfo_rootx() - pad
            ry = self.root.winfo_rooty() - pad
            return (rx <= x <= rx + self.root.winfo_width() + 2 * pad
                    and ry <= y <= ry + self.root.winfo_height() + 2 * pad)
        except Exception:
            return False

    def _watch(self):
        """不抢焦点，靠轮询关菜单：只认「新的一次按下」且在菜单外才关。

        开菜单那一击左键通常还按着，若直接用 down 判定，光标因取整落在
        菜单外 1px 就会在 120ms 后把刚开的菜单关掉（表现为点了没反应）。
        面板上的点击仍由 _on_press 负责关菜单，这里只管点到桌面/别的程序。
        """
        self._watch_job = None
        if self.root is None:
            return
        try:
            down = bool(user32.GetAsyncKeyState(0x01) & 0x8000)   # VK_LBUTTON
            if not down:
                self._armed = True        # 开菜单那一下已松开，之后才允许判定
            elif self._armed and not self._inside(*self._cursor(), pad=6):
                self._close()
                return
        except Exception:
            pass
        try:
            self._watch_job = self.root.after(120, self._watch)
        except Exception:
            self._watch_job = None

    def _menu_w(self) -> int:
        f = tkfont.Font(font=self.theme.f(9, False, self.S))
        labels = []
        for it in self.items:
            if it is None:
                continue
            labels.append(it[1] if it[0] == "check" else it[0])
        w = max([f.measure(t) for t in labels] or [80])
        w += int(PAD_X * 2 * self.S)
        if any(it and it[0] == "check" for it in self.items):
            w += int(18 * self.S)            # 勾选位
        return max(int(120 * self.S), min(w, int(420 * self.S)))

    def _menu_h(self) -> int:
        h = 2 * int(6 * self.S)
        for it in self.items:
            h += int(SEP_H * self.S) if it is None else int(ITEM_H * self.S)
        return h

    def _bg(self):
        """菜单底色：固定不透明度，跟窗口底色混合（不再读 ui.opacity.menu）。"""
        return customize.blend(self.theme.pal.card, self.theme.pal.window,
                               MENU_ALPHA)

    def _draw(self):
        t = self.theme
        pal = t.pal
        s = self.S
        W, H = self._menu_w(), self._menu_h()
        cv = self.cv
        cv.configure(width=W, height=H, bg=rgb(self._bg()))
        cv.delete("all")
        draw_round_rect(cv, 0, 0, W, H, RADIUS * s,
                        fill=rgb(self._bg()), outline=rgb(pal.border),
                        width=max(1, int(round(s))))
        # 顶部一条高光，模拟"薄玻璃"的边
        cv.create_line(PAD_X * s, 1.5 * s, W - PAD_X * s, 1.5 * s,
                       fill=rgb(customize.blend((255, 255, 255), self._bg(),
                                                0.35)),
                       width=max(1, int(round(s))))
        f9 = t.f(9, False, s)
        self._rows = []
        y = 6 * s
        for it in self.items:
            if it is None:
                cv.create_line(PAD_X * s, y + SEP_H * s / 2,
                               W - PAD_X * s, y + SEP_H * s / 2,
                               fill=rgb(pal.border),
                               width=max(1, int(round(s))))
                self._rows.append(("sep", y, y + SEP_H * s, "", None, False))
                y += SEP_H * s
                continue
            if it[0] == "check":
                _, label, on, cmd = it
            else:
                label, cmd, on = it[0], it[1], False
            row1, row2 = y, y + ITEM_H * s
            tx = PAD_X * s
            if it[0] == "check":
                cv.create_text(PAD_X * s, (row1 + row2) / 2,
                               text="✓" if on else " ", anchor="w",
                               fill=rgb(pal.primary), font=f9)
                tx += 18 * s
            cv.create_text(tx, (row1 + row2) / 2, text=label, anchor="w",
                           fill=rgb(pal.text), font=f9)
            self._rows.append(("item", row1, row2, label, cmd, on))
            y += ITEM_H * s
        self._hl = cv.create_rectangle(0, 0, 0, 0, fill=rgb(pal.btn_hover),
                                       outline="", state="hidden")
        cv.tag_lower(self._hl)
        self._hover = -1

    def _place(self):
        W, H = self._menu_w(), self._menu_h()
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
        except Exception:
            sw, sh = 1920, 1080
        x, y = self._x, self._y
        if x + W > sw - 8:
            x = x - W                        # 先翻到锚点左侧
        if y + H > sh - 8:
            y = y - H                        # 先翻到锚点上方
        # 翻转后必须再夹紧：锚点本身就在屏幕外时（窗口贴右边缘），
        # 只翻不夹会让整块菜单落在屏幕外 —— 视觉上就是「点了没反应」
        x = max(8, min(x, sw - W - 8))
        y = max(8, min(y, sh - H - 8))
        self.root.geometry(f"{W}x{H}+{x}+{y}")
        self.root.update_idletasks()
        hwnd = self.root.winfo_id()
        set_round_region(hwnd, W, H, int(RADIUS * 2 * self.S))
        # 先试亚克力；失败就用整窗 96% 不透明度兜底（Tk 画布是不透明的，
        # 亚克力在部分 Win11 版本会被系统忽略，所以两条路都得留着）
        try:
            self.acrylic_ok = bool(set_acrylic(hwnd, self._bg(), 0.92))
        except Exception:
            self.acrylic_ok = False
        if not self.acrylic_ok:
            try:
                self.root.attributes("-alpha", 0.96)
            except Exception:
                pass
        if self.panel._shadow is not None:
            try:
                self.panel._shadow.hide()    # 菜单打开时先收面板阴影，避免重叠
            except Exception:
                pass

    def _add_shadow(self):
        self._shadow_job = None
        if self.root is None:
            return
        try:
            from .shadow import ShadowWindow
            target = _ShadowTarget(self._menu_w(), self._menu_h(), self.S,
                                   self.root.winfo_id())
            self._shadow = ShadowWindow(target)
            self._shadow.show(self.root.winfo_x(), self.root.winfo_y())
        except Exception:
            self._shadow = None

    # -- 事件 -------------------------------------------------------------
    def _row_at(self, y: float):
        for i, row in enumerate(self._rows):
            if row[0] == "item" and row[1] <= y <= row[2]:
                return i
        return -1

    def _motion(self, e):
        i = self._row_at(e.y)
        if i == self._hover:
            return
        self._hover = i
        if i < 0:
            self.cv.itemconfigure(self._hl, state="hidden")
            return
        _kind, y1, y2, _l, _c, _o = self._rows[i]
        pad = 4 * self.S
        self.cv.coords(self._hl, pad, y1, self._menu_w() - pad, y2)
        self.cv.itemconfigure(self._hl, state="normal")
        self.cv.tag_raise(self._hl)
        # 高亮块要压在文字下面
        for i2 in self.cv.find_all():
            if self.cv.type(i2) == "text":
                self.cv.tag_raise(i2)

    def _click(self, e):
        i = self._row_at(e.y)
        if i < 0:
            self._close()
            return
        cmd = self._rows[i][4]
        self._close()                        # 先关再执行：命令里可能再开菜单
        if cmd:
            try:
                cmd()
            except Exception:
                pass

    def _close(self):
        if self._shadow_job is not None:
            try:
                self.root.after_cancel(self._shadow_job)
            except Exception:
                pass
            self._shadow_job = None
        if self._watch_job is not None:
            try:
                self.root.after_cancel(self._watch_job)
            except Exception:
                pass
            self._watch_job = None
        root, self.root = self.root, None
        if self._shadow is not None:
            try:
                self._shadow.destroy()
            except Exception:
                pass
            self._shadow = None
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
        for cb in list(self._on_close):
            try:
                cb(None)
            except Exception:
                pass
        self._on_close = []
        if getattr(self.panel, "_menu", None) is self:
            self.panel._menu = None
        try:
            if self.panel._shadow is not None and getattr(self.panel, "_visible", False):
                self.panel._shadow.show(self.panel.root.winfo_x(),
                                        self.panel.root.winfo_y())
        except Exception:
            pass
