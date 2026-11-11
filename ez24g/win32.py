# -*- coding: utf-8 -*-
"""Win32 底层：DLL 句柄、结构体、函数原型、消息常量。

这个模块只做一件事——把 ctypes 的定义集中起来。
任何业务逻辑都不写在这里，改 Win32 调用只改这一个文件。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

# ---------------------------------------------------------------------------
# DLL
# ---------------------------------------------------------------------------
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
imm32 = ctypes.WinDLL("imm32", use_last_error=True)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, s: str = ""):
        super().__init__()
        self.parse(s or "{00000000-0000-0000-0000-000000000000}")

    def parse(self, s: str):
        s = s.strip().strip("{}")
        parts = s.split("-", 3)
        self.Data1 = int(parts[0], 16)
        self.Data2 = int(parts[1], 16)
        self.Data3 = int(parts[2], 16)
        rest = bytes.fromhex(parts[3].replace("-", ""))
        for i in range(8):
            self.Data4[i] = rest[i]

    def as_str(self) -> str:
        return ("{%08X-%04X-%04X-%02X%02X-%02X%02X%02X%02X%02X%02X}"
                % (self.Data1, self.Data2, self.Data3,
                   self.Data4[0], self.Data4[1], self.Data4[2], self.Data4[3],
                   self.Data4[4], self.Data4[5], self.Data4[6], self.Data4[7]))


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wt.ULONG), ("VendorID", wt.USHORT),
                ("ProductID", wt.USHORT), ("VersionNumber", wt.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wt.USHORT), ("UsagePage", wt.USHORT),
        ("InputReportByteLength", wt.USHORT),
        ("OutputReportByteLength", wt.USHORT),
        ("FeatureReportByteLength", wt.USHORT),
        ("Reserved", wt.USHORT * 17),
        ("NumberLinkCollectionNodes", wt.USHORT),
        ("NumberInputButtonCaps", wt.USHORT),
        ("NumberInputValueCaps", wt.USHORT),
        ("NumberInputDataIndices", wt.USHORT),
        ("NumberOutputButtonCaps", wt.USHORT),
        ("NumberOutputValueCaps", wt.USHORT),
        ("NumberOutputDataIndices", wt.USHORT),
        ("NumberFeatureButtonCaps", wt.USHORT),
        ("NumberFeatureValueCaps", wt.USHORT),
        ("NumberFeatureDataIndices", wt.USHORT),
    ]


class HIDP_RANGE(ctypes.Structure):
    _fields_ = [("UsageMin", wt.USHORT), ("UsageMax", wt.USHORT),
                ("StringMin", wt.USHORT), ("StringMax", wt.USHORT),
                ("DesignatorMin", wt.USHORT), ("DesignatorMax", wt.USHORT),
                ("DataIndexMin", wt.USHORT), ("DataIndexMax", wt.USHORT)]


class HIDP_NOTRANGE(ctypes.Structure):
    _fields_ = [("Usage", wt.USHORT), ("Reserved1", wt.USHORT),
                ("StringIndex", wt.USHORT), ("Reserved2", wt.USHORT),
                ("DesignatorIndex", wt.USHORT), ("Reserved3", wt.USHORT),
                ("DataIndex", wt.USHORT), ("Reserved4", wt.USHORT)]


class HIDP_UNION(ctypes.Union):
    _fields_ = [("Range", HIDP_RANGE), ("NotRange", HIDP_NOTRANGE)]


class HIDP_VALUE_CAPS(ctypes.Structure):
    _fields_ = [
        ("UsagePage", wt.USHORT), ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte), ("BitField", wt.USHORT),
        ("LinkCollection", wt.USHORT), ("LinkUsage", wt.USHORT),
        ("LinkUsagePage", wt.USHORT), ("IsRange", ctypes.c_ubyte),
        ("IsStringRange", ctypes.c_ubyte), ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte), ("HasNull", ctypes.c_ubyte),
        ("Reserved", ctypes.c_ubyte), ("BitSize", wt.USHORT),
        ("ReportCount", wt.USHORT), ("Reserved2", wt.USHORT * 5),
        ("UnitsExp", wt.ULONG), ("Units", wt.ULONG),
        ("LogicalMin", wt.LONG), ("LogicalMax", wt.LONG),
        ("PhysicalMin", wt.LONG), ("PhysicalMax", wt.LONG),
        ("u", HIDP_UNION),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wt.DWORD), ("Reserved", ctypes.c_void_p)]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("ClassGuid", GUID),
                ("DevInst", wt.DWORD), ("Reserved", ctypes.c_void_p)]


class DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wt.ULONG)]


# DEVPKEY_Device_Parent：用来判断一条 HID 接口挂在 USB 上还是蓝牙上
DEVPKEY_Device_Parent = DEVPROPKEY()
DEVPKEY_Device_Parent.fmtid = GUID("{4340A6C5-93FA-4706-972C-7B648008A5A7}")
DEVPKEY_Device_Parent.pid = 8

# DEVPKEY_Device_FriendlyName：设备显示名（"xxx Hands-Free AG"）
DEVPKEY_Device_FriendlyName = DEVPROPKEY()
DEVPKEY_Device_FriendlyName.fmtid = GUID("{A45C254E-DF1C-4EFD-8020-67D146A850E0}")
DEVPKEY_Device_FriendlyName.pid = 14

# 未公开蓝牙电量：Windows 把 HFP 耳机上次上报的电量缓存在 Hands-Free AG 节点上。
# 类型是 DEVPROP_TYPE_BYTE（3），不是 BOOL（17），也不是 STRING。
DEVPKEY_BT_Battery = DEVPROPKEY()
DEVPKEY_BT_Battery.fmtid = GUID("{104EA319-6EE2-4701-BD47-8DDBF425BBE5}")
DEVPKEY_BT_Battery.pid = 2


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                ("Offset", wt.DWORD), ("OffsetHigh", wt.DWORD),
                ("hEvent", wt.HANDLE)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT,
                             wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
                ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT),
                ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
                ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", wt.HICON)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", wt.BYTE), ("BlendFlags", wt.BYTE),
                ("SourceConstantAlpha", wt.BYTE), ("AlphaFormat", wt.BYTE)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD),
                ("yHotspot", wt.DWORD), ("hbmMask", wt.HBITMAP),
                ("hbmColor", wt.HBITMAP)]


class ACCENTPOLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", wt.DWORD), ("AnimationId", ctypes.c_int)]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t)]


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("fMask", ctypes.c_ulong),
        ("hwnd", wt.HWND), ("lpVerb", wt.LPCWSTR),
        ("lpFile", wt.LPCWSTR), ("lpParameters", wt.LPCWSTR),
        ("lpDirectory", wt.LPCWSTR), ("nShow", ctypes.c_int),
        ("hInstApp", wt.HINSTANCE), ("lpIDList", ctypes.c_void_p),
        ("lpClass", wt.LPCWSTR), ("hkeyClass", wt.HKEY),
        ("dwHotKey", wt.DWORD), ("hIcon", wt.HANDLE),
        ("hProcess", wt.HANDLE),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong)]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wt.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wt.DWORD),
                ("SchedulingClass", wt.DWORD)]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
INVALID_HANDLE_VALUE = wt.HANDLE(-1).value
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_RW = 0x00000001 | 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
ERROR_IO_PENDING = 997
ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
CREATE_NO_WINDOW = 0x08000000

GUID_DEVINTERFACE_HID = GUID("{4D1E55B2-F16F-11CF-88CB-001111000030}")
GUID_DEVCLASS_SYSTEM = GUID("{4D36E97D-E325-11CE-BFC1-08002BE10318}")
DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10

# DEVPROP_TYPE_*（DEVPROPKEY 的返回类型）
DEVPROP_TYPE_EMPTY = 0x00000000
DEVPROP_TYPE_BYTE = 0x00000003
DEVPROP_TYPE_STRING = 0x00000012

HIDP_INPUT = 0
HIDP_OUTPUT = 1
HIDP_FEATURE = 2

WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_APP_REFRESH = WM_APP + 2
WM_APP_SHOW = WM_APP + 3
WM_APP_INTERVAL = WM_APP + 4
WM_TIMER = 0x0113
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_SETTINGCHANGE = 0x001A
WM_DEVICECHANGE = 0x0219
DBT_DEVNODES_CHANGED = 0x0007
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 1
NIF_ICON = 2
NIF_TIP = 4
NIF_INFO = 0x10
NIIF_INFO = 0x1
NIIF_USER = 0x4
NIIF_LARGE_ICON = 0x20

GCL_STYLE = -26
CS_DROPSHADOW = 0x00020000

# 窗口层级（§1 置顶工具 + 菜单/弹窗压住 topmost 面板）
GWL_EXSTYLE = -20
GWL_HWNDPARENT = -8
HWND_TOPMOST = wt.HWND(-1)
HWND_NOTOPMOST = wt.HWND(-2)
WS_EX_TOPMOST = 0x00000008
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040


# ---------------------------------------------------------------------------
# 函数原型
# ---------------------------------------------------------------------------
kernel32.CreateFileW.restype = wt.HANDLE
kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD,
                                 ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                               ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
kernel32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                              ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
kernel32.CreateEventW.restype = wt.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.GetOverlappedResult.argtypes = [wt.HANDLE, ctypes.c_void_p,
                                         ctypes.POINTER(wt.DWORD), wt.BOOL]
kernel32.CancelIoEx.argtypes = [wt.HANDLE, ctypes.c_void_p]
kernel32.ResetEvent.argtypes = [wt.HANDLE]
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]

user32.SetTimer.argtypes = [wt.HWND, wt.UINT, wt.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wt.HWND, wt.UINT]
user32.GetClassLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetClassLongPtrW.restype = ctypes.c_size_t
user32.SetClassLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_size_t]
user32.SetClassLongPtrW.restype = ctypes.c_size_t
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wt.HWND, wt.HMENU,
                                   wt.HINSTANCE, ctypes.c_void_p]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.RegisterWindowMessageW.restype = wt.UINT
user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SetProcessDPIAware.argtypes = []
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.UpdateLayeredWindow.argtypes = [
    wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE),
    wt.HDC, ctypes.POINTER(wt.POINT), wt.DWORD,
    ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
user32.UpdateLayeredWindow.restype = wt.BOOL
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.CreateIconIndirect.restype = wt.HICON
user32.DestroyIcon.argtypes = [wt.HICON]
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.FillRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]
user32.SetWindowRgn.argtypes = [wt.HWND, wt.HRGN, wt.BOOL]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.BringWindowToTop.restype = wt.BOOL
user32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
user32.SetWindowDisplayAffinity.restype = wt.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = wt.HWND

# 前台窗口 / 进程（截屏工具识别用，见 syswin.capture_tool_active）
user32.GetForegroundWindow.restype = wt.HWND
user32.GetForegroundWindow.argtypes = []
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wt.HWND,
                                            ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.GetWindowLongW.restype = ctypes.c_long
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD,
                                                wt.LPWSTR,
                                                ctypes.POINTER(wt.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL

setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR,
                                          wt.HWND, wt.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), wt.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wt.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wt.HANDLE]
setupapi.SetupDiEnumDeviceInfo.argtypes = [
    wt.HANDLE, wt.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)]
setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
    wt.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), wt.LPWSTR, wt.DWORD,
    ctypes.POINTER(wt.DWORD)]
setupapi.SetupDiGetDevicePropertyW.argtypes = [
    wt.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.POINTER(DEVPROPKEY),
    ctypes.POINTER(wt.ULONG), ctypes.c_void_p, wt.DWORD,
    ctypes.POINTER(wt.DWORD), wt.DWORD]

hid.HidD_GetAttributes.argtypes = [wt.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidD_GetProductString.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_GetManufacturerString.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_GetFeature.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_SetFeature.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_SetOutputReport.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidP_GetValueCaps.argtypes = [ctypes.c_int, ctypes.POINTER(HIDP_VALUE_CAPS),
                                  ctypes.POINTER(wt.USHORT), ctypes.c_void_p]
hid.HidP_GetUsageValue.argtypes = [ctypes.c_int, wt.USHORT, wt.USHORT, wt.USHORT,
                                   ctypes.POINTER(wt.ULONG), ctypes.c_void_p,
                                   ctypes.POINTER(HIDP_VALUE_CAPS), ctypes.c_void_p]

shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL

dwmapi.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p,
                                         wt.DWORD]

gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE,
                                   wt.DWORD]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wt.UINT, wt.UINT,
                               ctypes.c_void_p]
gdi32.CreateBitmap.restype = wt.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.CreateSolidBrush.argtypes = [wt.COLORREF]
gdi32.CreateSolidBrush.restype = wt.HBRUSH
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.GetStockObject.restype = wt.HGDIOBJ
gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wt.COLORREF]
gdi32.CreatePen.restype = wt.HPEN
gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.CreateRoundRectRgn.restype = wt.HRGN
gdi32.RoundRect.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.Ellipse.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int]
gdi32.Arc.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                      ctypes.c_int]
gdi32.Rectangle.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int]
gdi32.Polygon.argtypes = [wt.HDC, ctypes.POINTER(wt.POINT), ctypes.c_int]
gdi32.GetPixel.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.GetPixel.restype = wt.COLORREF
# 桌面快照（ui/backdrop.py）：BitBlt 取屏 + GetDIBits 读像素
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int,
                         wt.DWORD]
gdi32.BitBlt.restype = wt.BOOL
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
                            ctypes.c_void_p, ctypes.POINTER(BITMAPINFO),
                            wt.UINT]
gdi32.GetDIBits.restype = ctypes.c_int


def colorref(r: int, g: int, b: int) -> int:
    return r & 255 | (g & 255) << 8 | (b & 255) << 16


def set_round_region(hwnd: int, w: int, h: int, radius: int = 24) -> None:
    """把窗口裁成圆角（Tk 自己做不到）。radius 是椭圆直径，所以传 2×圆角。"""
    try:
        rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius, radius)
        user32.SetWindowRgn(hwnd, rgn, True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 抓屏排除（WDA_EXCLUDEFROMCAPTURE）
# ---------------------------------------------------------------------------
WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011
_GA_ROOT = 2


def set_capture_exclusion(hwnd: int, on: bool = True) -> bool:
    """让窗口「在屏幕上照常显示，但不进入任何截屏」。

    这是「快照伪装透明」的钥匙：窗口开着不掉 alpha、不隐藏，BitBlt 桌面
    直接读到窗口背后的真实像素，于是可以逐帧刷新背景而完全不闪。

    只在 Windows 10 2004+ 有效；对非顶层 HWND 会失败（Tk 的 winfo_id 是
    子窗口，必须先 GetAncestor(.., GA_ROOT)）。失败一律返回 False，
    调用方必须保留「隐藏窗口再抓」的老路径。
    """
    if not hwnd:
        return False
    try:
        top = user32.GetAncestor(wt.HWND(hwnd), _GA_ROOT) or hwnd
        return bool(user32.SetWindowDisplayAffinity(
            wt.HWND(top),
            WDA_EXCLUDEFROMCAPTURE if on else WDA_NONE))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 亚克力 / 毛玻璃
# ---------------------------------------------------------------------------
class _ACCENTPOLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_uint), ("AccentFlags", ctypes.c_uint),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_uint)]


class _WCADATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t)]


ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
WCA_ACCENT_POLICY = 19


def set_acrylic(hwnd: int, tint=(32, 32, 34), alpha: float = 0.9) -> bool:
    """给窗口上亚克力模糊。返回 False = 这个系统不支持 / 被忽略。

    注意这属于未公开 API：部分 Win11 版本会静默忽略，不报错也没效果，
    所以调用方必须有 fallback（半透明底 + 高光边），不能只依赖它。
    """
    try:
        fn = user32.SetWindowCompositionAttribute
    except Exception:
        return False
    try:
        a = int(max(0.0, min(1.0, alpha)) * 255)
        policy = _ACCENTPOLICY()
        policy.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
        policy.AccentFlags = 0
        # GradientColor 是 AABBGGRR
        policy.GradientColor = ((a << 24)
                                | (int(tint[2]) << 16)
                                | (int(tint[1]) << 8)
                                | int(tint[0]))
        data = _WCADATA(WCA_ACCENT_POLICY,
                        ctypes.cast(ctypes.byref(policy), ctypes.c_void_p),
                        ctypes.sizeof(policy))
        fn.argtypes = [wt.HWND, ctypes.POINTER(_WCADATA)]
        fn.restype = wt.BOOL
        ok = fn(wt.HWND(hwnd), ctypes.byref(data))
        return bool(ok)
    except Exception:
        return False
