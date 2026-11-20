"""在两个 Cuttlefish 宿主执行同一 APK 回归，并比较实际结果。"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import os
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
APKS = ("v1.apk", "v2.apk", "other.apk", "split.apk")
IMAGE = "e6bdd6ec3a3e20112bccc477a24b397fec3cb78e8aef5390bdddc8f12f77467b"
PACKAGE = "dev.cuttlefish.cfctl.fixture"
CHECKS = {"foreground", "text", "clicked", "retained", "signature_error", "split"}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, data):
    staged = path.with_suffix(".tmp")
    staged.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    staged.replace(path)


def inputs(bundle, image):
    actual = digest(image)
    if actual != IMAGE:
        raise ValueError("Android CI 14781626 ZIP 的 SHA-256 不匹配")
    return {"build": "14781626", "image_sha256": actual,
            "apks": {name: digest(bundle / name) for name in APKS}}


def prepare(args):
    dest = args.bundle.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        work = Path(tmp)
        subprocess.run(["bash", ROOT / "tests/apk/build.sh", work], check=True)
        stage = work / "bundle"
        stage.mkdir()
        for name in APKS:
            (work / name).rename(stage / name)
        write_json(stage / "manifest.json", inputs(stage, args.image))
        if dest.exists():
            raise ValueError("测试包目录已存在")
        stage.rename(dest)


def guest(device, *args):
    timeout = 300 if device == "linux-1" else 120
    if args[:2] in (("device", "start"), ("device", "wait"), ("device", "stop")):
        timeout = 2400 if device == "linux-1" else 240
    cmd = [sys.executable, "-B", ROOT / "cfctl.py", "--json", "--timeout", str(timeout),
           "--device", device, *map(str, args)]
    capture_grace = 130 if device == "linux-1" and args[0] == "ui" else 15
    proc = subprocess.run(cmd, timeout=timeout + capture_grace, capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError(f"{device} {' '.join(map(str, args[:2]))}: {proc.stdout or proc.stderr}")
    return json.loads(proc.stdout)["result"]


def focused(cli):
    return next(line.strip() for line in cli("shell", "dumpsys", "window").splitlines()
                if "mCurrentFocus=" in line)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def application(cli, bundle, output, report):
    checks = report["checks"]
    require("package:" + PACKAGE not in cli("app", "list"), "测试包已安装，保留原应用")
    try:
        cli("app", "install", bundle / "v1.apk")
        cli("input", "key", "KEYCODE_WAKEUP")
        cli("shell", "wm", "dismiss-keyguard")
        cli("app", "launch", PACKAGE)
        deadline = time.monotonic() + 300
        focus = focused(cli)
        while PACKAGE + "/" not in focus:
            require(time.monotonic() < deadline, f"等待测试应用前台窗口超时，最后记录为 {focus}")
            if "NotificationShade" in focus:
                cli("shell", "wm", "dismiss-keyguard")
            time.sleep(1)
            focus = focused(cli)
        checks["foreground"] = PACKAGE
        cli("ui", "set-text", "--desc", "Fixture input", "cf test")
        require(cli("ui", "find", "--text", "cf test"), "输入文本不匹配")
        checks["text"] = "cf test"
        cli("ui", "click", "--text", "START")
        require(cli("ui", "wait", "--text", "VERIFIED"), "点击结果不匹配")
        checks["clicked"] = "VERIFIED"
        png = output / "screen.png"
        cli("screen", "capture", png)
        require(png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), "无效截图")
        cli("app", "install", "--launch", bundle / "v2.apk")
        require(cli("ui", "find", "--text", "VERIFIED"), "升级后应用数据不匹配")
        checks["retained"] = "VERIFIED"
        try:
            cli("app", "install", bundle / "other.apk")
        except RuntimeError as exc:
            if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" not in str(exc):
                raise
        else:
            raise RuntimeError("不同签名的 APK 升级意外成功")
        checks["signature_error"] = "INSTALL_FAILED_UPDATE_INCOMPATIBLE"
        cli("app", "install", "--launch", bundle / "v2.apk", bundle / "split.apk")
        require("split_config.en.apk" in cli("shell", "pm", "path", PACKAGE), "拆分 APK 缺失")
        checks["split"] = "split_config.en.apk"
    except Exception:
        for name, args in (("failure.xml", ("ui", "tree")), ("logcat.txt", ("logs", "--lines", "160")),
                           ("failure.png", ("screen", "capture", output / "failure.png"))):
            try:
                result = cli(*args)
                if name != "failure.png":
                    (output / name).write_text(result)
            except Exception as exc:
                report.setdefault("capture_errors", {})[name] = str(exc)
        raise
    finally:
        if "package:" + PACKAGE in cli("app", "list"):
            cli("app", "uninstall", PACKAGE)
        require("package:" + PACKAGE not in cli("app", "list"), "测试包卸载失败")


def run(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    system = platform.system()
    report = {"ok": False, "host": system, "machine": platform.machine(), "device": args.device,
              "checks": {}, "steps": []}
    def cli(*argv):
        start = time.monotonic()
        step = {"command": list(map(str, argv[:2])), "ok": False}
        report["steps"].append(step)
        try:
            value = guest(args.device, *argv)
            step["ok"] = True
            return value
        except Exception as exc:
            step["error"] = str(exc)
            raise
        finally:
            step["seconds"] = round(time.monotonic() - start, 3)
            write_json(out / "result.json", report)
    try:
        expected = {"Darwin": "darwin-1", "Linux": "linux-1"}.get(system)
        require(args.device == expected, "请选择此宿主的 Cuttlefish 实例")
        require(report["machine"].lower() in ("arm64", "aarch64"), "需要 ARM64 宿主")
        require(not (set(args.observe) & {args.device}), "对照实例与测试目标重合")
        report["inputs"] = inputs(args.bundle, args.image)
        require(report["inputs"] == json.loads((args.bundle / "manifest.json").read_text()), "测试输入摘要不匹配")
        before = {name: (sorted(guest(name, "app", "list")), focused(lambda *a: guest(name, *a)))
                  for name in args.observe}
        cli("device", "wait")
        if system == "Linux":
            qmp = cli("doctor").get("qmp") or {}
            require("query-kvm" in qmp, "缺少 Linux QMP 加速器状态")
            report["kvm"] = qmp["query-kvm"]
        report["guest"] = {prop: cli("shell", "getprop", prop) for prop in
                           ("ro.build.version.incremental", "ro.system.build.fingerprint", "ro.product.cpu.abi")}
        require(report["guest"]["ro.build.version.incremental"] == "14781626" and
                report["guest"]["ro.product.cpu.abi"] == "arm64-v8a", "运行中的来宾构建号或 ABI 不符")
        hostdir = Path(os.environ.get("CF_RUN", ROOT / ".local-cf" / ("run-" + system.lower())))
        report["host_binaries"] = {name: digest(hostdir / "bin" / name) for name in ("launch_cvd", "run_cvd")}
        application(cli, args.bundle, out, report)
        for name, state in before.items():
            require(state == (sorted(guest(name, "app", "list")), focused(lambda *a: guest(name, *a))),
                    f"{name} 的应用列表或前台窗口发生变化")
        report["isolation"] = list(before)
        report["ok"] = True
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        write_json(out / "result.json", report)


def compare(a, b):
    results = [json.loads(path.read_text()) for path in (a, b)]
    require({r["host"] for r in results} == {"Darwin", "Linux"}, "需要 Darwin 和 Linux 的实际报告")
    for r in results:
        require(r["ok"] is True and set(r["checks"]) == CHECKS, "缺少已通过的完整回归结果")
    for key in ("inputs", "guest", "checks"):
        require(results[0][key] == results[1][key], f"两端的 {key} 不一致")
    print("Darwin 与 Linux 的镜像、APK、来宾版本及应用断言一致")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    for cmd in ("prepare", "run"):
        sub = subs.add_parser(cmd)
        sub.add_argument("--image", type=Path, required=True)
        sub.add_argument("--bundle", type=Path, required=True)
        if cmd == "run":
            sub.add_argument("--device", required=True)
            sub.add_argument("--out", type=Path, required=True)
            sub.add_argument("--observe", action="append", default=[])
    sub = subs.add_parser("compare")
    sub.add_argument("darwin", type=Path)
    sub.add_argument("linux", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(args)
        elif args.command == "run":
            run(args)
        else:
            compare(args.darwin, args.linux)
    except (KeyError, ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"{exc}\n")
