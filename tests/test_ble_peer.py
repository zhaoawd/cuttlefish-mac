import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ble_peer import instance_port


class BlePeerTests(unittest.TestCase):
    def test_connector_selects_active_netsimd_with_stale_default_ini(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            instance = root / "cuttlefish/instances/cvd-1"
            (instance / "logs").mkdir(parents=True)
            config = root / "cuttlefish/assembly/cuttlefish_config.json"
            config.parent.mkdir()
            config.write_text(json.dumps({"instances": {"1": {"instance_dir": str(instance), "adb_host_port": 6520}}}))
            (instance / "logs/launcher.log").write_text("subprocess.cpp:524] --connector_instance_num=2\n")
            (root / "netsim.ini").write_text("grpc.port=41111\n")
            (root / "netsim_2.ini").write_text("grpc.port=42222\n")
            with patch.dict(os.environ, TMPDIR=temp), patch("ble_peer.subprocess.check_output") as query:
                query.return_value = "0.0.0.0:6520 \n"
                self.assertEqual(instance_port(root), 42222)
                self.assertEqual(query.call_args.args[0], [root / "bin/netsim", "-p", "42222", "devices"])
                query.return_value = "0.0.0.0:6521 \n"
                with self.assertRaisesRegex(RuntimeError, "未列出目标来宾"):
                    instance_port(root)
