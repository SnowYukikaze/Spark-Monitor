# -*- coding: utf-8 -*-
"""交互自检：改名、右键菜单、抓包向导、切换刷新间隔。

会真的创建 Tk 窗口和 Entry，但不常驻；跑完自动关闭。
用法：<带 tkinter 的 python> tools/smoke_interact.py
"""
import os
import time
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

tmp = tempfile.mkdtemp(prefix="ez24g_ui_")
os.environ["APPDATA"] = tmp          # 别动用户真实配置

fails = []


def check(ok, msg):
    print(("  OK   " if ok else "  FAIL ") + msg)
    if not ok:
        fails.append(msg)


import ez24g.app as A
from ez24g import config, devices
from ez24g.model import Reading

A.UI_SCALE = 1.0
app = A.TrayApp(panel_only=True)

# 先存一台设备，改完名要能落到配置里
devices.add_device(0x373B, 0x1278, "ATK")
app.devices = config.load_saved_devices()

from ez24g.ui.panel import Panel, LAYOUT

p = Panel(app)
key = "x373B1278"
app._readings = {key: Reading(ok=True, level=66, name="ATK", vid=0x373B,
                              pid=0x1278, usage_page=0xFF05, hid_known=True)}
p.show()
p.root.update()

# -- 单击昵称 → 弹窗改名（§3.10）---------------------------------------
from ez24g.ui import dialogs as _dlg10

p._open_rename(key)
p.root.update()
check(isinstance(p._dlg, _dlg10.RenameDialog), "单击昵称弹出改名窗")
check(bool(p._dlg.cv.find_withtag("dlg_close")), "改名窗也有右上角 X")
check(p._dlg.entry.get() == "ATK", f"预填原名：{p._dlg.entry.get()}")
p._dlg.entry.delete(0, "end")
p._dlg.entry.insert(0, "我的鼠标")
p._dlg._ok()
p.root.update()
check(p._dlg is None, "确定后弹窗关闭")
check(config.custom_names().get(key) == "我的鼠标",
      f"名字写进配置：{config.custom_names()}")
check(app.snapshot()[key].name == "我的鼠标", "改名后内存快照立即更新")

# 空名字不应覆盖
p._open_rename(key)
p.root.update()
p._dlg.entry.delete(0, "end")
p._dlg._ok()
p.root.update()
check(config.custom_names().get(key) == "我的鼠标", "空名字被忽略")

# 超长名字截到 24 字
p._open_rename(key)
p.root.update()
p._dlg.entry.delete(0, "end")
p._dlg.entry.insert(0, "长" * 30)
p._dlg._ok()
p.root.update()
check(app.snapshot()[key].name == "长" * 24,
      f"超长昵称截到 24 字（{len(app.snapshot()[key].name)}）")
p._open_rename(key)
p.root.update()
p._dlg.entry.delete(0, "end")
p._dlg.entry.insert(0, "我的鼠标")
p._dlg._ok()
p.root.update()

# -- 右键菜单 ----------------------------------------------------------
class _E:
    x = 60
    y = 120
    x_root = 300
    y_root = 400


# tk_popup 会进入菜单的模态循环，测试里替换成空实现
import tkinter as tk

tk.Menu.tk_popup = lambda self, x, y, entry="": None
p._on_right_click(_E())
check(p._menu is not None, "右键卡片弹出菜单")
labels = []
if p._menu is not None:
    last = p._menu.index("end")
    for i in range(last + 1):
        try:
            labels.append(p._menu.entrycget(i, "label"))
        except Exception:
            labels.append("---")          # 分隔线没有 label
check(labels == ["重命名", "与另一台设备合并…", "自定义背景…", "---", "删除设备"],
      f"卡片菜单项 {labels}")
p._clear_menu()

# -- 抓包向导 ----------------------------------------------------------
p.open_help(key)
p.root.update()
hw = p._help_win
check(hw is not None, "打开抓包向导")
check(hw.target_vid == 0x373B, f"向导带上设备 VID {hw.target_vid:04X}")
check(len(hw.cv.find_all()) > 10, f"向导画布元素 {len(hw.cv.find_all())} 个")

# 提权链加固：命令行里的可执行文件必须是 USBPcapCMD.exe 本体
_exe_dir = tempfile.mkdtemp(prefix="ez24g_usbpcap_")
_good_exe = os.path.join(_exe_dir, "USBPcapCMD.exe")
with open(_good_exe, "wb") as _f:
    _f.write(b"MZ")
_bad_exe = os.path.join(_exe_dir, "calc.exe")
with open(_bad_exe, "wb") as _f:
    _f.write(b"MZ")
check(hw._is_usbpcap_cmd(_good_exe), "提权前校验：USBPcapCMD.exe 被接受")
check(not hw._is_usbpcap_cmd(_bad_exe),
      "提权前校验：其它 exe 被拒绝（配置文件不能变成提权执行器）")
check(not hw._is_usbpcap_cmd(
        os.path.join(tempfile.mkdtemp(prefix="ez24g_none_"), "USBPcapCMD.exe")),
      "提权前校验：不存在的路径被拒绝")
check(not hw._is_usbpcap_cmd(""), "提权前校验：空路径被拒绝")

# 抓包结果路径必须落在本次随机工作目录内（防伪造 status.json 采信任意路径）
_work = tempfile.mkdtemp(prefix="BatteryTrayCapture-")
_inside = os.path.join(_work, "capture.pcap")
with open(_inside, "wb") as _f:
    _f.write(b"x" * 3)
_outside = os.path.join(tempfile.mkdtemp(prefix="ez24g_other_"), "capture.pcap")
with open(_outside, "wb") as _f:
    _f.write(b"x" * 3)
_old_dir = hw._cap_dir
hw._cap_dir = _work
check(hw._accept_result_path(_inside), "工作目录内的抓包文件被接受")
check(not hw._accept_result_path(_outside),
      "工作目录外的文件被拒绝（status.json 的 path 不再被无条件采信）")
check(not hw._accept_result_path(""), "空路径被拒绝")
hw._cap_dir = _old_dir
shutil.rmtree(_work, ignore_errors=True)
shutil.rmtree(_exe_dir, ignore_errors=True)

hw.close()
p._help_win = None
p.root.update()

# -- 刷新间隔：弹窗手填秒数 ---------------------------------------------
from tkinter import simpledialog

before = app.interval
subtitle = "".join(p.cv.itemcget(i, "text") for i in p.cv.find_all()
                   if p.cv.type(i) == "text" and "台 ·" in p.cv.itemcget(i, "text"))
check(f"· {before}s 刷新 ·" in subtitle, f"副标题按秒显示（{subtitle}）")

_orig_ask = simpledialog.askinteger
try:
    simpledialog.askinteger = lambda *a, **k: 120
    p._ask_interval()
    p.root.update()
    texts = [p.cv.itemcget(i, "text") for i in p.cv.find_all()
             if p.cv.type(i) == "text"]
    check(any("· 120s 刷新 ·" in t for t in texts),
          f"填 120 后副标题立刻更新：{[t for t in texts if '台 ·' in t]}")
    check(config.get_interval() == 120, "间隔 120 写进配置")
    check(app.interval == 120, "内存里的间隔同步")

    # 取消 → 什么都不改
    simpledialog.askinteger = lambda *a, **k: None
    p._ask_interval()
    p.root.update()
    check(config.get_interval() == 120, "取消弹窗不改配置")

    # 越界交给 config.clamp_interval
    simpledialog.askinteger = lambda *a, **k: 5
    p._ask_interval()
    check(config.get_interval() == 30, f"填 5 → {config.get_interval()}")
    simpledialog.askinteger = lambda *a, **k: 99999
    p._ask_interval()
    check(config.get_interval() == 1800, f"填 99999 → {config.get_interval()}")

    # 头部按钮右对齐且不越界（替代原来的「设置」文字按钮）
    bb_ref = p.cv.bbox(p._btn_refresh.tag)
    bb_more = p.cv.bbox(p._btn_more.tag)
    check(bb_ref is not None and bb_more is not None and bb_ref[2] <= bb_more[0],
          f"头部按钮顺序正确：{bb_ref} ... {bb_more}")
finally:
    simpledialog.askinteger = _orig_ask
    app.set_interval(before)
    p.render()
    p.root.update()

# -- 滚动 --------------------------------------------------------------
app._readings = {f"k{i}": Reading(ok=True, level=i * 5, name=f"设备{i}",
                                 vid=0x1000 + i, pid=0x2000 + i,
                                 usage_page=0xFF00, hid_known=True)
                 for i in range(10)}

class _W:
    delta = -120

p.render()
p.root.update()
p._on_wheel(_W())
p.root.update()
check(p._scroll > 0, f"滚轮生效 scroll={p._scroll:.0f}")

# -- 标题栏按钮（关闭 / 最小化）----------------------------------------
app._readings = {key: Reading(ok=True, level=66, name="ATK", vid=0x373B,
                              pid=0x1278, usage_page=0xFF05, hid_known=True)}
p.render()
p.root.update()

check(getattr(p, "_btn_close", None) is not None, "存在关闭按钮")
check(getattr(p, "_btn_min", None) is not None, "存在最小化按钮")
if p._btn_close and p._btn_min:
    bb_close = p.cv.bbox(p._btn_close.tag)
    bb_min = p.cv.bbox(p._btn_min.tag)
    check(bb_close is not None and bb_min is not None, "两个按钮都画出来了")
    if bb_close and bb_min:
        check(bb_close[2] <= p.W - 10 * p.S + 1,
              f"× 在右上角（右端 {bb_close[2]}，窗口宽 {p.W}）")
        check(bb_close[0] > bb_min[0],
              f"× 在 − 右边（× {bb_close[0]} > − {bb_min[0]}）")
        check(bb_close[1] < 60 * p.S,
              f"两个按钮都在标题栏高度内（y={bb_close[1]}）")
    check(p._btn_close.command == app.quit, "× 绑定 app.quit")
    check(p._btn_min.command == p.hide, "− 绑定 panel.hide（行为不变）")

texts = [p.cv.itemcget(i, "text") for i in p.cv.find_all()
         if p.cv.type(i) == "text"]
check("最小化" not in texts and "开机自启" not in texts,
      "底栏已移除（设置项进「设置」菜单）")
check("✕" in texts and "−" in texts, f"按钮文字正确：{texts[-6:]}")

# -- 头部「…」菜单：设置 / 开机自启 / 改标题 / 托盘设置 ----------------
p._header_menu()
labels = []
if p._menu is not None:
    for i in range(p._menu.index("end") + 1):
        try:
            labels.append(p._menu.entrycget(i, "label"))
        except Exception:
            labels.append("---")
check("设置刷新间隔…" in labels and "开机自启" in labels
      and "更改标题…" in labels and "打开托盘设置" in labels,
      f"头部菜单项 {labels}")
p._clear_menu()

# -- 标题改名 ----------------------------------------------------------
from tkinter import simpledialog as _sd
_orig_askstr = _sd.askstring
_sd.askstring = lambda *a, **k: "我的外设"
p._rename_title()
p.root.update()
check(config.get_title() == "我的外设", f"标题写进配置：{config.get_title()}")
check("我的外设" in [p.cv.itemcget(i, "text") for i in p.cv.find_all()
                     if p.cv.type(i) == "text"], "标题立刻重绘")
p._reset_title()
p.root.update()
check(config.get_title() == "星火 Monitor", "恢复默认标题")
_sd.askstring = _orig_askstr

# -- 添加设备菜单（有可添加设备时才列出）--------------------------------
check(callable(p._add_device), "「+ 添加设备」入口存在")

# -- 窗口位置记忆 / 首次落右下角 ----------------------------------------
config.set_panel_pos(180, 140)
p._place()
p.root.update()
check((p.root.winfo_x(), p.root.winfo_y()) == (180, 140),
      f"有记录时回到上次位置：{p.root.winfo_x()},{p.root.winfo_y()}")
config.ui_set("pos", None)
p._place()
p.root.update()
sw = p.root.winfo_screenwidth()
check(p.root.winfo_x() == max(0, sw - p.W - 24),
      f"没有记录时落在屏幕右下角：x={p.root.winfo_x()} 期望 {sw - p.W - 24}")

# -- 浅色主题 ----------------------------------------------------------
app.theme_light = True
p.render()
p.root.update()
check(len(p.cv.find_all()) > 10, "浅色主题渲染正常")

# -- 关闭按钮真的会退出（放最后，会置 _stop）----------------------------
p._btn_close.command()
check(app._stop, "点击 × 后 app._stop = True")

# -- 按钮悬停（曾经字段名和 _hover() 撞名，鼠标一移上去就刷 TypeError）--
from ez24g.ui.widgets import Button

btn = Button(p.cv, p.theme, p.S, 40, 300, 64, 24, "测试").draw()
check(callable(btn._hover), "按钮的 _hover 仍是方法，没被同名字段覆盖")
try:
    btn._hover(True)
    btn._hover(False)
    check(True, "悬停进入/离开不抛异常")
except Exception as e:                       # noqa: BLE001
    check(False, f"悬停切换抛异常：{type(e).__name__}: {e}")

# -- 卡片拖动排序 ------------------------------------------------------
app._readings = {f"d{i}": Reading(ok=True, level=90 - i, name=f"设备{i}",
                                 vid=0x1200 + i, pid=0x3400 + i,
                                 usage_page=0xFF00, hid_known=True)
                 for i in range(4)}
config.set_card_order([])
p._scroll = 0.0
p.render()
p.root.update()


class _Ev:
    def __init__(self, x, y, xr=None, yr=None):
        self.x, self.y = x, y
        self.x_root = xr if xr is not None else x
        self.y_root = yr if yr is not None else y


k0, k1 = "d0", "d1"
r0, r1 = p._card_rects[k0], p._card_rects[k1]
p._on_press(_Ev(int((r0[0] + 10) * p.S), int((r0[1] + 10) * p.S)))
p.root.update()
mid_x = int(((r1[0] + r1[2]) / 2) * p.S)
mid_y = int(((r1[1] + r1[3]) / 2) * p.S)
p._on_drag(_Ev(mid_x, mid_y))
p._on_drag(_Ev(mid_x, mid_y))
p.root.update()
check(p._drag == k0, f"移动超过 6px 进入拖动（drag={p._drag}）")
for _ in range(12):
    p.root.update()
    time.sleep(0.02)
p._on_release(_Ev(mid_x, mid_y))
p.root.update()
check(p._anim_frames >= 8, f"让位动画跑了 {p._anim_frames} 帧（≥8）")
order = config.get_card_order()
check(order[:2] == [k1, k0], f"同一分区内顺序保存：{order}")

# 跨区拖动：下区卡片拖到上区应弹回
app._readings["d0"] = Reading(ok=False, present=False, status="unplugged",
                              name="设备0", vid=0x1200, pid=0x3400)
p.render()
p.root.update()
if "d0" in p._card_rects and "d1" in p._card_rects:
    rb = p._card_rects["d0"]
    rt = p._card_rects["d1"]
    p._on_press(_Ev(int((rb[0] + 10) * p.S), int((rb[1] + 10) * p.S)))
    p._on_drag(_Ev(int(((rt[0] + rt[2]) / 2) * p.S),
                   int(((rt[1] + rt[3]) / 2) * p.S)))
    p._on_release(_Ev(0, 0))
    p.root.update()
    check(config.get_card_order()[:2] == [k1, k0], "跨区拖动不改变顺序")


# =====================================================================
# Bug 1：拖动不再糊、不再回顶
# =====================================================================
from ez24g.ui import customize

app._readings = {f"d{i}": Reading(ok=True, level=90 - i, name=f"设备{i}",
                                 vid=0x1200 + i, pid=0x3400 + i,
                                 usage_page=0xFF00, hid_known=True)
                 for i in range(4)}
config.set_card_order([])
p._scroll = 40.0
p.render()
p.root.update()

_calls = {"n": 0}
_orig_render = p.render


def _counted_render():
    _calls["n"] += 1
    return _orig_render()


p.render = _counted_render
r0 = p._card_rects["d2"]          # 用第二排的卡：第一排滚上去会被头部遮罩盖住
r3 = p._card_rects["d3"]
check(r0[1] >= LAYOUT["header_h"], f"拖动起点在卡片区（y={r0[1]:.0f}）")
p._on_press(_Ev(int((r0[0] + 12) * p.S), int((r0[1] + 12) * p.S)))
# 斜着连续跨槽位：以前每跨一次就多挂一条 after 链，同一张卡被几条链一起 move
steps = 14
for i in range(1, steps + 1):
    fx = (r0[0] + 12) + (r3[2] - r0[0] - 24) * i / steps
    fy = (r0[1] + 12) + (r3[1] - r0[1] + 40) * i / steps
    p._on_drag(_Ev(int(fx * p.S), int(fy * p.S)))
p.root.update()
check(p._drag == "d2", f"斜拖保持拖动状态（drag={p._drag}）")
check(_calls["n"] == 0, f"拖动期间 render 调用 {_calls['n']} 次（要求 0）")

# 模拟 30 秒自动刷新在拖动中打进来：必须被挂起，不能重画画布
n_items = len(p.cv.find_all())
p.render()
check(p._render_pending, "拖动中外部 render 被挂起（_render_pending）")
check(len(p.cv.find_all()) == n_items, "挂起的 render 没有重画画布")
calls_at_release = _calls["n"]

bb = p.cv.bbox("card:d2")
cw = (r0[2] - r0[0]) * p.S
check(bb and abs((bb[2] - bb[0]) - cw) <= 4 * p.S,
      f"拖动中整块移动，bbox 宽 {(bb[2]-bb[0]) if bb else 0:.0f} ≈ 卡宽 {cw:.0f}")

scroll_before = p._scroll
p._on_release(_Ev(int((r3[2] - 20) * p.S), int((r3[1] + 40) * p.S)))
p.render = _orig_render
p.root.update()
check(abs(p._scroll - scroll_before) < 0.5,
      f"松手后视口不变：{scroll_before} → {p._scroll}")
check(_calls["n"] == calls_at_release + 1,
      f"松手只补 1 次 render（挂起 {calls_at_release} + 1 = {_calls['n']}）")
check(p._dragging is False, "_dragging 已复位")

# 让位动画同一张卡只有一条链在跑：代际号必须单调递增
p._drag_layout = dict(p._slots)
p._move_card_to("d3", 10, 10)
g1 = p._anim_gen["d3"]
p._move_card_to("d3", 20, 20)
g2 = p._anim_gen["d3"]
check(g2 == g1 + 1, f"同一张卡再开动画 → 代际号 {g1}→{g2}，旧链自动退出")
check(p._anim_gen["d3"] == g2, "代际号保存在 _anim_gen 上")

# =====================================================================
# Bug 2：背景入口合并成一个
# =====================================================================
p._header_menu()
p.root.update()
m = p._menu
hlabels = [m.entrycget(i, "label") for i in range(m.index("end") + 1)]
check(sum(1 for t in hlabels if "背景" in t) == 1,
      f"头部菜单里背景只有一行：{[t for t in hlabels if '背景' in t]}")
check("自定义背景…" in hlabels, "头部菜单有「自定义背景…」")
m.unpost()
p.root.update()

p._card_menu("d1", 300, 300)
p.root.update()
cm = p._menu
clabels = [cm.entrycget(i, "label") for i in range(cm.index("end") + 1)]
check(sum(1 for t in clabels if "背景" in t) == 1,
      f"卡片菜单里背景只有一行：{[t for t in clabels if '背景' in t]}")
# 点这一项 → 直接作用于这张卡（不用再选设备）
_i = clabels.index("自定义背景…")
cm.items[_i][1]()
p.root.update()
from ez24g.ui import dialogs as _dlgs

check(isinstance(p._dlg, _dlgs.BackgroundDialog), "「自定义背景…」打开的是背景弹窗")
check(p._dlg._target() == "d1", f"默认作用在这张卡：{p._dlg._target()}")
_dtexts = [p._dlg.cv.itemcget(i, "text") for i in p._dlg.cv.find_all()
           if p._dlg.cv.type(i) == "text"]
check("卡片背景" in _dtexts and "软件背景" in _dtexts, "弹窗里两个入口都在")
p._dlg.close()
p.root.update()

# 头部进来的没有 key → 可以用 ◀ ▶ 换设备，两个入口同样都在
p._open_bg_picker("")
p.root.update()
check(p._dlg.key == "" and p._dlg._target() in app.snapshot(), "头部进来可选目标设备")
p._dlg._cycle(1)
check(p._dlg._target() != "", "◀ ▶ 能切换目标设备")
p._dlg.close()
p.root.update()

# =====================================================================
# Bug 3：背景能换第二次（PhotoImage 缓存键 + 文件覆盖）
# =====================================================================
if customize.available():
    from PIL import Image as _I2
    from ez24g.ui import crop as _crop
    red = os.path.join(tmp, "r.png")
    blue = os.path.join(tmp, "b.png")
    _I2.new("RGB", (700, 500), (210, 40, 40)).save(red)
    _I2.new("RGB", (700, 500), (40, 80, 210)).save(blue)
    from tkinter import filedialog as _fd

    _orig_open = _fd.askopenfilename
    try:
        seen = []
        for src in (red, blue):
            _fd.askopenfilename = lambda *a, **k: src
            p._pick_app_bg()
            p.root.update()
            check(isinstance(p._dlg, _crop.CropDialog), "选完图先进裁剪窗")
            if isinstance(p._dlg, _crop.CropDialog):
                p._dlg._confirm()          # 默认选框（居中 80%）直接确认
            p.root.update()
            name = config.get_app_bg()
            pil = customize.app_image(name, p.W, p.H, 1.0, p.theme.pal.window)
            seen.append(pil.getpixel((5, 5)))
            img_items = [i for i in p.cv.find_all() if p.cv.type(i) == "image"]
            check(bool(img_items), "软件背景图出现在画布上")
        check(seen[0] != seen[1], f"第二次换图生效：{seen[0]} → {seen[1]}")

        # 同一张图再换一次：不报错、也不变
        _fd.askopenfilename = lambda *a, **k: blue
        p._pick_app_bg()
        p.root.update()
        if isinstance(p._dlg, _crop.CropDialog):
            p._dlg._confirm()
        p.root.update()
        pil = customize.app_image(config.get_app_bg(), p.W, p.H, 1.0,
                                  p.theme.pal.window)
        check(pil.getpixel((5, 5)) == seen[1], "同一张图换第二次不报错也不变")

        # 卡片背景也换两次
        seen_card = []
        for src in (red, blue):
            _fd.askopenfilename = lambda *a, **k: src
            p._pick_card_bg("d1")
            p.root.update()
            if isinstance(p._dlg, _crop.CropDialog):
                p._dlg._confirm()
            p.root.update()
            name = config.get_card_bg("d1")
            pil = customize.card_image(name, 100, 60, 8, 1.0,
                                       p.theme.pal.window)
            seen_card.append(pil.getpixel((50, 30)) if pil else None)
        check(seen_card[0] != seen_card[1],
              f"卡片背景第二次换图生效：{seen_card}")

        # 坏图：弹窗里要写具体原因，不是笼统「打不开」
        bad = os.path.join(tmp, "bad.png")
        with open(bad, "wb") as fh:
            fh.write(b"this is not an image at all")
        _fd.askopenfilename = lambda *a, **k: bad
        from tkinter import messagebox as _mb
        from ez24g.ui import panel as _panel_mod
        shown = []
        _orig_err = _mb.showerror
        _mb.showerror = lambda title, msg, **k: shown.append((title, msg))
        try:
            p._pick_app_bg()
            p.root.update()
        finally:
            _mb.showerror = _orig_err
        msg = shown[0][1] if shown else ""
        check(bool(shown), "坏图会弹错误提示（不是静默失败）")
        check("UnidentifiedImageError" in msg or "cannot identify" in msg,
              f"错误里带具体原因：{msg.splitlines()[-1][:60]}")
        check(customize.last_error() != "", "last_error() 记录了失败原因")
    finally:
        _fd.askopenfilename = _orig_open
        config.set_app_bg("")
        config.clear_card_bg("d1")
        customize.clear_cache()
        p._img_cache.clear()
        p.render()
        p.root.update()
else:
    check(True, "未安装 Pillow，换图断言跳过")

# =====================================================================
# §3.12.1 背景裁剪弹窗
# =====================================================================
if customize.available():
    from ez24g.ui import crop as _cropmod

    src_img = _I2.new("RGB", (700, 500), (220, 30, 30))     # 左半红
    src_img.paste(_I2.new("RGB", (350, 500), (30, 60, 220)), (350, 0))  # 右半蓝

    class _CEv:
        def __init__(self, x, y):
            self.x, self.y = int(x), int(y)
            self.x_root, self.y_root = int(x) + 500, int(y) + 300

    got = []
    d = _cropmod.CropDialog(p, src_img, (348, 272), lambda im: got.append(im))
    d.root.update()
    tx = [d.cv.itemcget(i, "text") for i in d.cv.find_all()
          if d.cv.type(i) == "text"]
    check("输出尺寸：348 × 272（自动适配，不会拉伸/留白）" in tx,
          f"裁剪窗常驻输出尺寸：{tx[:3]}")
    check(bool(d.cv.find_withtag("dlg_close")), "裁剪窗也有右上角 X")
    check(d._orig_size() == (560, 400),
          f"初始选框 = 显示区 80% 对应的原图像素：{d._orig_size()}")

    def _sel_drag(dlg, fx, fy, tx_, ty_):
        dlg._press(_CEv(fx, fy))
        dlg._drag(_CEv(tx_, ty_))
        dlg._release(_CEv(tx_, ty_))

    x1, y1, x2, y2 = d.sel
    w0, h0 = x2 - x1, y2 - y1
    # 拖右边中点：只改宽
    _sel_drag(d, x2, (y1 + y2) / 2, x2 + 40, (y1 + y2) / 2)
    w1, h1 = d.sel[2] - d.sel[0], d.sel[3] - d.sel[1]
    check(abs(w1 - (w0 + 40)) < 1.5 and abs(h1 - h0) < 1.5,
          f"拖右把手只改宽：{w0:.0f}×{h0:.0f} → {w1:.0f}×{h1:.0f}")
    # 拖下边中点：只改高
    _sel_drag(d, (d.sel[0] + d.sel[2]) / 2, d.sel[3],
              (d.sel[0] + d.sel[2]) / 2, d.sel[3] + 30)
    w2, h2 = d.sel[2] - d.sel[0], d.sel[3] - d.sel[1]
    check(abs(w2 - w1) < 1.5 and abs(h2 - (h1 + 30)) < 1.5,
          f"拖下把手只改高：{w1:.0f}×{h1:.0f} → {w2:.0f}×{h2:.0f}")
    # 拖过头：不超出图片显示区
    _sel_drag(d, d.sel[2], d.sel[3], 5000, 5000)
    dx0, dy0, dw, dh = d.disp
    check(d.sel[2] <= dx0 + dw + 0.5 and d.sel[3] <= dy0 + dh + 0.5,
          f"选框不越出显示区：{d.sel} vs {d.disp}")
    # 把右下把手往左上拖过头：夹在最小边，不翻转
    _sel_drag(d, d.sel[2], d.sel[3], dx0 - 3000, dy0 - 3000)
    _mw, _mh = d.sel[2] - d.sel[0], d.sel[3] - d.sel[1]
    check(abs(_mw - _cropmod.MIN_SEL) < 0.5
          and abs(_mh - _cropmod.MIN_SEL) < 0.5,
          f"反向拖到底夹在最小边 {_cropmod.MIN_SEL}：{_mw:.0f}×{_mh:.0f}")
    check(d.sel[2] > d.sel[0] and d.sel[3] > d.sel[1], "选框不翻转")
    # 框外点击不重画
    _before = list(d.sel)
    d._press(_CEv(dx0 + 4, dy0 + 4))
    d._release(_CEv(dx0 + 4, dy0 + 4))
    check(d.sel == _before, "框外点击不改选框")
    # 选区数字是原图像素
    _ow, _oh = d._orig_size()
    check(abs(_ow - (d.sel[2] - d.sel[0]) / d.k) < 2,
          f"选区数字 = 显示宽度 / 缩放比：{_ow} vs "
          f"{(d.sel[2] - d.sel[0]) / d.k:.0f}")
    d.close()
    d.root.update()

    # 确认后拿到的是「框内那块」（把框整体挪到右半，验证是蓝的）
    got.clear()
    d2 = _cropmod.CropDialog(p, src_img, (348, 272), lambda im: got.append(im))
    d2.root.update()
    _sel_drag(d2, (d2.sel[0] + d2.sel[2]) / 2, (d2.sel[1] + d2.sel[3]) / 2,
              (d2.sel[0] + d2.sel[2]) / 2 + 200, (d2.sel[1] + d2.sel[3]) / 2)
    d2._confirm()
    d2.root.update()
    check(len(got) == 1, f"确认裁剪回调拿到图：{len(got)} 张")
    if got:
        check(got[0].size == (348, 272), f"裁完缩放到目标尺寸：{got[0].size}")
        r, g, b = got[0].getpixel((174, 136))
        check(b > r, f"框在右半 → 裁出来是蓝色那块：{(r, g, b)}")

    # 取消：配置一点不动
    config.set_app_bg("")
    d3 = _cropmod.CropDialog(p, src_img, (760, 560), p._apply_app_bg)
    d3.root.update()
    d3._confirm()
    d3.root.update()
    check(config.get_app_bg() == "app_bg.png", "确认后才写入配置")
    config.set_app_bg("")
    d4 = _cropmod.CropDialog(p, src_img, (760, 560), p._apply_app_bg)
    d4.root.update()
    d4.close()
    d4.root.update()
    check(config.get_app_bg() == "", "取消裁剪不改配置")
    customize.clear_cache()
    p._img_cache.clear()

# =====================================================================
# 第 3 项：电池颜色按设备单独设置
# =====================================================================
from ez24g.ui import dialogs as _dlg3

app._readings = {
    "devA": Reading(ok=True, level=80, name="设备A", vid=0x1A00, pid=0x2A00,
                    usage_page=0xFF00, hid_known=True),
    "devB": Reading(ok=True, level=70, name="设备B", vid=0x1B00, pid=0x2B00,
                    usage_page=0xFF00, hid_known=True),
}
config.set_card_order([])
config.set_colors({}, False)
for _k in list(config.get_device_colors()):
    config.clear_device_color(_k)
p._apply_metrics(760, 560, render=True)
p.root.update()


def _fills(k):
    return {p.cv.itemcget(i, "fill") for i in p.cv.find_withtag(f"card:{k}")
            if p.cv.type(i) == "polygon"}


cd = _dlg3.ColorDialog(p)
cd.root.update()
check(not cd.cv.find_withtag("cc_devpick"), "非「电池颜色」组不显示设备下拉")
cd._pick("battery")
cd.root.update()
check(bool(cd.cv.find_withtag("cc_devpick")), "「电池颜色」组出现设备下拉")
check(cd._target_label() == "全部设备（默认）", f"默认目标：{cd._target_label()}")
cd._set_target("devA")
cd.root.update()
check(cd._target_label() == "设备A", f"选中设备后标签跟着变：{cd._target_label()}")
cd._save([255, 0, 0], live=False)
p.render()
p.root.update()
check(config.get_device_color("devA") == (255, 0, 0),
      f"设备色写进配置：{config.get_device_colors()}")
check("#ff0000" in _fills("devA"), f"设备A 卡片电池变红：{_fills('devA')}")
check("#ff0000" not in _fills("devB"), f"设备B 不受影响：{_fills('devB')}")

cd.close()
cd2 = _dlg3.ColorDialog(p)
cd2.root.update()
cd2._pick("battery")
cd2._set_target("devA")
check(cd2._current() == [255, 0, 0], f"重开弹窗仍显示设备色：{cd2._current()}")

# 默认项改绿 → A 仍红（设备色优先），B 跟随全局
cd2._set_target("")
cd2._save([0, 200, 0], live=False)
p.render()
p.root.update()
check("#ff0000" in _fills("devA") and "#00c800" in _fills("devB"),
      f"设备色优先于全局：A={_fills('devA')} B={_fills('devB')}")

# 选中设备时「恢复默认」只清设备色，不动全局
cd2._set_target("devA")
cd2._reset()
p.render()
p.root.update()
check(config.get_device_color("devA") is None
      and config.get_colors(False).get("battery") == [0, 200, 0],
      f"恢复默认只清设备色：{config.get_device_colors()} / "
      f"{config.get_colors(False)}")
cd2.close()
p.root.update()

# 设备色与主题无关
config.set_device_color("devA", (10, 20, 30))
_light = app.theme_light
app.theme_light = not _light
p.render()
check(config.get_device_color("devA") == (10, 20, 30), "切主题后设备色保留")
app.theme_light = _light
p.render()

# 删除设备要清掉它的设备色
_orig_remove = app.remove_device
app.remove_device = lambda k: app._readings.pop(k, None)
try:
    p.delete_device("devA")
finally:
    app.remove_device = _orig_remove
check("devA" not in config.get_device_colors(),
      f"删除设备后配置无残留：{config.get_device_colors()}")

config.set_colors({}, False)
for _k in list(config.get_device_colors()):
    config.clear_device_color(_k)
p.root.update()

# =====================================================================
# 第 8 项：字体大小
# =====================================================================
from ez24g.ui import theme as _theme_mod

config.set_font_scale(1.0)
_theme_mod.set_font_scale(1.0)
p._apply_metrics(760, 560, render=True)
p.root.update()


def _name_bbox(k):
    r = app.snapshot().get(k)
    for cand in (f"cardname:{k}", f"cardname_{id(r)}" if r else ""):
        if not cand:
            continue
        bb = p.cv.bbox(cand)
        if bb:
            return bb
    return None


fd = _dlg3.FontDialog(p)
fd.root.update()
check(fd.s_font.value == 50, f"打开时滑块居中：{fd.s_font.value}")
check(config.get_font_scale() == 1.0, "默认字号 1.0")
check(bool(fd.cv.find_withtag("dlg_close")), "字体弹窗也有右上角 X")


def _font_at(frac):
    fd._press(_Ev((fd.s_font.x + fd.s_font.w * frac) * fd.S,
                  (fd.s_font.y + 3) * fd.S))
    fd._release(_Ev(0, 0))
    p.root.update()


_bb_title_100 = p.cv.bbox(p._title_tag)
_s100 = _theme_mod.font_scale()
_font_at(0.0)
check(abs(config.get_font_scale() - 0.85) < 0.01
      and abs(_theme_mod.font_scale() - 0.85) < 0.01,
      f"左端 → 0.85：config={config.get_font_scale()} "
      f"theme={_theme_mod.font_scale()}")
_font_at(1.0)
check(abs(config.get_font_scale() - 1.25) < 0.01
      and abs(_theme_mod.font_scale() - 1.25) < 0.01,
      f"右端 → 1.25：config={config.get_font_scale()} "
      f"theme={_theme_mod.font_scale()}")
_bb_title_125 = p.cv.bbox(p._title_tag)
check(_bb_title_125[3] - _bb_title_125[1] > _bb_title_100[3] - _bb_title_100[1],
      f"1.25 时标题变高：{_bb_title_100[3] - _bb_title_100[1]} → "
      f"{_bb_title_125[3] - _bb_title_125[1]}")
check(_bb_title_125[2] <= p._btn_refresh.x * p.S + 1,
      f"标题不越到「立即刷新」按钮上：{_bb_title_125[2]} vs "
      f"{p._btn_refresh.x * p.S:.0f}")

_bad_name = []
for _k, _rect in p._card_rects.items():
    _bb = _name_bbox(_k)
    if not _bb:
        continue
    if _bb[0] < _rect[0] * p.S + 6 or _bb[2] > _rect[2] * p.S - 6:
        _bad_name.append((_k, _bb, _rect))
check(not _bad_name, f"1.25 下卡片名都在卡内（越界 {len(_bad_name)}）")

# 落盘 + 重启（走同一条读取路径）
check(abs(config.get_font_scale() - 1.25) < 0.01, "字号写进配置")
p.theme = p._theme()
check(abs(_theme_mod.font_scale() - 1.25) < 0.01, "重启后字号保持")

fd._reset()
p.root.update()
check(abs(config.get_font_scale() - 1.0) < 0.01 and fd.s_font.value == 50,
      f"恢复默认 → 1.0，滑块回中：{fd.s_font.value}")
fd.close()
p.root.update()

# 没有 Pillow 也能开这个窗
_orig_avail = customize.available
customize.available = lambda: False
try:
    p._open_font()
    p.root.update()
    check(isinstance(p._dlg, _dlg3.FontDialog), "无 Pillow 时字体窗照常打开")
    p._dlg.close()
    p.root.update()
finally:
    customize.available = _orig_avail

config.set_font_scale(1.0)
_theme_mod.set_font_scale(1.0)
p._apply_metrics(760, 560, render=True)
p.root.update()

# =====================================================================
# 第 10 项：昵称单击改名（弹窗）+ 长按拖动改位置
# =====================================================================
app._readings = {"nnA": Reading(ok=True, level=77, name="我的键盘",
                                vid=0x1C00, pid=0x2C00, usage_page=0xFF00,
                                hid_known=True)}
config.set_card_order([])
config.clear_name_pos("nnA")
p._apply_metrics(760, 560, render=True)
p.root.update()

_bb = p.cv.bbox("cardname:nnA")
check(bool(_bb), "昵称带 cardname:key 标签，可按 key 反查")
_rect = p._card_rects["nnA"]
_cx, _cy = (_bb[0] + _bb[2]) / 2, (_bb[1] + _bb[3]) / 2

# 单击 → 弹窗改名
p._on_press(_Ev(_cx, _cy))
check(p._name_press is not None and p._name_press["key"] == "nnA",
      "按在昵称上进入昵称模式（不会当成整卡拖动）")
p._on_release(_Ev(_cx, _cy))
p.root.update()
check(isinstance(p._dlg, _dlg10.RenameDialog), "单击昵称打开改名窗")
if p._dlg is not None:
    p._dlg.close()
p.root.update()

# 短按 + 极小位移（< 4px）→ 仍算单击，开改名窗
p._on_press(_Ev(_cx, _cy))
p._on_drag(_Ev(_cx + 1, _cy))
check(p._name_press is not None and p._name_press["drag"] is False,
      "移动不到 4px 仍算单击")
p._on_release(_Ev(_cx + 1, _cy))
p.root.update()
check(isinstance(p._dlg, _dlg10.RenameDialog), "单击（没移动）打开改名窗")
if p._dlg is not None:
    p._dlg.close()
p.root.update()

# 短按 + 移动 ≥4px → 立刻进拖动（不必等满 0.18s，这是 bug 4 的回归点）
p._on_press(_Ev(_cx, _cy))
p._on_drag(_Ev(_cx + 10, _cy))
check(p._name_press is not None and p._name_press["drag"] is True,
      "短按+移动直接进入拖动")
p._on_release(_Ev(_cx + 10, _cy))
p.root.update()
_dx0, _dy0 = config.get_name_pos("nnA")
check((_dx0, _dy0) != (0.0, 0.0), f"拖动写盘：{(_dx0, _dy0)}")
check(p._dlg is None, "拖动松手不弹改名窗")
config.clear_name_pos("nnA")
p.render()
p.root.update()
_bb = p.cv.bbox("cardname:nnA")
_cx, _cy = (_bb[0] + _bb[2]) / 2, (_bb[1] + _bb[3]) / 2

# 长按 500ms 再拖 → 照样能拖
p._on_press(_Ev(_cx, _cy))
p._name_press["t"] = time.time() - 0.5          # 模拟按住 500ms
p._on_drag(_Ev(_cx + 30, _cy - 20))
check(p._name_press["drag"] is True, "按住 500ms 后进入拖动")
p._on_release(_Ev(_cx + 30, _cy - 20))
p.root.update()
_dx, _dy = config.get_name_pos("nnA")
check((_dx, _dy) != (0.0, 0.0), f"松手写盘：{(_dx, _dy)}")
check(p._dlg is None, "拖动松手不弹改名窗")
_bb2 = p.cv.bbox("cardname:nnA")
check(abs((_bb2[0] + _bb2[2]) / 2 - (_cx + 30 * p.S)) <= 1.5,
      f"重绘后位置与松手位置一致：{(_bb2[0]+_bb2[2])/2:.0f} vs {_cx + 30 * p.S:.0f}")

# 往右拖出卡片 → 夹在卡片右内边
p._on_press(_Ev((_bb2[0] + _bb2[2]) / 2, (_bb2[1] + _bb2[3]) / 2))
p._name_press["t"] = time.time() - 0.5
p._on_drag(_Ev(4000, (_bb2[1] + _bb2[3]) / 2))
p._on_release(_Ev(4000, (_bb2[1] + _bb2[3]) / 2))
p.root.update()
_bb3 = p.cv.bbox("cardname:nnA")
check(_bb3[2] <= _rect[2] * p.S - 8 * p.S + 1,
      f"右边界夹在卡片内：{_bb3[2]} vs {_rect[2] * p.S - 8 * p.S:.0f}")

# 往下拖到底 → 停在电池框上方
p._on_press(_Ev((_bb3[0] + _bb3[2]) / 2, (_bb3[1] + _bb3[3]) / 2))
p._name_press["t"] = time.time() - 0.5
p._on_drag(_Ev((_bb3[0] + _bb3[2]) / 2, 4000))
p._on_release(_Ev((_bb3[0] + _bb3[2]) / 2, 4000))
p.root.update()
_bb4 = p.cv.bbox("cardname:nnA")
from ez24g.ui import cards as _cards10
_top = _cards10.battery_top(_rect[3]) * p.S
check(_bb4[3] <= _top - 1, f"下边界停在电池上方：{_bb4[3]} vs {_top:.0f}")

# 非昵称区域按住 → 仍然是整卡拖动（排序不受影响）
p._on_press(_Ev(_rect[0] * p.S + 20, _rect[1] * p.S + 40))
check(p._press is not None and p._name_press is None,
      "卡片空白处按下仍是卡片拖动，不是昵称")
p._press = None

config.clear_name_pos("nnA")
p.render()
p.root.update()

# -- 外观自定义：背景 / 透明度 / 颜色 -----------------------------------
from ez24g.ui import customize

if customize.available():
    from PIL import Image as _Img
    src = os.path.join(tmp, "bg.png")
    _Img.new("RGB", (600, 400), (200, 60, 60)).save(src)

    # 每张卡都有统一 tag（后面所有功能的地基）
    config.set_card_order([])
    app._readings = {"x369B0054": Reading(
        ok=True, level=87, name="L8 Air", vid=0x369B, pid=0x0054,
        usage_page=0xFF05, hid_known=True)}
    p.render()
    p.root.update()
    check(bool(p.cv.find_withtag("card:x369B0054"))
          and bool(p.cv.find_withtag("cardbg:x369B0054")),
          "卡片 item 都打上了 card / cardbg tag")

    fn = customize.import_image(src, "card_x369B0054.png")
    config.set_card_bg("x369B0054", fn)
    customize.clear_cache()
    p.render()
    p.root.update()
    tags = p.cv.find_withtag("cardbg:x369B0054")
    kinds = [p.cv.type(i) for i in tags]
    check(os.path.isfile(os.path.join(config.media_dir(), fn)),
          f"背景图已复制进 media：{fn}")
    check("image" in kinds, f"卡片背景出现图片 item {kinds}")
    name_tag = "cardname:x369B0054"
    bb = p.cv.bbox(name_tag)
    ov = p.cv.find_overlapping(*bb) if bb else []
    img_id = [i for i in tags if p.cv.type(i) == "image"]
    check(bool(img_id) and img_id[0] in ov
          and ov.index(img_id[0]) < min(ov.index(i) for i in ov
                                        if p.cv.type(i) == "text"),
          "背景图在文字层之下（不遮设备名）")
    check(not any(p.cv.itemcget(i, "text") == "L"
                  for i in p.cv.find_all() if p.cv.type(i) == "text"),
          "有背景图后不再画首字母徽标")
    os.remove(src)                     # 删掉原图，背景仍要在（说明是复制存储）
    customize.clear_cache()
    p.render()
    p.root.update()
    check("image" in [p.cv.type(i)
                      for i in p.cv.find_withtag("cardbg:x369B0054")],
          "删掉原图后背景仍显示（复制存储）")

    # 软件背景 + 头部裁片
    app_src = os.path.join(tmp, "appbg.png")
    _Img.new("RGB", (900, 700), (40, 90, 160)).save(app_src)
    config.set_app_bg(customize.import_image(app_src, "app_bg.png"))
    customize.clear_cache()
    p.render()
    p.root.update()
    imgs = [i for i in p.cv.find_all() if p.cv.type(i) == "image"]
    # 整窗只铺一张合成图：以前头部/底部还会各贴一张 slice 与整窗图重叠，
    # 窗口缩放时就会出现上下两张图 / 分层。
    _hdr_full = [i for i in imgs
                 if (lambda b: b and b[0] <= 1 and b[1] <= 1)(p.cv.bbox(i))]
    check(len(_hdr_full) == 1 and len(imgs) >= 2,
          f"软件背景只铺一张整窗图、不再叠上下裁片（整窗 {len(_hdr_full)} / 共 {len(imgs)} 张）")
    tx = [p.cv.itemcget(i, "text") for i in p.cv.find_all()
          if p.cv.type(i) == "text"]
    check(any("台 ·" in t for t in tx) and "立即刷新" in tx,
          "加背景后头部/卡片文字照常绘制")

    # 透明度：卡片层对着「已合成的软件背景」混合，中间态不留白底（§3.1）
    from ez24g.ui import customize as _cz

    def _card_pil(op_card):
        config.set_opacity(op_card, 100)
        p.render()
        p.root.update()
        x1, y1, x2, y2 = p._card_rects["x369B0054"]
        w = int(round((x2 - x1) * p.S))
        h = int(round((y2 - y1) * p.S))
        base = p._app_layer_img.crop((int(round(x1 * p.S)),
                                      int(round(y1 * p.S)),
                                      int(round(x1 * p.S)) + w,
                                      int(round(y1 * p.S)) + h))
        lay = _cz.card_layer(config.get_card_bg("x369B0054"), w, h,
                             int(p.theme.card_radius * p.S),
                             1 - op_card / 100.0, base, p.theme.pal.card)
        return lay, base, (w // 2, h // 2)

    lay50, base50, mid = _card_pil(50)
    c50 = lay50.getpixel(mid)[:3]
    b50 = base50.getpixel(mid)[:3]
    check(c50 != (255, 255, 255), f"卡片 50% 透明度不是纯白：{c50}")
    check(max(abs(c50[i] - b50[i]) for i in range(3)) < 140,
          f"卡片 50% 与软件背景层同色系：{c50} vs {b50}")
    lay100, base100, mid = _card_pil(100)
    check(lay100.getpixel(mid)[:3] == base100.getpixel(mid)[:3],
          f"卡片 100% 透明时像素与软件背景层完全一致："
          f"{lay100.getpixel(mid)[:3]}")
    lay0, base0, mid = _card_pil(0)
    check(lay0.getpixel(mid)[:3] != base0.getpixel(mid)[:3],
          f"卡片 0%（不透明）时盖住软件背景：{lay0.getpixel(mid)[:3]}")
    check("image" in [p.cv.type(i) for i in
                      p.cv.find_withtag("cardbg:x369B0054")],
          "透明度中间态用合成图，不再靠多边形填色")
    config.set_opacity(100, 100)
    p._img_cache.clear()

    # 颜色
    config.set_colors({"text": [200, 0, 0]}, False)
    p.render()
    p.root.update()
    title_item = p.cv.find_withtag(p._title_tag)[0]
    check(p.cv.itemcget(title_item, "fill") == "#c80000",
          f"文字颜色生效：{p.cv.itemcget(title_item, 'fill')}")
    config.set_colors({}, False)
    p.render()

    # 弹窗
    from ez24g.ui import dialogs
    d = dialogs.OpacityDialog(p)
    p.root.update()
    check(len(d.cv.find_all()) > 10, "透明度弹窗画出来了")
    d.s_card.set_from(type("E", (), {"x": 130 + 200 * 0.4, "y": 73})())
    check(config.get_opacity()["card"] == 40,
          f"滑块写入 40：{config.get_opacity()}")
    d.close()

    c = dialogs.ColorDialog(p)
    p.root.update()
    check(len(c.cv.find_all()) > 15, "颜色弹窗画出来了")
    c._pick("ui")
    c._save([10, 120, 220])
    check(config.get_colors(False).get("ui") == [10, 120, 220],
          f"颜色写进配置：{config.get_colors(False)}")
    c.close()
    config.set_colors({}, False)

    # 一帧耗时（带背景+透明度）：只量 render 本身，root.update() 里的
    # 系统绘制受机器负载影响，当成断言会抖动（实测同一份代码 25ms / 53ms 都出现过）。
    # 这一项防的是「PhotoImage 每帧重建」那类 90ms+ 回归，不是本机性能基准。
    # 40ms 在 125% DPI + 6 卡 + 背景下会偶发超 1ms（实测 40.5 / 41.4ms），
    # 50ms 在机器负载高时仍偶发 54.3ms；留足余量后仍能拦住 90ms+ 退化。
    config.set_opacity(30, 10)
    app._readings = {f"d{i}": Reading(ok=True, level=90 - i, name=f"设备{i}",
                                     vid=0x1200 + i, pid=0x3400 + i,
                                     usage_page=0xFF00, hid_known=True)
                     for i in range(6)}
    customize.clear_cache()
    costs, full = [], []
    for _ in range(10):
        t0 = time.perf_counter()
        p.render()
        costs.append((time.perf_counter() - t0) * 1000)
        t1 = time.perf_counter()
        p.root.update()
        full.append((time.perf_counter() - t0) * 1000)
    mid = sorted(costs)[len(costs) // 2]
    mid_full = sorted(full)[len(full) // 2]
    check(mid < 70, f"带背景 render 中位 {mid:.1f}ms < 70ms（防 90ms+ 回归）")
    check(mid_full < 120, f"连系统绘制一起中位 {mid_full:.1f}ms < 120ms")

    # 收尾：清掉自定义，别影响别的用例
    for k in list(app._readings):
        config.clear_card_bg(k)
    config.set_app_bg("")
    config.set_opacity(100, 100)
    customize.clear_cache()
    p.render()
else:
    check(True, "未安装 Pillow，背景相关断言跳过")


# =====================================================================
# Bug 4 / 5：弹窗坐标系、滑块热区、误拖窗口、色轮性能
# =====================================================================
class _StubPanel:
    """只给弹窗用的面板替身：把 S 调成 1.5 又不破坏真实面板的几何。"""

    def __init__(self, real, scale):
        self.app = real.app
        self.root = real.root
        self.S = scale
        self.W, self.H = real.W, real.H
        self._shadow = None
        self._visible = False
        self._dlg = None
        self.renders = 0

    def render(self):
        self.renders += 1

    # 外观设置统一入口；替身按原来的 render 计一次即可
    _settings_changed = render

    def snapshot(self):
        return self.app.snapshot()


class _PEv:
    def __init__(self, x, y):
        self.x, self.y = int(x), int(y)
        self.x_root, self.y_root = int(x) + 500, int(y) + 300


if customize.available():
    sp = _StubPanel(p, 1.5)

    # 裁剪窗在 150%（S 夹到 1.35）下选框跟手，没有偏移
    from ez24g.ui import crop as _cropmod2
    _cimg = _I2.new("RGB", (700, 500), (200, 60, 60))
    d5 = _cropmod2.CropDialog(sp, _cimg, (760, 560), None)
    d5.root.update()
    check(abs(d5.S - 1.35) < 1e-6, f"弹窗缩放夹在 1.35：{d5.S}")
    _sx = d5.sel[0]
    _cxp = (d5.sel[0] + d5.sel[2]) / 2 * d5.S
    _cyp = (d5.sel[1] + d5.sel[3]) / 2 * d5.S
    d5._press(_PEv(_cxp, _cyp))
    d5._drag(_PEv(_cxp + 30 * d5.S, _cyp))
    d5._release(_PEv(_cxp + 30 * d5.S, _cyp))
    check(abs((d5.sel[0] - _sx) - 30) < 1.5,
          f"150% 下选框跟手：位移 {d5.sel[0] - _sx:.1f} 逻辑像素（期望 30）")
    d5.close()

    d = dialogs.OpacityDialog(sp)
    d.root.update()
    bb = d.cv.bbox("all")
    check(bb[0] <= 2 and bb[1] <= 2 and bb[2] >= d.W - 4 and bb[3] >= d.H - 4,
          f"150% 弹窗内容铺满：bbox={bb}，窗口 {d.W}x{d.H}")
    check(len(d._sliders()) == 2, f"两个滑块：{[s.value for s in d._sliders()]}")
    check(not hasattr(d, "s_menu"), "透明度弹窗里没有第三个滑块（菜单档已下线）")

    def _slider_at(sl, frac):
        d._press(_PEv((sl.x + sl.w * frac) * d.S, (sl.y + 3) * d.S))
        d._release(_PEv(0, 0))

    _slider_at(d.s_card, 0.0)
    v0 = config.get_opacity()["card"]
    _slider_at(d.s_card, 0.5)
    v1 = config.get_opacity()["card"]
    _slider_at(d.s_card, 1.0)
    v2 = config.get_opacity()["card"]
    check(v0 == 0 and abs(v1 - 50) <= 2 and v2 == 100,
          f"点滑块左/中/右 → {v0}/{v1}/{v2}")

    _slider_at(d.s_app, 0.0)
    op = config.get_opacity()
    check(op["app"] == 0 and op["card"] == 100,
          f"拖「软件背景」只改 app：{op}")

    # 内容区按下拖动 → 窗口不跟着跑
    x_before = d.root.winfo_x()
    d._press(_PEv(40 * d.S, 150 * d.S))
    d._drag(_PEv(240 * d.S, 160 * d.S))
    d._release(_PEv(0, 0))
    check(d.root.winfo_x() == x_before, f"空白处拖动窗口不动（x={x_before}）")

    # 标题区按下拖动 → 窗口应该跟手（这是唯一的拖动入口）
    d._press(_PEv(60 * d.S, 20 * d.S))
    d._drag(_PEv(90 * d.S, 20 * d.S))
    d.root.update_idletasks()
    check(d.root.winfo_x() != x_before,
          f"标题区仍能拖动窗口（{x_before} → {d.root.winfo_x()}）")
    d._release(_PEv(0, 0))

    # 手滑出界也还在拖（热区放宽）
    sl = d.s_card
    inside = sl.hit(_PEv((sl.x + 10) * d.S, (sl.y - 12) * d.S))
    check(inside, "滑块热区纵向放宽到轨道上方 12px")
    d.close()
    sp._dlg = None

    # ---- 二级弹窗右上角 X（_Base 统一提供）----
    def _alive(win):
        try:
            return bool(win.root.winfo_exists())
        except Exception:
            return False

    xd = dialogs.OpacityDialog(_StubPanel(p, 1.5))
    xd.root.update()
    check(bool(xd.cv.find_withtag("dlg_close")), "透明度弹窗画了右上角 X")
    bbx = xd.cv.bbox("dlg_close")
    check(bool(bbx) and bbx[2] >= xd.W - 40 * xd.S and bbx[1] <= 30 * xd.S,
          f"150% 下 X 落在窗口右上角：{bbx}，窗口 {xd.W}x{xd.H}")
    xd.cv.event_generate("<Button-1>",
                         x=int((xd.LW - 30) * xd.S), y=int(20 * xd.S))
    xd.root.update()
    check(not _alive(xd), "点 X 能关掉透明度弹窗")

    for _cls, _args in (("ColorDialog", ()), ("BackgroundDialog", ())):
        _klass = getattr(dialogs, _cls)
        _w = _klass(_StubPanel(p, 1.5), *_args)
        _w.root.update()
        check(bool(_w.cv.find_withtag("dlg_close")), f"{_cls} 也有右上角 X")
        _w.close()
        _w.root.update()

    # 从面板打开时，X 关闭后 panel._dlg 要归零
    p._open_opacity()
    p.root.update()
    _d0 = p._dlg
    check(_d0 is not None and bool(_d0.cv.find_withtag("dlg_close")),
          "面板打开的透明度弹窗带 X")
    if _d0 is not None:
        _d0.close()
        p.root.update()
    check(p._dlg is None, "X 关闭后 panel._dlg 归零")

    # ---- 颜色弹窗 ----
    c = dialogs.ColorDialog(_StubPanel(p, 1.5))
    c.root.update()
    bb = c.cv.bbox("all")
    check(bb[2] >= c.W - 4 and bb[3] >= c.H - 4,
          f"颜色弹窗 150% 也铺满：bbox={bb}，窗口 {c.W}x{c.H}")
    check(len(c.entries) == 3, "RGB 三个输入框都在")

    # 点 SV 方块右上角 = 当前色相的纯色（饱和 100%、明度 100%）
    c._press(_PEv((20 + 150 * 0.995) * c.S, (90 + 1) * c.S))
    c.root.update()
    got = config.get_colors(False).get(c.group)
    check(got and got[2] > 200 and got[0] < 60, f"点 SV 右上角得到纯色：{got}")
    check(c.cv.itemcget("preview_text", "text") == "#%02X%02X%02X" % tuple(got),
          "预览 HEX 与配置一致")

    # 输入 255/0/0 → 纯红，marker 跑到右上
    for ch, val in zip("RGB", ("255", "0", "0")):
        c.entries[ch].delete(0, "end")
        c.entries[ch].insert(0, val)
    c._from_entries()
    c.root.update()
    check(config.get_colors(False).get(c.group) == [255, 0, 0],
          f"输入 255/0/0 → {config.get_colors(False).get(c.group)}")
    check(c.cv.itemcget("preview_text", "text") == "#FF0000", "预览变 #FF0000")

    # Entry 不被重建：拖色相时焦点/内容不丢
    ids_before = [id(e) for e in c.entries.values()]
    n_items = len(c.cv.find_all())
    cost, cost_upd = [], []
    for i in range(30):
        y = (90 + 150 * (i / 30)) * c.S
        t0 = time.perf_counter()
        c._drag(_PEv(187 * c.S, y))
        cost.append((time.perf_counter() - t0) * 1000)
        t1 = time.perf_counter()
        c.root.update()
        cost_upd.append((time.perf_counter() - t1) * 1000 + cost[-1])
    med = sorted(cost)[len(cost) // 2]
    med_upd = sorted(cost_upd)[len(cost_upd) // 2]
    check(med < 33, f"拖色相每帧处理中位 {med:.1f}ms < 33ms")
    check(med_upd < 33, f"连 update() 一起算中位 {med_upd:.1f}ms < 33ms")
    check([id(e) for e in c.entries.values()] == ids_before,
          "拖色相不重建 Entry（焦点不丢）")
    check(len(c.cv.find_all()) <= n_items + 4,
          f"拖 30 帧画布 item 不堆积：{n_items} → {len(c.cv.find_all())}")
    check(len(c.cv.find_all()) == n_items,
          f"每帧只替换 marker：{n_items} → {len(c.cv.find_all())}")

    # 切分组要完全重画且颜色独立
    c._pick("battery")
    c.root.update()
    c._save([0, 180, 60])
    c.root.update()
    cols = config.get_colors(False)
    check(cols.get("battery") == [0, 180, 60] and cols.get("text")
          and cols.get("text") != cols.get("battery"),
          f"三组颜色互相独立：{cols}")
    c.close()
    config.set_colors({}, False)
    p.render()
    p.root.update()

# =====================================================================
# Bug 6：菜单改自绘（圆角 / 翻转 / 关闭 / 透明度）
# =====================================================================
from ez24g.ui import menu as _menu_mod

src = open(os.path.join(ROOT, "ez24g", "ui", "panel.py"), encoding="utf-8").read()
check("tk.Menu(" not in src, "panel.py 里已经没有原生 tk.Menu")

p._header_menu()
p.root.update()
m = p._menu
check(isinstance(m, _menu_mod.PopupMenu), "头部菜单是自绘 PopupMenu")
check(m.root.overrideredirect(), "菜单窗口无系统边框（overrideredirect）")
kinds = [m.cv.type(i) for i in m.cv.find_all()]
check("polygon" in kinds, "菜单底是圆角多边形（不是方角矩形）")
bbm = m.cv.bbox("all")
check(bbm[2] <= m._menu_w() + 3 and bbm[3] <= m._menu_h() + 3,
      f"菜单内容不超出自身尺寸 {bbm} vs {m._menu_w()}x{m._menu_h()}")
check(isinstance(m.acrylic_ok, bool), f"亚克力尝试有结果记录（acrylic_ok={m.acrylic_ok}）")
if not m.acrylic_ok:
    check(float(m.root.attributes("-alpha")) >= 0.9,
          f"亚克力失效时走半透明兜底（alpha={m.root.attributes('-alpha')}）")

# Esc：菜单不抢焦点 → 按键只会到面板，先关菜单再关面板
p._on_escape()
p.root.update()
check(p._menu is None, "Esc 关闭菜单")
check(p._visible, "Esc 只关菜单，面板还在")
p._on_escape()
p.root.update()
check(not p._visible, "再按 Esc 才隐藏面板")
p.show()
p.root.update()

# 点面板外面关闭
p._header_menu()
p.root.update()
check(p._menu is not None, "菜单打开了")
p._on_press(_Ev(int(20 * p.S), int(20 * p.S)))
p.root.update()
check(p._menu is None, "点外面关闭菜单")

# 靠近右下角自动翻转
m3 = _menu_mod.PopupMenu(p, [("项目 %d" % i, None) for i in range(6)])
sw, sh = p.root.winfo_screenwidth(), p.root.winfo_screenheight()
m3.tk_popup(sw - 6, sh - 6)
p.root.update()
w3, h3 = m3._menu_w(), m3._menu_h()
check(m3.root.winfo_x() + w3 <= sw and m3.root.winfo_y() + h3 <= sh,
      f"右下角翻转：({m3.root.winfo_x()},{m3.root.winfo_y()}) 尺寸 {w3}x{h3}")
m3.unpost()

# 菜单底色固定，不再受「菜单背景透明度」影响（该档已下线）
config.set_opacity(100, 100, 100)
m4 = _menu_mod.PopupMenu(p, [("甲", None), ("乙", None)])
m4.tk_popup(50, 50)
p.root.update()
fill100 = m4.cv.itemcget(1, "fill")
m4.unpost()
config.set_opacity(100, 100, 30)
m5 = _menu_mod.PopupMenu(p, [("甲", None), ("乙", None)])
m5.tk_popup(50, 50)
p.root.update()
fill30 = m5.cv.itemcget(1, "fill")
m5.unpost()
check(fill100 == fill30, f"菜单底色固定，不受旧 menu 档影响：{fill100} == {fill30}")
from ez24g.ui.draw import rgb as _rgb_menu
_expect = _rgb_menu(customize.blend(p.theme.pal.card, p.theme.pal.window,
                                    _menu_mod.MENU_ALPHA))
check(fill100.lower() == _expect.lower(),
      f"菜单底色 = card ⊕ window @ MENU_ALPHA：{fill100} vs {_expect}")
config.set_opacity(100, 100, 100)

# 勾选项
p._startup_var = False
mi = p._header_menu_items()
checks = [it for it in mi if isinstance(it, tuple) and it and it[0] == "check"]
check(len(checks) == 1 and checks[0][1] == "开机自启", f"开机自启是勾选项：{checks}")
m6 = _menu_mod.PopupMenu(p, mi)
m6.tk_popup(50, 50)
p.root.update()
check(any(m6.cv.itemcget(i, "text") == "✓" for i in m6.cv.find_all()
          if m6.cv.type(i) == "text") is False,
      "未勾选时没有 ✓")
m6.unpost()
p._startup_var = True
m7 = _menu_mod.PopupMenu(p, p._header_menu_items())
m7.tk_popup(50, 50)
p.root.update()
check(any(m7.cv.itemcget(i, "text") == "✓" for i in m7.cv.find_all()
          if m7.cv.type(i) == "text"), "勾选后画 ✓")
m7.unpost()
p._startup_var = startup_enabled() if False else p._startup_var

# =====================================================================
# 本轮四个回归：菜单置顶 / 弹窗层级 / 卡片单击 / 排序落地
# =====================================================================
from ez24g.win32 import user32 as _u32, wt as _wt
from ez24g.ui.ontop import is_topmost as _is_topmost


def _root_of(hwnd):
    return _u32.GetAncestor(hwnd, 2) or hwnd          # GA_ROOT


def _top_window_at(x, y):
    return _root_of(_u32.WindowFromPoint(_wt.POINT(int(x), int(y))))


def _z_above(win, other):
    """按顶层窗口 Z 序判断 win 是否在 other 之上。

    WindowFromPoint 会被第三方置顶窗口（桌面歌词等）挡住而产生假阳性；
    这里只看「弹窗 vs 面板」的相对层级，与桌面上还有谁无关。
    """
    a = _root_of(win.winfo_id())
    b = _root_of(other.winfo_id())
    h = _u32.GetTopWindow(0)
    while h:
        if h == a:
            return True
        if h == b:
            return False
        h = _u32.GetWindow(h, 2)          # GW_HWNDNEXT
    return False


app._readings = {
    "mA": Reading(ok=True, level=88, name="设备A", vid=0x1D00, pid=0x2D00,
                  usage_page=0xFF00, hid_known=True),
    "mB": Reading(ok=True, level=66, name="设备B", vid=0x1E00, pid=0x2E00,
                  usage_page=0xFF00, hid_known=True),
}
config.set_card_order([])
p._apply_metrics(760, 560, render=True)
p.root.update()

# 1) 菜单：面板抢回焦点也不能消失（bug 3 的回归点）
p._header_menu()
p.root.update()
check(p._menu is not None, "点「设置」菜单能弹出来")
p.root.focus_force()
p.root.update()
check(p._menu is not None, "面板重新拿到焦点后菜单仍在（不再 FocusOut 自毁）")
check(_is_topmost(p._menu.root), "菜单位于最顶层（WS_EX_TOPMOST）")
# 锚点=鼠标位置：固定指针读数再开菜单，避免真人挪鼠标导致断言抖动
_orig_ptr = p.root.winfo_pointerxy
p.root.winfo_pointerxy = lambda: (600, 400)
try:
    p._header_menu()
    p.root.update()
    _geo = p._menu.root.geometry()           # "120x327+1810+586"
    _gx, _gy = int(_geo.split("+")[1]), int(_geo.split("+")[2])
finally:
    p.root.winfo_pointerxy = _orig_ptr
check(_gx == 600 and _gy == 400,
      f"菜单锚点 = 鼠标位置：{_geo} vs (600,400)")
p._close_menu()
p.root.update()

# 2) 二级弹窗不被 topmost 面板盖住（bug 4 的真因）
p._open_rename("mA")
p.root.update()
_dlg = p._dlg
check(isinstance(_dlg, _dlg10.RenameDialog), "改名窗已创建")
if _dlg is not None:
    _dcx = _dlg.root.winfo_rootx() + _dlg.root.winfo_width() // 2
    _dcy = _dlg.root.winfo_rooty() + _dlg.root.winfo_height() // 2
    check(_top_window_at(_dcx, _dcy) == _root_of(_dlg.root.winfo_id())
          or (_z_above(_dlg.root, p.root) and _is_topmost(_dlg.root)),
          "改名窗没被面板盖住（WindowFromPoint 命中自己，或 Z 序在面板之上）")
    check(_is_topmost(_dlg.root), "改名窗带 WS_EX_TOPMOST")
    _dlg.close()
p.root.update()

# 3) 卡片空白单击 → 卡片菜单（bug 4）
_r0 = p._card_rects["mA"]
_bx, _by = (_r0[0] + 100) * p.S, (_r0[1] + 20) * p.S
p._on_press(_Ev(_bx, _by))
p._on_release(_Ev(_bx, _by))
p.root.update()
_labels = ([p._menu.entrycget(i, "label")
            for i in range(p._menu.index("end") + 1)] if p._menu else [])
check("自定义背景…" in _labels and "删除设备" in _labels,
      f"单击卡片弹出卡片菜单：{_labels}")
p._close_menu()
p.root.update()

# 点「点我解决」这类子链不应弹卡片菜单
_r1 = p._card_rects["mA"]
p._on_press(_Ev((_r1[0] + 30) * p.S, (_r1[1] + 240) * p.S))
p._press_tags = ("cardfix_x",)
p._on_release(_Ev((_r1[0] + 30) * p.S, (_r1[1] + 240) * p.S))
p.root.update()
check(p._menu is None, "点在子链（点我解决）上不抢弹卡片菜单")

# 4) 拖动排序：松手后内存快照立刻变序（bug 2）
config.set_card_order([])
app.reorder_now()
p._scroll = 0.0
p.render()
p.root.update()
before = list(app.snapshot())
check(before == ["mA", "mB"], f"初始顺序 {before}")
_ra, _rb = p._card_rects["mA"], p._card_rects["mB"]
_sx, _sy = (_ra[0] + 40) * p.S, (_ra[1] + 200) * p.S
_tx, _ty = ((_rb[0] + _rb[2]) / 2) * p.S, ((_rb[1] + _rb[3]) / 2) * p.S
p._on_press(_Ev(_sx, _sy))
p._on_drag(_Ev(_sx + 20, _sy))
p._on_drag(_Ev(_tx, _ty))
p.root.update()
p._on_release(_Ev(_tx, _ty))
p.root.update()
check(list(app.snapshot()) == ["mB", "mA"],
      f"拖完快照立刻变序（不用等 60 秒）：{list(app.snapshot())}")
check(config.get_card_order()[:2] == ["mB", "mA"],
      f"配置顺序同步：{config.get_card_order()[:2]}")

# 配置里已不存在的键要被惰性清掉
config.set_card_order(["xDEADBEEF", "mA", "mB"])
p.render()
p.root.update()
check("xDEADBEEF" not in config.get_card_order(),
      f"死键被清掉：{config.get_card_order()}")
config.set_card_order([])
app.reorder_now()
p.render()
p.root.update()

# =====================================================================
# 第 9 项：窗口八向缩放 + 内部元素同步缩放 + 记住尺寸
# =====================================================================
config.set_panel_size(760, 560)
app._readings = {f"z{i}": Reading(ok=True, level=90 - i, name=f"设备{i}",
                                 vid=0x1300 + i, pid=0x3500 + i,
                                 usage_page=0xFF00, hid_known=True)
                 for i in range(4)}
config.set_card_order([])
p._apply_metrics(760, 560, render=True)
p.root.update()
p._scroll = 0.0
p.render()
p.root.update()
_w0, _h0, _s0 = p.W, p.H, p.S
_cw0 = (p._card_rects["z0"][2] - p._card_rects["z0"][0]) * p.S
check((_w0, _h0) == (760, 560), f"基准尺寸 {_w0}x{_h0}")

# 右下角往里/外拖
p._on_press(_Ev(p.W - 2, p.H - 2))
check(p._resize is not None and p._resize["edge"] == "se",
      f"右下角按下进入缩放：{p._resize and p._resize['edge']}")
p._on_drag(_Ev(p.W + 120, p.H + 80))
p.root.update()
check(p.W > _w0 and p.H > _h0, f"拖右下角变大：{p.W}x{p.H}")
check(abs(p.W / p.H - _w0 / _h0) < 0.35, "尺寸变化后仍是同一个窗口比例量级")
p.root.update()
p._on_release(_Ev(p.W + 120, p.H + 80))
p.root.update()
_s1 = p.S
_cw1 = (p._card_rects["z0"][2] - p._card_rects["z0"][0]) * p.S
check(abs(p.S - _s0) > 0.02, f"缩放后内部 S 变了：{_s0:.3f} → {p.S:.3f}")
check(abs((_cw1 / _cw0) - (_s1 / _s0)) < 0.03,
      f"卡片宽同比缩放：{_cw0:.0f}→{_cw1:.0f}，S {_s0:.3f}→{_s1:.3f}")
check(config.get_panel_size() == (p.W, p.H),
      f"松手后尺寸落盘：{config.get_panel_size()} vs {(p.W, p.H)}")

# 拖到最小：被夹在 620×440
p._on_press(_Ev(p.W - 2, p.H - 2))
p._on_drag(_Ev(-2000, -2000))
p.root.update()
p._on_release(_Ev(-2000, -2000))
p.root.update()
check((p.W, p.H) == (config.PANEL_MIN[0], config.PANEL_MIN[1]),
      f"拖到最小夹紧：{p.W}x{p.H} 期望 {config.PANEL_MIN}")
check(p.LW >= 600 and p.LH >= 400, f"逻辑尺寸不为负：LW={p.LW:.0f} LH={p.LH:.0f}")

# 拖左上角：原点跟着走，右下角不动
p._apply_metrics(760, 560, render=True)
p.root.update()
_x_right = p.root.winfo_rootx() + p.W
p._on_press(_Ev(2, 2))
check(p._resize is not None and p._resize["edge"] == "nw", "左上角能抓")
p._on_drag(_Ev(42, 32))
p.root.update()
p._on_release(_Ev(42, 32))
p.root.update()
check(abs((p.root.winfo_rootx() + p.W) - _x_right) < 2,
      f"拖左上角时右下角不动：{p.root.winfo_rootx() + p.W} vs {_x_right}")

# 缩放会作废图片缓存 + 重建阴影位图
p._img_cache["x"] = object()
p._apply_metrics(880, 620, render=True)
p.root.update()
check("x" not in p._img_cache, "缩放后 PhotoImage 缓存被清空")
if p._shadow is not None:
    p.root.update()
    p._shadow.show(p.root.winfo_x(), p.root.winfo_y())
    bm = p._shadow._bitmap
    _margin = max(24, int(28 * p.S))
    check(bm is not None and abs(bm[0] - (p.W + 2 * _margin)) <= 2,
          f"阴影位图跟着新尺寸重建：{bm and bm[0]} vs {p.W + 2 * _margin}")

# 缩放后拖动排序仍准
config.set_card_order([])
p._apply_metrics(980, 700, render=True)
p.root.update()
_ra, _rb = p._card_rects["z0"], p._card_rects["z1"]
p._on_press(_Ev(int((_ra[0] + 12) * p.S), int((_ra[1] + 12) * p.S)))
p._on_drag(_Ev(int((_rb[0] + _rb[2]) / 2 * p.S),
               int((_rb[1] + _rb[3]) / 2 * p.S)))
p._on_drag(_Ev(int((_rb[0] + _rb[2]) / 2 * p.S),
               int((_rb[1] + _rb[3]) / 2 * p.S)))
p.root.update()
check(p._drag == "z0", f"放大后仍能拖动排序：drag={p._drag}")
p._on_release(_Ev(int((_rb[0] + _rb[2]) / 2 * p.S),
                  int((_rb[1] + _rb[3]) / 2 * p.S)))
p.root.update()
check(config.get_card_order()[:2] == ["z1", "z0"],
      f"放大后排序仍写回配置：{config.get_card_order()[:2]}")

# 「重启」按保存的尺寸还原（走同一条 _compute_metrics）
_saved = (config.get_panel_size() or (760, 560))
p._compute_metrics(*_saved)
check((p.W, p.H) == _saved, f"按保存尺寸还原：{p.W}x{p.H} vs {_saved}")

# 收尾：回到基准尺寸
config.set_panel_size(760, 560)
config.set_card_order([])
p._apply_metrics(760, 560, render=True)
p.root.update()

# =====================================================================
# 第 7 项：去掉 -transparentcolor，透明度 100% 时仍能拖窗口 / 拖卡片
# =====================================================================
_src_panel = ""
for _root_dir, _dirs, _files in os.walk(os.path.join(ROOT, "ez24g")):
    for _fn in _files:
        if _fn.endswith(".py"):
            _src_panel += open(os.path.join(_root_dir, _fn),
                               encoding="utf-8").read()
check("transparentcolor" not in _src_panel and "KEY_COLOR" not in _src_panel,
      "ez24g/ 里已无色键（transparentcolor / KEY_COLOR）残留，注释也算")
try:
    _tc = p.root.attributes("-transparentcolor")
except Exception:
    _tc = ""
check(str(_tc) == "", f"-transparentcolor 为空：{_tc!r}")

app._readings = {f"z{i}": Reading(ok=True, level=90 - i, name=f"设备{i}",
                                 vid=0x1300 + i, pid=0x3500 + i,
                                 usage_page=0xFF00, hid_known=True)
                 for i in range(4)}
config.set_card_order([])
config.set_opacity(100, 100)
p._scroll = 0.0
p.render()
p.root.update()

_zr = p._card_rects["z0"]
_zcx = int((_zr[0] + _zr[2]) / 2 * p.S)
_zcy = int((_zr[1] + _zr[3]) / 2 * p.S)
p._on_press(_Ev(_zcx, _zcy))
check(p._press is not None and p._press[0] == "z0",
      f"100% 透明时卡片中心仍收到按下：{p._press and p._press[0]}")
p._on_drag(_Ev(_zcx + 20, _zcy + 20))
check(p._drag == "z0", f"100% 透明时卡片能拖起来：drag={p._drag}")
p._on_release(_Ev(_zcx + 20, _zcy + 20))
p.root.update()

# 卡片之间的缝 / 头部空白：按下去应该判成「拖窗口」
# 上面那次拖动已经改写了顺序，卡片矩形要重新取
_zr = p._card_rects["z0"]
_zcy = int((_zr[1] + _zr[3]) / 2 * p.S)
_row = sorted([r for r in p._card_rects.values() if abs(r[1] - _zr[1]) < 1],
              key=lambda r: r[0])
_gapx = int(((_row[0][2] + _row[1][0]) / 2 if len(_row) > 1 else _zr[2] + 8)
            * p.S)
p._on_press(_Ev(_gapx, _zcy))
check(p._drag_window is not None, "100% 透明时卡片缝里能拖窗口")
p._on_release(_Ev(_gapx, _zcy))
p._on_press(_Ev(int(12 * p.S), int(24 * p.S)))
check(p._drag_window is not None, "100% 透明时头部空白能拖窗口")
p._drag_window = None
p._on_release(_Ev(int(12 * p.S), int(24 * p.S)))

# 卡片区没有 item 命中时也能拖（走 _card_rects，不依赖 item）
config.set_opacity(100, 100)
p.render()
p.root.update()
_hit0 = p._card_rects["z0"]
_inside = p.cv.find_overlapping(int((_hit0[0] + _hit0[2]) / 2 * p.S),
                                int((_hit0[1] + _hit0[3]) / 2 * p.S),
                                int((_hit0[0] + _hit0[2]) / 2 * p.S),
                                int((_hit0[1] + _hit0[3]) / 2 * p.S))
p._on_press(_Ev(int((_hit0[0] + _hit0[2]) / 2 * p.S),
                int((_hit0[1] + _hit0[3]) / 2 * p.S)))
check(p._press is not None,
      f"卡中心命中的 item 数 {len(_inside)}，仍能按下（不靠 item 命中）")
p._press = None

# app=0 / card=0：完全不进 PIL 合成
_pil_calls = {"n": 0}
_orig_card_layer = customize.card_layer


def _counted_card_layer(*a, **k):
    _pil_calls["n"] += 1
    return _orig_card_layer(*a, **k)


customize.card_layer = _counted_card_layer
try:
    config.set_opacity(0, 0)
    p._img_cache.clear()
    p.render()
    p.root.update()
    check(_pil_calls["n"] == 0,
          f"app=0/card=0 时卡片不走合成（customize.card_layer {_pil_calls['n']} 次）")
    check(not any(p.cv.type(i) == "image"
                  for i in p.cv.find_withtag("cardbg:z0")),
          "app=0/card=0 时卡片仍是纯色多边形")
finally:
    customize.card_layer = _orig_card_layer
    config.set_opacity(0, 0)


# =====================================================================
# 第 12 项：三 Bug 修复 + 裁剪自适应（这些断言在修复前必须失败）
# =====================================================================
config.set_opacity(100, 100)
config.set_card_order([])
app._readings = {"qA": Reading(ok=True, level=80, name="设备A",
                               vid=0x1201, pid=0x3401, usage_page=0xFF00,
                               hid_known=True)}
p._scroll = 0.0
p._apply_metrics(760, 560, render=True)
p.render()
p.root.update()
p._close_menu()

# --- Bug 2：真实鼠标点「设置」（item 绑定先于 widget 绑定）---------------
_bbm = p.cv.bbox(p._btn_more.tag)
_bx, _by = int((_bbm[0] + _bbm[2]) / 2), int((_bbm[1] + _bbm[3]) / 2)
p.cv.event_generate("<Motion>", x=_bx, y=_by)
p.root.update()
p.cv.event_generate("<Button-1>", x=_bx, y=_by)
p.root.update()
check(p._menu is not None,
      "真实鼠标点「设置」能弹出菜单（改前被 _on_press 关掉）")
p._close_menu()
p.root.update()

# --- Bug 1：已有偏移 + 释放点为准 + 拖动期间冻结 render ------------------
config.set_name_pos("qA", 30, -5)
p.render()
p.root.update()
_qit = p.cv.find_withtag("cardname:qA")
if _qit:
    _qbb = p.cv.bbox(_qit[0])
    _qcx, _qcy = (_qbb[0] + _qbb[2]) / 2, (_qbb[1] + _qbb[3]) / 2
    p._on_press(_Ev(_qcx, _qcy))
    p._on_drag(_Ev(_qcx + 10, _qcy))       # 最后一次 motion 只到 +10
    _qbb2 = p.cv.bbox(_qit[0])
    _qmx = (_qbb2[0] + _qbb2[2]) / 2
    p.render()                              # 模拟刷新：文字不能被打回旧位置
    p.root.update()
    _qbb3 = p.cv.bbox("cardname:qA")       # render 会重建 item，不能复用旧 id
    _qcx3 = (_qbb3[0] + _qbb3[2]) / 2 if _qbb3 else -9999.0
    check(_qbb3 is not None and abs(_qcx3 - _qmx) <= 2,
          f"昵称拖动期间外部 render 被冻结：{_qcx3:.0f} vs {_qmx:.0f}")
    p._on_release(_Ev(_qcx + 40, _qcy))    # 手快：松手在 +40
    p.root.update()
    _qdx, _qdy = config.get_name_pos("qA")
    check(abs(_qdx - 70) <= 2 and abs(_qdy + 5) <= 2,
          f"已有 (30,-5) 再拖 40px 落盘 (70,-5)：{(_qdx, _qdy)}")
    _qbb4 = p.cv.bbox("cardname:qA")
    _qcx4 = (_qbb4[0] + _qbb4[2]) / 2 if _qbb4 else -9999.0
    check(_qbb4 is not None and abs(_qcx4 - (_qcx + 40)) <= 2,
          f"渲染后文字中心停在释放点：{_qcx4:.0f} vs {_qcx+40:.0f}")
else:
    check(False, "昵称拖动期间外部 render 被冻结（item 缺失）")
    check(False, "已有偏移再拖 40px 落盘 (70,-5)（item 缺失）")
    check(False, "渲染后文字中心停在释放点（item 缺失）")
config.clear_name_pos("qA")

# --- Bug 3 / R4：区内用户顺序优先于在线/已添加 --------------------------
config.set_card_order(["s1", "s2"])
_sres = {
    "s1": Reading(ok=True, level=80, name="S1", present=False),
    "s2": Reading(ok=True, level=70, name="S2", present=True),
}
_sgot = list(devices.order_readings(_sres, saved=[]))
check(_sgot == ["s1", "s2"], f"同区用户顺序优先于在线状态：{_sgot}")

# --- 红线 R1：不可读卡不能靠 card_order 顶到前面（守护断言）--------------
_sres["s0"] = Reading(ok=False, level=None, name="S0", present=False,
                      status="unplugged")
config.set_card_order(["s0", "s1", "s2"])
check(list(devices.order_readings(_sres, saved=[]))[0] != "s0",
      "红线 R1：不可读卡不能靠 card_order 置顶")
config.set_card_order([])

# --- Bug 3：快拖按释放点重算，不被上一条 motion 误判跨区 ----------------
app._readings = {
    "tA": Reading(ok=True, level=80, name="TA", vid=0x1211, pid=0x3411,
                  usage_page=0xFF00, hid_known=True),
    "tB": Reading(ok=True, level=70, name="TB", vid=0x1212, pid=0x3412,
                  usage_page=0xFF00, hid_known=True),
    "uA": Reading(ok=False, level=None, name="UA", present=False,
                  status="unplugged"),
    "uB": Reading(ok=False, level=None, name="UB", present=False,
                  status="unplugged"),
}
app.reorder_now()
p._scroll = 0.0
p.render()
p.root.update()
_zt, _zb = list(p._zones["top"]), list(p._zones["bottom"])
if len(_zt) >= 2 and len(_zb) >= 1:
    _r0, _r1, _rz = (p._card_rects[_zt[0]], p._card_rects[_zt[1]],
                     p._card_rects[_zb[0]])
    _sx, _sy = (_r0[0] + 60) * p.S, (_r0[1] + 120) * p.S
    _zx, _zy = (_rz[0] + 60) * p.S, (_rz[1] + 120) * p.S
    _tx, _ty = (_r1[0] + 60) * p.S, (_r1[1] + 120) * p.S
    p._on_press(_Ev(_sx, _sy))
    p._on_drag(_Ev(_sx + 20, _sy))
    p._on_drag(_Ev(_zx, _zy))            # 扫过另一区：旧代码会记成跨区
    p._on_release(_Ev(_tx, _ty))         # 松手仍在本区
    p.root.update()
    _after = list(app.snapshot())[:2]
    check(_after == [_zt[1], _zt[0]],
          f"快拖按释放点落位，不被上一条 motion 误判跨区：{_after}")
else:
    check(False, f"快拖按释放点落位（分区数据不足 top={_zt} bottom={_zb}）")
config.set_card_order([])
app.reorder_now()

# --- 新功能：裁剪选区比例对齐 + 不越界 ----------------------------------
from ez24g.ui import crop as _crop3
_fit = getattr(_crop3, "_fit_to_ratio", None)
check(_fit is not None, "crop 提供 _fit_to_ratio（改前不存在）")
for _box0 in ((10, 10, 500, 120), (0, 0, 100, 600), (0, 0, 800, 600)):
    if _fit is None:
        check(False, f"选区比例对齐目标：{_box0}（函数缺失）")
        check(False, f"选区不越界：{_box0}（函数缺失）")
        continue
    _fb = _fit(_box0, (800, 600), (348, 272))
    _fw, _fh = _fb[2] - _fb[0], _fb[3] - _fb[1]
    check(_fh > 0 and abs(_fw / _fh - 348 / 272) < 0.01,
          f"选区比例对齐目标：{_box0} → {_fb}")
    check(_fb[0] >= 0 and _fb[1] >= 0 and _fb[2] <= 800 and _fb[3] <= 600,
          f"选区不越界：{_box0} → {_fb}")

# --- 新功能：渲染层兜底不拉伸 --------------------------------------------
_ci = getattr(customize, "cover_image", None)
check(_ci is not None, "customize 提供 cover_image（改前不存在）")
if _ci is not None and customize.available():
    from PIL import Image as _PImg3
    _out = _ci(_PImg3.new("RGB", (400, 200), (10, 20, 30)), 348, 272)
    check(_out is not None and _out.size == (348, 272),
          f"cover_image 输出正好是目标尺寸：{_out and _out.size}")
else:
    check(False, "cover_image 输出正好是目标尺寸（函数缺失）")

# --- 新功能：裁剪目标必须是物理像素（150% DPI 下与逻辑像素不同）----------
_cap = {}
_old_s, _old_lw, _old_lh, _old_crop = p.S, p.LW, p.LH, p._open_crop
try:
    import unittest.mock as _um
    from tkinter import filedialog as _fd3
    if customize.available():
        from PIL import Image as _PImg4
        _fake = _PImg4.new("RGB", (8, 8), (1, 2, 3))
    else:
        _fake = object()
    p.S, p.LW, p.LH = 1.5, p.W / 1.5, p.H / 1.5
    with _um.patch.object(customize, "available", return_value=True), \
         _um.patch.object(p, "_open_image", return_value=_fake), \
         _um.patch.object(_fd3, "askopenfilename", return_value="fake.png"):
        p._open_crop = lambda img, target, cb: _cap.setdefault("card", target)
        p._pick_card_bg("qA")
        p._open_crop = lambda img, target, cb: _cap.setdefault("app", target)
        p._pick_app_bg()
finally:
    p.S, p.LW, p.LH, p._open_crop = _old_s, _old_lw, _old_lh, _old_crop
_exp_card = (max(64, int(round(p._card_w * 1.5))),
             max(64, int(round(p.theme.card_h * 1.5))))
check(_cap.get("card") == _exp_card,
      f"卡片裁剪目标用物理像素：{_cap.get('card')} vs {_exp_card}")
check(_cap.get("app") == (max(64, p.W), max(64, p.H)),
      f"软件裁剪目标用物理像素：{_cap.get('app')} vs {(p.W, p.H)}")


# =====================================================================
# 第 13 项：裁剪完整呈现 + 编号自定义（蓝牙 / 2.4G）+ 昵称编号吸附联动
# =====================================================================
# --- 13.1 裁剪输出必须完整包住用户选区（收窄会只剩中间一条）------------
for _box13 in ((100, 100, 700, 900), (10, 10, 500, 120), (0, 0, 100, 600)):
    _b13 = _crop3._fit_to_ratio(_box13, (2000, 2000), (462, 320))
    _w13, _h13 = _b13[2] - _b13[0], _b13[3] - _b13[1]
    check(_b13[0] <= _box13[0] and _b13[1] <= _box13[1]
          and _b13[2] >= _box13[2] and _b13[3] >= _box13[3]
          and _b13[0] >= 0 and _b13[1] >= 0
          and _b13[2] <= 2000 and _b13[3] <= 2000,
          f"裁剪输出完整包住选区且不越界：{_box13} → {_b13}")
    check(_h13 > 0 and abs(_w13 / _h13 - 462 / 320) < 0.02,
          f"裁剪输出比例对齐卡片：{_box13} → {_b13}")

# 端到端像素验证：四色象限图 → 裁剪 → 保存 → 卡片渲染，四角颜色都在
if customize.available():
    from PIL import Image as _I13, ImageDraw as _D13
    _src13 = _I13.new("RGB", (2000, 2000), (255, 255, 255))
    _dr13 = _D13.Draw(_src13)
    _dr13.rectangle((0, 0, 999, 999), fill=(255, 0, 0))
    _dr13.rectangle((1000, 0, 1999, 999), fill=(0, 255, 0))
    _dr13.rectangle((0, 1000, 999, 1999), fill=(0, 0, 255))
    _dr13.rectangle((1000, 1000, 1999, 1999), fill=(255, 255, 0))
    _fb13 = _crop3._fit_to_ratio((500, 500, 1500, 1500),
                                 _src13.size, (462, 320))
    _out13 = _src13.crop(_fb13).resize((462, 320), customize.Image.LANCZOS)
    _want13 = ((5, 5, (255, 0, 0)), (456, 5, (0, 255, 0)),
               (5, 314, (0, 0, 255)), (456, 314, (255, 255, 0)))
    check(_out13.size == (462, 320)
          and all(_out13.getpixel((x, y)) == c for x, y, c in _want13),
          f"裁剪后四色象限完整保留（输出框 {_fb13}）")
    _fn13 = customize.import_image_pil(_out13, "card_verify13.png")
    customize.clear_cache()
    _ren13 = customize.cover(_fn13, 462, 320)
    check(_ren13 is not None and _ren13.size == (462, 320)
          and all(_ren13.getpixel((x, y)) == _out13.getpixel((x, y))
                  for x, y in ((5, 5), (456, 5), (5, 100), (456, 100))),
          "卡片背景渲染与裁剪输出逐像素一致，无二次放大裁切")
    _base13 = customize.solid(462, 320, (255, 255, 255))
    _lay13 = customize.card_layer(_fn13, 462, 320, 12, 1.0,
                                  _base13, (255, 255, 255))
    check(_lay13 is not None
          and all(tuple(_lay13.getpixel((x, y))[:3]) == _out13.getpixel((x, y))
                  for x, y in ((5, 5), (456, 5), (5, 100), (456, 100))),
          "卡片背景层完整呈现裁剪结果（上半部不受底部渐变影响）")
else:
    check(False, "裁剪像素验证（Pillow 不可用）")
    check(False, "卡片背景渲染验证（Pillow 不可用）")
    check(False, "卡片背景层验证（Pillow 不可用）")

# --- 13.2 蓝牙编号 / 2.4G 编号：更名、拖动、联动 ------------------------
_K13 = {
    "bt:AA:BB:CC:DD:EE:FF": Reading(
        ok=True, level=80, name="蓝牙耳机", transport="bt",
        addr="aa:bb:cc:dd:ee:ff", present=True),
    "x39A53D02": Reading(
        ok=True, level=70, name="2.4G鼠标", transport="hid",
        vid=0x39A5, pid=0x3D02, usage_page=0xFF00, hid_known=True,
        present=True),
}
app._readings = dict(_K13)
config.set_card_order([])
p._scroll = 0.0
p._apply_metrics(760, 560, render=True)
p.root.update()


def _centers13(k):
    nb = p.cv.bbox(f"cardname:{k}")
    ib = p.cv.bbox(f"cardid:{k}")
    return (((nb[0] + nb[2]) / 2, (nb[1] + nb[3]) / 2),
            ((ib[0] + ib[2]) / 2, (ib[1] + ib[3]) / 2))


for _k13, _tag13 in (("bt:AA:BB:CC:DD:EE:FF", "AA:BB:CC:DD:EE:FF"),
                     ("x39A53D02", "39A5:3D02")):
    config.clear_name_pos(_k13)
    config.clear_id_pos(_k13)
    config.clear_custom_id(_k13)
    p.render()
    p.root.update()
    _it13 = p.cv.find_withtag(f"cardid:{_k13}")
    check(bool(_it13) and _tag13 in p.cv.itemcget(_it13[0], "text"),
          f"卡片编号带 cardid 标签且显示 {_tag13}")

    # 单击编号 → 编号改名窗；写盘后渲染文本立即更新；留空恢复默认
    p._open_rename_id(_k13)
    p.root.update()
    check(isinstance(p._dlg, _dlg10.RenameIdDialog),
          f"单击编号弹出「修改编号」窗（{_tag13}）")
    p._dlg.entry.delete(0, "end")
    p._dlg.entry.insert(0, "我的编号")
    p._dlg._ok()
    p.root.update()
    check(config.custom_ids().get(_k13) == "我的编号"
          and p.cv.itemcget(p.cv.find_withtag(f"cardid:{_k13}")[0], "text")
          == "我的编号",
          f"编号更名写盘并立即显示（{_tag13}）")
    p._open_rename_id(_k13)
    p.root.update()
    p._dlg.entry.delete(0, "end")
    p._dlg._ok()
    p.root.update()
    check(config.custom_ids().get(_k13) is None
          and _tag13 in p.cv.itemcget(
              p.cv.find_withtag(f"cardid:{_k13}")[0], "text"),
          f"留空恢复默认编号（{_tag13}）")

    # 拖编号：落点跟手 + 昵称吸附联动 + 两行位置一起落盘
    (n0, i0) = _centers13(_k13)
    _rel0 = (i0[0] - n0[0], i0[1] - n0[1])
    p._on_press(_Ev(i0[0], i0[1]))
    p._on_drag(_Ev(i0[0] + 30, i0[1]))
    p._on_release(_Ev(i0[0] + 30, i0[1]))
    p.root.update()
    (n1, i1) = _centers13(_k13)
    check(abs(i1[0] - (i0[0] + 30)) <= 2,
          f"编号拖动落点跟手（{_tag13}）：{i1[0]:.0f} vs {i0[0]+30:.0f}")
    check(abs((i1[0] - n1[0]) - _rel0[0]) <= 2
          and abs((i1[1] - n1[1]) - _rel0[1]) <= 2,
          f"拖编号时昵称同步跟随、相对位置不变（{_tag13}）")
    check(config.get_id_pos(_k13) != (0.0, 0.0)
          and config.get_name_pos(_k13) != (0.0, 0.0),
          f"昵称与编号位置一起落盘（{_tag13}）")

    # 拖昵称：编号同样同步跟随
    (n2, i2) = _centers13(_k13)
    p._on_press(_Ev(n2[0], n2[1]))
    p._on_drag(_Ev(n2[0] - 20, n2[1]))
    p._on_release(_Ev(n2[0] - 20, n2[1]))
    p.root.update()
    (n3, i3) = _centers13(_k13)
    check(abs((i3[0] - n3[0]) - (i2[0] - n2[0])) <= 2
          and abs((i3[1] - n3[1]) - (i2[1] - n2[1])) <= 2,
          f"拖昵称时编号同步跟随（{_tag13}）")

    # 守护：单击昵称仍走原来的昵称改名窗
    p._on_press(_Ev(n3[0], n3[1]))
    p._on_release(_Ev(n3[0], n3[1]))
    p.root.update()
    check(type(p._dlg) is _dlg10.RenameDialog,
          f"单击昵称仍打开原昵称改名窗（{_tag13}）")
    if p._dlg is not None:
        p._dlg.close()
    config.clear_name_pos(_k13)
    config.clear_id_pos(_k13)
    config.clear_custom_id(_k13)
p.render()
p.root.update()


# =====================================================================
# 第 14 项：背景层尺寸自适应 + 恢复默认位置 + 两种编号场景回归
# =====================================================================
# --- 14.1 卡片尺寸变化后，裁剪结果仍完整呈现（不再被 cover 二次裁切）----
if customize.available():
    from PIL import Image as _I14, ImageDraw as _D14
    _s14 = _I14.new("RGB", (900, 680), (255, 255, 255))
    _dr14 = _D14.Draw(_s14)
    _dr14.rectangle((0, 0, 449, 339), fill=(255, 0, 0))
    _dr14.rectangle((450, 0, 899, 339), fill=(0, 255, 0))
    _dr14.rectangle((0, 340, 449, 679), fill=(0, 0, 255))
    _dr14.rectangle((450, 340, 899, 679), fill=(255, 255, 0))
    _fn14 = customize.import_image_pil(_s14, "bg14.png")
    customize.clear_cache()
    for (_w14, _h14) in ((450, 340), (348, 272), (600, 454)):
        _fill14 = (17, 23, 31)
        _im14 = customize.card_bg(_fn14, _w14, _h14, _fill14)
        _k14s = min(_w14 / 900.0, _h14 / 680.0)
        _nw14 = max(1, min(_w14, int(round(900 * _k14s))))
        _nh14 = max(1, min(_h14, int(round(680 * _k14s))))
        _ox14, _oy14 = (_w14 - _nw14) // 2, (_h14 - _nh14) // 2
        _cs14 = ((int(_nw14 * .05), int(_nh14 * .05), (255, 0, 0)),
                 (_nw14 - int(_nw14 * .05) - 1, int(_nh14 * .05), (0, 255, 0)),
                 (int(_nw14 * .05), _nh14 - int(_nh14 * .05) - 1, (0, 0, 255)),
                 (_nw14 - int(_nw14 * .05) - 1,
                  _nh14 - int(_nh14 * .05) - 1, (255, 255, 0)))
        _ok14 = (_im14 is not None and _im14.size == (_w14, _h14)
                 and all(_im14.getpixel((_ox14 + x, _oy14 + y)) == c
                         for x, y, c in _cs14))
        _pad14 = _nw14 < _w14 or _nh14 < _h14
        _ok14 = _ok14 and ((not _pad14)
                           or _im14.getpixel((_w14 - 1, _h14 - 1)) == _fill14)
        check(_ok14,
              f"卡片尺寸变 {_w14}x{_h14} 后裁剪图四色等比完整、留白为纯色")
    # 比例不一致：主图等比完整居中，周围纯色留白，不叠第二张位图
    _im14b = customize.card_bg(_fn14, 500, 300)
    _left14 = (500 - 397) // 2
    check(_im14b is not None and _im14b.size == (500, 300)
          and _im14b.getpixel((_left14 + 20, 20)) == (255, 0, 0)
          and _im14b.getpixel((_left14 + 377, 280)) == (255, 255, 0)
          and _im14b.getpixel((1, 1)) == (245, 246, 248),
          "比例不一致时主图等比完整居中、周围纯色留白无第二张图")
else:
    for _i in range(4):
        check(False, "背景自适应断言（Pillow 不可用）")

# --- 14.2 设置菜单含「恢复默认位置」-----------------------------------
_mi14 = p._header_menu_items()
check(any(isinstance(it, tuple) and it and it[0] == "恢复默认位置"
          for it in _mi14),
      "设置菜单新增「恢复默认位置」操作项")

# --- 14.3 蓝牙编号 / 2.4G 编号两种场景分别验证 --------------------------
for _k14 in ("bt:AA:BB:CC:DD:EE:FF", "x39A53D02"):
    config.set_custom_id(_k14, "")
    config.set_name_pos(_k14, 40, -12)
    config.set_id_pos(_k14, -30, 8)
    p.render()
    p.root.update()

    # 该卡背景层按当前尺寸自适应（两种编号场景各验一次）
    _rect14 = p._card_rects[_k14]
    _cw14 = int(round((_rect14[2] - _rect14[0]) * p.S))
    _ch14 = int(round((_rect14[3] - _rect14[1]) * p.S))
    _bg14 = customize.card_bg(_fn14, _cw14, _ch14) if customize.available() else None
    _got14 = set()
    if _bg14 is not None:
        _got14 = {_bg14.getpixel((x, y))
                  for x in range(2, _cw14, max(1, _cw14 // 12))
                  for y in range(2, _ch14, max(1, _ch14 // 12))}
    _need14 = {(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)}
    check(_bg14 is not None and _bg14.size == (_cw14, _ch14)
          and len(_need14 & _got14) >= 3,
          f"卡片背景按 {_k14} 当前尺寸等比 contain 且四色内容完整")

    # 恢复默认位置：两个偏移一起归零，文字回到各自默认锚点
    p._reset_text_positions()
    p.root.update()
    check(config.get_name_pos(_k14) == (0.0, 0.0)
          and config.get_id_pos(_k14) == (0.0, 0.0),
          f"恢复默认位置同时清空编号与昵称偏移（{_k14}）")
    _nb14 = p.cv.bbox(f"cardname:{_k14}")
    _ib14 = p.cv.bbox(f"cardid:{_k14}")
    check(abs((_nb14[1] + _nb14[3]) / 2 - (_rect14[1] + 158) * p.S) <= 3
          and abs((_ib14[1] + _ib14[3]) / 2 - (_rect14[1] + 180) * p.S) <= 3,
          f"编号与昵称各自回到卡片内默认位置（{_k14}）")

    # 恢复后联动拖动仍正常
    (n14, i14) = _centers13(_k14)
    rel14 = (i14[0] - n14[0], i14[1] - n14[1])
    p._on_press(_Ev(i14[0], i14[1]))
    p._on_drag(_Ev(i14[0] + 25, i14[1]))
    p._on_release(_Ev(i14[0] + 25, i14[1]))
    p.root.update()
    (n14b, i14b) = _centers13(_k14)
    check(abs((i14b[0] - n14b[0]) - rel14[0]) <= 2
          and abs((i14b[1] - n14b[1]) - rel14[1]) <= 2,
          f"恢复默认位置后编号/昵称吸附联动仍正常（{_k14}）")

    # 恢复后更名仍正常
    p._open_rename_id(_k14)
    p.root.update()
    p._dlg.entry.delete(0, "end")
    p._dlg.entry.insert(0, "编号A")
    p._dlg._ok()
    p.root.update()
    check(config.custom_ids().get(_k14) == "编号A"
          and p.cv.itemcget(p.cv.find_withtag(f"cardid:{_k14}")[0], "text")
          == "编号A",
          f"恢复默认位置后编号更名仍正常（{_k14}）")
    config.clear_custom_id(_k14)
    config.clear_name_pos(_k14)
    config.clear_id_pos(_k14)
p.render()
p.root.update()


# =====================================================================
# 第 15 项：裁剪图唯一输入（无双图/分层/遮挡）+ 添加设备不遗漏已识别项
# =====================================================================
if customize.available():
    from PIL import Image as _I15, ImageDraw as _D15
    _s15 = _I15.new("RGB", (900, 680), (255, 255, 255))
    _d15 = _D15.Draw(_s15)
    _d15.rectangle((0, 0, 449, 339), fill=(255, 0, 0))
    _d15.rectangle((450, 0, 899, 339), fill=(0, 255, 0))
    _d15.rectangle((0, 340, 449, 679), fill=(0, 0, 255))
    _d15.rectangle((450, 340, 899, 679), fill=(255, 255, 0))
    _fn15 = customize.import_image_pil(_s15, "bg15.png")
    customize.clear_cache()

    # 15.1 原图小于/大于目标、比例不同：等比 contain，主图逐点等于等比
    #      缩放结果（不变形、不裁切），比例不一致时留白是纯色而非第二张图。
    _fill15 = (17, 23, 31)
    for (_w15, _h15) in ((200, 150), (450, 340), (1200, 900),
                         (500, 300), (200, 500)):
        _im15 = customize.card_bg(_fn15, _w15, _h15, _fill15)
        _k15s = min(_w15 / 900.0, _h15 / 680.0)
        _nw15 = max(1, min(_w15, int(round(900 * _k15s))))
        _nh15 = max(1, min(_h15, int(round(680 * _k15s))))
        _want15 = _s15.resize((_nw15, _nh15), customize.Image.LANCZOS)
        _ox15, _oy15 = (_w15 - _nw15) // 2, (_h15 - _nh15) // 2
        _pts15 = [(int(_nw15 * fx), int(_nh15 * fy))
                  for fx in (.05, .5, .95) for fy in (.05, .5, .95)]
        _ok_main15 = all(
            _im15.getpixel((_ox15 + q[0], _oy15 + q[1]))
            == _want15.getpixel(q) for q in _pts15)
        _pad15 = _nw15 < _w15 or _nh15 < _h15
        _ok_pad15 = (not _pad15) or _im15.getpixel((_w15 - 1, _h15 - 1)) == _fill15
        check(_im15 is not None and _im15.size == (_w15, _h15)
              and _ok_main15 and _ok_pad15,
              f"裁剪图唯一输入，{_w15}x{_h15} 等比完整且无第二张图")

    # 15.2 设了卡片图时，卡片层必须按滑块 alpha 透出「软件背景层裁片」，
    #      而不是用纯色垫层把透明度写死。每层仍只有自己一张图，无重复贴图。
    _k15 = "bt:AA:BB:CC:DD:EE:FF"
    _orig_cl15 = customize.card_layer
    _cap15 = {}

    def _spy_cl15(*a, **kw):
        if (a[0] if a else kw.get("filename")) == _fn15:
            _cap15["base"] = a[5] if len(a) > 5 else kw.get("base")
            _cap15["alpha"] = a[4] if len(a) > 4 else kw.get("alpha")
        return _orig_cl15(*a, **kw)

    config.set_card_bg(_k15, _fn15)
    config.set_app_bg(_fn15)
    config.set_opacity(100, 0)             # 卡片全透 / 软件背景不透明
    customize.card_layer = _spy_cl15
    p._img_cache.clear()
    customize.clear_cache()
    try:
        p.render()
        p.root.update()
    finally:
        customize.card_layer = _orig_cl15
    _b15 = _cap15.get("base")
    _rect15b = p._card_rects.get(_k15)
    _app15b = p._app_layer_img
    _match15 = False
    if (hasattr(_b15, "getpixel") and _rect15b is not None
            and _app15b is not None):
        _bx15 = int(round(_rect15b[0] * p.S))
        _by15 = int(round(_rect15b[1] * p.S))
        _match15 = all(
            _b15.getpixel(q) == _app15b.getpixel((_bx15 + q[0], _by15 + q[1]))
            for q in ((2, 2), (10, 10)))
    check(_match15 and _cap15.get("alpha") == 0.0,
          "卡片层 base = 软件背景层裁片，alpha 跟随滑块（100% 完全透出）")

    # 15.3 窗口尺寸变化后重绘：卡片区域只有一张图且完整贴合卡片矩形
    for (_rw15, _rh15) in ((980, 700), (760, 560), (1100, 620)):
        p._apply_metrics(_rw15, _rh15, render=True)
        p.root.update()
        _imgs15 = [i for i in p.cv.find_withtag(f"cardbg:{_k15}")
                   if p.cv.type(i) == "image"]
        _rect15 = p._card_rects.get(_k15)
        _ok15 = len(_imgs15) == 1 and _rect15 is not None
        if _ok15:
            _bb15 = p.cv.bbox(_imgs15[0])
            _exp15 = tuple(int(round(v * p.S)) for v in _rect15)
            _ok15 = all(abs(a - b) <= 2 for a, b in zip(_bb15, _exp15))
        check(_ok15,
              f"窗口 {_rw15}x{_rh15} 重绘后卡片只有一张图且完整贴合")
    p._apply_metrics(760, 560, render=True)
    p.root.update()

    # 15.4 桌面快照只在「软件背景需要透出」时抓一次，之后全程复用。
    #      拖动滑块连续重绘不重抓 → 不会隐藏窗口一帧，也就不会闪烁。
    _orig_hidden15 = p._grab_hidden
    _cnt15 = {"n": 0}

    def _spy_hidden15():
        _cnt15["n"] += 1
        return _orig_hidden15()

    p._grab_hidden = _spy_hidden15
    try:
        config.set_opacity(0, 0)
        p._img_cache.clear()
        customize.clear_cache()
        p.render()
        p.root.update()
        _n150 = _cnt15["n"]
        check(_n150 == 0,
              "透明度 0（完全不透明）时不引入桌面快照，无第二张图")

        config.set_opacity(0, 50)
        p._img_cache.clear()
        customize.clear_cache()
        p.render()
        p.root.update()
        check(_cnt15["n"] - _n150 <= 1,
              f"需要透出桌面时最多抓一次快照：{_cnt15['n'] - _n150}")

        _before15 = _cnt15["n"]
        for _v15 in (55, 60, 70, 85, 100):
            config.set_opacity(0, _v15)
            p.render()
            p.root.update()
        check(_cnt15["n"] == _before15,
              "连续拖动透明度全程复用同一张桌面快照，不重抓、不闪烁")
    finally:
        p._grab_hidden = _orig_hidden15

    config.set_app_bg("")
    config.clear_card_bg(_k15)
    customize.clear_cache()
    p._img_cache.clear()
    p.render()
    p.root.update()
else:
    for _i15 in range(10):
        check(False, "背景唯一输入断言（Pillow 不可用）")

# 15.5 添加设备：完整列出所有已识别的蓝牙 / 2.4G 设备，不遗漏已添加项
from ez24g import providers as _pv15
from ez24g.providers.base import DeviceRef as _DR15

_orig_enum15 = _pv15.enumerate_refs
_orig_snap15 = app.snapshot
_orig_load15 = config.load_saved_devices
_fake15 = [
    _DR15(key="bt:DE:1B:B4:46:93:66", kind="bt", transport="bt",
          name="G517KL", addr="DE:1B:B4:46:93:66", connected=True),
    _DR15(key="x046DB390", kind="hid", transport="hid", name="USB Receiver",
          vid=0x046D, pid=0xB390, connected=True),
]
_pv15.enumerate_refs = lambda *a, **k: list(_fake15)
app.snapshot = lambda: {
    "bt:DE:1B:B4:46:93:66": Reading(
        ok=True, level=43, name="G517KL", transport="bt",
        addr="DE:1B:B4:46:93:66"),
    "x1234ABCD": Reading(ok=True, level=80, name="L8 Air", transport="hid",
                         vid=0x1234, pid=0xABCD),
}
config.load_saved_devices = lambda: [
    {"kind": "hid", "vid": 0x1234, "pid": 0xABCD, "name": "L8 Air"}]
try:
    # 枚举改到工作线程：先证明这次调用立即返回，再等菜单被 root.after 送上主线程
    def _slow_enum15(*a, **k):
        time.sleep(0.4)                    # 模拟 BLE 枚举阻塞（真机最长 4s）
        return list(_fake15)

    _pv15.enumerate_refs = _slow_enum15
    _t15 = time.time()
    p._add_device()
    _cost15 = time.time() - _t15
    check(_cost15 < 0.1,
          f"添加设备不阻塞界面（调用返回 {_cost15*1000:.0f} ms < 100 ms）")
    for _wait15 in range(300):             # 最多等 3 秒让工作线程回主线程
        p.root.update()
        if p._menu is not None:
            break
        time.sleep(0.01)
    check(p._menu is not None, "枚举完成后菜单确实弹出来了")
    _labels15 = [it[0] for it in (p._menu.items if p._menu else []) if it]
    check(any("G517KL" in s for s in _labels15),
          f"蓝牙 G517KL 出现在添加设备菜单：{_labels15}")
    check(any("USB Receiver" in s for s in _labels15),
          "枚举到的 2.4G 设备不遗漏")
    check(any("L8 Air" in s and "已添加" in s for s in _labels15),
          "已添加设备也完整列出并标注")
    p._close_menu()
finally:
    _pv15.enumerate_refs = _orig_enum15
    app.snapshot = _orig_snap15
    config.load_saved_devices = _orig_load15

# =====================================================================
# 第 16 项：透明度 0–100 真正生效（走 panel 实渲染，不绕过）+ 毛玻璃 / 图标
# =====================================================================
from ez24g.ui import backdrop as _bd16, cards as _cards16
from ez24g.ui import dialogs as _dlg16, draw as _draw16, widgets as _wg16
import tkinter as _tk16

# 三张可区分的纯色源：卡片红 / 软件背景蓝 / 桌面四宫格
_red16 = _cz.Image.new("RGB", (320, 220), (220, 20, 20))
_blue16 = _cz.Image.new("RGB", (p.W, p.H), (20, 20, 220))
_desk16 = _cz.Image.new("RGB", (1600, 1000), (10, 200, 10))
_dr16 = _cz.ImageDraw.Draw(_desk16)
_dr16.rectangle((0, 0, 800, 500), fill=(10, 200, 10))
_dr16.rectangle((800, 0, 1600, 500), fill=(200, 200, 10))
_dr16.rectangle((0, 500, 800, 1000), fill=(10, 10, 200))
_dr16.rectangle((800, 500, 1600, 1000), fill=(200, 10, 200))
_fn16c = customize.import_image_pil(_red16, "op16_card.png")
_fn16a = customize.import_image_pil(_blue16, "op16_app.png")

app._readings = {"op16": Reading(ok=True, level=88, name="OP16",
                                 transport="hid", vid=0x1234, pid=0x5678)}
config.set_card_order([])
_k16 = "op16"
config.set_card_bg(_k16, _fn16c)
config.set_app_bg(_fn16a)

_orig_grab16 = _bd16.grab


def _fake_grab16(x, y, w, h):
    # 保证 extrema 有变化（backdrop.usable 会把纯色判为「没抓到」）
    _im16 = _desk16.crop((0, 0, w, h)).copy()
    _im16.putpixel((0, 0), (0, 0, 0))
    _im16.putpixel((min(w - 1, 10), min(h - 1, 10)), (255, 255, 255))
    return _im16


_bd16.grab = _fake_grab16
# 前面的用例可能已缓存过真实桌面快照；隔离掉，避免 mock 不生效
p._backdrop_key = None
p._backdrop_img = None
p._backdrop_epoch += 1


def _render16(card, appv):
    config.set_opacity(card, appv)
    p._img_cache.clear()
    customize.clear_cache()
    p.render()
    p.root.update()
    _r16 = p._card_rects.get(_k16)
    _w16 = int(round((_r16[2] - _r16[0]) * p.S))
    _h16 = int(round((_r16[3] - _r16[1]) * p.S))
    _bx16, _by16 = int(round(_r16[0] * p.S)), int(round(_r16[1] * p.S))
    _l16 = p._app_layer_img
    _base16 = _l16.crop((_bx16, _by16, _bx16 + _w16, _by16 + _h16))
    _lay16 = _cz.card_layer(_fn16c, _w16, _h16,
                            int(p.theme.card_radius * p.S),
                            1 - card / 100.0, _base16, p.theme.pal.card)
    return (_l16.getpixel((5, 5)),
            _lay16.getpixel((_w16 // 2, _h16 // 2))[:3],
            _base16.getpixel((_w16 // 2, _h16 // 2)))


try:
    _a16 = _render16(0, 0)
    _b16 = _render16(0, 100)
    _c16 = _render16(100, 0)
    _d16 = _render16(100, 100)
    _m16 = _render16(50, 50)

    check(_a16[0] == (20, 20, 220),
          f"0/0 软件背景完全不透明显示自定义图：{_a16[0]}")
    check(_a16[1] == (220, 20, 20),
          f"0/0 卡片完全不透明显示裁剪图：{_a16[1]}")
    check(_b16[0] == _desk16.getpixel((5, 5)),
          f"软件背景 100 直接显示桌面而非纯色/自定义图：{_b16[0]}")
    check(_b16[0] != (20, 20, 220),
          "软件背景 100 时自定义背景图已完全让出")
    check(_c16[1] == _c16[2] == (20, 20, 220),
          f"卡片 100 完全透出软件背景：{_c16[1]}")
    check(_d16[1] == _d16[2] == _desk16.getpixel((5, 5)),
          f"卡片与软件背景都 100 时两级串联透出桌面：{_d16[1]}")
    check(_m16[0] != (20, 20, 220) and _m16[0] != _desk16.getpixel((5, 5)),
          "中间值 50 连续过渡，不是 0/100 二值跳变")
    check(_m16[1] != (220, 20, 20) and _m16[1] != _m16[2],
          "卡片 50 连续过渡（介于裁剪图与软件背景之间）")
    check(_a16[0] != _b16[0] and _a16[1] != _c16[1],
          "两档滑块各自独立生效，互不覆盖")
finally:
    _bd16.grab = _orig_grab16

_hm16 = p._header_menu_items()
check(any(isinstance(it, tuple) and it and "透明度" in str(it[0])
          for it in _hm16),
      "设置菜单里「透明度…」入口存在，随时可调两档滑块")

# 16.2 数值与滑块位置一致、边界 0 / 100 正确、重置回 0
_dlg16 = _dlg16.OpacityDialog(p)
p._dlg = _dlg16
try:
    _S16 = _dlg16.S
    _sc16 = _dlg16.s_card
    _sa16 = _dlg16.s_app
    # 走真实事件链（set_from 本身不重画，_press 才会 _redraw_sliders）
    _dlg16._press(_Ev((130 + 200) * _S16, 70 * _S16))
    _dlg16._release(_Ev((130 + 200) * _S16, 70 * _S16))
    check(_sc16.value == 100 and config.get_opacity()["card"] == 100,
          f"卡片滑块拖到最右写入 100：{_sc16.value}")
    _ov16 = [i for i in _dlg16.cv.find_withtag(_sc16.tag)
             if _dlg16.cv.type(i) == "oval"]
    _bb16 = _dlg16.cv.bbox(_ov16[0]) if _ov16 else None
    check(_bb16 and abs((_bb16[0] + _bb16[2]) / 2 - 330 * _S16) <= 2,
          f"圆点位置与 100 一致：{_bb16}")
    _txt16 = [_dlg16.cv.itemcget(i, "text")
              for i in _dlg16.cv.find_withtag(_sc16.tag)
              if _dlg16.cv.type(i) == "text"]
    check("100%" in _txt16, f"数值文本与滑块一致：{_txt16}")
    _dlg16._press(_Ev(130 * _S16, 120 * _S16))
    _dlg16._release(_Ev(130 * _S16, 120 * _S16))
    check(_sa16.value == 0 and config.get_opacity()["app"] == 0,
          f"软件背景滑块拖到最左写入 0：{_sa16.value}")
    _dlg16._reset()
    check(config.get_opacity()["card"] == 0
          and config.get_opacity()["app"] == 0,
          "重置为不透明：两档都回 0")
    check(_dlg16.s_card.value == 0 and _dlg16.s_app.value == 0,
          "重置后滑块圆点与数值同步回 0，无错位")
    _ep16 = p._backdrop_epoch
    p._tick_backdrop()
    check(p._backdrop_epoch == _ep16,
          "设置弹窗打开期间定时刷新不重抓桌面快照（不闪烁）")
finally:
    p._dlg = None
    _dlg16.close()
    try:
        p._backdrop_epoch += 0
    except Exception:
        pass

# 16.3 毛玻璃按钮：多层装饰 + 高光同 tag（不吃点击）+ 交互仍可用
_w16 = _tk16.Toplevel(p.root)
_c16 = _tk16.Canvas(_w16, width=300, height=120, highlightthickness=0)
_c16.pack()
_w16.update()
_hit16 = {"n": 0}
_btn16 = _wg16.Button(_c16, p.theme, 1.0, 20, 20, 100, 30, "毛玻璃",
                      lambda: _hit16.__setitem__("n", _hit16["n"] + 1),
                      kind="primary").draw()
_items16 = list(_c16.find_withtag(_btn16.tag))
check(len(_items16) >= 4,
      f"毛玻璃按钮含阴影/主体/顶部高光/底部内阴影/文字：{len(_items16)} 层")
check(_btn16.poly in _items16 and _c16.type(_btn16.poly) == "polygon",
      "主体仍是原圆角矩形，hover 改色逻辑不变")
_c16.event_generate("<Button-1>", x=70, y=35)
_w16.update()
check(_hit16["n"] == 1,
      "毛玻璃装饰层不影响按钮点击（高光挂同一 tag）")
check(len(_draw16.round_rect_points(0, 0, 100, 40, 8)) >= 60,
      "界面圆角采样加密，边缘更平滑")
_cards16._draw_icon(_c16, p.theme, 1.0, 200, 60, "keyboard")
check(not [i for i in _c16.find_all() if _c16.type(i) == "rectangle"],
      "设备矢量图标已圆角化，无直角矩形")
_w16.destroy()

config.clear_card_bg(_k16)
config.set_app_bg("")
config.set_opacity(0, 0)
customize.clear_cache()
p._img_cache.clear()
p.render()
p.root.update()

# =====================================================================
# 第 17 项：按确认项验证 —— contain 适配 / 快照质量 / 裁片钳制 + 按帧淘汰
# =====================================================================
from ez24g.ui import backdrop as _bd17
import sys as _sys17

# 17.1 裁剪：比例一致时无留白且逐点等于等比缩放；比例不一致时完整不变形
_q17 = _cz.Image.new("RGB", (400, 300), (255, 255, 255))
_qd17 = _cz.ImageDraw.Draw(_q17)
_qd17.rectangle((0, 0, 199, 149), fill=(255, 0, 0))
_qd17.rectangle((200, 0, 399, 149), fill=(0, 255, 0))
_qd17.rectangle((0, 150, 199, 299), fill=(0, 0, 255))
_qd17.rectangle((200, 150, 399, 299), fill=(255, 255, 0))
_fn17 = customize.import_image_pil(_q17, "op17.png")
customize.clear_cache()
_same17 = customize.card_bg(_fn17, 200, 150, (17, 23, 31))
_want17 = _q17.resize((200, 150), customize.Image.LANCZOS)
check(_same17.getpixel((5, 5)) == _want17.getpixel((5, 5))
      and _same17.getpixel((195, 145)) == _want17.getpixel((195, 145)),
      "比例一致时无留白，逐点等于等比重采样")
_diff17 = customize.card_bg(_fn17, 500, 300, (17, 23, 31))
# 500x300 容器、4:3 源 → 主图 400x300 居中，左右各 50 纯色留白
check(_diff17.size == (500, 300)
      and _diff17.getpixel((50 + 20, 20)) == (255, 0, 0)
      and _diff17.getpixel((50 + 380, 280)) == (255, 255, 0)
      and _diff17.getpixel((1, 1)) == (17, 23, 31)
      and _diff17.getpixel((499, 1)) == (17, 23, 31),
      "比例不一致时四色完整、主图不变形、左右留白为纯色")
check(_diff17.getpixel((50 + 100, 150)) == (0, 0, 255)
      or _diff17.getpixel((50 + 300, 150)) == (255, 255, 0),
      "主图等比居中，未被 cover 二次裁掉中心以外内容")

# 17.2 软件背景：多帧稳定判定 + 连抓多帧 + 线性映射保持
_a17 = _cz.Image.new("RGB", (200, 150), (30, 60, 90))
_b17 = _a17.copy()
_b17.putpixel((100, 75), (0, 0, 0))
check(_bd17.stable(_a17, _a17) is True
      and _bd17.stable(_a17, _b17) is False,
      "两帧桌面快照稳定判定（相同 True / 中心变化 False）")

app._readings = {f"r{i}": Reading(ok=True, level=80 - i, name=f"R{i}",
                                  transport="hid", vid=0x1200 + i,
                                  pid=0x3400 + i)
                 for i in range(10)}
config.set_card_order([])
config.set_app_bg(fn_app if "fn_app" in dir() else "")
p._scroll = 0.0
p.render()
p.root.update()

_orig_g17 = _bd17.grab
_orig_excl17 = p._capture_excl
_cnt17 = {"n": 0}


def _fake_g17(x, y, w, h):
    _cnt17["n"] += 1
    im = _cz.Image.new("RGB", (w, h), (10, 200, 10))
    im.putpixel((0, 0), (0, 0, 0))
    im.putpixel((min(w - 1, 7), min(h - 1, 7)), (255, 255, 255))
    return im


_bd17.grab = _fake_g17
try:
    config.set_opacity(0, 50)
    p._img_cache.clear()
    customize.clear_cache()
    # (a) 抓屏排除可用：可见状态下直接抓一帧，绝不隐藏窗口、不做多帧稳定判定
    #     （只数 render() 内部的抓取；root.update() 里的逐帧同步会再抓一帧，
    #      那是 _backdrop_sync 的职责，另有断言）
    _cnt17["n"] = 0
    p._capture_excl = True
    p._backdrop_key = None
    p._backdrop_epoch += 1
    p.render()
    _n17a = _cnt17["n"]
    p.root.update()
    check(_n17a == 1,
          f"抓屏排除路径单帧抓取，不隐藏窗口（render 内 {_n17a} 帧）")

    # (b) 旧系统没有抓屏排除：退回「隐藏窗口 + 多帧取稳定帧」
    _cnt17["n"] = 0
    p._capture_excl = False
    p._backdrop_key = None
    p._backdrop_epoch += 1
    p.render()
    _n17b = _cnt17["n"]
    p.root.update()
    check(_n17b >= 2,
          f"无抓屏排除时仍连抓多帧取稳定帧（render 内 {_n17b} 帧）")
finally:
    _bd17.grab = _orig_g17
    p._capture_excl = bool(_orig_excl17)
_lin17 = []
for _v17 in (0, 30, 36, 50, 90, 100):
    config.set_opacity(0, _v17)
    _lin17.append(abs(config.get_alpha("app") - (1 - _v17 / 100.0)) < 1e-9)
check(all(_lin17),
      "软件背景 0–100 仍是线性映射，未引入非线性曲线")
config.set_opacity(0, 50)
_src17 = ""
with open(os.path.join(ROOT, "ez24g", "ui", "panel.py"),
          encoding="utf-8") as _f17:
    _src17 = _f17.read()
check("opaque = 1.0 - op / 100.0" in _src17,
      "软件背景层合成仍用线性不透明度公式")

# 17.3 滚动：越界绝不退回纯色、卡片 100 透出 L1、本帧缓存不被回收
_app17 = _cz.Image.new("RGB", (p.W, p.H), (20, 20, 220))
_fn_app17 = customize.import_image_pil(_app17, "op17_app.png")
config.set_app_bg(_fn_app17)
config.set_opacity(100, 36)
p._backdrop_key = None
p._backdrop_img = None
p._backdrop_epoch += 1
customize.clear_cache()
p._img_cache.clear()
p.render()
p.root.update()

_orig_solid17 = customize.solid
_fb17 = {"n": 0}


def _spy_solid17(w, h, color, *a, **kw):
    try:
        if _sys17._getframe(1).f_code.co_name == "_card_layer":
            _fb17["n"] += 1
    except Exception:
        pass
    return _orig_solid17(w, h, color, *a, **kw)


customize.solid = _spy_solid17
try:
    _n17 = 30
    for _i17 in range(_n17):
        p._scroll = (p._max_scroll or 1.0) * _i17 / (_n17 - 1)
        p.render()
        p.root.update()
finally:
    customize.solid = _orig_solid17
check(_fb17["n"] == 0,
      f"滚动 {_n17} 步无一次退回纯色底图（兜底 {_fb17['n']} 次）")
check(all(k in p._img_cache for k in p._frame_new_keys),
      "本帧新建的 PhotoImage 全部仍在缓存，未被按帧清理回收")
_r17 = p._card_rects.get("r0")
if _r17 and p._app_layer_img is not None:
    _w17 = int(round((_r17[2] - _r17[0]) * p.S))
    _h17 = int(round((_r17[3] - _r17[1]) * p.S))
    _lw17, _lh17 = p._app_layer_img.size
    _bx17 = max(0, min(int(round(_r17[0] * p.S)), _lw17 - _w17))
    _by17 = max(0, min(int(round(_r17[1] * p.S)), _lh17 - _h17))
    _base17 = p._app_layer_img.crop((_bx17, _by17,
                                     _bx17 + _w17, _by17 + _h17))
    _lay17 = customize.card_layer(None, _w17, _h17, 12, 0.0,
                                  _base17, p.theme.pal.card)
    check(tuple(_lay17.getpixel((_w17 // 2, _h17 // 2))[:3])
          == _base17.getpixel((_w17 // 2, _h17 // 2)),
          "卡片 100% 透明时显示软件背景层裁片而非纯白")

config.set_opacity(0, 0)
config.set_app_bg("")
p._scroll = 0.0
customize.clear_cache()
p._img_cache.clear()
p.render()
p.root.update()

# =====================================================================
# 第 18 项：非纯色软件背景下滑动 —— 卡片背景严格对齐、无未渲染区域
# =====================================================================
_W18, _H18 = p.W, p.H
_grad18 = _cz.Image.new("RGB", (_W18, _H18))
_d18 = _cz.ImageDraw.Draw(_grad18)
for _y18 in range(_H18):
    _d18.line((0, _y18, _W18, _y18),
              fill=(int(255 * _y18 / _H18), 40,
                    200 - int(200 * _y18 / _H18)))

# 18.1 aligned_crop 纯函数：完全在内 / 上越界 / 下越界 / 完全在外
_in18 = customize.aligned_crop(_grad18, 30, 60, 100, 80)
check(_in18 is not None and _in18.size == (100, 80)
      and _in18.getpixel((0, 0)) == _grad18.getpixel((30, 60))
      and _in18.getpixel((99, 79)) == _grad18.getpixel((129, 139)),
      "裁片完全在窗口内时逐点等于真实坐标裁剪")

_up18 = customize.aligned_crop(_grad18, 30, -40, 100, 80)
check(_up18.getpixel((0, 40)) == _grad18.getpixel((30, 0))
      and _up18.getpixel((0, 0)) == _grad18.getpixel((30, 0))
      and _up18.getpixel((0, 79)) == _grad18.getpixel((30, 39)),
      "上越界：交集按真实偏移对齐，越界区用边缘行延展填满")

_dn18 = customize.aligned_crop(_grad18, 30, _H18 - 30, 100, 80)
check(_dn18.getpixel((0, 0)) == _grad18.getpixel((30, _H18 - 30))
      and _dn18.getpixel((0, 29)) == _grad18.getpixel((30, _H18 - 1))
      and _dn18.getpixel((0, 79)) == _grad18.getpixel((30, _H18 - 1)),
      "下越界：交集按真实偏移对齐，越界区用边缘行延展填满")

_left18 = customize.aligned_crop(_grad18, -20, 100, 100, 80)
check(_left18.getpixel((20, 0)) == _grad18.getpixel((0, 100))
      and _left18.getpixel((0, 0)) == _grad18.getpixel((0, 100)),
      "左越界：交集按真实偏移对齐，越界区用边缘列延展填满")

_out18 = customize.aligned_crop(_grad18, 30, _H18 + 50, 100, 80)
check(_out18 is not None and _out18.size == (100, 80)
      and len({_out18.getpixel(q)
               for q in ((0, 0), (99, 0), (0, 79), (99, 79))}) == 1,
      "完全在窗口外时整张裁片仍被填满，无空白")

_no_blank18 = all(
    im.getpixel((x, y)) != (0, 0, 0)
    for im in (_up18, _dn18, _left18, _out18)
    for x in range(0, 100, 7) for y in range(0, 80, 7))
check(_no_blank18, "所有越界裁片都不存在未渲染的黑/空白像素")

# 18.2 端到端：渐变背景 + card=80，缓慢滑动 30 步逐帧比对
config.set_app_bg(customize.import_image_pil(_grad18, "op18_grad.png"))
config.set_opacity(80, 0)
p._scroll = 0.0
customize.clear_cache()
p._img_cache.clear()
p.render()
p.root.update()

_cur18, _rows18 = {}, []
_orig_l18, _orig_c18 = p._card_layer, customize.card_layer


def _wrap18(key, x1, y1, x2, y2):
    _cur18["rect"] = (x1, y1, x2, y2)
    return _orig_l18(key, x1, y1, x2, y2)


def _spy18(filename, w, h, radius, alpha, base, card_color, *a, **kw):
    _rows18.append((_cur18.get("rect"), base.copy(), w, h))
    return _orig_c18(filename, w, h, radius, alpha, base, card_color, *a, **kw)


p._card_layer = _wrap18
customize.card_layer = _spy18
_worst18, _blank18, _pairs18 = 0, 0, 0
try:
    for _i18 in range(30):
        p._scroll = (p._max_scroll or 1.0) * _i18 / 29
        _rows18.clear()
        p.render()
        p.root.update()
        _L18 = p._app_layer_img
        _lw18, _lh18 = _L18.size
        for _rect18, _b18, _bw18, _bh18 in _rows18:
            if _rect18 is None:
                continue
            _x1, _y1, _x2, _y2 = _rect18
            _ytrue = int(round(_y1 * p.S))
            _rows_vis = {max(0, -_ytrue) + 2,
                         max(0, -_ytrue) + _bh18 // 2,
                         min(_bh18 - 3, _lh18 - 1 - _ytrue)}
            for _row18 in _rows_vis:
                if not (0 <= _row18 < _bh18):
                    continue
                _wy = _ytrue + _row18
                if not (0 <= _wy < _lh18):
                    continue
                _inside18 = _b18.getpixel((_bw18 // 2, _row18))
                _outside18 = _L18.getpixel(
                    (min(_lw18 - 1, int(round(_x2 * p.S)) + 6), _wy))
                _worst18 = max(_worst18, abs(_inside18[0] - _outside18[0]))
                _pairs18 += 1
            if any(_b18.getpixel((x, y)) == (0, 0, 0)
                   for x in range(0, _bw18, 13)
                   for y in range(0, _bh18, 13)):
                _blank18 += 1
finally:
    p._card_layer = _orig_l18
    customize.card_layer = _orig_c18
check(_pairs18 > 100 and _worst18 <= 2,
      f"非纯色背景滚动 30 步、{_pairs18} 组同 y 比对，最大色差={_worst18}")
check(_blank18 == 0,
      f"滚动全程卡片裁片无未渲染/黑块（命中 {_blank18} 次）")

# 18.3 缓存键用真实坐标：慢速滑动 0.05px 也生成新键
_k0_18 = {k for k in p._img_cache
          if isinstance(k, tuple) and k and k[0] == "cardlayer"}
p._scroll = 0.05
p.render()
p.root.update()
_k1_18 = {k for k in p._img_cache
          if isinstance(k, tuple) and k and k[0] == "cardlayer"}
check(len(_k1_18 - _k0_18) > 0,
      "慢速滑动 0.05px 也生成新的裁片缓存键，不复用旧位置")
_src18 = ""
with open(os.path.join(ROOT, "ez24g", "ui", "panel.py"),
          encoding="utf-8") as _f18:
    _src18 = _f18.read()
check("bx, by, round(y1, 3)" in _src18
      and "max(0, min(int(round(y1 * self.S))" not in _src18,
      "裁片坐标不再钳制，缓存键改用真实物理坐标")

# 18.4 对照：纯色背景仍正常；card=0 不合成卡片层
config.set_app_bg(customize.import_image_pil(
    _cz.Image.new("RGB", (_W18, _H18), (120, 130, 140)), "op18_solid.png"))
customize.clear_cache()
p._img_cache.clear()
p._scroll = (p._max_scroll or 1.0) * 0.4
p.render()
p.root.update()
_solid_l18 = customize.aligned_crop(p._app_layer_img, -30, -20, 100, 80)
# 整窗背景现在自带圆角：四角让出桌面画面，越界延展会沿用角像素。
# 取样点改到圆角内部（远离左上角），「全图有内容、不产生色差」的原意不变。
check(_solid_l18 is not None
      and all(_solid_l18.getpixel(q) == (120, 130, 140)
              for q in ((80, 50), (90, 60), (60, 70), (99, 70))),
      "纯色背景对照：圆角内部裁片全图有内容且不产生色差")
check(p._app_layer_img is not None
      and p._app_layer_img.getpixel((p.W // 2, p.H // 2)) == (120, 130, 140),
      "纯色背景对照：圆角只让出四角，窗口中心仍是原背景色")
config.set_opacity(0, 0)
_k18 = list(p._card_rects)[0]
check(p._card_layer(_k18, *p._card_rects[_k18]) is None,
      "card=0（完全不透明）时不合成 L2，行为未变")

config.set_app_bg("")
config.set_opacity(0, 0)
p._scroll = 0.0
customize.clear_cache()
p._img_cache.clear()
p.render()
p.root.update()

# =====================================================================
# 第 19 项：窗口拖动时软件背景实时重绘（非 0 透明度）
# =====================================================================
from ez24g.ui import backdrop as _bd19
import time as _time19

_calls19 = []


def _fake_grab19(x, y, w, h):
    """整屏纵向渐变：颜色由屏幕绝对 y 决定，窗口上下移动必然变色。"""
    _calls19.append((x, y, w, h))
    im = _cz.Image.new("RGB", (w, h))
    d = _cz.ImageDraw.Draw(im)
    for yy in range(h):
        c = (y + yy) % 200 + 30
        d.line((0, yy, w, yy), fill=(c, 60, 90))
    return im


_orig_g19 = _bd19.grab
_bd19.grab = _fake_grab19
_orig_r19 = p.render
_orig_cl19 = customize.card_layer
_rc19 = {"n": 0}
_l2_19 = []


def _spy_r19():
    _rc19["n"] += 1
    return _orig_r19()


def _spy_cl19(filename, w, h, radius, alpha, base, card_color, *a, **kw):
    _l2_19.append(base.getpixel((w // 2, h // 2))[:3])
    return _orig_cl19(filename, w, h, radius, alpha, base, card_color, *a, **kw)


class _E19:
    pass


def _mk19(x, y, xr, yr):
    e = _E19()
    e.x, e.y, e.x_root, e.y_root = x, y, xr, yr
    return e


p.render = _spy_r19
customize.card_layer = _spy_cl19
try:
    config.set_app_bg(customize.import_image_pil(
        _cz.Image.new("RGB", (p.W, p.H), (20, 20, 220)), "op19_app.png"))
    config.set_opacity(60, 50)          # 卡片也半透明，才能验证 L1+L2 同步
    p._drag_snapshot = None
    p._backdrop_key = None
    p._backdrop_img = None
    p._backdrop_epoch += 1
    customize.clear_cache()
    p._img_cache.clear()
    p.render()
    p.root.update()

    _calls19.clear()
    _rc19["n"] = 0
    _l2_19.clear()
    _sw19 = p.root.winfo_screenwidth()
    _big0 = sum(1 for c in _calls19 if c[2] >= _sw19 - 2)
    _before19 = p._app_layer_img.getpixel((7, 7))

    p._drag_last_render = 0.0
    p._on_press(_mk19(200, 20, 500, 300))
    _drag_px19 = []
    for _dx19, _dy19 in ((20, 30), (40, 60), (60, 90), (80, 120), (100, 150)):
        _time19.sleep(0.025)              # 越过 20ms 节流间隔
        p._on_drag(_mk19(200, 20, 500 + _dx19, 300 + _dy19))
        p.root.update()
        _drag_px19.append(p._app_layer_img.getpixel((7, 7)))
    _big1 = sum(1 for c in _calls19 if c[2] >= _sw19 - 2)

    check(_rc19["n"] >= 3,
          f"拖动 5 步期间完整 render 至少 3 次（实测 {_rc19['n']} 次）")
    check(len(set(_drag_px19)) >= 2 and _before19 != _drag_px19[0],
          f"拖动中 L1 随窗口位置实时变化：{_drag_px19}")
    check(_big1 - _big0 == 1,
          f"整屏桌面快照只在拖动首帧抓 1 次（实测 {_big1 - _big0} 次）")
    check(p._drag_snapshot is not None,
          "拖动期扩展快照已启用")
    check(len(set(_l2_19)) >= 2,
          f"卡片层 L2 的 base 同步跟随 L1 变化（{len(set(_l2_19))} 种取值）")

    _grab_before19 = len(_calls19)
    p._on_release(_mk19(200, 20, 600, 450))
    p.root.update()
    check(p._drag_snapshot is None,
          "松手后拖动期快照已清空")
    check(len(_calls19) > _grab_before19,
          "松手走完整质量流程真实重抓校正")

    # 对照：app=0 时完全不启用拖动期路径，行为与修复前一致
    config.set_opacity(60, 0)
    p._drag_snapshot = None
    p._backdrop_key = None
    p._backdrop_img = None
    p._backdrop_epoch += 1
    customize.clear_cache()
    p._img_cache.clear()
    p.render()
    p.root.update()
    _calls19.clear()
    _rc19["n"] = 0
    p._drag_last_render = 0.0
    p._on_press(_mk19(200, 20, 500, 300))
    for _dx19, _dy19 in ((20, 30), (40, 60), (60, 90)):
        _time19.sleep(0.025)
        p._on_drag(_mk19(200, 20, 500 + _dx19, 300 + _dy19))
        p.root.update()
    check(_rc19["n"] == 0 and not _calls19 and p._drag_snapshot is None,
          "app=0 对照：拖动不抓屏、不额外重绘，行为未变")
    p._on_release(_mk19(200, 20, 560, 390))

    _src19 = ""
    with open(os.path.join(ROOT, "ez24g", "ui", "panel.py"),
              encoding="utf-8") as _f19:
        _src19 = _f19.read()
    check("_grab_drag_snapshot" in _src19
          and "_drag_last_render >= 0.02" in _src19
          and "self._drag_snapshot is not None:" in _src19,
          "拖动期路径与节流完整重绘已落地")
finally:
    p.render = _orig_r19
    customize.card_layer = _orig_cl19
    _bd19.grab = _orig_g19

config.set_app_bg("")
config.set_opacity(0, 0)
p._drag_snapshot = None
customize.clear_cache()
p._img_cache.clear()
p.render()
p.root.update()

# =====================================================================
# 第 20 项：滚动条能拖（不再被窗口拖动拦截、重绘也不掉绑定）
# =====================================================================
from ez24g.ui import customize as _cz20


class _E20:
    pass


def _mk20(x, y, xr=None, yr=None, delta=0):
    e = _E20()
    e.x, e.y = x, y
    e.x_root = x if xr is None else xr
    e.y_root = y if yr is None else yr
    e.delta = delta
    return e


config.set_opacity(0, 0)          # 关掉背景合成，专测交互
config.set_app_bg("")
config.set_card_order([])
app._readings = {f"s{i}": Reading(ok=True, level=70 - i, name=f"S{i}",
                                  transport="hid", vid=0x1300 + i,
                                  pid=0x3500 + i) for i in range(10)}
p._scroll = 0.0
p.render()
p.root.update()

_bar20 = p._scrollbar
check(_bar20 is not None and p._max_scroll > 0 and _bar20.thumb_h > 0,
      f"10 台设备出现滚动条（max={p._max_scroll:.0f}, thumb={_bar20.thumb_h:.0f}）")

_cx20 = (_bar20.x1 + _bar20.x2) / 2 * p.S
_bb20 = p.cv.bbox(p.cv.find_withtag("sbar_thumb")[0])
_cy20 = (_bb20[1] + _bb20[3]) / 2
_wx20, _wy20 = p.root.winfo_x(), p.root.winfo_y()

p._bar_drag = None
p._drag_window = None
p._press = None
p._on_press(_mk20(int(_cx20), int(_cy20)))
check(p._bar_drag is not None and p._drag_window is None and p._press is None,
      "按住滚动条把手：只进入滚动拖动，不再同时开始拖窗口")
check(p.cv.cget("cursor") == "sb_v_double_arrow",
      f"把手上给上下箭头光标（{p.cv.cget('cursor')}）")

_s20 = [p._scroll]
for _i20 in range(1, 5):
    p._on_drag(_mk20(int(_cx20), int(_cy20 + 20 * _i20)))
    p.root.update()
    _s20.append(p._scroll)
check(all(b > a for a, b in zip(_s20, _s20[1:])),
      f"连续拖动每步都跟手（{['%.0f' % v for v in _s20]}）")
check(p.root.winfo_x() == _wx20 and p.root.winfo_y() == _wy20,
      f"拖滚动条时窗口不动（{p.root.winfo_x()},{p.root.winfo_y()}）")

_s_hold20 = p._scroll
p._on_release(_mk20(int(_cx20), int(_cy20 + 80)))
check(p._bar_drag is None, "松手后拖动状态清空，没有残留")
p._on_drag(_mk20(int(_cx20), int(_cy20 + 160)))
p.root.update()
check(abs(p._scroll - _s_hold20) < 0.5,
      f"松手后继续移动不再滚动（{_s_hold20:.0f} → {p._scroll:.0f}）")

# 点轨道空白 = 翻一屏（把手回到顶部，点它下方的轨道）
p._scroll = 0.0
p.render()
p.root.update()
_bar20 = p._scrollbar
_cx20 = (_bar20.x1 + _bar20.x2) / 2 * p.S
_y_track20 = min(_bar20.y2 - 6, (_bar20.thumb[3] + 30)) * p.S
p._bar_drag = None
p._on_press(_mk20(int(_cx20), int(_y_track20)))
_page20 = p._scroll
check(_page20 > 0, f"点轨道空白翻一屏（scroll={_page20:.0f}）")
check(p._bar_drag is not None, "翻屏后仍可继续拖动把手")
p._on_release(_mk20(int(_cx20), int(_y_track20)))
check(p._bar_drag is None, "轨道翻屏松手也清状态")

# 滚动条以外的右槽仍可拖窗口（不能因为修 A 把 B 弄坏）
p._bar_drag = None
p._drag_window = None
p._on_press(_mk20(int((p.LW - 22) * p.S), int(p.H * 0.5)))
check(p._drag_window is not None and p._bar_drag is None,
      "滚动条之外的空白仍按原逻辑拖动窗口")
p._drag_window = None

# 滚轮仍然有效
_s_wheel20 = p._scroll
p._on_wheel(_mk20(10, 10, delta=-120))
check(p._scroll != _s_wheel20, f"滚轮仍能滚动（{_s_wheel20:.0f} → {p._scroll:.0f}）")
p._scroll = 0.0
p.render()
p.root.update()

# =====================================================================
# 第 21 项：静止状态下背景逐帧同步（不靠拖动）
# =====================================================================
from ez24g.ui import backdrop as _bd21

_cur21 = {"rgb": (10, 200, 10)}
_cnt21 = {"grab": 0}


def _fake_g21(x, y, w, h):
    # 纯色会被 backdrop.usable 当成「没抓到」，所以带一点纵向渐变
    _cnt21["grab"] += 1
    r, g, b = _cur21["rgb"]
    im = _cz20.Image.new("RGB", (w, h))
    d = _cz20.ImageDraw.Draw(im)
    for yy in range(h):
        k = 1.0 - 0.25 * yy / max(1, h - 1)
        d.line((0, yy, w, yy), fill=(int(r * k), int(g * k), int(b * k)))
    return im


_orig_g21 = _bd21.grab
_orig_r21 = p.render
_orig_rp21 = p._repaint_backdrop
_rc21 = {"n": 0, "fast": 0}


def _spy_r21():
    _rc21["n"] += 1
    return _orig_r21()


def _spy_rp21():
    r = _orig_rp21()
    if r:
        _rc21["fast"] += 1
    return r


_bd21.grab = _fake_g21
p.render = _spy_r21
p._repaint_backdrop = _spy_rp21
try:
    config.set_opacity(0, 50)
    config.set_app_bg("")
    p._capture_excl = True
    p._visible = True
    p._sync_last = None
    p._sync_misses = 0
    p._backdrop_key = None
    p._backdrop_epoch += 1
    customize.clear_cache()
    p._img_cache.clear()
    p.render()                       # 首帧：把当前桌面画出来
    p.root.update()
    check(p._sync_ready(), "逐帧同步条件成立（可见 / 有排除 / 透明度>0）")

    # 1) 桌面变了 → 不靠任何鼠标交互，自己也重绘
    _rc21["n"] = 0
    _cnt21["grab"] = 0
    _cur21["rgb"] = (220, 30, 30)
    p._last_render = 0.0             # 跳过「刚画过就让一让」的避让
    p._stop_backdrop_sync()
    p._backdrop_sync()
    p._stop_backdrop_sync()
    check(_rc21["fast"] == 1 and _rc21["n"] == 0,
          f"桌面变化后走快路径换图（快 {_rc21['fast']} 次 / 整窗 render "
          f"{_rc21['n']} 次）")
    check(_cnt21["grab"] == 1,
          f"一帧只抓一次屏（{_cnt21['grab']} 次，不重复抓）")
    _l1_21 = p._app_layer_img.getpixel((p.W // 2, p.H // 2))
    check(_l1_21[0] > 80,
          f"L1 立刻反映新桌面（{_l1_21} 应偏向桌面红）")

    # 2) 桌面没变 → 不重绘（静止时不能空转刷帧）
    _rc21["n"] = 0
    _rc21["fast"] = 0
    _cnt21["grab"] = 0
    p._last_render = 0.0
    p._backdrop_sync()
    p._stop_backdrop_sync()
    check(_rc21["n"] == 0 and _rc21["fast"] == 0,
          "桌面没变时不重绘，静止开销只有一次抓取")

    # 2b) 卡片半透明时卡片层也依赖 L1 → 也必须走快路径（只换图元，不整窗重画）
    #     2026-11 修复：以前这里退回完整 render()，而完整 render 是
    #     delete("all") + 重建 300+ 图元（单帧 60ms），同步循环最高 20fps 地跑，
    #     屏幕上看就是整窗闪一下（浅色主题闪白 / 深色闪黑）。
    config.set_opacity(60, 50)
    p.render()
    p.root.update()
    _rc21["n"] = 0
    _rc21["fast"] = 0
    _cur21["rgb"] = (30, 30, 220)
    p._last_render = 0.0
    _card_before21 = {k: p.cv.itemcget(v, "image") for k, v in p._card_img.items()}
    p._backdrop_sync()
    p._stop_backdrop_sync()
    check(_rc21["n"] == 0 and _rc21["fast"] == 1,
          f"卡片半透明时也只换图元、不整窗 render（render {_rc21['n']} 次 / "
          f"快路径 {_rc21['fast']} 次）")
    _card_after21 = {k: p.cv.itemcget(v, "image")
                     for k, v in p._card_img.items()}
    check(bool(_card_before21) and _card_after21 != _card_before21,
          f"卡片背景图元已随桌面更新（{len(_card_after21)} 张）")
    check(p.cv.type(next(iter(p._card_img.values()))) == "image",
          "卡片图元仍是 image（换图不重建卡片）")
    config.set_opacity(0, 50)

    # 3) 拖动卡片/窗口期间让路（不抢交互）
    p._dragging = True
    check(not p._sync_ready(), "卡片拖动期间逐帧同步让路")
    p._dragging = False
    p._bar_drag = (10, 0.0)
    check(not p._sync_ready(), "拖滚动条期间逐帧同步让路")
    p._bar_drag = None
    p._drag_window = (0, 0, 0, 0)
    check(not p._sync_ready(), "拖窗口期间逐帧同步让路")
    p._drag_window = None

    # 4) 透明度 0（完全不透明）时不抓屏
    config.set_opacity(0, 0)
    check(not p._sync_ready(), "软件背景不透明时不做逐帧抓屏")

    # 5) 逐帧同步会自己续期
    config.set_opacity(0, 50)
    p._last_render = 0.0
    p._stop_backdrop_sync()
    p._backdrop_sync()
    check(p._sync_job is not None, "同步循环自续期（下一帧已排好）")
    p._stop_backdrop_sync()
finally:
    _bd21.grab = _orig_g21
    p.render = _orig_r21
    p._dragging = False
    p._bar_drag = None
    p._drag_window = None
    p._last_render = 0.0

config.set_opacity(0, 0)
config.set_app_bg("")
customize.clear_cache()
p._img_cache.clear()
p.render()
p.root.update()

# =====================================================================
# 第 22 项：截屏让路时背景不跳（截屏工具在前台 → 面板要被截进去，且背景
#          必须是「那一刻的桌面」，不能是旧帧/纯色；暂停期间不得再抓屏）
# =====================================================================
from ez24g.ui import backdrop as _bd22
import ez24g.syswin as _sw22

_CONTENT22 = (240, 240, 240)          # 软件背景用纯色图，方便算期望值
_p22 = 50                             # 透明度 50 → 不透明度 0.5
_op22 = 1.0 - _p22 / 100.0
_ST22 = {"c1": (0, 220, 0), "c2": (0, 40, 90)}
_g22 = []


def _expect22(desk):
    return tuple(int(round(desk[i] * _op22 + _CONTENT22[i] * (1 - _op22)))
                 for i in range(3))


def _fake_g22(x, y, w, h):
    im = _cz20.Image.new("RGB", (w, h), _ST22["c1"])
    for i in range(0, w, 4):           # 条纹，避免被 usable() 判成「没抓到」
        for j in range(h):
            im.putpixel((i, j), _ST22["c2"])
    _g22.append({"excl": p._capture_excl, "paused": p._capture_paused})
    return im


_orig_g22 = _bd22.grab
_orig_cta22 = _sw22.capture_tool_active
_orig_ep22 = p._excl_hwnds
_bd22.grab = _fake_g22
try:
    _fn22 = customize.import_image_pil(
        _cz20.Image.new("RGB", (760, 560), _CONTENT22), "cap22.png")
    config.set_app_bg(_fn22 or "")
    config.set_opacity(60, _p22)          # 卡片也半透明：卡片层才会依赖 L1
    p._capture_excl = True
    p._visible = True
    p._capture_paused = False
    p._backdrop_key = None
    p._backdrop_epoch += 1
    p.render()
    p.root.update()
    _got22 = p._app_layer_img.getpixel((8, p.H // 2))
    check(_got22 == _expect22(_ST22["c2"]),
          f"截屏前 L1 符合「桌面×透明度」（{_got22}）")

    # 卡片层的 L2 必须从**本帧** L1 上裁：以前 `_app_layer_img` 赋值晚于
    # `_repaint_cards()`，桌面一变卡片背景就慢一帧（截屏让路时窗口背景是新帧、
    # 卡片还是旧帧，看起来就是「面板里有几块背景不对」）。
    _seen22 = {}
    _orig_rc22 = p._repaint_cards

    def _spy_rc22():
        _seen22["base"] = p._app_layer_img
        return _orig_rc22()

    p._repaint_cards = _spy_rc22
    try:
        _ST22["c1"], _ST22["c2"] = (10, 10, 200), (5, 5, 150)
        p._backdrop_key = None
        p._backdrop_epoch += 1
        p._repaint_backdrop()
        check(_seen22.get("base") is p._app_layer_img,
              "卡片层裁切用的是本帧 L1（卡片背景不慢一帧）")
    finally:
        p._repaint_cards = _orig_rc22

    # 桌面换色 + 截屏工具到前台
    _ST22["c1"], _ST22["c2"] = (250, 250, 60), (200, 160, 0)
    _g22.clear()
    _sw22.capture_tool_active = lambda: True
    p._cap_check_at = 0.0
    p._watch_capture_tool()
    p.root.update()
    check(p._capture_paused and not p._capture_excl,
          "识别到截屏工具：暂时撤掉抓屏排除（面板会出现在截图里）")

    p.render()                        # 暂停期间刷新一次（间隔到点/托盘通知）
    p.root.update()
    _got22 = p._app_layer_img.getpixel((8, p.H // 2))
    check(_got22 == _expect22(_ST22["c2"]),
          f"暂停期间背景仍是「当时那一刻的桌面」（{_got22}，"
          f"旧帧会是 {_expect22(_ST22['c1'])}）")
    _bad22 = [g for g in _g22 if g["paused"] and not g["excl"]]
    check(not _bad22,
          f"暂停期间没有在「排除已撤」状态下抓屏（{len(_bad22)} 次，"
          f"否则会抓到面板自己）")
    # 冻结之后的重绘必须复用屏幕上那张 L1：既不重算也不漂移
    _l1_22 = p._app_layer_img
    _g22.clear()
    p.render()
    p.root.update()
    check(p._app_layer_img is _l1_22,
          "暂停期间重绘直接复用屏幕上那张 L1（零重算、零漂移）")
    check(not _g22,
          f"暂停期间的普通重绘不再抓屏（{len(_g22)} 次）")
    check(p._grab_drag_snapshot() is None,
          "暂停期间不隐藏窗口去抓拖动快照（否则截图里面板会闪没）")

    # 工具退出 → 恢复实时跟随
    _sw22.capture_tool_active = lambda: False
    p._cap_check_at = 0.0
    p._watch_capture_tool()
    p.root.update()
    _ST22["c1"], _ST22["c2"] = (0, 0, 200), (0, 0, 120)
    p.render()
    p.root.update()
    _got22 = p._app_layer_img.getpixel((8, p.H // 2))
    check(_got22 == _expect22((0, 0, 120)),
          f"工具退出后背景恢复实时跟随（{_got22}）")
    check(not p._capture_paused and p._capture_excl,
          "工具退出后抓屏排除已恢复")
finally:
    _bd22.grab = _orig_g22
    _sw22.capture_tool_active = _orig_cta22
    p._capture_paused = False
    p._excl_hwnds = _orig_ep22
    p._capture_excl = True
    config.set_opacity(0, 0)
    config.set_app_bg("")
    customize.clear_cache()
    p._img_cache.clear()
    p.render()
    p.root.update()


# =====================================================================
# 第 23 项：方案5「截图 / 录屏模式」手动开关
# =====================================================================
from ez24g.ui import backdrop as _bd23

_g23 = {"n": 0}
_orig_g23 = _bd23.grab


def _fake_g23(x, y, w, h):
    _g23["n"] += 1
    im = _cz20.Image.new("RGB", (w, h), (60, 80, 100))
    d = _cz20.ImageDraw.Draw(im)
    for i in range(0, w, 8):
        d.line((i, 0, i, h), fill=(120, 140, 160))
    return im


config.set_opacity(0, 50)
config.set_app_bg("")
p._visible = True
p._capture_paused = False
p._manual_capture_mode = False
p._apply_capture_exclusion(True)
_bd23.grab = _fake_g23
try:
    check(not p._manual_capture_mode,
          "方案5：默认关闭，重启不会残留在截图模式")
    _items23 = p._header_menu_items()
    check(not p._manual_capture_mode
          and any(isinstance(it, tuple) and it
                  and str(it[0]) == "截图模式" for it in _items23),
          "默认关闭：菜单只写「截图模式」，状态由开关窗口显示")
    p.set_capture_mode(True)
    p.root.update()
    check(p._manual_capture_mode
          and any(isinstance(it, tuple) and it
                  and str(it[0]) == "截图模式"
                  for it in p._header_menu_items()),
          "开启后菜单仍只写「截图模式」，状态由开关窗口显示")
    check(p._manual_capture_mode and p._capture_paused and not p._capture_excl,
          "方案5：开启后撤掉抓屏排除，PrtSc / 第三方录屏都能拍到窗口")
    check(not p._sync_ready(),
          "方案5：开启后逐帧抓屏停止（窗口不闪、画面不跳）")
    _g23["n"] = 0
    for _ in range(3):
        p.render()
        p.root.update()
    check(_g23["n"] == 0,
          f"方案5：录屏全程不抓屏（{_g23['n']} 次），窗口始终在画面里")
    _sw22.capture_tool_active = lambda: True
    p._cap_check_at = 0.0
    p._watch_capture_tool()
    check(p._manual_capture_mode and not p._capture_excl,
          "方案5：截图工具出现不会把录屏模式改回去")
    _sw22.capture_tool_active = lambda: False
    p._cap_check_at = 0.0
    p._watch_capture_tool()
    check(p._manual_capture_mode and not p._capture_excl,
          "方案5：截图工具退出后仍保持在录屏模式（支持长时间录像）")
    p.set_capture_mode(False)
    p.root.update()
    check(not p._manual_capture_mode and not p._capture_paused
          and p._capture_excl,
          "方案5：关闭后恢复抓屏排除与背景实时跟随")
    check("capture_mode" not in config.load_config(),
          "方案5：开关状态不写进配置，重启默认关闭")
finally:
    _bd23.grab = _orig_g23
    _sw22.capture_tool_active = _orig_cta22
    p.set_capture_mode(False)

# =====================================================================
# 第 24 项：Wallpaper Engine 2K 动态壁纸的「闪黑 / 闪白」坏帧过滤
# =====================================================================
from PIL import Image as _Im24, ImageDraw as _Dr24
from ez24g.ui import backdrop as _bd24

_base24 = {
    "normal": (60, 80, 100),
    "normal2": (68, 84, 104),
    "dark": (5, 5, 6),          # 带一点纹理的近黑帧：usable() 判不出
    "light": (250, 250, 250),   # 带一点纹理的近白帧
    "gray20": (20, 20, 20),      # 不触发「近黑占比」判据，但亮度极低
    "gray200": (200, 200, 200),  # 不触发「近白占比」判据，但亮度极高
    "gray150": (150, 150, 150),  # 正常的明亮转场
}


def _mk24(kind, idx=0):
    w, h = p.W, p.H
    c = _base24[kind]
    im = _Im24.new("RGB", (w, h), c)
    d = _Dr24.Draw(im)
    for xx in range((idx * 7) % 40, w, 24):
        d.line((xx, 0, xx, h), fill=tuple(min(255, v + 18) for v in c))
    by = (idx * 11) % h
    d.rectangle((0, by, w, min(h, by + 30)),
                fill=tuple(min(255, v + 40) for v in c))
    return im


_n24, _n2_24 = _mk24("normal"), _mk24("normal2", 5)
_d24, _l24 = _mk24("dark"), _mk24("light")
check(_bd24.usable(_d24) and _bd24.usable(_l24),
      "第24项前提：带纹理的近黑/近白帧能通过旧的 usable() 纯色判据")
check(_bd24.sane(_n24, None), "没有上一帧时正常帧照常采用")
check(_bd24.sane(_n2_24, _n24), "正常动态壁纸的连续变化不误杀")
check(_bd24.sane(_mk24("normal", 9), _n24), "壁纸轻微动画不误杀")
check(not _bd24.sane(_d24, _n24), "整片近黑的坏帧被丢弃（旧逻辑会直接上屏）")
check(not _bd24.sane(_l24, _n24), "整片近白的坏帧被丢弃")
check(not _bd24.sane(_mk24("gray200"), _mk24("gray20")),
      "整帧亮度极端突变被丢弃（两帧都不是纯黑白，靠亮度判据拦住）")
check(_bd24.sane(_mk24("gray150"), _mk24("normal"))
      and _bd24.sane(_mk24("normal2"), _mk24("normal2")),
      "正常转场 / 静止帧不被误判为坏帧")
check(_bd24.sane(object(), _n24), "判不出来时一律放行（宁可偶尔闪一下）")

# 集成：坏帧进入逐帧同步链 → 不推帧、不重绘、画面保持上一张、立刻重试
_c24 = {"kind": "normal", "n": 0}
_orig_g24 = _bd24.grab
_orig_rep24 = p._repaint_backdrop
_rc24 = {"n": 0}


def _fake_g24(x, y, w, h):
    _c24["n"] += 1
    return _mk24(_c24["kind"], _c24["n"])


def _spy_rep24():
    r = _orig_rep24()
    if r:
        _rc24["n"] += 1
    return r


_bd24.grab = _fake_g24
p._repaint_backdrop = _spy_rep24
try:
    config.set_opacity(0, 50)
    config.set_app_bg("")
    p._visible = True
    p._manual_capture_mode = False
    p._capture_paused = False
    p._capture_excl = True
    p._drag_snapshot = None
    p._drag_window = None
    p._bar_drag = None
    p._dragging = False
    p._sync_last = None
    p._sync_misses = 0
    p._backdrop_key = None
    p._backdrop_epoch += 1
    p._img_cache.clear()
    customize.clear_cache()
    _c24["kind"] = "normal"
    p.render(); p.root.update()

    # 先跑一帧正常的，建立「上一张」
    p._last_render = 0.0
    p._backdrop_sync(); p._stop_backdrop_sync(); p.root.update()
    _epoch24 = p._backdrop_epoch
    _l1_24 = p._app_layer_img.getpixel((p.W // 2, p.H // 2))

    # 注入坏帧
    _c24["kind"] = "dark"
    _rc24["n"] = 0
    p._last_render = 0.0
    p._backdrop_sync(); p._stop_backdrop_sync(); p.root.update()
    check(_rc24["n"] == 0 and p._backdrop_epoch == _epoch24,
          "坏帧不触发重绘、不推入背景缓存（画面保持上一张）")
    check(p._app_layer_img.getpixel((p.W // 2, p.H // 2)) == _l1_24,
          "坏帧后 L1 像素不变（不会闪黑）")
    check(p._sync_misses == 0, "坏帧后不退让到 200ms，下一帧立刻重试（背景不停）")

    # 恢复正常帧：必须照常跟手，不能因为加了过滤就一直不刷新
    _c24["kind"] = "normal"
    p._last_render = 0.0
    p._backdrop_sync(); p._stop_backdrop_sync(); p.root.update()
    check(p._backdrop_epoch > _epoch24,
          "坏帧之后下一张正常帧仍被采用（不破坏实时跟随）")
finally:
    _bd24.grab = _orig_g24
    p._repaint_backdrop = _orig_rep24
    p._stop_backdrop_sync()
    config.set_opacity(0, 0)


# =====================================================================
# 第 25 项：手动绑定 / 蓝牙 HID 联动 / 删除优先 / 自动识别开关
# =====================================================================
import ez24g.providers as _pv25
import ez24g.devices as _dv25
from ez24g.model import Reading as _Rd25
from ez24g.providers.base import DeviceRef as _DR25

# 1) 手动绑定输入解析
_i25, _e25 = config.parse_bt_manual("85")
check(_i25 == {"kind": "fixed", "level": 85} and not _e25,
      "手动绑定：填数字解析成固定电量")
_i25, _e25 = config.parse_bt_manual("gatt:180F:2A19")
check(_i25 == {"kind": "gatt", "service": "180F",
               "characteristic": "2A19"} and not _e25,
      "手动绑定：填 gatt:服务:特征 解析成指定位置")
_i25, _e25 = config.parse_bt_manual("")
check(_i25 is None and not _e25, "手动绑定：留空表示取消")
_i25, _e25 = config.parse_bt_manual("abc")
check(_i25 is None and bool(_e25), "手动绑定：乱填会给出错误提示")
_i25, _e25 = config.parse_bt_manual("101")
check(_i25 is None and bool(_e25), "手动绑定：超出 0-100 会被拒绝")
config.set_bt_manual("AA:BB:CC:DD:EE:25", {"kind": "fixed", "level": 77})
check(config.bt_manual_text("AA:BB:CC:DD:EE:25") == "77",
      "手动绑定：已绑定内容能还原成输入框文字")
config.set_bt_manual("AA:BB:CC:DD:EE:25", None)
check(not config.bt_manual_of("AA:BB:CC:DD:EE:25"),
      "手动绑定：取消后配置里不留残留")

# 2) 固定电量只在自动链路全失败时兜底，且明确标注「手动」
_a25 = "AA:BB:CC:DD:EE:25"
_ref25 = _DR25(key=config.bt_key(_a25), kind="bt", transport="bt",
               addr=_a25, connected=True, name="测试蓝牙")
_o_bt25 = _pv25.bt.read_ref
_o_link25 = _pv25._linked_hid
_o_hid25 = _pv25.hid.read_ref
_pv25.bt.read_ref = lambda ref: _Rd25(
    ok=False, present=True, status="unknown_proto", detail="读不到",
    transport="bt", addr=ref.addr)
_pv25._linked_hid = lambda addr: None
try:
    _r25 = _pv25.read_ref(_ref25)
    check(_r25 is not None and not _r25.ok, "未绑定时自动链路失败就是失败")
    config.set_bt_manual(_a25, {"kind": "fixed", "level": 77})
    _r25 = _pv25.read_ref(_ref25)
    check(_r25 and _r25.ok and _r25.level == 77
          and _r25.source == "手动绑定",
          "绑定固定值后兜底生效，并标注「手动绑定」不是实时读数")
finally:
    config.set_bt_manual(_a25, None)
    _pv25.bt.read_ref = _o_bt25
    _pv25._linked_hid = _o_link25
    _pv25.hid.read_ref = _o_hid25

# 3) 蓝牙 HID 联动：成功记住来源，连续失败 2 次自动降级
config.set_bt_source(_a25, "")
_pv25.bt.read_ref = lambda ref: _Rd25(
    ok=False, present=True, status="unknown_proto", detail="读不到",
    transport="bt", addr=ref.addr)
_pv25._linked_hid = lambda addr: _DR25(
    key="x00000000", kind="hid", transport="hid", name="假 HID")
_pv25.hid.read_ref = lambda ref, include_manual=False, prefer_id=None: _Rd25(
    ok=True, level=42, status="discharging", transport="hid")
try:
    _r25 = _pv25.read_ref(_ref25)
    check(_r25 and _r25.ok and _r25.level == 42
          and "联动" in _r25.source,
          "蓝牙主链读不到时自动切到同一台设备的 HID 读数")
    check(config.bt_source_of(_a25) == "hid",
          "联动成功后记住这台设备优先走 HID")
    _pv25._linked_hid = lambda addr: None
    _pv25.read_ref(_ref25)
    _pv25.read_ref(_ref25)
    check(config.bt_source_of(_a25) == "",
          "记住的来源连续 2 次失败后自动降回默认顺序")
finally:
    config.set_bt_source(_a25, "")
    _pv25.bt.read_ref = _o_bt25
    _pv25._linked_hid = _o_link25
    _pv25.hid.read_ref = _o_hid25

# 4) 删除优先：删过的设备不再自动冒出来，手动添加可解除
_k25 = "xABCD1234"
from types import SimpleNamespace as _NS25
_iface25 = _NS25(is_vendor=False, vid=0xABCD, pid=0x1234,
                 usage_page=0xFF00, product="假设备", manufacturer="",
                 instance="", parent="", path="", input_len=2,
                 output_len=2, feature_len=0)
_fake25 = _DR25(key=_k25, kind="hid", transport="hid", vid=0xABCD,
                pid=0x1234, ifaces=[_iface25])
_o_pr25 = _dv25.providers.read_ref
_dv25.providers.read_ref = lambda ref, include_manual=False, prefer_id=None: _Rd25(
    ok=True, level=50, status="discharging", transport="hid")
try:
    config.unignore_device(_k25)
    check(_k25 in _dv25.discover_extras(
        ifaces=[], skip_keys=set(), refs=[_fake25]),
        "正常情况下未添加的设备会出现在额外卡片里")
    config.ignore_device(_k25)
    check(_k25 in config.ignored_devices(), "删除后写入忽略名单")
    check(_k25 not in _dv25.discover_extras(
        ifaces=[], skip_keys=set(), refs=[_fake25]),
        "删过的设备刷新后不再自动回到卡片（任务4）")
    config.unignore_device(_k25)
    check(_k25 in _dv25.discover_extras(
        ifaces=[], skip_keys=set(), refs=[_fake25]),
        "从「+ 添加设备」手动选回来后恢复显示")
finally:
    _dv25.providers.read_ref = _o_pr25
    config.unignore_device(_k25)

# 5) 自动识别开关：默认开启、文字与状态同步
_was_auto25 = config.vendor_probe_enabled()
try:
    config.set_vendor_probe(True)
    _items25 = p._header_menu_items()
    check(config.vendor_probe_enabled()
          and any(isinstance(it, tuple) and it
                  and str(it[0]) == "自动识别" for it in _items25),
          "自动识别默认/开启时菜单只写「自动识别」")
    config.set_vendor_probe(False)
    _items25 = p._header_menu_items()
    check(not config.vendor_probe_enabled()
          and any(isinstance(it, tuple) and it
                  and str(it[0]) == "自动识别" for it in _items25),
          "关闭后菜单仍只写「自动识别」，状态由开关窗口显示")
finally:
    config.set_vendor_probe(_was_auto25)

# 6) README 设计红线已同步
_rp25 = os.path.join(config.project_dir(), "README.md")
_rm25 = open(_rp25, encoding="utf-8").read()
check("自动识别" in _rm25
      and "截图模式" in _rm25
      and "不联网、不上传" in _rm25
      and "已内置协议" in _rm25,
      "README 已写明自动识别与零风险边界（任务5）")

# ---------------------------------------------------------------------------
# 26) 设备管理稳定性：刷新时序 / 一轮提速 / 去重 / 状态语义
# ---------------------------------------------------------------------------
print("\n[26] 设备管理稳定性")

# 26.1 刷新进行中再点 = 排队一次，本轮结束立刻补跑（以前直接 return）
import threading as _th26

_o_worker26 = app._worker
_gate26 = _th26.Event()
_calls26 = []


def _stub_worker26():
    _calls26.append(1)
    _gate26.wait(3.0)
    with app._lock:
        app._refreshing = False
        again = app._queued
        app._queued = False
    if again:
        app.refresh()


app._worker = _stub_worker26
try:
    check(app.refresh() is True, "点「立即刷新」会真的启动一轮")
    check(app.refresh() is False, "刷新进行中再点：不丢弃，改为排队")
    _gate26.set()
    _deadline26 = time.time() + 3.0
    while len(_calls26) < 2 and time.time() < _deadline26:
        time.sleep(0.02)
    check(len(_calls26) >= 2, "本轮结束后自动补跑排队的刷新")
finally:
    app._worker = _o_worker26
    app._refreshing = False
    app._queued = False
    _gate26.set()

# 26.2 首轮没读完 ≠ 没有设备
_a26 = A.TrayApp(panel_only=True)
check(_a26.loaded is False and _a26.busy is False, "新实例：未读完、不忙")
_a26._publish({})
check(_a26.loaded is True, "发布过一次后 loaded=True（空态才写「还没有设备」）")

# 26.3 自动发现读到厂商协议后写进配置：下一轮不必全量试探
config.forget_protocol(0xAE26, 0x1126)
devices._remember_protocol(0xAE26, 0x1126, "logitech")
check(config.user_protocols().get("AE26:1126", {}).get("id") == "logitech",
      "自动发现的设备也会记住命中的厂商协议")
check(devices._prefer_id(0xAE26, 0x1126) == "logitech",
      "记住的协议会被提到最前面（prefer_id）")
devices._remember_protocol(0xAE26, 0x1126, "standard")
check(config.user_protocols().get("AE26:1126", {}).get("id") == "logitech",
      "已存厂商协议不被后续 standard 覆盖")
config.forget_protocol(0xAE26, 0x1126)

# 26.4 罗技状态码：只有明确值才报「充电中 / 已充满」
from ez24g.protocols import logitech as _lg26
check(_lg26._status_name(1) == "charging", "罗技状态 1 = 充电中")
check(_lg26._status_name(2) == "full", "罗技状态 2 = 已充满")
check(_lg26._status_name(0) == "discharging", "罗技状态 0 = 已连接/放电")
check(all(_lg26._status_name(c) == "unknown" for c in (3, 4, 5, 9)),
      "不认识的状态码归「未知」，不再瞎猜充电中")
check(_lg26.PROBE_BUDGET <= 2.0,
      f"罗技探测有时间预算（{_lg26.PROBE_BUDGET}s），不会拖住整轮刷新")

# 26.5 同一台蓝牙只留一张卡；2.4G 与手动添加的设备不受影响
_mac26 = "AA:BB:CC:DD:EE:FF"
_r26 = {
    "x046DB390": Reading(ok=True, level=80, transport="bt", addr=_mac26),
    "bt:" + _mac26: Reading(ok=True, level=80, transport="bt", addr=_mac26),
    "x12345678": Reading(ok=True, level=50, transport="hid"),
    "g:g1": Reading(ok=True, level=60, transport="bt", addr=_mac26),
}
_out26 = devices._dedupe_bt_hid(dict(_r26), saved=[])
check("x046DB390" not in _out26, "同一台蓝牙的 HID 身份被合并掉（不再三张卡）")
check("bt:" + _mac26 in _out26, "保留蓝牙身份那条（连接状态与原因更准）")
check("x12345678" in _out26, "2.4G 设备不受去重影响")
check("g:g1" in _out26, "分组卡不受去重影响")

_devs26 = config.load_saved_devices()
config.save_saved_devices(_devs26 + [{"kind": "hid", "vid": 0x046D,
                                      "pid": 0xB390, "name": "蓝牙键盘"}])
try:
    _out26b = devices._dedupe_bt_hid(dict(_r26), saved=config.load_saved_devices())
    check("x046DB390" in _out26b, "手动添加过的 HID 身份不被合并掉")
finally:
    config.save_saved_devices(_devs26)

# 26.6 启动清掉 MAC 为空的脏记录（"bt:"）
_l26 = config.last_store()
_l26["bt:"] = {"level": 90, "ts": time.time()}
_l26["x373B1278"] = {"level": 87, "ts": time.time()}
_l26["bt:11:22:33:44:55:66"] = {"level": 55, "ts": time.time()}
config.save_last(_l26)
_n26 = config.prune_last()
check(_n26 >= 1 and "bt:" not in config.last_store(),
      "启动清掉 MAC 为空的脏记录（凭空多出的电量卡）")
check("x373B1278" in config.last_store(), "合法 HID 记录不被误删")
check("bt:11:22:33:44:55:66" in config.last_store(), "合法蓝牙记录不被误删")

# 26.7 蓝牙身份但键是 VID:PID 的卡片，右键也有「手动绑定电量…」
app._readings["xZZZ26"] = Reading(ok=False, name="蓝牙键盘", transport="bt",
                                  addr=_mac26)
try:
    check(p._bt_addr_of("xZZZ26") == _mac26,
          "蓝牙身份 + VID:PID 键的也能取到 MAC（右键入口不再缺失）")
    check(p._bt_addr_of("x12345678") == "", "2.4G 卡片不会出现手动绑定入口")
finally:
    app._readings.pop("xZZZ26", None)

# 26.8 多台蓝牙并发读，结果一台不少
_o_rb26 = devices._reading_bt
_seen26 = []


def _fake_rb26(dev, refs):
    _seen26.append(dev.get("addr", ""))
    return Reading(ok=True, level=42, transport="bt",
                   addr=dev.get("addr", ""))


_devs26b = config.load_saved_devices()
config.save_saved_devices(_devs26b + [
    {"kind": "bt", "addr": "11:22:33:44:55:66", "name": "A", "vid": 0, "pid": 0},
    {"kind": "bt", "addr": "11:22:33:44:55:77", "name": "B", "vid": 0, "pid": 0},
])
devices._reading_bt = _fake_rb26
try:
    _res26 = devices.read_bt(refs=[], saved=config.load_saved_devices())
    _bt26 = [k for k in _res26 if k.startswith("bt:")]
    check(len(_bt26) >= 2 and len(_seen26) >= 2,
          f"多台蓝牙并发读取，结果一台不少（{len(_bt26)} 台）")
finally:
    devices._reading_bt = _o_rb26
    config.save_saved_devices(_devs26b)

# 26.9 并发读 + 轮次推进 + 失败退避
from ez24g.protocols import base as _pb26
_r1_26 = _pb26.begin_round()
_r2_26 = _pb26.begin_round()
check(_r2_26 == _r1_26 + 1, "每轮刷新推进轮次（协议探测预算据此重置）")

for _ in range(3):
    _pb26._note_fail("probe26", 1, 2)
check(_pb26._backed_off("probe26", 1, 2),
      "连续 3 轮读不到 → 30 秒内不再硬试（否则每轮都白等一个超时）")
_pb26._note_ok("probe26", 1, 2)
check(not _pb26._backed_off("probe26", 1, 2), "读到一次就恢复立即重试")

_o_rd26 = devices.read_device


def _fake_rd26(dev, ifaces=None, include_manual=False, refs=None):
    return Reading(ok=True, level=55, name=dev.get("name", ""),
                   vid=int(dev["vid"]), pid=int(dev["pid"]))


devices.read_device = _fake_rd26
try:
    _saved26 = [{"kind": "hid", "vid": 0x1234, "pid": 0x0001, "name": "A"},
                {"kind": "hid", "vid": 0x1234, "pid": 0x0002, "name": "B"},
                {"kind": "hid", "vid": 0x1234, "pid": 0x0003, "name": "C"}]
    _h26 = devices.read_hid(ifaces=[], refs=[], saved=_saved26,
                            include_manual=False)
    check(all(k in _h26 for k in ("x12340001", "x12340002", "x12340003")),
          "多台 HID 并发读，一台不少也不串")
finally:
    devices.read_device = _o_rd26

# ---------------------------------------------------------------------------
# 第 27 项：真机演练问题修复（Esc / 下载入口 / 回读不保存 / 清单过滤 / 离线手填）
# ---------------------------------------------------------------------------
import struct as _st27
from types import SimpleNamespace as _NS27
import ez24g.ui.help as _h27
from ez24g.ui import manual as _m27
from ez24g import providers as _pv27
import ez24g.hidapi as _ha27
from ez24g.providers.base import DeviceRef as _DR27

_g27 = [t for _k, t in _h27.GUIDE]
check(any("打开下载页" in t for t in _g27), "R2 引导第 1 步给出下载页入口")
check(any("保持唤醒" in t for t in _g27), "R2 引导提醒设备保持唤醒")
_w27 = _h27.HelpWindow(p, "x369B0054")
_w27.target_vid, _w27.target_pid = 0x369B, 0x0054   # 固定目标，便于打桩回读
p.root.update()
check(bool(_w27.root.tk.call("bind", _w27.root._w, "<Escape>")),
      "R1 抓包向导已绑定 Esc")
# 打桩成「没装」再重画：本机可能真的装了 USBPcap，按设计那种情况下
# 按钮不该出现，直接断言会在两种环境里给出相反结论。
_o_find27 = _h27.HelpWindow._find_usbpcap
_h27.HelpWindow._find_usbpcap = lambda self: ""
_w27.render()
_t27 = [_w27.cv.itemcget(i, "text") for i in _w27.cv.find_all()
        if _w27.cv.type(i) == "text"]
check("打开下载页" in _t27, "R2 未装 USBPcap 时有下载页按钮")
_h27.HelpWindow._find_usbpcap = _o_find27
_o27, _o_site27 = [], _h27.webbrowser.open
_h27.webbrowser.open = lambda u: _o27.append(u)
_w27._open_site()
check(bool(_o27) and "usbpcap" in _o27[0], "R2 下载按钮能打开官网")
_h27.webbrowser.open = _o_site27

# 回读失败：只提示、绝不保存（打桩模拟一次抓包，不碰真机 USBPcap）
_cap27 = os.path.join(tmp, "cap27.pcap")
open(_cap27, "wb").write(_st27.pack("<IHHIIII", 0xa1b2c3d4, 2, 4,
                                     0, 0, 65535, 249))
_o_ex27, _o_scan27 = _h27.pcap.extract_for, _h27.pcap.scan_devices
_o_val27, _o_ask27 = _h27.infer.validate_capture, _h27.simpledialog.askinteger
_frames27 = [(0x01, 1, b"\x08\x7d\x72\x02"),
             (0x81, 1, b"\x00\x11\x22\x4d\x33\x44")]
_h27.pcap.extract_for = lambda *a, **k: (_frames27, {})
_h27.pcap.scan_devices = lambda *a, **k: [(0x369B, 0x0054)]
_h27.simpledialog.askinteger = lambda *a, **k: 77
_h27.infer.validate_capture = lambda *a, **k: (None, None)
_w27._cap_file = _cap27
_w27._finish_capture()
check("暂不保存" in _w27._cap_status, "R3 设备没回应时提示写清楚")
check("369B:0054" not in config.user_protocols(), "R3 回读失败绝不保存协议")
_h27.infer.validate_capture = lambda *a, **k: (
    None, Reading(ok=True, level=42, source="回读"))
_w27._finish_capture()
check("未保存" in _w27._cap_status
      and "369B:0054" not in config.user_protocols(),
      "R3 回读值不符也不保存")
_h27.pcap.extract_for, _h27.pcap.scan_devices = _o_ex27, _o_scan27
_h27.infer.validate_capture, _h27.simpledialog.askinteger = _o_val27, _o_ask27
_w27.close()

# 清单只挡主板内置 HID，不误伤外接 2.4G
_o_enum27 = _ha27.enumerate_hid
def _iface27(vid, pid, prod, inst=""):
    return _NS27(vid=vid, pid=pid, product=prod, instance=inst, parent="")
_ha27.enumerate_hid = lambda: [
    _iface27(0x048D, 0x8051, "HIDI2C Device", "ACPI\\XYZ"),
    _iface27(0x093A, 0x2008, "Sensor", "ACPI\\PNP0C50"),
    _iface27(0x046D, 0xC547, "USB Receiver", "USB\\VID_046D")]
_wa27 = _m27.AddUnknownWindow(p, "2g")
_items27 = _wa27._items
check(len(_items27) == 1 and _items27[0][0] == "046D:C547",
      "R4 清单只挡 HIDI2C/ACPI，真 2.4G 保留")
_ha27.enumerate_hid = _o_enum27
_wa27.close()

# 离线蓝牙不显示手填电量；在线时兜底保持不变
_a27 = "AA:BB:CC:DD:EE:27"
def _ref27(conn):
    return _DR27(key=config.bt_key(_a27), kind="bt", transport="bt",
                 addr=_a27, connected=conn, name="测试蓝牙")
config.set_bt_manual(_a27, {"kind": "fixed", "level": 85})
_r27 = _pv27.read_ref(_ref27(False))
check(_r27 is not None and not _r27.ok and _r27.level is None
      and _r27.status == "unplugged", "R5 离线蓝牙不显示手填固定值")
config.set_bt_manual(_a27, {"kind": "gatt", "service": "180F",
                            "characteristic": "2A19"})
_r27 = _pv27.read_ref(_ref27(False))
check("设备未连接" in (_r27.detail or ""), "R6 离线 GATT 给出明确原因")
_o_bt27, _o_link27 = _pv27.bt.read_ref, _pv27._linked_hid
_pv27.bt.read_ref = lambda ref: Reading(
    ok=False, present=True, status="unknown_proto", detail="读不到",
    transport="bt", addr=ref.addr)
_pv27._linked_hid = lambda addr, hid_refs=None: None
config.set_bt_manual(_a27, {"kind": "fixed", "level": 77})
_r27 = _pv27.read_ref(_ref27(True))
check(_r27 and _r27.ok and _r27.level == 77
      and _r27.source == "手动绑定", "R5 在线设备的固定值兜底保持不变")
_pv27.bt.read_ref, _pv27._linked_hid = _o_bt27, _o_link27
config.set_bt_manual(_a27, None)

# ---------------------------------------------------------------------------
# 第 28 项：星火 Monitor 改名 / 二级窗口 / 引导 / 疑似断连
# ---------------------------------------------------------------------------
import ez24g.ui.help as _h28
from ez24g.ui import manual as _m28, cards as _cd28, about as _ab28

check(config.APP_NAME == "星火 Monitor", "R8 软件名为星火 Monitor")
check(config.CONFIG_DIR_NAME == "SparkMonitor", "R8 配置目录为 SparkMonitor")
check(config.get_title() == "星火 Monitor", "R8 默认标题为星火 Monitor")

p.render(); p.root.update()
check(bool(p.cv.tk.call(p.cv._w, "bind", "title_btn", "<Button-1>")),
      "R9 标题左键可改名")
check(bool(p.cv.tk.call(p.cv._w, "bind", "title_btn", "<Button-3>")),
      "R9 标题右键菜单保留")

for _g28, _n28 in ((_m28.GUIDE_BT, "蓝牙手动"),
                   (_m28.GUIDE_ADD_2G, "2.4G添加"),
                   (_m28.GUIDE_ADD_BT, "蓝牙添加"),
                   (_h28.GUIDE, "抓包引导")):
    check(any("最终方法" in t for _, t in _g28),
          "R5 %s 引导含最终方法" % _n28)
check(any("弹出一个小输入框" in t for _, t in _h28.GUIDE),
      "R6 抓包引导写明填到哪里")

_w28 = _m28.BtManualWindow(p, "AA:BB:CC:DD:EE:28", "测试")
p.root.update()
check(int(_w28.root.attributes("-topmost")) == 1,
      "R1 手动窗口压在主窗口之上")
check(_w28.value.cget("relief") == "solid"
      and _w28.value.cget("bg") != _w28.root.cget("bg"),
      "R3 输入框有明显底色与边框")
_w28.value.delete(0, "end"); _w28.value.insert(0, "85")
_w28._save(); p.root.update()
check(not _w28.is_alive(), "R4 保存后窗口正常关闭")
p._open_bt_manual("bt:AA:BB:CC:DD:EE:28"); _w28b = p._dlg
_w28b.value.delete(0, "end"); _w28b.value.insert(0, "90")
check(_w28b.value.cget("state") == "normal" and _w28b.value.get() == "90",
      "R4 重开后输入框能点能删能改")
_w28b.close()

p.open_help("x369B0054"); _hw28 = p._help_win
_hw28.close()
check(p._help_win is None, "R2 抓包窗口关闭后清掉记录")
p.open_help("x369B0054"); _hw28b = p._help_win
check(_hw28b is not None and _hw28b is not _hw28,
      "R2 同一路径能重新打开抓包窗口")
p.open_help("x369B0054")
check(p._help_win is _hw28b, "R2 已开着时提到前面不重复创建")
if p._help_win:
    p._help_win.close()

check(any(isinstance(i, tuple) and "关于" in str(i[0])
          for i in p._header_menu_items()), "R5 设置菜单有关于入口")
p._open_about(); _a28 = p._dlg
check(isinstance(_a28, _ab28.AboutWindow)
      and int(_a28.root.attributes("-topmost")) == 1,
      "R5 关于窗口可开且置顶")
_a28.close()

# 三个窗口底部文字与按钮不重叠
_o_lines28 = _m28._ManualBase._lines
_res28 = []
def _rec28(self, items, *a, **k):
    y = _o_lines28(self, items, *a, **k)
    off = 62 if isinstance(self, _m28.BtManualWindow) else 116
    floor = 54 if off == 62 else 108
    btn = max(y + floor, min(y + off, self.LH - 18 - 40))
    _res28.append(btn + 30 <= self.LH - 18 - 6)
    return y
_m28._ManualBase._lines = _rec28
for _k28 in ("2g", "bt"):
    _ww28 = _m28.AddUnknownWindow(p, _k28); _ww28.root.update(); _ww28.close()
_ww28 = _m28.BtManualWindow(p, "AA:BB:CC:DD:EE:28", "t")
_ww28.root.update(); _ww28.close()
_m28._ManualBase._lines = _o_lines28
check(len(_res28) == 3 and all(_res28), "R7 三个窗口底部文字与按钮不重叠")

_st28 = Reading(ok=True, level=15, status="discharging",
                source="x", name="s", stale=True)
check(_cd28._status_of(_st28)[0] == "疑似断连", "R10 断连写「疑似断连」")
check(_cd28._SUSPECT_BLUE == (46, 134, 222), "R10 指示灯固定蓝色")
_o_read28 = app._readings
_o_ever28 = app._ever
app._ever = True
app._readings = {
    "xAAA10001": Reading(ok=True, level=80, status="discharging", source="x",
                         name="A", transport="hid", vid=1, pid=1, hid_known=True),
    "xBBB20002": Reading(ok=True, level=15, status="discharging", source="x",
                         name="B", transport="hid", vid=2, pid=2,
                         hid_known=True, stale=True),
}
p.render(); p.root.update()
check("xBBB20002" in p._zones.get("bottom", [])
      and "xBBB20002" not in p._zones.get("top", []),
      "R10 断连卡片归入读不到电量区")
app._readings = _o_read28
app._ever = _o_ever28

# ---------------------------------------------------------------------------
# 第 29 项：手动窗口输入框打开即可打字（无边框置顶窗不会自动交焦点）
# ---------------------------------------------------------------------------
def _focus29(make, first):
    w = make(); w.root.update()
    e = getattr(w, first)
    ok = str(w.root.focus_get()) == str(e._w)
    w.root.event_generate("<Key>", keysym="4"); e.update_idletasks()
    return w, e, ok and e.get() == "4"


_w29, _e29, _ok29 = _focus29(
    lambda: _m28.AddUnknownWindow(p, "2g"), "code")
check(_ok29, "R-fix 添加2.4G 打开即可打字")
_e2_29 = _w29.name
_e2_29.event_generate("<Button-1>", x=5, y=5); _w29.root.update()
_e2_29.event_generate("<Key>", keysym="A"); _e2_29.update_idletasks()
check(str(_w29.root.focus_get()) == str(_e2_29._w)
      and _e2_29.get() == "A", "R-fix 点第二个输入框可切换并输入")
_w29.close()

_w29, _e29, _ok29 = _focus29(
    lambda: _m28.AddUnknownWindow(p, "bt"), "code")
check(_ok29, "R-fix 添加蓝牙 打开即可打字")
_w29.close()

_w29, _e29, _ok29 = _focus29(
    lambda: _m28.BtManualWindow(p, "AA:BB:CC:DD:EE:29", "测试"), "value")
check(_ok29, "R-fix 蓝牙手动 打开即可打字")
_w29.close()

# ---------------------------------------------------------------------------
# 第 30 项：罗技 0x1004 真实百分比 + 双模链路合并（打桩，不碰真机硬件）
# ---------------------------------------------------------------------------
from ez24g.protocols import logitech as _lg30
from ez24g.protocols import begin_round as _round30
from ez24g.model import Reading as _R30

check(_lg30._status_name_unified(0) == "discharging",
      "R7 0x1004 状态 0 = 放电中")
check(_lg30._status_name_unified(1) == "charging"
      and _lg30._status_name_unified(3) == "full",
      "R7 0x1004 状态 1/3 = 充电中 / 已充满")
check(_lg30._status_name_unified(2) == "charging"
      and _lg30._status_name_unified(4) == "charging",
      "R7 即将充满 / 慢速充电 按公开规范归充电中")
check(all(_lg30._status_name_unified(c) == "unknown" for c in (5, 6, 7)),
      "R7 电池异常码不瞎猜状态，仍归未知")
check(_lg30._status_name_unified(9) == "unknown"
      and "未知状态码 9" in _lg30._status_note_unified(9),
      "R7 规范外的值明确写成未知状态码")
check(_lg30._bucket_name(8) == "电量充足", "R7 无百分比设备按档位描述")

class _Dev30:
    def __init__(self, *a, **k): pass
    def drain(self): pass
    def write_output(self, b): return len(b)
    def read_input(self, *a): return None
    def set_output_report(self, b): return True
    def close(self): pass

_state30 = {"pct": True, "status": True}
def _call30(dev, rid, devidx, featidx, func, params, length, timeout=0.15):
    head = bytes([0x11, devidx, featidx & 0xFF, ((func & 0x0F) << 4) | 0x01])
    if featidx == 0 and params == b"\x10\x04":
        return bytes([0x11, devidx, 0, 1, 8, 0, 5, 0]) + bytes(12)
    if featidx == 0 and params == b"\x00\x05":
        return bytes([0x11, devidx, 0, 1, 3, 0, 3, 0]) + bytes(12)
    if featidx == 8 and func == 0:
        flags = 0x0F if _state30["pct"] else 0x0D
        return head + bytes([0x0F, flags, 1]) + bytes(10)
    if featidx == 8 and func == 1:
        if not _state30["status"]:
            return None
        return head + bytes([0x57, 0x08, 0x00]) + bytes(10)
    if featidx == 3 and func == 1:
        return head + b"G517\x00" + bytes(10)
    return None

_o_dev30, _o_call30 = _lg30.HidDevice, _lg30._hidpp_call
_lg30.HidDevice, _lg30._hidpp_call = _Dev30, _call30
for _d in (_lg30._SKIP_UNTIL, _lg30._LAST, _lg30._FAILS,
           _lg30._ROUND, _lg30._PAIR_NAMES, _lg30._KNOWN_PAIR,
           _lg30._RECENT):
    _d.clear()
_if30 = _NS27(vid=0x046D, pid=0xC547, output_len=20, path="fake")
_round30()
_r30 = _lg30.read_logitech(_if30)
check(_r30.ok and _r30.level == 87,
      "R7 0x1004 读 fn1 拿到真实 87%，不再把能力掩码 15 当电量")
check(_r30.status == "discharging" and _r30.pair_name == "G517",
      "R7 状态与配对设备名一并带出")
_state30["pct"] = False
_lg30._LAST.clear(); _lg30._ROUND.clear(); _lg30._PAIR_NAMES.clear(); _lg30._RECENT.clear()
_round30()
_r30b = _lg30.read_logitech(_if30)
check(not _r30b.ok and _r30b.level is None and "档位" in _r30b.detail,
      "R7 设备没有百分比电量计时只报档位，绝不编数字")
_state30["pct"], _state30["status"] = True, False
_lg30._LAST.clear(); _lg30._ROUND.clear(); _lg30._PAIR_NAMES.clear(); _lg30._RECENT.clear()
_round30()
_r30c = _lg30.read_logitech(_if30)
check(not _r30c.ok and "不猜数字" in _r30c.detail,
      "R7 fn1 无应答时不显示假电量")
_lg30.HidDevice, _lg30._hidpp_call = _o_dev30, _o_call30

# 双模合并：2.4G 优先，蓝牙只做备用，其它设备不动
_h30 = "x046DC547"; _b30 = "bt:DE:1B:B4:46:93:66"; _a30 = "bt:41:42:5B:30:7C:BB"
def _res30(hid_ok=True):
    return {_h30: _R30(ok=hid_ok, level=87 if hid_ok else None,
                       name="USB Receiver", transport="hid",
                       pair_name="G517", source="罗技 HID++"),
            _b30: _R30(ok=True, level=89, name="G517 LS TKL",
                       transport="bt", addr="DE:1B:B4:46:93:66"),
            _a30: _R30(ok=True, level=80, name="A6", transport="bt")}
_o30 = devices.order_readings(_res30(), [])
check(len(_o30) == 2 and _b30 not in _o30 and _a30 in _o30,
      "R8 同一台 G517 的 2.4G / 蓝牙合成一张，其它设备不受影响")
check(_o30[_h30].level == 87 and _o30[_h30].name == "G517 LS TKL",
      "R8 以 2.4G 的 87 为准，卡片显示完整型号")
_o30b = devices.order_readings(_res30(False), [])
check(_b30 not in _o30b and _o30b[_h30].level == 89,
      "R8 2.4G 读不到时才用蓝牙 89 兜底")
check(devices.merge_paired_links({_a30: _R30(ok=True, level=80, name="A6")})
      [_a30].level == 80,
      "R8 没有配对名的设备完全不参与合并")

# ---------------------------------------------------------------------------
# 第 31 项：双模切换 / 连点刷新后能自愈（全程打桩，不碰真机硬件）
# ---------------------------------------------------------------------------
from ez24g.protocols import reset_backoff as _rb31
for _d31 in (_lg30._SKIP_UNTIL, _lg30._LAST, _lg30._FAILS,
             _lg30._ROUND, _lg30._PAIR_NAMES, _lg30._KNOWN_PAIR,
             _lg30._RECENT):
    _d31.clear()

# ① 切换瞬间「问配对名」没应答：绝不能把空结果记死
_o_call31 = _lg30._hidpp_call
_lg30._hidpp_call = lambda *a, **k: None
check(_lg30._pair_name(None, 0x11, 1, 20, (0x046D, 0xC547)) == ""
      and not _lg30._PAIR_NAMES,
      "R9 切换瞬间没问到配对名时不把空结果记死，之后还会重问")
_lg30._hidpp_call = _o_call31

# ② 设备就绪后重问一次就能拿回 G517，并按设备记住身份
_lg30.HidDevice, _lg30._hidpp_call = _Dev30, _call30
_state30["pct"], _state30["status"] = True, True
_round30()
_r31 = _lg30.read_logitech(_if30)
check(_r31.ok and _r31.pair_name == "G517"
      and _lg30.known_pair(0x046D, 0xC547) == "G517",
      "R9 设备就绪后重新问一次拿回 G517，并按设备记住身份")

# ③ 退避读不到时仍带着已知身份 → 合并改名不失效、不闪回 USB Receiver
_lg30._RECENT.clear()
_lg30._SKIP_UNTIL[(0x046D, 0xC547)] = time.monotonic() + 30.0
_r31b = _lg30.read_logitech(_if30)
check(not _r31b.ok and _r31b.pair_name == "G517",
      "R9 读不到 / 退避时仍带 G517，卡片不会闪回 USB Receiver")
_o31 = devices.order_readings(
    {_h30: _R30(ok=False, name="USB Receiver", transport="hid",
                pair_name=_r31b.pair_name),
     _b30: _R30(ok=True, level=89, name="G517 LS TKL", transport="bt",
                addr="DE:1B:B4:46:93:66")}, [])
check(len(_o31) == 1 and _o31[_h30].name == "G517 LS TKL"
      and _o31[_h30].level == 89,
      "R9 读不到时仍合成一张 G517 卡，蓝牙 89 兜底且名字不退回")

# ④ 手动刷新能一并清掉罗技层退避与节流，不必重启软件
_lg30._RECENT[(0x046D, 0xC547)] = (time.monotonic(), _R30(ok=True, level=87))
_rb31()
check(not _lg30._SKIP_UNTIL and not _lg30._RECENT,
      "R9 手动刷新清掉罗技层退避与节流，不必重启软件")

# ⑤ 已学会专属读法的设备：别的读法顶上来的数字不采信
check(devices._from_preferred(
          _R30(ok=True, level=15, protocol_id="standard"), "logitech") is False,
      "R9 专属读法没应答时，不采信通用读法给的 15")
check(devices._from_preferred(
          _R30(ok=True, level=87, protocol_id="logitech"), "logitech") is True,
      "R9 专属读法自己读到的 87 照常采信")
check(devices._from_preferred(
          _R30(ok=True, level=64), None) is True,
      "R9 没学过专属读法的设备完全不受影响")

# ⑥ 短节流：一轮里的重复刷新不再重复轰接收器
_lg30._RECENT.clear(); _lg30._SKIP_UNTIL.clear()
_lg30._LAST.clear(); _lg30._ROUND.clear()
_round30()
_r31c = _lg30.read_logitech(_if30)
check(_r31c.ok and _r31c.level == 87 and bool(_lg30._RECENT),
      "R9 一轮里第一次读到 87 后记入节流")
check(_lg30.read_logitech(_if30).level == 87,
      "R9 紧接着的重复刷新复用读数，不再轰接收器")
_lg30.HidDevice, _lg30._hidpp_call = _o_dev30, _o_call30

# ---------------------------------------------------------------------------
# 第 32 项：读不到时的自愈与「通道偶尔打不开」（打桩，不碰真机硬件）
# ---------------------------------------------------------------------------
from ez24g import hidapi as _ha32
from ez24g.protocols import base as _pb32

# ① 已学会的设备不再一失败就歇 30 秒；从没读到的设备仍保留退避
_lg30._LAST[(0x046D, 0xC547)] = (1, 0x1004)
_lg30._SKIP_UNTIL.clear(); _lg30._FAILS.clear()
for _ in range(5):
    _lg30._note_fail((0x046D, 0xC547))
check(not _lg30._SKIP_UNTIL,
      "R10 学会过的设备连着没问到也不歇 30 秒，下一轮自动补上")
_lg30._SKIP_UNTIL.clear(); _lg30._FAILS.clear()
for _ in range(3):
    _lg30._note_fail((0x9999, 0x0001))
check(_lg30._SKIP_UNTIL.get((0x9999, 0x0001), 0) > time.monotonic(),
      "R10 从没读到过的设备仍保留退避，不拖慢整轮刷新")

# ② 写通道打不开时改用另一种发送方式，不再整轮静默读不到
class _FakeDev32:
    h = 1                      # 无效句柄：WriteFile 必失败
    def set_output_report(self, data): return True
check(_ha32.HidDevice.write_output(_FakeDev32(), b"\x11\x01\x00") == 3,
      "R10 写通道打不开时仍能发出报告，不再整轮静默读不到")

# ③ 专属读法即使被标记退避，每轮仍轻轻试一次
class _P32:
    id = "logitech"; name = "fake"; priority = 1
    auto = False; long_report_first = False; calls = 0
    def match(self, i): return True
    def read(self, i):
        _P32.calls += 1
        return _R30(ok=True, level=87)
class _I32:
    vid = 0x046D; pid = 0xC547; vendor_hint = ""; output_len = 20
_o_reg32 = _pb32.registry
_pb32.registry = lambda include_manual=True: [_P32()]
_pb32._SKIP_UNTIL[("logitech", 0x046D, 0xC547)] = time.monotonic() + 30
_r32 = _pb32.read_best([_I32()], prefer_id="logitech")
check(_r32 is not None and _r32.level == 87 and _P32.calls == 1,
      "R10 专属读法被标记退避时每轮仍问一次，且只问一次")
_pb32.registry = _o_reg32
_pb32._SKIP_UNTIL.clear()

# ---------------------------------------------------------------------------
# 第 33 项：公开协议细节 + 「接收器被占用」提示（打桩，不碰真机硬件）
# ---------------------------------------------------------------------------
from ez24g import hidapi as _ha33
from ez24g.ui import cards as _cards33

# ① 能力位只做说明，不改变读数判定
_cap33 = bytes([0x11, 1, 8, 0x01, 0x0F, 0x0F, 0]) + bytes(13)
_note33 = _lg30._capability_note(_cap33)
check("极低" in _note33 and "可充电" in _note33
      and "有百分比电量计" in _note33,
      "R11 0x1004 能力位被翻译成人话（档位/可充电/百分比电量计）")
check(_lg30._capability_note(b"\x11\x01") == "支持档位 未声明",
      "R11 能力字节缺失时不崩、不编造能力")

# ② 详细原因写进读数，真实百分比不受影响
_lg30._RECENT.clear(); _lg30._SKIP_UNTIL.clear()
_lg30._LAST.clear(); _lg30._ROUND.clear()
_state30["pct"], _state30["status"] = True, True
_lg30.HidDevice, _lg30._hidpp_call = _Dev30, _call30
_round30()
_r33 = _lg30.read_logitech(_if30)
check(_r33.ok and _r33.level == 87
      and "放电中" in _r33.detail and "有百分比电量计" in _r33.detail,
      "R11 读数仍是真实 87%，悬停原因带上状态与能力说明")
_lg30.HidDevice, _lg30._hidpp_call = _o_dev30, _o_call30

# ③ 接收器被占用：明确状态 + 看得懂的提示
class _BusyDev33:
    def __init__(self, *a, **k): pass
    h = 1; write_mode = False
    def drain(self): pass
    def write_output(self, b):
        raise _ha33.HidError(_ha33.WRITE_BUSY_HINT)
    def set_output_report(self, data): return False
    def read_input(self, *a): return None
    def close(self): pass
_lg30._RECENT.clear(); _lg30._SKIP_UNTIL.clear()
_lg30._LAST.clear(); _lg30._ROUND.clear()
_lg30.HidDevice = _BusyDev33
_round30()
_r33b = _lg30.read_logitech(_if30)
check(not _r33b.ok and _r33b.status == "busy"
      and "占用" in _r33b.detail and "G HUB" in _r33b.detail,
      "R11 写通道打不开时给出「接收器被占用」状态和中文原因")
_lg30.HidDevice = _o_dev30
check(_cards33._STATUS.get("busy") == ("接收器被占用", "status_asleep"),
      "R11 卡片状态行能直接显示「接收器被占用」")

# ---------------------------------------------------------------------------
# 第 34 项：状态行独立悬浮提示 + 整窗背景自带圆角
# ---------------------------------------------------------------------------
from ez24g.ui import cards as _cards34
from ez24g.ui import customize as _cust34
from ez24g.ui import panel as _panel34

_t34 = getattr(p, "theme", None) or p._theme()
_long34 = ("索引 1 电量 87 · 放电中 · 支持档位 极低/低/正常/充足；"
           "可充电、有百分比电量计 · 接收器被占用时也会写在这里")
try:
    _cards34._tip_show(p.cv, _t34, 1.0, _long34)
    _w34 = _cards34._TIP.get("win")
    check(_w34 is not None, "R12 悬停状态行弹出独立小提示窗")
    if _w34 is not None:
        _lb34 = _w34.winfo_children()[0]
        check(_lb34.cget("text") == _long34,
              "R12 提示窗文字一字不漏，不再塞进状态行")
        check(int(_lb34.cget("wraplength")) >= 180,
              "R12 提示窗按宽度自动换行，不会被截断")
        _f34 = str(_lb34.cget("font"))
        _wrap34 = int(_lb34.cget("wraplength"))
        check(float(_cards34._TIP["alpha"]) > 0.0,
              "R12 出现时走渐入动画（透明度从 0 起步）")
        _cards34._tip_show(p.cv, _t34, 2.0, _long34)
        _lb34b = _cards34._TIP["win"].winfo_children()[0]
        check(str(_lb34b.cget("font")) != _f34
              and int(_lb34b.cget("wraplength")) > _wrap34,
              "R12 整体字号变大时字体与换行宽度同步放大")
        check(_cards34.hide_status_tip is _cards34._tip_hide,
              "R12 重绘时统一走淡出收起")
        _cards34._TIP["alpha"] = 1.0
        _cards34._tip_hide()
        check(0.0 < float(_cards34._TIP["alpha"]) < 1.0,
              "R12 移开状态行走渐出过渡，不瞬间消失")
finally:
    _cards34._tip_destroy()
check(_cards34._TIP.get("win") is None,
      "R12 淡出结束后提示窗销毁，不留残影")

_m34 = _cust34._round_mask(80, 60, 12, invert=True)
check(_m34.getpixel((0, 0)) == 255 and _m34.getpixel((40, 30)) == 0,
      "R12 反向圆角蒙版：四角让出、中心保留")
check(_cust34._round_mask(80, 60, 12).getpixel((0, 0)) == 0,
      "R12 正向蒙版未受影响，卡片圆角照旧")

_Image34 = getattr(_cust34, "Image", None)
if _Image34 is not None:
    _im34 = _Image34.new("RGB", (80, 60), (10, 20, 30))
    _ba34 = _Image34.new("RGB", (80, 60), (200, 100, 50))
    _out34 = _cust34.round_window(_im34, _ba34, 12, (1, 2, 3))
    check(_out34.getpixel((0, 0)) == (200, 100, 50)
          and _out34.getpixel((40, 30)) == (10, 20, 30),
          "R12 整窗背景四角让出桌面画面，中心仍是背景图")
    _out34b = _cust34.round_window(_im34, None, 12, (7, 8, 9))
    check(_out34b.getpixel((0, 0)) == (7, 8, 9),
          "R12 没有桌面快照时四角退化为底色，不露方图")
else:
    check(True, "R12 无 Pillow 环境：跳过像素校验（一）")
    check(True, "R12 无 Pillow 环境：跳过像素校验（二）")

_o_rgn34 = _panel34.set_round_region
_seen34 = []
_panel34.set_round_region = lambda hw, w, h, r: _seen34.append((w, h, r))
try:
    p._round_key = None
    p._sync_round_region(812, 604)
    check(_seen34 and _seen34[-1][0] == 812 and _seen34[-1][1] == 604,
          "R12 尺寸刚变那一刻直接用事件里的新宽高重设圆角")
finally:
    _panel34.set_round_region = _o_rgn34
    p._round_key = None

# ── 第 35 节：抓包向导的提权入口、状态兜底、停止看门狗 ──────────
import ctypes
import json
import ez24g.syswin as _sw35
import ez24g.ui.help as _h35
import ez24g.capture.helper as _hp35

_entry35 = _sw35.helper_entry()
check(os.path.basename(_entry35) == "my_devices.py" and os.path.isfile(_entry35),
      "R13 提权入口固定为产品启动脚本")

_fake35 = os.path.join(tmp, "别的宿主.py")
with open(_fake35, "w", encoding="utf-8") as _f35:
    _f35.write("# 没有 helper 分发的宿主脚本\n")
_o_argv35 = sys.argv
try:
    sys.argv = [_fake35]
    check(_sw35.helper_entry() == _entry35
          and os.path.basename(_sw35.script_path()) == "别的宿主.py",
          "R13 从任意宿主脚本启动都不会把提权目标带偏")
finally:
    sys.argv = _o_argv35

check(any("静默授权" in t for _k, t in _h35.GUIDE),
      "R13 向导文案说明静默提权时不会弹授权窗")
check(isinstance(_h35._uac_silent(), bool),
      "R13 静默提权检测只读注册表、不抛异常")
check(_h35._readable(os.path.join(tmp, "不存在的文件35")) is False,
      "R13 抓包结果不可读时能判出来")

p.open_help("x369B0054")
_w35 = p._help_win
_seen35 = []


def _fake_shell35(ptr):
    sei = ctypes.cast(ptr, ctypes.POINTER(_h35.SHELLEXECUTEINFOW)).contents
    _seen35.append(str(sei.lpParameters))
    sei.hProcess = 0                 # 不真提权，也不留需要关闭的句柄
    return 1


def _reset35(state=None):
    _w35._capturing = True
    _w35._cap_started = time.monotonic()
    _w35._stop_at = 0.0
    _w35._cap_proc = 0
    if state is None:
        try:
            os.remove(_w35._cap_status_file)
        except Exception:
            pass
    else:
        with open(_w35._cap_status_file, "w", encoding="utf-8") as f:
            json.dump({"state": state, "hub": 1, "file": ""}, f)


_o_shell35 = ctypes.windll.shell32.ShellExecuteExW
_o_uac35 = _h35._uac_silent
_o_exit35 = _h35._process_exited
_o_read35 = _h35._readable
_o_cap35 = _h35.CAP_WAIT_TIMEOUT
_o_runt35 = _h35.CAP_RUN_TIMEOUT
_o_stop35 = _h35.STOP_TIMEOUT
_o_after35 = _w35.root.after
_o_find35 = _w35._find_usbpcap
try:
    ctypes.windll.shell32.ShellExecuteExW = _fake_shell35
    _h35._uac_silent = lambda: True
    _w35.root.after = lambda *a: None
    _w35._find_usbpcap = lambda: r"C:\Program Files\USBPcap\USBPcapCMD.exe"
    _w35.start_capture()
    check(_seen35 and os.path.basename(_entry35) in _seen35[0]
          and "--capture-helper" in _seen35[0],
          "R13 提权命令行固定带产品入口和 helper 参数")
    check(_w35._capturing and _w35._cap_started > 0,
          "R13 启动后记下时刻，供超时兜底使用")
    check(_h35._readable(_w35._cap_status_file)
          and os.path.dirname(os.path.realpath(_w35._cap_file))
          == os.path.realpath(_w35._cap_dir),
          "R13 状态文件由主界面预先建好、普通权限可读；抓包文件路径落在本次工作目录")
    check("静默提权" in _w35._cap_status,
          "R13 系统静默提权时状态行明说不会弹授权窗")

    # 抓包前先给目录授权：后台被强杀也不会留下读不了的 pcap
    import subprocess as _sp36
    _calls36 = []
    _o_run36 = _sp36.run

    class _R36:
        returncode = 0

    try:
        _sp36.run = lambda cmd, *a, **k: (_calls36.append(cmd), _R36())[1]
        _w35._capturing = False
        _w35._stop_at = 0.0
        _w35._cap_dir = ""
        _w35.start_capture()
        check(any(isinstance(c, (list, tuple)) and c and c[0] == "icacls"
                  for c in _calls36),
              "R16 抓包前先给工作目录授权，强杀也不会留下读不了的文件")
    finally:
        _sp36.run = _o_run36

    _h35._process_exited = lambda h: True
    _reset35(None)
    _w35._poll_capture()
    check(not _w35._capturing and "提前退出" in _w35._cap_status,
          "R13 后台提前退出时给明确结局，不再无限重排")

    _h35._process_exited = lambda h: False
    _h35.CAP_WAIT_TIMEOUT = 0
    _reset35("starting")
    _w35._poll_capture()
    check(not _w35._capturing and "长时间没有回应" in _w35._cap_status,
          "R13 后台一直不写状态时超时收尾")

    # 正在抓包时不能被 60 秒探测超时杀掉；只有总上限到点才停
    _h35.CAP_WAIT_TIMEOUT = 0
    _reset35("capturing")
    _w35._poll_capture()
    check(_w35._capturing and "正在抓包" in _w35._cap_status,
          "R16 正在抓包时不受 60 秒探测超时影响")

    _h35.CAP_RUN_TIMEOUT = 0
    _reset35("capturing")
    _w35._cap_started = time.monotonic() - 1
    _w35._poll_capture()
    check(not _w35._capturing and "已达上限" in _w35._cap_status,
          "R16 抓包总上限到点才自动停止")

    _h35.CAP_WAIT_TIMEOUT = _o_cap35
    _h35.STOP_TIMEOUT = 0
    _reset35("probing")
    _w35._stop_at = time.monotonic() - 5
    _w35._poll_capture()
    check(not _w35._capturing and "强制停止" in _w35._cap_status,
          "R13 停止看门狗超时后强制结束，不卡在「正在停止」")

    _h35.STOP_TIMEOUT = _o_stop35
    _w35._capturing = True
    _w35._stop_at = 0.0
    _w35.stop_capture()
    check(_w35._stop_at > 0 and "正在停止" in _w35._cap_status
          and os.path.isfile(_w35._cap_stop),
          "R13 点停止会写标记并启动看门狗计时")

    _reset35("stopped")
    _w35._poll_capture()
    check(not _w35._capturing and "已停止" in _w35._cap_status,
          "R13 探测阶段收到停止指令会给出「已停止」结论")

    _done35 = _w35._cap_file
    try:
        open(_done35, "wb").close()       # 模拟后台已经产出抓包文件
    except Exception:
        pass
    with open(_w35._cap_status_file, "w", encoding="utf-8") as f:
        json.dump({"state": "done", "hub": 1, "file": _done35}, f)
    _h35._readable = lambda p: False
    _reset35("done")
    with open(_w35._cap_status_file, "w", encoding="utf-8") as f:
        json.dump({"state": "done", "hub": 1, "file": _done35}, f)
    _w35._cap_read_retry = 0
    _w35._poll_capture()
    check(_w35._capturing and "正在读取" in _w35._cap_status
          and _w35._cap_read_retry == 1,
          "R14 结果文件暂时读不了时先等几轮，不立刻误报「被管理员阻挡」")
    for _ in range(6):
        _w35._capturing = True
        _w35._poll_capture()
    check(not _w35._capturing and "锁住" in _w35._cap_status,
          "R14 重试到上限仍读不了才明说原因，提示里让用户点常显的清理按钮")

    # 权限补上后，同样一份 done 应该正常进入结果处理
    _h35._readable = _o_read35
    _calls35 = []
    _o_fin35 = _w35._finish_capture
    try:
        _w35._finish_capture = lambda: _calls35.append(1)
        _reset35("done")
        with open(_w35._cap_status_file, "w", encoding="utf-8") as f:
            json.dump({"state": "done", "hub": 1, "file": _done35}, f)
        _w35._cap_read_retry = 0
        _w35._poll_capture()
        check(_calls35 and not _w35._capturing,
              "R14 权限补上后正常处理抓包结果，不误报锁住")
    finally:
        _w35._finish_capture = _o_fin35

    _w35.render()
    _texts35 = [str(_w35.cv.itemcget(i, "text"))
               for i in _w35.cv.find_all()
               if _w35.cv.type(i) == "text"]
    check("清理抓包" in _texts35,
          "R14 「清理抓包」按钮一直可见，失败提示不再是空头支票")
finally:
    ctypes.windll.shell32.ShellExecuteExW = _o_shell35
    _h35._uac_silent = _o_uac35
    _h35._process_exited = _o_exit35
    _h35._readable = _o_read35
    _h35.CAP_WAIT_TIMEOUT = _o_cap35
    _h35.CAP_RUN_TIMEOUT = _o_runt35
    _h35.STOP_TIMEOUT = _o_stop35
    _w35.root.after = _o_after35
    _w35._find_usbpcap = _o_find35

# 提权后台：探测期能立刻响应停止、授权失败不影响主流程
_stop35 = os.path.join(tmp, "stop35.flag")
with open(_stop35, "w", encoding="utf-8") as f:
    f.write("1")


class _P35:
    def poll(self):
        return None


_o_sleep35 = time.sleep
_slept35 = []
try:
    time.sleep = lambda s: _slept35.append(s)
    check(_hp35.wait_probe(_P35(), _stop35) is True and not _slept35,
          "R13 探测期一收到停止标记就返回，不再干等满 4 秒")
finally:
    time.sleep = _o_sleep35

_o_run35 = _hp35.subprocess.run
try:
    _hp35.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(OSError("x"))
    _hp35._grant_read([_stop35])
    check(True, "R13 给产出文件补读权限失败也不影响抓包主流程")
finally:
    _hp35.subprocess.run = _o_run35

_ord35 = []
_hp35.finalize_capture("done", lambda *a, **k: _ord35.append("status"),
                       lambda: _ord35.append("grant"))
check(_ord35 == ["grant", "status"],
      "R14 后台先开文件权限再宣布完成，消除读文件竞态")

try:
    _w35._clear_capture()
    _w35.close()
except Exception:
    pass
p._help_win = None

# ── 第 36 节：设备只给三档时的手动档位兜底 ─────────────────────
import ez24g.ui.cards as _cards36

_K36 = "x369B0054"
config.set_manual_level(_K36, 99)
check(config.manual_level_of(_K36) == 99, "R15 手动档位可写入并读回")
config.set_manual_level(_K36, 500)
check(config.manual_level_of(_K36) == 100, "R15 越界值被夹到 0–100")
config.set_manual_level(_K36, None)
check(config.manual_level_of(_K36) is None, "R15 手动档位可清除")

config.set_manual_level(_K36, 99)
_r15 = devices.apply_manual_levels(
    {_K36: Reading(ok=False, present=True, status="unknown_proto")})[_K36]
check(_r15.ok and _r15.level == 99, "R15 读不到但在线时补手动档位")
check(_r15.source == "手动档位" and "非实测" in _r15.detail,
      "R15 来源与说明明确标注非实测")
check(_cards36._status_of(_r15)[0] == "手动档位", "R15 卡片状态显示「手动档位」")
_r15b = devices.apply_manual_levels(
    {_K36: Reading(ok=False, present=False, status="unplugged")})[_K36]
check(not _r15b.ok and _r15b.level is None, "R15 真断开时不补，不假装在线")
_r15c = devices.apply_manual_levels(
    {_K36: Reading(ok=True, level=55, status="discharging")})[_K36]
check(_r15c.level == 55 and _r15c.source == "", "R15 能读到真实电量时不覆盖")

_o_snap36, _o_popup36 = p.app.snapshot, p._popup
_cap36 = []
try:
    p.app.snapshot = lambda: {_K36: Reading(ok=False, present=True, name="L8 Air")}
    p._popup = lambda items, x, y: _cap36.extend(items)
    p._card_menu(_K36, 0, 0)
finally:
    p.app.snapshot, p._popup = _o_snap36, _o_popup36
_labels36 = [x[0] for x in _cap36 if isinstance(x, tuple)]
check(all(any("%d%%" % v in l for l in _labels36) for v in (99, 66, 33)),
      "R15 右键菜单含高 99 / 中 66 / 低 33")
check(any("清除手动档位" in l for l in _labels36), "R15 已设置时可清除手动档位")

config.save_saved_devices([{"kind": "hid", "vid": 0x369B, "pid": 0x0054,
                            "name": "L8 Air"}])
devices.remove_device(_K36)
check(config.manual_level_of(_K36) is None, "R15 删除设备时一并清掉手动档位")

# ---------------------------------------------------------------------------
# R17 觅声音频通道：请求结构、应答校验、接入顺序
# ---------------------------------------------------------------------------
from ez24g.providers import xmos as _xmos16
from ez24g.ui import cards as _cards16

_req16 = _xmos16.build_request()
check(len(_req16) == _xmos16.REQ_LEN == 128,
      "R17 控制请求固定 128 字节")
check((_req16[0], _req16[4], _req16[8]) == (0x17, 0x43, 0x17),
      "R17 请求头三个固定字节正确")
check(_req16[12] == _xmos16.CMD_BATTERY == 0x87,
      "R17 命令码位于第 12 字节")
check(_req16[24] == 1,
      "R17 第 24 字节为 1 —— 只读，从不下发写请求")
check(all(b == 0 for i, b in enumerate(_req16) if i not in (0, 4, 8, 12, 24)),
      "R17 请求其余字节全 0，不带任何设置项")
check(_xmos16.IOCTL_USB_CONTROL == 0x2214D3,
      "R17 IOCTL 码与厂商驱动一致")

check(_xmos16.parse_response(bytes([0x87, 60])) == 60,
      "R17 应答正常解析出电量")
check(_xmos16.parse_response(bytes([0x87, 0])) == 0
      and _xmos16.parse_response(bytes([0x87, 100])) == 100,
      "R17 0 与 100 两个边界值都接受")
check(_xmos16.parse_response(bytes([0x87, 101])) is None,
      "R17 电量超过 100 判为无效应答，绝不显示假数字")
check(_xmos16.parse_response(bytes([0x86, 50])) is None,
      "R17 首字节不是命令码回显时判为无效")
check(_xmos16.parse_response(b"") is None and _xmos16.parse_response(None) is None,
      "R17 空应答不崩、返回 None")
check(_xmos16.supported(0x39A5) and not _xmos16.supported(0x046D)
      and not _xmos16.supported(0),
      "R17 只对觅声白名单启用，其它厂商一律不动")

_o_xm16 = _xmos16.read_level
try:
    _xmos16.read_level = lambda vid, pid: 60
    _r16 = devices.apply_xmos_levels(
        {"x39A53D21": Reading(ok=False, present=True, status="unknown_proto",
                              name="Aries Pro", vid=0x39A5, pid=0x3D21)})
    _v16 = _r16["x39A53D21"]
    check(_v16.ok and _v16.level == 60 and _v16.source == "觅声音频通道",
          "R17 读不到时用音频通道补上真实百分比")
    check("非实测" not in _v16.detail,
          "R17 音频通道是设备上报，不标注「非实测」")
    check(_cards16._status_of(_v16)[0] == "已连接",
          "R17 卡片状态显示「已连接」而不是未知/未连接")

    _r16b = devices.apply_xmos_levels(
        {"x39A53D21": Reading(ok=True, level=77, source="HID 专用",
                              vid=0x39A5, pid=0x3D21)})
    check(_r16b["x39A53D21"].level == 77 and _r16b["x39A53D21"].source == "HID 专用",
          "R17 已有真实读数时绝不覆盖")

    _r16c = devices.apply_xmos_levels(
        {"x39A53D21": Reading(ok=False, present=False, vid=0x39A5, pid=0x3D21)})
    check(not _r16c["x39A53D21"].ok and _r16c["x39A53D21"].level is None,
          "R17 设备真的不在时不补，不假装在线")

    _r16d = devices.apply_xmos_levels(
        {"x046DC547": Reading(ok=False, present=True, vid=0x046D, pid=0xC547)})
    check(not _r16d["x046DC547"].ok and _r16d["x046DC547"].level is None,
          "R17 白名单外的设备完全不碰")

    _xmos16.read_level = lambda vid, pid: None
    _r16e = devices.apply_xmos_levels(
        {"x39A53D21": Reading(ok=False, present=True, vid=0x39A5, pid=0x3D21)})
    check(not _r16e["x39A53D21"].ok,
          "R17 通道读不到时保持原样，交给后面的兜底")

    # 收尾顺序：真实值优先、手动档位兜底
    config.set_manual_level("x39A53D21", 99)
    _xmos16.read_level = lambda vid, pid: 60
    _fin16 = devices.apply_manual_levels(devices.apply_xmos_levels(
        {"x39A53D21": Reading(ok=False, present=True, vid=0x39A5, pid=0x3D21)}))
    check(_fin16["x39A53D21"].level == 60,
          "R17 真实值优先于手动档位")
    _xmos16.read_level = lambda vid, pid: None
    _fin16b = devices.apply_manual_levels(devices.apply_xmos_levels(
        {"x39A53D21": Reading(ok=False, present=True, vid=0x39A5, pid=0x3D21)}))
    check(_fin16b["x39A53D21"].level == 99
          and _fin16b["x39A53D21"].source == "手动档位",
          "R17 通道失效时回退到手动档位")
finally:
    _xmos16.read_level = _o_xm16
    config.set_manual_level("x39A53D21", None)

# 主刷新路径必须走完整三条链：以前只调 apply_last_known，
# 于是「手动档位」在界面上其实一直没生效（它只挂在 read_all 上）。
import inspect as _ins16
from ez24g.app import TrayApp as _TrayApp16
_src16 = _ins16.getsource(_TrayApp16._finish)
check(all(n in _src16 for n in ("apply_xmos_levels", "apply_manual_levels",
                                "apply_last_known")),
      "R17 主刷新收尾同时调用三条兜底链")
check(_src16.index("apply_xmos_levels") < _src16.index("apply_manual_levels")
      < _src16.index("apply_last_known"),
      "R17 收尾顺序为「真实值 → 手动档位 → 上次读数」")

# ---------------------------------------------------------------------------
# R18 ITON 状态报告（机械师 L8 Air）：请求结构、应答解析、匹配范围
# ---------------------------------------------------------------------------
from ez24g.protocols import itons as _it18
import types as _types18

_r18 = _it18.build_request(64)
check(len(_r18) == 64 and _r18[0] == 0xA0 and _r18[1] == 0x50,
      "R18 查询请求是 a0 50 + 补零的 64 字节")
check(all(b == 0 for b in _r18[2:]),
      "R18 请求不带任何设置项（纯查询）")
check(_it18.LEVEL_OFFSET == 8,
      "R18 电量位于回应第 8 字节")

check(_it18.parse_status(
          bytes([0xA1, 0x50, 0x01, 0x03, 0x0D, 0x02, 0x04, 0x25, 0x45, 0x44])) == 69,
      "R18 实测报文解析出 69%")
check(_it18.parse_status(bytes([0xA1, 0x50]) + bytes(6) + bytes([0])) == 0
      and _it18.parse_status(bytes([0xA1, 0x50]) + bytes(6) + bytes([100])) == 100,
      "R18 0 与 100 边界值都接受")
check(_it18.parse_status(bytes([0xA1, 0x50]) + bytes(6) + bytes([101])) is None,
      "R18 超过 100 判为无效，绝不显示假数字")
check(_it18.parse_status(bytes([0xA0, 0x50]) + bytes(6) + bytes([50])) is None
      and _it18.parse_status(bytes([0xA1, 0x51]) + bytes(6) + bytes([50])) is None,
      "R18 报文头不对时判为无效")
check(_it18.parse_status(bytes([0xA1, 0x50])) is None
      and _it18.parse_status(None) is None and _it18.parse_status(b"") is None,
      "R18 过短 / 空应答不崩、返回 None")

_p18 = _it18.ItonStatus()
def _if18(**kw):
    d = {"vid": 0x369B, "pid": 0x0054, "usage_page": 0xFFA0,
         "input_len": 64, "output_len": 64}
    d.update(kw)
    return _types18.SimpleNamespace(**d)

check(_p18.match(_if18()), "R18 命中 ITON 厂商页接口")
check(not _p18.match(_if18(vid=0x046D)), "R18 其它厂商不匹配")
check(not _p18.match(_if18(usage_page=0x0001)), "R18 非厂商页不匹配")
check(not _p18.match(_if18(output_len=0)), "R18 不能写的接口不匹配")
check(_p18.auto and _p18.priority >= 40,
      "R18 默认自动启用，且排在标准读法之后才试")

# ---------------------------------------------------------------------------
# R19 托盘同心圆顺序：拖动排序后必须立刻通知托盘，不能等下一轮刷新
# ---------------------------------------------------------------------------
import threading as _th19
from ez24g import app as _ap19
from ez24g.ui import tray as _tray19

_sent19 = []
_o_pm19 = _ap19.user32.PostMessageW
class _Tray19:
    hwnd = 0x1234
try:
    _ap19.user32.PostMessageW = lambda h, m, w=0, l=0: _sent19.append(m)
    _t19 = _ap19.TrayApp.__new__(_ap19.TrayApp)
    _t19._lock = _th19.Lock()
    _t19._readings = {}
    _t19.devices = []
    _t19.tray = _Tray19()
    _t19._dirty = False
    _t19._full = {}

    config.set_card_order([])
    _t19.reorder_now()
    check(not _sent19, "R19 快照为空时不惊动托盘")

    _t19._readings = {
        "g517": Reading(ok=True, level=78, name="G517", vid=0x046D, pid=0xC547),
        "misiom": Reading(ok=True, level=60, name="Mision-Aries Pro",
                          vid=0x39A5, pid=0x3D21),
        "l8air": Reading(ok=True, level=68, name="L8 Air", vid=0x369B, pid=0x0054),
    }
    config.set_card_order(["g517", "misiom", "l8air"])
    _sent19.clear()
    _t19.reorder_now()
    _order19 = list(_t19.snapshot())
    check(_order19 == ["g517", "misiom", "l8air"],
          f"R19 快照按卡片顺序排列：{_order19}")
    check(_ap19.WM_APP_REFRESH in _sent19,
          "R19 拖动排序后立刻通知托盘重画（不再等 60 秒刷新）")

    # 托盘同心圆：第一个 = 最外层，必须与卡片顺序一致
    _rings19 = _tray19.Tray._tray_rings(_types18.SimpleNamespace(app=_t19))
    _rk19 = [k for k, _lvl in _rings19]
    check(_rk19 == ["g517", "misiom", "l8air"],
          f"R19 同心圆由外到内与卡片顺序一致：{_rk19}")

    # 换一个顺序，确认立刻跟着变
    config.set_card_order(["l8air", "misiom", "g517"])
    _sent19.clear()
    _t19.reorder_now()
    _rk19b = [k for k, _lvl in
              _tray19.Tray._tray_rings(_types18.SimpleNamespace(app=_t19))]
    check(_rk19b == ["l8air", "misiom", "g517"],
          f"R19 换序后同心圆顺序立刻跟着变：{_rk19b}")
    check(_ap19.WM_APP_REFRESH in _sent19,
          "R19 再次排序仍然每次通知托盘")

    # _publish 那条老路径不能丢
    _sent19.clear()
    _t19._publish(dict(_t19._readings))
    check(_ap19.WM_APP_REFRESH in _sent19,
          "R19 刷新发布时托盘照旧收到重画通知")

    # 没有托盘 / 托盘没窗口时不能崩
    _t19.tray = None
    _sent19.clear()
    _t19.reorder_now()
    check(not _sent19, "R19 没有托盘时静默跳过，不抛异常")
finally:
    _ap19.user32.PostMessageW = _o_pm19
    config.set_card_order([])

# ---------------------------------------------------------------------------
# R20 二级窗口布局：不超界、不重叠、该滚动的会滚
# ---------------------------------------------------------------------------
from ez24g.ui import manual as _m20
from ez24g.ui import help as _h20
from ez24g.ui import about as _ab20

class _P20:
    """冒充 Panel：只给窗口需要的那几个属性，不带画布。"""
    def __init__(self, base, s):
        self.S = s
        self.root = base.root
        self.app = base.app
        self._dlg = None
        self._backdrop_epoch = 0
    def render(self):
        pass

_p20 = _P20(p, 1.25)          # 用户实际就是 1.25，按最挤的情况验
_w20 = _m20.AddUnknownWindow(_p20, "2g")
_w20.root.update()
_bottom20 = max(y + h for (_x, y, h) in _w20._place_y)
check(_bottom20 <= _w20.H,
      "R20 添加设备窗：输入框最低点 %d 不超过窗高 %d（不再被裁）"
      % (_bottom20, _w20.H))
check(_w20.H > 660,
      "R20 内容超过一屏时窗口自动长高（H=%d）" % _w20.H)
_w20.close(); p.root.update()

_hw20 = _h20.HelpWindow(_p20, None)
_hw20.root.update()
_bt20 = [_hw20.cv.bbox(i) for i in _hw20.cv.find_all()
         if _hw20.cv.type(i) == "text"
         and str(_hw20.cv.itemcget(i, "text")) in ("开始抓包", "停止", "清理抓包")]
_hint20 = [_hw20.cv.bbox(i) for i in _hw20.cv.find_all()
           if _hw20.cv.type(i) == "text"
           and "每一步" in str(_hw20.cv.itemcget(i, "text"))]
check(bool(_bt20) and bool(_hint20)
      and max(b[3] for b in _bt20) < _hint20[0][1],
      "R20 使用说明：按钮底部不再压底部提示文字")
_bb20 = _hw20.cv.bbox(*_hw20._hbody_ids)
check(_hw20._scroll_range > 0 or (bool(_bb20) and _bb20[3] <= _hw20.H),
      "R20 使用说明：正文要么装得下、要么可滚动，不会溢出窗口")
# 滚动机制单独验：不依赖「装没装 USBPcap」（那会改变按钮行数与可视高度）
_hw20._scroll_range = 50.0
_y20 = _hw20.cv.bbox(_hw20._hbody_ids[0])[1]
_hw20._scroll_help_by(-20)
check(_hw20.cv.bbox(_hw20._hbody_ids[0])[1] < _y20,
      "R20 滚动机制能把正文真的滚上去")
_hw20._scroll_range = 0.0
_hw20.close(); p.root.update()

_abw20 = _ab20.AboutWindow(_p20)
_abw20.root.update()
_foot20 = [_abw20.cv.bbox(i) for i in _abw20.cv.find_all()
           if _abw20.cv.type(i) == "text"
           and "点地址" in str(_abw20.cv.itemcget(i, "text"))]
check(bool(_foot20) and _foot20[0][3] <= _abw20.H,
      "R20 关于窗：底部提示没有掉出窗口")
_abw20.close(); p.root.update()

# -- 托盘悬停提示：两排 + 标题实时同步 --------------------------------
from ez24g.ui import tray as _tr20

class _App20:
    theme_light = True
    _snap = {"k1": Reading(ok=True, level=76, name="G517 LS TKL"),
             "k2": Reading(ok=True, level=50, name="Mision-Aries Pro")}
    def snapshot(self):
        return dict(self._snap)

class _Nid20:
    uID = 1
    hIcon = 0
    szTip = ""

_t20 = _tr20.Tray.__new__(_tr20.Tray)
_t20.app = _App20()
_t20.icons = {}
_t20.icon_keys = {}
_nid20 = _Nid20()
config.set_title("")
_t20._fill_icon(_nid20, ["k1", "k2"])
_lines20 = _nid20.szTip.split("\n")
check(len(_lines20) == 2, "R20 托盘提示分成两排")
check("星火 Monitor" in _lines20[0] and "G517 LS TKL" in _lines20[0]
      and "76%" not in _lines20[0],
      "R20 第一排是标题 + 设备昵称，不含电量")
check("76%" in _lines20[1] and "50%" in _lines20[1],
      "R20 第二排是各设备电量")
config.set_title("我的电量小助手")
_t20._fill_icon(_nid20, ["k1", "k2"])
check("我的电量小助手" in _nid20.szTip.split("\n")[0],
      "R20 改了标题后提示第一排立刻跟着变，不用重启")
config.set_title("")

# -- 名称、许可、关于文案 ---------------------------------------------
check(config.APP_NAME == "星火 Monitor", "R20 中文名统一为「星火 Monitor」")
check(config.APP_NAME_EN == "Spark Monitor", "R20 英文名为 Spark Monitor")
_absrc20 = open(os.path.join(ROOT, "ez24g", "ui", "about.py"),
                encoding="utf-8").read()
check("你所见的，皆由你定" in _absrc20, "R20 关于副标题已改为「你所见的，皆由你定」")
check("MIT" in _absrc20, "R20 关于里许可写的是 MIT")
check("SnowYukikaze/Spark-Monitor" in _absrc20, "R20 关于里项目地址已更新")
check("_copy_url" in _absrc20 and "clipboard_append" in _absrc20,
      "R20 关于里项目地址做成可点击复制")
_lic20 = open(os.path.join(ROOT, "LICENSE.md"), encoding="utf-8").read()
check("MIT License" in _lic20 and "SnowYukikaze" in _lic20 and "Nier" in _lic20,
      "R20 LICENSE 已换成 MIT 并署上两位作者")

# ------------------------------------------------------------------ R21
# 启动.bat 必须是 CRLF + 纯 ASCII。cmd.exe 按 ANSI(GBK) 逐行解析批处理：
# 变成 LF 之后中文注释会被拆错行、当成命令执行，用户看到的现象就是
# 「双击一闪而过、软件打不开」——这个坑真实踩过一次。
_bat21 = os.path.join(ROOT, "启动.bat")
check(os.path.isfile(_bat21), "R21 发布包里有 启动.bat")
if os.path.isfile(_bat21):
    _raw21 = open(_bat21, "rb").read()
    check(_raw21.count(b"\r\n") > 0
          and _raw21.count(b"\n") == _raw21.count(b"\r\n"),
          "R21 启动.bat 全部是 CRLF 换行（没有裸 LF）")
    check(not [b for b in _raw21 if b > 0x7F],
          "R21 启动.bat 纯 ASCII（不会被 GBK 解码拆错行）")
    check(not _raw21.startswith(b"\xef\xbb\xbf"), "R21 启动.bat 不带 UTF-8 BOM")
    _t21 = _raw21.decode("ascii", "replace")
    check("my_devices.py" in _t21 and "pythonw" in _t21,
          "R21 启动.bat 仍然调用 my_devices.py 并搜索 pythonw")
    check('import tkinter' in _t21,
          "R21 启动.bat 会先验证解释器能 import tkinter")
    check("pause" in _t21 and "ERROR" in _t21,
          "R21 找不到解释器时会停下来给出提示，不静默退出")
_ga21 = os.path.join(ROOT, ".gitattributes")
check(os.path.isfile(_ga21)
      and "*.bat" in open(_ga21, encoding="utf-8").read(),
      "R21 有 .gitattributes，保证 clone 下来 bat 仍是 CRLF")

p.close()
shutil.rmtree(tmp, ignore_errors=True)
print("\n结果：", "全部通过" if not fails else f"{len(fails)} 项失败")
sys.exit(1 if fails else 0)
