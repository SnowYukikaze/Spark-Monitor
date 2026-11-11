# -*- coding: utf-8 -*-
"""主题：所有「长什么样」的参数都集中在这里。

想换配色：改 `DARK` / `LIGHT` 两个 Theme 的字段，不用碰绘制代码。
想加一套皮肤：照抄一个 Theme(...) 改数值，在 `get_theme()` 里选。
想改字号/圆角/卡片高度：改 Theme 里的 f_* / radius / card_h。
"""
from __future__ import annotations

import dataclasses
from typing import Tuple

RGB = Tuple[int, int, int]

# 字体缩放（§3.8）：模块级缓存，别放进 Theme.f 里读盘
FS_MIN, FS_MAX, FS_DEFAULT = 0.85, 1.25, 1.0
_font_scale = FS_DEFAULT


def font_scale() -> float:
    return _font_scale


def set_font_scale(v) -> float:
    global _font_scale
    try:
        v = float(v)
    except Exception:
        v = FS_DEFAULT
    _font_scale = max(FS_MIN, min(FS_MAX, v))
    return _font_scale


@dataclasses.dataclass(frozen=True)
class Palette:
    window: RGB          # 窗口底色
    card: RGB            # 卡片底色
    card_hover: RGB      # 卡片悬停
    border: RGB          # 描边
    text: RGB            # 主文字
    sub: RGB             # 次要文字
    muted: RGB           # 更弱的文字（时间戳等）
    track: RGB           # 进度条轨道
    btn: RGB             # 按钮底
    btn_hover: RGB
    accent: RGB          # 强调色
    link: RGB            # 可点文字
    ok: RGB              # 电量 >= high
    warn: RGB            # 电量 >= low
    low: RGB             # 电量 < low
    danger: RGB          # 关闭/删除
    on_danger: RGB
    # —— 新版卡片 / 头部用到的语义色 ——
    model: RGB           # 型号行
    primary: RGB         # 主按钮底
    on_primary: RGB      # 主按钮字
    sec_bg: RGB          # 次按钮底
    sec_border: RGB      # 次按钮边
    sec_text: RGB        # 次按钮字
    status_ok: RGB       # 已连接
    status_charging: RGB  # 充电中
    status_asleep: RGB   # 休眠
    status_offline: RGB  # 未连接
    level_full: RGB      # 满电
    level_low: RGB       # 低电


@dataclasses.dataclass(frozen=True)
class Theme:
    name: str
    pal: Palette
    font_family: str = "Microsoft YaHei UI"

    # 尺寸（逻辑像素，会再乘 DPI 缩放）
    panel_w: int = 760
    panel_h: int = 560
    radius: int = 16
    card_radius: int = 12
    card_h: int = 272
    card_gap: int = 16
    header_h: int = 88
    footer_h: int = 54
    padding: int = 24

    # 电量阈值
    level_high: int = 60
    level_low: int = 30

    def f(self, size: float, bold: bool = False, scale: float = 1.0):
        """Tk 字体元组：负数字号 = 像素单位，跟系统缩放走。

        再乘一个用户选的字体缩放（0.85–1.25）。这条乘算必须留在模块级缓存上，
        config.load_config() 是读文件，每帧读会把 render 拖慢。
        """
        return (self.font_family, -int(round(size * scale * _font_scale)),
                "bold" if bold else "normal")

    def level_color(self, level) -> RGB:
        if level is None:
            return self.pal.muted
        if level >= 100:
            return self.pal.level_full
        if level <= 20:
            return self.pal.level_low
        return self.pal.text

    def battery_color(self, level, override=None) -> RGB:
        """电池图形填充色（数字颜色是另一回事，走 pal.primary）。

        以前电池用的是 level_color()，中间电量（21–99%）返回 pal.text，
        用户改「电池颜色」只覆盖 level_full / level_low，所以几乎看不到变化。

        override = 该设备单独设的颜色（§3.3）。用户显式选过就用它，
        低电也不换成警示色 —— 别「好心」改回按电量变色。
        """
        if override is not None:
            return tuple(override)
        if level is None:
            return self.pal.muted
        if level <= 20:
            return self.pal.level_low          # 低电仍走警示色
        return self.pal.level_full             # 其余全部用「电池颜色」

    def ui_accent(self) -> RGB:
        """电量数字的颜色：跟随「UI 颜色」（用户色会覆盖 pal.primary）。"""
        return self.pal.primary


DARK = Theme(
    name="dark",
    pal=Palette(
        window=(27, 27, 28),
        card=(33, 33, 35),
        card_hover=(38, 38, 41),
        border=(48, 49, 54),
        text=(245, 245, 247),
        sub=(160, 163, 168),
        muted=(120, 123, 128),
        track=(46, 47, 51),
        btn=(67, 69, 74),
        btn_hover=(80, 82, 88),
        accent=(80, 160, 255),
        link=(90, 170, 240),
        ok=(61, 220, 151),
        warn=(228, 184, 99),
        low=(248, 113, 113),
        danger=(196, 43, 28),
        on_danger=(255, 255, 255),
        model=(150, 154, 160),
        primary=(59, 130, 246),
        on_primary=(255, 255, 255),
        sec_bg=(45, 46, 50),
        sec_border=(62, 64, 70),
        sec_text=(226, 228, 232),
        status_ok=(61, 220, 151),
        status_charging=(245, 158, 11),
        status_asleep=(156, 163, 175),
        status_offline=(120, 123, 128),
        level_full=(61, 220, 151),
        level_low=(248, 113, 113),
    ),
)

LIGHT = Theme(
    name="light",
    pal=Palette(
        window=(245, 246, 248),
        card=(255, 255, 255),
        card_hover=(250, 251, 253),
        border=(236, 238, 242),
        text=(31, 35, 40),
        sub=(138, 143, 152),
        muted=(154, 160, 166),
        track=(232, 235, 240),
        btn=(238, 240, 244),
        btn_hover=(226, 230, 236),
        accent=(22, 119, 255),
        link=(24, 110, 210),
        ok=(16, 150, 90),
        warn=(190, 128, 16),
        low=(214, 58, 58),
        danger=(196, 43, 28),
        on_danger=(255, 255, 255),
        model=(154, 160, 166),
        primary=(37, 99, 235),
        on_primary=(255, 255, 255),
        sec_bg=(255, 255, 255),
        sec_border=(229, 231, 235),
        sec_text=(55, 65, 81),
        status_ok=(34, 197, 94),
        status_charging=(245, 158, 11),
        status_asleep=(156, 163, 175),
        status_offline=(196, 199, 204),
        level_full=(34, 197, 94),
        level_low=(239, 68, 68),
    ),
)


def get_theme(light: bool) -> Theme:
    return LIGHT if light else DARK


def hexstr(c: RGB) -> str:
    return "#%02x%02x%02x" % c


# ---------------------------------------------------------------------------
# 用户自定义颜色
# ---------------------------------------------------------------------------
def _triple(v):
    try:
        r, g, b = [int(x) for x in v]
        return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
    except Exception:
        return None


def _mix(a, b, t: float):
    return tuple(int(round(a[i] * (1 - t) + b[i] * t)) for i in range(3))


def with_colors(theme: Theme, data: dict) -> Theme:
    """把用户选的三组颜色套到主题上（返回新的 Theme，不动常量）。

    取色方案（写清楚，别让人猜）：
      软件内文字 → text 用所选色，sub/model/muted 由它向窗口底色淡化得出，
                   保持层级；卡片背景与按钮底不变。
      UI 颜色    → primary / accent / link 用所选色，sec_border 向卡片底混合。
      电池颜色   → ok / level_full / level_low 全部用所选色，
                   所以「满电绿、低电红」的区分会被用户色覆盖（按需求 6.2 的简单方案）。
    """
    if not isinstance(data, dict):
        return theme
    pal = theme.pal
    kw = {}
    text = _triple(data.get("text"))
    ui = _triple(data.get("ui"))
    battery = _triple(data.get("battery"))
    if text:
        kw.update(text=text, sub=_mix(text, pal.window, 0.35),
                  model=_mix(text, pal.window, 0.45),
                  muted=_mix(text, pal.window, 0.6))
    if ui:
        kw.update(primary=ui, accent=ui, link=ui,
                  sec_border=_mix(ui, pal.card, 0.75))
    if battery:
        kw.update(ok=battery, level_full=battery, level_low=battery)
    if not kw:
        return theme
    return dataclasses.replace(theme, pal=dataclasses.replace(pal, **kw))
