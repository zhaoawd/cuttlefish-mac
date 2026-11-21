"""比较 Darwin 与 Linux 两份硬件任务的 JSON 报告。"""
import argparse
import ipaddress
import json
from pathlib import Path

from parity import require
from real_app import compare as compare_app

CHECKS = {"gles", "audio", "camera", "wifi", "bluetooth", "cellular"}


def validate(platform, checks):
    require(platform["ok"] and platform["uninstalled"] and not platform["errors"]
            and set(platform["checks"]) == checks, "设备能力回归未完整通过")
    if "wifi_data" in checks:
        data = platform["checks"]["wifi_data"]
        require(data["ssid"] == "VirtWifi" and data["interface"] == "wlan0" and data["restored"]
                and data["gateway"] == "10.0.2.2"
                and ipaddress.ip_address(data["guest_ipv4"]) in ipaddress.ip_network("10.0.2.0/24")
                and data["request_bytes"] == data["reply_bytes"] == 64, "Wi-Fi 收发或恢复断言不完整")
    if "cellular_data" in checks:
        cell = platform["checks"]["cellular_data"]
        guest, router = (("10.0.2.15", "10.0.2.2") if platform["device"] == "darwin-1" else
                         ("192.168.97.2", "192.168.97.1"))
        require(cell["guest_ipv4"] == guest and cell["gateway"] == router
                and cell["interface"] == "buried_eth0" and cell["request_bytes"] == cell["reply_bytes"] == 64,
                "蜂窝 TCP 端点或收发长度不符")
        call = platform["checks"]["incoming_call"]
        require(call["peer_confirmed_answer"] and call["states"] == ["idle", "ringing", "offhook", "idle"],
                "缺少模拟通话状态或对端确认")
        wifi = platform["checks"]["wifi_data"]
        require(wifi["bytes_per_direction"] == 1024 * 1024 and min(wifi["download_ns"], wifi["upload_ns"]) > 0,
                "缺少完整的 Wi-Fi 数据与计时")
    if "ble_data" in checks:
        ble = platform["checks"]["ble_data"]
        require(ble["encrypted"] and ble["bond_removed"] and ble["echo_bytes"] == 16, "蓝牙配对或回显不完整")


def matching_reports(platforms):
    require({p["device"] for p in platforms} == {"darwin-1", "linux-1"}, "需要两种宿主的报告")
    require(platforms[0]["checks"].keys() == platforms[1]["checks"].keys(), "两端的设备检查项目不同")
    for key in ("fixture_sha256", "fingerprint"):
        require(platforms[0][key] == platforms[1][key], f"两端的 {key} 不一致")


def compare_radios(paths):
    reports = [json.loads(path.read_text()) for path in paths]
    for platform in reports:
        validate(platform, CHECKS | {"wifi_data", "cellular_data", "incoming_call", "ble_data"})
        require(platform["checks"]["wifi_data"].get("https") == "wlan0 HTTPS 204", "缺少公网 HTTPS 检查")
    matching_reports(reports)
    print("两端十项设备检查、Wi-Fi 公网 HTTPS 及测试包卸载均通过")


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
        if job.get("cellular_data"):
            checks |= {"cellular_data", "incoming_call"}
        validate(platform, checks)
        require(platform["fingerprint"] == app["fingerprint"], "同一任务的来宾指纹不一致")
        platforms.append(platform)
    matching_reports(platforms)
    print(f"两端的 {len(platforms[0]['checks'])} 项设备能力断言、测试包卸载和正常关机均通过")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folders", type=Path, nargs=2)
    parser.add_argument("--platform-only", action="store_true", help="比较两个含蓝牙、蜂窝和公网检查的设备报告")
    args = parser.parse_args()
    (compare_radios if args.platform_only else compare)(args.folders)
