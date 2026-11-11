# -*- coding: utf-8 -*-
"""手动识别 / 添加未识别设备：选择窗口与两类引导窗口。

风格与主面板一致（无边框圆角 Canvas 窗），右上角 ✕ 与 Esc 都能退出。
引导统一按「要做什么 → 按什么键 → 按完怎么做 → 怎样算完成」四段写清楚，
不跳步、不要求用户猜术语。
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import messagebox

from .. import config, devices
from ..win32 import set_round_region
from .draw import draw_round_rect, rgb
from .theme import get_theme
from .widgets import Button


GUIDE_BT = [
    ("h", "第 1 步 · 先确认设备连着"),
    ("b", "打开 Windows「设置 → 蓝牙和其他设备」，确认这台设备显示「已连接」。"),
    ("b", "要按的键：没连上时，长按耳机 / 键盘上的配对键，直到指示灯闪烁，"),
    ("b", "再在 Windows 里点一下「连接」。"),
    ("h", "第 2 步 · 在下面选一种填法"),
    ("b", "① 只想让卡片显示个数：填 0~100 的数字，例如 85。"),
    ("b", "② 想让软件自己读：填 gatt:服务:特征，例如 gatt:180F:2A19。"),
    ("b", "③ 想恢复自动识别：把下面清空，再点「保存」。"),
    ("h", "第 3 步 · 保存后怎么做"),
    ("b", "点「保存」，软件会立刻重新读一遍设备，这个窗口自动关闭。"),
    ("h", "怎样算完成"),
    ("b", "卡片上出现电量数字；填数字时状态写「手动」，不再写读不到。"),
    ("h", "最终方法（都不行时）"),
    ("b", "试完所有办法仍不出电量，就把本项目源码拷给大肥鱼或任意AI，"),
    ("b", "指出读不到电量的设备让他修好即可（项目地址在「关于」里）。"),
]

GUIDE_ADD_2G = [
    ("h", "第 1 步 · 找到设备的编号"),
    ("b", "右键「开始」→ 设备管理器 → 展开「人体学输入设备」或"),
    ("b", "「通用串行总线设备」，找到这台设备。"),
    ("b", "要按的键：右键它 → 属性 → 「详细信息」→ 属性下拉选"),
    ("b", "「设备实例路径」；里面 VID_046D&PID_C547 这串，"),
    ("b", "046D 就是 VID，C547 就是 PID（各 4 位）。"),
    ("h", "第 2 步 · 手填，或从右边的系统清单里点一行"),
    ("b", "两种办法都在这一页：填好下面两格，或点清单里的一行自动填。"),
    ("b", "按完清单 / 填完，点左下角「添加设备」。"),
    ("h", "怎样算完成"),
    ("b", "窗口自动关闭，面板上多出这台设备的卡片；能读到就直接显示电量。"),
    ("h", "最终方法（都不行时）"),
    ("b", "试完所有办法仍不出电量，就把本项目源码拷给大肥鱼或任意AI，"),
    ("b", "指出读不到电量的设备让他修好即可（项目地址在「关于」里）。"),
]

GUIDE_ADD_BT = [
    ("h", "第 1 步 · 找到蓝牙地址（MAC）"),
    ("b", "打开 Windows「设置 → 蓝牙和其他设备」，点开这台设备。"),
    ("b", "在设备属性里找「蓝牙地址」，形如 AA:BB:CC:DD:EE:FF，共 6 段。"),
    ("b", "要按的键：设备关着时先长按配对键，让指示灯闪烁，"),
    ("b", "Windows 才会列出它，右边清单里也才会出现。"),
    ("h", "第 2 步 · 手填，或从右边的系统清单里点一行"),
    ("b", "填好下面两格，或点清单里已配对的一行自动填。"),
    ("b", "填完点左下角「添加设备」。"),
    ("h", "怎样算完成"),
    ("b", "窗口自动关闭，面板上多出这台蓝牙设备的卡片。"),
    ("h", "最终方法（都不行时）"),
    ("b", "试完所有办法仍不出电量，就把本项目源码拷给大肥鱼或任意AI，"),
    ("b", "指出读不到电量的设备让他修好即可（项目地址在「关于」里）。"),
]


class _ManualBase:
    LW, LH = 560, 500

    def __init__(self, panel, title, subtitle):
        self.panel = panel
        self.title_text = title
        self.sub_text = subtitle
        self.S = min(getattr(panel, "S", 1.0), 1.25)
        self.theme = get_theme(panel.app.theme_light)
        self.W, self.H = int(self.LW * self.S), int(self.LH * self.S)
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
        self._press_pos = None
        self._widgets = []
        self._focused_once = False
        # 滚动：正文超出一屏时用滚轮 + 右侧细条，标题栏和关闭按钮不动。
        self._base_items: set = set()      # _frame() 画完时的「固定元素」
        self._scroll_off = 0.0             # 正文已经上移多少（物理像素）
        self._scroll_range = 0.0           # 还能再滚多少
        self._thumb = 0
        self._sbar = (0, 0, 0)             # (top, bottom, thumb_h)
        self._place_y: list = []           # [(widget, 基准 y 物理像素)]
        self._relaid = False               # 已经按内容调过一次高度
        self.cv.bind("<Button-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonRelease-1>",
                     lambda e: setattr(self, "_press_pos", None))
        self.root.bind("<Escape>", lambda e: self.close())
        self.root.transient(panel.root)
        from .ontop import raise_above
        raise_above(self.root, panel.root.winfo_id())
        self.draw()
        # 无边框置顶窗带「不激活」标志，系统不会把它设为前台；窗口没被激活
        # 时连 focus_get() 都是 None，键盘消息不会送进来。显式激活并把光标
        # 交给第一个输入框，否则点了输入框也打不了字。
        try:
            self.root.focus_force()
            for _box in self._widgets:
                if isinstance(_box, tk.Entry):
                    _box.focus_set()
                    break
        except Exception:
            pass

    def _place(self):
        sw, sh = (self.root.winfo_screenwidth(),
                  self.root.winfo_screenheight())
        try:
            px, py = (self.panel.root.winfo_rootx(),
                      self.panel.root.winfo_rooty())
            pw, ph = (self.panel.root.winfo_width(),
                      self.panel.root.winfo_height())
        except Exception:
            px = py = pw = ph = 0
        x = px - self.W - 12
        if x < 8:
            x = min(max(8, px + (pw - self.W) // 2), sw - self.W - 8)
        y = min(max(8, py + (ph - self.H) // 2), sh - self.H - 48)
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")

    # -- 窗口拖动（只限标题区）--------------------------------------------
    def _press(self, e):
        if e.y / self.S <= 62:
            self._press_pos = (e.x_root, e.y_root,
                               self.root.winfo_x(), self.root.winfo_y())

    def _drag(self, e):
        if not self._press_pos:
            return
        x0, y0, wx, wy = self._press_pos
        self.root.geometry(f"+{wx + e.x_root - x0}+{wy + e.y_root - y0}")

    # -- 绘制工具 ---------------------------------------------------------
    def _clear_widgets(self):
        for w in self._widgets:
            try:
                w.destroy()
            except Exception:
                pass
        self._widgets = []

    def _frame(self):
        t = self.theme
        cv = self.cv
        self._clear_widgets()
        cv.delete("all")
        draw_round_rect(cv, 0, 0, self.LW, self.LH, 16,
                        fill=rgb(t.pal.card), outline=rgb(t.pal.border))
        cv.create_text(24, 26, text=self.title_text, anchor="w",
                       fill=rgb(t.pal.text), font=t.f(13, True, self.S))
        cv.create_text(24, 48, text=self.sub_text, anchor="w",
                       fill=rgb(t.pal.sub), font=t.f(9, False, self.S))
        cv.create_line(24, 66, self.LW - 24, 66, fill=rgb(t.pal.border))
        cv.create_oval(self.LW - 40, 16, self.LW - 14, 42,
                       fill=rgb(t.pal.sec_bg),
                       outline=rgb(t.pal.sec_border), tags=("mclose",))
        cv.create_text(self.LW - 27, 29, text="✕", fill=rgb(t.pal.text),
                       font=t.f(9, True, self.S), tags=("mclose",))
        cv.tag_bind("mclose", "<Button-1>", lambda e: self.close())
        # 到这里为止是「固定层」：背景、标题、分隔线、关闭按钮，滚动时不动。
        # 之后子类画的内容都算正文，超出可视区时整体平移。
        self._base_items = set(cv.find_all())
        self._body_ids = []
        self._place_y = []
        self._scroll_off = 0.0
        self._scroll_range = 0.0
        self._thumb = 0

    def _lines(self, items, x=24, y=82, width=None, gap=21, head_gap=27):
        """按内容实际高度排版，长句自动换行也不会和下一行叠在一起。"""
        t = self.theme
        cv = self.cv
        width = width or (self.LW - 48)
        for kind, text in items:
            item = cv.create_text(
                x, y, text=text, anchor="nw", width=width, justify="left",
                fill=rgb(t.pal.text if kind == "h" else t.pal.sub),
                font=t.f(10 if kind == "h" else 9, kind == "h", self.S))
            try:
                bb = cv.bbox(item)
                h = (bb[3] - bb[1]) if bb else gap
            except Exception:
                h = gap
            y += max(head_gap if kind == "h" else gap, h + 5)
        return y

    def _entry(self, x, y, w, default=""):
        pal = self.theme.pal
        # 明显的淡蓝底（深色主题用深蓝灰），一眼能看出这里可以输入
        light = bool(getattr(self.panel.app, "theme_light", True))
        field_bg = (232, 240, 252) if light else (38, 48, 62)
        e = tk.Entry(self.root, font=("Microsoft YaHei UI", 10),
                     relief="solid", bd=1,
                     bg=rgb(field_bg), fg=rgb(pal.text),
                     insertbackground=rgb(pal.text),
                     highlightthickness=1,
                     highlightbackground=rgb(pal.sec_border),
                     highlightcolor=rgb(pal.primary),
                     selectbackground=rgb(pal.primary),
                     selectforeground=rgb(pal.on_primary))
        if default:
            e.insert(0, default)
        e.place(x=int(x * self.S), y=int(y * self.S),
                width=int(w * self.S), height=int(27 * self.S))
        self._widgets.append(e)
        # 记下基准位置：正文滚动时 Entry 是真实控件，得单独跟着平移，
        # 光靠 Canvas 的 move() 带不走它。
        self._place_y.append((e, int(y * self.S), int(27 * self.S)))
        # 无边框置顶窗不会自动把键盘交给子控件：打开时把光标放进第一个框，
        # 点任意一个框也能把光标叫回来（add="+" 保留 Entry 原有的点击定位）。
        e.bind("<Button-1>", lambda ev, box=e: box.focus_set(), add="+")
        if not self._focused_once:
            e.focus_set()
            self._focused_once = True
        return e

    def _listbox(self, x, y, w, h, values, on_pick):
        box = tk.Listbox(self.root, font=("Microsoft YaHei UI", 9),
                         relief="flat", bd=1, activestyle="dotbox")
        for v in values:
            box.insert(tk.END, v)
        box.place(x=int(x * self.S), y=int(y * self.S),
                  width=int(w * self.S), height=int(h * self.S))
        box.bind("<<ListboxSelect>>", on_pick)
        self._widgets.append(box)
        self._place_y.append((box, int(y * self.S), int(h * self.S)))
        return box

    def _btn(self, x, y, w, h, text, cmd, kind="primary"):
        return Button(self.cv, self.theme, self.S, x, y, w, h,
                      text, cmd, kind=kind).draw()

    def _finish(self):
        self.cv.scale("all", 0, 0, self.S, self.S)
        self._after_layout()

    # -- 内容装不下：先长高，再不行才出滚动条 -----------------------------
    def _body_items(self):
        """正文元素（不含固定层、不含滚动条）。"""
        base = self._base_items
        out = []
        for i in self.cv.find_all():
            if i in base:
                continue
            if "sbar" in self.cv.gettags(i):
                continue
            out.append(i)
        return out

    def _grow_to_fit(self) -> bool:
        """内容比窗口高就把窗口撑高（上限屏幕 92%），返回是否改了高度。"""
        body = self._body_items()
        if not body:
            return False
        try:
            bb = self.cv.bbox(*body)
        except Exception:
            return False
        if not bb:
            return False
        # Entry / Listbox 是真实 tk 控件，不算在 Canvas bbox 里，
        # 只看 bbox 会把「底部输入框被切」当成「内容装得下」。
        bottom = bb[3]
        for _w, _y, _h in self._place_y:
            bottom = max(bottom, _y + _h)
        need = bottom + int(12 * self.S)
        if need <= self.H:
            return False
        sh = self.root.winfo_screenheight()
        target = min(int(need), int(sh * 0.92))
        if target <= self.H:
            return False
        self.H = target
        self.LH = int(round(target / self.S))
        # Canvas 是固定尺寸创建后 pack 的，窗口变大它不会自己跟着长，
        # 不显式改尺寸的话内容仍旧画在旧画布上、下半截照样被裁。
        try:
            self.cv.configure(width=self.W, height=self.H)
        except Exception:
            pass
        y = min(self.root.winfo_y(), max(8, sh - self.H - 40))
        self.root.geometry(f"{self.W}x{self.H}+{self.root.winfo_x()}+{max(8, y)}")
        try:
            set_round_region(self.hwnd, self.W, self.H, int(20 * self.S))
        except Exception:
            pass
        return True

    def _after_layout(self):
        if not self._relaid and self._grow_to_fit():
            self._relaid = True
            self.draw()            # 高度变了，整窗按新尺寸重画一次
            return
        self._setup_scroll()

    def _setup_scroll(self):
        """正文仍超出可视区时，挂滚轮 + 右侧细滚动条。"""
        self._scroll_off = 0.0
        self._scroll_range = 0.0
        self._thumb = 0
        body = self._body_items()
        self._body_ids = body
        if not body:
            return
        try:
            bb = self.cv.bbox(*body)
        except Exception:
            return
        if not bb:
            return
        top = int(74 * self.S)
        bottom = self.H - int(8 * self.S)
        view = bottom - top
        body_bottom = bb[3]
        for _w, _y, _h in self._place_y:
            body_bottom = max(body_bottom, _y + _h)
        need = body_bottom - top
        if view <= 0 or need <= view + 2:
            return                 # 装得下：不画滚动条，也不绑滚轮
        self._scroll_range = need - view
        self._draw_scrollbar(top, bottom, view)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.cv.bind(seq, self._on_wheel)

    def _draw_scrollbar(self, top, bottom, view):
        pal = self.theme.pal
        x = self.W - int(7 * self.S)
        wdt = max(3, int(3 * self.S))
        self.cv.create_line(x, top, x, bottom, width=wdt,
                            fill=rgb(pal.border), capstyle="round",
                            tags=("sbar",))
        total = view + self._scroll_range
        h = max(int(28 * self.S), int(view * view / max(1, total)))
        self._thumb = self.cv.create_line(x, top, x, top + h, width=wdt,
                                          fill=rgb(pal.sub), capstyle="round",
                                          tags=("sbar",))
        self._sbar = (top, bottom, h)
        self.cv.tag_raise("sbar")

    def _on_wheel(self, e):
        if self._scroll_range <= 0:
            return None
        num = getattr(e, "num", None)
        # 滚轮向上 = 想看前面的内容 = 正文往下移（dy 为正），别搞反。
        if num == 4:
            step = 1
        elif num == 5:
            step = -1
        else:
            step = 1 if getattr(e, "delta", 0) > 0 else -1
        self._scroll_by(step * int(46 * self.S))
        return "break"

    def _scroll_by(self, dy):
        if self._scroll_range <= 0:
            return
        lo = -self._scroll_range - self._scroll_off
        hi = -self._scroll_off
        dy = max(lo, min(hi, dy))
        if not dy:
            return
        self._scroll_off += dy
        for i in self._body_ids:
            try:
                self.cv.move(i, 0, dy)
            except Exception:
                pass
        for w, base_y, _h in self._place_y:
            try:
                w.place_configure(y=int(base_y + self._scroll_off))
            except Exception:
                pass
        self._move_thumb()

    def _move_thumb(self):
        if not self._thumb:
            return
        top, bottom, h = self._sbar
        view = bottom - top
        total = view + self._scroll_range
        if total <= 0:
            return
        pos = top + int(view * (-self._scroll_off) / total)
        x = self.W - int(7 * self.S)
        try:
            self.cv.coords(self._thumb, x, pos, x, pos + h)
            self.cv.tag_raise("sbar")
        except Exception:
            pass

    def is_alive(self) -> bool:
        try:
            return bool(self.root.winfo_exists())
        except Exception:
            return False

    def bring_front(self):
        """已经开着就提到主窗口之前，不重复创建。"""
        try:
            from .ontop import raise_above
            raise_above(self.root, self.panel.root.winfo_id())
            self.root.lift()
        except Exception:
            pass

    def _dismiss(self):
        """只关窗口、不触发面板重绘（后面马上要开下一个窗口）。"""
        self._clear_widgets()
        try:
            self.root.destroy()
        except Exception:
            pass
        if getattr(self.panel, "_dlg", None) is self:
            self.panel._dlg = None

    def close(self):
        self._dismiss()
        try:
            self.panel._backdrop_epoch += 1
            self.panel.render()
        except Exception:
            pass

    def draw(self):
        raise NotImplementedError


class ChoiceWindow(_ManualBase):
    """「这台设备是哪一类」二选一。"""

    LW, LH = 440, 270

    def __init__(self, panel, title, subtitle, on_pick):
        self.on_pick = on_pick
        super().__init__(panel, title, subtitle)

    def draw(self):
        t = self.theme
        cv = self.cv
        self._frame()
        cards = [
            ("pick_2g", 84, "2.4GHz 设备",
             "用 USB 接收器 / 无线适配器连电脑的鼠标、键盘、耳机"),
            ("pick_bt", 160, "蓝牙设备",
             "Windows 蓝牙里配对的耳机、键盘、手柄"),
        ]
        for tag, y, title, desc in cards:
            draw_round_rect(cv, 24, y, self.LW - 24, y + 62, 10,
                            fill=rgb(t.pal.sec_bg),
                            outline=rgb(t.pal.sec_border), tags=(tag,))
            cv.create_text(40, y + 19, text=title, anchor="w",
                           fill=rgb(t.pal.text),
                           font=t.f(11, True, self.S), tags=(tag,))
            cv.create_text(40, y + 41, text=desc, anchor="w",
                           fill=rgb(t.pal.sub),
                           font=t.f(9, False, self.S), tags=(tag,))
            cv.tag_bind(tag, "<Enter>",
                        lambda e: cv.configure(cursor="hand2"))
            cv.tag_bind(tag, "<Leave>", lambda e: cv.configure(cursor=""))
        cv.tag_bind("pick_2g", "<Button-1>", lambda e: self._pick("2g"))
        cv.tag_bind("pick_bt", "<Button-1>", lambda e: self._pick("bt"))
        cv.create_text(24, self.LH - 20,
                       text="点上面任意一类继续 · 右上角 ✕ 或 Esc 退出",
                       anchor="w", fill=rgb(t.pal.muted),
                       font=t.f(8, False, self.S))
        self._finish()

    def _pick(self, kind):
        cb = self.on_pick
        self._dismiss()
        cb(kind)


class BtManualWindow(_ManualBase):
    """蓝牙：手动指定电量来源（固定值 / GATT 位置 / 取消）。"""

    LW, LH = 560, 620

    def __init__(self, panel, addr, name=""):
        self.addr = addr
        super().__init__(panel, "手动识别蓝牙设备电量",
                         (name or addr) + " · 连着但读不到电量时用它")

    def draw(self):
        t = self.theme
        cv = self.cv
        self._frame()
        y = self._lines(GUIDE_BT)
        cur = config.bt_manual_text(self.addr)
        cv.create_text(24, y + 4, text="填这里：", anchor="w",
                       fill=rgb(t.pal.text), font=t.f(9, True, self.S))
        self.value = self._entry(24, y + 24, self.LW - 48, cur)
        btn_y = max(y + 54, min(y + 62, self.LH - 18 - 40))
        self._btn(24, btn_y, 100, 30, "保存", self._save, "primary")
        self._btn(136, btn_y, 110, 30, "取消绑定", self._clear, "secondary")
        cv.create_text(24, self.LH - 18,
                       text="右上角 ✕ 或 Esc 可随时退出，不保存改动",
                       anchor="w", fill=rgb(t.pal.muted),
                       font=t.f(8, False, self.S))
        self._finish()

    def _save(self):
        text = self.value.get().strip()
        item, err = config.parse_bt_manual(text)
        if err:
            messagebox.showerror("填法不对", err, parent=self.root)
            return
        config.set_bt_manual(self.addr, item)
        try:
            self.panel.app.refresh()
        except Exception:
            pass
        self.close()

    def _clear(self):
        self.value.delete(0, tk.END)
        self._save()


class AddUnknownWindow(_ManualBase):
    """添加软件完全没读到信息的设备：手填 + 系统清单选，同窗并存。"""

    LW, LH = 620, 660

    def __init__(self, panel, kind):
        self.kind = kind
        title = ("添加未识别的 2.4GHz 设备" if kind == "2g"
                 else "添加未识别的蓝牙设备")
        super().__init__(panel, title,
                         "软件完全没读到它 · 手动告诉软件它是谁")

    def _system_items(self):
        out = []
        if self.kind == "2g":
            try:
                from ..hidapi import enumerate_hid
                from ..providers import hid as _hid
                seen = set()
                for d in enumerate_hid():
                    k = (d.vid, d.pid)
                    # 只挡主板内置的 HIDI2C / ACPI，其余外接设备一律保留
                    if k in seen or not d.vid or _hid.is_internal_iface(d):
                        continue
                    seen.add(k)
                    out.append(("%04X:%04X" % (d.vid, d.pid),
                                (d.product or "").strip() or "未命名设备"))
            except Exception:
                pass
        else:
            try:
                from ..providers import bt_pnp
                for item in bt_pnp.enumerate_paired().values():
                    out.append((item.get("addr", ""),
                                (item.get("name") or "").strip() or "蓝牙设备"))
            except Exception:
                pass
        return out

    def draw(self):
        t = self.theme
        cv = self.cv
        self._frame()
        guide = GUIDE_ADD_2G if self.kind == "2g" else GUIDE_ADD_BT
        y = self._lines(guide, width=350)

        # 右侧：系统里看得到的设备，点一行自动填好下面两格
        self._items = self._system_items()
        cv.create_text(396, 82, text="系统里看得到的设备", anchor="nw",
                       fill=rgb(t.pal.text), font=t.f(9, True, self.S))
        labels = ["%s · %s" % (code, name) for code, name in self._items]
        if not labels:
            labels = ["（暂时没读到，可以直接在下面手填）"]
        self._list = self._listbox(396, 104, 200, 210, labels,
                                   self._on_pick)

        cv.create_text(24, y + 4,
                       text=("VID:PID（例如 046D:C547）" if self.kind == "2g"
                             else "蓝牙地址（例如 AA:BB:CC:DD:EE:FF）"),
                       anchor="nw", fill=rgb(t.pal.text),
                       font=t.f(9, True, self.S))
        self.code = self._entry(24, y + 24, 350)
        cv.create_text(24, y + 58, text="给它起个名字（可不填）", anchor="nw",
                       fill=rgb(t.pal.text), font=t.f(9, True, self.S))
        self.name = self._entry(24, y + 78, 350)
        btn_y = max(y + 108, min(y + 116, self.LH - 18 - 40))
        self._btn(24, btn_y, 110, 30, "添加设备", self._add, "primary")
        self._btn(146, btn_y, 90, 30, "取消", self.close, "secondary")
        cv.create_text(24, self.LH - 18,
                       text="右上角 ✕ 或 Esc 可随时退出 · 已添加过的设备不会重复添加",
                       anchor="w", fill=rgb(t.pal.muted),
                       font=t.f(8, False, self.S))
        self._finish()

    def _on_pick(self, e=None):
        try:
            i = self._list.curselection()[0]
            code, name = self._items[i]
        except Exception:
            return
        self.code.delete(0, tk.END)
        self.code.insert(0, code)
        self.name.delete(0, tk.END)
        self.name.insert(0, name)

    def _add(self):
        code = self.code.get().strip()
        name = self.name.get().strip()
        if self.kind == "2g":
            nums = re.findall(r"[0-9A-Fa-f]{4}", code.replace(":", ""))
            if len(nums) < 2:
                messagebox.showerror(
                    "编号不对",
                    "请填 4 位 VID + 4 位 PID，例如 046D:C547。\n"
                    "去设备管理器 → 属性 → 详细信息 → 设备实例路径里找。",
                    parent=self.root)
                return
            ok = devices.add_device(int(nums[0], 16), int(nums[1], 16), name)
        else:
            addr = config.normalize_addr(code)
            if len(addr) != 17 or addr.count(":") != 5:
                messagebox.showerror(
                    "地址不对",
                    "请填 6 段蓝牙地址，例如 AA:BB:CC:DD:EE:FF。\n"
                    "去 Windows 设置 → 蓝牙和其他设备 → 设备属性里找。",
                    parent=self.root)
                return
            ok = devices.add_device(0, 0, name, kind="bt", addr=addr)
        if not ok:
            messagebox.showinfo("已经添加过",
                                "这台设备已经在列表里了，不用再加一次。",
                                parent=self.root)
            return
        try:
            self.panel.app.devices = config.load_saved_devices()
            self.panel.app.refresh()
        except Exception:
            pass
        self.close()
