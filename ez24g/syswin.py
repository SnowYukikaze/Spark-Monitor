# -*- coding: utf-8 -*-
"""系统集成：深浅色检测、开机自启、桌面快捷方式。

全部用 ctypes 直接调 COM / Shell API，不依赖 pywin32。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys

from . import config
from .win32 import GUID, advapi32, kernel32, ole32, shell32, user32

kernel32.CreateSymbolicLinkW.restype = wt.BOOL
kernel32.CreateSymbolicLinkW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.DWORD]


# ---------------------------------------------------------------------------
# 系统主题
# ---------------------------------------------------------------------------
def reg_dword(path: str, name: str, default: int = 0) -> int:
    advapi32.RegGetValueW.argtypes = [ctypes.c_void_p, wt.LPCWSTR, wt.LPCWSTR,
                                      wt.DWORD, ctypes.POINTER(wt.DWORD),
                                      ctypes.c_void_p, ctypes.POINTER(wt.DWORD)]
    advapi32.RegGetValueW.restype = wt.LONG
    val = wt.DWORD(0)
    size = wt.DWORD(4)
    try:
        rc = advapi32.RegGetValueW(
            ctypes.c_void_p(0x80000001),   # HKEY_CURRENT_USER
            path, name, 0x00000010, None,
            ctypes.byref(val), ctypes.byref(size))
        if rc == 0:
            return val.value
    except Exception:
        pass
    return default


def is_light_theme() -> bool:
    return reg_dword(
        r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        "AppsUseLightTheme", 1) != 0


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
def _shell_folder(csidl: int) -> str:
    buf = ctypes.create_unicode_buffer(260)
    shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return buf.value


def desktop_dir() -> str:
    return _shell_folder(0x0010)      # CSIDL_DESKTOPDIRECTORY


def startup_dir() -> str:
    return _shell_folder(0x0007)      # CSIDL_STARTUP


def startup_lnk() -> str:
    return os.path.join(startup_dir(), "2.4G.lnk")


def legacy_startup_lnk() -> str:
    return os.path.join(startup_dir(), "电量托盘.lnk")


def startup_enabled() -> bool:
    return (os.path.isfile(startup_lnk())
            or os.path.isfile(legacy_startup_lnk()))


def script_path() -> str:
    """启动脚本（my_devices.py）的绝对路径。"""
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    arg = os.path.abspath(sys.argv[0] or "")
    if arg.lower().endswith(".py") and os.path.isfile(arg):
        return arg
    return os.path.join(config.project_dir(), "my_devices.py")


def helper_entry() -> str:
    """抓包后台的启动脚本：必须是会把 --capture-helper 交给 app.main 的入口。

    不能用 script_path()：它读 sys.argv[0]，从临时入口 / 其它宿主脚本
    启动时指向的可能是一个完全没有 helper 分发的脚本。提权后那样只会
    再开一个界面，后台永远不写 status.json，界面卡在「正在查找设备…」。
    """
    candidate = os.path.join(config.project_dir(), "my_devices.py")
    try:
        with open(candidate, "r", encoding="utf-8", errors="ignore") as f:
            if "ez24g.app" in f.read():
                return os.path.abspath(candidate)
    except Exception:
        pass
    return script_path()


def pythonw_path() -> str:
    exe = sys.executable or ""
    if exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.isfile(cand):
            return cand
    return exe


# ---------------------------------------------------------------------------
# 快捷方式（IShellLink + IPersistFile）
# ---------------------------------------------------------------------------
def _com_method(obj, index, restype, *argtypes):
    vt = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vt[index])


def create_shortcut(lnk_path: str, target: str, args: str = "",
                    workdir: str = "", desc: str = "", icon: str = "") -> None:
    ole32.CoInitializeEx(None, 0x2)   # COINIT_APARTMENTTHREADED
    CLSID_ShellLink = GUID("{00021401-0000-0000-C000-000000000046}")
    IID_IShellLinkW = GUID("{000214F9-0000-0000-C000-000000000046}")
    IID_IPersistFile = GUID("{0000010B-0000-0000-C000-000000000046}")

    psl = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(CLSID_ShellLink), None, 1,
                                ctypes.byref(IID_IShellLinkW),
                                ctypes.byref(psl))
    if hr != 0 or not psl:
        raise RuntimeError("CoCreateInstance(CLSID_ShellLink) 失败 "
                           f"hr=0x{hr & 0xFFFFFFFF:08X}")

    # IShellLinkW: SetPath=20 SetArguments=11 SetWorkingDirectory=9
    #              SetDescription=7 SetIconLocation=17
    _com_method(psl, 20, ctypes.c_long, ctypes.c_wchar_p)(
        psl, ctypes.c_wchar_p(target))
    if args:
        _com_method(psl, 11, ctypes.c_long, ctypes.c_wchar_p)(
            psl, ctypes.c_wchar_p(args))
    if workdir:
        _com_method(psl, 9, ctypes.c_long, ctypes.c_wchar_p)(
            psl, ctypes.c_wchar_p(workdir))
    if desc:
        _com_method(psl, 7, ctypes.c_long, ctypes.c_wchar_p)(
            psl, ctypes.c_wchar_p(desc))
    if icon:
        _com_method(psl, 17, ctypes.c_long, ctypes.c_wchar_p, ctypes.c_int)(
            psl, ctypes.c_wchar_p(icon), 0)

    ppf = ctypes.c_void_p()
    _com_method(psl, 0, ctypes.c_long, ctypes.POINTER(GUID),
                ctypes.POINTER(ctypes.c_void_p))(
        psl, ctypes.byref(IID_IPersistFile), ctypes.byref(ppf))
    if not ppf:
        _com_method(psl, 2, ctypes.c_ulong)(psl)
        raise RuntimeError("QueryInterface(IPersistFile) 失败")
    _com_method(ppf, 6, ctypes.c_long, ctypes.c_wchar_p, ctypes.c_int)(
        ppf, ctypes.c_wchar_p(lnk_path), 1)
    _com_method(ppf, 2, ctypes.c_ulong)(ppf)
    _com_method(psl, 2, ctypes.c_ulong)(psl)
    ole32.CoUninitialize()


# ---------------------------------------------------------------------------
# 开机自启
# ---------------------------------------------------------------------------
def install_startup() -> bool:
    path = startup_lnk()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if getattr(sys, "frozen", False):
        create_shortcut(path, script_path(), "", config.project_dir(),
                        "2.4G", script_path())
    else:
        create_shortcut(path, pythonw_path(), f'"{script_path()}"',
                        config.project_dir(), "2.4G", pythonw_path())
    if os.path.isfile(path):
        try:
            os.remove(legacy_startup_lnk())
        except FileNotFoundError:
            pass
    return os.path.isfile(path)


def remove_startup() -> None:
    for p in (startup_lnk(), legacy_startup_lnk()):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def migrate_startup_link() -> None:
    """把旧的「电量托盘.lnk」迁移成「2.4G.lnk」，指向当前程序。"""
    if os.path.isfile(legacy_startup_lnk()):
        try:
            install_startup()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 桌面入口
# ---------------------------------------------------------------------------
def _create_dir_link(link: str, target: str) -> bool:
    try:
        if kernel32.CreateSymbolicLinkW(link, target, 0x1 | 0x2):
            if os.path.isdir(link):
                return True
    except Exception:
        pass
    # 兜底走 junction。注意不能用 ["cmd","/c","mklink",...] 这种列表形式：
    # list2cmdline 只在参数含空格时加引号，路径里出现 & | ^ 而**没有空格**时
    # （例如 C:\work&test\EZ2.4G），cmd 会把 & 之后当成新命令执行。
    # 这里整条命令作为一个字符串，路径已用引号包住，cmd 不会解析引号内的符号。
    try:
        for p in (link, target):
            if any(ch in p for ch in ('"', "\n", "\r")):
                return False
        subprocess.run(f'cmd /c mklink /J "{link}" "{target}"',
                       capture_output=True, shell=False)
        return os.path.isdir(link)
    except Exception:
        return False


def _create_file_link(link: str, target: str) -> bool:
    try:
        if kernel32.CreateSymbolicLinkW(link, target, 0x2):
            if os.path.isfile(link):
                return True
    except Exception:
        pass
    try:
        os.link(target, link)
        return os.path.isfile(link)
    except Exception:
        pass
    # 跨卷 / 没有符号链接权限：退化成 .lnk，仍指向脚本
    try:
        create_shortcut(link + ".lnk", target, "", config.project_dir(),
                        "外设电量启动脚本", pythonw_path())
        return os.path.isfile(link + ".lnk")
    except Exception:
        return False


def install_desktop_links() -> list[str]:
    created = []
    desk = desktop_dir()
    if getattr(sys, "frozen", False):
        lnk = os.path.join(desk, "外设电量.lnk")
        if not os.path.exists(lnk):
            try:
                create_shortcut(lnk, script_path(), "", config.project_dir(),
                                "外设电量", script_path())
                created.append(lnk)
            except Exception:
                pass
        return created
    dlink = os.path.join(desk, "外设电量")
    flink = os.path.join(desk, "my_devices.py")
    if not os.path.exists(dlink):
        if _create_dir_link(dlink, config.project_dir()):
            created.append(dlink)
    if not os.path.exists(flink):
        if _create_file_link(flink, script_path()):
            created.append(flink if os.path.exists(flink) else flink + ".lnk")
    return created


# ---------------------------------------------------------------------------
# 截屏工具识别（修「单击截屏时窗口变透明」）
# ---------------------------------------------------------------------------
# 已知的截屏/录屏前端进程名。命中后再看窗口大小，避免「聊天窗口在前台」
# 也被当成正在截屏（那样面板背景会莫名停止跟随桌面）。
CAPTURE_PROCS = {
    # Windows 自带
    "snippingtool.exe", "snipandsketch.exe", "screenclippinghost.exe",
    "screenclip.exe", "screensketch.exe",
    # 国内常见
    "qq.exe", "qqnt.exe", "tim.exe", "weixin.exe", "wechat.exe",
    "snipaste.exe", "pixpin.exe", "jietu.exe", "picpick.exe",
    # 国外常见
    "sharex.exe", "greenshot.exe", "fscapture.exe", "faststone.exe",
    "obs64.exe", "obs32.exe", "bandicam.exe", "lightscreen.exe",
}

# 只要这些进程的前台窗口「接近全屏」，就认为正在截屏
_CAPTURE_AREA_RATIO = 0.6
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROC_CACHE: dict[int, str] = {}


def _window_class(hwnd) -> str:
    try:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value or ""
    except Exception:
        return ""


def _window_rect(hwnd):
    try:
        r = wt.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return None


def _pid_of(hwnd) -> int:
    try:
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def process_exe_name(pid: int) -> str:
    """进程可执行文件名（小写）。查不到返回空串；结果按 pid 缓存。"""
    if not pid:
        return ""
    hit = _PROC_CACHE.get(pid)
    if hit is not None:
        return hit
    name = ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf,
                                                   ctypes.byref(size)):
                name = os.path.basename(buf.value or "").lower()
        finally:
            kernel32.CloseHandle(h)
    if len(_PROC_CACHE) > 128:            # 长期运行别无限涨
        _PROC_CACHE.clear()
    _PROC_CACHE[pid] = name
    return name


def foreground_info() -> dict:
    """当前前台窗口的 pid / 进程名 / 窗口类 / 面积占比。失败返回空 dict。"""
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {}
        pid = _pid_of(hwnd)
        rect = _window_rect(hwnd)
        info = {"hwnd": int(hwnd), "pid": pid,
                "exe": process_exe_name(pid), "cls": _window_class(hwnd),
                "ratio": 0.0}
        if rect:
            try:
                sw = user32.GetSystemMetrics(0) or 1920
                sh = user32.GetSystemMetrics(1) or 1080
            except Exception:
                sw, sh = 1920, 1080
            w = max(0, rect[2] - rect[0])
            h = max(0, rect[3] - rect[1])
            info["ratio"] = (w * h) / float(max(1, sw * sh))
        return info
    except Exception:
        return {}


def capture_tool_active() -> bool:
    """是不是正在截屏 / 录屏（只判「截屏前端进程 + 它的前台窗口接近全屏」）。

    为什么要这层判断：面板的「透明」是拿桌面快照伪装出来的，快照靠
    ``WDA_EXCLUDEFROMCAPTURE`` 把自己从抓屏里排除掉。副作用是任何截屏工具
    （包括系统截图、QQ/微信截图）拍到的都是「没有面板」的画面 —— 用户看到
    的就是「一按截屏，窗口就变透明了」。识别到截屏工具在前台时，调用方
    临时取消排除并停掉逐帧同步，截出来的就是面板本来的样子。
    """
    info = foreground_info()
    if not info or not info.get("exe"):
        return False
    if info["exe"] not in CAPTURE_PROCS:
        return False
    if info.get("ratio", 0.0) >= _CAPTURE_AREA_RATIO:
        return True
    cls = (info.get("cls") or "").lower()
    # 有些截图工具（Snipaste 等）用无边框置顶窗口，面积不一定够，但类名很特征
    return any(k in cls for k in ("snipaste", "pixpin", "snippingtool"))
