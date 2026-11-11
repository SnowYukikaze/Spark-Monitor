# -*- coding: utf-8 -*-
"""USBPcap 抓包 helper（需要管理员权限的独立子进程）。

流程：
    1. 依次试 USBPcap1..6，各抓 4 秒，看哪个接口上出现了目标 VID:PID；
    2. 找到后在那个接口上正式抓包，直到出现 stop.flag 或 180 秒；
    3. 状态写入 status.json（probing / capturing / done / stopped /
       busy / notraffic / error），主程序轮询这个文件显示进度。

为什么要单独一个进程：抓包必须 runas 提权，主程序不能整体提权。
用 Job 对象绑定，helper 被杀时 USBPcapCMD 一定跟着退出，不留僵尸。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import subprocess
import time

from .. import win32 as W

_JOB = None


def scan_devices(path: str) -> list:
    """扫 pcap 里出现过的 (vid, pid)。

    复用 pcap.iter_packets 的流式解析（已带 `incl ≤ 64MB` / `hlen ≤ 600`
    等边界检查）。旧实现把整个文件 `f.read()` 进内存再切片，在**提权进程**
    里等于把文件大小放大 2–3 倍；而 probe*.pcap 是可预测文件名，
    被替换成大文件时会直接顶爆内存。
    """
    from . import pcap

    out = []
    try:
        for _bus, _dev, _ep, _tr, payload in pcap.iter_packets(path):
            for i in range(0, max(0, len(payload) - 11)):
                if payload[i] == 18 and payload[i + 1] == 1:
                    vid = payload[i + 8] | (payload[i + 9] << 8)
                    pid = payload[i + 10] | (payload[i + 11] << 8)
                    if vid and pid and (vid, pid) not in out:
                        out.append((vid, pid))
                    break
    except Exception:
        return out
    return out


def hub_exists(n: int) -> bool:
    r"""\\.\USBPcapN 能不能打开；错误 5 说明存在但需要管理员。"""
    k32 = W.kernel32
    k32.CreateFileW.restype = wt.HANDLE
    k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD,
                                ctypes.c_void_p, wt.DWORD, wt.DWORD,
                                wt.HANDLE]
    h = k32.CreateFileW("\\\\.\\USBPcap%d" % n, 0, 3, None, 3, 0, None)
    err = ctypes.get_last_error()
    if h and h != wt.HANDLE(-1).value:
        k32.CloseHandle(h)
        return True
    return err == 5


def setup_job():
    """Job：helper 退出时连带收掉 USBPcapCMD。"""
    global _JOB
    try:
        k32 = W.kernel32
        k32.CreateJobObjectW.restype = wt.HANDLE
        k32.GetCurrentProcess.restype = wt.HANDLE
        k32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return
        info = W.JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(job, 9, ctypes.byref(info),
                                    ctypes.sizeof(info))
        k32.AssignProcessToJobObject(job, k32.GetCurrentProcess())
        _JOB = job
    except Exception:
        _JOB = None


def _grant_read(paths):
    """给提权进程产出的文件补一条「当前用户可读」权限。

    高完整性进程新建的文件默认只认管理员，主界面（普通权限）读不到，
    于是会一直停在「正在查找设备…」，抓完了也解析不了 pcap。
    icacls 只追加一条 ACE，不动原有权限；失败就忽略，界面另有超时兜底。
    """
    user = os.environ.get("USERNAME") or ""
    if not user:
        return
    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        try:
            subprocess.run(["icacls", p, "/grant", f"{user}:(R)", "/Q"],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           creationflags=W.CREATE_NO_WINDOW,
                           timeout=5)
        except Exception:
            pass


def finalize_capture(state, status_fn, grant_fn, hub=0, file="", code=0):
    """收尾顺序：先给产出文件开权限，再宣布完成。

    反过来的话，主界面一看到 done 就去读 pcap，那一刻权限还没补上，
    会误报「抓包文件被管理员权限锁住」——其实文件几百毫秒后就能读。
    """
    grant_fn()
    status_fn(state, hub, file)
    return code


def wait_probe(proc, stop_flag: str, seconds: float = 4.0) -> bool:
    """探测期等待；用户点「停止」就立刻返回 True，不用干等 4 秒 × N。"""
    for _ in range(max(1, int(seconds / 0.2))):
        if os.path.exists(stop_flag):
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.2)
    return os.path.exists(stop_flag)


def main(argv: list) -> int:
    setup_job()
    (cp, vid_s, pid_s, outdir, stop_flag, status_file) = argv[:6]
    vid = int(vid_s, 16)
    pid = int(pid_s, 16)

    def status(state, hub=0, file=""):
        # 直接覆写主界面预先建好的文件：访问权限始终是本进程创建时
        # 那一套，主界面读得到。早先「写 .tmp 再 os.replace」会把文件
        # 换成提权进程的 ACL，界面永远读不到状态。
        try:
            with open(status_file, "w", encoding="utf-8") as f:
                json.dump({"state": state, "hub": hub, "file": file}, f)
        except Exception:
            pass

    def grant_outputs():
        try:
            names = os.listdir(outdir)
        except Exception:
            names = []
        _grant_read([os.path.join(outdir, n) for n in names])

    def finish(state, hub=0, file="", code=0):
        return finalize_capture(state, status, grant_outputs,
                                hub, file, code)

    hubs = [n for n in range(1, 7) if hub_exists(n)]
    status("probing")
    any_bytes = False
    found = 0

    if vid:
        for hub in hubs:
            probe = os.path.join(outdir, "probe%d.pcap" % hub)
            try:
                os.remove(probe)
            except Exception:
                pass
            try:
                p = subprocess.Popen(
                    [cp, "-d", "\\\\.\\USBPcap%d" % hub, "-A",
                     "--inject-descriptors", "-o", probe],
                    creationflags=W.CREATE_NO_WINDOW,
                    stderr=subprocess.DEVNULL)
            except Exception:
                continue
            stopped = wait_probe(p, stop_flag)
            try:
                p.terminate()
                p.wait(3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
            if stopped:
                return finish("stopped")
            try:
                if os.path.getsize(probe) > 0:
                    any_bytes = True
            except Exception:
                pass
            if (vid, pid) in scan_devices(probe):
                found = hub
                break
        if not found:
            return finish("notraffic" if any_bytes else "busy")
    else:
        found = hubs[0] if hubs else 1

    status("capturing", found)
    final = os.path.join(outdir, "capture.pcap")
    try:
        p = subprocess.Popen(
            [cp, "-d", "\\\\.\\USBPcap%d" % found, "-A",
             "--inject-descriptors", "-o", final],
            creationflags=W.CREATE_NO_WINDOW,
            stderr=subprocess.DEVNULL)
    except Exception:
        return finish("error", code=1)

    started = time.time()
    while not os.path.exists(stop_flag):
        if p.poll() is not None:
            break
        if time.time() - started > 180:
            break
        try:
            if os.path.getsize(final) > 256 * 1024 * 1024:
                break
        except Exception:
            pass
        time.sleep(0.2)
    try:
        p.terminate()
        p.wait(3)
    except Exception:
        try:
            p.kill()
            p.wait(2)
        except Exception:
            pass
    return finish("done", found, final)


def is_helper_invocation(argv: list) -> bool:
    return bool(argv) and argv[0] == "--capture-helper"


def run(argv: list) -> int:
    return main(argv[1:])
