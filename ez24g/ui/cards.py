# -*- coding: utf-8 -*-
"""设备卡片（新版：矢量设备图 + 名称 + 型号 + 状态 / 电量）。

    render_card(...)   一张卡片
    render_empty(...)  一台设备都没有时的占位

坐标都是逻辑像素（外面整体乘 DPI 缩放），颜色/字号全部来自 Theme。
"""
from __future__ import annotations

from typing import Callable, Optional

import tkinter as tk

from ..model import STATUS_TEXT, Reading
from .draw import draw_round_rect, rgb, vcenter_text
from .theme import Theme

# 疑似断连（沿用上次读数）用固定蓝，不跟随用户配色
_SUSPECT_BLUE = (46, 134, 222)

# 状态 → （文字, 调色板字段）
_STATUS = {
    "charging": ("充电中", "status_charging"),
    "full": ("已充满", "level_full"),
    "discharging": ("已连接", "status_ok"),
    "wired": ("有线模式", "status_ok"),
    "unknown": ("已连接", "status_ok"),
    "asleep": ("休眠", "status_asleep"),
    "unplugged": ("未连接", "status_offline"),
    "no_support": ("不可用", "status_asleep"),
    "busy": ("接收器被占用", "status_asleep"),
}


# 字体对象缓存：(family, size, weight) → tkfont.Font
# tkfont.Font() 每次新建都要向 Tk 注册一个字体对象，实测 0.196ms/次；
# _fit_text 每张卡每帧调 2 次（昵称 + 编号），10 张卡就是 1.6ms/帧 ≈ render 的 9%。
# 字体不可变，可以安全复用；Tk 根窗口被销毁后旧对象会失效，用 metrics() 探活。
_FONT_CACHE: dict = {}


def _tkfont(font):
    """按字体元组取一个可复用的 tkfont.Font（失效则重建）。"""
    key = tuple(font) if isinstance(font, (tuple, list)) else (font,)
    hit = _FONT_CACHE.get(key)
    if hit is not None:
        try:
            hit.metrics()
            return hit
        except Exception:
            _FONT_CACHE.pop(key, None)      # 旧根窗口已销毁，重建
    import tkinter.font as tkfont
    try:
        f = tkfont.Font(font=font)
    except Exception:
        return None
    _FONT_CACHE[key] = f
    return f


def _truncate(name: str, limit: int = 12) -> str:
    name = (name or "设备").strip()
    return name if len(name) <= limit else name[:limit - 1] + "…"


def _fit_text(cv, text: str, font, max_px: float, limit: int = 24) -> str:
    """按像素宽度截断（字号放大后按字数截会压到卡片边）。"""
    text = (text or "设备").strip()[:limit]
    f = _tkfont(font)
    if f is None:                          # 取不到字体对象：退回按字数截
        return _truncate(text, limit)
    try:
        if f.measure(text) <= max_px:
            return text
        while text and f.measure(text + "…") > max_px:
            text = text[:-1]
    except Exception:
        _FONT_CACHE.clear()                # 字体对象失效，下次重建
        return _truncate(text, limit)
    return (text + "…") if text else "…"


def _kind(name: str) -> str:
    n = (name or "").lower()
    if any(k in n for k in ("mouse", "鼠标", "g502", "g304", "viper", "毒蝰")):
        return "mouse"
    if any(k in n for k in ("keyboard", "键盘", "tkl", "kbd")):
        return "keyboard"
    if any(k in n for k in ("buds", "headset", "耳机", "airpods", "freebuds")):
        return "headset"
    if any(k in n for k in ("speaker", "音箱", "sound")):
        return "speaker"
    if any(k in n for k in ("gamepad", "手柄", "controller", "xbox")):
        return "gamepad"
    return ""


def _draw_icon(cv, theme: Theme, scale: float, cx: float, cy: float,
               name: str) -> None:
    """内置矢量设备图：鼠标 / 键盘 / 耳机 / 音箱 / 手柄 / 首字母徽标。"""
    pal = theme.pal
    line = rgb(pal.model)
    kind = _kind(name)
    if kind == "mouse":
        cv.create_oval(cx - 26, cy - 44, cx + 26, cy + 44,
                       outline=line, width=2 * scale, fill=rgb(pal.card))
        cv.create_line(cx, cy - 42, cx, cy - 10, fill=line, width=2 * scale)
    elif kind == "keyboard":
        draw_round_rect(cv, cx - 62, cy - 22, cx + 62, cy + 22, 8,
                        outline=line, width=2 * scale, fill=rgb(pal.card))
        for row in range(2):
            for col in range(7):
                x = cx - 48 + col * 16
                y = cy - 12 + row * 12
                draw_round_rect(cv, x, y, x + 8, y + 6, 1.5,
                                outline=line, width=max(1, scale))
        cv.create_line(cx - 30, cy + 16, cx + 30, cy + 16,
                       fill=line, width=max(1, scale))
    elif kind == "headset":
        cv.create_arc(cx - 46, cy - 46, cx + 46, cy + 34, start=0, extent=180,
                      style="arc", outline=line, width=3 * scale)
        draw_round_rect(cv, cx - 54, cy - 12, cx - 34, cy + 30, 8,
                        outline=line, width=2 * scale, fill=rgb(pal.card))
        draw_round_rect(cv, cx + 34, cy - 12, cx + 54, cy + 30, 8,
                        outline=line, width=2 * scale, fill=rgb(pal.card))
    elif kind == "speaker":
        draw_round_rect(cv, cx - 34, cy - 46, cx + 34, cy + 46, 10,
                        outline=line, width=2 * scale, fill=rgb(pal.card))
        cv.create_oval(cx - 16, cy + 2, cx + 16, cy + 34,
                       outline=line, width=2 * scale)
        cv.create_oval(cx - 8, cy - 30, cx + 8, cy - 14,
                       outline=line, width=2 * scale)
    elif kind == "gamepad":
        draw_round_rect(cv, cx - 56, cy - 24, cx + 56, cy + 24, 20,
                        outline=line, width=2 * scale, fill=rgb(pal.card))
        cv.create_oval(cx - 34, cy - 8, cx - 18, cy + 8,
                       outline=line, width=2 * scale)
        cv.create_oval(cx + 18, cy - 8, cx + 34, cy + 8,
                       outline=line, width=2 * scale)
    else:
        letter = (name or "?").strip()[:1].upper() or "?"
        cv.create_oval(cx - 34, cy - 34, cx + 34, cy + 34,
                       outline=line, width=2 * scale, fill=rgb(pal.card))
        cv.create_text(cx, cy, text=letter, fill=rgb(pal.sub),
                       font=theme.f(22, True, scale))


_BT_HINT_MAX = 14


def _short_reason(text: str) -> str:
    """卡片状态行放不下长句子；完整原因在鼠标悬停时显示。"""
    text = (text or "").strip()
    return text if len(text) <= _BT_HINT_MAX else text[:_BT_HINT_MAX - 1] + "…"


def _status_of(r: Reading) -> tuple[str, str]:
    """返回（状态文字, 调色板字段）。蓝牙失败时把「为什么读不到」写清楚。"""
    if getattr(r, "stale", False):
        return ("疑似断连", "status_ok")
    if r.source.startswith("手动档位"):
        return ("手动档位", "status_asleep")
    if r.source.startswith("手动绑定"):
        return ("手动", "status_asleep")
    if r.status == "unknown_proto":
        text = r.detail or STATUS_TEXT["unknown_proto"]
        return ((_short_reason(text) if r.transport == "bt" else text),
                "status_asleep")
    if (r.transport == "bt" and r.detail
            and r.status in ("no_support", "asleep", "unplugged")):
        return (_short_reason(r.detail), "status_asleep")
    return _STATUS.get(r.status, ("未知", "status_asleep"))


# ---------------------------------------------------------------------------
# 状态行悬浮提示：完整原因用独立小窗呈现，不就地撑状态行（会被电量徽章压住）
# ---------------------------------------------------------------------------
_TIP: dict = {"win": None, "job": None, "alpha": 0.0, "hwnd": 0}
_TIP_STEPS = 8
_TIP_MS = 18          # 8 × 18ms ≈ 0.14s，淡入 / 淡出共用一条时间线


def _tip_destroy() -> None:
    """立刻收掉提示窗（取消未跑完的动画、撤掉抓屏排除）。"""
    win = _TIP.get("win")
    job = _TIP.get("job")
    if job and win is not None:
        try:
            win.after_cancel(job)
        except Exception:
            pass
    # 这里不反向关闭抓屏排除：窗口随即销毁，排除自动失效；万一
    # GetAncestor 把它归到主窗口，反向调用反而会关掉主窗口的排除。
    if win is not None:
        try:
            win.destroy()
        except Exception:
            pass
    _TIP.update(win=None, job=None, alpha=0.0, hwnd=0)


def _tip_animate(target: float) -> None:
    """逐帧改 -alpha；淡出到 0 时销毁窗口。"""
    win = _TIP.get("win")
    if win is None:
        return
    _TIP["job"] = None
    cur = float(_TIP.get("alpha", 0.0))
    step = 1.0 / _TIP_STEPS
    nxt = min(1.0, cur + step) if target > cur else max(0.0, cur - step)
    try:
        win.attributes("-alpha", nxt)
    except Exception:
        # 个别平台不支持窗口透明度：动画降级为直接显示 / 直接关闭，
        # 但提示窗本身必须照常出现、照常消失。
        _TIP["alpha"] = target
        if target <= 0.0:
            _tip_destroy()
        return
    _TIP["alpha"] = nxt
    if abs(nxt - target) < 0.001:
        if target <= 0.0:
            _tip_destroy()
        return
    try:
        _TIP["job"] = win.after(_TIP_MS, lambda: _tip_animate(target))
    except Exception:
        _tip_destroy()


def _tip_hide() -> None:
    """鼠标移开状态行：淡出，不直接销毁，保证有过渡。"""
    if _TIP.get("win") is None:
        return
    job = _TIP.get("job")
    if job:
        try:
            _TIP["win"].after_cancel(job)
        except Exception:
            pass
        _TIP["job"] = None
    _tip_animate(0.0)


def _tip_show(cv, theme: Theme, scale: float, text: str) -> None:
    """在鼠标附近显示完整原因；同一时刻只留一个提示窗。"""
    if not text:
        return
    try:
        _tip_destroy()
        win = tk.Toplevel(cv.winfo_toplevel())
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        pal = theme.pal
        wrap = max(180, int(300 * scale))     # 换行宽度随整体缩放
        pad = max(8, int(10 * scale))
        lbl = tk.Label(win, text=text, wraplength=wrap, justify="left",
                       anchor="nw", font=theme.f(9, False, scale),
                       bg=rgb(pal.window), fg=rgb(pal.text),
                       padx=pad, pady=pad)
        lbl.pack()
        win.configure(bg=rgb(pal.window))
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        px, py = cv.winfo_pointerxy()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        x = max(0, min(px + 12, sw - w - 8))
        y = max(0, min(py + 18, sh - h - 8))
        win.geometry(f"{w}x{h}+{x}+{y}")
        try:
            win.attributes("-alpha", 0.0)
        except Exception:
            pass
        _TIP.update(win=win, alpha=0.0, hwnd=int(win.winfo_id()))
        try:
            from ..win32 import set_capture_exclusion
            set_capture_exclusion(_TIP["hwnd"], True)
        except Exception:
            pass
        _tip_animate(1.0)
    except Exception:
        _tip_destroy()


hide_status_tip = _tip_hide       # 面板整窗重绘前调用，避免提示窗残留


def _lum(c) -> float:
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


# 电池框上沿 = y2 - 47（by = y2 - 34，电池高 26 → 半高 13）。
# 判定昵称下边界和画电池用的是同一个数，抽出来两边共用，别写两份魔法数。
BATTERY_TOP_OFFSET = 47


def battery_top(y2: float) -> float:
    return y2 - BATTERY_TOP_OFFSET


def _readable(fg, bg):
    """数字颜色跟 UI 色，只有和电池填充亮度几乎相同（差 < 12）时才退回黑/白。

    不做「自动对比色」那类聪明处理：那会把用户设的 UI 颜色吃掉。
    """
    if abs(_lum(fg) - _lum(bg)) >= 12:
        return fg
    return (255, 255, 255) if _lum(bg) < 128 else (31, 35, 40)


def _draw_battery(cv, theme: Theme, scale: float, x: float, y: float,
                  w: float, h: float, level: Optional[int], color,
                  label: str, text_color, tags=()) -> None:
    """电池图形，电量数字画在内部；x、y 是左上角。"""
    pal = theme.pal
    line = max(1, int(round(scale)))
    draw_round_rect(cv, x, y, x + w, y + h, 5,
                    fill=rgb(pal.card), outline=rgb(pal.model),
                    width=line, tags=tags)
    cv.create_rectangle(x + w, y + h * 0.32, x + w + 3, y + h * 0.68,
                        fill=rgb(pal.model), outline="", tags=tags)
    if level is not None and level > 0:
        pad = 2
        inner = (w - pad * 2) * max(0, min(100, level)) / 100
        if inner >= 2:
            draw_round_rect(cv, x + pad, y + pad, x + pad + inner, y + h - pad,
                            3, fill=rgb(color), outline="", tags=tags)
    cv.create_text(x + w / 2, y + h / 2, text=label,
                   fill=rgb(text_color), font=theme.f(11, True, scale),
                   tags=tags)


def render_card(cv, theme: Theme, scale: float,
                x1: float, y1: float, x2: float, y2: float,
                r: Reading,
                on_rename: Optional[Callable] = None,
                on_fix: Optional[Callable] = None,
                key: str = "",
                card_fill=None, bg_photo=None,
                bg_top: float = 0.0, card_border=None,
                bg_is_photo: bool = False, battery_rgb=None,
                name_offset=(0.0, 0.0), id_text: str = "",
                id_offset=(0.0, 0.0),
                on_rename_id: Optional[Callable] = None) -> dict:
    """画一张设备卡，返回 {name_tag, fix_tag, bg_id} 供面板绑事件。

    key 非空时给本次画的所有 item 打 card:{key}，背景单独打 cardbg:{key}：
    拖动排序靠这个 tag 整块移动；背景图靠 cardbg 判断层级。

    bg_photo 现在总是有值（卡片底色也是合成图），所以「画不画矢量图标」
    要看 bg_is_photo（真的贴了用户图），不能看 bg_photo 是否为 None。
    """
    pal = theme.pal
    out = {"name_tag": None, "fix_tag": None, "bg_id": None,
           "id_tag": None, "id_item": None, "id_bbox": None}
    cx = (x1 + x2) / 2
    before = set(cv.find_all())

    bg = draw_round_rect(cv, x1, y1, x2, y2, theme.card_radius,
                         fill=rgb(card_fill) if card_fill else "",
                         outline=rgb(card_border or pal.border), width=1)
    out["bg_id"] = bg
    if bg_photo is not None:
        cv.create_image(x1, y1, image=bg_photo, anchor="nw")

    # 设备图区（高 120）；贴了用户图时不再画矢量图标（图标也算背景层）
    if not bg_is_photo:
        _draw_icon(cv, theme, scale, cx, y1 + 76, r.name)

    # 设备名（单击弹窗改名，长按可拖位置）；按像素截断，字号放大后不压边
    name_tag = f"cardname:{key}" if key else f"cardname_{id(r)}"
    out["name_tag"] = name_tag
    name_font = theme.f(12, True, scale)
    # 注意：font 是物理像素，可用宽度也要换算成物理像素
    name_text = _fit_text(cv, r.name, name_font, ((x2 - x1) - 28) * scale)
    name_item = cv.create_text(cx + name_offset[0],
                               y1 + 158 + name_offset[1],
                               text=name_text, anchor="center",
                               fill=rgb(pal.text), font=name_font,
                               tags=(name_tag,))
    out["name_item"] = name_item
    out["name_bbox"] = cv.bbox(name_item)
    if on_rename:
        cv.tag_bind(name_tag, "<Double-Button-1>", lambda e: on_rename())

    # 编号（蓝牙地址 / 2.4G 设备号）：可更名、可拖动，与昵称吸附联动
    model = r.addr.upper() if r.transport == "bt" else r.ident
    id_font = theme.f(8, False, scale)
    label = (id_text or "").strip() or f"{model or '----'} · {r.transport_text}"
    label = _fit_text(cv, label, id_font, ((x2 - x1) - 28) * scale)
    id_tag = f"cardid:{key}" if key else f"cardid_{id(r)}"
    out["id_tag"] = id_tag
    id_item = cv.create_text(cx + id_offset[0], y1 + 180 + id_offset[1],
                             text=label, anchor="center",
                             fill=rgb(pal.model), font=id_font,
                             tags=(id_tag,))
    out["id_item"] = id_item
    out["id_bbox"] = cv.bbox(id_item)
    if on_rename_id:
        cv.tag_bind(id_tag, "<Double-Button-1>", lambda e: on_rename_id())

    # 底部：状态 + 电量
    text, color_field = _status_of(r)
    dot = (rgb(_SUSPECT_BLUE) if getattr(r, "stale", False)
           else rgb(getattr(pal, color_field)))
    by = y2 - 34
    cv.create_oval(x1 + 18, by - 4, x1 + 24, by + 2, fill=dot, outline="")
    _st_tag = f"cardstatus_{id(r)}"
    _st_item = cv.create_text(x1 + 30, by, text=text, anchor="w",
                              fill=rgb(pal.sub), font=theme.f(9, False, scale),
                              tags=(_st_tag,))
    if r.detail and len(r.detail) > len(text):
        # 完整原因交给独立悬浮窗：就地换字会被右侧电量徽章压住。
        cv.tag_bind(_st_tag, "<Enter>",
                    lambda e, c=cv, t=theme, s=scale, d=r.detail:
                    _tip_show(c, t, s, d))
        cv.tag_bind(_st_tag, "<Leave>", lambda e: _tip_hide())
        out["status_tag"] = _st_tag
    # 只要卡片在、却没读到电量数字，就给「点我解决」（2.4G 与蓝牙都算）
    if on_fix and not (r.ok and r.level is not None):
        tag = f"cardfix_{id(r)}"
        out["fix_tag"] = tag
        cv.create_text(x1 + 30, by + 18, text="点我解决", anchor="w",
                       fill=rgb(pal.link), font=theme.f(9, True, scale),
                       tags=(tag,))
        cv.tag_bind(tag, "<Button-1>", lambda e: on_fix())
        cv.tag_bind(tag, "<Enter>", lambda e: cv.configure(cursor="hand2"))
        cv.tag_bind(tag, "<Leave>", lambda e: cv.configure(cursor=""))

    level = r.level if (r.ok and r.level is not None) else None
    bw, bh = 62, 26                       # 62 宽刚好放下 "100%"，26 高留住圆角
    if level is not None:
        prefix = "⚡" if r.status == "charging" else ""
        label = f"{prefix}{level}%"
        fill = theme.battery_color(level, battery_rgb)  # 「电池颜色」/每设备色
        num = _readable(pal.primary, fill)  # 「UI 颜色」
    else:
        label = "休眠" if r.status == "asleep" else "--"
        fill = num = pal.muted
    _draw_battery(cv, theme, scale, x2 - 18 - bw, by - bh / 2, bw, bh,
                  level, fill, label, num)

    if key:
        new_items = [i for i in cv.find_all() if i not in before]
        for i in new_items:
            cv.addtag_withtag(f"card:{key}", i)
        cv.addtag_withtag(f"cardbg:{key}", bg)
        if bg_photo is not None:
            cv.addtag_withtag(f"cardbg:{key}", new_items[1])
    return out


def render_empty(cv, theme: Theme, scale: float,
                 x1: float, y1: float, x2: float, y2: float,
                 title: str = "还没有设备",
                 hint: str = "插上接收器，或点右上角「+ 添加设备」") -> None:
    pal = theme.pal
    draw_round_rect(cv, x1, y1, x2, y2, theme.card_radius,
                    fill=rgb(pal.card), outline=rgb(pal.border), width=1)
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    vcenter_text(cv, cx, cy - 12, title, rgb(pal.sub), theme.f(13, True, scale))
    vcenter_text(cv, cx, cy + 14, hint, rgb(pal.muted),
                 theme.f(9, False, scale))
