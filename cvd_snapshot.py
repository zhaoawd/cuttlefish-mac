"""Darwin CVD 的 QEMU 内部快照，保存和加载时重新枚举三个设备。"""
from contextlib import contextmanager
import json
import os
import time
from pathlib import Path

from instance_store import lock, valid_name

DEVICES = {"input": {"driver": "vhost-user-input-pci", "chardev": "vhost-user-input-keyboard"},
           "sound": {"driver": "virtio-sound-pci", "audiodev": "snd0"},
           "vsock": {"driver": "vhost-user-vsock-pci", "chardev": "vhost-user-vsock"}}


def peripherals(qmp):
    return {entry["name"] for entry in qmp("qom-list", {"path": "/machine/peripheral"})}


def reconnect(qmp, sockets, deadline):
    present = peripherals(qmp)
    errors = []
    for name, device in DEVICES.items():
        ident = "cf-" + name
        if ident in present:
            continue
        try:
            if name in sockets:
                channel = device["chardev"]
                if channel in {c["label"] for c in qmp("query-chardev")}:
                    qmp("chardev-remove", {"id": channel})
                address = {"type": "unix", "data": {"path": str(sockets[name])}}
                backend = {"type": "socket", "data": {"server": False, "addr": address}}
                until = min(deadline, time.monotonic() + 15)
                while True:
                    try:
                        qmp("chardev-add", {"id": channel, "backend": backend})
                        break
                    except RuntimeError:
                        if time.monotonic() >= until:
                            raise
                        time.sleep(0.2)
            qmp("device_add", dict(device, id=ident, bus="rp-" + name))
        except RuntimeError as exc:
            errors.append(f"{name}: {exc}")
    if errors:
        raise RuntimeError("设备重连失败，请重试 snapshot live-reconnect。" + " / ".join(errors))


@contextmanager
def detached(qmp, sockets, deadline):
    operation_error = None
    try:
        for name, device in DEVICES.items():
            ident = "cf-" + name
            qmp("device_del", {"id": ident})
            until = min(deadline, time.monotonic() + 20)
            while ident in peripherals(qmp):
                if time.monotonic() >= until:
                    raise TimeoutError(f"等待 {name} 拔除超时，请用 doctor 复核并执行 snapshot live-reconnect")
                time.sleep(0.15)
            if name in sockets:
                qmp("chardev-remove", {"id": device["chardev"]})
        yield
    except Exception as exc:
        operation_error = exc
        raise
    finally:
        try:
            if qmp("query-status")["running"]:
                reconnect(qmp, sockets, deadline)
        except Exception as recovery_error:
            if operation_error is not None:
                raise RuntimeError(f"{operation_error}。检查状态或重连设备失败：{recovery_error}") from recovery_error
            raise


def writable_disks(qmp, host):
    paths = {"drive-virtio-disk0": host.directory / "overlay.img",
             "drive-virtio-disk1": host.run / ".cfctl-reconnect/persistent.qcow2",
             "drive-virtio-disk2": host.directory / "sdcard_overlay.img"}
    blocks = {b["device"]: b["inserted"] for b in qmp("query-block") if "inserted" in b}
    if blocks.keys() != paths.keys():
        raise RuntimeError("QMP 磁盘集合与实例 1 不符")
    for key, block in blocks.items():
        if block["drv"] != "qcow2" or block["ro"] or not paths[key].samefile(block["file"]):
            raise RuntimeError("可写磁盘与此运行目录的快照配置不符")
    return blocks.values()


def snapshots(disks):
    tags = [{s["name"]: s for s in block["image"].get("snapshots", [])} for block in disks]
    if any(t.keys() != tags[0].keys() for t in tags):
        raise RuntimeError("三个磁盘上的快照名称不一致，请保留磁盘并检查")
    return {name: [t[name] for t in tags] for name in tags[0]}


def operate(device, action, name, connection):
    if name is not None:
        valid_name(name)
    host = device.cvd
    with lock(host.run / ".cfctl-control.lock", deadline=device.deadline):
        state = host.processes(device)
        if state["conflict"] or not all(state[k] for k in ("host_running", "qemu", "owns_port")) or not host.monitor:
            raise RuntimeError("无法确认当前实例的 QEMU 和端口归属")
        config = json.loads(host.config.read_text())["instances"]["1"]
        sockets = {"input": host.monitor.parent / "keyboard.sock",
                   "vsock": Path(f'/tmp/vsock_{config["vsock_guest_cid"]}_{os.getuid()}/vhost.socket')}
        with connection(host.monitor, device.deadline) as qmp:
            disks = writable_disks(qmp, host)
            attached = peripherals(qmp)
            if not all("rp-" + key in attached for key in DEVICES):
                raise RuntimeError("当前 QEMU 未使用重连快照启动配置")
            if action == "live-list":
                return sorted(snapshots(disks))
            if not qmp("query-status")["running"]:
                raise RuntimeError("QEMU 已暂停，请先用 doctor 检查。恢复失败时请保留磁盘并执行 device stop --force")
            if action == "live-reconnect":
                reconnect(qmp, sockets, device.deadline)
            else:
                tags = snapshots(disks)
                if action == "live-save" and name in tags:
                    raise ValueError(f"快照已存在：{name}")
                if action != "live-save" and name not in tags:
                    raise ValueError(f"找不到快照：{name}")
                if action == "live-delete":
                    error = qmp("human-monitor-command", {"command-line": f"delvm {name}"})
                    if error.strip():
                        raise RuntimeError(error.strip())
                    return {"device": device.name, "snapshot": name}
                if action == "live-restore":
                    clock = ("vm-clock-sec", "vm-clock-nsec", "date-sec", "date-nsec")
                    if (len({tuple(s[k] for k in clock) for s in tags[name]}) != 1
                            or not any(s["vm-state-size"] for s in tags[name])):
                        raise RuntimeError("快照时间不一致或缺少虚拟机状态")
                if not all("cf-" + key in attached for key in DEVICES):
                    raise RuntimeError("设备未完整连接，请先执行 snapshot live-reconnect")
                device.ready()
                command = {"live-save": "savevm", "live-restore": "loadvm"}[action]
                with detached(qmp, sockets, device.deadline):
                    blockers = qmp("query-migrate").get("blocked-reasons")
                    if blockers:
                        raise RuntimeError("存在迁移阻碍：" + " / ".join(blockers))
                    error = qmp("human-monitor-command", {"command-line": f"{command} {name}"})
                    if error.strip():
                        if action == "live-restore":
                            raise RuntimeError(f"{error.strip()}。请用 doctor 查看状态；如已暂停请保留磁盘并执行 device stop --force")
                        raise RuntimeError(error.strip())
        device.ready(wait=True)
        return {"device": device.name, "snapshot": name, "reconnected": True}
