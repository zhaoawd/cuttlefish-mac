"""在已启动的本机 Cuttlefish 实例 2 上验证指定官方 A/B OTA。"""
import argparse
import sys
import json
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid
import zipfile

from parity import ROOT, digest, require, write_json

sys.path.insert(0, str(ROOT))
from instance_store import lock

BUILDS = {
    "14781626": "2182f5c88ff3a632a8fb79a038b92911ead1cd9964439dd08dec5fddc835a277",
    "14818820": "63b843cca52435f9f681d75f2ebbe9eeddf2d1099640a953056b81ecc7b16aae",
}


def run(args):
    config = json.loads((args.run / "cuttlefish/assembly/cuttlefish_config.json").read_text())
    require(set(config["instances"]) == {"2"} and Path(config["root_dir"]).resolve() ==
            (args.run / "cuttlefish").resolve(), "需要实例 2 的独立运行目录")
    instance = config["instances"]["2"]
    require(instance["adb_host_port"] == 6521, "实例 2 的 ADB 端口不符")
    serial = "127.0.0.1:6521"
    adb = ["adb", "-s", serial]
    def shell(*argv, timeout=30, check=True):
        p = subprocess.run([*adb, "shell", shlex.join(argv)], capture_output=True, text=True, timeout=timeout)
        if check:
            require(p.returncode == 0, p.stdout + p.stderr)
        return p.stdout.strip() if check else p
    def prop(key):
        return shell("getprop", key)
    def bootctl(cmd):
        return shell("su", "0", "bootctl", cmd)
    subprocess.run(["adb", "connect", serial], check=True, timeout=10, capture_output=True)
    require(prop("ro.serialno") == instance["serial_number"] and prop("ro.hardware") == "cutf_cvm"
            and prop("ro.product.device") == "vsoc_arm64_only", "ADB 设备身份不符")
    before = prop("ro.build.version.incremental")
    with zipfile.ZipFile(args.ota) as archive:
        meta = dict(row.split("=", 1) for row in archive.read("META-INF/com/android/metadata").decode().splitlines())
        target = meta["post-build-incremental"]
        require(target in BUILDS and digest(args.ota) == BUILDS[target], "官方 OTA 摘要不符")
        require(meta["ota-type"] == "AB" and meta.get("ota-wipe") != "yes"
                and meta["pre-device"] == prop("ro.product.device"), "OTA 与测试设备不匹配")
        offset, size = re.search(r"(?:^|,)payload\.bin:(\d+):(\d+)", meta["ota-streaming-property-files"]).groups()
        require(int(size) == archive.getinfo("payload.bin").file_size, "payload 长度不符")
        headers = archive.read("payload_properties.txt").decode().strip()
    require(before in BUILDS and before != target and prop("sys.boot_completed") == "1", "测试初态不符")
    down = int(target) < int(before)
    args.out.mkdir(mode=0o700, parents=True, exist_ok=False)
    current = bootctl("get-current-slot")
    require(bootctl("get-active-boot-slot") == current and current in ("0", "1"), "已有待重启更新")
    other = str(1 - int(current))
    result = dict(source=before, target=target, serial=instance["serial_number"], sha256=BUILDS[target],
                  source_slot=current, ok=False)
    name = "cf-ota-" + uuid.uuid4().hex + ".zip"
    tmp, ota = "/data/local/tmp/" + name, "/data/ota_package/" + name
    try:
        subprocess.run([*adb, "push", str(args.ota), tmp], check=True, timeout=240)
        shell("su", "0", "cp", tmp, ota, timeout=120)
        shell("su", "0", "chmod", "0644", ota)
        shell("su", "0", "restorecon", ota)
        def apply(stage):
            p = shell("su", "0", "update_engine_client", "--update", "--follow", "--payload=file://" + ota,
                      "--offset=" + offset, "--size=" + size, "--headers=" + headers, timeout=900, check=False)
            log = p.stdout + p.stderr
            (args.out / (stage + ".log")).write_text(log)
            if down and "kPayloadTimestampError" in log:
                require(p.returncode != 0 and bootctl("get-active-boot-slot") == current
                        and prop("ro.build.version.incremental") == before, log[-1200:])
                return False
            require(p.returncode == 0 and "kSuccess (0)" in log and bootctl("get-active-boot-slot") == other,
                    log[-1200:])
            return True
        if not apply("apply"):
            result.update(downgrade_rejected=True, active_slot=current, ok=True)
            return
        if not down:
            shell("su", "0", "update_engine_client", "--reset_status")
            require(bootctl("get-active-boot-slot") == bootctl("get-current-slot") == current and
                    prop("ro.build.version.incremental") == before, "取消更新后未恢复原槽")
            result["pending_update_cancelled"] = True
            apply("reapply")
        subprocess.run([*adb, "reboot"], check=True, timeout=15)
        until = time.monotonic() + 240
        while time.monotonic() < until:
            subprocess.run(["adb", "connect", serial], timeout=8, capture_output=True)
            booted = shell("getprop", "sys.boot_completed", check=False).stdout.strip() == "1"
            if booted and shell("getprop", "ro.build.version.incremental", check=False).stdout.strip() == target:
                break
            time.sleep(2)
        else:
            raise RuntimeError("新版系统未在时限内完成启动")
        require(prop("ro.serialno") == instance["serial_number"] and prop("ro.build.fingerprint") == meta["post-build"]
                and bootctl("get-current-slot") == other, "重启后的设备、指纹或槽位不符")
        shell("su", "0", "update_engine_client", "--merge", timeout=900)
        result.update(fingerprint=prop("ro.build.fingerprint"), active_slot=other,
                      merge_status=bootctl("get-snapshot-merge-status"))
        require(result["merge_status"].lower() == "none", "快照合并未结束")
        result["ok"] = True
    finally:
        completed = result["ok"]
        try:
            if shell("getprop", "ro.serialno", check=False).stdout.strip() == instance["serial_number"]:
                shell("su", "0", "rm", "-f", tmp, ota)
                result["staging_cleaned"] = True
        finally:
            result["ok"] = result["ok"] and result.get("staging_cleaned", False)
            write_json(args.out / "result.json", result)
        if completed:
            require(result["ok"], "临时 OTA 文件未清理")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("run", "ota", "out"):
        parser.add_argument("--" + flag, type=Path, required=True)
    args = parser.parse_args()
    require(args.run.is_dir(), "运行目录不存在")
    with lock(args.run / ".cfctl-ota.lock"):
        run(args)
