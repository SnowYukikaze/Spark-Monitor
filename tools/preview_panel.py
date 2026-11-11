# -*- coding: utf-8 -*-
"""临时预览：用假数据把面板显示出来，N 秒后自动关闭（供截图核对）。

用法：<带 tkinter 的 python> tools/preview_panel.py [秒数] [dark|light]
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ez24g.app as A
from ez24g.model import Reading

A.UI_SCALE = 1.0

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
mode = sys.argv[2] if len(sys.argv) > 2 else "dark"

app = A.TrayApp(panel_only=True)
app.theme_light = (mode == "light")
from ez24g.ui.panel import Panel

p = Panel(app)
p._startup_var = True

full = Reading(ok=True, level=87, name="L8 Air", vid=0x369B, pid=0x0054,
               usage_page=0xFF05, hid_known=True, source="标准 HID 电量字段",
               status="discharging")
asleep = Reading(ok=True, level=64, name="USB Receiver", vid=0x046D,
                 pid=0xC547, usage_page=0xFF00, hid_known=True, stale=True,
                 detail="上次更新 22:31 · 设备休眠")
unknown = Reading(ok=False, name="Virtual Multitouch Device", vid=0x045E,
                  pid=0x0111, usage_page=0xFA00, hid_known=True,
                  status="unknown_proto", detail="协议未知，无法读取电量")
charging = Reading(ok=True, level=23, name="MCHOSE V9 Turbo", vid=0x3837,
                   pid=0x600D, usage_page=0xFF05, hid_known=True,
                   status="charging", source="MCHOSE 协议")
app._readings = {"a": full, "b": asleep, "c": unknown, "d": charging}
app.interval = 60

p.show()
p.root.attributes("-topmost", True)
p.root.after(int(secs * 1000), p.close)
p.root.mainloop()
print("preview closed")
