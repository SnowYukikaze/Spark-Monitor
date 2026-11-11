# -*- coding: utf-8 -*-
"""从抓包帧里推断「电量在哪个字节」。

思路：
1. 先把帧分成 IN（设备→主机）和 OUT（主机→设备），
   并过滤掉描述符、EP0 标准控制请求、字符串描述符、全零报告；
2. 单次抓包：只有唯一字节等于电量 → 采用；多个 → ambiguous；
3. 两次不同电量：某字节差值 == 电量差值 → 采用（更可靠）；
4. 保存前必须回读验证，值对得上才落库（正向或反向）。
"""
from __future__ import annotations

from typing import Optional

from ..hidapi import enumerate_hid
from ..protocols.captured import listen_with_protocol, read_with_protocol

# 这些前缀的帧是 USB 描述符，不可能承载电量
_DESCRIPTOR_PREFIXES = (
    b"\x12\x01",  # 设备描述符
    b"\x09\x02",  # 配置描述符
    b"\x09\x04",  # 接口描述符
    b"\x07\x05",  # 端点描述符
    b"\x09\x21",  # HID 描述符
    b"\x05\x01",  # 报告描述符：通用桌面
    b"\x05\x0c",  # 报告描述符：消费控制
    b"\x06\x00",  # 报告描述符：厂商页
    b"\x0a\x06",  # 设备限定符
    b"\x07\x07",  # Other Speed 配置
    b"\x0f\x05",  # BOS 描述符
)


def _looks_descriptor(d: bytes) -> bool:
    return len(d) >= 2 and d[:2] in _DESCRIPTOR_PREFIXES


def _looks_control_setup(d: bytes, ep: int) -> bool:
    """EP0 上不承载电量的标准控制请求。"""
    if ep not in (0x00, 0x80) or len(d) != 8:
        return False
    return ((d[0] == 0x80 and d[1] == 0x06)
            or (d[0] == 0x00 and d[1] == 0x09)
            or (d[0] == 0x01 and d[1] == 0x0B)
            or (d[0] == 0x02 and d[1] == 0x01))


def _looks_string_descriptor(d: bytes, ep: int) -> bool:
    """EP0 上的字符串描述符：只是枚举期读回来的 UTF-16 文本。

    不过滤会拿文本里的字母撞值，例如 'd' = 0x64 = 100%。
    """
    if ep != 0x80:
        return False
    if len(d) >= 2 and d[1] == 0x03 and d[0] == len(d):
        return True
    return len(d) == 8 and d[0] == 0x80 and d[1] == 0x06


def split_frames(frames: list):
    ins = []
    outs = []
    for idx, (ep, _tr, d) in enumerate(frames):
        if not d:
            continue
        if ep & 0x80:
            if len(d) < 4:
                continue
            if ep == 0x80 and (_looks_descriptor(d)
                               or _looks_control_setup(d, ep)
                               or _looks_string_descriptor(d, ep)):
                continue
            if not any(d):
                continue
            ins.append((idx, d))
        elif ep != 0 and len(d) >= 3:
            outs.append((idx, d))
    return ins, outs


def _match_offsets(ins: list, level: int) -> set:
    out = set()
    for _idx, d in ins:
        for off in range(1, min(len(d), 64)):
            if d[off] == level:
                out.add(off)
    return out


def _requests_at(frames: list, ins: list, outs: list,
                 off: int, level: int) -> list:
    """找触发这个回复的请求帧：取回复前最近的一条 OUT。"""
    reqs = []
    oi = 0
    last_idx = -1
    last = None
    for idx, d in ins:
        while oi < len(outs) and outs[oi][0] < idx:
            last_idx, last = outs[oi][0], outs[oi][1]
            oi += 1
        if len(d) <= off or d[off] != level:
            continue
        if last is not None and idx - last_idx <= 80 and last not in reqs:
            reqs.append(last)
    if not reqs:
        prefixes = {d[:2] for _i, d in ins
                    if len(d) > off and d[off] == level}
        reqs = [d for _i, d in outs if d[:2] in prefixes]
    if not reqs:
        reqs = [d for _i, d in outs]
    return reqs[:6]


def _proto_at(reqs: list, off: int) -> dict:
    return {
        "requests": [" ".join(f"{b:02X}" for b in d[:32]) for d in reqs[:6]],
        "offset": off,
    }


def infer_single_ex(level: int, frames: list):
    """单次抓取：唯一字节位置直接采用，多个位置返回 ambiguous。"""
    level = int(level)
    if level <= 0:
        return None, "none"
    ins, outs = split_frames(frames)
    if not ins or not outs:
        return None, "none"
    offs = _match_offsets(ins, level)
    if not offs:
        return None, "none"
    if len(offs) > 1:
        return None, "ambiguous"
    off = next(iter(offs))
    reqs = _requests_at(frames, ins, outs, off, level)
    if not reqs:
        return None, "none"
    return _proto_at(reqs, off), "ok"


def infer_two(level_a: int, frames_a: list,
              level_b: int, frames_b: list):
    """两次不同电量：某字节的差值等于两次电量差值，唯一则采用。"""
    level_a = int(level_a)
    level_b = int(level_b)
    if level_a <= 0 or level_b <= 0 or level_a == level_b:
        return None, "none"
    ins_a, outs_a = split_frames(frames_a)
    ins_b, outs_b = split_frames(frames_b)
    if not ins_a or not ins_b:
        return None, "none"
    delta = level_b - level_a
    candidates = []
    for off in range(1, 64):
        va = {d[off] for _i, d in ins_a if len(d) > off}
        vb = {d[off] for _i, d in ins_b if len(d) > off}
        if any((y - x) == delta for x in va for y in vb):
            candidates.append(off)
    if not candidates:
        return None, "none"
    if len(candidates) > 1:
        return None, "ambiguous"
    off = candidates[0]
    reqs = _requests_at(frames_a, ins_a, outs_a, off, level_a)
    if not reqs:
        reqs = _requests_at(frames_b, ins_b, outs_b, off, level_b)
    if not reqs:
        return None, "none"
    return _proto_at(reqs, off), "ok"


def infer_single(level: int, frames: list) -> Optional[dict]:
    return infer_single_ex(level, frames)[0]


def infer_generic(samples: list) -> Optional[dict]:
    """兼容入口：samples = [(电量, frames), ...]，两次以上才可用。"""
    if len(samples) < 2:
        return None
    return infer_two(samples[0][0], samples[0][1],
                     samples[1][0], samples[1][1])[0]


# ---------------------------------------------------------------------------
# 回读验证
# ---------------------------------------------------------------------------
def validate_capture(proto: dict, vid: int, pid: int, level: int):
    """返回 (模式, 回读结果)：direct / invert / None。

    保存协议前必须过这一关，否则界面会显示一个假电量。
    """
    try:
        ifaces = [d for d in enumerate_hid() if d.vid == vid and d.pid == pid]
    except Exception:
        ifaces = []
    if not ifaces:
        return None, None
    r = None
    for d in ifaces:
        r = read_with_protocol(d, proto)
        if r and r.ok:
            break
    if not r or not r.ok:
        for d in ifaces:
            r = listen_with_protocol(d, proto)
            if r and r.ok:
                break
    if not r or not r.ok or r.level is None:
        return None, r
    raw = int(r.level)
    if abs(raw - int(level)) <= 2:
        return "direct", r
    if abs((100 - raw) - int(level)) <= 2:
        return "invert", r
    return None, r


def verify_saved(vid: int, pid: int, proto: dict):
    try:
        ifaces = [d for d in enumerate_hid() if d.vid == vid and d.pid == pid]
    except Exception:
        ifaces = []
    if not ifaces:
        return None
    for d in ifaces:
        r = read_with_protocol(d, proto)
        if r and r.ok:
            return r
    for d in ifaces:
        r = listen_with_protocol(d, proto)
        if r and r.ok:
            return r
    return None
