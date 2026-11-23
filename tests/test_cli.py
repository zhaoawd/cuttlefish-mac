import base64
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
    def test_set_text_refuses_password_and_ambiguous_focus(self):
        device = cfctl.Device("original", 5)
        args = cfctl.parser().parse_args(["ui", "set-text", "--focused", "北京"])
        password = {"class": "android.widget.EditText", "password": "true", "focused": "true"}
        with patch.object(device, "shell") as shell:
            with patch.object(cfctl, "matching_nodes", return_value=[password]):
                with self.assertRaisesRegex(ValueError, "普通 EditText"):
                    cfctl.ui(device, args)
            with patch.object(cfctl, "matching_nodes", return_value=[password, password]):
                with self.assertRaisesRegex(RuntimeError, "多个控件"):
                    cfctl.ui(device, args)
            shell.assert_not_called()

    def test_unicode_missing_helper_preserves_existing_text(self):
        device = cfctl.Device("original", 5)
        node = {"class": "android.widget.EditText", "text": "existing"}
        with patch.object(device, "shell", return_value="") as shell:
            with self.assertRaisesRegex(RuntimeError, "text.apk"):
                cfctl.replace_text(device, node, "content-desc", "Field", "北京🙂")
            shell.assert_called_once_with("pm", "list", "instrumentation", "dev.cuttlefish.cfctl.text")

    def test_unicode_selector_and_value_reach_instrumentation(self):
        text = "北京 A座 302🙂"
        device = cfctl.Device("original", 5)
        for key in ("resource-id", "content-desc"):
            with self.subTest(key=key):
                node = {"class": "android.widget.EditText", "focused": "true",
                        "package": PACKAGE, key: "field", "text": "initial"}
                updated = dict(node, text=text)
                with patch.object(cfctl, "matching_nodes", side_effect=[[node], [updated]]), \
                        patch.object(device, "shell", side_effect=[
                            "instrumentation:dev.cuttlefish.cfctl.text/.SetText",
                            "INSTRUMENTATION_RESULT: cfctl=ok\nINSTRUMENTATION_CODE: -1"]) as shell:
                    self.assertEqual(cfctl.replace_text(device, node, key, "field", text), [updated])
                    shell.assert_called_with("am", "instrument", "-w", "-r", "-e", "pkg", PACKAGE,
                                             "-e", "key", key, "-e", "selector", "field", "-e", "value",
                                             base64.b64encode(text.encode()).decode(), "dev.cuttlefish.cfctl.text/.SetText")

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
        header = ",state,\n"
        listener = f"tcp6 ::1.{device.port}<->*.*,Listen,\n"
        with tempfile.TemporaryDirectory() as tmp:
            device.directory = Path(tmp)
            (device.directory / "qemu.pid").write_text("120")
            with patch.object(cfctl, "vm_running", return_value=True), \
                    patch("cvd_host.platform.system", return_value="Darwin"), \
                    patch.object(device, "run") as run, patch.object(device, "adb") as adb:
                run.return_value = header + "qemu.120,,\n" + listener
                adb.side_effect = ["device", "1"]
                self.assertTrue(device.ready()["owns_port"])
                adb.side_effect = None
                adb.return_value = "device"
                foreign = "unrelated.121,,\n" + listener
                for output in (header, header + foreign, header + "qemu.120,,\n" + listener + foreign):
                    with self.subTest(output=output):
                        run.return_value = output
                        adb.reset_mock()
                        with self.assertRaisesRegex(RuntimeError, "未就绪"):
                            device.ready()
                        adb.assert_called_once_with("get-state", check=False, limit=2)
                for output, error in (("", "nettop 未返回 TCP 表头"),
                                      (header + "unrelated.0,,\n" + listener, "监听进程 PID")):
                    with self.subTest(output=output):
                        run.return_value = output
                        adb.reset_mock()
                        with self.assertRaisesRegex(RuntimeError, error):
                            device.ready()
                        adb.assert_not_called()


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
