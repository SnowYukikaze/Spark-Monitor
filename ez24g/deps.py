# -*- coding: utf-8 -*-
"""可选依赖的自动安装（内置 pip 引导 + 私有安装目录）。

要解决的问题
------------
以前程序依赖 Pillow / winrt 时只弹一句「pip install Pillow」，用户得自己
找到「和本程序同版本的那个 Python」、开终端、敲命令。换台电脑就再折腾一次，
很多人干脆放弃背景图 / 蓝牙电量。

现在的做法（三句话）
--------------------
1. **私有目录**：所有可选依赖装到 ``%APPDATA%\\BatteryTray\\vendor``，
   不碰系统 site-packages，不需要管理员权限，也不会污染用户环境；
   启动时把这个目录挂到 sys.path 前面，装完立刻可用。
2. **内置 pip 引导**：优先用当前解释器自带的 ``python -m pip``；没有就先
   ``-m ensurepip``；再不行才去下载 get-pip.py。全程用 ``sys.executable``，
   所以永远装进「正在跑这个程序的那个 Python」，不靠 PATH 猜。
3. **离线优先**：如果程序目录下带 ``wheels/``（发行版可预置 .whl），
   直接用 ``--no-index --find-links wheels`` 装，完全不联网。

约定
----
* 任何函数都不抛异常给界面：失败返回 False，原因写进 ``last_error()`` 与
  ``%APPDATA%\\BatteryTray\\deps.log``。
* 绝不在 import 阶段跑网络/子进程；必须显式调用 ``ensure()``。
* 同一个包并发只装一次（``_lock`` + ``_running``）。
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import threading
import time
from typing import Callable, Optional, Sequence

from . import config

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
# 逻辑名 → pip 包。界面只说「Pillow」「蓝牙组件」，具体装哪些包在这里决定。
PACKAGES = {
    "Pillow": ["Pillow>=10.0"],
    "winrt": ["winrt-runtime",
              "winrt-Windows.Foundation",
              "winrt-Windows.Foundation.Collections",
              "winrt-Windows.Storage.Streams",
              "winrt-Windows.Devices.Enumeration",
              "winrt-Windows.Devices.Bluetooth",
              "winrt-Windows.Devices.Bluetooth.GenericAttributeProfile"],
}

# 逻辑名 → 装完要能 import 的模块
IMPORT_NAME = {
    "Pillow": "PIL",
    "winrt": "winrt.windows.devices.bluetooth",
}

INSTALL_TIMEOUT = 240          # 单次 pip 最长等 4 分钟（慢网 / 编译）
PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

_lock = threading.RLock()
_running: set[str] = set()
_state: dict[str, bool] = {}          # 逻辑名 → 最近一次探测结果
_last_error = ""
_log_path_cache = ""


def vendor_dir() -> str:
    """私有安装目录（用户级，无需管理员）。"""
    return os.path.join(config.config_dir(), "vendor")


def wheels_dir() -> str:
    """发行版预置的离线轮子目录；不存在就返回空串。"""
    p = os.path.join(config.project_dir(), "wheels")
    return p if os.path.isdir(p) else ""


def log_path() -> str:
    global _log_path_cache
    if not _log_path_cache:
        _log_path_cache = os.path.join(config.config_dir(), "deps.log")
    return _log_path_cache


def last_error() -> str:
    return _last_error


def _log(msg: str) -> None:
    try:
        os.makedirs(config.config_dir(), exist_ok=True)
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# sys.path：让私有目录里的包能被 import
# ---------------------------------------------------------------------------
def ensure_path() -> str:
    """把私有目录挂到 sys.path 最前面（幂等）。返回该目录路径。"""
    d = vendor_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return d
    if d not in sys.path:
        sys.path.insert(0, d)
        try:
            importlib.invalidate_caches()      # 新装的 .pyd 才能被找到
        except Exception:
            pass
    return d


def have(name: str) -> bool:
    """依赖是否可用（只 import 一次，结果缓存；失败不缓存成永久）。"""
    mod = IMPORT_NAME.get(name, name)
    hit = _state.get(name)
    if hit:
        return True
    ensure_path()
    try:
        importlib.invalidate_caches()
        importlib.import_module(mod)
    except Exception:
        return False
    _state[name] = True
    return True


def reset_cache() -> None:
    _state.clear()


# ---------------------------------------------------------------------------
# pip
# ---------------------------------------------------------------------------
def _python() -> str:
    return sys.executable or "python"


def _frozen() -> bool:
    """打包成 exe 后 sys.executable 是 exe 自己，跑不了 -m pip。"""
    return bool(getattr(sys, "frozen", False))


def _run(args: Sequence[str], timeout: float = INSTALL_TIMEOUT,
         env: Optional[dict] = None) -> tuple[int, str]:
    """跑一个子进程并返回 (退出码, 合并输出)。不抛异常。"""
    flags = 0
    if os.name == "nt":
        flags = 0x08000000                     # CREATE_NO_WINDOW
    try:
        p = subprocess.run(list(args), capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout, env=env, creationflags=flags)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return -9, f"超时（>{timeout:.0f}s）：{' '.join(args[:4])}…"
    except Exception as e:                     # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def pip_available() -> bool:
    if _frozen():
        return False
    code, _out = _run([_python(), "-m", "pip", "--version"], timeout=40)
    return code == 0


def bootstrap_pip() -> bool:
    """没有 pip 时现场装一个：ensurepip → get-pip.py。"""
    if _frozen():
        _set_error("当前是打包版（exe），无法内置安装；请使用带 Python 的版本")
        return False
    if pip_available():
        return True
    _log("pip 不可用，尝试 ensurepip")
    code, out = _run([_python(), "-m", "ensurepip", "--upgrade"], timeout=120)
    _log(f"ensurepip rc={code} out={out[-800:]}")
    if pip_available():
        return True

    # 最后手段：下载 get-pip.py（用户机器上 ensurepip 常被精简掉）
    _log("ensurepip 失败，尝试下载 get-pip.py")
    dst = os.path.join(vendor_dir(), "get-pip.py")
    try:
        os.makedirs(vendor_dir(), exist_ok=True)
        import urllib.request
        with urllib.request.urlopen(PIP_URL, timeout=60) as r:
            data = r.read()
        with open(dst, "wb") as f:
            f.write(data)
        code, out = _run([_python(), dst, "--no-warn-script-location",
                          "--disable-pip-version-check"], timeout=180)
        _log(f"get-pip rc={code} out={out[-800:]}")
    except Exception as e:                     # noqa: BLE001
        _log(f"下载 get-pip.py 失败：{type(e).__name__}: {e}")
    return pip_available()


def _set_error(msg: str) -> None:
    global _last_error
    _last_error = msg or ""
    if msg:
        _log("ERROR " + msg)


# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------
def _pip_install(pkgs: Sequence[str], progress=None) -> bool:
    """一次 pip install，装进私有目录；离线轮子优先。"""
    ensure_path()
    target = vendor_dir()
    base = [_python(), "-m", "pip", "install", "--target", target,
            "--upgrade", "--no-input", "--disable-pip-version-check",
            "--no-warn-script-location"]
    wd = wheels_dir()

    attempts = []
    if wd:
        # 离线优先：内置轮子不需要网络，也不会因为镜像抽风而失败
        attempts.append(base + ["--no-index", "--find-links", wd] + list(pkgs))
    attempts.append(base + ["--prefer-binary"] + list(pkgs))
    attempts.append(base + ["--only-binary=:all:"] + list(pkgs))

    for i, args in enumerate(attempts, 1):
        if progress:
            progress("install", f"正在安装 {' '.join(pkgs)}"
                                f"（{i}/{len(attempts)}）")
        code, out = _run(args)
        _log(f"pip install #{i} rc={code} args={args[3:]} out={out[-1200:]}")
        if code == 0:
            return True
        # 网络类错误换下一次尝试；参数类错误（如包名不存在）没必要重试
        tail = out[-400:].lower()
        if "no matching distribution" in tail or "could not find a version" in tail:
            _set_error(f"找不到可用安装包：{' '.join(pkgs)}")
            return False
    _set_error(f"安装失败：{' '.join(pkgs)}（详情见 {log_path()}）")
    return False


def _verify(name: str) -> bool:
    """用**子进程**验证安装结果：只信真实 import，不信日志。"""
    mod = IMPORT_NAME.get(name, name)
    if _frozen():
        try:
            importlib.invalidate_caches()
            return have(name)
        except Exception:
            return False
    env = dict(os.environ)
    env["PYTHONPATH"] = vendor_dir() + os.pathsep + env.get("PYTHONPATH", "")
    code, out = _run([_python(), "-c", f"import {mod}"], timeout=60, env=env)
    if code != 0:
        _log(f"verify {mod} rc={code} out={out[-400:]}")
    return code == 0


def install(name: str, progress: Optional[Callable[[str, str], None]] = None
            ) -> bool:
    """安装一个逻辑依赖；已装则直接返回 True。"""
    pkgs = PACKAGES.get(name)
    if not pkgs:
        _set_error(f"未知依赖：{name}")
        return False
    if have(name):
        return True

    with _lock:
        if name in _running:                   # 同包并发只跑一次
            return have(name)
        _running.add(name)
        try:
            if progress:
                progress("check", f"正在准备安装 {name}…")
            if not bootstrap_pip():
                return False
            if not _pip_install(pkgs, progress):
                return False
            if progress:
                progress("verify", f"正在验证 {name}…")
            if not _verify(name):
                _set_error(f"{name} 装完了但导入失败，可能是 Python 版本不匹配"
                           f"（详情见 {log_path()}）")
                return False
            ensure_path()
            importlib.invalidate_caches()
            _state[name] = True
            if progress:
                progress("done", f"{name} 已就绪")
            return True
        finally:
            _running.discard(name)


def ensure(names: Sequence[str],
           progress: Optional[Callable[[str, str], None]] = None) -> bool:
    """批量确保依赖可用；全部成功才返回 True。"""
    ok = True
    for n in names:
        if not install(n, progress):
            ok = False
    return ok


def install_async(names: Sequence[str],
                  done: Optional[Callable[[bool], None]] = None,
                  progress: Optional[Callable[[str, str], None]] = None
                  ) -> threading.Thread:
    """后台线程安装，装完回调 done(ok)。界面不会卡住。"""
    def work():
        ok = False
        try:
            ok = ensure(names, progress)
        except Exception as e:                 # noqa: BLE001
            _set_error(f"{type(e).__name__}: {e}")
        finally:
            if done:
                try:
                    done(ok)
                except Exception:
                    pass
    t = threading.Thread(target=work, daemon=True,
                         name="deps-install")
    t.start()
    return t


def status() -> dict:
    """给 --doctor / 交接文档用的一份快照。"""
    return {
        "python": _python(),
        "frozen": _frozen(),
        "vendor": vendor_dir(),
        "wheels": wheels_dir(),
        "pip": pip_available() if not _frozen() else False,
        "Pillow": have("Pillow"),
        "winrt": have("winrt"),
        "error": last_error(),
        "log": log_path(),
    }
