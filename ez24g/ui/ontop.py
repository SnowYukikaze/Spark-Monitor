# -*- coding: utf-8 -*-
"""把二级窗口压到面板之上。

面板是 WS_EX_TOPMOST（Panel.show 里设的），普通 Toplevel 会被整块盖住。
实测：150% 下改名窗与面板重叠时，WindowFromPoint(弹窗中心) 返回的是面板 ——
用户看到的就是「点了没反应」。菜单同理（它抢焦点后又被 FocusOut 自毁）。
"""
from __future__ import annotations

from ..win32 import (GWL_EXSTYLE, GWL_HWNDPARENT, HWND_TOPMOST, SWP_NOACTIVATE,
                     SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW, WS_EX_TOPMOST,
                     user32)


def _root_hwnd(win) -> int:
    """Tk 的 winfo_id() 给的是子窗口，样式/层级都挂在根窗口上。"""
    try:
        h = int(win.winfo_id())
    except Exception:
        return 0
    try:
        return int(user32.GetAncestor(h, 2) or h)      # GA_ROOT
    except Exception:
        return h


def raise_above(win, owner_hwnd: int, topmost: bool = True) -> bool:
    """owner + topmost + BringWindowToTop，三重保证在面板之上。

    不调用 focus_force：抢焦点会让面板掉激活，菜单的焦点判定/输入法
    状态都会跟着抖（这正是「菜单刚出来就没了」的成因之一）。
    """
    hwnd = _root_hwnd(win)
    if not hwnd:
        return False
    ok = False
    try:
        user32.SetWindowLongPtrW(hwnd, GWL_HWNDPARENT, owner_hwnd)
        ok = True
    except Exception:
        pass
    try:
        win.attributes("-topmost", bool(topmost))
    except Exception:
        pass
    try:
        user32.SetWindowPos(hwnd, HWND_TOPMOST if topmost else 0,
                            0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
                            | SWP_SHOWWINDOW)
        ok = True
    except Exception:
        pass
    try:
        user32.BringWindowToTop(hwnd)
    except Exception:
        pass
    return ok


def is_topmost(win) -> bool:
    """自检用：窗口是否带 WS_EX_TOPMOST。"""
    hwnd = _root_hwnd(win)
    if not hwnd:
        return False
    try:
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        return bool(style & WS_EX_TOPMOST)
    except Exception:
        return False
