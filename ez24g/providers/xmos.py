# -*- coding: utf-8 -*-
"""觅声（Misiom）这类 USB 音频设备的电量读取通道。

为什么需要它
------------
这类设备的电量不在 HID 报告里，也不是标准 HID 电量字段。它的音频接口
（注册在 KSCATEGORY_AUDIO 下）挂了一个 ``\\xmosctl`` 子设备，厂家驱动就是
通过它跟设备对话的。用 CreateFile 打开该子设备、发一条 128 字节的厂商
控制请求，设备会回 128 字节：第 0 字节回显命令码，第 1 字节是电量百分比。

命令与校验规则来自厂商官方驱动 O HUB 的公开实现逻辑，并在真机上实测通过
（连续读取稳定、每次 1–2 毫秒），不是猜的、也不是估算值。

硬约束（照用户的红线来）
------------------------
- **只读**：请求里 ``buf[24] = 1`` 表示读；本模块从不发写请求，
  不会改 DPI、音量、灯光、均衡器等任何设置；
- **只对白名单厂商生效**：换一家设备盲目发厂商命令可能被当成写命令，
  所以只认 ``WHITELIST_VID`` 里的 VID；
- **任何异常都返回 None**，由调用方回退到原逻辑，绝不影响别的设备；
- 依赖厂商音频驱动（觅声是 ``oem33.inf`` / ``shengmang-misiom-service``），
  没装驱动的机器上这里会一直返回 None，软件表现与以前完全一致。
"""
from __future__ import annotations

import sys
from typing import Optional

__all__ = ["available", "supported", "read_level", "reset_state",
           "build_request", "parse_response",
           "CMD_BATTERY", "IOCTL_USB_CONTROL", "REQ_LEN", "WHITELIST_VID"]

try:                      # 非 Windows 上 ctypes.wintypes 不可用
    import ctypes
    import ctypes.wintypes as wt
except Exception:         # pragma: no cover - 仅非 Windows
    ctypes = None
    wt = None


# 只对这些厂商启用：命令码与请求结构是厂商驱动内部的约定。
WHITELIST_VID = frozenset({0x39A5})       # 觅声 / Misiom

AUDIO_CLASS_GUID = "{6994ad04-93ef-11d0-a3cc-00a0c9223196}"   # KSCATEGORY_AUDIO
CONTROL_SUFFIX = "\\xmosctl"
IOCTL_USB_CONTROL = 0x2214D3
CMD_BATTERY = 0x87
REQ_LEN = 128

# 请求里几个固定字节：驱动按这个格式解析，改了就没人应答。
_REQ_HEAD = (0x17, 0x43, 0x17)            # buf[0] / buf[4] / buf[8]
_REQ_CMD_OFF = 12                          # 命令码位置
_REQ_READ_OFF = 24                         # 1 = 读，0 = 写

_MAX_LEVEL = 100

# (vid, pid) -> 已确认可用的设备接口路径，省掉每轮枚举注册表
_PATH: dict[tuple[int, int], str] = {}
_K32 = None


def available() -> bool:
    """当前系统能不能走这条通道。"""
    return sys.platform == "win32" and wt is not None and ctypes is not None


def supported(vid: int) -> bool:
    """这个厂商在白名单里吗。"""
    try:
        return int(vid or 0) in WHITELIST_VID
    except Exception:
        return False


def reset_state() -> None:
    """设备拔插/热插拔后清掉路径缓存，下一次重新枚举。"""
    _PATH.clear()


def build_request(cmd: int = CMD_BATTERY) -> bytearray:
    """构造 128 字节厂商请求。纯函数，方便自检逐字节校验。"""
    buf = bytearray(REQ_LEN)
    buf[0], buf[4], buf[8] = _REQ_HEAD
    buf[_REQ_CMD_OFF] = int(cmd) & 0xFF
    buf[_REQ_READ_OFF] = 1          # 恒为读：本模块没有写路径
    return buf


def parse_response(data) -> Optional[int]:
    """从设备应答里取电量。

    规则照厂家驱动的校验：第 0 字节必须回显命令码，第 1 字节是电量，
    超出 0–100 视为无效应答（驱动那边直接报「电量值无效」）。
    """
    if not data:
        return None
    try:
        buf = bytes(data)
    except Exception:
        return None
    if len(buf) < 2:
        return None
    if buf[0] != CMD_BATTERY:
        return None
    level = buf[1]
    if not 0 <= level <= _MAX_LEVEL:
        return None
    return int(level)


def _kernel():
    global _K32
    if _K32 is not None:
        return _K32
    k = ctypes.windll.kernel32
    k.CreateFileW.restype = ctypes.c_void_p
    k.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                              wt.DWORD, wt.DWORD, ctypes.c_void_p]
    k.DeviceIoControl.restype = wt.BOOL
    k.DeviceIoControl.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_void_p,
                                  wt.DWORD, ctypes.c_void_p, wt.DWORD,
                                  ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    _K32 = k
    return k


def _device_paths(vid: int, pid: int) -> list[str]:
    """枚举音频接口，挑出 VID/PID 对得上的 ``\\xmosctl`` 路径。

    路径来自注册表的 DeviceClasses：形如
    ``##?#USB#VID_39A5&PID_3D21&MI_01#8&1056b123&0&0001#{GUID}``，
    去掉开头的 ``##?#`` 换成 ``\\\\?\\`` 就是 CreateFile 能吃的名字。
    """
    try:
        import winreg
    except Exception:
        return []
    key = r"SYSTEM\CurrentControlSet\Control\DeviceClasses" + "\\" + AUDIO_CLASS_GUID
    want = "VID_%04X&PID_%04X" % (int(vid), int(pid))
    out: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            count = winreg.QueryInfoKey(k)[0]
            for i in range(count):
                try:
                    name = winreg.EnumKey(k, i)
                except OSError:
                    break
                if want not in name.upper():
                    continue
                core = name[4:] if name.startswith("##?#") else name
                out.append("\\\\?\\" + core + CONTROL_SUFFIX)
    except OSError:
        return []
    return out


def _read_one(path: str) -> Optional[int]:
    """打开一条控制通道读一次电量；任何失败都返回 None。"""
    if not available():
        return None
    k = _kernel()
    invalid = ctypes.c_void_p(-1).value
    handle = k.CreateFileW(path, 0x80000000 | 0x40000000, 0x1 | 0x2,
                           None, 3, 0, None)
    if not handle or handle == invalid:
        return None
    try:
        req = build_request()
        inbuf = (ctypes.c_ubyte * REQ_LEN).from_buffer(req)
        out = (ctypes.c_ubyte * REQ_LEN)()
        got = wt.DWORD(0)
        ok = k.DeviceIoControl(ctypes.c_void_p(handle), IOCTL_USB_CONTROL,
                               inbuf, REQ_LEN, out, REQ_LEN,
                               ctypes.byref(got), None)
        if not ok:
            return None
        return parse_response(bytes(out))
    except Exception:
        return None
    finally:
        try:
            k.CloseHandle(ctypes.c_void_p(handle))
        except Exception:
            pass


def read_level(vid: int, pid: int) -> Optional[int]:
    """读电量百分比；读不到返回 None（调用方按原逻辑走）。"""
    if not supported(vid) or not available():
        return None
    k = (int(vid), int(pid))
    cached = _PATH.get(k)
    if cached:
        level = _read_one(cached)
        if level is not None:
            return level
        # 缓存可能过期（重插后实例号变了）：清掉重新枚举一次
        _PATH.pop(k, None)
    for path in _device_paths(vid, pid):
        level = _read_one(path)
        if level is not None:
            _PATH[k] = path
            return level
    return None
