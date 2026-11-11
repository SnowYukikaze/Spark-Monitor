#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""星火 Monitor —— 启动入口。

真正的代码在 ez24g/ 包里，这个文件只负责把参数交给它：

    ez24g/
      app.py          应用编排 + 命令行
      config.py       配置读写
      hidapi.py       HID 枚举/读写
      model.py        Reading 数据模型
      devices.py      设备服务（读哪台、休眠怎么显示、上次读数兜底）
      protocols/      ★ 设备协议插件：加一个文件就多支持一款设备
      capture/        USBPcap 抓包 + 协议推断 + 提权 helper
      ui/             ★ 界面：theme.py 改配色，cards.py 改卡片，panel.py 改版式
      syswin.py       开机自启 / 快捷方式 / 系统深浅色
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 先挂上「用户私有依赖目录」再 import 界面：Pillow / winrt 装在那里，
# 挂晚了 customize 就会认为「没装 Pillow」，装过的背景功能用不了。
from ez24g import deps as _deps          # noqa: E402

try:
    _deps.ensure_path()
except Exception:
    pass

from ez24g.app import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
