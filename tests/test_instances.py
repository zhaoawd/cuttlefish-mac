from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cfctl import ROOT, Device, live_snapshot
from instance_store import clone, lock, snapshot, valid_name
from phone import persistent_disk


class StoreTests(unittest.TestCase):
    def test_legacy_persistent_disk_stays_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            device = Device("original", 5)
            device.directory = directory
            raw = directory / "persistent.raw"
            raw.touch()
            self.assertEqual(persistent_disk(directory), (raw, "raw"))
            with self.assertRaisesRegex(ValueError, "clone --clean"):
                live_snapshot(device, "live-save", "test")
            (directory / "persistent.qcow2").touch()
            with self.assertRaisesRegex(ValueError, "两个"):
                persistent_disk(directory)

    def test_partial_copy_not_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = Device("original", 30)
            device.directory = Path(tmp) / "source"
            device.directory.mkdir()
            store = Path(tmp) / "instances"
            with patch.dict(os.environ, CF_INSTANCE_HOME=str(store)), \
                    patch("instance_store.copy_disks", side_effect=OSError("copy failed")):
                with self.assertRaises(OSError):
                    clone(device, "partial", None, False)
                self.assertFalse((store / "partial").exists())
                with self.assertRaises(OSError):
                    snapshot(device, "save", "partial")
                self.assertEqual([], snapshot(device, "list"))
                (device.directory / ".snapshots" / "baseline").mkdir()
                with self.assertRaises(OSError):
                    snapshot(device, "restore", "baseline")
                self.assertFalse((device.directory / ".active-disks").is_symlink())

    def test_invalid_names_and_contention(self):
        for name in ("../worker", ".stage", "/tmp", "", "a/b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                valid_name(name)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "control.lock"
            with lock(path), self.assertRaises(RuntimeError), lock(path):
                self.fail("锁应当互斥")


@unittest.skipUnless(os.environ.get("CF_TEST_BATCH") == "1", "需要本机 HVF、SDK、ADB 和直启基础镜像")
class BatchTests(unittest.TestCase):
    def cli(self, device, *args, success=True):
        cmd = [sys.executable, "-B", ROOT / "cfctl.py", "--json", "--timeout", "240"]
        if device:
            cmd += ["--device", device]
        proc = subprocess.run(cmd + list(args), env=self.env, capture_output=True, text=True, timeout=260)
        response = json.loads(proc.stdout)
        self.assertEqual(response["ok"], success, response)
        self.assertEqual(proc.returncode == 0, success)
        return response.get("result", response)

    def http(self, method, route, body=None, status=200, authorized=True):
        headers = {}
        if authorized:
            headers["Authorization"] = "Bearer " + self.env["CF_API_TOKEN"]
        if isinstance(body, dict):
            headers["Content-Type"] = "application/json"
            body = json.dumps(body).encode()
        req = Request(self.url + route, data=body, headers=headers, method=method)
        try:
            response = urlopen(req, timeout=260)
        except HTTPError as exc:
            response = exc
        with response:
            content = response.read()
            self.assertEqual(response.status, status, content[:1024])
            if response.headers.get_content_type() == "application/json":
                return json.loads(content)
            return content

    def rpc(self, name, *args, status=200, timeout=60):
        return self.http("POST", "/v1/command", {"device": name, "args": args, "timeout": timeout},
                         status=status)

    def ensure_stopped(self, name):
        path = Path(self.env["CF_INSTANCE_HOME"]) / name
        if (path / "qemu.pid").exists():
            try:
                self.cli(name, "device", "stop")
            except Exception:
                fd = os.open(path / "console.in", os.O_WRONLY | os.O_NONBLOCK)
                with os.fdopen(fd, "wb", buffering=0) as fifo:
                    fifo.write(b"\nsu 0 reboot -p\n")
                for _ in range(60):
                    if not (path / "qemu.pid").exists():
                        break
                    time.sleep(1)
                self.assertFalse((path / "qemu.pid").exists())

    def test_running_snapshot_restores_ram_and_disks(self):
        with ExitStack() as cleanup:
            work = Path(tempfile.mkdtemp(prefix="cf-live-", dir="/private/tmp"))
            def remove_work():
                self.assertFalse((work / "vm/ram-test/qemu.pid").exists(), f"保留运行目录 {work}")
                shutil.rmtree(work)
            cleanup.callback(remove_work)
            self.env = dict(os.environ, CF_INSTANCE_HOME=str(Path(work) / "vm"), CF_PHONE_DISPLAY="none")
            name = "ram-test"
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = str(sock.getsockname()[1])
            self.cli("recovered", "device", "clone", name, "--clean", "--port", port)
            try:
                self.cli(name, "device", "start")
                def markers():
                    return self.cli(name, "shell", "su", "0", "cat", "/proc/sys/kernel/random/boot_id",
                                    "/dev/cf_ram", "/data/local/tmp/cf_disk").splitlines()

                def write(value):
                    self.cli(name, "shell", "su", "0", "sh", "-c",
                             f"echo {value} > /dev/cf_ram && echo {value} > /data/local/tmp/cf_disk && sync")

                original, changed = secrets.token_hex(16), secrets.token_hex(16)
                write(original)
                before = markers()
                self.assertEqual(before[1:], [original, original])
                self.cli(name, "snapshot", "live-save", "baseline")
                self.assertEqual(self.cli(name, "snapshot", "live-list"), ["baseline"])
                self.assertIn("已存在", self.cli(name, "snapshot", "live-save", "baseline", success=False)["error"])
                write(changed)
                self.assertEqual(markers(), [before[0], changed, changed])
                self.cli(name, "snapshot", "live-restore", "baseline")
                self.assertEqual(markers(), before)
                self.cli(name, "snapshot", "live-delete", "baseline")
                self.assertEqual(self.cli(name, "snapshot", "live-list"), [])
            finally:
                self.ensure_stopped(name)

    def test_parallel_devices_api_and_restore(self):
        with ExitStack() as cleanup:
            work = Path(tempfile.mkdtemp(prefix="cf-batch-", dir="/private/tmp"))
            def remove_work():
                pids = list(work.glob("vm/*/qemu.pid"))
                self.assertFalse(pids, f"保留仍有 QEMU 进程记录的测试目录 {work}")
                shutil.rmtree(work)
            cleanup.callback(remove_work)
            self.env = dict(os.environ, CF_INSTANCE_HOME=str(work / "vm"), CF_API_FILES=str(work / "files"),
                            CF_API_TOKEN=secrets.token_hex(24))
            ws_args = ["--ws-port", "0"] if os.environ.get("CF_TEST_WS") == "1" else []
            api = subprocess.Popen([sys.executable, "-B", ROOT / "cfctl.py", "api", "serve", "--port", "0", *ws_args],
                                   env=self.env, text=True, stdout=subprocess.PIPE)
            def stop_api():
                api.terminate()
                api.wait(timeout=5)
                api.stdout.close()
            cleanup.callback(stop_api)
            self.assertTrue(select.select([api.stdout], [], [], 10)[0])
            self.url = api.stdout.readline().strip()
            self.assertTrue(self.url.startswith("http://127.0.0.1:"), self.url)
            if ws_args:
                from websockets.exceptions import InvalidStatus
                from websockets.sync.client import connect
                ws_url = api.stdout.readline().strip()
                with self.assertRaises(InvalidStatus) as err:
                    connect(ws_url, proxy=None)
                self.assertEqual(err.exception.response.status_code, 401)
                headers = {"Authorization": "Bearer " + self.env["CF_API_TOKEN"]}
                with connect(ws_url, additional_headers=headers, proxy=None) as ws:
                    ws.send(json.dumps({"args": ["device", "list"]}))
                    self.assertEqual(json.loads(ws.recv())["result"], self.cli(None, "device", "list"))
                    ws.send("invalid")
                    self.assertFalse(json.loads(ws.recv())["ok"])
            self.http("POST", "/v1/command", {"args": ["device", "list"]}, status=401, authorized=False)
            self.http("PUT", "/v1/files/../bad.apk", b"x", status=400)
            self.assertFalse(self.rpc("worker-a", "device", "status", status=422)["ok"])
            names = ("worker-a", "worker-b")
            with ThreadPoolExecutor(2) as pool:
                clones = list(pool.map(lambda n: self.cli("recovered", "device", "clone", n, "--clean"), names))
            self.assertNotEqual(clones[0]["serial"], clones[1]["serial"])
            self.assertIn("先正常关闭", self.cli("recovered", "snapshot", "save", "busy", success=False)["error"])
            for name in names:
                cleanup.callback(self.ensure_stopped, name)
            with ThreadPoolExecutor(2) as pool:
                states = list(pool.map(lambda n: self.cli(n, "device", "start"), names))
            self.assertNotEqual(states[0]["serial"], states[1]["serial"])
            self.assertTrue(all(s["boot_completed"] for s in states))
            self.assertEqual(self.cli("worker-a", "device", "status"), self.rpc("worker-a", "device", "status")["result"])
            subprocess.run(["bash", ROOT / "tests/apk/build.sh", work], check=True, timeout=120)
            self.http("PUT", "/v1/files/test.apk", (work / "v1.apk").read_bytes(), status=201)

            def flow(name):
                self.rpc(name, "input", "key", "KEYCODE_WAKEUP")
                self.rpc(name, "shell", "wm", "dismiss-keyguard")
                self.rpc(name, "app", "install", "--launch", "test.apk")
                self.rpc(name, "ui", "set-text", "--desc", "Fixture input", name)
                self.assertTrue(self.rpc(name, "ui", "find", "--text", name)["result"])
                self.rpc(name, "ui", "click", "--text", "START")
                self.assertTrue(self.rpc(name, "ui", "wait", "--text", "VERIFIED")["result"])
                focus = self.rpc(name, "shell", "dumpsys", "window")["result"]
                self.assertTrue(any("dev.cuttlefish.cfctl.fixture/" in line for line in focus.splitlines()
                                    if "mCurrentFocus=" in line))
                self.rpc(name, "screen", "capture", name + ".png")
                self.assertTrue(self.http("GET", "/v1/files/" + name + ".png").startswith(b"\x89PNG"))
                self.rpc(name, "shell", "touch", "/data/local/tmp/" + name)
            with ThreadPoolExecutor(2) as pool:
                list(pool.map(flow, names))
            self.assertNotIn("worker-a", self.cli("worker-b", "shell", "ls", "/data/local/tmp"))
            timed = self.rpc("worker-a", "ui", "wait", "--text", "absent-example", timeout=4, status=422)
            self.assertIn(b"<hierarchy", self.http("GET", timed["artifacts"]["tree.xml"]))
            self.assertTrue(self.http("GET", timed["artifacts"]["screen.png"]).startswith(b"\x89PNG"))
            subprocess.run(["adb", "-H", "127.0.0.1", "-P", "5037", "disconnect", states[0]["serial"]],
                           check=True, stdout=subprocess.DEVNULL)
            diag = self.cli("worker-a", "doctor")
            self.assertFalse(diag["boot_completed"])
            self.assertNotEqual(diag["adb"], "device")
            self.assertTrue(diag["serial_echo"])
            self.assertTrue(diag["qmp"]["query-status"]["running"])
            self.cli("worker-a", "device", "wait")
            self.cli("worker-a", "device", "stop")
            self.rpc("worker-a", "snapshot", "save", "baseline")
            self.rpc("worker-a", "device", "clone", "saved-copy")
            cleanup.callback(self.ensure_stopped, "saved-copy")
            self.assertEqual(["baseline"], self.cli("worker-a", "snapshot", "list"))
            self.assertIn("已存在", self.cli("worker-a", "snapshot", "save", "baseline", success=False)["error"])
            self.cli("worker-a", "device", "start")
            self.cli("worker-a", "shell", "touch", "/data/local/tmp/later")
            self.assertIn("later", self.cli("worker-a", "shell", "ls", "/data/local/tmp"))
            self.cli("worker-a", "device", "stop")
            self.rpc("worker-a", "snapshot", "restore", "baseline")
            self.cli("worker-a", "device", "start")
            files = self.cli("worker-a", "shell", "ls", "/data/local/tmp").splitlines()
            self.assertIn("worker-a", files)
            self.assertNotIn("later", files)
            self.assertIn("worker-b", self.cli("worker-b", "shell", "ls", "/data/local/tmp"))
            self.cli("worker-a", "snapshot", "delete", "baseline")
            self.assertEqual([], self.cli("worker-a", "snapshot", "list"))
            self.assertIn("只能删除", self.cli("original", "device", "delete", success=False)["error"])
            for name in names:
                self.cli(name, "device", "stop")
                self.cli(name, "device", "delete")
                self.assertFalse((work / "vm" / name).exists())
            self.cli("saved-copy", "device", "start")
            files = self.cli("saved-copy", "shell", "ls", "/data/local/tmp").splitlines()
            self.assertIn("worker-a", files)
            self.assertNotIn("later", files)
            self.assertIn("package:dev.cuttlefish.cfctl.fixture", self.cli("saved-copy", "app", "list"))
