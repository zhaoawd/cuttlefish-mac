"""将指定 Android CI 镜像组装为 phone.py 使用的磁盘、内核和 initrd。"""

import argparse
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
from zipfile import ZipFile
import zlib


ROOT = Path(__file__).resolve().parent
MIB = 1024 * 1024
SECTOR = 512
LINUX_DATA = uuid.UUID("0fc63daf-8483-4772-8e79-3d69d8477de4")
SLOTTED = ("boot", "init_boot", "vendor_boot", "vbmeta", "vbmeta_system",
           "vbmeta_system_dlkm", "vbmeta_vendor_dlkm")


def write_disk(path, partitions):
    entries = bytearray(128 * 128)
    offset = MIB
    with path.open("xb") as disk:
        for index, (name, size, image) in enumerate(partitions):
            end = offset + (size + MIB - 1) // MIB * MIB
            struct.pack_into("<16s16sQQQ72s", entries, index * 128, LINUX_DATA.bytes_le,
                             uuid.uuid4().bytes_le, offset // SECTOR, end // SECTOR - 1,
                             0, name.encode("utf-16le"))
            if image is not None:
                disk.seek(offset)
                image.seek(0)
                while image.tell() < size:
                    data = image.read(min(MIB, size - image.tell()))
                    if not data:
                        raise ValueError(f"{name} 镜像不完整")
                    if data.count(0) == len(data):
                        disk.seek(len(data), os.SEEK_CUR)
                    else:
                        disk.write(data)
            offset = end

        last = (offset + MIB) // SECTOR - 1
        guid = uuid.uuid4().bytes_le

        def header(current, backup, entry_sector):
            data = bytearray(struct.pack("<8sIIIIQQQQ16sQIII", b"EFI PART", 0x10000, 92,
                                         0, 0, current, backup, 34, last - 33, guid,
                                         entry_sector, 128, 128, zlib.crc32(entries)))
            struct.pack_into("<I", data, 16, zlib.crc32(data))
            return data.ljust(SECTOR, bytes(1))

        protective_mbr = bytearray(SECTOR)
        struct.pack_into("<B3sB3sII", protective_mbr, 446, 0, bytes.fromhex("000200"), 0xee,
                         bytes.fromhex("ffffff"), 1, min(last, 2**32 - 1))
        protective_mbr[510:] = bytes.fromhex("55aa")
        disk.seek(0)
        disk.write(protective_mbr + header(1, last, 2) + entries)
        disk.seek((last - 32) * SECTOR)
        disk.write(entries + header(last, 1, last - 32))


def prepare(archive, aosp, destination, unpacker, qemu_img):
    sys.path.insert(0, str(aosp / "external/avb"))
    from avbtool import ImageHandler

    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        work = Path(temporary)
        result = work / "phone"
        result.mkdir()
        partitions = [("misc", MIB, None)]
        with ZipFile(archive) as guest:
            for name in (*SLOTTED, "super", "userdata"):
                image_path = Path(guest.extract(f"{name}.img", work))
                image = ImageHandler(str(image_path), read_only=True)
                suffixes = ("_a", "_b") if name in SLOTTED else ("",)
                partitions.extend((name + suffix, image.image_size, image) for suffix in suffixes)
        partitions.append(("metadata", 64 * MIB, None))
        raw = work / "system.raw"
        write_disk(raw, partitions)
        write_disk(result / "persistent.raw", [("frp", MIB, None)])
        subprocess.run([qemu_img, "convert", "-c", "-f", "raw", "-O", "qcow2", raw,
                        result / "system.qcow2"], check=True)
        subprocess.run([qemu_img, "compare", "-f", "raw", "-F", "qcow2", raw,
                        result / "system.qcow2"], check=True)
        subprocess.run([qemu_img, "create", "-f", "qcow2", "-F", "qcow2", "-b",
                        "system.qcow2", "userdata-overlay.qcow2"], cwd=result, check=True)
        for name in ("boot", "init_boot", "vendor_boot"):
            subprocess.run([sys.executable, unpacker, "--boot_img", work / f"{name}.img",
                            "--out", work / name], check=True, stdout=subprocess.DEVNULL)
        shutil.copyfile(work / "boot/kernel", result / "kernel")
        with (result / "initrd").open("wb") as out:
            for ramdisk in ("vendor_boot/vendor_ramdisk00", "init_boot/ramdisk"):
                subprocess.run(["lz4", "-dc", work / ramdisk], stdout=out, check=True)
            config = ((work / "vendor_boot/bootconfig").read_bytes() +
                      (ROOT / "manifests/phone.bootconfig").read_bytes())
            config += bytes(-(out.tell() + len(config)) % 4)
            out.write(config + struct.pack("<II", len(config), sum(config) & 0xffffffff))
            out.write(b"#BOOTCONFIG\n")
        result.rename(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    base = ROOT / ".local-cf/aosp-volume"
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path, help="新的实例目录，其父目录必须存在")
    parser.add_argument("--aosp", type=Path, default=base / "aosp")
    parser.add_argument("--unpack-bootimg", type=Path)
    args = parser.parse_args()
    if args.destination.exists():
        parser.error("目标目录已存在，保留其中的设备数据。请指定新目录。")
    tools = Path(os.environ.get("QEMU_BINARY_DIR", ROOT / ".local-cf/qemu-install/bin"))
    unpacker = args.unpack_bootimg or args.aosp / "system/tools/mkbootimg/unpack_bootimg.py"
    prepare(args.archive, args.aosp.resolve(), args.destination.resolve(), unpacker.resolve(),
            (tools / "qemu-img").resolve())
