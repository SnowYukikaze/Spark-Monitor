# -*- coding: utf-8 -*-
"""背景裁剪弹窗：选完图先框一块再应用（§3.12.1）。

坐标约定和 dialogs.py 一致：全部用**逻辑坐标**画，最后一次 `_finish()`
统一 `cv.scale`；拖选框时的动态层单独按物理坐标重画（不吃第二次 scale）。
"""
from __future__ import annotations

import tkinter as tk

from . import customize
from .dialogs import _Base
from .draw import draw_round_rect, rgb

DX, DY, DW, DH = 40, 86, 640, 400     # 显示区（逻辑像素）
MIN_SEL = 40                          # 选框最小边
HANDLE = 8                            # 把手边长


class CropDialog(_Base):
    """拖框裁剪：确认后回调 on_done(PIL.Image)，取消什么都不改。"""

    LW, LH = 720, 560
    DONE_XY = (600, 516)              # 只是给 _Base 用，这里自己画按钮

    def __init__(self, panel, image, target, on_done=None):
        self.src = image
        self.target = (int(target[0]), int(target[1]))
        self.on_done = on_done
        self._photo = None
        self._sel_drag = None
        self.sel = None
        self.disp = (DX, DY, DW, DH)
        self.k = 1.0
        super().__init__(panel, "裁剪背景")
        self.cv.bind("<Motion>", self._on_motion)

    # -- 几何 -------------------------------------------------------------
    def _fit(self):
        """图片 contain 到显示区，返回 (x, y, w, h) 与缩放比。"""
        sw, sh = self.src.size
        k = min(DW / sw, DH / sh)
        w, h = max(1, int(round(sw * k))), max(1, int(round(sh * k)))
        x = DX + (DW - w) // 2
        y = DY + (DH - h) // 2
        self.disp = (x, y, w, h)
        self.k = w / sw
        return self.disp, self.k

    def _init_sel(self):
        x, y, w, h = self.disp
        bw, bh = w * 0.8, h * 0.8
        cx, cy = x + w / 2, y + h / 2
        self.sel = [cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2]

    def _orig_size(self):
        """选框对应的原图像素尺寸（用户关心的是这个，不是屏幕像素）。"""
        if not self.sel or self.k <= 0:
            return 0, 0
        return (int(round((self.sel[2] - self.sel[0]) / self.k)),
                int(round((self.sel[3] - self.sel[1]) / self.k)))

    # -- 绘制 -------------------------------------------------------------
    def draw(self):
        t = self.theme
        cv = self.cv
        cv.delete("all")
        self._scaled = False
        draw_round_rect(cv, 0, 0, self.LW, self.LH, 14,
                        fill=rgb(t.pal.card), outline=rgb(t.pal.border))
        cv.create_text(20, 24, text="裁剪背景", anchor="w",
                       fill=rgb(t.pal.text), font=t.f(12, True, self.S))
        self._close_x()

        self._fit()
        if self.sel is None:
            self._init_sel()

        # 顶部常驻：输出尺寸（整段裁剪过程一直在）
        cv.create_text(20, 58,
                       text=f"输出尺寸：{self.target[0]} × {self.target[1]}"
                            f"（自动适配，不会拉伸/留白）",
                       anchor="w", fill=rgb(t.pal.sub),
                       font=t.f(9, False, self.S))

        # 显示区底 + 图片
        draw_round_rect(cv, DX, DY, DX + DW, DY + DH, 6,
                        fill=rgb(t.pal.sec_bg), outline=rgb(t.pal.border))
        photo = self._fit_photo()
        if photo is not None:
            cv.create_image(self.disp[0], self.disp[1], image=photo,
                            anchor="nw")
        cv.create_text(20, 492,
                       text="选区比例会自动对齐输出尺寸",
                       anchor="w", fill=rgb(t.pal.sub),
                       font=t.f(8, False, self.S))
        cv.create_text(20, 506,
                       text=f"选区：{self._orig_size()[0]} × "
                            f"{self._orig_size()[1]}",
                       anchor="w", fill=rgb(t.pal.sub),
                       font=t.f(9, False, self.S), tags=("crop_info",))

        # 按钮
        self._btn(500, 516, 90, 30, "取消", self.close, "crop_cancel", False)
        self._btn(600, 516, 100, 30, "确认裁剪", self._confirm, "crop_ok", True)
        self._finish()
        self._paint_sel()

    def _fit_photo(self):
        x, y, w, h = self.disp
        try:
            im = self.src
            if tuple(im.size) != (w, h):
                im = im.resize((w, h), customize.Image.LANCZOS)
            from .customize import to_photo
            self._photo = to_photo(im)
        except Exception:
            self._photo = None
        return self._photo

    def _btn(self, x, y, w, h, text, command, tag, primary: bool):
        t = self.theme
        fill = t.pal.primary if primary else t.pal.sec_bg
        edge = t.pal.primary if primary else t.pal.sec_border
        col = t.pal.on_primary if primary else t.pal.sec_text
        draw_round_rect(self.cv, x, y, x + w, y + h, 8,
                        fill=rgb(fill), outline=rgb(edge), tags=(tag,))
        self.cv.create_text(x + w / 2, y + h / 2, text=text, fill=rgb(col),
                            font=t.f(9, False, self.S), tags=(tag,))
        self.cv.tag_bind(tag, "<Button-1>", lambda e: command())

    # -- 动态选框（物理坐标，不吃第二次 scale）----------------------------
    def _paint_sel(self):
        s = self.S
        cv = self.cv
        cv.delete("cropsel")
        if not self.sel:
            return
        x1, y1, x2, y2 = [v * s for v in self.sel]
        ix, iy, iw, ih = [v * s for v in self.disp]
        dark = rgb(self.theme.pal.window)
        # 框外遮罩（Canvas 没有 alpha，用 stipple 冒充 50% 黑）
        for box in ((ix, iy, ix + iw, y1), (ix, y2, ix + iw, iy + ih),
                    (ix, y1, x1, y2), (x2, y1, ix + iw, y2)):
            if box[2] - box[0] > 0 and box[3] - box[1] > 0:
                cv.create_rectangle(*box, fill=dark, outline="",
                                    stipple="gray50", tags=("cropsel",))
        cv.create_rectangle(x1, y1, x2, y2, outline=rgb(self.theme.pal.primary),
                            width=max(1, int(round(2 * s))),
                            tags=("cropsel",))
        hs = HANDLE * s / 2
        for hx, hy in self._handle_centers():
            px, py = hx * s, hy * s
            cv.create_rectangle(px - hs, py - hs, px + hs, py + hs,
                                fill=rgb(self.theme.pal.card),
                                outline=rgb(self.theme.pal.primary),
                                width=max(1, int(round(1.5 * s))),
                                tags=("cropsel",))
        cv.itemconfigure("crop_info",
                         text=f"选区：{self._orig_size()[0]} × "
                              f"{self._orig_size()[1]}")

    def _handle_centers(self):
        x1, y1, x2, y2 = self.sel
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        return [(x1, y1), (cx, y1), (x2, y1), (x2, cy),
                (x2, y2), (cx, y2), (x1, y2), (x1, cy)]

    _HCURSORS = ("size_nw_se", "size_ns", "size_ne_sw", "size_we",
                 "size_nw_se", "size_ns", "size_ne_sw", "size_we")

    # -- 交互 -------------------------------------------------------------
    def _handle_at(self, lx, ly):
        tol = HANDLE / 2 + 3
        for i, (hx, hy) in enumerate(self._handle_centers()):
            if abs(lx - hx) <= tol and abs(ly - hy) <= tol:
                return i
        return -1

    def _on_motion(self, e):
        if self._drag:
            return
        lx, ly = e.x / self.S, e.y / self.S
        i = self._handle_at(lx, ly)
        if i >= 0:
            self.cv.configure(cursor=self._HCURSORS[i])
        elif self.sel and self.sel[0] < lx < self.sel[2] \
                and self.sel[1] < ly < self.sel[3]:
            self.cv.configure(cursor="fleur")
        else:
            self.cv.configure(cursor="")

    def _press(self, e):
        lx, ly = e.x / self.S, e.y / self.S
        i = self._handle_at(lx, ly)
        if i >= 0:
            self._sel_drag = ("size", i, list(self.sel), (lx, ly))
            return
        if self.sel and self.sel[0] < lx < self.sel[2] \
                and self.sel[1] < ly < self.sel[3]:
            self._sel_drag = ("move", -1, list(self.sel), (lx, ly))
            return
        # 框外点击：不重画（只允许框内移动 / 拖把手）
        super()._press(e)

    def _drag(self, e):
        if not self._sel_drag:
            return super()._drag(e)
        mode, idx, start, (px, py) = self._sel_drag
        lx, ly = e.x / self.S, e.y / self.S
        dx, dy = lx - px, ly - py
        ix, iy, iw, ih = self.disp
        x1, y1, x2, y2 = start
        if mode == "move":
            nx1 = max(ix, min(x1 + dx, ix + iw - (x2 - x1)))
            ny1 = max(iy, min(y1 + dy, iy + ih - (y2 - y1)))
            self.sel = [nx1, ny1, nx1 + (x2 - x1), ny1 + (y2 - y1)]
        else:
            edge = ("nw", "n", "ne", "e", "se", "s", "sw", "w")[idx]
            a1, b1, a2, b2 = x1, y1, x2, y2
            if "w" in edge:
                a1 = min(max(ix, x1 + dx), a2 - MIN_SEL)
            if "e" in edge:
                a2 = max(min(ix + iw, x2 + dx), a1 + MIN_SEL)
            if "n" in edge:
                b1 = min(max(iy, y1 + dy), b2 - MIN_SEL)
            if "s" in edge:
                b2 = max(min(iy + ih, y2 + dy), b1 + MIN_SEL)
            self.sel = [a1, b1, a2, b2]
        self._paint_sel()

    def _release(self, e):
        self._sel_drag = None
        super()._release(e)

    # -- 收尾 -------------------------------------------------------------
    def _confirm(self):
        if not self.sel or not self.on_done:
            self.close()
            return
        k = self.k or 1.0
        x, y = self.disp[0], self.disp[1]
        box = (int(round((self.sel[0] - x) / k)),
               int(round((self.sel[1] - y) / k)),
               int(round((self.sel[2] - x) / k)),
               int(round((self.sel[3] - y) / k)))
        try:
            # 选区先对齐目标比例，再直接缩放到目标尺寸：比例一致不拉伸，
            # 尺寸正好不留白。_cover() 会二次居中裁切，这里不再用。
            box = _fit_to_ratio(box, self.src.size, self.target)
            img = self.src.crop(box)
            img = img.resize((self.target[0], self.target[1]),
                             customize.Image.LANCZOS)
        except Exception:
            self.close()
            return
        try:
            self.on_done(img)
        finally:
            self.close()


def _cover(img, w: int, h: int):
    """裁完的块再按 cover 缩放成目标尺寸（允许用户裁得不准）。"""
    from .customize import Image
    sw, sh = img.size
    k = max(w / max(1, sw), h / max(1, sh))
    img = img.resize((max(w, int(sw * k + 0.5)), max(h, int(sh * k + 0.5))),
                     Image.LANCZOS)
    left = (img.size[0] - w) // 2
    top = (img.size[1] - h) // 2
    return img.crop((left, top, left + w, top + h))


def _fit_to_ratio(box, src_size, target):
    """把选区扩展成目标宽高比，完整保留用户框选的内容。

    以选区中心为准，向不足的那一维扩展（取原图相邻画面），再整体夹回图片内。
    不收窄：收窄会把用户选中的上下/左右裁掉，卡片上就只剩局部（实测 600x800
    的选区只剩 600x416）。输出比例 == 目标比例、尺寸 == 目标尺寸：
    比例一致 → 不拉伸；尺寸正好 → 不留白；选区完整 → 画面不缺角。
    """
    sw, sh = float(src_size[0]), float(src_size[1])
    if sw < 2 or sh < 2:
        return tuple(int(round(v)) for v in box)
    ratio = target[0] / max(1, target[1])
    x1, y1, x2, y2 = [float(v) for v in box]
    x1, x2 = sorted((max(0.0, min(x1, sw)), max(0.0, min(x2, sw))))
    y1, y2 = sorted((max(0.0, min(y1, sh)), max(0.0, min(y2, sh))))
    sel_w = max(8.0, x2 - x1)
    sel_h = max(8.0, y2 - y1)
    w, h = sel_w, sel_h
    if w / h > ratio:                 # 太宽 → 补足高度
        h = w / ratio
    else:                             # 太高 → 补足宽度
        w = h * ratio
    # 扩展后比原图还大：等比缩小框与选区，选区仍完整落在框内
    k = min(1.0, sw / w, sh / h)
    if k < 1.0:
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        sel_w, sel_h = sel_w * k, sel_h * k
        x1, x2 = cx - sel_w / 2, cx + sel_w / 2
        y1, y2 = cy - sel_h / 2, cy + sel_h / 2
        w, h = w * k, h * k
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    nx = min(max(0.0, cx - w / 2), sw - w)
    ny = min(max(0.0, cy - h / 2), sh - h)
    # 夹回图片边缘时仍要包住选区：把框往选区方向推，不往反方向推
    nx = min(max(nx, x2 - w), x1)
    ny = min(max(ny, y2 - h), y1)
    nx = min(max(0.0, nx), max(0.0, sw - w))
    ny = min(max(0.0, ny), max(0.0, sh - h))
    return (max(0, int(round(nx))), max(0, int(round(ny))),
            min(int(sw), int(round(nx + w))), min(int(sh), int(round(ny + h))))
