# -*- coding: utf-8 -*-
"""可复用的 Canvas 控件。

Canvas 手绘的好处是不用跟 ttk 的默认皮肤打架；
代价是每个控件都要自己处理悬停和点击。这里把它封装一次，
以后加按钮/勾选框就一行：

    Button(cv, theme, S, 24, 360, 64, 24, "刷新", app.refresh).draw()
"""
from __future__ import annotations

from typing import Callable, Optional

from .draw import draw_round_rect, glass_button, rgb, vcenter_text
from .theme import Theme

_seq = 0


def _tag(prefix: str) -> str:
    global _seq
    _seq += 1
    return f"{prefix}{_seq}"


class Button:
    """kind: ghost（描边）/ solid（实心）/ close（圆点关闭）/ circle（圆点普通）/ link（纯文字）"""

    def __init__(self, cv, theme: Theme, scale: float,
                 x: float, y: float, w: float, h: float,
                 text: str, command: Optional[Callable] = None,
                 kind: str = "ghost"):
        self.cv = cv
        self.t = theme
        self.S = scale
        self.x, self.y, self.w, self.h = x, y, w, h
        self.text = text
        self.command = command
        self.kind = kind
        self.tag = _tag("btn")
        self._on = False

    def draw(self):
        pal = self.t.pal
        if self.kind == "primary":
            fill, border, col = pal.primary, pal.primary, pal.on_primary
            hover, hover_col = pal.primary, pal.on_primary
        elif self.kind == "secondary":
            fill, border, col = pal.sec_bg, pal.sec_border, pal.sec_text
            hover, hover_col = pal.btn_hover, pal.text
        elif self.kind == "solid":
            fill, border, col = pal.btn, pal.btn, pal.text
            hover, hover_col = pal.btn_hover, pal.text
        elif self.kind == "close":
            fill = border = pal.window
            col = pal.sub
            hover, hover_col = pal.danger, pal.on_danger
        elif self.kind == "circle":
            fill = border = pal.window
            col = pal.sub
            hover, hover_col = pal.btn_hover, pal.text
        elif self.kind == "link":
            fill = border = None
            col = pal.sub
            hover, hover_col = None, pal.text
        else:
            fill, border, col = pal.window, pal.border, pal.sub
            hover, hover_col = pal.btn_hover, pal.text

        self._fill, self._border = fill, border
        # 注意：字段不能叫 _hover，会和下面的 _hover() 方法撞名 → 悬停时报 tuple 不可调用
        self._hover_fill, self._col, self._hover_col = hover, col, hover_col

        if fill is not None:
            radius = (self.h / 2 if self.kind in ("close", "circle")
                      else 8)
            self.poly = glass_button(
                self.cv, self.x, self.y, self.x + self.w, self.y + self.h,
                radius, fill, border, tags=(self.tag,))
        else:
            self.poly = None
        size = {"close": 11, "circle": 12}.get(self.kind, 9)
        self.txt = vcenter_text(self.cv, self.x + self.w / 2,
                                self.y + self.h / 2, self.text, rgb(col),
                                self.t.f(size, False, self.S), (self.tag,))
        self.cv.tag_bind(self.tag, "<Button-1>",
                         lambda e: self.command() if self.command else None)
        if self.kind == "link":
            # 纯文字按钮：只给手型光标，颜色不动
            self.cv.tag_bind(self.tag, "<Enter>",
                             lambda e: self.cv.configure(cursor="hand2"))
            self.cv.tag_bind(self.tag, "<Leave>",
                             lambda e: self.cv.configure(cursor=""))
        else:
            self.cv.tag_bind(self.tag, "<Enter>", lambda e: self._hover(True))
            self.cv.tag_bind(self.tag, "<Leave>", lambda e: self._hover(False))
        return self

    def _hover(self, on: bool):
        if on == self._on:
            return
        self._on = on
        if self.poly is not None and self._hover_fill is not None:
            self.cv.itemconfigure(self.poly, fill=rgb(self._hover_fill),
                                  outline=rgb(self._hover_fill))
        self.cv.itemconfigure(
            self.txt, fill=rgb(self._hover_col if on else self._col))


class Checkbox:
    """小勾选框 + 文字，整体可点。"""

    def __init__(self, cv, theme: Theme, scale: float, x: float, y: float,
                 checked: bool, label: str,
                 command: Optional[Callable] = None):
        self.cv, self.t, self.S = cv, theme, scale
        self.x, self.y, self.checked = x, y, checked
        self.label, self.command = label, command
        self.tag = _tag("chk")

    def draw(self):
        pal = self.t.pal
        fill, border = ((pal.btn, pal.btn) if self.checked
                        else (pal.window, pal.border))
        # 方框与文字同高（上 6px / 下 6px），不向下突出
        draw_round_rect(self.cv, self.x, self.y - 6,
                        self.x + 12, self.y + 6, 3.5,
                        fill=rgb(fill), outline=rgb(border), width=1,
                        tags=(self.tag,))
        if self.checked:
            self.cv.create_line(self.x + 3, self.y + 1,
                                self.x + 5.5, self.y + 3.5,
                                self.x + 9.5, self.y - 3,
                                fill=rgb(pal.text), width=2, tags=(self.tag,))
        vcenter_text(self.cv, self.x + 20, self.y, self.label,
                     rgb(pal.sub), self.t.f(9, False, self.S),
                     tags=(self.tag,), anchor="w")
        self.cv.tag_bind(self.tag, "<Button-1>",
                         lambda e: self.command() if self.command else None)
        return self


class ScrollBar:
    """细滚动条：只负责画 + 命中判定，交互交给 panel。

    以前把拖动绑在 Canvas 图元上（tag_bind），而每次 render() 都 delete("all")
    重建图元，绑定跟着消失 → 拖一下就不动了。现在拖动状态由 panel 在 widget
    级事件里自己维护，重绘多少次都不影响。
    """

    WIDTH = 6
    HIT_PAD = 3          # 左右各外扩，命中带共 12px（视觉仍是 6px 细条）

    def __init__(self, cv, theme: Theme, scale: float,
                 x: float, y1: float, y2: float):
        self.cv, self.t, self.S = cv, theme, scale
        self.x1, self.x2 = x, x + self.WIDTH
        self.y1, self.y2 = y1, y2
        self.track_h = 0.0
        self.thumb_h = 0.0
        self.thumb = (0.0, 0.0, 0.0, 0.0)     # 逻辑坐标 (x1, y1, x2, y2)

    def draw(self, scroll: float, max_scroll: float, view_h: float) -> bool:
        if max_scroll <= 1:
            self.track_h = self.thumb_h = 0.0
            self.thumb = (0.0, 0.0, 0.0, 0.0)
            return False
        pal = self.t.pal
        track_h = self.y2 - self.y1
        content = max_scroll + view_h
        thumb_h = max(24.0, track_h * view_h / content)
        ty = self.y1 + (scroll / max_scroll) * (track_h - thumb_h)
        self.track_h, self.thumb_h = track_h, thumb_h
        self.thumb = (self.x1, ty, self.x2, ty + thumb_h)
        draw_round_rect(self.cv, self.x1, self.y1, self.x2, self.y2, 3,
                        fill=rgb(pal.track), outline="")
        bar = draw_round_rect(self.cv, self.x1, ty, self.x2, ty + thumb_h, 3,
                              fill=rgb(pal.muted), outline="")
        self.cv.addtag_withtag("sbar_thumb", bar)   # 仅供自检定位，不绑事件
        return True

    def hit(self, x: float, y: float) -> str:
        """逻辑坐标命中判定："" / "thumb"（把手）/ "track"（轨道空白）。"""
        if self.thumb_h <= 0 or self.track_h <= 0:
            return ""
        if not (self.y1 - 2 <= y <= self.y2 + 2):
            return ""
        if not (self.x1 - self.HIT_PAD <= x <= self.x2 + self.HIT_PAD):
            return ""
        _x1, ty1, _x2, ty2 = self.thumb
        if ty1 - 2 <= y <= ty2 + 2:
            return "thumb"
        return "track"

    def thumb_center(self) -> float:
        _x1, ty1, _x2, ty2 = self.thumb
        return (ty1 + ty2) / 2
