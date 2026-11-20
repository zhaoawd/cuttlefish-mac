import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("CF_TEST_PHONE") and os.environ.get("GUEST_ZIP"),
                     "需要 macOS 图形会话、已同步的 AOSP 和设备 ZIP")
class PhoneTest(unittest.TestCase):
    def test_fresh_phone(self):
        archive = Path(os.environ["GUEST_ZIP"]).resolve()
        with tempfile.TemporaryDirectory(dir=archive.parent) as work:
            phone = Path(work) / "phone"
            with socket.socket() as port:
                port.bind(("127.0.0.1", 0))
                adb_port = port.getsockname()[1]
            env = dict(os.environ, CF_PHONE=str(phone), CF_ADB_PORT=str(adb_port))
            subprocess.run([sys.executable, "-B", ROOT / "prepare_phone.py", archive, phone],
                           env=env, check=True, timeout=360)
            cmd = [sys.executable, "-B", ROOT / "phone.py"]
            try:
                subprocess.run(cmd + ["start"], env=env, check=True, timeout=280)
                check = subprocess.check_output(cmd + ["verify"], env=env, text=True, timeout=45)
                self.assertIn("com.android.settings/", check)
                self.assertIn("sys.boot_completed=1", check)
            finally:
                if (phone / "qemu.pid").exists():
                    # 独立实例的串口支持在 ADB 未就绪时从来宾正常关机。
                    if subprocess.run(cmd + ["stop"], env=env, timeout=75).returncode:
                        import time
                        with (phone / "console.in").open("wb", buffering=0) as console:
                            console.write(b"\nsu 0 reboot -p\n")
                        for _ in range(60):
                            if not (phone / "qemu.pid").exists():
                                break
                            time.sleep(1)
                    self.assertFalse((phone / "qemu.pid").exists())
