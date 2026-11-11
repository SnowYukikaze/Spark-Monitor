# -*- coding: utf-8 -*-
"""一条命令跑完全部自检。

用法：
    python tools/verify_all.py           # 常规检查（约 15 秒）
    python tools/verify_all.py --full    # 额外跑一次真实设备读取（--once）
    python tools/verify_all.py --python "C:\\...\\python.exe"   # 指定解释器

会依次跑：
    compileall          全量编译
    test_core           抓包推断 / 配置 / 注册表 / 上次读数      基线 102 项
    smoke_layout        面板几何：越界 / 重叠 / 文本超宽 / z 序   基线 32 项
    smoke_interact      改名 / 菜单 / 抓包向导 / 滚动 / 浅色      基线 362 项
    smoke_tray          托盘窗口 / GDI 图标 / 通知                基线 33 项
    CLI                 --help / --protocols / --once(可选)

任何一项失败，退出码非 0。通过数比基线少会给出警告（可能有用例被跳过）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")

BASELINE = {
    "test_core": 113,
    "smoke_layout": 33,
    "smoke_interact": 667,
    "smoke_tray": 33,
}


def find_tk_python(explicit: str = "") -> str:
    """找一个带 tkinter 的解释器：GUI 自检必须有 tk。"""
    cands = []
    if explicit:
        cands.append(explicit)
    cands.append(sys.executable)
    local = os.environ.get("LOCALAPPDATA", "")
    for v in ("314", "313", "312", "311", "310"):
        cands.append(os.path.join(local, "Programs", "Python",
                                  f"Python{v}", "python.exe"))
    cands.append(shutil.which("python") or "")
    cands.append(shutil.which("python3") or "")

    seen = set()
    for exe in cands:
        if not exe or exe in seen or not os.path.isfile(exe):
            continue
        seen.add(exe)
        try:
            p = subprocess.run([exe, "-c", "import tkinter"],
                               capture_output=True, timeout=20)
        except Exception:
            continue
        if p.returncode == 0:
            return exe

    # 退一步：py 启动器
    py = shutil.which("py")
    if py:
        try:
            p = subprocess.run([py, "-3", "-c", "import tkinter"],
                               capture_output=True, timeout=20)
            if p.returncode == 0:
                return f"{py} -3"
        except Exception:
            pass
    return ""


def run(cmd, timeout=180):
    started = time.time()
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out, time.time() - started
    except subprocess.TimeoutExpired:
        return -9, f"超时（>{timeout}s）", time.time() - started


def count_ok(out: str) -> int:
    return len(re.findall(r"^\s*OK\s", out, re.M))


def count_fail(out: str) -> int:
    return len(re.findall(r"^\s*FAIL\s", out, re.M))


def first_failure(out: str) -> str:
    for line in out.splitlines():
        if line.strip().startswith("FAIL"):
            return line.strip()[:100]
    for line in out.splitlines():
        if "Traceback" in line or "Error" in line:
            return line.strip()[:100]
    return (out.strip().splitlines() or [""])[-1][:100]


def main(argv):
    full = "--full" in argv
    explicit = ""
    if "--python" in argv:
        i = argv.index("--python")
        if i + 1 < len(argv):
            explicit = argv[i + 1]

    tk_py = find_tk_python(explicit)
    plain_py = sys.executable

    print(f"项目：{ROOT}")
    print(f"Python（含 tkinter）：{tk_py or '未找到'}")
    if not tk_py:
        print("!! 找不到带 tkinter 的解释器，GUI 自检会跳过。")
        print("   安装 Python 时勾选 tcl/tk，或用 --python 指定路径。")
    print()

    steps = []

    code, out, cost = run([plain_py, "-m", "compileall", "-q", "ez24g",
                           "my_devices.py"])
    steps.append(("compileall", code == 0, 0 if code == 0 else 1, cost,
                  "" if code == 0 else out.strip()[:160]))

    for name in ("test_core", "smoke_layout", "smoke_interact", "smoke_tray"):
        script = os.path.join(TOOLS, name + ".py")
        if not os.path.isfile(script):
            steps.append((name, False, 0, 0.0, "脚本不存在"))
            continue
        py = tk_py if name in ("smoke_layout", "smoke_interact") else plain_py
        if not py:
            steps.append((name, False, 0, 0.0, "缺少 tkinter 解释器"))
            continue
        cmd = py.split() if " " in py else [py]
        code, out, cost = run(cmd + [script])
        ok, fail = count_ok(out), count_fail(out)
        steps.append((name, code == 0 and fail == 0, ok, cost,
                      "" if code == 0 and fail == 0 else first_failure(out)))

    code, out, cost = run([plain_py, "my_devices.py", "--help"])
    steps.append(("CLI --help", code == 0 and "用法" in out, 0, cost,
                  "" if code == 0 else out[:160]))

    code, out, cost = run([plain_py, "my_devices.py", "--protocols"])
    n = len([l for l in out.splitlines() if re.match(r"^\S+\s+\d+\s+(yes|no)", l)])
    steps.append(("CLI --protocols", code == 0 and n >= 10, n, cost,
                  "" if code == 0 and n >= 10 else out[:160]))

    if full:
        code, out, cost = run([plain_py, "my_devices.py", "--once"], timeout=300)
        steps.append(("CLI --once（真机）", code == 0, 0, cost,
                      "" if code == 0 else out[:160]))

    print(f"{'检查项':<20}{'结果':<8}{'通过':>6}{'耗时':>8}   备注")
    print("-" * 68)
    warnings = []
    failed = 0
    for name, ok, count, cost, note in steps:
        base = BASELINE.get(name)
        if ok and base and count < base:
            warnings.append(f"{name} 通过 {count} 项，少于基线 {base} 项"
                            "（可能有用例被跳过）")
        if not ok:
            failed += 1
        mark = "PASS" if ok else "FAIL"
        print(f"{name:<20}{mark:<8}{count:>6}{cost:>7.1f}s   {note}")

    print("-" * 68)
    if warnings:
        print("\n警告：")
        for w in warnings:
            print("  - " + w)
    if failed:
        print(f"\n结论：{failed} 项失败，不能交付。")
        return 1
    print("\n结论：全部通过。")
    if not full:
        print("提示：真机插拔/休眠/透明度这些还需要人眼确认，见 docs/验收清单.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
