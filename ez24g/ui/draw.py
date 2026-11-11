# -*- coding: utf-8 -*-
"""画布绘制小工具：圆角矩形、颜色、光学居中。"""
from __future__ import annotations

import math

from .theme import RGB, hexstr

rgb = hexstr


def round_rect_points(x1, y1, x2, y2, r, steps=16):
    """圆角矩形的多边形顶点（Tk 没有原生圆角矩形）。"""
    pts = []
    for cx, cy, a0 in ((x2 - r, y1 + r, -90), (x2 - r, y2 - r, 0),
                       (x1 + r, y2 - r, 90), (x1 + r, y1 + r, 180)):
        for i in range(steps + 1):
            a = (a0 + 90 * i / steps) * math.pi / 180
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def draw_round_rect(cv, x1, y1, x2, y2, r, **kw):
    pts = round_rect_points(x1, y1, x2, y2, r)
    flat = [v for p in pts for v in p]
    return cv.create_polygon(flat, smooth=False, **kw)


def glass_colors(fill: RGB, border: RGB) -> dict:
    """毛玻璃按钮的预混实色：Canvas 没有逐像素 alpha，用预混色模拟玻璃层。

    全部是实色，不引入第二张位图，也不依赖窗口透明度，任何缩放下一致。
    """
    return {
        "shadow": mix(fill, (0, 0, 0), 0.13),
        "body": mix(fill, (255, 255, 255), 0.16),
        "edge": mix(border, (255, 255, 255), 0.30),
        "highlight": mix(fill, (255, 255, 255), 0.58),
        "underside": mix(fill, (0, 0, 0), 0.10),
    }


def glass_button(cv, x1, y1, x2, y2, r, fill, border, tags=(), width=1):
    """毛玻璃质感按钮：柔和外阴影 + 玻璃面 + 顶部高光 + 底部内阴影。

    只叠加装饰：主体圆角矩形的几何、半径、tags 与事件绑定完全沿用原
    实现，点击热区不变。高光线也挂同一 tags，避免盖住主体后吃掉点击。
    返回主体 polygon，供调用方 hover 时改色。
    """
    c = glass_colors(fill, border)
    draw_round_rect(cv, x1, y1 + 1.5, x2, y2 + 1.5, r,
                    fill=rgb(c["shadow"]), outline="")
    body = draw_round_rect(cv, x1, y1, x2, y2, r,
                           fill=rgb(c["body"]), outline=rgb(c["edge"]),
                           width=width, tags=tags)
    if r * 2 < (y2 - y1) - 2:              # 纯圆按钮不画水平高光
        cv.create_line(x1 + r, y1 + 1.2, x2 - r, y1 + 1.2,
                       fill=rgb(c["highlight"]), width=1, tags=tags)
        cv.create_line(x1 + r, y2 - 1.2, x2 - r, y2 - 1.2,
                       fill=rgb(c["underside"]), width=1, tags=tags)
    return body


def vcenter_text(cv, x, y, text, fill, font, tags=(), anchor="center"):
    """按文字实际包围盒做垂直光学居中（中文基线偏下）。"""
    item = cv.create_text(x, y, text=text, fill=fill, font=font,
                          tags=tags, anchor=anchor)
    try:
        _x1, y1, _x2, y2 = cv.bbox(item)
        dy = (y1 + y2) / 2.0 - y
        if abs(dy) >= 0.5:
            cv.move(item, 0, -dy)
    except Exception:
        pass
    return item


def mix(a: RGB, b: RGB, t: float) -> RGB:
    return (int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t))
