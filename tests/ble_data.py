"""通过英文系统设置配对临时 LE 对端，并核对加密 GATT 回显和取消配对。"""
import re
import xml.etree.ElementTree as ET

from parity import require
from wifi_data import wait_for


def verify_ble(cli, shell, media, address):
    def enabled():
        match = re.search(r"^\s*state: (ON|OFF)\s*$", shell("dumpsys", "bluetooth_manager"), re.M)
        return None if match is None else match[1] == "ON"
    initial = enabled()
    require(initial is not None, "无法读取蓝牙开关")
    try:
        if not initial:
            shell("cmd", "bluetooth_manager", "enable")
            wait_for(lambda: enabled() is True, "蓝牙未启用")
        return paired_exchange(cli, shell, media, address)
    finally:
        if not initial:
            shell("cmd", "bluetooth_manager", "disable")
            wait_for(lambda: enabled() is False, "蓝牙开关未恢复")


def paired_exchange(cli, shell, media, address):
    require(re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", address), "需要测试对端的静态地址")
    name = "CF-" + address.replace(":", "")
    def nodes():
        return ET.fromstring(cli("ui", "tree")).iter("node")
    def has(key, value):
        return any(n.get(key) == value for n in nodes())
    def click(key, value):
        cli("ui", "click", "--" + key, value)
    def settings():
        shell("am", "start", "-W", "-a", "android.settings.BLUETOOTH_SETTINGS")
    def bonds():
        raw = shell("dumpsys", "bluetooth_manager")
        match = re.search(r"(?m)^\s*Bonded devices: (\d+)\s*$", raw)
        require(match is not None, "未找到配对设备数量")
        return int(match[1]), any(name in line for line in raw.splitlines() if "=>" in line)
    initial, exists = bonds()
    require(not exists, "测试对端已配对")
    try:
        settings()
        click("text", "Pair new device")
        click("text", name)
        require(has("text", "Pair with " + name + "?"), "配对对话框的设备名不符")
        click("text", "Pair")
        wait_for(lambda: bonds() == (initial + 1, True), "配对未完成")
        evidence = media("bluetooth-data", "-e", "name", name)
        require(evidence == "bonded encrypted echo bytes=16", evidence)
    finally:
        if bonds()[1]:
            settings()
            click("desc", name + ". Configure device detail.")
            click("text", "Forget")
            require(has("text", "Your phone will no longer be paired with " + name), "取消配对的设备名不符")
            click("text", "Forget device")
        wait_for(lambda: bonds() == (initial, False), "配对状态未恢复")
    return {"echo_bytes": 16, "encrypted": True, "bond_removed": True}
