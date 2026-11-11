# -*- coding: utf-8 -*-
"""托盘图标：用 GDI 画电量圆环，不依赖 Pillow。

4 倍超采样 + 覆盖率降采样，边缘比直接画 32px 圆润得多。

两个入口：
    render_rgba(rings, light, size)  纯像素（自检用，不碰图标句柄）
    make_battery_icon(...)           画好再包成 HICON
"""
from __future__ import annotations

import ctypes
import math
from typing import Optional

from ..win32 import (BITMAPINFO, BITMAPINFOHEADER, ICONINFO, colorref, gdi32,
                     user32, wt)


class _LogBrush(ctypes.Structure):
    _fields_ = [("lbStyle", wt.UINT),
                ("lbColor", wt.COLORREF),
                ("lbHatch", ctypes.c_void_p)]


gdi32.ExtCreatePen.argtypes = [wt.DWORD, ctypes.c_int,
                               ctypes.POINTER(_LogBrush), wt.DWORD,
                               ctypes.c_void_p]
gdi32.ExtCreatePen.restype = wt.HPEN
gdi32.GdiFlush.argtypes = []

# 32px 基准下的同心环几何：(半径, 线宽)，由外到内
RING_GEO = [(13.0, 3.2), (8.6, 2.6), (4.2, 2.0)]


def level_color(level: Optional[int]) -> tuple:
    """托盘图标用的高饱和配色（和界面主题分开，小图标需要更亮）。"""
    if level is None:
        return (120, 128, 140)
    if level >= 60:
        return (34, 197, 94)
    if level >= 30:
        return (245, 158, 11)
    return (239, 68, 68)


def _ring_pen(rgb, width: int) -> int:
    """平头几何画笔：圆弧端点不会额外探出，50% 就是正好 180°。"""
    lb = _LogBrush()
    lb.lbStyle = 0          # BS_SOLID
    lb.lbColor = colorref(*rgb)
    lb.lbHatch = 0
    return gdi32.ExtCreatePen(0x00010000 | 0x00000200, width,
                              ctypes.byref(lb), 0, None)


def _draw_ring(memdc, cx, cy, radius: float, thick: int, track,
               level: Optional[int], color=None) -> None:
    """画一圈：轨道 + 进度弧。color 为 None 时按电量取色。"""
    pen_track = _ring_pen(track, thick)
    gdi32.SelectObject(memdc, pen_track)
    gdi32.Ellipse(memdc, int(cx - radius), int(cy - radius),
                  int(cx + radius), int(cy + radius))
    pen_arc = 0
    if level is not None:
        rgbv = tuple(color) if color is not None else level_color(level)
        pen_arc = _ring_pen(rgbv, thick)
        gdi32.SelectObject(memdc, pen_arc)
        if level >= 100:
            gdi32.Ellipse(memdc, int(cx - radius), int(cy - radius),
                          int(cx + radius), int(cy + radius))
        else:
            theta = 2.0 * math.pi * max(2, min(100, level)) / 100.0
            ex = cx + radius * math.cos(-math.pi / 2 + theta)
            ey = cy + radius * math.sin(-math.pi / 2 + theta)
            # Arc 逆时针画：从终点绕回顶点 = 从顶点顺时针的进度弧
            gdi32.Arc(memdc, int(cx - radius), int(cy - radius),
                      int(cx + radius), int(cy + radius),
                      int(round(ex)), int(round(ey)),
                      int(cx), int(cy - radius))
    # 先把画笔从 DC 摘下来再删，否则 DeleteObject 删不掉「仍在用」的对象
    gdi32.SelectObject(memdc, gdi32.GetStockObject(8))     # BLACK_PEN
    if pen_arc:
        gdi32.DeleteObject(pen_arc)
    gdi32.DeleteObject(pen_track)


def render_rgba(rings, light: bool = True, size: int = 32) -> bytes:
    """把 rings 画成 size×size×4 的 BGRA 字节（洋红色键已扣成透明）。"""
    SS = 4
    big = size * SS
    hdc_screen = user32.GetDC(None)
    memdc = gdi32.CreateCompatibleDC(hdc_screen)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = big
    bmi.bmiHeader.biHeight = -big
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    bits = ctypes.c_void_p()
    hbm_big = gdi32.CreateDIBSection(memdc, ctypes.byref(bmi), 0,
                                     ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(memdc, hbm_big)
    try:
        key = colorref(255, 0, 255)         # 洋红当透明色键
        bg = gdi32.CreateSolidBrush(key)
        rect = wt.RECT(0, 0, big, big)
        user32.FillRect(memdc, ctypes.byref(rect), bg)
        gdi32.DeleteObject(bg)

        s = big / 32.0
        cx = cy = big / 2.0
        track = (95, 98, 105) if not light else (175, 180, 188)
        null_brush = gdi32.GetStockObject(5)      # NULL_BRUSH
        gdi32.SelectObject(memdc, null_brush)
        geo = RING_GEO[:max(1, len(rings))]
        for (lvl, col), (rad, th) in zip(rings, geo):
            _draw_ring(memdc, cx, cy, rad * s, max(2, int(round(th * s))),
                       track, lvl, col)

        gdi32.SelectObject(memdc, old)
        gdi32.GdiFlush()
        raw = ctypes.string_at(bits, big * big * 4)

        # 4×4 覆盖率降采样：统计非洋红像素占比作为 alpha
        small = bytearray(size * size * 4)
        for y in range(size):
            for x in range(size):
                cnt = 0
                rs = gs = bs = 0
                for yy in range(y * SS, (y + 1) * SS):
                    base = yy * big * 4 + x * SS * 4
                    for xx in range(SS):
                        i = base + xx * 4
                        b_, g_, r_ = raw[i], raw[i + 1], raw[i + 2]
                        if (r_, g_, b_) != (255, 0, 255):
                            cnt += 1
                            rs += r_
                            gs += g_
                            bs += b_
                o = (y * size + x) * 4
                if cnt:
                    small[o] = bs // cnt
                    small[o + 1] = gs // cnt
                    small[o + 2] = rs // cnt
                    small[o + 3] = cnt * 255 // (SS * SS)
        return bytes(small)
    finally:
        gdi32.DeleteObject(hbm_big)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(None, hdc_screen)


def _mask_bits(rgba: bytes, size: int) -> bytes:
    """1bpp AND mask：alpha<128 的位设 1（= 透明）。行按 16 位对齐。

    以前 CreateBitmap 传的是未初始化内存，实测透明位 0/1024（全不透明），
    某些主题/缩放下图标会渲染成黑块或空白。
    """
    stride = ((size + 15) // 16) * 2
    out = bytearray(stride * size)
    for y in range(size):
        for x in range(size):
            if rgba[(y * size + x) * 4 + 3] < 128:
                out[y * stride + (x >> 3)] |= 0x80 >> (x & 7)
    return bytes(out)


def make_battery_icon(level: Optional[int] = None, light: bool = True,
                      charging: bool = False, size: int = 32,
                      rings=None, color=None) -> int:
    """托盘图标。

    rings = [(level, (r, g, b)), ...]，由外到内，最多 3 个同心环（§3.2）。
    rings 为 None 时走原来的单环几何（半径 12、线宽 4），旧调用方像素不变。
    color 只在单环路径下生效（每设备电池色，§3.3）。
    """
    if rings is None:
        col = color
        if col is None and level is not None:
            col = (34, 197, 94) if charging else level_color(level)
        rings = [(level, col)]
    else:
        rings = list(rings)[:3]

    rgba = render_rgba(rings, light, size)

    hdc_screen = user32.GetDC(None)
    memdc = gdi32.CreateCompatibleDC(hdc_screen)
    hbm_small = 0
    try:
        bmi2 = BITMAPINFO()
        bmi2.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi2.bmiHeader.biWidth = size
        bmi2.bmiHeader.biHeight = -size
        bmi2.bmiHeader.biPlanes = 1
        bmi2.bmiHeader.biBitCount = 32
        bmi2.bmiHeader.biCompression = 0
        bits2 = ctypes.c_void_p()
        hbm_small = gdi32.CreateDIBSection(memdc, ctypes.byref(bmi2), 0,
                                           ctypes.byref(bits2), None, 0)
        old2 = gdi32.SelectObject(memdc, hbm_small)
        ctypes.memmove(bits2, rgba, len(rgba))

        mbuf = ctypes.create_string_buffer(_mask_bits(rgba, size),
                                           max(4, ((size + 15) // 16) * 2 * size))
        mask = gdi32.CreateBitmap(size, size, 1, 1,
                                  ctypes.cast(mbuf, ctypes.c_void_p))
        ii = ICONINFO()
        ii.fIcon = 1
        ii.hbmMask = mask
        ii.hbmColor = hbm_small
        icon = user32.CreateIconIndirect(ctypes.byref(ii))
        gdi32.DeleteObject(mask)
        gdi32.SelectObject(memdc, old2)      # 先取消选中，才能删位图
        return icon
    finally:
        if hbm_small:
            gdi32.DeleteObject(hbm_small)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(None, hdc_screen)
