# -*- coding: utf-8 -*-
"""外观自定义：图片导入/合成缓存、颜色混合、色相条。

原则：
- Pillow 是可选依赖，没装时这里所有函数返回 None，界面给提示，其它功能照常。
- 合成结果按 (文件, 尺寸, 透明度, 底色) 缓存，同一帧不会重复算。
- PhotoImage 由调用方（Panel）持有，避免被 GC 回收变白。
"""
from __future__ import annotations

import colorsys
import os
from typing import Optional

from .. import config

try:                                     # 可选依赖
    from PIL import Image, ImageChops, ImageDraw, ImageTk
    PIL_OK = True
except Exception:                        # noqa: BLE001
    Image = ImageChops = ImageDraw = ImageTk = None
    PIL_OK = False

# 解码上限：一张 100MPix 的 RGB 图解码后就是 300MB。Pillow 默认的
# MAX_IMAGE_PIXELS=89M 只报警告（超过 2 倍才抛错），所以在 open 之后、
# load() 之前自己按尺寸先挡一次，别等内存已经吃满才发现图太大。
MAX_DECODE_PIXELS = 40_000_000           # 约 55.5MPix ≈ 160MB RGB
if PIL_OK:
    try:
        Image.MAX_IMAGE_PIXELS = MAX_DECODE_PIXELS
    except Exception:
        pass

_pil_cache: dict = {}
_hsv_cache: dict = {}
_mask_cache: dict = {}
_last_error = ""


def reload() -> bool:
    """装完 Pillow 之后重新挂上（PIL_OK 是 import 时定死的，必须重探一次）。

    返回 True = 现在可用。调用方在 deps 安装成功回调里必须调它，否则界面
    仍然认为「没装 Pillow」，用户会觉得装了没用。
    """
    global Image, ImageChops, ImageDraw, ImageTk, PIL_OK
    try:
        from PIL import Image as _Image
        from PIL import ImageChops as _ImageChops
        from PIL import ImageDraw as _ImageDraw
        from PIL import ImageTk as _ImageTk
    except Exception:                        # noqa: BLE001
        return False
    Image, ImageChops, ImageDraw, ImageTk = (_Image, _ImageChops, _ImageDraw,
                                             _ImageTk)
    PIL_OK = True
    _pil_cache.clear()
    _hsv_cache.clear()
    _mask_cache.clear()
    return True


def last_error() -> str:
    """最近一次 import_image 失败的具体原因（成功时为空串）。"""
    return _last_error


def set_last_error(msg: str) -> None:
    """给别的模块（裁剪窗 / 打开图片）共用同一个错误出口。"""
    global _last_error
    _last_error = msg or ""


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------
def available() -> bool:
    return PIL_OK


def install_hint() -> str:
    return ("背景图 / 透明度需要 Pillow（Python 图像库）。\n"
            "程序可以自动装到用户目录，不需要管理员权限，也不需要手动敲 pip。")


def _round_mask(w: int, h: int, radius: int, invert: bool = False):
    """圆角蒙版缓存：卡片逐帧重算时省掉一次 Draw + 一次整块位图分配。

    invert=True 时返回「四角」蒙版，供整窗背景把圆角外让给桌面画面。
    """
    key = (int(w), int(h), int(radius), bool(invert))
    hit = _mask_cache.get(key)
    if hit is not None:
        return hit
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1],
                                           radius=radius, fill=255)
    if invert:
        mask = mask.point(lambda p: 255 - p)
    if len(_mask_cache) > 24:              # 尺寸变化很少，别无限涨
        _mask_cache.clear()
    _mask_cache[key] = mask
    return mask


def blend(color, base, alpha: float):
    """把 color 按 alpha 叠在 base 上，返回实色（Canvas 不支持透明度）。"""
    a = max(0.0, min(1.0, alpha))
    return tuple(int(round(color[i] * a + base[i] * (1 - a))) for i in range(3))


def ensure_media() -> str:
    d = config.media_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def import_image(src: str, filename: str) -> Optional[str]:
    """把用户选的图统一转成 PNG 存进 media 目录，配置里只记文件名。

    失败时把**具体原因**记进 `last_error()`，别让调用方只能说「打不开」。
    写临时文件 + os.replace：覆盖同名文件时不会留下半截 PNG，也不会
    因为目标文件被占而静默失败（Windows 上很常见）。
    """
    global _last_error
    _last_error = ""
    if not PIL_OK:
        _last_error = "未安装 Pillow"
        return None
    if not src or not os.path.isfile(src):
        _last_error = f"文件不存在：{src}"
        return None
    d = ensure_media()
    dst = os.path.join(d, filename)
    tmp = dst + ".tmp"
    try:
        with Image.open(src) as im:
            # 先读文件头拿尺寸（open 不解码像素），超限直接拒绝，别先 load() 吃满内存
            if im.size[0] * im.size[1] > MAX_DECODE_PIXELS:
                _last_error = (f"图片太大（{im.size[0]}×{im.size[1]}"
                               f" = {im.size[0]*im.size[1]/1e6:.0f} 百万像素），"
                               f"请换一张小一些的图")
                return None
            im.load()
            out = im.convert("RGB")
            if max(out.size) > 1600:          # 控制内存，别把 4K 图塞进卡片
                out.thumbnail((1600, 1600), Image.LANCZOS)
            out.save(tmp, "PNG")
        if not os.path.isfile(tmp) or os.path.getsize(tmp) < 64:
            raise RuntimeError("写入文件失败（结果为空）")
        os.replace(tmp, dst)                  # 原子替换，覆盖同名文件
        if not os.path.isfile(dst) or os.path.getsize(dst) < 64:
            raise RuntimeError("替换后文件不可用")
        return filename
    except Exception as e:                    # noqa: BLE001
        _last_error = f"{type(e).__name__}: {e}"
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return None


def file_stamp(filename: str) -> str:
    """图片内容标识：mtime_ns + 大小。PhotoImage 缓存键必须带上它，
    否则换图后文件名不变 → 永远命中第一张。"""
    p = media_path(filename)
    try:
        st = os.stat(p)
        return f"{st.st_mtime_ns}:{st.st_size}"
    except Exception:
        return "0:0"


def media_path(filename: str) -> str:
    return os.path.join(config.media_dir(), filename) if filename else ""


def remove_media(filename: str) -> None:
    p = media_path(filename)
    if p and os.path.isfile(p):
        try:
            os.remove(p)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 图片合成
# ---------------------------------------------------------------------------
# 合成缓存的预算：以前这个 dict 只进不出，8 向缩放走一遍（每档尺寸一个键）
# 实测能堆到 280 条 / 226MB 位图且永不释放。淘汰只会让下一次重算，
# 不影响画面（PhotoImage 由 Panel 另有一层缓存，并保护画布上在用的图）。
_CACHE_MAX_ITEMS = 24
_CACHE_MAX_PIXELS = 40_000_000        # 约 120MB（RGB 3 字节/像素）


def _cache_pixels() -> int:
    total = 0
    for v in _pil_cache.values():
        try:
            w, h = v.size
            total += w * h * len(v.getbands())
        except Exception:
            continue
    return total


def _cache_put(key, im) -> None:
    """写合成缓存；超预算时按插入顺序淘汰最旧的（跳过刚写入的这条）。"""
    try:
        _pil_cache[key] = im
        if (len(_pil_cache) <= _CACHE_MAX_ITEMS
                and _cache_pixels() <= _CACHE_MAX_PIXELS):
            return
        for k in list(_pil_cache):
            if k == key:
                continue
            _pil_cache.pop(k, None)
            if (len(_pil_cache) <= _CACHE_MAX_ITEMS
                    and _cache_pixels() <= _CACHE_MAX_PIXELS):
                break
    except Exception:
        pass


def cover(filename: str, w: int, h: int):
    """按 cover 缩放并居中裁到 w×h，返回 PIL RGB 或 None。"""
    if not PIL_OK or not filename:
        return None
    path = media_path(filename)
    if not os.path.isfile(path):
        return None
    key = ("cover", path, file_stamp(filename), w, h)
    hit = _pil_cache.get(key)
    if hit is not None:
        return hit
    try:
        im = Image.open(path).convert("RGB")
        sw, sh = im.size
        s = max(w / sw, h / sh)
        im = im.resize((max(w, int(sw * s + 0.5)),
                        max(h, int(sh * s + 0.5))), Image.LANCZOS)
        left, top = (im.size[0] - w) // 2, (im.size[1] - h) // 2
        im = im.crop((left, top, left + w, top + h))
        _cache_put(key, im)
        return im
    except Exception:
        return None


# 背景图与容器形状不一致时怎么铺满。三份交付单元各自取一个值，互不干扰：
#   cover   = 等比放大铺满、多出的裁掉（不变形、无边）
#   stretch = 直接拉到容器大小（不裁、无边，可能变形）
#   edge    = 等比缩到能装下、剩余用图片自身边缘像素延展（不裁、不变形、无边）
# 旧做法是「等比缩到能装下 + 多出的地方填纯色」，那正是窗口拉伸后
# 两端黑边/白边的来源：留白色用的是窗口/卡片底色。
FIT_MODE = "stretch"


def fit_image(im, w: int, h: int, mode: str = ""):
    """把已打开的图按 mode（缺省取 FIT_MODE）铺满 w×h，绝不产生纯色边。"""
    if im is None or w <= 0 or h <= 0:
        return None
    mode = mode or FIT_MODE
    sw, sh = im.size
    if (sw, sh) == (w, h):
        return im
    if mode == "stretch":
        return im.resize((w, h), Image.LANCZOS)
    if mode == "cover":
        return cover_image(im, w, h)
    # edge：先等比 contain，再用自身边缘像素把四周补满（不是纯色）
    s = min(w / max(1, sw), h / max(1, sh))
    nw = max(1, min(w, int(round(sw * s))))
    nh = max(1, min(h, int(round(sh * s))))
    scaled = im if (nw, nh) == (sw, sh) else im.resize((nw, nh), Image.LANCZOS)
    out = Image.new("RGB", (w, h))
    px, py = (w - nw) // 2, (h - nh) // 2
    out.paste(scaled, (px, py))
    if px > 0:
        out.paste(scaled.crop((0, 0, 1, nh)).resize((px, nh)), (0, py))
    right = w - px - nw
    if right > 0:
        out.paste(scaled.crop((nw - 1, 0, nw, nh)).resize((right, nh)),
                  (px + nw, py))
    if py > 0:
        out.paste(out.crop((0, py, w, py + 1)).resize((w, py)), (0, 0))
    bottom = h - py - nh
    if bottom > 0:
        out.paste(out.crop((0, py + nh - 1, w, py + nh)).resize((w, bottom)),
                  (0, py + nh))
    return out


def cover_image(im, w: int, h: int):
    """内存图按 cover 等比缩放（不拉伸、不留白），用于尺寸不一致时的兜底。"""
    if not PIL_OK or im is None:
        return None
    sw, sh = im.size
    if (sw, sh) == (w, h):
        return im
    s = max(w / max(1, sw), h / max(1, sh))
    im2 = im.resize((max(w, int(sw * s + 0.5)),
                     max(h, int(sh * s + 0.5))), Image.LANCZOS)
    left, top = (im2.size[0] - w) // 2, (im2.size[1] - h) // 2
    return im2.crop((left, top, left + w, top + h))


def card_bg(filename: str, w: int, h: int, fill=None, mode: str = "contain"):
    """取背景图。mode="contain" 是旧的等比完整居中 + 纯色留白（裁剪预览 /
    自检仍用它，保证既有结论不变）；渲染路径传 mode=FIT_MODE，改用无留白
    铺法，窗口放大缩小都跟着变，两端不会再露出窗口底色的黑边/白边。
    """
    if not PIL_OK or not filename:
        return None
    path = media_path(filename)
    if not os.path.isfile(path):
        return None
    w, h = max(1, int(w)), max(1, int(h))
    fill = tuple(fill) if fill is not None else (245, 246, 248)
    key = ("cardbg", path, file_stamp(filename), w, h, fill, mode)
    hit = _pil_cache.get(key)
    if hit is not None:
        return hit
    try:
        im = Image.open(path).convert("RGB")
        if mode != "contain":
            out = fit_image(im, w, h, mode)
        else:
            sw, sh = im.size
            s = min(w / max(1, sw), h / max(1, sh))
            nw = max(1, min(w, int(round(sw * s))))
            nh = max(1, min(h, int(round(sh * s))))
            scaled = im if (nw, nh) == (sw, sh) else im.resize((nw, nh), Image.LANCZOS)
            if (nw, nh) == (w, h):
                out = scaled
            else:
                out = Image.new("RGB", (w, h), fill)
                out.paste(scaled, ((w - nw) // 2, (h - nh) // 2))
        if out is None:
            return None
        _cache_put(key, out)
        return out
    except Exception:
        return None

def solid(w: int, h: int, color):
    return Image.new("RGB", (w, h), tuple(color)) if PIL_OK else None


def aligned_crop(src, x0: int, y0: int, w: int, h: int):
    """按**真实坐标**从 src 取 w×h 裁片；越界部分用 src 边缘像素延展填满。

    滚动时卡片顶部 y0 可能为负、底部 y0+h 可能超过 src 高度。把整块裁片
    钳制回合法范围会让卡片显示软件背景另一处的图案（视觉割裂）；直接
    crop 越界则会出现黑边 / 空白。这里把「卡片矩形 ∩ src」的交集按真实
    偏移贴到正确行列，交集外逐行 / 逐列复制交集的边缘像素 —— 整张裁片
    都有内容，且与交界处连续，不会产生未渲染区域。
    """
    if not PIL_OK or src is None or w <= 0 or h <= 0:
        return None
    sw, sh = src.size
    x1, y1 = x0 + w, y0 + h
    ix0, iy0 = max(0, x0), max(0, y0)
    ix1, iy1 = min(sw, x1), min(sh, y1)
    if ix1 <= ix0 or iy1 <= iy0:
        # 完全在窗口外（正常不会被绘制）：仍用源图角点色填满，不留空
        return Image.new("RGB", (w, h),
                         src.getpixel((min(sw - 1, max(0, x0)),
                                       min(sh - 1, max(0, y0)))))
    patch_im = src.crop((ix0, iy0, ix1, iy1))
    pw, ph = patch_im.size
    out = Image.new("RGB", (w, h))
    px, py = ix0 - x0, iy0 - y0
    out.paste(patch_im, (px, py))
    # 左右越界：复制交集首列 / 末列
    if px > 0:
        col = patch_im.crop((0, 0, 1, ph)).resize((px, ph))
        out.paste(col, (0, py))
    if x1 - ix1 > 0:
        cw = x1 - ix1
        col = patch_im.crop((pw - 1, 0, pw, ph)).resize((cw, ph))
        out.paste(col, (px + pw, py))
    # 上下越界：复制已填好区域（含左右延展）的顶行 / 底行
    if py > 0:
        row = out.crop((0, py, w, py + 1)).resize((w, py))
        out.paste(row, (0, 0))
    if y1 - iy1 > 0:
        bh = y1 - iy1
        sy = py + ph - 1
        row = out.crop((0, sy, w, sy + 1)).resize((w, bh))
        out.paste(row, (0, h - bh))
    return out


def blend_layer(base, content, alpha: float):
    """alpha=1 取 content，0 取 base，中间线性；两边必须同尺寸。"""
    if base is None or content is None:
        return content if content is not None else base
    if alpha >= 0.999:
        return content
    if alpha <= 0.001:
        return base
    if base.size != content.size:
        # 尺寸不一致按 cover 等比缩放并居中裁切，不能非等比拉伸
        content = cover_image(content, base.size[0], base.size[1]) or content
    return Image.blend(base, content, alpha)


def round_window(im, base, radius: int, fill=None):
    """整窗背景自带圆角：四角让出 base（桌面快照）或 fill 底色。

    窗口圆角原本只由 Win32 区域裁剪负责；拖动 / 缩放那一瞬间区域可能
    还按旧尺寸，方形的背景图就会从圆角外露出。这里让图片四角先变成
    桌面画面，两道保险叠加后任意窗口尺寸下都不会溢出，也看不出接缝。
    """
    if im is None or not PIL_OK:
        return im
    w, h = im.size
    radius = max(1, int(radius))
    if base is not None and base.size == (w, h):
        corner = base.convert("RGB")
    else:
        corner = solid(w, h, fill or (255, 255, 255))
    if corner is None:
        return im
    try:
        out = im.convert("RGB").copy()
        out.paste(corner, (0, 0), _round_mask(w, h, radius, invert=True))
        return out
    except Exception:
        return im


def _bottom_gradient(im):
    """底部 40% 压暗，保证状态行/电量字可读（原 card_image 里的那段搬过来）。"""
    w, h = im.size
    grad = Image.new("L", (1, h), 0)
    start = int(h * 0.6)
    for y in range(start, h):
        grad.putpixel((0, y), int(90 * (y - start) / max(1, h - start)))
    return Image.composite(Image.new("RGB", (w, h), (0, 0, 0)),
                           im, grad.resize((w, h)))


def card_layer(filename, w, h, radius, alpha, base, card_color):
    """卡片背景层：底色/卡图按 alpha 叠在 base（软件背景层裁片）上，圆角裁切。

    base 由调用方先裁好，尺寸必须与 w×h 一致 —— 这里不再碰「窗口底色」，
    否则中间透明度会重新出现白底。
    """
    if not PIL_OK or base is None:
        return None
    content = (card_bg(filename, w, h, card_color, mode=FIT_MODE) if filename
               else solid(w, h, card_color))
    if content is None:
        return None
    if content.size != (w, h):
        content = content.resize((w, h), Image.LANCZOS)
    if filename:                     # 只有真图片才加底部渐变，纯色底不要
        content = _bottom_gradient(content)
    im = blend_layer(base, content, alpha)
    mask = _round_mask(w, h, radius)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(im.convert("RGBA"), (0, 0), mask)
    return out


def import_image_pil(img, filename: str):
    """已经是 PIL Image 时直接用，省一次落盘再读（裁剪窗用）。"""
    global _last_error
    if not PIL_OK or img is None:
        _last_error = "未安装 Pillow 或图像为空"
        return None
    dst = os.path.join(ensure_media(), filename)
    tmp = dst + ".tmp"
    try:
        img.convert("RGB").save(tmp, "PNG")
        os.replace(tmp, dst)
        return filename
    except Exception as e:
        _last_error = f"{type(e).__name__}: {e}"
        return None


def card_image(filename: str, w: int, h: int, radius: int,
               opacity: float, base) -> Optional[object]:
    """卡片背景：cover 缩放 + 圆角 mask + 底部渐变 + 与底色按 alpha 混合。

    自检用的旧签名，内部走 card_layer。注意 base 是「调用方给的底色」，
    面板已经不再传窗口底色了（见 panel._card_layer）。
    """
    if not PIL_OK or not filename:
        return None
    path = media_path(filename)
    if not os.path.isfile(path):
        return None
    base_img = solid(w, h, base)
    return card_layer(filename, w, h, radius, opacity, base_img, base)


def app_image(filename: str, w: int, h: int, opacity: float,
              base) -> Optional[object]:
    """整窗背景：cover 缩放后与窗口底色按透明度混合。"""
    if not PIL_OK or not filename:
        return None
    path = media_path(filename)
    if not os.path.isfile(path):
        return None
    key = ("app", path, file_stamp(filename), w, h,
           round(opacity, 2), tuple(base))
    hit = _pil_cache.get(key)
    if hit is not None:
        return hit
    try:
        im = Image.open(path).convert("RGB")
        sw, sh = im.size
        scale = max(w / sw, h / sh)
        im = im.resize((max(w, int(sw * scale + 0.5)),
                        max(h, int(sh * scale + 0.5))), Image.LANCZOS)
        left = (im.size[0] - w) // 2
        top = (im.size[1] - h) // 2
        im = im.crop((left, top, left + w, top + h))
        if opacity < 1.0:
            im = Image.blend(Image.new("RGB", (w, h), tuple(base)), im,
                             opacity)
        _cache_put(key, im)
        return im
    except Exception:
        return None


def crop(image, box):
    if not PIL_OK or image is None:
        return None
    try:
        return image.crop(box)
    except Exception:
        return None


def to_photo(image):
    """PIL → PhotoImage；调用方必须自己持有返回值。"""
    if not PIL_OK or image is None:
        return None
    try:
        return ImageTk.PhotoImage(image)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 色轮：色相条 + 饱和度/明度方块
# ---------------------------------------------------------------------------
def hue_bar(w: int = 22, h: int = 160):
    """竖色相条。只算一列再横向拉伸，避免 w×h 次循环。"""
    if not PIL_OK:
        return None
    key = ("hue", w, h)
    hit = _hsv_cache.get(key)
    if hit is not None:
        return hit
    strip = Image.new("RGB", (1, max(2, h)))
    px = strip.load()
    for y in range(h):
        r, g, b = colorsys.hsv_to_rgb(y / max(1, h - 1), 1, 1)
        px[0, y] = (int(r * 255), int(g * 255), int(b * 255))
    im = strip.resize((max(1, w), h), Image.BILINEAR)
    _hsv_cache[key] = im
    return im


def sv_square(hue: float, size: int = 160):
    """给定色相的饱和度/明度方块（左上=白，右上=纯色，底部=黑）。

    性能：只算一行水平渐变再纵向拉伸，最后用亮度渐变做正片叠底，
    全程走 C 层；色相量化到 1/36，拖色相条时不会每帧重算 22500 像素。
    """
    if not PIL_OK:
        return None
    key = ("sv", round(hue * 36) / 36, size)
    hit = _hsv_cache.get(key)
    if hit is not None:
        return hit
    size = max(2, size)
    row = Image.new("RGB", (size, 1))
    px = row.load()
    for x in range(size):
        s = x / max(1, size - 1)
        r, g, b = colorsys.hsv_to_rgb(hue, s, 1)
        px[x, 0] = (int(r * 255), int(g * 255), int(b * 255))
    im = row.resize((size, size), Image.BILINEAR)
    strip = Image.new("L", (1, size))
    sp = strip.load()
    for y in range(size):
        sp[0, y] = int(round(255 * (1 - y / max(1, size - 1))))
    grad = strip.resize((size, size)).convert("RGB")
    im = ImageChops.multiply(im, grad)
    _hsv_cache[key] = im
    return im


def hue_of(rgb) -> float:
    r, g, b = [c / 255.0 for c in rgb]
    return colorsys.rgb_to_hsv(r, g, b)[0]


def sv_of(rgb):
    r, g, b = [c / 255.0 for c in rgb]
    _h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return s, v


def hsv_to_rgb(hue: float, s: float, v: float):
    r, g, b = colorsys.hsv_to_rgb(hue, s, v)
    return [int(r * 255), int(g * 255), int(b * 255)]


def clear_cache() -> None:
    _pil_cache.clear()
