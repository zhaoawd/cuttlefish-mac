"""在独立 Cuttlefish 实例验证 v2rayNG 的本地配置搜索。"""
import argparse
import json
from pathlib import Path
import platform
import sys
import xml.etree.ElementTree as ET

from parity import ROOT, digest, focused, guest, require, write_json

sys.path.insert(0, str(ROOT))
from cfctl import Device, package_from_apk
from cvd_host import run_directory
from instance_store import lock

APP = "com.v2ray.ang"
HELPER = "dev.cuttlefish.cfctl.text"
APK_SHA256 = "c559ce80045041ab9bd3fd2732549ca05871486f488194cde8f0f9efb8858770"
QUERY = "北京 A座 302🙂"


def observe(device):
    def cli(*args):
        return guest(device, *args)
    return sorted(cli("app", "list")), focused(cli)


def scenario(cli, out):
    cli("input", "key", "KEYCODE_WAKEUP")
    cli("shell", "wm", "dismiss-keyguard")
    cli("app", "launch", APP)
    deny = "com.android.permissioncontroller:id/permission_deny_button"
    if any(n.get("resource-id") == deny for n in ET.fromstring(cli("ui", "tree")).iter("node")):
        cli("ui", "click", "--id", deny)
    require(APP + "/" in focused(cli), "应用未处于前台")
    cli("ui", "wait", "--desc", "Search")
    try:
        cli("ui", "set-text", "--desc", "Search", QUERY)
    except RuntimeError as exc:
        if "set-text 需要普通 EditText" not in str(exc):
            raise
    else:
        raise RuntimeError("非输入控件意外接受文本")
    cli("ui", "click", "--desc", "Search")
    for value in (QUERY, "上海 42", ""):
        result = cli("ui", "set-text", "--focused", value)
        require(len(result) == 1 and result[0]["text"] == value and result[0]["package"] == APP,
                "搜索框内容或所属应用不符")
        if value == QUERY:
            cli("screen", "capture", out / "search.png")
    cli("ui", "click", "--desc", "Back")
    search = cli("ui", "wait", "--desc", "Search")
    require(len(search) == 1 and search[0]["package"] == APP, "未返回应用的配置首页")
    cli("ui", "wait", "--text", "Not connected")
    return {"query": QUERY, "replacement": "上海 42", "cleared": True, "non_editable_rejected": True,
            "home": "Not connected"}


def run(args):
    system = platform.system()
    require(args.device == {"Linux": "linux-1", "Darwin": "darwin-1"}.get(system), "请选择独立宿主测试实例")
    require(args.device not in args.observe, "对照实例与测试目标重合")
    require(digest(args.apk) == APK_SHA256, "APK 与已验收的 v2rayNG 2.3.14 不符")
    require(package_from_apk(Device(args.device, 30), args.helper) == HELPER, "输入组件的包名不符")
    out = args.out.resolve()
    report = {"ok": False, "device": args.device, "host": system, "apk_sha256": APK_SHA256,
              "helper_sha256": digest(args.helper), "observed": [], "uninstalled": []}
    def cli(*argv):
        return guest(args.device, *argv)

    packages = set(cli("app", "list"))
    require(all("package:" + p not in packages for p in (APP, HELPER)), "请使用未安装应用及输入组件的测试实例")
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    attempted = []
    try:
        report["fingerprint"] = cli("shell", "getprop", "ro.build.fingerprint")
        if system == "Linux":
            report["kvm"] = cli("doctor")["qmp"]["query-kvm"]
            require(report["kvm"]["enabled"], "Linux 测试实例未启用 KVM")
        before = {name: observe(name) for name in args.observe}
        for package, apk in ((HELPER, args.helper), (APP, args.apk)):
            attempted.append(package)
            cli("app", "install", apk)
        report["checks"] = scenario(cli, out)
        for name, state in before.items():
            require(state == observe(name), f"{name} 的应用列表或前台窗口发生变化")
            report["observed"].append(name)
    except Exception as exc:
        report["error"] = str(exc)
        for name, argv in (("failure.xml", ("ui", "tree")), ("logcat.txt", ("logs", "--lines", "160")),
                           ("failure.png", ("screen", "capture", out / "failure.png"))):
            try:
                result = cli(*argv)
                if name != "failure.png":
                    (out / name).write_text(result)
            except Exception as capture_error:
                report.setdefault("capture_errors", {})[name] = str(capture_error)
        raise
    finally:
        for package in reversed(attempted):
            try:
                if "package:" + package in cli("app", "list"):
                    cli("app", "uninstall", package)
                    require("package:" + package not in cli("app", "list"), f"{package} 卸载失败")
                    report["uninstalled"].append(package)
            except Exception as exc:
                report.setdefault("cleanup_errors", {})[package] = str(exc)
        report["ok"] = "error" not in report and not report.get("cleanup_errors")
        write_json(out / "result.json", report)
    require(report["ok"], json.dumps(report.get("cleanup_errors"), ensure_ascii=False))
    print(out / "result.json")


def compare(paths):
    results = [json.loads(path.read_text()) for path in paths]
    require({r["host"] for r in results} == {"Darwin", "Linux"}, "需要 Darwin 和 Linux 实测报告")
    for r in results:
        require(r["ok"] and r["uninstalled"] == [APP, HELPER], "回归或卸载未通过")
        if r["host"] == "Linux":
            require(r["kvm"]["enabled"], "Linux 报告缺少 KVM 验证")
    for key in ("apk_sha256", "helper_sha256", "fingerprint", "checks"):
        require(results[0][key] == results[1][key], f"两端的 {key} 不一致")
    print("两端的 APK、输入组件、Android 构建指纹及搜索断言一致")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    regression = commands.add_parser("run")
    regression.add_argument("--device", required=True)
    regression.add_argument("--apk", type=Path, required=True)
    regression.add_argument("--helper", type=Path, required=True)
    regression.add_argument("--out", type=Path, required=True)
    regression.add_argument("--observe", action="append", default=[])
    commands.add_parser("compare").add_argument("reports", nargs=2, type=Path)
    args = parser.parse_args()
    if args.command == "run":
        with lock(run_directory() / ".cfctl-control.lock"):
            run(args)
    else:
        compare(args.reports)
