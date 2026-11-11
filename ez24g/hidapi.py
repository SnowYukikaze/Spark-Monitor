# -*- coding: utf-8 -*-
"""HID 接口枚举与读写封装。

对外只有三样东西：
    HidIface   一条 HID 接口的描述（VID/PID/UsagePage/各报告长度）
    HidDevice  打开的接口句柄，提供 write_output / read_input / get_feature ...
    enumerate_hid()  当前系统里所有 HID 接口

协议插件只应该用这三样，不要直接碰 ctypes。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import dataclasses
from typing import Optional

from . import win32 as W
from .win32 import HIDD_ATTRIBUTES, HIDP_CAPS


class HidError(Exception):
    """HID 打开/读写失败。协议插件捕获它并转成 Reading(ok=False)。"""


# 同一个接收器同一时间只能被一个程序占用（罗技 G HUB / Options+ 常驻时
# 就会抢）。写通道打不开时给界面一句看得懂的话，而不是「错误码 5」。
WRITE_BUSY_HINT = ("接收器正被其它程序占用（例如罗技 G HUB / Options+），"
                   "暂时读不到电量；退出该程序或稍后会自动重试")


@dataclasses.dataclass
class HidIface:
    path: str
    vid: int
    pid: int
    usage_page: int
    usage: int
    input_len: int
    output_len: int
    feature_len: int
    product: str = ""
    manufacturer: str = ""
    instance: str = ""
    parent: str = ""          # 父设备实例：USB\VID_... 或 BTHENUM\... / BTHLEDEVICE\...
    vendor_hint: str = ""     # 从厂商名/产品名归一出的厂商族，用于选协议

    @property
    def label(self) -> str:
        return (f"{self.vid:04X}:{self.pid:04X} "
                f"UP={self.usage_page:04X} U={self.usage:04X} "
                f"in={self.input_len} out={self.output_len} "
                f"feat={self.feature_len}")

    @property
    def is_vendor(self) -> bool:
        return self.usage_page >= 0xFF00

    @property
    def is_bluetooth(self) -> bool:
        """挂在蓝牙上的 HID 接口（经典蓝牙 BTHENUM 或 BLE 的 GATT HID）。"""
        p = (self.parent or "").upper()
        return p.startswith(("BTHENUM", "BTHLE", "BTHLEDEVICE"))


def _open_path(path: str, access: int, overlapped: bool = False) -> int:
    flags = W.FILE_FLAG_OVERLAPPED if overlapped else 0
    return W.kernel32.CreateFileW(path, access, W.FILE_SHARE_RW, None,
                                  W.OPEN_EXISTING, flags, None)


def _devprop_string(hdev, devinfo, key) -> str:
    """读一个字符串型的设备属性（这里只用来取父设备实例）。"""
    buf = ctypes.create_unicode_buffer(512)
    need = wt.DWORD(0)
    ptype = wt.ULONG(0)
    try:
        ok = W.setupapi.SetupDiGetDevicePropertyW(
            hdev, ctypes.byref(devinfo), ctypes.byref(key),
            ctypes.byref(ptype), buf, ctypes.sizeof(buf),
            ctypes.byref(need), 0)
    except Exception:
        return ""
    return buf.value if ok else ""


def enumerate_hid() -> list[HidIface]:
    """枚举所有 HID 接口。设备不在时返回空列表，不抛异常。"""
    hdev = W.setupapi.SetupDiGetClassDevsW(
        ctypes.byref(W.GUID_DEVINTERFACE_HID), None, None,
        W.DIGCF_PRESENT | W.DIGCF_DEVICEINTERFACE)
    if hdev == W.INVALID_HANDLE_VALUE or not hdev:
        return []
    out: list[HidIface] = []
    try:
        idx = 0
        while True:
            did = W.SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            if not W.setupapi.SetupDiEnumDeviceInterfaces(
                    hdev, None, ctypes.byref(W.GUID_DEVINTERFACE_HID), idx,
                    ctypes.byref(did)):
                break
            idx += 1
            need = wt.DWORD(0)
            W.setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(did), None, 0, ctypes.byref(need), None)
            if not need.value:
                continue
            buf = ctypes.create_string_buffer(need.value)
            ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0] = 8
            # 第六个参数回填 DevInfo，有了它才能问「父设备是谁」
            devinfo = W.SP_DEVINFO_DATA()
            devinfo.cbSize = ctypes.sizeof(devinfo)
            if not W.setupapi.SetupDiGetDeviceInterfaceDetailW(
                    hdev, ctypes.byref(did), buf, need.value,
                    ctypes.byref(need), ctypes.byref(devinfo)):
                continue
            # DevicePath 紧跟在 cbSize(DWORD) 之后：偏移 4
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            inst = ctypes.create_unicode_buffer(512)
            inst_len = wt.DWORD(0)
            W.setupapi.SetupDiGetDeviceInstanceIdW(
                hdev, ctypes.byref(devinfo), inst, 512, ctypes.byref(inst_len))
            parent = _devprop_string(hdev, devinfo, W.DEVPKEY_Device_Parent)

            h = _open_path(path, 0)
            if not h or h == W.INVALID_HANDLE_VALUE:
                continue
            try:
                attrs = HIDD_ATTRIBUTES()
                attrs.Size = ctypes.sizeof(attrs)
                W.hid.HidD_GetAttributes(h, ctypes.byref(attrs))
                caps = HIDP_CAPS()
                pp = ctypes.c_void_p()
                if W.hid.HidD_GetPreparsedData(h, ctypes.byref(pp)):
                    W.hid.HidP_GetCaps(pp, ctypes.byref(caps))
                    W.hid.HidD_FreePreparsedData(pp)
                prod = ctypes.create_unicode_buffer(256)
                W.hid.HidD_GetProductString(h, prod, 512)
                manu = ctypes.create_unicode_buffer(256)
                W.hid.HidD_GetManufacturerString(h, manu, 512)
                meta = ((manu.value or "") + " " + (prod.value or "")).lower()
                hint = ""
                for word, family in (("logitech", "logitech"), ("罗技", "logitech"),
                                     ("atk", "atk"), ("mchose", "mchose"),
                                     ("jieli", "jieli"), ("杰理", "jieli"),
                                     ("endgame", "endgame"), ("vaxee", "vaxee"),
                                     ("nordic", "nordic")):
                    if word in meta:
                        hint = family
                        break
                out.append(HidIface(
                    path=path, vid=attrs.VendorID, pid=attrs.ProductID,
                    usage_page=caps.UsagePage, usage=caps.Usage,
                    input_len=caps.InputReportByteLength,
                    output_len=caps.OutputReportByteLength,
                    feature_len=caps.FeatureReportByteLength,
                    product=prod.value, manufacturer=manu.value,
                    instance=inst.value, parent=parent,
                    vendor_hint=hint))
            finally:
                W.kernel32.CloseHandle(h)
    finally:
        W.setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out


class HidDevice:
    """一条已打开的 HID 接口。

    读句柄单独用 FILE_FLAG_OVERLAPPED 打开：绝不在同一句柄上并发阻塞式
    ReadFile 与 WriteFile/IOCTL（实测会卡死进程）。
    """

    def __init__(self, path: str, write: bool = False):
        self.path = path
        self.write_mode = write
        self.h = 0
        self.rh = 0
        access = W.GENERIC_READ | (W.GENERIC_WRITE if write else 0)
        self.h = _open_path(path, access)
        if not self.h or self.h == W.INVALID_HANDLE_VALUE:
            err = ctypes.get_last_error()
            if write:
                # 带写权限打不开（常被厂商驱动临时占着）时，至少保留读权限：
                # 后面仍可用 HidD_SetOutputReport 发报告，不必整轮放弃。
                self.h = _open_path(path, W.GENERIC_READ)
            if not self.h or self.h == W.INVALID_HANDLE_VALUE:
                raise HidError(f"打开接口失败（错误码 {err}）")
            self.write_mode = False
        self.rh = _open_path(path, W.GENERIC_READ, overlapped=True)
        if not self.rh or self.rh == W.INVALID_HANDLE_VALUE:
            self.rh = 0

    # -- 生命周期 ---------------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        for h in (self.rh, self.h):
            if h and h != W.INVALID_HANDLE_VALUE:
                W.kernel32.CloseHandle(h)
        self.rh = 0
        self.h = 0

    # -- 写 ---------------------------------------------------------------
    def write_output(self, data: bytes) -> int:
        buf = ctypes.create_string_buffer(data, len(data))
        written = wt.DWORD(0)
        ok = W.kernel32.WriteFile(self.h, buf, len(data),
                                  ctypes.byref(written), None)
        if not ok:
            # 句柄降级成「只读」时 WriteFile 必失败；HID 类驱动通常仍允许
            # 用 IOCTL 发 Output Report，再给它一次机会。罗技接收器被自家
            # 驱动临时占用就是这种情形，以前这一轮会静默读不到电量。
            if self.set_output_report(data):
                return len(data)
            if not getattr(self, "write_mode", True):
                raise HidError(WRITE_BUSY_HINT)
            raise HidError(f"写入失败（错误码 {ctypes.get_last_error()}）")
        return written.value

    def set_feature(self, data: bytes) -> bool:
        buf = ctypes.create_string_buffer(data, len(data))
        return bool(W.hid.HidD_SetFeature(self.h, buf, len(data)))

    def set_output_report(self, data: bytes) -> bool:
        buf = ctypes.create_string_buffer(data, len(data))
        return bool(W.hid.HidD_SetOutputReport(self.h, buf, len(data)))

    # -- 读 ---------------------------------------------------------------
    def read_input(self, length: int, timeout_ms: int = 800) -> Optional[bytes]:
        if not self.rh:
            return None
        ev = W.kernel32.CreateEventW(None, True, False, None)
        if not ev:
            return None
        try:
            ov = W.OVERLAPPED()
            ov.hEvent = ev
            buf = ctypes.create_string_buffer(length)
            got = wt.DWORD(0)
            ok = W.kernel32.ReadFile(self.rh, buf, length,
                                     ctypes.byref(got), ctypes.byref(ov))
            if not ok and ctypes.get_last_error() != W.ERROR_IO_PENDING:
                return None
            if W.kernel32.WaitForSingleObject(ev, timeout_ms) == W.WAIT_TIMEOUT:
                W.kernel32.CancelIoEx(self.rh, ctypes.byref(ov))
                return None
            W.kernel32.GetOverlappedResult(self.rh, ctypes.byref(ov),
                                           ctypes.byref(got), False)
            return bytes(buf.raw[:got.value])
        finally:
            W.kernel32.CloseHandle(ev)

    def get_feature(self, length: int) -> Optional[bytes]:
        buf = ctypes.create_string_buffer(length)
        if W.hid.HidD_GetFeature(self.h, buf, length):
            return bytes(buf.raw[:length])
        return None

    def get_feature_report(self, report_id: int, length: int) -> Optional[bytes]:
        buf = ctypes.create_string_buffer(length)
        ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))[0] = report_id & 0xFF
        if W.hid.HidD_GetFeature(self.h, buf, length):
            return bytes(buf.raw[:length])
        return None

    def drain(self):
        """丢弃缓冲区里的陈旧输入报告，避免读到上一次的回复。"""
        while self.rh and self.read_input(256, 15):
            pass

    # -- 报文描述符 -------------------------------------------------------
    def value_caps(self, kind: int) -> list:
        pp = ctypes.c_void_p()
        if not W.hid.HidD_GetPreparsedData(self.h, ctypes.byref(pp)):
            return []
        try:
            n = wt.USHORT(0)
            W.hid.HidP_GetValueCaps(kind, None, ctypes.byref(n), pp)
            if not n.value:
                return []
            arr = (W.HIDP_VALUE_CAPS * n.value)()
            if not W.hid.HidP_GetValueCaps(kind, arr, ctypes.byref(n), pp):
                return []
            return list(arr[:n.value])
        finally:
            W.hid.HidD_FreePreparsedData(pp)

    def usage_value(self, kind: int, report: bytes, cap) -> Optional[int]:
        pp = ctypes.c_void_p()
        if not W.hid.HidD_GetPreparsedData(self.h, ctypes.byref(pp)):
            return None
        try:
            val = wt.ULONG(0)
            buf = ctypes.create_string_buffer(report, len(report))
            usage = (cap.u.NotRange.Usage if not cap.IsRange
                     else cap.u.Range.UsageMin)
            ok = W.hid.HidP_GetUsageValue(kind, cap.UsagePage,
                                          cap.LinkCollection, usage,
                                          ctypes.byref(val), pp,
                                          ctypes.byref(cap), buf)
            return val.value if ok else None
        finally:
            W.hid.HidD_FreePreparsedData(pp)
