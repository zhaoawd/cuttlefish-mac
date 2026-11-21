"""在专用 Cuttlefish 实例验证图形、相机、音频和虚拟无线设备。"""
import argparse
from pathlib import Path
import re
import sys
import time

from parity import PACKAGE, ROOT, digest, guest, require, write_json

sys.path.insert(0, str(ROOT))
from cfctl import Device, package_from_apk
from cvd_host import NAME, run_directory
from instance_store import lock


def run(args):
    require(args.device == NAME, "请选择当前宿主的专用 Cuttlefish 实例")
    require(package_from_apk(Device(NAME, 30), args.apk) == PACKAGE, "测试 APK 包名不符")
    def cli(*argv):
        return guest(NAME, *argv)

    def shell(*argv):
        return cli("shell", *argv)

    def media(test):
        output = shell("am", "instrument", "-w", "-r", "-e", "test", test, PACKAGE + "/.MediaProbe")
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
    try:
        cli("app", "install", args.apk)
        cli("input", "key", "KEYCODE_WAKEUP")
        shell("wm", "dismiss-keyguard")
        shell("pm", "grant", PACKAGE, "android.permission.CAMERA")
        for name, probe in (("gles", lambda: media("gles")), ("audio", lambda: media("audio")),
                            ("camera", lambda: media("camera")), ("wifi", lambda: radio("wifi")),
                            ("bluetooth", lambda: radio("bluetooth")), ("cellular", cellular)):
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
            report["ok"] = len(report["checks"]) == 6 and report.get("uninstalled", False)
            write_json(args.out / "result.json", report)
    require(report["ok"], str(report["errors"]))
    print(args.out / "result.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with lock(run_directory() / ".cfctl-control.lock"):
        run(args)
