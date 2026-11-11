# -*- coding: utf-8 -*-
"""抓窗口背后的桌面像素：Canvas 没有逐像素 alpha，用「快照 + 实色混合」伪装透明。

抓不到（远程桌面 / HDR 叠加层 / 权限）一律返回 None，调用方退回纯色，不能崩。

窗口背景现在按帧同步（见 panel._backdrop_sync），抓取必须便宜：
这里用「按尺寸复用的离屏位图」省掉每帧建 DC / 建位图 / 申请缓冲
（760×560 约 11ms，其中 BitBlt 本身占 8ms，是本机硬下限）。
"""
from __future__ import annotations

import ctypes

from ..win32 import BITMAPINFO, BITMAPINFOHEADER, gdi32, user32
from . import customize

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0

# (w, h) -> (screen_dc, memdc, hbitmap, bmi, buffer)
_SCRATCH: dict = {}


def _scratch(w: int, h: int):
    """按尺寸复用的离屏位图；只保留当前尺寸，尺寸变了就把旧的销毁。"""
    key = (w, h)
    hit = _SCRATCH.get(key)
    if hit is not None:
        return hit
    for old in list(_SCRATCH.values()):
        try:
            gdi32.SelectObject(old[1], old[0])
            gdi32.DeleteObject(old[2])
            gdi32.DeleteDC(old[1])
            user32.ReleaseDC(None, old[0])
        except Exception:
            pass
    _SCRATCH.clear()
    hdc = user32.GetDC(None)
    memdc = gdi32.CreateCompatibleDC(hdc)
    hbm = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(memdc, hbm)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h        # 负数 = 自上而下
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    rec = (hdc, memdc, hbm, bmi, buf)
    _SCRATCH[key] = rec
    return rec


def grab(x: int, y: int, w: int, h: int):
    """屏幕 (x, y, w, h) 的像素，返回 PIL RGB；失败 None。"""
    Image = getattr(customize, "Image", None)
    if Image is None or w <= 0 or h <= 0:
        return None
    try:
        hdc, memdc, _hbm, bmi, buf = _scratch(int(w), int(h))
        if not gdi32.BitBlt(memdc, 0, 0, int(w), int(h), hdc,
                            int(x), int(y), SRCCOPY):
            return None
        if not gdi32.GetDIBits(memdc, _hbm, 0, int(h), buf,
                               ctypes.byref(bmi), DIB_RGB_COLORS):
            return None
        im = Image.frombuffer("RGB", (int(w), int(h)), buf, "raw", "BGRX", 0, 1)
        return im.copy()               # frombuffer 不共享内存，但 Buffer 会被复用，必须复制
    except Exception:
        return None


def changed(a, b, tol: int = 8, mean_tol: float = 1.2) -> bool:
    """两张缩略图是否明显不同（任一通道峰值 > tol 或平均差 > mean_tol）。

    用 ImageChops 在 C 层算差，40×30 一次约 0.1ms；任一张为空按「变了」处理，
    逼调用方走一次真实抓取。
    """
    if a is None or b is None or a.size != b.size:
        return True
    try:
        from PIL import ImageChops, ImageStat
        diff = ImageChops.difference(a, b)
        peak = max(hi for _lo, hi in diff.getextrema())
        mean = max(ImageStat.Stat(diff).mean)
        return peak > tol or mean > mean_tol
    except Exception:
        try:
            for pa, pb in zip(a.getdata(), b.getdata()):
                if max(abs(pa[i] - pb[i]) for i in range(3)) > tol:
                    return True
            return False
        except Exception:
            return True


def stable(a, b, tol: int = 12) -> bool:
    """两帧桌面快照是否稳定（抽样 25 点，逐通道差都 <= tol）。

    刚隐藏窗口时 DWM 可能还没重绘完，前后两帧差异很大；只有稳定帧才拿去
    和软件背景混合，否则会把旧帧 / 白底混进来把背景冲淡。
    （逐帧同步走抓屏排除路径，用不到这条；它是「隐藏窗口抓取」的兜底。）
    """
    if a is None or b is None or a.size != b.size:
        return False
    w, h = a.size
    try:
        for fy in (0.1, 0.3, 0.5, 0.7, 0.9):
            for fx in (0.1, 0.3, 0.5, 0.7, 0.9):
                p = (min(w - 1, int(w * fx)), min(h - 1, int(h * fy)))
                pa, pb = a.getpixel(p)[:3], b.getpixel(p)[:3]
                if max(abs(pa[i] - pb[i]) for i in range(3)) > tol:
                    return False
        return True
    except Exception:
        return False


def usable(img) -> bool:
    """纯黑/纯灰说明没抓到（远程桌面、HDR 叠加层），别拿它当背景。"""
    if img is None:
        return False
    try:
        return any(lo != hi for lo, hi in img.getextrema())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 帧可信度（Wallpaper Engine 等 GPU 动态壁纸的频闪修复）
#
# GDI BitBlt 读显卡合成的动态壁纸时，偶尔会拿到尚未合成好的整片黑 / 整片白
# （或半帧撕裂）。这类帧通常还带一点纹理，usable() 的「纯色」判据拦不住，
# 于是被逐帧同步直接混进 L1/L2 —— 表现就是「时不时闪黑、闪白」。
# 动态壁纸每帧都在变，changed() 恒为 True，同步链会把每一帧都推上屏；
# 静态壁纸 / 普通窗口则退让到 200ms 几乎不重绘，所以只有动态壁纸会暴露。
#
# 阈值刻意宽松（用户选择「宁可偶尔还闪一下，也别让背景停」）：
# 只拦「几乎整屏突然变纯黑/纯白」这种极端坏帧，正常壁纸的明暗变化、
# 场景转场、闪光特效一律放行。判不准也一律放行。
# ---------------------------------------------------------------------------
_PROBE = (64, 48)
_BLACK = 16
_WHITE = 239
_MAX_EXTREME = 0.90       # 整帧 90% 以上近黑或近白 → 坏帧
_MAX_RATIO_JUMP = 0.70    # 相对上一帧，极端像素占比突然涨 70% → 坏帧
_MAX_LUM_JUMP = 150.0     # 整帧平均亮度突变 150（0–255）→ 坏帧


def _extremes(img):
    """(近黑占比, 近白占比, 平均亮度)。64×48 灰度直方图，约 0.2ms。"""
    hist = img.resize(_PROBE).convert("L").histogram()
    n = float(sum(hist) or 1)
    return (sum(hist[:_BLACK]) / n,
            sum(hist[_WHITE + 1:]) / n,
            sum(i * c for i, c in enumerate(hist)) / n)


def extremes(img):
    """公开的帧统计入口：panel 每帧只算一次新画面，结果同时给 sane 用。

    算法与 _extremes 完全相同，缩小画面的方式一个字都没改 —— 所以判断
    结果和阈值依旧，只是不再把上一帧的统计重复算一遍。
    """
    return _extremes(img)


def sane(frame, prev, *, frame_stats=None, prev_stats=None) -> bool:
    """这一帧像不像「抓坏了的画面」。只给 panel._backdrop_sync 使用。

    prev 为 None（第一帧 / 刚解冻）时无条件采用。任何异常都返回 True，
    宁可偶尔还闪一下，也不让背景停住不跟桌面变。

    frame_stats / prev_stats 是 extremes() 算好的三元组。两项都不传时
    行为与旧版完全一致；都传时只做比较、不再重复算上一帧的直方图
    （实测每帧省约 2.75ms）。数值来源不变，判断结果也不变。
    """
    if frame is None:
        return False
    try:
        dark, light, lum = (frame_stats if frame_stats is not None
                            else _extremes(frame))
        if dark > _MAX_EXTREME or light > _MAX_EXTREME:
            return False
        if prev is None:
            return True
        p_dark, p_light, p_lum = (prev_stats if prev_stats is not None
                                  else _extremes(prev))
        if dark - p_dark > _MAX_RATIO_JUMP or light - p_light > _MAX_RATIO_JUMP:
            return False
        if abs(lum - p_lum) > _MAX_LUM_JUMP:
            return False
        return True
    except Exception:
        return True
