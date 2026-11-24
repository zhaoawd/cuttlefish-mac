"""在专用 Darwin CVD 实例 1 上通过正式 CLI 检查重连快照与宿主重启。"""
import argparse
import json
import os
from pathlib import Path
import secrets
import select
import subprocess
import sys

from parity import ROOT, require


def run(directory):
    env = dict(os.environ, CF_RUN=str(directory))

    def cli(*args, success=True):
        p = subprocess.run([sys.executable, "-B", ROOT / "cfctl.py", "--device", "darwin-1", "--json", *args],
                           env=env, capture_output=True, text=True, timeout=300)
        reply = json.loads(p.stdout)
        require(reply["ok"] == success and (p.returncode == 0) == success, str(reply))
        return reply.get("result", reply.get("error"))

    tag = "cf" + secrets.token_hex(8)
    ram, disk = "/dev/" + tag, "/data/local/tmp/" + tag
    initial, changed = secrets.token_hex(16), secrets.token_hex(16)

    def mark(value):
        cli("shell", "su", "0", "sh", "-c", f"echo {value} >{ram} && echo {value} >{disk} && sync")

    def read():
        return cli("shell", "su", "0", "cat", "/proc/sys/kernel/random/boot_id", ram, disk).splitlines()

    def operation(action, serial):
        with subprocess.Popen(["adb", "-s", serial, "shell", "-T", "cat"], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE) as old:
            try:
                old.stdin.write(b"before-detach\n")
                old.stdin.flush()
                require(select.select([old.stdout], [], [], 5)[0] and old.stdout.readline() == b"before-detach\n",
                        "旧 ADB 会话未返回测试数据")
                require(cli("snapshot", action, tag)["reconnected"], "缺少重连结果")
                old.wait(timeout=10)
            finally:
                if old.poll() is None:
                    old.terminate()

    first = cli("device", "start", "--reconnect-snapshots")
    ok = False
    try:
        mark(initial)
        before = read()
        require(len(before) == 3 and before[1:] == [initial, initial], str(before))
        operation("live-save", first["serial"])
        require(tag in cli("snapshot", "live-list"), "缺少保存点")
        require("已存在" in cli("snapshot", "live-save", tag, success=False), "重名快照未被拒绝")
        mark(changed)
        require(read() == [before[0], changed, changed], "保存后修改未生效")
        require(cli("device", "stop")["guest_shutdown"], "未正常关机")
        restarted = cli("device", "start")
        require(tag in cli("snapshot", "live-list"), "重启后缺少快照")
        require(cli("shell", "cat", "/proc/sys/kernel/random/boot_id") != before[0], "启动标识未变化")
        operation("live-restore", restarted["serial"])
        restored = read()
        require(restored == before, str(restored))
        cli("snapshot", "live-delete", tag)
        require(tag not in cli("snapshot", "live-list"), "删除后仍有快照")
        require("找不到" in cli("snapshot", "live-restore", tag, success=False), "已删除的快照仍可恢复")
        cli("shell", "su", "0", "rm", ram, disk)
        ok = True
        return {"restored": restored, "modified_marker": changed, "reconnected_after_save_and_restore": True,
                "survived_host_restart": True, "deleted": True}
    finally:
        if ok:
            cli("device", "stop")
        else:
            try:
                cli("device", "stop")
            except Exception:
                if cli("device", "status")["host_running"]:
                    cli("device", "stop", "--force")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path, help="专用实例 1 的独立运行目录")
    args = parser.parse_args()
    print(json.dumps(run(args.run.resolve(strict=True)), indent=2))
