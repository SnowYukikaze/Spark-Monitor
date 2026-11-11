# -*- coding: utf-8 -*-
"""外观设置弹窗：透明度（三个滑块）、颜色（三组 + 色相条/SV 方块）、背景选择。

三款都是无边框圆角小窗，风格跟面板一致：标题栏可拖、右下角「完成」。
拖动滑块 / 点色块都**实时**写 config 并让主面板重绘，关窗不撤销。

坐标约定（踩过的坑）：
    所有绘制都用**逻辑坐标**，最后一次 `_finish()` 整体 `cv.scale`，
    这样 125% / 150% 缩放下内容才铺满窗口、点击位置才对得上。
    只有 cv.create_window（Entry）不吃 cv.scale，必须在 _finish() 之后
    用物理坐标单独摆。
"""
from __future__ import annotations

import tkinter as tk

from .. import config
from ..win32 import set_round_region
from . import customize
from . import theme as theme_mod
from .cards import _fit_text
from .draw import draw_round_rect, glass_button, rgb, vcenter_text
from .theme import FS_DEFAULT, FS_MAX, FS_MIN, get_theme
from .widgets import Button

TITLE_H = 40          # 标题区高度：只有这一段按下能拖窗口


class _Base:
    LW, LH = 420, 260
    DONE_XY = (336, 206)

    def __init__(self, panel, title: str):
        self.panel = panel
        self.app = panel.app
        # 弹窗可能先于面板重绘打开，字号得自己先同步一次
        theme_mod.set_font_scale(config.get_font_scale())
        # 面板 zoom 到 1.4 时弹窗最多 1.35，避免超出屏幕
        self.S = min(panel.S, 1.35)
        self.theme = get_theme(self.app.theme_light)
        self.W = int(self.LW * self.S)
        self.H = int(self.LH * self.S)
        self.root = tk.Toplevel(panel.root)
        self.root.overrideredirect(True)
        self.root.configure(bg=rgb(self.theme.pal.window))
        self.cv = tk.Canvas(self.root, width=self.W, height=self.H,
                            highlightthickness=0, bd=0,
                            bg=rgb(self.theme.pal.window))
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Button-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.root.bind("<Escape>", lambda e: self.close())
        self.root.update_idletasks()
        self.hwnd = self.root.winfo_id()
        set_round_region(self.hwnd, self.W, self.H, int(28 * self.S))
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        # 优先放面板左侧；放不下再居中盖在面板上（靠 raise_above 保证可见）
        try:
            px, py = panel.root.winfo_rootx(), panel.root.winfo_rooty()
            pw, ph = panel.root.winfo_width(), panel.root.winfo_height()
        except Exception:
            px = py = pw = ph = 0
        x = px - self.W - 12
        if x < 8:
            x = min(max(8, px + (pw - self.W) // 2), sw - self.W - 8)
        y = min(max(8, py + (ph - self.H) // 2), sh - self.H - 48)
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")
        self._press_pos = None
        self.title = title
        self._img_cache = {}
        self._scaled = False
        self.draw()
        self.root.transient(panel.root)
        from .ontop import raise_above
        raise_above(self.root, panel.root.winfo_id())

    # -- 窗口拖动（只有标题区）--------------------------------------------
    def _press(self, e):
        ly = e.y / self.S
        if ly > TITLE_H:
            return                       # 内容区按下不能拖窗口
        self._press_pos = (e.x_root, e.y_root,
                           self.root.winfo_x(), self.root.winfo_y())

    def _drag(self, e):
        if not self._press_pos:
            return
        x0, y0, wx, wy = self._press_pos
        self.root.geometry(f"+{wx + e.x_root - x0}+{wy + e.y_root - y0}")

    def _release(self, e):
        self._press_pos = None

    def close(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        if getattr(self.panel, "_dlg", None) is self:
            self.panel._dlg = None
        try:
            # 弹窗关掉后背后的画面变了，桌面快照作废（§3.1.5）
            self.panel._backdrop_epoch += 1
        except Exception:
            pass
        try:
            self.panel.render()
        except Exception:
            pass

    # -- 绘制 -------------------------------------------------------------
    def draw(self):
        raise NotImplementedError

    def _frame(self, title: str):
        """画外框 + 标题 + 完成按钮，并清空画布。"""
        t = self.theme
        cv = self.cv
        cv.delete("all")
        self._scaled = False
        draw_round_rect(cv, 0, 0, self.LW, self.LH, 14,
                        fill=rgb(t.pal.card), outline=rgb(t.pal.border))
        cv.create_text(20, 24, text=title, anchor="w", fill=rgb(t.pal.text),
                       font=t.f(12, True, self.S))
        self._done_btn(*self.DONE_XY, "完成", self.close)
        self._close_x()

    def _close_x(self):
        """右上角 X：和主面板的关闭键同款（细线 + 悬停变红）。

        必须在 _finish() 之前画（逻辑坐标，最后统一 cv.scale），
        否则 150% 下会偏。
        """
        t = self.theme
        cx, cy, d = self.LW - 30, 20, 6
        tag, hit = "dlg_close", "dlg_close_hit"
        # 热区用与卡片同色的矩形（fill="" 的 item 命中不稳定，别赌）
        self.cv.create_rectangle(cx - 12, cy - 12, cx + 12, cy + 12,
                                 fill=rgb(t.pal.card), outline="", tags=(hit,))
        self.cv.create_line(cx - d, cy - d, cx + d, cy + d,
                            fill=rgb(t.pal.sub), width=2, tags=(tag,))
        self.cv.create_line(cx - d, cy + d, cx + d, cy - d,
                            fill=rgb(t.pal.sub), width=2, tags=(tag,))
        for tg in (tag, hit):
            self.cv.tag_bind(tg, "<Button-1>", lambda e: self.close())
            self.cv.tag_bind(tg, "<Enter>", lambda e: self._hover_x(True))
            self.cv.tag_bind(tg, "<Leave>", lambda e: self._hover_x(False))

    def _hover_x(self, on: bool):
        self.cv.itemconfigure("dlg_close",
                              fill=rgb(self.theme.pal.danger if on
                                       else self.theme.pal.sub))
        self.cv.configure(cursor="hand2" if on else "")

    def _finish(self):
        """所有逻辑坐标 → 物理坐标（子类 draw 的最后一步）。"""
        if not self._scaled:
            self.cv.scale("all", 0, 0, self.S, self.S)
            self._scaled = True

    def _done_btn(self, x, y, text, command, tag="dlg_done"):
        t = self.theme
        glass_button(self.cv, x, y, x + 64, y + 26, 6,
                     t.pal.primary, t.pal.primary, tags=(tag,))
        vcenter_text(self.cv, x + 32, y + 13, text, rgb(t.pal.on_primary),
                     t.f(9, False, self.S), tags=(tag,))
        self.cv.tag_bind(tag, "<Button-1>", lambda e: command())
        return tag


class Slider:
    """Canvas 自绘滑块：圆角轨道 + 已填充段 + 圆点。

    绘制用逻辑坐标；`scaled=True` 时自己乘 S（用于弹窗画完之后的局部重绘，
    这时再 scale("all") 会把别的东西也缩放一遍）。
    """

    def __init__(self, cv, theme, scale, x, y, w, value, command):
        self.cv, self.t, self.S = cv, theme, scale
        self.x, self.y, self.w = x, y, w
        self.value = value
        self.command = command
        self.h = 6
        self.drag = False
        self.tag = f"slider_{id(self)}"

    def draw(self, scaled: bool = False):
        t = self.t
        pal = t.pal
        k = self.S if scaled else 1.0
        x, y, w, h = self.x * k, self.y * k, self.w * k, self.h * k
        self.cv.delete(self.tag)               # 重画前先清掉旧的，否则越拖越多
        tag = (self.tag,)
        draw_round_rect(self.cv, x, y, x + w, y + h, 3 * k,
                        fill=rgb(pal.track), outline="", tags=tag)
        vx = x + w * self.value / 100.0
        draw_round_rect(self.cv, x, y, max(x + 6 * k, vx), y + h, 3 * k,
                        fill=rgb(pal.primary), outline="", tags=tag)
        r = 8 * k
        self.cv.create_oval(vx - r, y - 5 * k, vx + r, y + 11 * k,
                            fill=rgb(pal.card), outline=rgb(pal.primary),
                            width=max(1, int(round(2 * k))), tags=tag)
        self.cv.create_text(x + w + 34 * k, y + 3 * k,
                            text=f"{self.value}%", anchor="w",
                            fill=rgb(pal.sub), font=t.f(9, False, self.S),
                            tags=tag)

    def hit(self, e) -> bool:
        """热区比轨道大一圈：纵向 y-16 ~ y+22（约 38 逻辑像素）。"""
        lx, ly = e.x / self.S, e.y / self.S
        return (self.x - 12 <= lx <= self.x + self.w + 12
                and self.y - 16 <= ly <= self.y + self.h + 22)

    def set_from(self, e):
        lx = e.x / self.S
        v = int(round((lx - self.x) / self.w * 100))
        self.value = max(0, min(100, v))
        if self.command:
            self.command(self.value)


class OpacityDialog(_Base):
    """背景透明度：卡片 / 软件两档。

    第三档「菜单背景透明度」已下线（2026-11 需求），菜单不透明度改成固定值
    （见 menu.MENU_ALPHA）。config.get_opacity() 仍返回 menu 键，仅供旧配置兼容。
    """

    LW, LH = 420, 210
    DONE_XY = (336, 150)
    TITLE = "背景透明度"

    def __init__(self, panel):
        self._active_slider = None
        super().__init__(panel, self.TITLE)

    def draw(self):
        t = self.theme
        self._frame(self.TITLE)
        op = config.get_opacity()
        s = self.S
        self.s_card = Slider(self.cv, t, s, 130, 70, 200, op["card"],
                             self._set_card)
        self.s_app = Slider(self.cv, t, s, 130, 120, 200, op["app"],
                            self._set_app)
        for y, label in ((73, "卡片背景透明度"), (123, "软件背景透明度")):
            self.cv.create_text(20, y, text=label, anchor="w",
                                fill=rgb(t.pal.text), font=t.f(9, False, s))
        self.cv.create_text(20, 160, text="数值越大越透明",
                            anchor="w", fill=rgb(t.pal.muted),
                            font=t.f(8, False, s))
        self.cv.create_text(20, 176, text="100% = 完全透明，0% = 完全不透明",
                            anchor="w", fill=rgb(t.pal.muted),
                            font=t.f(8, False, s))
        self.s_card.draw()
        self.s_app.draw()
        self._reset_btn()
        self._finish()

    def _reset_btn(self):
        t = self.theme
        x, y, w = 214, 150, 106
        glass_button(self.cv, x, y, x + w, y + 26, 6,
                     t.pal.sec_bg, t.pal.sec_border,
                     tags=("opacity_reset",))
        vcenter_text(self.cv, x + w / 2, y + 13, "重置为不透明",
                     rgb(t.pal.sec_text), t.f(9, False, self.S),
                     tags=("opacity_reset",))
        self.cv.tag_bind("opacity_reset", "<Button-1>", lambda e: self._reset())

    def _reset(self):
        config.set_opacity(0, 0)
        self._active_slider = None
        self.draw()
        self.panel._settings_changed()

    # -- 滑块回调 ---------------------------------------------------------
    def _set_card(self, v):
        op = config.get_opacity()
        config.set_opacity(v, op["app"])
        # 实时应用：只重算 L0/L1/L2 的 alpha 混合；冻结态下会自动退出
        # 截图模式再生效，不再需要点一下截图模式才看得到变化。
        self.panel._settings_changed()

    def _set_app(self, v):
        op = config.get_opacity()
        config.set_opacity(op["card"], v)
        self.panel._settings_changed()

    def _sliders(self):
        return [self.s_card, self.s_app]

    def _redraw_sliders(self):
        # 只重画滑块区域，避免整窗闪烁
        for s in self._sliders():
            s.draw(scaled=True)

    # -- 事件 -------------------------------------------------------------
    def _press(self, e):
        for s in self._sliders():
            if s.hit(e):
                self._active_slider = s
                s.set_from(e)
                self._redraw_sliders()
                return
        self._active_slider = None
        super()._press(e)

    def _drag(self, e):
        if self._active_slider is not None:
            self._active_slider.set_from(e)
            self._redraw_sliders()
            return
        super()._drag(e)

    def _release(self, e):
        self._active_slider = None
        super()._release(e)


class FontDialog(_Base):
    """字体大小：滑块默认居中，左减右增，实时生效。

    滑块 0–100 不是线性映射到 0.85–1.25，而是**过 1.0 的两段**：
    0→0.85、50→1.0、100→1.25。这样「默认居中」和「两端就是缩放上下限」
    两条同时成立（线性映射时 1.0 会落在 38 而不是 50）。
    """

    LW, LH = 420, 190
    DONE_XY = (336, 136)
    TITLE = "字体大小"

    def __init__(self, panel):
        self._active_slider = None
        super().__init__(panel, self.TITLE)

    # -- 滑块值 ↔ 字号倍数 ------------------------------------------------
    @staticmethod
    def _scale_of(v: float) -> float:
        v = max(0.0, min(100.0, float(v)))
        if v <= 50:
            return FS_MIN + (FS_DEFAULT - FS_MIN) * (v / 50.0)
        return FS_DEFAULT + (FS_MAX - FS_DEFAULT) * ((v - 50) / 50.0)

    def _value(self) -> int:
        fs = config.get_font_scale()
        if fs <= FS_DEFAULT:
            v = (fs - FS_MIN) / (FS_DEFAULT - FS_MIN) * 50
        else:
            v = 50 + (fs - FS_DEFAULT) / (FS_MAX - FS_DEFAULT) * 50
        return int(round(max(0, min(100, v))))

    def draw(self):
        t, s = self.theme, self.S
        self._frame(self.TITLE)
        self.s_font = Slider(self.cv, t, s, 130, 66, 200, self._value(),
                             self._set)
        self.cv.create_text(20, 69, text="字体大小", anchor="w",
                            fill=rgb(t.pal.text), font=t.f(9, False, s))
        self.cv.create_text(344, 69, text="A+", anchor="w",
                            fill=rgb(t.pal.sub), font=t.f(8, False, s))
        self.cv.create_text(112, 69, text="A−", anchor="e",
                            fill=rgb(t.pal.sub), font=t.f(8, False, s))
        self._preview_item = self.cv.create_text(
            20, 112, text=self._preview(), anchor="w",
            fill=rgb(t.pal.text), font=t.f(12, True, s))
        self.cv.create_text(20, 140, text="左小右大，默认居中；只影响文字，不改窗口尺寸",
                            anchor="w", fill=rgb(t.pal.muted),
                            font=t.f(8, False, s))
        self.s_font.draw()
        self._reset_btn()
        self._finish()

    def _preview(self) -> str:
        return (f"示例文字 Aa · 立即刷新 · "
                f"{int(round(config.get_font_scale() * 100))}%")

    def _set(self, v):
        fs = self._scale_of(v)
        config.set_font_scale(fs)
        theme_mod.set_font_scale(fs)
        self.s_font.value = int(v)
        # 预览行要立刻用新字号（重画整窗会重建滑块，拖动就断了）
        try:
            self.cv.itemconfigure(self._preview_item, text=self._preview(),
                                  font=self.theme.f(12, True, self.S))
        except Exception:
            pass
        self.panel.render()
        self._redraw_sliders()

    def _reset_btn(self):
        t = self.theme
        x, y, w = 214, 136, 106
        draw_round_rect(self.cv, x, y, x + w, y + 26, 6,
                        fill=rgb(t.pal.sec_bg), outline=rgb(t.pal.sec_border),
                        tags=("font_reset",))
        vcenter_text(self.cv, x + w / 2, y + 13, "恢复默认",
                     rgb(t.pal.sec_text), t.f(9, False, self.S),
                     tags=("font_reset",))
        self.cv.tag_bind("font_reset", "<Button-1>", lambda e: self._reset())

    def _reset(self):
        config.set_font_scale(FS_DEFAULT)
        theme_mod.set_font_scale(FS_DEFAULT)
        self.panel.render()
        self.draw()                     # 滑块要回到正中间

    def _sliders(self):
        return [self.s_font]

    def _redraw_sliders(self):
        for s in self._sliders():
            s.draw(scaled=True)

    def _press(self, e):
        for s in self._sliders():
            if s.hit(e):
                self._active_slider = s
                s.set_from(e)
                self._redraw_sliders()
                return
        self._active_slider = None
        super()._press(e)

    def _drag(self, e):
        if self._active_slider is not None:
            self._active_slider.set_from(e)
            self._redraw_sliders()
            return
        super()._drag(e)

    def _release(self, e):
        self._active_slider = None
        super()._release(e)


class RenameDialog(_Base):
    """改昵称：预填原名。

    不用面板上的内联 Entry —— panel._drop_ime() 摘了面板的 IME，内联输入
    打不出中文；独立弹窗有自己的输入上下文。
    """

    LW, LH = 360, 164
    DONE_XY = (276, 112)
    TITLE = "修改昵称"

    def __init__(self, panel, key: str, current: str):
        self.key = key
        self.current = current or ""
        super().__init__(panel, self.TITLE)

    def draw(self):
        t, s = self.theme, self.S
        self._frame(self.TITLE)
        self.cv.create_text(20, 56, text="昵称", anchor="w",
                            fill=rgb(t.pal.sub), font=t.f(9, False, s))
        self.cv.create_text(20, 132, text="留空不保存；最多 24 字", anchor="w",
                            fill=rgb(t.pal.muted), font=t.f(8, False, s))
        self._finish()
        self._build_entry()               # Entry 不吃 cv.scale，必须物理坐标

    def _build_entry(self):
        t, s = self.theme, self.S
        self.entry = tk.Entry(self.root, bd=0, relief="flat",
                              highlightthickness=1,
                              highlightbackground=rgb(t.pal.sec_border),
                              bg=rgb(t.pal.card), fg=rgb(t.pal.text),
                              insertbackground=rgb(t.pal.text),
                              font=t.f(10, False, s))
        self.entry.insert(0, self.current)
        self.entry.select_range(0, "end")
        self.cv.create_window(72 * s, 56 * s, window=self.entry, anchor="w",
                              width=int(268 * s), height=int(24 * s))
        self.entry.bind("<Return>", lambda e: self._ok())
        self.entry.bind("<Escape>", lambda e: self.close())
        self.entry.focus_set()

    def _ok(self):
        try:
            text = self.entry.get().strip()[:24]
        except Exception:
            text = ""
        if text:
            if self.key.startswith("g:"):          # 合并分组的名字走分组配置
                config.set_group_name(self.key[2:], text)
                self.panel.app.refresh()
            else:
                self.panel.app.rename_device(self.key, text)
        self.close()


class RenameIdDialog(RenameDialog):
    """改卡片编号（蓝牙地址 / 2.4G 设备号）：只改显示文本，不动设备配置。"""

    TITLE = "修改编号"

    def draw(self):
        t, s = self.theme, self.S
        self._frame(self.TITLE)
        self.cv.create_text(20, 56, text="编号", anchor="w",
                            fill=rgb(t.pal.sub), font=t.f(9, False, s))
        self.cv.create_text(20, 132, text="留空恢复默认；最多 32 字", anchor="w",
                            fill=rgb(t.pal.muted), font=t.f(8, False, s))
        self._finish()
        self._build_entry()

    def _ok(self):
        try:
            text = self.entry.get().strip()[:32]
        except Exception:
            text = ""
        if text:
            config.set_custom_id(self.key, text)
        else:
            config.clear_custom_id(self.key)
        try:
            self.panel.render()
        except Exception:
            pass
        self.close()


class ColorDialog(_Base):
    LW, LH = 460, 360
    DONE_XY = (330, 300)
    GROUPS = (("text", "软件内文字"), ("ui", "UI 颜色"), ("battery", "电池颜色"))
    DROP = (318, 48, 122, 26)          # 设备下拉：x, y, w, h（逻辑像素）

    def __init__(self, panel):
        self.group = "text"
        self.target = ""               # "" = 全部设备（默认）；否则是设备 key
        self.hue = 0.6
        self._updating = False
        self._render_job = None
        self.entries = {}
        super().__init__(panel, "颜色")

    # -- 当前颜色 ---------------------------------------------------------
    def _cfg(self) -> dict:
        return config.get_colors(not self.app.theme_light)

    def _current(self):
        if self.group == "battery" and self.target:
            got = config.get_device_color(self.target)
            if got:
                return list(got)
        data = self._cfg().get(self.group)
        if data:
            try:
                return [max(0, min(255, int(x))) for x in data]
            except Exception:
                pass
        t = self.theme.pal
        if self.group == "text":
            return list(t.text)
        if self.group == "ui":
            return list(t.primary)
        return list(t.level_full)

    # -- 绘制（静态一次 + 动态局部）----------------------------------------
    def draw(self):
        t = self.theme
        s = self.S
        self._frame("颜色")
        cv = self.cv

        for i, (gid, label) in enumerate(self.GROUPS):
            x = 20 + i * 100
            on = (gid == self.group)
            tag = f"cg_{gid}"
            draw_round_rect(cv, x, 48, x + 92, 74, 6,
                            fill=rgb(t.pal.primary if on else t.pal.sec_bg),
                            outline=rgb(t.pal.primary if on
                                        else t.pal.sec_border), tags=(tag,))
            vcenter_text(cv, x + 46, 61, label,
                         rgb(t.pal.on_primary if on else t.pal.sec_text),
                         t.f(9, False, s), tags=(tag,))
            cv.tag_bind(tag, "<Button-1>", lambda e, g=gid: self._pick(g))

        for i, ch in enumerate("RGB"):
            cv.create_text(240, 100 + i * 34 + 10, text=ch, anchor="w",
                           fill=rgb(t.pal.sub), font=t.f(9, False, s))

        # 预览框（静态占位，动态只改 fill / text）
        draw_round_rect(cv, 240, 210, 268, 238, 4, fill=rgb(tuple(self._current())),
                        outline=rgb(t.pal.sec_border), tags=("preview_box",))
        self.preview = cv.create_text(276, 224, anchor="w", text="",
                                      fill=rgb(t.pal.sub),
                                      font=t.f(9, False, s),
                                      tags=("preview_text",))
        self._btns()
        self._draw_device_picker()

        self._finish()
        self._place_images()
        self._build_entries()
        self.update_dynamic(sync_entries=True)

    # -- 设备下拉（只对「电池颜色」有意义）--------------------------------
    def _target_label(self) -> str:
        if not self.target:
            return "全部设备（默认）"
        r = self.app.snapshot().get(self.target)
        return (r.name if r and r.name else self.target)

    def _draw_device_picker(self):
        if self.group != "battery":
            return
        t, s = self.theme, self.S
        x, y, w, h = self.DROP
        tag = "cc_devpick"
        draw_round_rect(self.cv, x, y, x + w, y + h, 6,
                        fill=rgb(t.pal.sec_bg), outline=rgb(t.pal.sec_border),
                        tags=(tag,))
        self.cv.create_text(
            x + 8, y + h / 2,
            text=_fit_text(self.cv, self._target_label(), t.f(9, False, s),
                           (w - 24) * s),
            anchor="w", fill=rgb(t.pal.sec_text),
            font=t.f(9, False, s), tags=(tag,))
        self.cv.create_text(x + w - 10, y + h / 2, text="▾", anchor="e",
                            fill=rgb(t.pal.sub), font=t.f(9, False, s),
                            tags=(tag,))
        self.cv.tag_bind(tag, "<Button-1>", lambda e: self._open_device_menu())
        self.cv.tag_bind(tag, "<Enter>",
                         lambda e: self.cv.configure(cursor="hand2"))
        self.cv.tag_bind(tag, "<Leave>",
                         lambda e: self.cv.configure(cursor=""))

    def _open_device_menu(self):
        from .menu import PopupMenu
        items = [("全部设备（默认）", lambda: self._set_target(""))]
        for key, r in self.app.snapshot().items():
            if key.startswith("g:"):
                continue
            items.append((r.name or key, lambda k=key: self._set_target(k)))
        ax = self.root.winfo_rootx() + int(self.DROP[0] * self.S)
        ay = (self.root.winfo_rooty()
              + int((self.DROP[1] + self.DROP[3] + 4) * self.S))
        PopupMenu(self.panel, items, theme=self.theme).tk_popup(ax, ay)

    def _set_target(self, key: str):
        self.target = key or ""
        self.draw()                       # 标签 / 当前色 / 预览都要跟着换

    def _place_images(self):
        """色相条 / SV 方块按物理尺寸创建（cv.scale 不会放大图片）。"""
        s = self.S
        size = int(150 * s)
        self.hue = customize.hue_of(self._current())
        sv = customize.sv_square(self.hue, size)
        self._img_cache["sv"] = customize.to_photo(sv)
        hb = customize.hue_bar(int(18 * s), size)
        self._img_cache["hb"] = customize.to_photo(hb)
        if self._img_cache["sv"] is not None:
            self._sv_item = self.cv.create_image(
                20 * s, 90 * s, image=self._img_cache["sv"], anchor="nw")
        else:
            self._sv_item = None
        if self._img_cache["hb"] is not None:
            self.cv.create_image(178 * s, 90 * s,
                                 image=self._img_cache["hb"], anchor="nw")

    def _build_entries(self):
        """Entry 不吃 cv.scale，必须用物理坐标单独摆。"""
        t = self.theme
        s = self.S
        cur = self._current()
        cv = self.cv
        for e in self.entries.values():
            try:
                e.destroy()
            except Exception:
                pass
        self.entries = {}
        for i, ch in enumerate("RGB"):
            e = tk.Entry(cv, bd=0, relief="flat", highlightthickness=1,
                         highlightbackground=rgb(t.pal.sec_border),
                         bg=rgb(t.pal.card), fg=rgb(t.pal.text),
                         insertbackground=rgb(t.pal.text),
                         justify="center", font=t.f(9, False, s))
            e.insert(0, str(cur[i]))
            cv.create_window(268 * s, (100 + i * 34 + 10) * s, window=e,
                             width=int(52 * s), height=int(20 * s))
            e.bind("<Return>", lambda ev: self._from_entries())
            e.bind("<FocusOut>", lambda ev: self._from_entries())
            self.entries[ch] = e

    def _btns(self):
        """「完成」由 _frame 统一画在 DONE_XY，这里只放「恢复默认」。"""
        t = self.theme
        x, label, cmd, tag = 240, "恢复默认", self._reset, "cc_reset"
        draw_round_rect(self.cv, x, 300, x + 76, 326, 6,
                        fill=rgb(t.pal.sec_bg),
                        outline=rgb(t.pal.sec_border), tags=(tag,))
        vcenter_text(self.cv, x + 38, 313, label, rgb(t.pal.sec_text),
                     t.f(9, False, self.S), tags=(tag,))
        self.cv.tag_bind(tag, "<Button-1>", lambda e, c=cmd: c())

    # -- 动态部分 ---------------------------------------------------------
    def update_dynamic(self, sync_entries: bool = False):
        """只改 marker / 预览色 / 输入框文字，不整窗重画。"""
        cur = self._current()
        s = self.S
        cv = self.cv
        cv.delete("sv_marker")
        sat, val = customize.sv_of(cur)
        x = (20 + sat * 150) * s
        y = (90 + (1 - val) * 150) * s
        r = 6 * s
        cv.create_oval(x - r, y - r, x + r, y + r, outline="#FFFFFF",
                       width=max(1, int(round(2 * s))), tags=("sv_marker",))
        cv.create_oval(x - r - s, y - r - s, x + r + s, y + r + s,
                       outline="#333333", width=1, tags=("sv_marker",))
        cv.itemconfigure("preview_box", fill=rgb(tuple(cur)))
        lum = 0.299 * cur[0] + 0.587 * cur[1] + 0.114 * cur[2]
        cv.itemconfigure("preview_text",
                         text="#%02X%02X%02X" % tuple(cur),
                         fill=rgb(self.theme.pal.text if lum > 150
                                  else self.theme.pal.sub))
        if sync_entries and not self._updating:
            self._updating = True
            try:
                for i, ch in enumerate("RGB"):
                    e = self.entries.get(ch)
                    if e is None:
                        continue
                    e.delete(0, "end")
                    e.insert(0, str(cur[i]))
            finally:
                self._updating = False

    def _refresh_sv(self):
        s = self.S
        size = int(150 * s)
        photo = customize.to_photo(customize.sv_square(self.hue, size))
        if photo is not None:
            self._img_cache["sv"] = photo
            if self._sv_item is not None:
                self.cv.itemconfigure(self._sv_item, image=photo)

    # -- 交互 -------------------------------------------------------------
    def _pick(self, group: str):
        self.group = group
        self.draw()

    def _in_sv(self, lx, ly) -> bool:
        return 20 <= lx <= 172 and 88 <= ly <= 242

    def _in_hue(self, lx, ly) -> bool:
        return 174 <= lx <= 200 and 88 <= ly <= 242

    def _press(self, e):
        lx, ly = e.x / self.S, e.y / self.S
        if self._in_sv(lx, ly):
            self._set_sv((lx - 20) / 150, (ly - 90) / 150)
            return
        if self._in_hue(lx, ly):
            self._set_hue((ly - 90) / 150)
            return
        super()._press(e)

    def _drag(self, e):
        lx, ly = e.x / self.S, e.y / self.S
        if self._in_sv(lx, ly):
            self._set_sv((lx - 20) / 150, (ly - 90) / 150)
            return
        if self._in_hue(lx, ly):
            self._set_hue((ly - 90) / 150)
            return
        super()._drag(e)

    def _set_hue(self, v: float):
        hue = max(0.0, min(1.0, v))
        if abs(hue - self.hue) < 1e-4:
            return
        self.hue = hue
        self._refresh_sv()                 # 只换一张图，不重建 Entry
        sat, val = customize.sv_of(self._current())   # 保留当前饱和度/明度
        rgbv = customize.hsv_to_rgb(self.hue, sat, val)
        self._save(rgbv)
        self.cv.itemconfigure("preview_box", fill=rgb(tuple(rgbv)))
        self.cv.itemconfigure("preview_text",
                              text="#%02X%02X%02X" % tuple(rgbv))
        self.cv.delete("sv_marker")
        x = (20 + sat * 150) * self.S
        y = (90 + (1 - val) * 150) * self.S
        r = 6 * self.S
        self.cv.create_oval(x - r, y - r, x + r, y + r, outline="#FFFFFF",
                            width=max(1, int(round(2 * self.S))),
                            tags=("sv_marker",))
        self.cv.create_oval(x - r - self.S, y - r - self.S,
                            x + r + self.S, y + r + self.S,
                            outline="#333333", width=1, tags=("sv_marker",))

    def _set_sv(self, s: float, v: float):
        s = max(0.0, min(1.0, s))
        v = max(0.0, min(1.0, 1.0 - v))
        rgbv = customize.hsv_to_rgb(self.hue, s, v)
        self._save(rgbv)
        self.update_dynamic(sync_entries=True)

    def _from_entries(self):
        if self._updating or not self.entries:
            return
        try:
            vals = [int(self.entries[c].get()) for c in "RGB"]
        except Exception:
            self.update_dynamic(sync_entries=True)   # 输入非法 → 回填原值
            return
        vals = [max(0, min(255, v)) for v in vals]
        self.hue = customize.hue_of(vals)
        self._refresh_sv()
        self._save(vals)
        self.update_dynamic(sync_entries=True)

    def _save(self, rgbv, live: bool = True):
        """写配置；主面板重绘做节流（拖动时每帧重绘整窗会吃掉 10ms+）。"""
        if self.group == "battery" and self.target:
            config.set_device_color(self.target, rgbv)
        else:
            data = self._cfg()
            data[self.group] = list(rgbv)
            config.set_colors(data, not self.app.theme_light)
        if live:
            self._schedule_panel_render()
        else:
            self.panel.render()

    def _schedule_panel_render(self):
        if self._render_job is not None:
            return                       # 已经排队了，合并这一次
        try:
            self._render_job = self.root.after(100, self._flush_panel_render)
        except Exception:
            self._render_job = None
            self.panel.render()

    def _flush_panel_render(self):
        self._render_job = None
        try:
            self.panel.render()
        except Exception:
            pass
        tray = getattr(self.app, "tray", None)      # 设备色也要同步到托盘
        if tray is not None:
            try:
                tray.refresh_icons()
            except Exception:
                pass

    def close(self):
        if self._render_job is not None:
            try:
                self.root.after_cancel(self._render_job)
            except Exception:
                pass
            self._render_job = None
        super().close()

    def _reset(self):
        if self.group == "battery" and self.target:
            config.clear_device_color(self.target)
            self.panel.render()
            self.draw()
            return
        data = self._cfg()
        data.pop(self.group, None)
        config.set_colors(data, not self.app.theme_light)
        self.panel.render()
        self.draw()


class BackgroundDialog(_Base):
    """「自定义背景」单窗：卡片背景 / 软件背景 两个大按钮。

    从某张卡的右键菜单进来时 key 已给定，直接作用于这张卡；
    从头部「设置」进来时 pane 内可用 ◀ ▶ 换目标设备（不再套一层菜单）。
    """

    LW, LH = 420, 220
    DONE_XY = (336, 166)

    def __init__(self, panel, key: str = ""):
        self.key = key or ""
        self._idx = 0
        self._keys = [k for k in panel.app.snapshot() if not k.startswith("g:")]
        if self.key in self._keys:
            self._idx = self._keys.index(self.key)
        super().__init__(panel, "自定义背景")

    # -- 目标设备 ---------------------------------------------------------
    def _target(self) -> str:
        if self.key:
            return self.key
        if not self._keys:
            return ""
        self._idx = max(0, min(self._idx, len(self._keys) - 1))
        return self._keys[self._idx]

    def _target_name(self) -> str:
        k = self._target()
        r = self.app.snapshot().get(k) if k else None
        return (r.name if r and r.name else k) or "（没有设备）"

    def _cycle(self, step: int):
        if not self._keys:
            return
        self._idx = (self._idx + step) % len(self._keys)
        self.draw()

    def draw(self):
        t = self.theme
        s = self.S
        self._frame("自定义背景")
        cv = self.cv
        if self.key:
            cv.create_text(20, 52, text=f"作用于卡片：{self._target_name()}",
                           anchor="w", fill=rgb(t.pal.sub),
                           font=t.f(9, False, s))
        else:
            cv.create_text(20, 52, text="目标卡片", anchor="w",
                           fill=rgb(t.pal.sub), font=t.f(9, False, s))
            cv.create_text(150, 52, text=self._target_name(), anchor="w",
                           fill=rgb(t.pal.text), font=t.f(9, True, s))
            Button(cv, t, s, 92, 42, 22, 20, "◀",
                   lambda: self._cycle(-1), kind="link").draw()
            Button(cv, t, s, 330, 42, 22, 20, "▶",
                   lambda: self._cycle(1), kind="link").draw()

        Button(cv, t, s, 40, 78, 160, 64, "卡片背景",
               self._pick_card, kind="secondary").draw()
        Button(cv, t, s, 220, 78, 160, 64, "软件背景",
               self._pick_app, kind="secondary").draw()
        cv.create_text(210, 158, text="点一下就选图，选完立即生效",
                       anchor="center", fill=rgb(t.pal.muted),
                       font=t.f(8, False, s))
        self._finish()

    # -- 选图 -------------------------------------------------------------
    def _pick_card(self):
        key = self._target()
        if not key:
            from tkinter import messagebox
            messagebox.showinfo("卡片背景", "还没有设备。", parent=self.root)
            return
        self.close()
        self.panel._pick_card_bg(key)

    def _pick_app(self):
        self.close()
        self.panel._pick_app_bg()
