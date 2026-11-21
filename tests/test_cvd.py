import json
import os
import subprocess
import uuid
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import cfctl
from cvd_host import CvdHost, NAME


class CvdTests(unittest.TestCase):
    def test_stale_instance_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "cuttlefish/assembly/cuttlefish_config.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"root_dir": str(Path(tmp) / "cuttlefish"), "instances": {
                "1": {"instance_dir": "/unrelated", "serial_number": "A", "adb_host_port": 6520}}}))
            with self.assertRaisesRegex(ValueError, "其他运行目录"):
                CvdHost(Path(tmp))

    def test_foreign_listener_and_stale_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = CvdHost(Path(tmp))
            d = Mock()
            d.run.side_effect = ["1 0 launchd\n77 1 unrelated", "77"]
            with patch("cvd_host.platform.system", return_value="Darwin"), patch.object(Path, "is_file", return_value=False):
                state = host.processes(d)
            self.assertTrue(state["conflict"])
            self.assertFalse(state["owns_port"])
            self.assertFalse(state["host_running"])

    def test_linux_instance_conflict_before_adb_listens(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = CvdHost(Path(tmp))
            d = Mock()
            d.run.side_effect = ["10 1 run_cvd", ""]
            with patch("cvd_host.platform.system", return_value="Linux"), \
                    patch.object(Path, "readlink", return_value=Path("/other/cuttlefish/instances/cvd-1")):
                self.assertTrue(host.processes(d)["conflict"])

    def test_serial_mismatch_blocks_uninstall_and_shutdown(self):
        for command in (["app", "uninstall", "example.fixture"], ["device", "stop"]):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                with patch("cfctl.run_directory", return_value=Path(tmp)), \
                        patch.object(CvdHost, "processes", side_effect=lambda d: dict(
                            conflict=False, host_running=True, qemu=True, owns_port=True)), \
                        patch.object(cfctl.Device, "adb", side_effect=["device", "FOREIGN"]) as adb:
                    with self.assertRaisesRegex(RuntimeError, "未就绪"):
                        cfctl.execute(cfctl.parser().parse_args(["--device", NAME, *command]))
                    self.assertEqual([args.args[0] for args in adb.call_args_list], ["get-state", "shell"])
                    self.assertEqual(adb.call_args.args[1:], ("getprop", "ro.serialno"))

    def test_identified_guest_can_power_off_before_framework_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp, patch("cfctl.run_directory", return_value=Path(tmp)):
            device = cfctl.Device(NAME, 5)
            host = device.cvd
            host.serial = "LOCAL"
            log = host.directory / "logs/kernel.log"
            log.parent.mkdir(parents=True)
            log.write_bytes(b"old boot: reboot: Power down\n")
            online = dict(conflict=False, host_running=True, qemu=True, owns_port=True)
            offline = dict(conflict=False, host_running=False, qemu=False, owns_port=False)

            def adb(*args, **kwargs):
                if args == ("shell", "su", "0", "reboot", "-p"):
                    with log.open("ab") as stream:
                        stream.write(b"reboot: Power down\n")
                    return ""
                return {("get-state",): "device", ("shell", "getprop", "ro.serialno"): "LOCAL",
                        ("shell", "getprop", "sys.boot_completed"): ""}[args]

            with patch.object(host, "processes", side_effect=[online.copy(), online.copy(), offline]), \
                    patch.object(device, "adb", side_effect=adb), patch.object(device, "run") as run:
                self.assertTrue(host.lifecycle(device, "stop")["guest_shutdown"])
                self.assertEqual(run.call_args.args[0][0], host.run / "bin/stop_cvd")

    def test_tcg_launcher_preserves_disk_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "bin/aarch64-linux-gnu/qemu/qemu-system-aarch64"
            binary.parent.mkdir(parents=True)
            binary.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            binary.chmod(0o755)
            launcher = cfctl.ROOT / "linux-tcg/qemu-system-aarch64"
            env = dict(os.environ, ANDROID_HOST_OUT=tmp)
            cmd = [launcher, "-accel", "kvm", "-cpu", "host", "-drive", "file=a b.img"]
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True)
            self.assertEqual(proc.stdout.splitlines(),
                             ["-accel", "tcg,thread=multi", "-cpu", "max", "-drive", "file=a b.img"])

    def test_direct_disk_commands_refuse_cvd(self):
        for argv in (["device", "delete"], ["device", "clone", "bad"], ["snapshot", "save", "bad"]):
            with self.subTest(argv=argv), tempfile.TemporaryDirectory() as tmp, \
                    patch("cfctl.run_directory", return_value=Path(tmp)):
                with self.assertRaisesRegex(ValueError, "直启"):
                    cfctl.execute(cfctl.parser().parse_args(["--device", NAME, *argv]))

    def test_kvm_largepages_only_replaces_main_ram_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "bin/aarch64-linux-gnu/qemu/qemu-system-aarch64"
            binary.parent.mkdir(parents=True)
            binary.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            binary.chmod(0o755)
            launcher = cfctl.ROOT / "linux-kvm/qemu-system-aarch64"
            env = dict(os.environ, ANDROID_HOST_OUT=tmp, CF_KVM_HUGETLBFS="/huge,pages")
            pmem = "memory-backend-file,id=objpmem0,share=on,mem-path=/persist"
            ram = "memory-backend-file,id=vm_ram,size=3072M,share=on,mem-path=/regular"
            args = ["-accel", "kvm", "-object", pmem, "-object", ram]
            proc = subprocess.run([launcher, *args], env=env, check=True, text=True, capture_output=True)
            self.assertEqual(proc.stdout.splitlines(), [*args[:-1], ram.replace("/regular", "/huge,,pages")])
            bad = subprocess.run([launcher, "-object", pmem], env=env, capture_output=True)
            self.assertNotEqual(bad.returncode, 0)


@unittest.skipUnless(os.environ.get("CF_TEST_DARWIN_CYCLE") == "1" and NAME == "darwin-1",
                     "需要已停止的 Darwin Cuttlefish 和两个已启动的直启实例")
class DarwinLifecycleTests(unittest.TestCase):
    def test_restart_preserves_data_and_other_instances(self):
        from parity import guest

        def controls():
            result = {}
            for name in ("original", "recovered"):
                status = guest(name, "device", "status")
                self.assertTrue(status["boot_completed"], name)
                directory = cfctl.devices()[name][0]
                result[name] = (directory / "qemu.pid").read_text().strip()
            return result

        before = controls()
        initial = guest(NAME, "device", "status")
        self.assertFalse(initial["host_running"])
        self.assertFalse(initial["conflict"])
        marker = "/data/local/tmp/cfctl-" + uuid.uuid4().hex
        for cycle in (1, 2):
            guest(NAME, "device", "start")
            try:
                if cycle == 1:
                    guest(NAME, "shell", "touch", marker)
                else:
                    guest(NAME, "shell", "test", "-f", marker)
                    guest(NAME, "shell", "rm", marker)
            finally:
                stopped = guest(NAME, "device", "stop")
            self.assertTrue(stopped["guest_shutdown"] and stopped["port_closed"])
            self.assertEqual(before, controls())


class ParityReportTests(unittest.TestCase):
    def test_compare_requires_distinct_hosts_and_matching_inputs(self):
        from parity import CHECKS, compare

        with tempfile.TemporaryDirectory() as tmp:
            a, b = (Path(tmp) / name for name in ("a.json", "b.json"))
            report = dict(ok=True, host="Darwin", inputs={"build": "14781626"}, guest={},
                          checks=dict.fromkeys(CHECKS, True))
            a.write_text(json.dumps(report))
            b.write_text(json.dumps(report))
            with self.assertRaisesRegex(RuntimeError, "实际报告"):
                compare(a, b)
            b.write_text(json.dumps(dict(report, host="Linux", inputs={"build": "other"})))
            with self.assertRaisesRegex(RuntimeError, "inputs"):
                compare(a, b)
            b.write_text(json.dumps(dict(report, host="Linux", ok=False)))
            with self.assertRaisesRegex(RuntimeError, "完整回归"):
                compare(a, b)
            b.write_text(json.dumps(dict(report, host="Linux", kvm={"enabled": False})))
            with self.assertRaisesRegex(RuntimeError, "加速器"):
                compare(a, b, "kvm")
