# -*- coding: utf-8 -*-
"""USBPcap 抓包文件（pcap）解析。

只做一件事：把 pcap 拆成 (bus, dev, endpoint, transfer, payload)。
USBPcap 头 27 字节固定，数据长度在偏移 23。
"""
from __future__ import annotations

import struct


def iter_packets(path: str):
    """流式读取，逐包产出 (bus, dev, ep, transfer, payload)。"""
    try:
        f = open(path, "rb")
    except Exception:
        return
    with f:
        head = f.read(24)          # pcap 全局头
        if len(head) < 24:
            return
        while True:
            ph = f.read(16)        # 包记录头
            if len(ph) < 16:
                return
            try:
                _ts, _tu, incl, _orig = struct.unpack("<IIII", ph)
            except Exception:
                return
            if incl <= 0 or incl > 64 * 1024 * 1024:
                return
            pkt = f.read(incl)
            if len(pkt) < incl:
                return
            if len(pkt) < 27:
                continue
            try:
                hlen = struct.unpack_from("<H", pkt, 0)[0]
            except Exception:
                continue
            if not (27 <= hlen <= 600) or len(pkt) < hlen:
                continue
            dlen = struct.unpack_from("<I", pkt, 23)[0]
            payload = pkt[hlen:hlen + dlen]
            bus = struct.unpack_from("<H", pkt, 17)[0]
            dev = struct.unpack_from("<H", pkt, 19)[0]
            yield bus, dev, pkt[21], pkt[22], payload


def scan_map(path: str) -> dict:
    """返回 {(bus, device): (vid, pid)}，来自 pcap 里的设备描述符。"""
    out = {}
    for bus, dev, _ep, _tr, payload in iter_packets(path):
        for i in range(0, max(0, len(payload) - 11)):
            if payload[i] == 18 and payload[i + 1] == 1:
                vid = payload[i + 8] | (payload[i + 9] << 8)
                pid = payload[i + 10] | (payload[i + 11] << 8)
                if vid and pid:
                    out[(bus, dev)] = (vid, pid)
                break
    return out


def scan_devices(path: str) -> list:
    """返回 pcap 里出现过的 [(vid, pid)]。"""
    seen = []
    for v in scan_map(path).values():
        if v not in seen:
            seen.append(v)
    return seen


def extract_reports(path: str, target: set = None,
                    limit: int = None) -> list:
    """提取 HID 帧；target 为 {(bus, device)} 时只收目标设备。"""
    out = []
    for bus, dev, ep, transfer, payload in iter_packets(path):
        if target is not None and (bus, dev) not in target:
            continue
        if len(payload) < 4:
            continue
        # HID SET_REPORT 控制传输：把报告本体当作 OUT 帧
        if (ep == 0 and len(payload) >= 10
                and payload[0] == 0x21 and payload[1] == 0x09):
            wlen = payload[6] | (payload[7] << 8)
            report = payload[8:8 + wlen]
            if len(report) >= 3 and (not out or out[-1][2] != report):
                out.append((0x02, 1, report))
            if limit and len(out) >= limit:
                break
            continue
        if not out or out[-1][2] != payload:
            out.append((ep, transfer, payload))
            if limit and len(out) >= limit:
                break
    return out


def extract_for(path: str, vid: int, pid: int, limit: int = None):
    """只提取目标 VID:PID 的帧；找不到映射时退回全部帧。

    `limit` 是安全阀：不设上限时会把每一帧都 append 进 list
    （实测 63MB 抓包 ≈ 3.4 秒 / 116MB 内存，256MB 上限约 14 秒 / 470MB）。
    推断电量只需要开头的若干帧，几千条之后的帧对结果没有贡献。
    """
    mapping = scan_map(path)
    targets = {k for k, v in mapping.items() if v == (vid, pid)} if vid else set()
    frames = extract_reports(path, targets or None, limit=limit)
    return frames, mapping


def format_reports(reports: list) -> str:
    lines = ["# endpoint dir transfer length data"]
    for ep, transfer, data in reports:
        direction = "IN" if ep & 0x80 else "OUT"
        hexs = " ".join(f"{b:02X}" for b in data)
        lines.append(f"{ep:04X} {direction} t={transfer} "
                     f"len={len(data)} {hexs}")
    return "\n".join(lines)
