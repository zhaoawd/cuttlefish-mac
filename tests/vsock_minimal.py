"""对照相同 HVF 虚拟机在有无 vhost-user-vsock 时的快照结果。"""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time

from parity import ROOT, require

sys.path.insert(0, str(ROOT))
from cfctl import LOCAL, qmp_connection


@contextmanager
def running(argv, directory, endpoint):
    with subprocess.Popen(argv, cwd=directory, stdout=sys.stderr) as process:
        try:
            deadline = time.monotonic() + 10
            while not endpoint.is_socket():
                require(process.poll() is None, f"{argv[0]} 已退出")
                require(time.monotonic() < deadline, f"等待 {endpoint.name} 超时")
                time.sleep(0.05)
            yield
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


def backend(directory):
    binary = LOCAL / "run-darwin/bin/vhost_device_vsock"
    return running([binary, "--socket", directory / "vhost", "--uds-path", directory / "stream",
                    "--guest-cid", "7"], directory, directory / "vhost")


def protocol_features(directory):
    with backend(directory), socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(5)
        conn.connect(str(directory / "vhost"))
        with conn.makefile("rb") as stream:
            # VHOST_USER_GET_PROTOCOL_FEATURES，版本 1，无请求载荷。
            conn.sendall(struct.pack("<III", 15, 1, 0))
            request, flags, size = struct.unpack("<III", stream.read(12))
            require((request, flags, size) == (15, 5, 8), f"vhost-user 协议特性响应无效 {request=} {flags=} {size=}")
            mask, = struct.unpack("<Q", stream.read(8))
    return {"mask": hex(mask), "LOG_SHMFD": bool(mask & (1 << 1)),
            "DEVICE_STATE": bool(mask & (1 << 19))}


def snapshot(directory, with_vsock):
    qemu = Path(os.environ.get("QEMU_BINARY_DIR", LOCAL / "qemu-install/bin")).resolve()
    subprocess.run([qemu / "qemu-img", "create", "-q", "-f", "qcow2", directory / "disk", "8M"],
                   timeout=10, check=True)
    args = [qemu / "qemu-system-aarch64", "-machine", "virt,accel=hvf,memory-backend=ram", "-cpu", "host",
            "-m", "128M", "-object", "memory-backend-file,id=ram,size=128M,share=on,mem-path=ram.bin",
            "-nodefaults", "-display", "none", "-S", "-monitor", "none", "-serial", "none",
            "-qmp", "unix:qmp,server=on,wait=off", "-drive", "if=virtio,format=qcow2,file=disk"]
    with ExitStack() as stack:
        if with_vsock:
            stack.enter_context(backend(directory))
            args.extend(["-chardev", "socket,id=v,path=vhost", "-device", "vhost-user-vsock-pci,chardev=v"])
        stack.enter_context(running(args, directory, directory / "qmp"))
        execute = stack.enter_context(qmp_connection(directory / "qmp", time.monotonic() + 30))
        result = {"blockers": execute("query-migrate").get("blocked-reasons", [])}
        for command in ("savevm", "loadvm"):
            message = execute("human-monitor-command", {"command-line": f"{command} baseline"}).strip()
            result[command] = message
            if message:
                break
        return result


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
        work = Path(tmp)
        for name in ("features", "control", "vsock"):
            (work / name).mkdir()
        report = {"protocol_features": protocol_features(work / "features"),
                  "control": snapshot(work / "control", False), "vsock": snapshot(work / "vsock", True)}
        print(json.dumps(report, indent=2))
        require(report["control"].get("loadvm") == "", "对照组保存或恢复失败")
        sys.exit(0 if report["vsock"].get("loadvm") == "" else 2)
