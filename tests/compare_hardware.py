"""比较 Darwin 与 Linux 两份硬件任务的 JSON 报告。"""
import argparse
import ipaddress
import json
from pathlib import Path

from parity import require
from real_app import compare as compare_app

CHECKS = {"gles", "audio", "camera", "wifi", "bluetooth", "cellular"}


def compare(folders):
    compare_app([folder / "app/result.json" for folder in folders])
    platforms = []
    for folder in folders:
        def read(relative):
            return json.loads((folder / relative).read_text())
        job = read("result.json")
        platform = read("platform/result.json")
        app = read("app/result.json")
        expected = {"Darwin": "darwin-1", "Linux": "linux-1"}[app["host"]]
        require(job["device"] == platform["device"] == expected, "报告设备不一致")
        if app["host"] == "Darwin":
            require(set(app["observed"]) == {"original", "recovered"}, "缺少 Mac 直启实例隔离验证")
        require(job["ok"] and job["stop"]["guest_shutdown"] and job["stop"]["port_closed"], "缺少正常关机记录")
        checks = CHECKS | {"wifi_data"} if job.get("wifi_data") else CHECKS
        require(platform["ok"] and platform["uninstalled"] and not platform["errors"]
                and set(platform["checks"]) == checks, "设备能力回归未完整通过")
        if job.get("wifi_data"):
            data = platform["checks"]["wifi_data"]
            require(data["ssid"] == "VirtWifi" and data["interface"] == "wlan0" and data["restored"]
                    and data["gateway"] == "10.0.2.2"
                    and ipaddress.ip_address(data["guest_ipv4"]) in ipaddress.ip_network("10.0.2.0/24")
                    and data["request_bytes"] == data["reply_bytes"] == 64, "Wi-Fi 收发或恢复断言不完整")
        require(platform["fingerprint"] == app["fingerprint"], "同一任务的来宾指纹不一致")
        platforms.append(platform)
    require(platforms[0]["checks"].keys() == platforms[1]["checks"].keys(), "两端的设备检查项目不同")
    for key in ("fixture_sha256", "fingerprint"):
        require(platforms[0][key] == platforms[1][key], f"两端的 {key} 不一致")
    print(f"两端的 {len(platforms[0]['checks'])} 项设备能力断言、测试包卸载和正常关机均通过")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folders", type=Path, nargs=2)
    compare(parser.parse_args().folders)
