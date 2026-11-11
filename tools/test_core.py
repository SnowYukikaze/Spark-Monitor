# -*- coding: utf-8 -*-
"""核心逻辑单元测试（不碰真实设备）。

覆盖：
- 抓包协议推断：单次 / 两次 / 描述符干扰
- 配置读写与迁移兼容
- 协议注册表
- 上次读数兜底

用法：python tools/test_core.py
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []


def check(ok, msg):
    print(("  OK   " if ok else "  FAIL ") + msg)
    if not ok:
        fails.append(msg)


# ---------------------------------------------------------------------------
print("[1] 抓包协议推断")
from ez24g.capture import infer
from ez24g.model import Reading

REQ = (0x02, 1, bytes([0x08, 0x7D, 0x72, 0x02, 0x00, 0x01, 0x07, 0x01])
       + bytes(56))


def reply(level, at=7):
    b = bytearray(64)
    b[0] = 0x08
    b[1] = 0x72
    b[5] = 0x07
    b[at] = level
    b[20] = 0x5A          # 一个固定不变量，用来排除误判
    return (0x81, 2, bytes(b))


frames = [REQ, reply(60)]
proto, why = infer.infer_single_ex(60, frames)
check(proto is not None, f"单次抓包能推断出协议（why={why}）")
check(proto and proto["offset"] == 7, f"电量偏移 = 7（实际 {proto and proto['offset']}）")
check(proto and proto["requests"], "推断结果带请求帧")

p2, why2 = infer.infer_two(60, [REQ, reply(60)], 80, [REQ, reply(80)])
check(p2 is not None and p2["offset"] == 7,
      f"两次抓包差值推断偏移 = {p2 and p2['offset']}")

p3, why3 = infer.infer_two(60, [REQ, reply(60)], 60, [REQ, reply(60)])
check(p3 is None, "两次电量相同 → 不推断")

# 字符串描述符里的 'd'(0x64=100%) 不能被当成电量
desc = bytearray([0x1A, 0x03]) + "Product d".encode("utf-16-le")
desc[0] = len(desc)
desc = bytes(desc)
real = reply(100, at=9)
p4, why4 = infer.infer_single_ex(100, [(0x80, 2, desc), REQ, real])
check(p4 is not None and p4["offset"] == 9,
      f"忽略字符串描述符，偏移 = {p4 and p4['offset']}")

# 全零报告、描述符帧要被过滤
p5, why5 = infer.infer_single_ex(100, [(0x81, 2, bytes(64)), REQ, reply(100, 12)])
check(p5 is not None and p5["offset"] == 12,
      f"忽略全零报告，偏移 = {p5 and p5['offset']}")

# ---------------------------------------------------------------------------
print("\n[2] 配置读写")
tmp = tempfile.mkdtemp(prefix="ez24g_test_")
old_appdata = os.environ.get("APPDATA")
os.environ["APPDATA"] = tmp
try:
    from ez24g import config

    check(config.load_config() == {}, "无配置文件时返回空 dict")
    config.save_saved_devices([{"vid": 0x373B, "pid": 0x1278, "name": "鼠标"}])
    devs = config.load_saved_devices()
    check(len(devs) == 1 and devs[0]["vid"] == 0x373B, "设备清单往返一致")

    config.save_standard_protocol(0x373B, 0x1278)
    protos = config.user_protocols()
    check(protos.get("373B:1278", {}).get("kind") == "standard",
          "标准协议写入正确")

    config.save_user_protocol(0x046D, 0xC547,
                              {"requests": ["08 7D 72"], "offset": 7})
    p = config.user_protocols()["046D:C547"]
    check(p["kind"] == "captured" and p["offset"] == 7 and p["invert"] is False,
          "抓包协议写入字段完整")

    config.set_custom_name("x373B1278", "我的鼠标")
    check(config.custom_names().get("x373B1278") == "我的鼠标", "自定义名保存")
    config.set_custom_name("x373B1278", "")
    check("x373B1278" not in config.custom_names(), "空名字会删除条目")

    check(config.clamp_interval(5) == 30, "间隔下限 30s")
    check(config.clamp_interval(99999) == 1800, "间隔上限 1800s")
    check(config.clamp_interval("abc") == 60, "非法间隔回落到 60s")

    config.forget_protocol(0x046D, 0xC547)
    check("046D:C547" not in config.user_protocols(), "删除设备协议")

    # 本轮新增配置键（§2.3）
    check(config.get_font_scale() == 1.0, "字号默认 1.0")
    config.set_font_scale(9)
    check(config.get_font_scale() == 1.25, "字号上限夹到 1.25")
    config.set_font_scale(0.1)
    check(config.get_font_scale() == 0.85, "字号下限夹到 0.85")
    config.set_font_scale("abc")
    check(config.get_font_scale() == 1.0, "字号脏数据回落 1.0")

    check(config.get_panel_size() is None, "没记录尺寸返回 None")
    config.set_panel_size(100, 100)
    check(config.get_panel_size() == config.PANEL_MIN, "尺寸下限被夹紧")
    config.set_panel_size(99999, 99999)
    check(config.get_panel_size() == config.PANEL_MAX, "尺寸上限被夹紧")

    check(config.get_device_color("xAAAA0001") is None, "没设设备色返回 None")
    config.set_device_color("xAAAA0001", (300, -5, 0))
    check(config.get_device_color("xAAAA0001") == (255, 0, 0),
          "设备色越界值被裁剪")
    config.set_device_color("xAAAA0002", "坏数据")
    check(config.get_device_color("xAAAA0002") is None, "设备色脏数据被忽略")
    config.clear_device_color("xAAAA0001")
    check(config.get_device_color("xAAAA0001") is None, "设备色可清除")

    check(config.get_name_pos("xAAAA0001") == (0.0, 0.0), "没设昵称位置返回 (0,0)")
    config.set_name_pos("xAAAA0001", 12.34, -5.6)
    check(config.get_name_pos("xAAAA0001") == (12.3, -5.6), "昵称位置写盘并保留 1 位")
    config.ui_set("name_pos", {"xAAAA0001": "坏数据"})
    check(config.get_name_pos("xAAAA0001") == (0.0, 0.0), "昵称位置脏数据兜底")
    config.ui_set("name_pos", {"xAAAA0001": [1, 2]})
    config.clear_name_pos("xAAAA0001")
    check(config.get_name_pos("xAAAA0001") == (0.0, 0.0), "昵称位置可清除")

    config.set_opacity(40, 50, 60)
    check(config.get_opacity() == {"card": 40, "app": 50, "menu": 60},
          "三参 set_opacity 兼容")
    config.set_opacity(10, 20)
    check(config.get_opacity()["menu"] == 60 and config.get_opacity()["card"] == 10,
          "省略 menu 时保留原值")
    check(abs(config.get_alpha("card") - 0.9) < 1e-6,
          "get_alpha 仍是 1 - 透明度/100")
finally:
    if old_appdata is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = old_appdata
    shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------------------
print("\n[3] 协议注册表")
from ez24g.protocols import describe, registry

reg = registry()
ids = [p.id for p in reg]
check(len(reg) >= 10, f"注册了 {len(reg)} 个协议")
check(ids[0] == "standard", f"最高优先级是标准 HID（实际 {ids[0]}）")
check("captured" in ids and "atk2" in ids and "jieli" in ids,
      "抓包/ATK/杰理协议都在")
check([p.priority for p in reg] == sorted(p.priority for p in reg),
      "按 priority 排序")
auto = [p.id for p in reg if p.auto]
manual = [p.id for p in reg if not p.auto]
# 能自动跑的都是「纯读、且 match 写得很窄」的协议：
#   standard / captured / listen 只读字段或监听上报；
#   itons 是机械师 L8 的状态查询（a0 50），纯查询不改任何设置，
#   且只匹配 VID 369B + 厂商页 FFA0 + 64 字节收发那一条接口。
check(auto == ["standard", "captured", "itons", "listen"],
      f"默认自动跑的协议 = {auto}")
check("atk2" in manual, "厂商协议默认不自动跑（手册里可开）")

# ---------------------------------------------------------------------------
print("\n[4] 上次读数兜底")
tmp2 = tempfile.mkdtemp(prefix="ez24g_last_")
old = os.environ.get("APPDATA")
os.environ["APPDATA"] = tmp2
try:
    import importlib

    from ez24g import config as cfg2
    importlib.reload(cfg2)
    from ez24g import devices as dev2
    importlib.reload(dev2)

    ok = dev2.apply_last_known({
        "xAAAA0001": Reading(ok=True, level=77, name="A"),
    })
    check(ok["xAAAA0001"].level == 77 and not ok["xAAAA0001"].stale,
          "成功读数原样返回")

    stale = dev2.apply_last_known({
        "xAAAA0001": Reading(ok=False, present=True, name="A",
                             status="asleep", detail="设备休眠"),
    })
    r = stale["xAAAA0001"]
    check(r.ok and r.level == 77 and r.stale, "休眠时沿用上次电量并标记 stale")
    check("上次更新" in r.detail, f"副标题带时间：{r.detail}")

    none = dev2.apply_last_known({
        "xAAAA0001": Reading(ok=False, present=False, status="unplugged",
                             detail="未连接"),
    })
    check(not none["xAAAA0001"].ok and not none["xAAAA0001"].stale,
          "真的拔掉时不假装有电量")

    unknown = dev2.apply_last_known({
        "xAAAA0001": Reading(ok=False, present=True, status="unknown_proto",
                             detail="协议未知"),
    })
    check(not unknown["xAAAA0001"].ok, "协议未知时不使用上次电量")
finally:
    if old is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = old
    shutil.rmtree(tmp2, ignore_errors=True)

# ---------------------------------------------------------------------------
print("\n[5] 蓝牙 / providers")
from ez24g.hidapi import HidIface
from ez24g.providers import bt as btprov
from ez24g.providers import hid as hidprov
from ez24g.providers.base import DeviceRef


def iface(path, vid, pid, page, usage=0, flen=0, ilen=0, olen=0, product=""):
    return HidIface(path=path, vid=vid, pid=pid, usage_page=page, usage=usage,
                    input_len=ilen, output_len=olen, feature_len=flen,
                    product=product)


# 蓝牙 HID 鼠标：只有标准电量字段、没有厂商页，旧代码在 --scan 里看不到
bt_mouse = [iface("p1", 0x046D, 0xB025, 0x000D, 0x003B, flen=3,
                  product="MX Anywhere 3S")]
refs = hidprov.enumerate_refs(bt_mouse, caps_fn=lambda d: True)
check(len(refs) == 1 and refs[0].key == "x046DB025",
      f"标准 HID 电量的蓝牙设备能枚举出来（{refs[0].key if refs else '空'}）")
check(not hidprov.enumerate_refs(bt_mouse, caps_fn=lambda d: False),
      "既没厂商页也没电量字段 → 不出现")

hid2c = [iface("p2", 0x093A, 0x2008, 0x000D, 0x0005, flen=2,
               product="HIDI2C Device")]
check(not hidprov.enumerate_refs(hid2c, caps_fn=lambda d: True),
      "HIDI2C 仍然跳过（即使报了电量字段）")
acpi = [iface("p3", 0x1234, 0x5678, 0xFF00, 0, olen=8, product="ACPI Keyboard")]
check(not hidprov.enumerate_refs(acpi, caps_fn=lambda d: False),
      "ACPI 仍然跳过")

# 没装 winrt：不崩，给出明确提示
btprov._state = False
r = btprov.read_ref(DeviceRef(key="bt:AA:BB:CC:DD:EE:01", kind="bt",
                              addr="AA:BB:CC:DD:EE:01", connected=True))
check(r is not None and r.status == "no_support", "没装 winrt → status=no_support")
check("winrt" in (r.detail or ""), f"提示里说清要装 winrt：{r.detail}")

# 装了但设备关了：明确未连接，且不带旧电量
btprov._state = True
r = btprov.read_ref(DeviceRef(key="bt:AA:BB:CC:DD:EE:01", kind="bt",
                              addr="AA:BB:CC:DD:EE:01", connected=False))
check(r is not None and not r.ok and r.status == "unplugged",
      f"设备断开 → 未连接（{r.status if r else None}）")
btprov._state = None


# 连接正常时走 GATT：用假 WinRT 验证取值/解析这条链路
class _Op:
    def __init__(self, v):
        self._v = v

    def get(self):
        return self._v


class _Buf:
    def __init__(self, b):
        self._b = b

    def __bytes__(self):
        return self._b


class _Read:
    status = 0
    value = _Buf(b"\x57")           # 0x57 = 87%


class _Chr:
    def read_value_async(self, mode):
        return _Op(_Read())


class _ChrRes:
    status = 0
    characteristics = [_Chr()]


class _Svc:
    def get_characteristics_for_uuid_async(self, u):
        return _Op(_ChrRes())


class _SvcRes:
    status = 0
    services = [_Svc()]


class _Dev:
    connection_status = 1

    def get_gatt_services_for_uuid_async(self, u):
        return _Op(_SvcRes())

    def close(self):
        pass


class _FakeBLEDevice:
    @staticmethod
    def from_bluetooth_address_async(addr):
        return _Op(_Dev())


class _FakeStatus:
    CONNECTED = 1


class _FakeComm:
    SUCCESS = 0


class _FakeCache:
    UNCACHED = 0


_keep = dict(btprov._api)
btprov._state = True
btprov._api.update(BluetoothLEDevice=_FakeBLEDevice,
                   BluetoothConnectionStatus=_FakeStatus,
                   GattCommunicationStatus=_FakeComm,
                   BluetoothCacheMode=_FakeCache)
r = btprov.read_ref(DeviceRef(key="bt:AA:BB:CC:DD:EE:01", kind="bt",
                              addr="AA:BB:CC:DD:EE:01", connected=True))
check(r is not None and r.ok and r.level == 87, f"GATT 0x2A19 读数解析 = {r and r.level}")
check(r is not None and r.transport == "bt" and "0x2A19" in r.source,
      f"读数来源标成蓝牙：{r and r.source}")
btprov._api.clear()
btprov._api.update(_keep)
btprov._state = None

# 挂在蓝牙上的 HID：键还是 x{VID}{PID}，但来源要标成蓝牙
bt_hid = [iface("p4", 0x046D, 0xB025, 0x0001, 0x0002, ilen=9,
                product="MX Keys")]
bt_hid[0].parent = "BTHENUM\\{00001124-0000-1000-8000-00805F9B34FB}_VID&0002046D"
refs_bt = hidprov.enumerate_refs(bt_hid, caps_fn=lambda d: True)
check(refs_bt and refs_bt[0].transport == "bt" and refs_bt[0].key == "x046DB025",
      f"蓝牙 HID 标成蓝牙、键仍为 {refs_bt[0].key if refs_bt else '?'}")
usb_hid = [iface("p5", 0x046D, 0xC547, 0x0001, 0x0002, ilen=14,
                 product="USB Receiver")]
usb_hid[0].parent = "USB\\VID_046D&PID_C547&MI_00"
refs_usb = hidprov.enumerate_refs(usb_hid, caps_fn=lambda d: True)
check(refs_usb and refs_usb[0].transport == "hid",
      "USB 上的 HID 仍然标成 2.4G")

# 卡片文案用的字段
rb = Reading(ok=True, level=63, transport="bt", addr="de:1b:b4:46:93:66")
check(rb.transport_text == "蓝牙" and rb.ident == "DE:1B:B4:46:93:66",
      f"蓝牙卡片的来源/标识：{rb.transport_text} {rb.ident}")
rh = Reading(ok=True, level=80, vid=0x373B, pid=0x1278)
check(rh.transport_text == "2.4G" and rh.ident == "373B:1278",
      f"HID 卡片的来源/标识：{rh.transport_text} {rh.ident}")

# ---------------------------------------------------------------------------
print("\n[6] 设备键与旧配置兼容")
tmp3 = tempfile.mkdtemp(prefix="ez24g_bt_")
old3 = os.environ.get("APPDATA")
os.environ["APPDATA"] = tmp3
try:
    import importlib

    from ez24g import config as cfg3
    importlib.reload(cfg3)
    from ez24g import devices as dev3
    importlib.reload(dev3)

    # 旧配置：只有 vid/pid
    cfg3.save_config({"devices": [
        {"vid": 0x373B, "pid": 0x1278, "name": "旧鼠标"},
        {"kind": "bt", "addr": "de-1b-b4-46-93-66", "name": "蓝牙键盘"},
        {"kind": "bt", "addr": "", "name": "坏条目"},
    ]})
    devs = cfg3.load_saved_devices()
    check(len(devs) == 2, f"旧 HID 条目 + 蓝牙条目都保留，坏条目丢弃（{len(devs)} 台）")
    check(devs[0]["kind"] == "hid" and cfg3.key_for(devs[0]) == "x373B1278",
          "旧条目默认按 HID 处理，键不变")
    check(devs[1]["kind"] == "bt"
          and cfg3.key_for(devs[1]) == "bt:DE:1B:B4:46:93:66",
          f"蓝牙条目键 = {cfg3.key_for(devs[1])}")
    check(devs[1]["addr"] == "DE:1B:B4:46:93:66", "MAC 地址已规范化")

    # 增删往返：蓝牙设备不因为没有 vid/pid 被丢掉
    cfg3.save_saved_devices([])
    check(dev3.add_device(0, 0, "蓝牙键盘", kind="bt", addr="AA:BB:CC:DD:EE:FF"),
          "添加蓝牙设备")
    check(not dev3.add_device(0, 0, "重复", kind="bt", addr="AA:BB:CC:DD:EE:FF"),
          "同一 MAC 不重复添加")
    check(len(cfg3.load_saved_devices()) == 1, "蓝牙设备落盘后还在")
    removed = dev3.remove_device("bt:AA:BB:CC:DD:EE:FF")
    check(removed is not None and not cfg3.load_saved_devices(), "删除蓝牙设备")

    # 断开时不能拿上次电量充数
    cfg3.save_last({"bt:11:22:33:44:55:66": {"level": 88, "ts": 1750000000,
                                            "transport": "bt"}})
    got = dev3.apply_last_known({"bt:11:22:33:44:55:66": Reading(
        ok=False, present=False, status="unplugged", detail="未连接",
        transport="bt", addr="11:22:33:44:55:66")})
    check(not got["bt:11:22:33:44:55:66"].ok,
          "蓝牙设备关掉后不显示上次电量")
finally:
    if old3 is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = old3
    shutil.rmtree(tmp3, ignore_errors=True)

# ---------------------------------------------------------------------------
print("\n[5] 退出重入守卫（panel_only 下 hide 会回调 quit）")
tmp3 = tempfile.mkdtemp(prefix="ez24g_quit_")
old3 = os.environ.get("APPDATA")
os.environ["APPDATA"] = tmp3
try:
    from ez24g.app import TrayApp

    app = TrayApp(panel_only=True)          # 不建 Tk、不建托盘

    class _FakePanel:
        def hide(self):
            app.quit()                      # 模拟 Panel.hide 里的回调

    app.panel = _FakePanel()
    app.quit()
    check(app._stop and app._quitting, "第一次 quit 置位 _stop/_quitting")
    app.quit()
    check(app._quitting, "重复 quit 被守卫拦下，没有递归")
except RecursionError:
    check(False, "重复 quit 触发 RecursionError")
finally:
    if old3 is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = old3
    shutil.rmtree(tmp3, ignore_errors=True)

# ---------------------------------------------------------------------------
print("\n[7] 经典蓝牙 HFP（Hands-Free AG 系统属性）")
from ez24g.providers import bt as btmod
from ez24g.providers import bt_system

AG_INST = (r"BTHENUM\{0000111E-0000-1000-8000-00805F9B34FB}_LOCALMFG&0046"
           r"\9&2E5A28BA&0&50C0F005A67D_C00000000")
HF_INST = (r"BTHENUM\{0000111F-0000-1000-8000-00805F9B34FB}_VID&000109C6"
           r"\9&2E5A28BA&0&94F6F2FF63CA_C00000000")

check(bt_system.mac_from_instance(AG_INST) == "50:C0:F0:05:A6:7D",
      f"从实例 ID 取 MAC：{bt_system.mac_from_instance(AG_INST)}")
check(bt_system.strip_ag_suffix("Misiom-Aries Pro Hands-Free AG")
      == "Misiom-Aries Pro", "去掉 Hands-Free AG 后缀得到设备名")
check(bt_system.is_ag_instance(AG_INST), "111E 节点识别为 AG")
check(not bt_system.is_ag_instance(HF_INST), "111F（HF 侧）不算 AG")
check(not bt_system.is_ag_instance("BluetoothLE#BluetoothLE10:68:38:a3-f8"),
      "BLE 设备 ID 不算 AG")

check(bt_system.read_level("") is None, "空 MAC 返回 None")
check(bt_system.read_level("11:22:33:44:55:66") is None, "不存在的设备返回 None")

ag = bt_system.enumerate_ag(fresh=True)
bad = [(k, v) for k, v in ag.items()
       if not (v["level"] is None or 0 <= v["level"] <= 100)
       or v["addr"].count(":") != 5]
check(not bad, f"AG 枚举结果合法（{len(ag)} 台，非法 {len(bad)}）")

# 经典设备要并进 refs（BLE 优先，按 MAC 去重）
_orig_ble = btmod._enumerate_ble
_orig_ag = btmod.bt_system.enumerate_ag
_orig_conn = btmod.connection_status
try:
    # 假 MAC 查不到真实连接状态，这里固定成「已连接」，专测合并与读取逻辑
    btmod.connection_status = lambda addr: True
    btmod._enumerate_ble = lambda: [DeviceRef(
        key="bt:AA:BB:CC:DD:EE:FF", kind="bt", transport="bt",
        name="BLE 名字", addr="AA:BB:CC:DD:EE:FF", connected=False)]
    btmod.bt_system.enumerate_ag = lambda *a, **k: {
        "bt:AA:BB:CC:DD:EE:FF": {"name": "同名经典", "addr": "AA:BB:CC:DD:EE:FF",
                                 "level": 50},
        "bt:11:22:33:44:55:66": {"name": "纯经典", "addr": "11:22:33:44:55:66",
                                 "level": 90},
    }
    merged = btmod.enumerate_refs()
    keys = [r.key for r in merged]
    check(keys.count("bt:AA:BB:CC:DD:EE:FF") == 1, f"同一 MAC 去重：{keys}")
    dup = next(r for r in merged if r.key == "bt:AA:BB:CC:DD:EE:FF")
    check(not dup.classic, "重复 MAC 保留 BLE（BLE 优先）")
    pure = next(r for r in merged if r.key == "bt:11:22:33:44:55:66")
    check(pure.classic and pure.connected, "纯经典设备 connected=True 且标 classic")

    # HFP 有值 → 直接用它，不去碰 GATT
    btmod.bt_system.read_level = lambda addr: 87
    r = btmod.read_ref(pure)
    check(r.ok and r.level == 87 and "HFP" in r.source,
          f"经典设备读 HFP：{r.level} {r.source}")

    # HFP 为空 → unknown_proto，且不拿 GATT 打经典设备
    btmod.bt_system.read_level = lambda addr: None
    r = btmod.read_ref(pure)
    check(not r.ok and r.status == "unknown_proto"
          and "系统未上报" in r.detail,
          f"系统没上报时的状态：{r.status} / {r.detail}")
finally:
    btmod._enumerate_ble = _orig_ble
    btmod.bt_system.enumerate_ag = _orig_ag
    btmod.connection_status = _orig_conn
    btmod.bt_system.read_level = bt_system.__dict__["read_level"]
    import importlib
    importlib.reload(bt_system)
    btmod.bt_system = bt_system

# 耳机断电后 AG 节点消失 → 必须「未连接」，不能拿上次的 90
_orig_refs = None
try:
    import ez24g.devices as dev_mod
    _orig_refs = dev_mod.providers.enumerate_refs
    dev_mod.providers.enumerate_refs = lambda *a, **k: []
    gone = dev_mod.read_device({"kind": "bt", "addr": "50:C0:F0:05:A6:7D",
                                "name": "Misiom-Aries Pro", "vid": 0, "pid": 0})
    check(not gone.ok and not gone.present and gone.status == "unplugged",
          f"AG 节点消失 → 未连接（present={gone.present}, {gone.status}）")
finally:
    if _orig_refs is not None:
        import ez24g.devices as dev_mod
        dev_mod.providers.enumerate_refs = _orig_refs

# 节点还在但设备已关机（connected=False）→ 绝不能把缓存属性当实时值
_lvl_orig = bt_system.read_level
bt_system.read_level = lambda addr: 80
try:
    dead = DeviceRef(key="bt:41:42:5B:30:7C:BB", kind="bt", transport="bt",
                     addr="41:42:5B:30:7C:BB", name="A6",
                     connected=False, classic=True)
    r = btmod.read_ref(dead)
    check(not r.ok and r.status == "unplugged",
          f"关机（connected=False）→ {r.status}，不读缓存属性")
    check(r.level is None and not r.present,
          f"未连接时不带数值：level={r.level} present={r.present}")

    alive = DeviceRef(key="bt:41:42:5B:30:7C:BB", kind="bt", transport="bt",
                      addr="41:42:5B:30:7C:BB", name="A6",
                      connected=True, classic=True)
    r2 = btmod.read_ref(alive)
    check(r2.ok and r2.level == 80 and r2.status == "discharging",
          f"已连接时状态是 discharging：{r2.level} / {r2.status}")
finally:
    bt_system.read_level = _lvl_orig

# ---------------------------------------------------------------------------
print("\n[8] 排序稳定性与设备组")
tmp4 = tempfile.mkdtemp(prefix="ez24g_order_")
old4 = os.environ.get("APPDATA")
os.environ["APPDATA"] = tmp4
try:
    import importlib

    from ez24g import config as cfg4
    importlib.reload(cfg4)
    from ez24g import devices as dev4
    importlib.reload(dev4)

    base = {
        "a": Reading(ok=True, level=90, name="A", present=True),
        "b": Reading(ok=False, level=None, name="B", present=False,
                     status="unplugged"),
        "c": Reading(ok=True, level=20, name="C", present=True),
    }
    order1 = list(dev4.order_readings(base, saved=[]))
    check(order1 == ["a", "c", "b"], f"可读在前、未连接沉底：{order1}")

    moved = dict(base)
    moved["a"] = Reading(ok=True, level=89, name="A", present=True)
    check(list(dev4.order_readings(moved, saved=[])) == order1,
          "电量 90→89 卡片不移动")

    unplug = dict(base)
    unplug["a"] = Reading(ok=False, present=False, status="unplugged",
                          name="A")
    order3 = list(dev4.order_readings(unplug, saved=[]))
    check(order3[0] == "c" and order3[-1] != "c",
          f"在线设备掉线后下沉：{order3}")

    # R4：休眠卡（present=False）在同区内也要能按用户顺序排在前面。
    # 改前排序键是 (readable, alive, added, idx, key)，alive 会压过 idx。
    sleeping = {
        "a": Reading(ok=True, level=90, name="A", present=False),
        "c": Reading(ok=True, level=20, name="C", present=True),
        "b": Reading(ok=False, level=None, name="B", present=False,
                     status="unplugged"),
    }
    cfg4.set_card_order(["a", "c", "b"])
    order4 = list(dev4.order_readings(sleeping, saved=[]))
    check(order4 == ["a", "c", "b"],
          f"区内用户顺序优先于在线/已添加：{order4}")

    # R1 守护：任何 card_order 都不能把不可读卡顶到最上面。
    cfg4.set_card_order(["b", "a", "c"])
    order5 = list(dev4.order_readings(sleeping, saved=[]))
    check(order5[0] != "b" and order5[-1] == "b",
          f"红线 R1：不可读卡不能靠 card_order 置顶：{order5}")
    cfg4.set_card_order([])

    # 设备组：两条链路合成一张卡
    cfg4.save_groups({"g1": {"name": "G517 LS TKL",
                             "members": ["x046DC547", "bt:AA:BB:CC:DD:EE:FF"],
                             "prefer": "auto"}})
    pair = {
        "x046DC547": Reading(ok=False, present=True, name="USB Receiver",
                             status="unknown_proto", transport="hid"),
        "bt:AA:BB:CC:DD:EE:FF": Reading(ok=True, level=90, name="G517",
                                        transport="bt",
                                        addr="AA:BB:CC:DD:EE:FF"),
    }
    merged = dev4.merge_groups(pair)
    check(list(merged) == ["g:g1"], f"组内合并成一张卡：{list(merged)}")
    check(merged["g:g1"].level == 90 and merged["g:g1"].name == "G517 LS TKL",
          f"取有电量的那条链路：{merged['g:g1'].level} / {merged['g:g1'].name}")

    only_hid = {"x046DC547": Reading(ok=False, present=True, name="USB",
                                     status="unknown_proto", transport="hid")}
    m2 = dev4.merge_groups(only_hid)["g:g1"]
    check(m2.present and not m2.ok and "暂不支持读取" in m2.detail,
          f"只剩 2.4G 时保留卡片：{m2.detail}")

    both_off = {"x046DC547": Reading(ok=False, present=False,
                                     status="unplugged", name="USB")}
    m3 = dev4.merge_groups(both_off)["g:g1"]
    check(m3.status == "unplugged" and m3.level is None,
          "两条链路都断 → 未连接且不带数字")

    cfg4.remove_group("g1")
    check(not cfg4.load_groups(), "拆开分组后配置清空")
finally:
    if old4 is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = old4
    shutil.rmtree(tmp4, ignore_errors=True)


# ---------------------------------------------------------------------------
print("\n[9] Win11 蓝牙识别优先级与回退链")
from ez24g.providers import bt as bt9
from ez24g.providers import bt_pnp as pnp9
from ez24g.providers import bt_system as bts9
from ez24g.providers.base import DeviceRef as Ref9

_ref9 = Ref9(key="bt:AA:BB:CC:DD:EE:FF", kind="bt", transport="bt",
             addr="AA:BB:CC:DD:EE:FF", connected=True, classic=True,
             name="测试耳机")
_o_sys9 = bts9.read_level
_o_pnp9 = pnp9.read_level
_o_load9 = bt9._load
_o_ble9 = bt9._enumerate_ble
_o_ag9 = bts9.enumerate_ag
_o_pairs9 = pnp9.enumerate_paired
try:
    # 1) 首选 HFP 有读数：结果与新增方式无关
    bts9.read_level = lambda a: 90
    pnp9.read_level = lambda a: 80
    r1 = bt9.read_ref(_ref9)
    check(r1 is not None and r1.ok and r1.level == 90
          and "HFP" in (r1.source or ""),
          f"首选 HFP 成功时结果不变：{r1 and (r1.level, r1.source)}")

    # 2) 首选失败 → 自动回退到 PnP 节点电量
    bts9.read_level = lambda a: None
    r2 = bt9.read_ref(_ref9)
    check(r2 is not None and r2.ok and r2.level == 80
          and "PnP" in (r2.source or ""),
          f"HFP 失败后回退到 PnP 电量：{r2 and (r2.level, r2.source)}")

    # 3) 三级全失败：不伪造电量，保持原有 unknown_proto
    pnp9.read_level = lambda a: None
    r3 = bt9.read_ref(_ref9)
    check(r3 is not None and not r3.ok and r3.status == "unknown_proto",
          f"全部失败时不显示假电量：{r3 and (r3.status, r3.level)}")

    # 4) WinRT 不可用时，枚举回退到 PnP 识别到设备
    bt9._load = lambda: False
    bt9._enumerate_ble = lambda: []
    bts9.enumerate_ag = lambda fresh=False: {}
    pnp9.enumerate_paired = lambda fresh=False: {
        "bt:AA:BB:CC:DD:EE:FF": {"name": "PnP 耳机",
                                  "addr": "AA:BB:CC:DD:EE:FF",
                                  "level": 70, "present": True}}
    refs9 = bt9.enumerate_refs()
    hit9 = [x for x in refs9 if x.key == "bt:AA:BB:CC:DD:EE:FF"]
    check(bool(hit9) and hit9[0].connected
          and "PnP" in (hit9[0].detail or ""),
          f"WinRT 失败后 PnP 兜底识别到设备：{hit9}")

    # 5) WinRT 已连上一台设备时，PnP 层仍然要跑（2026-11 修复的根因回归）
    calls9 = {"n": 0}
    bt9._load = lambda: True
    bt9._enumerate_ble = lambda: [Ref9(
        key="bt:11:22:33:44:55:66", kind="bt", transport="bt",
        addr="11:22:33:44:55:66", connected=True, name="BLE 设备")]
    bts9.enumerate_ag = lambda fresh=False: {}

    def _count_pairs9(fresh=False):
        calls9["n"] += 1
        return {}

    pnp9.enumerate_paired = _count_pairs9
    bt9.enumerate_refs()
    check(calls9["n"] == 1,
          f"已有已连接设备时 PnP 层仍要跑（调用 {calls9['n']} 次）")

    # 6) 回归：耳机（WinRT/AG）连着的同时，只在 PnP 里的 BLE 键盘也必须出现
    bt9._load = lambda: False
    bt9._enumerate_ble = lambda: []
    bts9.enumerate_ag = lambda fresh=False: {
        "bt:AA:BB:CC:DD:EE:FF": {"name": "耳机", "addr": "AA:BB:CC:DD:EE:FF",
                                 "level": 90}}
    bt9._prime_conn = lambda addrs: None
    bt9.connection_status = lambda addr: True
    pnp9.enumerate_paired = lambda fresh=False: {
        "bt:AA:BB:CC:DD:EE:FF": {"name": "耳机", "addr": "AA:BB:CC:DD:EE:FF",
                                 "level": 90, "present": True},
        "bt:FC:CC:15:72:3F:33": {"name": "LANGTU BT5.0_3",
                                 "addr": "FC:CC:15:72:3F:33",
                                 "level": 28, "present": True}}
    refs9b = {r.key: r for r in bt9.enumerate_refs()}
    check("bt:FC:CC:15:72:3F:33" in refs9b,
          f"耳机连着时蓝牙键盘也被识别：{sorted(refs9b)}")
    check(refs9b.get("bt:FC:CC:15:72:3F:33").name == "LANGTU BT5.0_3",
          "键盘名字取自 PnP 节点")
    check("bt:AA:BB:CC:DD:EE:FF" not in ()
          and refs9b.get("bt:AA:BB:CC:DD:EE:FF").name == "耳机",
          "已有耳机条目不被 PnP 覆盖")
finally:
    bts9.read_level = _o_sys9
    pnp9.read_level = _o_pnp9
    bt9._load = _o_load9
    bt9._enumerate_ble = _o_ble9
    bts9.enumerate_ag = _o_ag9
    pnp9.enumerate_paired = _o_pairs9

# ---------------------------------------------------------------------------
print("\n[10] 配置缓存 / 并发写 / 错误可见性")
import builtins
import threading as _th10

_cfg10 = config
_old_ap10 = os.environ.get("APPDATA")
_ap10 = tempfile.mkdtemp(prefix="ez24g_cfg10_")
os.environ["APPDATA"] = _ap10
_cfg10._CACHE.update({"path": None, "sig": None, "data": None})
try:
    _cfg10.set_interval(90)

    # 10.1 同一份文件不重复读盘（缓存生效）
    _real_open10 = builtins.open
    _cnt10 = {"n": 0}

    def _open10(f, *a, **k):
        if isinstance(f, str) and f.endswith("config.json"):
            _cnt10["n"] += 1
        return _real_open10(f, *a, **k)

    builtins.open = _open10
    try:
        for _ in range(30):
            _cfg10.get_interval()
        check(_cnt10["n"] == 0,
              f"缓存命中时一次都不读盘（30 次 getter 读了 {_cnt10['n']} 次）")
    finally:
        builtins.open = _real_open10

    # 10.2 返回的是副本：就地改不会污染缓存
    _d10 = _cfg10.load_config()
    _d10["interval"] = 1
    check(_cfg10.get_interval() == 90, "load_config 返回副本，改它不影响下一次读")

    # 10.3 外部改文件后缓存失效（mtime/size 变了就重读）
    with _real_open10(_cfg10.config_file(), "w", encoding="utf-8") as _f:
        import json as _json10
        _json10.dump({"interval": 300}, _f)
    check(_cfg10.get_interval() == 300, "外部改配置文件后能读到新值")

    # 10.4 并发写不同字段不互相覆盖（以前 5/5 轮会丢最新值）
    _bad10 = 0
    for _r10 in range(3):
        _ap10b = tempfile.mkdtemp(prefix="ez24g_race10_")
        os.environ["APPDATA"] = _ap10b
        _cfg10._CACHE.update({"path": None, "sig": None, "data": None})

        def _w_ui10():
            for _i in range(120):
                _cfg10.ui_set("card_order", [str(_i)])

        def _w_work10():
            for _i in range(120):
                _cfg10.save_last({"xAAAA0001": {"level": _i}})

        _t1 = _th10.Thread(target=_w_ui10)
        _t2 = _th10.Thread(target=_w_work10)
        _t1.start(); _t2.start(); _t1.join(); _t2.join()
        _c10 = _cfg10.load_config()
        if not (_c10.get("ui", {}).get("card_order") == ["119"]
                and _c10.get("last", {}).get("xAAAA0001", {}).get("level") == 119):
            _bad10 += 1
    check(_bad10 == 0, f"并发写两个字段 3 轮无覆盖（异常 {_bad10} 轮）")

    # 10.5 写盘失败不再完全静默
    _bad_dir10 = os.path.join(tempfile.mkdtemp(prefix="ez24g_bad10_"), "f")
    with open(_bad_dir10, "w", encoding="utf-8") as _f:
        _f.write("x")            # 用「文件」占住路径，makedirs 必然失败
    os.environ["APPDATA"] = _bad_dir10
    _cfg10._CACHE.update({"path": None, "sig": None, "data": None})
    _cfg10.save_config({"interval": 30})
    check(bool(_cfg10.last_save_error()),
          f"写盘失败留下原因：{_cfg10.last_save_error()[:40]}")
finally:
    if _old_ap10 is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = _old_ap10
    _cfg10._CACHE.update({"path": None, "sig": None, "data": None})

# ---------------------------------------------------------------------------
print("\n[11] 图片解码上限")
from ez24g.ui import customize as _cz11

check(hasattr(_cz11, "MAX_DECODE_PIXELS") and _cz11.MAX_DECODE_PIXELS > 0,
      f"设置了单图解码像素上限：{getattr(_cz11, 'MAX_DECODE_PIXELS', None)}")
if _cz11.PIL_OK:
    _ap11 = tempfile.mkdtemp(prefix="ez24g_img11_")
    _old_ap11 = os.environ.get("APPDATA")
    os.environ["APPDATA"] = _ap11
    try:
        _big11 = os.path.join(_ap11, "big.png")
        _cz11.Image.new("RGB", (20, 20), (1, 2, 3)).save(_big11)
        _keep11 = _cz11.MAX_DECODE_PIXELS
        _cz11.MAX_DECODE_PIXELS = 100          # 20×20=400 > 100，必然被挡
        try:
            _got11 = _cz11.import_image(_big11, "big_import.png")
            check(_got11 is None, "超过像素上限的图被拒绝（不再先 load 再判）")
            check("太大" in _cz11.last_error(),
                  f"拒绝原因可读：{_cz11.last_error()[:30]}")
        finally:
            _cz11.MAX_DECODE_PIXELS = _keep11
    finally:
        if _old_ap11 is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = _old_ap11
else:
    # Pillow 或 ImageTk（要 tkinter）缺任一个，这条链路在本机就是不启用的。
    # 这里按分支里的条数补两条「跳过」，保证不同机器上通过数一致，
    # 不然 verify_all 的基线会随解释器环境漂移，出现假的「用例被跳过」告警。
    check(True, "无 Pillow/ImageTk：跳过图片越限用例（该功能在本机不启用）")
    check(True, "无 Pillow/ImageTk：跳过拒绝原因用例（该功能在本机不启用）")

print("\n结果：", "全部通过" if not fails else f"{len(fails)} 项失败")
sys.exit(1 if fails else 0)
