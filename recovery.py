#!/usr/bin/env python3
"""用设备镜像中的内核和 ramdisk 启动独立的 Android Recovery 串口。"""

import argparse
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from zipfile import ZipFile


def main():
    root = Path(__file__).resolve().parent
    local = root / ".local-cf"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("guest_zip", type=Path)
    parser.add_argument("--unpack-bootimg", type=Path,
                        default=local / "aosp-volume/aosp/system/tools/mkbootimg/unpack_bootimg.py")
    parser.add_argument("--adb-port", type=int, metavar="PORT",
                        help="将本机回环端口转发至 Recovery 的 TCP 5555")
    args = parser.parse_args()
    if args.adb_port is not None and not 1 <= args.adb_port <= 65535:
        parser.error("ADB 端口取值范围为 1 至 65535")
    unpacker = args.unpack_bootimg.resolve()
    if not unpacker.is_file():
        parser.error("请用 --unpack-bootimg 指定 AOSP 的 unpack_bootimg.py")
    qbin = Path(os.environ.get("QEMU_BINARY_DIR", local / "qemu-install/bin"))
    if not (qbin / "qemu-system-aarch64").is_file() or not shutil.which("lz4"):
        parser.error("需要 Meta qemu-system-aarch64 和 lz4")

    # 将临时镜像保存在输入 ZIP 所在卷，保持相同的空间限额。
    with tempfile.TemporaryDirectory(dir=args.guest_zip.resolve().parent) as tmp:
        work = Path(tmp)
        with ZipFile(args.guest_zip) as archive:
            for name in ("boot", "init_boot", "vendor_boot"):
                image = work / (name + ".img")
                with archive.open(image.name) as src, image.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                subprocess.run([sys.executable, str(unpacker), "--boot_img", str(image),
                                "--out", str(work / name)], check=True, stdout=subprocess.DEVNULL)

        rd = work / "recovery.cpio"
        with rd.open("wb") as out:
            for fragment in sorted((work / "vendor_boot").glob("vendor_ramdisk[0-9]*")) + [
                    work / "init_boot/ramdisk"]:
                subprocess.run(["lz4", "-dc", str(fragment)], stdout=out, check=True)
            config = (work / "vendor_boot/bootconfig").read_bytes()
            config += b"\0" * (-(out.tell() + len(config)) % 4)
            out.write(config + struct.pack("<II", len(config), sum(config) & 0xffffffff))
            out.write(b"#BOOTCONFIG\n")

        network = []
        if args.adb_port:
            network = ["-nic", "user,model=virtio-net-pci,restrict=on,"
                       f"hostfwd=tcp:127.0.0.1:{args.adb_port}-:5555"]
        return subprocess.call([
            str(qbin / "qemu-system-aarch64"), "-machine", "virt,accel=hvf,gic-version=3",
            "-cpu", "host", "-smp", "2", "-m", "2048", "-nodefaults", "-display", "none",
            "-serial", "mon:stdio", *network, "-no-reboot", "-kernel", str(work / "boot/kernel"),
            "-initrd", str(rd), "-append",
            "console=ttyAMA0 earlycon=pl011,mmio32,0x09000000 printk.devkmsg=on init=/init bootconfig",
        ])


if __name__ == "__main__":
    sys.exit(main())
