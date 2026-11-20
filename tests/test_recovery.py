import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]


def local_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(os.environ.get("GUEST_ZIP"), "需要 Android 16 ARM64 设备 ZIP")
class RecoveryTest(unittest.TestCase):
    def test_recovery_over_adb(self):
        archive = Path(os.environ["GUEST_ZIP"]).resolve()
        adb = shutil.which("adb")
        self.assertIsNotNone(adb)
        server_port, guest_port = local_port(), local_port()
        while guest_port == server_port:
            guest_port = local_port()
        device = f"127.0.0.1:{guest_port}"
        cmd = [sys.executable, str(ROOT / "recovery.py"), str(archive), "--adb-port", str(guest_port)]
        if os.environ.get("UNPACK_BOOTIMG"):
            cmd.extend(["--unpack-bootimg", os.environ["UNPACK_BOOTIMG"]])
        with tempfile.TemporaryDirectory(dir=archive.parent) as tmp:
            env = dict(os.environ, HOME=tmp, ANDROID_SDK_HOME=tmp, TMPDIR=tmp,
                       ANDROID_USER_HOME=str(Path(tmp) / ".android"), ADB_MDNS_AUTO_CONNECT="0")
            client = [adb, "-P", str(server_port)]
            log = Path(tmp) / "console.log"
            with log.open("wb") as output, subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT) as vm:
                try:
                    deadline = time.monotonic() + 120
                    while b"started service 'console'" not in log.read_bytes():
                        if vm.poll() is not None or time.monotonic() >= deadline:
                            self.fail(log.read_text(errors="replace")[-4000:])
                        time.sleep(0.5)
                    vm.stdin.write(b"\nifconfig eth0 10.0.2.15 netmask 255.255.255.0 up\n"
                                   b"setprop service.adb.tcp.port 5555\nstart adbd\n")
                    vm.stdin.flush()
                    subprocess.run(client + ["start-server"], env=env, timeout=15, check=True)
                    for _ in range(20):
                        subprocess.run(client + ["connect", device], env=env, timeout=5,
                                       capture_output=True)
                        result = subprocess.run(client + ["-s", device, "get-state"], env=env,
                                                timeout=5, capture_output=True, text=True)
                        if result.stdout.strip() == "recovery":
                            break
                        time.sleep(1)
                    else:
                        self.fail(f"ADB 未识别 Recovery 设备。{result.stderr}")
                    for prop, expected in [("ro.build.version.release", "16"),
                                           ("ro.hardware", "cutf_cvm"),
                                           ("ro.product.cpu.abi", "arm64-v8a")]:
                        value = subprocess.check_output(client + ["-s", device, "shell",
                                                        "getprop", prop], env=env, timeout=5, text=True)
                        self.assertEqual(value.strip(), expected, prop)
                finally:
                    subprocess.run(client + ["kill-server"], env=env, timeout=10, capture_output=True)
                    if vm.poll() is None:
                        vm.stdin.write(b"\x01x")  # QEMU 串口复用器的退出命令
                        vm.stdin.flush()
                        vm.wait(timeout=15)
