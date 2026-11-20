import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from cfctl import LOCAL, qmp_status


@unittest.skipUnless(os.environ.get("CF_TEST_QMP") == "1", "需要本机 QEMU、HVF 和已解包的 Android 内核")
class QmpTests(unittest.TestCase):
    def test_pause_and_block_io_error(self):
        binary = LOCAL / "qemu-install/bin/qemu-system-aarch64"
        phone = LOCAL / "aosp-volume/phone"
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / ("qmp-" + "a" * 96)
            work.mkdir()
            with (work / "disk.raw").open("wb") as disk:
                disk.truncate(4 * 1024 * 1024)
            (work / "errors.conf").write_text('[inject-error]\nevent = "read_aio"\nerrno = "5"\n')
            cmd = [binary, "-machine", "virt,accel=hvf,gic-version=3", "-cpu", "host", "-m", "1536M",
                   "-kernel", phone / "kernel", "-initrd", phone / "initrd", "-append",
                   "console=ttyAMA0 init=/init bootconfig panic=0", "-nodefaults",
                   "-display", "none", "-serial", "file:serial.log",
                   "-S", "-monitor", "stdio", "-qmp", "unix:qmp.sock,server=on,wait=off",
                   "-drive", "if=none,id=probe,format=raw,file=blkdebug:errors.conf:disk.raw,rerror=stop",
                   "-device", "virtio-blk-pci,drive=probe"]
            with subprocess.Popen(cmd, cwd=work, stdin=subprocess.PIPE,
                                  stdout=subprocess.DEVNULL) as proc:
                try:
                    def wait_state(expected):
                        until = time.monotonic() + 25
                        last = None
                        while time.monotonic() < until:
                            self.assertIsNone(proc.poll())
                            if (work / "qmp.sock").is_socket():
                                last = qmp_status(work / "qmp.sock")
                                if last["query-status"]["status"] == expected:
                                    return last
                            time.sleep(0.1)
                        self.fail(f"等待 {expected} 超时，实际状态 {last}")

                    wait_state("prelaunch")
                    proc.stdin.write(b"cont\nstop\n")
                    proc.stdin.flush()
                    wait_state("paused")
                    proc.stdin.write(b"cont\n")
                    proc.stdin.flush()
                    state = wait_state("io-error")
                    disk = next(item for item in state["query-block"] if item["device"] == "probe")
                    self.assertEqual(disk["io-status"], "failed")
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=10)
