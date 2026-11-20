#!/usr/bin/env python3
"""按名称控制本机 Android 直启或 Cuttlefish 实例。"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from cvd_host import CvdHost, NAME as CVD_NAME, run_directory
from instance_store import LOCAL, clone, delete_clone, devices, snapshot
from phone import ROOT, vm_running

LOCAL_ADB = ["adb", "-H", "127.0.0.1", "-P", "5037"]


class UiNotReady(RuntimeError):
    pass


class Device:
    def __init__(self, name, timeout):
        entries = devices()
        self.cvd = None
        if name == CVD_NAME and name not in entries:
            self.bind_cvd(CvdHost(run_directory()))
        elif name not in entries:
            raise ValueError(f"未知实例：{name}")
        else:
            self.directory, self.port = entries[name]
        self.last_tree = None
        self.name = name
        self.serial = f"127.0.0.1:{self.port}"
        self.deadline = time.monotonic() + timeout

    def bind_cvd(self, host):
        self.cvd = host
        self.directory, self.port = host.directory, host.port
        self.serial = f"127.0.0.1:{self.port}"

    def run(self, args, *, check=True, binary=False, limit=None, env=None):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("操作超时")
        p = subprocess.run(args, capture_output=True, env=env, timeout=min(remaining, limit) if limit else remaining)
        if check and p.returncode:
            raise RuntimeError((p.stderr or p.stdout).decode(errors="replace").strip() or
                               f"{args[0]} exited {p.returncode}")
        return p.stdout if binary else (p.stdout or p.stderr).decode(errors="replace").strip()

    def adb(self, *args, **kwargs):
        return self.run([*LOCAL_ADB, "-s", self.serial, *args], **kwargs)

    def shell(self, *args):
        return self.adb("shell", shlex.join(args))

    def status(self):
        extra = {}
        if self.cvd:
            extra = self.cvd.processes(self)
            running = extra.pop("qemu")
            owner = extra.pop("owns_port")
        else:
            running = vm_running(self.directory)
            owner = False
        if running and not self.cvd:
            pid = (self.directory / "qemu.pid").read_text().strip()
            owner = pid in self.run(["/usr/sbin/lsof", "-nP", "-t", "-a", "-p", pid,
                                     f"-iTCP:{self.port}", "-sTCP:LISTEN"], check=False).splitlines()
        boot = False
        # 嵌套 Linux 首次启动时，ADB 已在线但拉起 shell 仍可能需要数秒。
        probe_timeout = 20 if self.cvd and self.name == "linux-1" else 2
        state = "unknown"
        try:
            state = self.adb("get-state", check=False, limit=2)
            if self.cvd and owner and state == "device":
                actual = self.adb("shell", "getprop", "ro.serialno", limit=probe_timeout)
                extra["serial_matches"] = actual == self.cvd.serial
            if running and owner and not extra.get("conflict") and state == "device" and extra.get("serial_matches", True):
                boot = self.adb("shell", "getprop", "sys.boot_completed", limit=probe_timeout) == "1"
        except subprocess.TimeoutExpired:
            if state == "unknown":
                state = "timeout"
        return dict(name=self.name, serial=self.serial, qemu=running, owns_port=owner,
                    adb=state, boot_completed=boot, **extra)

    def ready(self, wait=False):
        while True:
            state = self.status()
            if state["boot_completed"]:
                return state
            if not wait:
                raise RuntimeError(f"{self.name} 未就绪：{state}")
            if state["owns_port"] and state["adb"] != "device":
                try:
                    self.run([*LOCAL_ADB, "connect", self.serial], check=False, limit=2)
                except subprocess.TimeoutExpired:
                    pass
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("等待设备启动超时")
            time.sleep(min(0.5, remaining))

    def launch(self, package):
        component = self.shell("cmd", "package", "resolve-activity", "--brief",
                               "-a", "android.intent.action.MAIN", "-c",
                               "android.intent.category.LAUNCHER", "-p", package).splitlines()
        if not component or not component[-1].startswith(package + "/"):
            raise RuntimeError(f"{package} 缺少启动 Activity")
        result = self.shell("am", "start", "-W", "-n", component[-1])
        if "Status: ok" not in result.splitlines():
            raise RuntimeError(result)
        return result

    def tree(self):
        output = self.adb("exec-out", "uiautomator", "dump", "/dev/tty")
        end = "</hierarchy>"
        if "<hierarchy" not in output or end not in output:
            if "could not get idle state" in output or "null root node" in output:
                raise UiNotReady(output)
            raise RuntimeError("无法获取 UI 层次：" + output[:160])
        root = ET.fromstring(output[output.index("<hierarchy"):output.index(end) + len(end)])
        self.last_tree = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        return root


def package_from_apk(device, apk):
    aapt = shutil.which("aapt2")
    if not aapt:
        sdk = Path(os.environ.get("ANDROID_HOME", Path.home() / "Library/Android/sdk"))
        tools = sorted(sdk.glob("build-tools/*/aapt2"), key=lambda p: tuple(
            int(x) for x in re.findall(r"\d+", p.parent.name)))
        aapt = str(tools[-1]) if tools else shutil.which("aapt")
        if not aapt:
            raise RuntimeError("--launch 需要 aapt2 或 aapt")
    info = device.run([aapt, "dump", "badging", str(apk)])
    match = re.search(r"^package: name='([^']+)'", info, re.M)
    if not match:
        raise RuntimeError("无法读取 APK 包名")
    return match[1]


def matching_nodes(device, key, value):
    return [node.attrib for node in device.tree().iter("node") if node.get(key) == value]


def tap_node(device, node):
    bounds = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node["bounds"])
    if not bounds:
        raise RuntimeError("控件缺少可用坐标")
    x, y, right, bottom = map(int, bounds.groups())
    if right <= x or bottom <= y:
        raise RuntimeError("控件当前没有可点击区域")
    device.shell("input", "tap", str((x + right) // 2), str((y + bottom) // 2))


def replace_text(device, node, key, value, text):
    if node["class"] != "android.widget.EditText" or node.get("password") == "true":
        raise ValueError("set-text 需要普通 EditText")
    if key == "text" or not re.fullmatch(r"[a-zA-Z0-9 ._@+-]*", text):
        raise ValueError("请按 id 或 desc 选择控件，文本支持英文字母、数字、空格和 ._@+-")
    tap_node(device, node)
    selected = matching_nodes(device, key, value)
    while len(selected) != 1 or selected[0]["focused"] != "true":
        if time.monotonic() + 1 >= device.deadline:
            raise TimeoutError("目标输入框未获得焦点")
        time.sleep(0.2)
        selected = matching_nodes(device, key, value)
    device.shell("input", "keycombination", "KEYCODE_CTRL_LEFT", "KEYCODE_A")
    device.shell("input", "keyevent", "KEYCODE_DEL")
    if text:
        device.shell("input", "text", text.replace(" ", "%s"))
    selected = matching_nodes(device, key, value)
    if len(selected) != 1 or selected[0].get("text") != text:
        raise RuntimeError("输入框的实际文本与请求不符")
    return selected


def ui(device, args):
    if args.action == "tree":
        return ET.tostring(device.tree(), encoding="unicode")
    key, value = next((k, v) for k, v in (("resource-id", args.id), ("text", args.text),
                                           ("content-desc", args.desc)) if v is not None)
    while True:
        try:
            matches = matching_nodes(device, key, value)
        except UiNotReady:
            if args.action == "find":
                raise
            matches = []
        if matches:
            break
        if args.action == "find":
            raise RuntimeError("未找到控件")
        time.sleep(0.2)
    if args.action in ("find", "wait"):
        return matches
    if len(matches) != 1:
        raise RuntimeError("选择器匹配多个控件")
    if args.action == "set-text":
        return replace_text(device, matches[0], key, value, args.value)
    tap_node(device, matches[0])
    return matches


def serial_echo(directory, deadline):
    fifo = directory / "console.in"
    if not fifo.is_fifo():
        return False
    token = "CFCTL" + secrets.token_hex(6)
    with (directory / "boot.log").open("rb") as log:
        log.seek(0, os.SEEK_END)
        fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, f"\necho {token}\n".encode())
        finally:
            os.close(fd)
        data = b""
        until = min(deadline, time.monotonic() + 2)
        while time.monotonic() < until:
            data += log.read(65536)
            if token.encode() in data.splitlines():
                return True
            time.sleep(0.05)
    return False


def qmp_status(path, timeout=3, *, include_kvm=False):
    state = {}
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(timeout)
        if len(os.fsencode(path)) >= 104:
            # Darwin 的 sockaddr_un.sun_path 只有 104 字节。
            with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
                short = Path(tmp) / "qmp"
                short.symlink_to(path.resolve())
                conn.connect(str(short))
        else:
            conn.connect(str(path))
        with conn.makefile("rb") as stream:
            def receive():
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError("QMP 响应超时")
                conn.settimeout(left)
                return json.loads(stream.readline())

            greeting = receive()
            while "event" in greeting:
                greeting = receive()
            if "QMP" not in greeting:
                raise RuntimeError(f"无效 QMP 握手：{greeting}")
            commands = ["qmp_capabilities", "query-status", "query-block"]
            if include_kvm:
                commands.append("query-kvm")
            for cmd in commands:
                conn.sendall(json.dumps({"execute": cmd, "id": cmd}).encode() + b"\n")
                while True:
                    msg = receive()
                    if msg.get("id") == cmd:
                        if "error" in msg:
                            raise RuntimeError(str(msg["error"]))
                        if cmd != "qmp_capabilities":
                            state[cmd] = msg["return"]
                        break
    return state


def diagnose(device):
    state = device.status()
    directory = device.directory
    state["free_bytes"] = shutil.disk_usage(directory).free if directory.exists() else None
    path = device.cvd.monitor if device.cvd else directory / ".control/qmp.sock"
    if state["qemu"] and not state.get("conflict") and path and path.is_socket():
        try:
            remaining = device.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("QMP 诊断超时")
            state["qmp"] = qmp_status(path, min(remaining, 3), include_kvm=device.name == "linux-1")
        except (OSError, ValueError, RuntimeError) as exc:
            state["qmp_error"] = str(exc)
    else:
        state["qmp"] = None
    if state["qemu"] and not device.cvd:
        try:
            state["serial_echo"] = serial_echo(directory, device.deadline)
        except OSError as exc:
            state["serial_error"] = str(exc)
    return state


def screenshot(device):
    data = device.adb("exec-out", "screencap", "-p", binary=True)
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("截图返回了无效 PNG")
    return data


class UiTimeout(TimeoutError):
    def __init__(self, device, action, root):
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix=device.name + "-", dir=root))
        self.artifacts = {}
        self.capture_errors = {}
        capture_timeout = 60 if device.name == "linux-1" and os.environ.get("CF_LINUX_ACCEL") == "tcg" else 5
        for name in ("tree.xml", "screen.png"):
            try:
                capture = Device(device.name, capture_timeout)
                if name == "screen.png":
                    capture.ready()
                    data = screenshot(capture)
                elif device.last_tree is not None:
                    data = device.last_tree
                else:
                    capture.ready()
                    capture.tree()
                    data = capture.last_tree
                (folder / name).write_bytes(data)
                self.artifacts[name] = str(folder / name)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, ET.ParseError) as exc:
                self.capture_errors[name] = str(exc)
        super().__init__(f"{device.name} 的 ui {action} 超时，诊断目录为 {folder}")


def execute(args, *, artifacts=None):
    if args.timeout is None:
        args.timeout = 240 if args.group == "snapshot" or (
            args.group == "device" and args.action in ("clone", "start", "stop")) else 30
        if args.device == "linux-1":
            if args.group == "device" and args.action in ("start", "wait"):
                args.timeout = 2400
            elif os.environ.get("CF_LINUX_ACCEL") == "tcg":
                args.timeout = 300
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("--timeout 必须为有限正数")
    if args.group == "api":
        from cf_api import serve
        return serve(args.port, args.ws_port)
    if args.group == "device" and args.action == "list":
        return [Device(name, args.timeout).status() for name in dict.fromkeys((*devices(), CVD_NAME))]
    if args.device is None:
        raise ValueError("请用 --device 指定实例")
    d = Device(args.device, args.timeout)
    action = args.action
    if d.cvd:
        if args.group == "snapshot" or (args.group == "device" and action in ("clone", "delete")):
            raise ValueError("此操作仅适用于直启实例的磁盘布局")
        if args.group == "device" and action in ("start", "stop"):
            return d.cvd.lifecycle(d, action)
    if args.group == "device":
        if action == "clone":
            return clone(d, args.name, args.port, args.clean)
        if action == "delete":
            return delete_clone(d)
        if action in ("start", "stop"):
            if action == "stop":
                d.ready()
            elif vm_running(d.directory):
                state = d.status()
                if state["boot_completed"]:
                    return state
                if not state["owns_port"]:
                    raise RuntimeError("QEMU 进程未持有所选端口")
            env = dict(os.environ, CF_PHONE=str(d.directory), CF_ADB_PORT=str(d.port))
            for key in ("ADB_SERVER_SOCKET", "ANDROID_ADB_SERVER_ADDRESS", "ANDROID_ADB_SERVER_PORT",
                        "CF_API_TOKEN"):
                env.pop(key, None)
            result = d.run([sys.executable, "-B", ROOT / "phone.py", action], env=env)
            return d.ready(wait=True) if action == "start" else result
        return d.status() if action == "status" else d.ready(wait=True)
    if args.group == "snapshot":
        return snapshot(d, action, getattr(args, "name", None))
    if args.group == "doctor":
        return diagnose(d)
    if args.group == "ui":
        try:
            d.ready()
            return ui(d, args)
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            directory = d.cvd.run if d.cvd else LOCAL
            raise UiTimeout(d, action, artifacts or directory / "artifacts") from exc
    d.ready()
    if args.group == "app":
        if action == "install":
            files = [p.resolve(strict=True) for p in args.apks]
            package = package_from_apk(d, files[0]) if args.launch else None
            command = "install" if len(files) == 1 else "install-multiple"
            result = d.adb(command, "-r", *map(str, files))
            if "Success" not in result.splitlines():
                raise RuntimeError(result)
            return d.launch(package) if package else result
        if action == "launch":
            return d.launch(args.package)
        if action == "uninstall":
            return d.adb("uninstall", args.package)
        return d.shell("pm", "list", "packages").splitlines()
    if args.group == "screen":
        data = screenshot(d)
        with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False) as tmp:
            try:
                tmp.write(data)
                tmp.close()
                os.replace(tmp.name, args.output)
            finally:
                Path(tmp.name).unlink(missing_ok=True)
        return str(args.output)
    if args.group == "input":
        if action == "text":
            return d.shell("input", "text", args.values[0].replace(" ", "%s"))
        return d.shell("input", "keyevent" if action == "key" else action, *args.values)
    if args.group == "logs":
        if args.lines < 1:
            raise ValueError("--lines 必须为正整数")
        return d.adb("logcat", "-d", "-t", str(args.lines))
    if not args.command:
        raise ValueError("shell 需要命令及其参数")
    return d.shell(*args.command)


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--device", metavar="NAME")
    cli.add_argument("--timeout", type=float)
    cli.add_argument("--json", action="store_true")
    cli.set_defaults(action=None)
    groups = cli.add_subparsers(dest="group", required=True)

    def actions(name, commands):
        subs = groups.add_parser(name).add_subparsers(dest="action", required=True)
        return {cmd: subs.add_parser(cmd) for cmd in commands.split()}

    device = actions("device", "list status wait start stop clone delete")
    device["clone"].add_argument("name")
    device["clone"].add_argument("--port", type=int)
    device["clone"].add_argument("--clean", action="store_true")
    snaps = actions("snapshot", "save list restore delete")
    for cmd in ("save", "restore", "delete"):
        snaps[cmd].add_argument("name")
    server = actions("api", "serve")["serve"]
    server.add_argument("--port", type=int, default=8765)
    server.add_argument("--ws-port", type=int)
    app = actions("app", "install list launch uninstall")
    app["install"].add_argument("apks", nargs="+", type=Path)
    app["install"].add_argument("--launch", action="store_true")
    for cmd in ("launch", "uninstall"):
        app[cmd].add_argument("package")
    actions("screen", "capture")["capture"].add_argument("output", type=Path)
    inputs = actions("input", "tap swipe key text")
    for cmd, n in (("tap", 2), ("swipe", 4), ("key", 1), ("text", 1)):
        inputs[cmd].add_argument("values", nargs=n)
    selectors = actions("ui", "tree find wait click set-text")
    selectors["set-text"].add_argument("value")
    for cmd in ("find", "wait", "click", "set-text"):
        selection = selectors[cmd].add_mutually_exclusive_group(required=True)
        for flag in ("--id", "--text", "--desc"):
            selection.add_argument(flag)
    groups.add_parser("logs").add_argument("--lines", type=int, default=100)
    groups.add_parser("shell").add_argument("command", nargs=argparse.REMAINDER)
    groups.add_parser("doctor")
    return cli


def failure(exc):
    result = {"ok": False, "error": str(exc)}
    if isinstance(exc, UiTimeout):
        result.update(artifacts=exc.artifacts, capture_errors=exc.capture_errors)
    return result


if __name__ == "__main__":
    args = parser().parse_args()
    try:
        result = execute(args)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, ET.ParseError) as exc:
        if args.json:
            print(json.dumps(failure(exc), ensure_ascii=False))
        else:
            print(exc, file=sys.stderr)
        sys.exit(1)
    if args.json:
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
    elif isinstance(result, str):
        print(result)
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
