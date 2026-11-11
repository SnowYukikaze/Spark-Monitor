# -*- coding: utf-8 -*-
"""一键打源码包：把项目按「单一顶层目录 EZ2.4G/」压成 zip。

沿用之前交接包的规则（`EZ2.4G_蓝牙电量版.zip` 就是这个结构）：
    EZ2.4G/README.md
    EZ2.4G/ez24g/...
    EZ2.4G/docs/...
    ...
排除：版本库、Python 缓存、构建产物（build/dist）、旧 zip 快照、*.spec。

用法：
    python tools/make_release.py                 # 输出到项目根的 EZ2.4G_<名字>.zip
    python tools/make_release.py --tag 四回归修复版
"""
from __future__ import annotations

import argparse
import os
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOP = "Spark Monitor"

EXCLUDE_DIRS = {".git", "__pycache__", "build", "dist", ".idea", ".vscode",
                ".workbuddy", ".pytest_cache"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".spec", ".log"}
# 旧产物 / 交接快照不进包（自引用会让包越来越大）
EXCLUDE_FILES = {"EZ2.4G_蓝牙电量版.zip", "Spark Monitor_蓝牙电量版.zip"}


def _skip(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if any(p in EXCLUDE_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    if name in EXCLUDE_FILES:
        return True
    if os.path.splitext(name)[1].lower() in EXCLUDE_SUFFIX:
        return True
    # 归档目录只放文档，zip 快照不进包
    if parts[0] == "归档" and name.lower().endswith(".zip"):
        return True
    # 项目根的 zip 都是发布产物（含本次输出自己），不进包
    if len(parts) == 1 and name.lower().endswith(".zip"):
        return True
    # 输出包自己不打包自己
    if parts[0] == TOP and name.lower().endswith(".zip"):
        return True
    return False


def collect() -> list:
    items = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        rel_dir = os.path.relpath(dirpath, ROOT)
        for fn in sorted(filenames):
            rel = fn if rel_dir == "." else os.path.join(rel_dir, fn)
            if _skip(rel):
                continue
            items.append(rel)
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="四回归修复版", help="包名后缀")
    ap.add_argument("--out", default="", help="输出路径（默认项目根）")
    args = ap.parse_args()

    items = collect()
    out = args.out or os.path.join(ROOT, f"Spark Monitor_{args.tag}.zip")
    tmp = out + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in items:
            src = os.path.join(ROOT, rel)
            arc = TOP + "/" + rel.replace("\\", "/")
            z.write(src, arc)
    os.replace(tmp, out)

    total = sum(os.path.getsize(os.path.join(ROOT, r)) for r in items)
    print(f"文件数：{len(items)}")
    print(f"原始大小：{total / 1024:.0f} KB")
    print(f"输出：{out}")
    print(f"压缩后：{os.path.getsize(out) / 1024:.0f} KB")
    print(f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
