"""在独立 Darwin CVD 实例 2 上检查快照操作前后的同一条 ADB-over-vsock 流。"""
import argparse
import json
import os
from pathlib import Path
import secrets
import select
import subprocess
import sys
import time

from parity import ROOT, require

sys.path.insert(0, str(ROOT))
from cfctl import qmp_connection


def probe(run):
    config = json.loads((run / "cuttlefish/assembly/cuttlefish_config.json").read_text())
    require(set(config["instances"]) == {"2"} and Path(config["root_dir"]).resolve() == run / "cuttlefish",
            "需要独立实例 2 的运行目录")
    instance = config["instances"]["2"]
    port = instance["adb_host_port"]
    require(instance["vhost_user_vsock"] and port == 6521, "需要通过 vsock 转发 ADB 的实例 2")
    route = f'Server: tcp: {port}, Client: vsock: {instance["vsock_guest_cid"]}:5555 vhost_user: true'
    require(route in (run / "cuttlefish_runtime.2/logs/launcher.log").read_text(), "缺少 ADB 路由记录")
    adb = ["adb", "-s", f"127.0.0.1:{port}", "shell"]
    serial = subprocess.check_output([*adb, "getprop", "ro.serialno"], timeout=10, text=True).strip()
    require(serial == instance["serial_number"], "ADB 序列号与实例配置不符")
    report = {"route": route, "serial": serial, "exchanges": []}
    script = 'n=0; while read token; do n=$((n+1)); echo "$n $token"; done'
    monitor = Path(config["instances_uds_dir"]) / "cvd-2/internal/qemu_monitor.sock"
    tag = "vsock-" + secrets.token_hex(8)
    with subprocess.Popen([*adb, "-T", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE) as peer:
        def exchange(stage, expected):
            token = secrets.token_hex(16)
            peer.stdin.write((token + "\n").encode())
            peer.stdin.flush()
            deadline = time.monotonic() + 10
            data = bytearray()
            while not data.endswith(b"\n"):
                remaining = deadline - time.monotonic()
                require(remaining > 0 and select.select([peer.stdout], [], [], remaining)[0], f"{stage} 响应超时")
                chunk = os.read(peer.stdout.fileno(), 128)
                require(chunk and len(data) + len(chunk) <= 128, f"{stage} 连接关闭或响应过长")
                data.extend(chunk)
            reply = data.decode().split()
            require(reply == [str(expected), token], f"{stage} 来宾序号或随机数据不匹配")
            report["exchanges"].append({"stage": stage, "guest_sequence": expected})
        try:
            with qmp_connection(monitor, time.monotonic() + 180) as execute:
                def hmp(command):
                    return execute("human-monitor-command", {"command-line": command}).strip()

                require(execute("query-status")["running"], "实例需要处于运行态")
                report["blockers"] = execute("query-migrate").get("blocked-reasons", [])
                exchange("before_save", 1)
                error = hmp(f"savevm {tag}")
                report["save_error"] = error
                if error:
                    report["restored"] = False
                    require(execute("query-status")["running"], "保存失败后实例未运行")
                    exchange("after_rejected_save", 2)
                else:
                    exchange("after_save", 2)
                    error = hmp(f"loadvm {tag}")
                    require(not error, error)
                    exchange("after_restore", 2)
                    report["restored"] = True
                    error = hmp(f"delvm {tag}")
                    require(not error, error)
        finally:
            peer.stdin.close()
            if peer.poll() is None:
                peer.terminate()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path, help="专用于此实验的实例 2 目录")
    args = parser.parse_args()
    result = probe(args.run.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["restored"] else 2)
