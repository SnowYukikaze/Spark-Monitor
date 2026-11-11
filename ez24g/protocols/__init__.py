# -*- coding: utf-8 -*-
"""协议插件包。

目录里的每个 .py 都会被自动 import（见 base.load_plugins），
所以新增协议 = 新增文件，不用改这里。
"""
from . import base as _base
from .base import (DeviceProtocol, begin_round, describe, load_plugins,
                   read_best, register, registry, round_id)


def reset_backoff() -> None:
    """清掉所有协议层的失败退避（通用层 + 各插件自己的记忆）。

    手动点刷新 / 设备变化 = 用户想立刻重试。以前只清通用层，罗技插件
    自己那份 30 秒退避与节流清不掉，于是「多次刷新后读不到」只能靠
    重启软件恢复。
    """
    _base.reset_backoff()
    try:
        from . import logitech
        logitech.reset_state()
    except Exception:
        pass


__all__ = ["DeviceProtocol", "register", "registry", "read_best",
           "load_plugins", "describe", "reset_backoff",
           "begin_round", "round_id"]
