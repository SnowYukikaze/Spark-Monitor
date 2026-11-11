# -*- coding: utf-8 -*-
"""托盘自检：真的建窗口、真的画图标、真的加进托盘再删掉。

不弹面板，跑完就退。用法：python tools/smoke_tray.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ez24g.app import TrayApp
from ez24g.model import Reading
from ez24g.ui.icon import _mask_bits, make_battery_icon, render_rgba
from ez24g.ui.tray import Tray

fails = []


def check(ok, msg):
    print(("  OK   " if ok else "  FAIL ") + msg)
    if not ok:
        fails.append(msg)


# 1) 先单独验证 GDI 图标能画出来
for lv in (None, 0, 5, 23, 55, 87, 100):
    h = make_battery_icon(lv, light=False)
    check(bool(h), f"生成 {lv}% 图标句柄 {h}")
    if h:
        import ctypes
        ctypes.windll.user32.DestroyIcon(h)

h64 = make_battery_icon(100, light=True, size=64)
check(bool(h64), "生成 64px 通知大图标")
if h64:
    import ctypes
    ctypes.windll.user32.DestroyIcon(h64)


# 1b) 像素层面：单环 / 三环（半径 12 / 8.6 / 4.2 → x = 4 / 7.4 / 11.8）
def _alpha(buf, x):
    return buf[(16 * 32 + x) * 4 + 3]


_one = render_rgba([(100, (34, 197, 94))], light=False)
_three = render_rgba([(100, (34, 197, 94)), (60, (245, 158, 11)),
                      (20, (239, 68, 68))], light=False)
check(all(_alpha(_one, x) == 0 for x in range(12, 20)), "单环：中心 8×8 全透明")
check(_alpha(_one, 3) > 0, "单环：半径 12 附近有非透明像素")
check(_alpha(_one, 7) == 0 and _alpha(_one, 12) == 0,
      "单环：内侧没有多余像素（旧几何没变）")
check(_alpha(_three, 3) > 0 and _alpha(_three, 7) > 0
      and _alpha(_three, 12) > 0, "三环：外/中/内三圈都有像素")
h3 = make_battery_icon(rings=[(100, None), (60, None), (20, None)])
check(bool(h3), "三环图标句柄生成成功")
if h3:
    import ctypes
    ctypes.windll.user32.DestroyIcon(h3)

# 1c) mask 按 alpha 生成（以前 CreateBitmap 传的是未初始化内存 → 全不透明）
_rgba_m = render_rgba([(80, None)], True, 32)
check(sum(1 for i in range(3, len(_rgba_m), 4) if _rgba_m[i] > 8) > 100,
      "单环图标有可见像素")
_bits = _mask_bits(_rgba_m, 32)
check(sum(bin(b).count("1") for b in _bits) > 0,
      f"mask 里有透明位（{sum(bin(b).count('1') for b in _bits)} 位，不再是全 0）")

# 2) 托盘窗口 + 图标
app = TrayApp(panel_only=False)
app._readings = {
    "a": Reading(ok=True, level=87, name="L8 Air", vid=0x369B, pid=0x0054,
                 present=True),
    "b": Reading(ok=True, level=12, name="USB Receiver", vid=0x046D,
                 pid=0xC547, present=True, status="charging"),
    "c": Reading(ok=False, name="没插的设备", vid=0x1111, pid=0x2222,
                 present=False),
}
tray = Tray(app)
tray.start()

deadline = time.time() + 5
while time.time() < deadline and not tray.hwnd:
    time.sleep(0.05)

check(bool(tray.hwnd), f"托盘窗口创建成功 hwnd={tray.hwnd}")
if tray.hwnd:
    time.sleep(0.3)
    tray.sync_icons()
    check(len(tray.nids) == 1, f"托盘图标只有 1 个（实际 {len(tray.nids)}）")
    keys = tray._tray_keys()
    check(keys == ["a", "b"],
          f"图标取 UI 顺序里有电量的设备（{keys}，期望 ['a', 'b']）")
    check(tray._visible_keys() == keys,
          f"_visible_keys 与 _tray_keys 一致（{tray._visible_keys()}）")
    check(all(v for v in tray.icons.values()), f"图标句柄有效 {tray.icons}")
    tip = tray.nids[1].szTip
    _tl = tip.split("\n")
    check(len(_tl) == 2
          and "L8 Air" in _tl[0] and "USB Receiver" in _tl[0]
          and "87%" in _tl[1] and "12%" in _tl[1],
          f"提示分两排（昵称一排、电量一排）且两台设备都在：{tip!r}")
    check(1 not in [uid for uid, k in tray.nids.items()
                    if k.uID in (3, 4)], "未插的设备不进托盘")

    # 三台有电量 → 三个环；第四台不显示
    app._readings = {f"m{i}": Reading(ok=True, level=90 - i * 30,
                                      name=f"设备{i}", vid=0x1400 + i,
                                      pid=0x3600 + i, present=True)
                     for i in range(4)}
    tray.sync_icons()
    _tk = tray._tray_keys()
    check(len(_tk) == 3, f"最多 3 个环（实际 {len(_tk)}）")
    check(_tk == list(app.snapshot())[:3], f"环顺序 = snapshot 顺序：{_tk}")
    check("设备3" not in tray.nids[1].szTip, "第 4 台不进图标")
    check(len(tray.nids) == 1, "仍然只有一个托盘图标")

    # 通知（要用现有的图标句柄）
    _ok_notify = tray.notify("自检通知")
    check(_ok_notify is True, f"通知调用成功：{_ok_notify}")

    # 全部读不到电量 → 灰色空环（1 个键）
    app._readings = {"z": Reading(ok=False, name="无电量设备", vid=0x1500,
                                  pid=0x3700, present=True,
                                  status="asleep")}
    tray.sync_icons()
    _vk = tray._visible_keys()
    check(len(_vk) == 1, f"全部无电量时仍保留 1 个键（{_vk}）")
    check("未连接" in tray.nids[1].szTip,
          f"空环提示未连接：{tray.nids[1].szTip}")

    # 设备都掉线（present=False）→ 图标仍在，不能删
    app._readings = {k: Reading(ok=False, present=False, status="unplugged")
                     for k in ("p1", "p2")}
    tray.sync_icons()
    check(1 in tray.nids and bool(tray.icons.get(1)),
          "设备都读不到时图标仍在（灰环）")
    check(bool(tray.nids[1].hIcon), "灰环图标句柄有效")

    # 真的一台设备都没有 → 才删图标
    app._readings = {}
    tray.sync_icons()
    check(not tray.nids, "完全没有设备时才删图标")

    tray.destroy()
    check(not tray.nids, "销毁后清空图标")

print("\n结果：", "全部通过" if not fails else f"{len(fails)} 项失败")
sys.exit(1 if fails else 0)
