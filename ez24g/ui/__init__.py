# -*- coding: utf-8 -*-
"""界面层：主题、控件、面板、卡片、托盘、抓包向导。

这一层不认识 HID，也不认识协议，只消费 Reading 并调用 app 的方法。

注意：这里不做 eager import。命令行模式（--once / --list）不需要 tkinter，
所以具体模块由调用方按需 import。
"""
