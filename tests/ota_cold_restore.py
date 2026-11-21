"""在已准备且停机的专用实例 2 上验证三个写入层的冷恢复。"""
import argparse
import json
import os
from pathlib import Path
import platform
import secrets
import subprocess
import sys

from ota_acceptance import run as verify_ota
from parity import ROOT, require, write_json

sys.path.insert(0, str(ROOT))
from instance_store import lock

LAYERS = ("overlay.img", "persistent_composite_overlay.img", "sdcard_overlay.img")


def run(args):
    runtime = args.run.resolve()
    disk = runtime / "cuttlefish_runtime.2"
    args.out.mkdir(mode=0o700, parents=True, exist_ok=False)
    require(runtime.stat().st_dev == args.out.stat().st_dev, "备份和写入层需要位于同一文件系统")
    before, after, ready = (args.out / name for name in ("before", "after", "ready"))
    for folder in (before, after, ready):
        folder.mkdir(mode=0o700)
    copy = ["cp", "-cp"] if platform.system() == "Darwin" else ["cp", "--reflink=auto", "--sparse=always", "-p"]
    def host(action):
        subprocess.run(["bash", ROOT / "tests/ota_host.sh", action, runtime], check=True)
    adb = ["adb", "-s", "127.0.0.1:6521"]
    def shell(*argv):
        return subprocess.check_output([*adb, "shell", *argv], text=True, timeout=15).strip()
    def identity():
        return {key: shell("getprop", key) for key in ("ro.serialno", "ro.build.fingerprint", "ro.boot.slot_suffix")}
    marker = "/data/local/tmp/cf-ota-check-" + secrets.token_hex(12)
    token = secrets.token_hex(32)
    local = args.out / "marker.txt"
    snapshot = None
    try:
        host("start")
        snapshot = identity()
        local.write_text(token)
        subprocess.run([*adb, "push", local, marker], check=True, timeout=15)
        host("stop")
        for name in LAYERS:
            subprocess.run([*copy, disk / name, before / name], check=True)
        host("start")
        if args.ota:
            verify_ota(argparse.Namespace(run=runtime, ota=args.ota, out=args.out / "ota"))
            ota_result = json.loads((args.out / "ota/result.json").read_text())
            require(ota_result["ok"] and ota_result.get("pending_update_cancelled"), "需要有效的跨版本升级")
        local.write_text("changed")
        subprocess.run([*adb, "push", local, marker], check=True, timeout=15)
        require(shell("cat", marker) == "changed", "测试标记未发生变化")
        modified = identity()
        host("stop")
        for name in LAYERS:
            subprocess.run([*copy, before / name, ready / name], check=True)
            os.link(disk / name, after / name)
        replaced = []
        try:
            for name in LAYERS:
                os.replace(ready / name, disk / name)
                replaced.append(name)
        except OSError:
            for name in replaced:
                os.replace(after / name, disk / name)
            raise
        host("start")
        require(identity() == snapshot and shell("cat", marker) == token, "冷恢复后指纹、槽位或标记不符")
        shell("rm", marker)
    finally:
        if snapshot:
            state = subprocess.run([*adb, "shell", "getprop", "ro.serialno"], capture_output=True,
                                   timeout=15, text=True)
            if state.stdout.strip() == snapshot["ro.serialno"]:
                host("stop")
    write_json(args.out / "result.json", dict(ok=True, before=snapshot, modified=modified, marker_restored=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ota", type=Path, help="可选的 14818820 官方 OTA")
    args = parser.parse_args()
    require(args.run.is_dir(), "运行目录不存在")
    with lock(args.run / ".cfctl-ota.lock"):
        run(args)
