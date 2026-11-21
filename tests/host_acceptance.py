"""在已准备且已停机的专用宿主上执行应用和设备能力验收。"""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from parity import ROOT, guest, require, write_json

sys.path.insert(0, str(ROOT))
from cvd_host import NAME, run_directory
from instance_store import lock


def run(args):
    for path in (args.fixture, args.apk, args.helper):
        require(path.is_file(), f"缺少 {path}")
    before = guest(NAME, "device", "status")
    require(not any(before[key] for key in ("qemu", "host_running", "owns_port", "conflict")),
            f"{NAME} 需要已停止且无端口冲突")
    args.out.mkdir(mode=0o700, parents=True, exist_ok=False)
    result = {"device": NAME, "ok": False, "wifi_data": True, "cellular_data": True}
    try:
        result["start"] = guest(NAME, "device", "start")
        common = ["--device", NAME]
        def test(name, *argv):
            subprocess.run([sys.executable, "-B", ROOT / "tests" / name, *argv], check=True)
        observers = ["--observe", "original", "--observe", "recovered"] if NAME == "darwin-1" else []
        test("real_app.py", "run", *common, "--apk", args.apk, "--helper", args.helper,
             "--out", args.out / "app", *observers)
        test("platform_probe.py", "--wifi-data", "--cellular", *common, "--apk", args.fixture, "--out", args.out / "platform")
        result["checks_passed"] = True
    finally:
        try:
            state = guest(NAME, "device", "status")
            if state["host_running"] and not state["conflict"]:
                result["stop"] = guest(NAME, "device", "stop")
            result["ok"] = result.get("checks_passed", False) and result.get("stop", {}).get("guest_shutdown", False)
        finally:
            write_json(args.out / "result.json", result)
    require(result["ok"], "未完成应用、设备能力回归与正常关机")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("fixture", "apk", "helper", "out"):
        parser.add_argument("--" + flag, type=Path, required=True)
    args = parser.parse_args()
    if NAME == "linux-1":
        os.environ["CF_KVM_VIRT_WIFI"] = "1"
    # 独立于单条生命周期命令的锁，避免两个验收任务操作同一个测试来宾。
    with lock(run_directory() / ".cfctl-acceptance.lock"):
        run(args)
