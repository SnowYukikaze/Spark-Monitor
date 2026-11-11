# -*- coding: utf-8 -*-
"""窗口外发光（分层窗口 + UpdateLayeredWindow）。

Tk 的 overrideredirect 窗口没有系统阴影，又不能开透明渐变，
所以单独建一个 CS_DROPSHADOW 之外的分层窗口，在面板背后画一圈向外渐隐的灰。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math

from ..win32 import (BITMAPINFO, BITMAPINFOHEADER, BLENDFUNCTION, gdi32,
                     kernel32, user32)


class ShadowWindow:
    def __init__(self, panel):
        self.panel = panel
        self.hwnd = 0
        self._bitmap = None
        self._create()

    def _create(self):
        hinst = kernel32.GetModuleHandleW(None)
        # TOPMOST | TRANSPARENT | NOACTIVATE | TOOLWINDOW | LAYERED
        ex = 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080 | 0x00000008
        self.hwnd = user32.CreateWindowExW(
            ex, "STATIC", "", 0x80000000, 0, 0, 10, 10,
            None, None, hinst, None)

    def _build(self):
        W, H, S = self.panel.W, self.panel.H, self.panel.S
        margin = max(24, int(28 * S))
        w, h = W + 2 * margin, H + 2 * margin
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h          # 负数 = 自上而下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        memdc = gdi32.CreateCompatibleDC(None)
        hbm = gdi32.CreateDIBSection(memdc, ctypes.byref(bmi), 0,
                                     ctypes.byref(bits), None, 0)
        gdi32.SelectObject(memdc, hbm)
        buf = bytearray(w * h * 4)
        cx, cy = margin + W / 2.0, margin + H / 2.0
        r = 16.0 * S
        hx, hy = W / 2.0 - r, H / 2.0 - r
        sigma = 6.0 * S
        kw = 1.0 / (2.0 * sigma * sigma)
        limit = 3.0 * sigma
        for yy in range(h):
            py = yy + 0.5
            qy = abs(py - cy) - hy
            if qy > limit + r:
                continue
            oy = qy if qy > 0 else 0.0
            for xx in range(w):
                px = xx + 0.5
                qx = abs(px - cx) - hx
                if qx > limit + r:
                    continue
                ox = qx if qx > 0 else 0.0
                d = math.hypot(ox, oy) + min(max(qx, qy), 0.0) - r
                i = (yy * w + xx) * 4
                if -3.0 < d <= 3.0:
                    cov = 1.0 if d <= 1.8 else max(0.0, (3.0 - d) / 1.2)
                    a = int(255 * cov)
                    buf[i] = 60 * a // 255
                    buf[i + 1] = 60 * a // 255
                    buf[i + 2] = 60 * a // 255
                    buf[i + 3] = a
                elif 3.0 < d < limit:
                    a = int(40.0 * math.exp(-(d * d) * kw))
                    if a > 0:
                        buf[i + 3] = a
        ctypes.memmove(bits, bytes(buf), len(buf))
        self._bitmap = (w, h, memdc, hbm, margin)

    def _top(self):
        return user32.GetAncestor(self.panel.hwnd, 2) or self.panel.hwnd

    def show(self, x, y):
        if not self.hwnd:
            return
        if not getattr(self.panel, "_visible", True):
            return
        if self._bitmap is None:
            self._build()
        w, h, memdc, _hbm, margin = self._bitmap
        pt_dst = wt.POINT(int(x) - margin, int(y) - margin)
        size = wt.SIZE(w, h)
        pt_src = wt.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, 1)
        user32.UpdateLayeredWindow(self.hwnd, None, ctypes.byref(pt_dst),
                                   ctypes.byref(size), memdc,
                                   ctypes.byref(pt_src), 0,
                                   ctypes.byref(blend), 2)
        user32.SetWindowPos(self.hwnd, self._top(),
                            int(x) - margin, int(y) - margin, w, h,
                            0x0010 | 0x0040)

    def move(self, x, y):
        if not self.hwnd or self._bitmap is None:
            return
        w, h, _memdc, _hbm, margin = self._bitmap
        user32.SetWindowPos(self.hwnd, self._top(),
                            int(x) - margin, int(y) - margin, w, h, 0x0010)

    def hide(self):
        if self.hwnd:
            user32.ShowWindow(self.hwnd, 0)

    def invalidate(self):
        """面板尺寸变了：缓存的位图按旧尺寸算的，必须丢掉重建。"""
        if self._bitmap:
            _w, _h, memdc, hbm, _m = self._bitmap
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(memdc)
            self._bitmap = None

    def destroy(self):
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = 0
        if self._bitmap:
            _w, _h, memdc, hbm, _m = self._bitmap
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(memdc)
            self._bitmap = None
