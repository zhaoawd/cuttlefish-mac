"""在专用 Cuttlefish 实例验证图形、相机、音频和虚拟无线设备。"""
import argparse
import json
from pathlib import Path
import re
import sys
import time

from ble_data import verify_ble
from cellular import incoming_call
from wifi_data import exchange, verify_wifi
from parity import PACKAGE, ROOT, digest, guest, require, write_json

sys.path.insert(0, str(ROOT))
from cfctl import Device, package_from_apk
from cvd_host import CvdHost, NAME, run_directory
from instance_store import lock


def run(args):
    require(args.device == NAME, "请选择当前宿主的专用 Cuttlefish 实例")
    require(package_from_apk(Device(NAME, 30), args.apk) == PACKAGE, "测试 APK 包名不符")
    def cli(*argv):
        return guest(NAME, *argv)

    def shell(*argv):
        return cli("shell", *argv)

    def media(test, *extras):
        output = shell("am", "instrument", "-w", "-r", "-e", "test", test, *extras, PACKAGE + "/.MediaProbe")
        require("INSTRUMENTATION_CODE: -1" in output.splitlines(), output)
        evidence = re.search(r"^INSTRUMENTATION_RESULT: evidence=(.+)$", output, re.M)
        require(evidence is not None, "缺少 instrumentation 结果")
        return evidence[1]

    def radio(kind):
        def state():
            if kind == "wifi":
                text = shell("cmd", "wifi", "status")
                match = re.search(r"^Wifi is (enabled|disabled)$", text, re.M)
                return None if not match else match[1] == "enabled"
            text = shell("dumpsys", "bluetooth_manager")
            match = re.search(r"^\s*state: (ON|OFF)\s*$", text, re.M)
            return None if not match else match[1] == "ON"

        def switch(on):
            if kind == "wifi":
                shell("cmd", "wifi", "set-wifi-enabled", "enabled" if on else "disabled")
            else:
                shell("cmd", "bluetooth_manager", "enable" if on else "disable")
            until = time.monotonic() + 20
            while state() != on:
                require(time.monotonic() < until, f"{kind} 未进入目标状态 {on}")
                time.sleep(0.5)

        initial = state()
        require(initial is not None, f"{kind} 初始状态未知")
        try:
            switch(not initial)
        finally:
            switch(initial)
        return {"initial_enabled": initial, "toggled": True, "restored": True}

    def cellular():
        sim = shell("getprop", "gsm.sim.state")
        data = shell("dumpsys", "telephony.registry")
        voice = re.search(r"mVoiceRegState=(\d+\(\w+\))", data)
        packet = re.search(r"mDataRegState=(\d+\(\w+\))", data)
        result = {"sim": sim, "voice": voice[1] if voice else None, "data": packet[1] if packet else None}
        require(sim == "LOADED" and result["voice"] == result["data"] == "0(IN_SERVICE)", str(result))
        return result

    require("package:" + PACKAGE not in cli("app", "list"), "测试包已存在")
    args.out.mkdir(mode=0o700, parents=True, exist_ok=False)
    report = dict(device=NAME, fingerprint=shell("getprop", "ro.build.fingerprint"),
                  fixture_sha256=digest(args.apk), checks={}, errors={})
    probes = [("gles", lambda: media("gles")), ("audio", lambda: media("audio")),
              ("camera", lambda: media("camera")), ("wifi", lambda: radio("wifi")),
              ("bluetooth", lambda: radio("bluetooth")), ("cellular", cellular)]
    if args.wifi_data or args.wifi_internet:
        probes.append(("wifi_data", lambda: verify_wifi(shell, media, args.wifi_internet)))
    if args.cellular:
        instance = json.loads(CvdHost(run_directory()).config.read_text())["instances"]["1"]
        gateway = instance["ril_gateway"]
        def cell_data():
            require(gateway in ("10.0.2.2", "192.168.97.1"), "需要测试实例的本地蜂窝网关")
            bind = "127.0.0.1" if NAME == "darwin-1" else gateway
            data = exchange(media, cellular=True, bind=bind, gateway=gateway)
            require(data["guest_ipv4"] == instance["ril_ipaddr"], "蜂窝源地址与 RIL 配置不符")
            return data
        probes.extend([("cellular_data", cell_data), ("incoming_call", lambda: incoming_call(shell, instance))])
    if args.ble_address:
        probes.append(("ble_data", lambda: verify_ble(cli, shell, media, args.ble_address)))
    try:
        cli("app", "install", args.apk)
        cli("input", "key", "KEYCODE_WAKEUP")
        shell("wm", "dismiss-keyguard")
        shell("pm", "grant", PACKAGE, "android.permission.CAMERA")
        if args.ble_address:
            shell("pm", "grant", PACKAGE, "android.permission.BLUETOOTH_CONNECT")
        for name, probe in probes:
            try:
                report["checks"][name] = probe()
            except Exception as exc:
                report["errors"][name] = str(exc)
            write_json(args.out / "result.json", report)
    finally:
        try:
            if "package:" + PACKAGE in cli("app", "list"):
                cli("app", "uninstall", PACKAGE)
            require("package:" + PACKAGE not in cli("app", "list"), "测试包卸载失败")
            report["uninstalled"] = True
        finally:
            report["ok"] = len(report["checks"]) == len(probes) and report.get("uninstalled", False)
            write_json(args.out / "result.json", report)
    require(report["ok"], str(report["errors"]))
    print(args.out / "result.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--wifi-data", action="store_true", help="通过 VirtWifi 收发数据并恢复初态")
    parser.add_argument("--cellular", action="store_true", help="通过本机模拟调制解调器验证来电及蜂窝 TCP")
    parser.add_argument("--ble-address", help="已启动的专用 BLE 测试对端地址")
    parser.add_argument("--wifi-internet", action="store_true", help="使用 wlan0 请求 Android 公网连通性检查端点")
    args = parser.parse_args()
    with lock(run_directory() / ".cfctl-control.lock"):
        run(args)
