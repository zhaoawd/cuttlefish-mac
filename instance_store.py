"""本机直启实例、离线磁盘快照和克隆。"""
from contextlib import contextmanager
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import socket
import tempfile
import time
import uuid

from phone import ROOT, QEMU, disk_directory, vm_running
from prepare_phone import MIB, write_disk

LOCAL = ROOT / ".local-cf"
BUILTINS = {"original": (LOCAL / "aosp-volume/phone", 16521),
            "recovered": (LOCAL / "phone-recovered", 16522)}
BASE = ("kernel", "initrd", "system.qcow2")
OVERLAY = "userdata-overlay.qcow2"


def home():
    return Path(os.environ.get("CF_INSTANCE_HOME", LOCAL / "instances")).resolve()


def valid_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", name):
        raise ValueError("名称须以小写字母开头，最多 40 个小写字母、数字或连字符")
    return name


def devices():
    result = BUILTINS.copy()
    for metadata in sorted(home().glob("*/instance.json")):
        try:
            port = json.loads(metadata.read_text()).get("port")
        except FileNotFoundError:
            continue
        name = valid_name(metadata.parent.name)
        if name in result:
            raise ValueError(f"实例名重复：{name}")
        if type(port) is not int or not 1024 <= port <= 65535 or port in {p for _, p in result.values()}:
            raise ValueError(f"实例端口无效或重复：{metadata}")
        result[name] = (metadata.parent, port)
    return result


@contextmanager
def lock(path, *, shared=False, deadline=None):
    with path.open("a") as stream:
        mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        while True:
            try:
                fcntl.flock(stream, mode | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if deadline is None or time.monotonic() >= deadline:
                    raise RuntimeError(f"另一项实例操作仍在执行：{path.parent}")
                time.sleep(0.05)
        yield


def offline(directory):
    if vm_running(directory) or (directory / "qemu.pid").exists():
        raise RuntimeError("需要先正常关闭源实例；运行中的源实例可使用 device clone --clean")


def copy_disks(source, dest, device, clean=False):
    # macOS cp -c 在同一 APFS 卷优先使用 clonefile，跨卷时复制文件。
    for name in BASE:
        device.run(["/bin/cp", "-c", source / name, dest / name])
    img = QEMU / "qemu-img"
    overlay = dest / OVERLAY
    if clean:
        write_disk(dest / "persistent.raw", [("frp", MIB, None)])
        device.run([img, "create", "-f", "qcow2", "-F", "qcow2", "-b", "system.qcow2", overlay])
    else:
        info = json.loads(device.run([img, "info", "--output=json", source / OVERLAY]))
        backing = info.get("backing-filename")
        if not backing or not (source / backing).samefile(source / "system.qcow2"):
            raise ValueError("写入层的基础镜像与实例目录不一致")
        for name in (OVERLAY, "persistent.raw"):
            device.run(["/bin/cp", "-c", source / name, dest / name])
        if backing != "system.qcow2":
            device.run([img, "rebase", "-u", "-f", "qcow2", "-F", "qcow2", "-b", "system.qcow2", overlay])
    device.run([img, "check", "-f", "qcow2", overlay])


def allocate_port(port, entries):
    used = {p for _, p in entries.values()}
    if port is not None and not 1024 <= port <= 65535:
        raise ValueError("ADB 端口须在 1024 到 65535 之间")
    for candidate in ([port] if port is not None else range(16523, 65536)):
        if candidate in used:
            continue
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
            except OSError as exc:
                if exc.errno != errno.EADDRINUSE:
                    raise
                continue
        return candidate
    raise ValueError("ADB 端口已分配或不可用")


def clone(device, name, port, clean):
    name = valid_name(name)
    directory = device.directory
    root = home()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if name in ("darwin-1", "linux-1") or name in devices() or (root / name).exists():
        raise ValueError(f"实例已存在：{name}")
    with tempfile.TemporaryDirectory(prefix=".stage-", dir=root) as tmp:
        stage = Path(tmp) / "data"
        stage.mkdir(mode=0o700)
        with lock(directory / "control.lock", shared=True):
            if not clean:
                offline(directory)
            copy_disks(disk_directory(directory), stage, device, clean)
        with lock(root / "registry.lock", deadline=device.deadline):
            entries = devices()
            if name in entries or (root / name).exists():
                raise ValueError(f"实例已存在：{name}")
            port = allocate_port(port, entries)
            (stage / "instance.json").write_text(json.dumps({"port": port}))
            stage.rename(root / name)
    return {"name": name, "serial": f"127.0.0.1:{port}"}


def delete_clone(device):
    if device.name in BUILTINS or device.directory.parent != home():
        raise ValueError("只能删除由 device clone 创建的实例")
    with tempfile.TemporaryDirectory(prefix=".delete-", dir=home()) as temp:
        with lock(home() / "registry.lock", deadline=device.deadline), lock(device.directory / "control.lock"):
            offline(device.directory)
            device.directory.rename(Path(temp) / "data")
    return {"deleted": device.name}


def snapshot(device, action, name=None):
    directory = device.directory
    snapshots = directory / ".snapshots"
    if action == "list":
        return sorted(p.name for p in snapshots.glob("*") if not p.name.startswith(".") and p.is_dir())
    name = valid_name(name)
    target = snapshots / name
    with lock(directory / "control.lock"):
        if action == "delete":
            if not target.is_dir():
                raise ValueError(f"找不到快照：{name}")
            with tempfile.TemporaryDirectory(prefix=".delete-", dir=snapshots) as tmp:
                target.rename(Path(tmp) / "data")
            return {"deleted": name}
        offline(directory)
        if action == "save":
            snapshots.mkdir(mode=0o700, exist_ok=True)
            if target.exists():
                raise ValueError(f"快照已存在：{name}")
            parent = snapshots
            source = disk_directory(directory)
        else:
            if not target.is_dir():
                raise ValueError(f"找不到快照：{name}")
            parent = directory / ".disk-sets"
            parent.mkdir(mode=0o700, exist_ok=True)
            source = target
        with tempfile.TemporaryDirectory(prefix=".stage-", dir=parent) as tmp:
            stage = Path(tmp) / "data"
            stage.mkdir(mode=0o700)
            copy_disks(source, stage, device)
            if action == "save":
                stage.rename(target)
            else:
                previous = disk_directory(directory)
                generation = parent / uuid.uuid4().hex
                stage.rename(generation)
                link = Path(tmp) / "active"
                link.symlink_to(generation.relative_to(directory))
                try:
                    os.replace(link, directory / ".active-disks")
                except OSError:
                    shutil.rmtree(generation)
                    raise
                if previous.parent == parent:
                    shutil.rmtree(previous)
    return {"device": device.name, "snapshot": name}
