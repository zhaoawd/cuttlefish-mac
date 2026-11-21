import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import wifi_data
from phone import ROOT


class WifiDataTests(unittest.TestCase):
    def test_failed_exchange_removes_only_the_new_network(self):
        state = {"enabled": False, "joined": False}
        saved = {"5    Saved    wpa2"}

        def shell(*args):
            if args[0] == "ip":
                return "inet 10.0.2.15/24"
            command, *values = args[2:]
            if command == "list-networks":
                return "\n".join(sorted(saved))
            if command == "status":
                if state["joined"]:
                    return 'Wifi is enabled\nWifi is connected to "VirtWifi"'
                return "Wifi is enabled" if state["enabled"] else "Wifi is disabled"
            if command == "set-wifi-enabled":
                state["enabled"] = values[0] == "enabled"
            elif command == "start-scan":
                pass
            elif command == "list-scan-results":
                return "00:00:00:00:00:01  5240 -50 0.1 VirtWifi  [ESS]"
            elif command == "connect-network":
                state["joined"] = True
                saved.update({"6  VirtWifi  open", "6  VirtWifi  owe^"})
            elif command == "forget-network":
                self.assertEqual(values, ["6"])
                state["joined"] = False
                saved.difference_update({"6  VirtWifi  open", "6  VirtWifi  owe^"})
            else:
                self.fail(f"unexpected shell call {args}")

        with patch.object(wifi_data, "exchange", side_effect=RuntimeError("no response")):
            with self.assertRaisesRegex(RuntimeError, "no response"):
                wifi_data.verify_wifi(shell, Mock())
        self.assertEqual(state, {"enabled": False, "joined": False})
        self.assertEqual(saved, {"5    Saved    wpa2"})

    def test_kvm_wifi_nic_is_opt_in_and_not_duplicated(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "bin/aarch64-linux-gnu/qemu/qemu-system-aarch64"
            binary.parent.mkdir(parents=True)
            binary.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            binary.chmod(0o755)
            wrapper = ROOT / "linux-kvm/qemu-system-aarch64"
            env = dict(os.environ, ANDROID_HOST_OUT=temp, CF_KVM_VIRT_WIFI="", CF_KVM_HUGETLBFS="")
            base = ["-netdev", "tap,id=hostnet0", "-drive", "file=disk.img"]
            def run(*argv):
                return subprocess.check_output([wrapper, *argv], env=env, text=True).splitlines()
            self.assertEqual(run(*base), base)
            env["CF_KVM_VIRT_WIFI"] = "1"
            added = run(*base)
            self.assertEqual(added[:len(base)], base)
            self.assertIn("user,id=hostnet2", added)
            self.assertEqual(run(*added), added)
            self.assertEqual(run("--version"), ["--version"])
            collision = subprocess.run([wrapper, *base, "-netdev", "tap,id=hostnet2"],
                                       env=env, capture_output=True, text=True)
            self.assertNotEqual(collision.returncode, 0)
            self.assertIn("已有网卡配置不同", collision.stderr)
