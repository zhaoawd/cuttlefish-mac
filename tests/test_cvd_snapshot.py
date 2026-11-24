from contextlib import nullcontext
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cvd_host import CvdHost
from cvd_snapshot import DEVICES, detached, operate, reconnect, snapshots, writable_disks


def partial_snapshot_blocks(host):
    blocks = []
    for i, path in enumerate([host.directory / "overlay.img", host.run / ".cfctl-reconnect/persistent.qcow2",
                              host.directory / "sdcard_overlay.img"]):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        blocks.append({"device": f"drive-virtio-disk{i}", "inserted": {
            "file": str(path), "drv": "qcow2", "ro": False,
            "image": {"snapshots": [{"name": "partial"}] if i == 0 else []}}})
    return blocks


class CvdSnapshotTests(unittest.TestCase):
    def test_reconnect_retries_only_missing_devices(self):
        present = {"cf-input", "cf-sound"}
        calls = []

        def qmp(command, args=None):
            calls.append((command, args))
            if command == "qom-list":
                return [{"name": name} for name in present]
            if command == "query-chardev":
                return [{"label": "vhost-user-vsock"}]
            if command == "device_add":
                present.add(args["id"])

        sockets = {"vsock": Path("/tmp/test-vsock")}
        reconnect(qmp, sockets, time.monotonic() + 5)
        self.assertEqual([name for name, _ in calls],
                         ["qom-list", "query-chardev", "chardev-remove", "chardev-add", "device_add"])
        self.assertFalse(calls[3][1]["backend"]["data"]["server"])
        calls.clear()
        reconnect(qmp, sockets, time.monotonic() + 5)
        self.assertEqual([name for name, _ in calls], ["qom-list"])

    def test_failed_load_leaves_paused_guest_and_devices_alone(self):
        qmp = Mock(side_effect=lambda cmd, args=None: {"running": False} if cmd == "query-status" else [])
        with self.assertRaisesRegex(RuntimeError, "load failed"):
            with detached(qmp, {}, time.monotonic() + 5):
                raise RuntimeError("load failed")
        commands = [args[0] for args, _ in qmp.call_args_list]
        self.assertEqual(commands.count("device_del"), len(DEVICES))
        self.assertNotIn("device_add", commands)
        self.assertNotIn("cont", commands)

    def test_reconnect_after_partial_save_checks_disk_ownership_but_ignores_tags(self):
        with tempfile.TemporaryDirectory() as temp:
            host = CvdHost(Path(temp))
            host.monitor = host.run / "monitor"
            host.config.parent.mkdir(parents=True)
            host.config.write_text('{"instances":{"1":{"vsock_guest_cid":16}}}')
            blocks = partial_snapshot_blocks(host)
            present = [{"name": prefix + key} for prefix in ("rp-", "cf-") for key in DEVICES]
            present.remove({"name": "cf-sound"})

            def execute(cmd, args=None):
                if cmd == "device_add":
                    present.append({"name": args["id"]})
                    return {}
                return {"query-block": blocks, "qom-list": present, "query-status": {"running": True}}[cmd]

            qmp = Mock(side_effect=execute)
            connect = Mock(return_value=nullcontext(qmp))
            device = Mock(cvd=host, deadline=time.monotonic() + 5)
            with patch.object(host, "processes", return_value=dict(conflict=False, host_running=True,
                                                                  qemu=True, owns_port=True)):
                self.assertTrue(operate(device, "live-reconnect", None, connect)["reconnected"])
                self.assertIn({"name": "cf-sound"}, present)
                device.ready.assert_called_once_with(wait=True)
                blocks[0]["inserted"]["file"] = blocks[1]["inserted"]["file"]
                with self.assertRaisesRegex(RuntimeError, "运行目录"):
                    operate(device, "live-reconnect", None, connect)

    def test_reconnect_failure_reports_snapshot_error_too(self):
        qmp = Mock(side_effect=lambda cmd, args=None: {"running": True} if cmd == "query-status" else [])
        with patch("cvd_snapshot.reconnect", side_effect=RuntimeError("attach failed")):
            with self.assertRaisesRegex(RuntimeError, "load failed.*attach failed"):
                with detached(qmp, {}, time.monotonic() + 5):
                    raise RuntimeError("load failed")

    def test_snapshot_rejects_inconsistent_disks(self):
        with tempfile.TemporaryDirectory() as temp:
            host = CvdHost(Path(temp))
            blocks = partial_snapshot_blocks(host)
            with self.assertRaisesRegex(RuntimeError, "名称不一致"):
                snapshots(writable_disks(Mock(return_value=blocks), host))
            blocks[0]["inserted"]["file"] = blocks[1]["inserted"]["file"]
            with self.assertRaisesRegex(RuntimeError, "运行目录"):
                snapshots(writable_disks(Mock(return_value=blocks), host))

    def test_paused_guest_guides_user_to_force_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            host = CvdHost(Path(temp))
            host.monitor = host.run / "monitor"
            host.config.parent.mkdir(parents=True)
            host.config.write_text('{"instances":{"1":{"vsock_guest_cid":16}}}')
            blocks = partial_snapshot_blocks(host)
            present = [{"name": "rp-" + key} for key in DEVICES]
            qmp = Mock(side_effect=lambda cmd, args=None: {
                "query-block": blocks, "qom-list": present, "query-status": {"running": False}}[cmd])
            connect = Mock(return_value=nullcontext(qmp))
            device = Mock(cvd=host, deadline=time.monotonic() + 5)
            with patch.object(host, "processes", return_value=dict(conflict=False, host_running=True,
                                                                  qemu=True, owns_port=True)):
                with self.assertRaisesRegex(RuntimeError, "device stop --force"):
                    operate(device, "live-restore", "snap", connect)
