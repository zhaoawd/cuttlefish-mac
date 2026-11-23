import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from cfctl import LOCAL, ROOT, qmp_connection


@unittest.skipUnless(os.environ.get("CF_TEST_COCOA_TRACE") == "1", "需要带退出诊断补丁的 Cocoa QEMU")
class CocoaQuitTests(unittest.TestCase):
    def test_quit_event_sender(self):
        qemu = LOCAL / "qemu-install/bin/qemu-system-aarch64"
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            sender = work / "send-quit"
            subprocess.run(["xcrun", "clang", ROOT / "tests/cocoa_quit.c", "-framework", "Carbon",
                            "-o", sender], check=True, timeout=30)
            log = work / "qemu.log"
            with log.open("wb") as output, subprocess.Popen(
                    [qemu, "-machine", "virt,accel=hvf", "-cpu", "host", "-m", "128M",
                     "-nodefaults", "-S", "-display", "cocoa", "-monitor", "none",
                     "-qmp", "unix:qmp.sock,server=on,wait=off"], cwd=work,
                    env=dict(os.environ, CF_COCOA_QUIT_TRACE="1"), stderr=output) as vm:
                try:
                    until = time.monotonic() + 15
                    while not (work / "qmp.sock").is_socket() and time.monotonic() < until:
                        self.assertIsNone(vm.poll())
                        time.sleep(0.1)
                    with qmp_connection(work / "qmp.sock", until) as execute:
                        self.assertEqual(execute("query-status")["status"], "prelaunch")
                    pid = subprocess.check_output([sender, str(vm.pid)], text=True, timeout=10).strip()
                    expected = f"CF_COCOA_QUIT request=apple-event sender_pid={pid}"
                    until = time.monotonic() + 10
                    while expected not in log.read_text() and time.monotonic() < until:
                        self.assertIsNone(vm.poll())
                        time.sleep(0.1)
                    self.assertIn(expected, log.read_text())
                finally:
                    if vm.poll() is None:
                        vm.terminate()
                        vm.wait(timeout=10)
