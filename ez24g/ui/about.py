# -*- coding: utf-8 -*-
"""关于窗口：软件名、项目地址、许可与一句话定位。"""
from __future__ import annotations
from .. import config
from . import manual as _m
from .draw import rgb

ABOUT_URL = "https://github.com/SnowYukikaze/Spark-Monitor"

# 复制成功后临时替换成这句，1.6 秒再换回来
COPIED_TEXT = "已复制到剪贴板 ✓"


class AboutWindow(_m._ManualBase):
    LW, LH = 440, 340

    def __init__(self, panel):
        super().__init__(panel, "关于 " + config.APP_NAME,
                         "你所见的，皆由你定")

    def draw(self):
        t = self.theme
        cv = self.cv
        self._frame()
        y = self._lines([
            ("h", "软件名称"),
            ("b", config.APP_NAME),
            ("h", "项目地址"),
        ])
        # 地址单独画：它是唯一可点的元素，点一下把地址复制走，
        # 省得用户手抄或截图识别（以前只能看，不能复制）。
        self._url_item = cv.create_text(
            24, y, text=ABOUT_URL, anchor="nw", width=self.LW - 48,
            justify="left", fill=rgb(t.pal.primary),
            font=t.f(9, False, self.S), tags=("about_url",))
        try:
            bb = cv.bbox(self._url_item)
            h = (bb[3] - bb[1]) if bb else 18
        except Exception:
            h = 18
        y += max(21, h + 5)

        y = self._lines([
            ("h", "许可"),
            ("b", "MIT License · 可自由使用、修改、再发布，也可商用"),
            ("b", "欢迎二改，希望同样开源，但不强制。"),
        ], y=y)

        cv.create_text(24, self.LH - 18,
                       text="点地址即可复制 · 右上角 ✕ 或 Esc 关闭",
                       anchor="w", fill=rgb(t.pal.muted),
                       font=t.f(8, False, self.S))
        cv.tag_bind("about_url", "<Button-1>", self._copy_url)
        cv.tag_bind("about_url", "<Enter>", self._url_hover)
        cv.tag_bind("about_url", "<Leave>", self._url_leave)
        self._finish()

    def _url_hover(self, e=None):
        self.cv.itemconfigure(self._url_item,
                              fill=rgb(self.theme.pal.text))
        self.cv.configure(cursor="hand2")

    def _url_leave(self, e=None):
        self.cv.itemconfigure(self._url_item,
                              fill=rgb(self.theme.pal.primary))
        self.cv.configure(cursor="")

    # -- 点击复制 ---------------------------------------------------------
    def _copy_url(self, e=None):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(ABOUT_URL)
            self.root.update_idletasks()
        except Exception:
            return
        try:
            self.cv.itemconfigure(self._url_item, text=COPIED_TEXT)
            self.root.after(1600, self._restore_url)
        except Exception:
            pass

    def _restore_url(self):
        """1.6 秒后把文字换回地址；窗口已经关了就算了。"""
        try:
            if self.is_alive() and getattr(self, "_url_item", None):
                self.cv.itemconfigure(self._url_item, text=ABOUT_URL)
        except Exception:
            pass
