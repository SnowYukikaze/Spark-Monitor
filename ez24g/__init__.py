# -*- coding: utf-8 -*-
"""2.4G 外设电量：Windows 10/11 无线外设电量托盘工具。

分层：
    协议插件 protocols/    怎么跟某一款设备说话（加设备 = 加文件）
    设备服务 devices.py    读哪台、休眠怎么显示、上次读数怎么兜底
    界面     ui/           主题、卡片、面板、托盘
    编排     app.py        线程、热插拔、命令行
"""
from .model import Reading

__version__ = "2.0.0"
__all__ = ["Reading", "__version__"]
