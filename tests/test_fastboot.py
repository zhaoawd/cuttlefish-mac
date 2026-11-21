import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

from cvd_host import run_directory
from phone import ROOT


@unittest.skipUnless(os.environ.get("CF_TEST_FASTBOOT") == "1", "需要 ARM64 宿主、U-Boot、fdtput 和 fastboot")
class FastbootTests(unittest.TestCase):
    def test_bootloader_getvar(self):
        for protocol in ("udp", "tcp"):
            with self.subTest(protocol=protocol):
                self.check_transport(protocol)

    def check_transport(self, protocol):
        accel = "kvm" if os.uname().sysname == "Linux" else "hvf"
        machine = f"virt,accel={accel},gic-version=3,highmem=off"
        qbin = Path(os.environ.get("QEMU_BINARY_DIR", ROOT / ".local-cf/qemu-install/bin"))
        fdtput = os.environ.get("CF_FDTPUT") or shutil.which("fdtput")
        self.assertTrue(fdtput, "请安装 fdtput 或设置 CF_FDTPUT")
        bios = run_directory() / "etc/bootloader_aarch64/bootloader.qemu"
        with socket.socket(type=socket.SOCK_DGRAM if protocol == "udp" else socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        # 仅运行配套固件，不挂载系统盘或用户盘。
        cmd = [qbin / "qemu-system-aarch64", "-cpu", "host", "-m", "512M", "-nodefaults",
               "-display", "none", "-monitor", "none", "-serial", "stdio", "-bios", bios, "-no-reboot",
               "-netdev", f"user,id=net,hostfwd={protocol}:127.0.0.1:{port}-:5554",
               "-device", "virtio-net-pci-non-transitional,netdev=net,romfile="]
        with tempfile.TemporaryDirectory() as tmp:
            dtb = Path(tmp) / "virt.dtb"
            log = Path(tmp) / "console.log"
            subprocess.run([*cmd, "-machine", f"{machine},dumpdtb={dtb}"], check=True, timeout=10)
            subprocess.run([fdtput, "-p", "-t", "i", dtb, "/config", "bootdelay", "3"], check=True, timeout=5)
            with log.open("wb") as output, subprocess.Popen(
                    [*cmd, "-machine", machine, "-dtb", dtb], stdin=subprocess.PIPE,
                    stdout=output, stderr=subprocess.STDOUT) as vm:
                def wait_for(marker, interrupt=False):
                    until = time.monotonic() + 10
                    while marker not in log.read_bytes():
                        if vm.poll() is not None or time.monotonic() >= until:
                            self.fail(log.read_text(errors="replace")[-1500:])
                        if interrupt:
                            vm.stdin.write(b" ")
                            vm.stdin.flush()
                        time.sleep(0.05)
                try:
                    wait_for(b"=> ", interrupt=True)
                    vm.stdin.write(b"\rvirtio scan\rsetenv netmask 255.255.255.0\r"
                                   b"setenv ipaddr 10.0.2.15\rsetenv serverip 10.0.2.2\r"
                                   + f"fastboot {protocol}\r".encode("ascii"))
                    vm.stdin.flush()
                    wait_for(b"Listening for fastboot command")
                    fb = subprocess.run(["fastboot", "-s", f"{protocol}:127.0.0.1:{port}", "getvar", "is-userspace"],
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=15)
                    self.assertEqual(fb.returncode, 0, fb.stdout)
                    self.assertIn("is-userspace: no", fb.stdout)
                finally:
                    if vm.poll() is None:
                        vm.terminate()
                    vm.wait(timeout=10)
