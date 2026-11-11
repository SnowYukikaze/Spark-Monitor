# -*- coding: utf-8 -*-
"""面板几何自检（760×560 两列网格版）：不靠眼睛，直接量画布元素。

检查项：
- 元素不越界；头部遮罩在卡片之后绘制
- 两列网格：列宽相等、x 对称、同排不重叠、行间不重叠
- 头部三个按钮都在窗口内、互不重叠、且不压标题
- 卡片内文字不超出卡片宽度；电量数字右对齐
- 空状态 / 多设备滚动 / 来源标签

用法：<带 tkinter 的 python> tools/smoke_layout.py
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 用户真实配置里可能已经设了背景图 / 自定义颜色，那会让几何断言失效
# （比如头部改成贴图就没有纯色遮罩矩形）。自检必须跑在干净的配置上。
_TMP = tempfile.mkdtemp(prefix="ez24g_layout_")
os.environ["APPDATA"] = _TMP
os.makedirs(os.path.join(_TMP, "BatteryTray"), exist_ok=True)

import ez24g.app as A
from ez24g.model import Reading

A.UI_SCALE = 1.0
app = A.TrayApp(panel_only=True)
from ez24g.ui.panel import Panel, LAYOUT

fails = []


def check(ok, msg):
    print(("  OK   " if ok else "  FAIL ") + msg)
    if not ok:
        fails.append(msg)


p = Panel(app)
readings = {
    "a": Reading(ok=True, level=87, name="L8 Air", vid=0x369B, pid=0x0054,
                 usage_page=0xFF05, hid_known=True),
    "b": Reading(ok=True, level=64, name="USB Receiver", vid=0x046D,
                 pid=0xC547, usage_page=0xFF00, hid_known=True, stale=True,
                 detail="上次更新 22:31 · 设备休眠"),
    "c": Reading(ok=False, name="Virtual Multitouch Device", vid=0x045E,
                 pid=0x0111, usage_page=0xFA00, hid_known=True,
                 status="unknown_proto", detail="协议未知，无法读取电量"),
}
app._readings = readings
p.render()
p.root.update()

W, H = p.W, p.H
print(f"窗口 {W}x{H}，画布元素 {len(p.cv.find_all())} 个")


def texts():
    return [p.cv.itemcget(i, "text") for i in p.cv.find_all()
            if p.cv.type(i) == "text"]


# 1) 不越界
outside = [(p.cv.type(i), p.cv.bbox(i)) for i in p.cv.find_all()
           if p.cv.bbox(i) and (p.cv.bbox(i)[0] < -2 or p.cv.bbox(i)[2] > W + 2)]
check(not outside, f"所有元素水平方向在窗口内（越界 {len(outside)} 个）")

# 1b) 头部遮罩必须在卡片之后
order = {item: i for i, item in enumerate(p.cv.find_all())}
card_ids = []
for item in p.cv.find_all():
    if p.cv.type(item) != "polygon":
        continue
    bb = p.cv.bbox(item)
    if not bb:
        continue
    for (x1, y1, x2, y2) in p._card_rects.values():
        if (abs(bb[0] - x1) <= 2 and abs(bb[1] - y1) <= 2
                and abs(bb[2] - x2) <= 2 and abs(bb[3] - y2) <= 2):
            card_ids.append(item)
            break
# 头部遮罩可能是纯色矩形，也可能是有软件背景时贴的同图裁片
header_masks = [i for i in p.cv.find_all()
                if p.cv.type(i) in ("rectangle", "image") and p.cv.bbox(i)
                and -2 <= p.cv.bbox(i)[1] <= 2 and p.cv.bbox(i)[3] <= 92]
check(bool(header_masks), "存在头部遮罩矩形")
check(len(card_ids) >= len(p._card_rects),
      f"每张卡片都画了底板（{len(card_ids)}/{len(p._card_rects)}）")
if header_masks and card_ids:
    check(min(order[m] for m in header_masks) > max(order[c] for c in card_ids),
          "头部遮罩在卡片之后绘制（z 序正确）")

# 2) 两列网格
rects = p._card_rects
check(len(rects) == 3, f"3 台设备 3 张卡都在视口内（{len(rects)}）")
rows = {}
for (x1, y1, x2, y2) in rects.values():
    rows.setdefault(round(y1), []).append((x1, y1, x2, y2))
check(len(rows) == 2, f"3 台设备排成 2 行（实际 {len(rows)} 行）")
for y, items in rows.items():
    if len(items) == 2:
        check(items[0][2] < items[1][0],
              f"同一行两列不重叠（{items[0][2]} < {items[1][0]}）")
row_ys = sorted(rows)
if len(row_ys) >= 2:
    check(rows[row_ys[0]][0][3] < rows[row_ys[1]][0][1], "相邻两行不重叠")

# 3) 列对称 / 留白
lefts = sorted({round(v[0]) for v in rects.values()})
rights = sorted({round(v[2]) for v in rects.values()})
pad = LAYOUT["pad"]
card_w = (W - 2 * pad - LAYOUT["card_gap"]) / 2
check(lefts == [pad, W - pad - card_w], f"两列左边距 {lefts}")
check(rights == [pad + card_w, W - pad], f"两列右边距 {rights}")
# 两列等宽
widths = {round(v[2] - v[0]) for v in rects.values()}
check(len(widths) == 1, f"两列等宽（{widths}）")
# 左右留白对称：第一列到左边界 == 第二列到右边界
check(abs((lefts[0] - 0) - (W - rights[-1])) <= 1,
      f"左右留白对称（{lefts[0]} / {W - rights[-1]}）")

# 4) 头部三个按钮
for name in ("_btn_refresh", "_btn_add", "_btn_more", "_btn_close", "_btn_min"):
    if getattr(p, name, None) is None:
        check(False, f"缺少按钮 {name}")
boxes = {}
for name in ("_btn_refresh", "_btn_add", "_btn_more"):
    b = getattr(p, name, None)
    if b is not None:
        boxes[name] = p.cv.bbox(b.tag)
check(all(b is not None for b in boxes.values()), f"头部按钮已绘制：{boxes}")
if len(boxes) == 3:
    r, a, m = boxes["_btn_refresh"], boxes["_btn_add"], boxes["_btn_more"]
    check(r[2] <= a[0] and a[2] <= m[0], "三个按钮不重叠")
    cb = p.cv.bbox(p._btn_close.tag)
    check(bool(cb) and cb[2] <= W - pad + 3, f"× 在窗口内（{cb}）")
    check(abs(r[1] - a[1]) <= 1 and abs(a[1] - m[1]) <= 1, "三个按钮同一基线")
    # 标题不压按钮
    tt = p.cv.bbox(p._title_tag)
    if tt:
        check(tt[2] <= r[0] - 8, f"标题右端 {tt[2]} 不压「立即刷新」{r[0]}")

# 4b) 副标题不再带「N 休眠」
subs = [t for t in texts() if "台 ·" in t]
check(bool(subs) and all("休眠" not in t for t in subs),
      f"副标题不含休眠计数：{subs}")

# 5) 卡片内文字不超卡片宽度；电量右对齐
card_w = (W - 2 * pad - LAYOUT["card_gap"]) / 2
wide = []
for item in p.cv.find_all():
    if p.cv.type(item) != "text":
        continue
    bb = p.cv.bbox(item)
    if bb and (bb[2] - bb[0]) > card_w - 24:
        wide.append((p.cv.itemcget(item, "text")[:30], bb[2] - bb[0]))
check(not wide, f"卡片文字不超内宽 {card_w - 24:.0f}（{len(wide)} 条）")

lv_boxes = [p.cv.bbox(i) for i in p.cv.find_all()
            if p.cv.type(i) == "text"
            and str(p.cv.itemcget(i, "text")).endswith("%")]
check(len(lv_boxes) >= 2, f"有电量数字 {len(lv_boxes)} 个")
if lv_boxes:
    right_edges = sorted({round(b[2]) for b in lv_boxes})
    check(len(right_edges) <= 2, f"电量数字按列右对齐（{right_edges}）")

# 6) 空状态：首轮没读完 → 「正在读取」；读完了确实没设备 → 「还没有设备」
app._readings = {}
app._ever = False
p.render()
p.root.update()
check(len(p.cv.find_all()) > 5, "空状态有占位卡")
check(any("正在读取设备" in t for t in texts()),
      "首轮未完成时显示「正在读取设备」")
app._ever = True
p.render()
p.root.update()
check("还没有设备" in texts(), "空状态文案正确")
empty_out = [p.cv.bbox(i) for i in p.cv.find_all()
             if p.cv.bbox(i) and (p.cv.bbox(i)[0] < -2 or p.cv.bbox(i)[2] > W + 2)]
check(not empty_out, f"空状态元素不越界（{empty_out[:2]}）")

# 7) 多设备滚动
app._readings = {f"k{i}": Reading(ok=True, level=i * 10 % 100, name=f"设备{i}",
                                 vid=0x1000 + i, pid=0x2000 + i,
                                 usage_page=0xFF00, hid_known=True)
                 for i in range(8)}
p.render()
p.root.update()
check(p._max_scroll > 0, f"8 台设备时出现滚动（max_scroll={p._max_scroll:.0f}）")
p._scroll = p._max_scroll
p.render()
p.root.update()
side_out = [p.cv.bbox(i) for i in p.cv.find_all()
            if p.cv.bbox(i) and (p.cv.bbox(i)[0] < -2 or p.cv.bbox(i)[2] > W + 2)]
check(not side_out, f"滚到底部没有横向越界（{side_out[:2]}）")

# 8) 来源标签 & 状态语义
p._scroll = 0.0
app._readings = {"bt:DE:1B:B4:46:93:66": Reading(
    ok=True, level=63, name="G517 LS TKL", transport="bt",
    addr="DE:1B:B4:46:93:66")}
p.render()
p.root.update()
tx = texts()
check(any("DE:1B:B4:46:93:66 · 蓝牙" in t for t in tx), "蓝牙卡片型号行正确")
check(any(t == "已连接" for t in tx), "已连接状态文字")
check(any(t.endswith("63%") for t in tx), "电量显示 63%")

app._readings = {"x369B0054": Reading(
    ok=True, level=87, name="L8 Air", vid=0x369B, pid=0x0054,
    usage_page=0xFF05, hid_known=True)}
p.render()
p.root.update()
tx = texts()
check(any("369B:0054 · 2.4G" in t for t in tx), "2.4G 卡片型号行正确")

# 断开：不能显示旧数字
app._readings = {"x369B0054": Reading(
    ok=False, present=False, status="unplugged", name="L8 Air",
    vid=0x369B, pid=0x0054, detail="未连接")}
p.render()
p.root.update()
tx = texts()
check("未连接" in tx, "断开显示「未连接」")
check(not any(t.endswith("%") for t in tx), "断开不显示电量数字")

p.close()
print("\n结果：", "全部通过" if not fails else f"{len(fails)} 项失败")
sys.exit(1 if fails else 0)

shutil.rmtree(_TMP, ignore_errors=True)
