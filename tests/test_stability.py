import os
import time
import unittest

from cfctl import devices
from parity import guest


@unittest.skipUnless(os.environ.get("CF_TEST_STABILITY_SECONDS"), "需要两个直启实例和指定观测时长")
class StabilityTests(unittest.TestCase):
    def test_direct_instances_stay_online(self):
        seconds = int(os.environ["CF_TEST_STABILITY_SECONDS"])
        self.assertGreater(seconds, 0)
        end = time.monotonic() + seconds
        entries = devices()
        pidfiles = {name: entries[name][0] / "qemu.pid" for name in ("original", "recovered")}
        expected = {name: path.read_text().strip() for name, path in pidfiles.items()}
        while True:
            for name, pid in expected.items():
                sample = f"{name} {time.strftime('%Y-%m-%d %H:%M:%S')}"
                state = guest(name, "doctor")
                self.assertEqual(pidfiles[name].read_text().strip(), pid, sample)
                self.assertTrue(state["owns_port"] and state["boot_completed"], sample)
                self.assertEqual(state["adb"], "device", sample)
                self.assertTrue(state["serial_echo"], sample)
                self.assertTrue(state["qmp"]["query-status"]["running"], sample)
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(30, remaining))
