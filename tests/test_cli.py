import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import cfctl
from parity import application, focused, PACKAGE

ROOT = Path(cfctl.__file__).parent


class RoutingTests(unittest.TestCase):
    def test_wait_retries_until_tree_is_available(self):
        device = cfctl.Device("original", 5)
        args = cfctl.parser().parse_args(["ui", "wait", "--text", "READY"])
        node = {"text": "READY"}
        attempts = [cfctl.UiNotReady("could not get idle state"), [], [node]]
        with patch.object(cfctl, "matching_nodes", side_effect=attempts), patch("cfctl.time.sleep"):
            self.assertEqual(cfctl.ui(device, args), [node])

    def test_no_implicit_target(self):
        args = cfctl.parser().parse_args(["input", "tap", "0", "0"])
        with self.assertRaises(ValueError):
            cfctl.execute(args)

    def test_offline_target_never_receives_action(self):
        args = cfctl.parser().parse_args(["--device", "original", "app", "uninstall", PACKAGE])
        with patch.object(cfctl.Device, "status", return_value={"boot_completed": False}), \
                patch.object(cfctl.Device, "adb") as adb:
            with self.assertRaises(RuntimeError):
                cfctl.execute(args)
            adb.assert_not_called()

    def test_foreign_listener_cannot_receive_app_command(self):
        device = cfctl.Device("original", 5)
        with tempfile.TemporaryDirectory() as tmp:
            device.directory = Path(tmp)
            (device.directory / "qemu.pid").write_text("120")
            with patch.object(cfctl, "vm_running", return_value=True), \
                    patch.object(device, "run", return_value="121"), \
                    patch.object(device, "adb", return_value="device") as adb:
                with self.assertRaises(RuntimeError):
                    device.ready()
                adb.assert_called_once_with("get-state", check=False, limit=2)


@unittest.skipUnless(os.environ.get("CF_TEST_CLI") == "1", "需要两个已启动的直启实例及 Android SDK")
class LiveCliTests(unittest.TestCase):
    def cli(self, *args, target="original"):
        cmd = [sys.executable, "-B", ROOT / "cfctl.py", "--device", target, "--json", *args]
        p = subprocess.run(cmd, text=True, capture_output=True, timeout=45)
        data = json.loads(p.stdout)
        if p.returncode:
            raise RuntimeError(data["error"])
        return data["result"]

    def recovered_state(self):
        def cli(*args):
            return self.cli(*args, target="recovered")

        return sorted(cli("app", "list")), focused(cli)

    def test_install_interact_upgrade_and_isolation(self):
        before = self.recovered_state()
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            subprocess.run(["bash", ROOT / "tests/apk/build.sh", work], check=True, timeout=120)
            application(self.cli, work, work, {"checks": {}})
        self.assertEqual(before, self.recovered_state())
