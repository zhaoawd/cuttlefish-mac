"""Cuttlefish instance 1 的运行配置、宿主进程和正常关机。"""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time

from instance_store import LOCAL, lock
from phone import ROOT

NAME = "darwin-1" if platform.system() == "Darwin" else "linux-1"


def run_directory():
    return Path(os.environ.get("CF_RUN", LOCAL / ("run-" + platform.system().lower()))).resolve()


def tcp_listeners(device, port):
    if platform.system() != "Darwin":
        lsof = shutil.which("lsof") or "/usr/sbin/lsof"
        return set(device.run([lsof, "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
                              check=False).splitlines())
    # netstat 的 TCP PCB 查询在部分宿主进程环境中会返回空表。
    table = device.run(["/usr/bin/nettop", "-n", "-m", "tcp", "-L", "1", "-J", "state"], limit=3,
                       env=dict(os.environ, LC_ALL="C"))
    header, _, rows = table.partition("\n")
    if header != ",state,":
        detail = f"：{header}" if header else ""
        raise RuntimeError(f"nettop 未返回 TCP 表头{detail}")
    owners = set()
    pid = ""
    for line in rows.splitlines():
        try:
            label, state, _ = line.rsplit(",", 2)
        except ValueError as exc:
            raise RuntimeError("无法识别 nettop TCP 表的列") from exc
        if not state:
            # 每个进程的套接字行紧跟在 name.pid 之后。
            pid = label.rpartition(".")[2]
            continue
        if state.casefold() != "listen":
            continue
        local, separator, _ = label.partition("<->")
        if not separator:
            raise RuntimeError("无法识别 nettop 的 TCP 监听地址")
        if not local.endswith((f":{port}", f".{port}")):
            continue
        if not pid.isascii() or not pid.isdecimal() or int(pid) <= 0:
            raise RuntimeError(f"nettop 未返回端口 {port} 的有效监听进程 PID")
        owners.add(pid)
    return owners


class CvdHost:
    def __init__(self, directory):
        self.run = directory
        self.directory = directory / "cuttlefish/instances/cvd-1"
        self.config = directory / "cuttlefish/assembly/cuttlefish_config.json"
        self.port = 6520
        self.serial = None
        self.monitor = None
        if self.config.exists():
            config = json.loads(self.config.read_text())
            item = config.get("instances", {}).get("1", {})
            if not all(item.get(key) for key in ("instance_dir", "adb_host_port", "serial_number")):
                raise ValueError("Cuttlefish 配置缺少实例 1 的目录、端口或序列号")
            if (Path(item["instance_dir"]).resolve() != self.directory or
                    Path(config.get("root_dir", "")).resolve() != directory / "cuttlefish"):
                raise ValueError("Cuttlefish 配置指向其他运行目录")
            if platform.system() == "Linux" and set(config["instances"]) != {"1"}:
                raise ValueError("Linux 运行目录须仅包含 Cuttlefish 实例 1")
            self.port = item["adb_host_port"]
            self.serial = item["serial_number"]
            if type(self.port) != int or not 1 <= self.port <= 65535 or not self.serial:
                raise ValueError("Cuttlefish 的 ADB 配置无效")
            if config.get("instances_uds_dir"):
                self.monitor = Path(config["instances_uds_dir"]) / "cvd-1/internal/qemu_monitor.sock"

    def darwin_processes(self, device):
        def pids(*args):
            result = device.run(["/usr/bin/pgrep", *args], check=False, limit=3)
            if any(not line.isdecimal() for line in result.splitlines()):
                raise RuntimeError(f"pgrep 查询失败：{result}")
            return set(result.splitlines())

        listeners = tcp_listeners(device, self.port)
        registry = Path(f"/tmp/cuttlefish_instances_{os.getuid()}.json")
        members = set()
        foreign = False
        if registry.is_file():
            entry = json.loads(registry.read_text()).get("instances", {}).get("1", {})
            pid = str(entry.get("run_cvd_pid", ""))
            if pid.isdecimal() and pid in pids("-x", "run_cvd"):
                if Path(entry.get("config_path", "")).resolve() == self.config.resolve():
                    members.add(pid)
                else:
                    foreign = True
        children = members.copy()
        while children:
            children = pids("-P", ",".join(sorted(children)), ".") - members
            members.update(children)
        vm = bool(members and members & pids("-x", "qemu-system-aarch64"))
        owner = bool(listeners) and listeners <= members
        return dict(host_running=bool(members), qemu=vm, owns_port=owner,
                    conflict=foreign or bool(listeners and not owner))

    def processes(self, device):
        # run_cvd、VM 和 ADB 代理属于同一棵进程树。端口和来宾序列号均可能复用。
        if platform.system() == "Darwin":
            return self.darwin_processes(device)
        rows = device.run(["ps", "-axo", "pid=,ppid=,comm="]).splitlines()
        procs = {}
        for line in rows:
            fields = line.strip().split(None, 2)
            if len(fields) == 3:
                procs[fields[0]] = (fields[1], Path(fields[2]).name)
        listeners = tcp_listeners(device, self.port)
        roots = set()
        foreign = False
        for pid, (_, process) in procs.items():
            if process != "run_cvd":
                continue
            try:
                directory = (Path("/proc") / pid / "cwd").readlink()
            except (PermissionError, FileNotFoundError, ProcessLookupError):
                continue
            if directory == self.directory:
                roots.add(pid)
            elif directory.parts[-3:] == ("cuttlefish", "instances", "cvd-1"):
                foreign = True

        def belongs(pid):
            while pid in procs and pid != procs[pid][0]:
                if pid in roots:
                    return True
                pid = procs[pid][0]
            return False

        vm = any(name in ("qemu-system-aarch64", "qemu-system-aar") and belongs(p)
                 for p, (_, name) in procs.items())
        owner = bool(listeners) and all(belongs(p) for p in listeners)
        return dict(host_running=bool(roots), qemu=vm, owns_port=owner,
                    conflict=foreign or bool(listeners and not owner))

    @staticmethod
    def client_environment():
        env = os.environ.copy()
        for key in ("CF_API_TOKEN", "ADB_SERVER_SOCKET", "ANDROID_ADB_SERVER_ADDRESS", "ANDROID_ADB_SERVER_PORT"):
            env.pop(key, None)
        return env

    def environment(self):
        env = self.client_environment()
        env.update(HOME=str(self.run), CF_RUN=str(self.run), ANDROID_HOST_OUT=str(self.run),
                   ANDROID_PRODUCT_OUT=str(self.run), CUTTLEFISH_INSTANCE="1")
        env.pop("CUTTLEFISH_CONFIG_FILE", None)
        return env

    def lifecycle(self, d, action):
        if not self.run.is_dir():
            raise ValueError(f"缺少宿主运行目录 {self.run}")
        with lock(self.run / ".cfctl-control.lock", deadline=d.deadline):
            state = self.processes(d)
            if state["conflict"]:
                raise RuntimeError("实例号或 ADB 端口已被其他进程占用")
            if action == "start":
                # 使用当前用户的 ADB 密钥，包括从节点重启或宿主已启动的状态恢复时。
                d.run(["adb", "-L", "tcp:localhost:5037", "start-server"], env=self.client_environment())
                if not state["host_running"]:
                    if platform.system() == "Darwin":
                        cmd = [ROOT / "cf.sh", "start"]
                    else:
                        cmd = [self.run / "bin/launch_cvd", "--daemon", "--base_instance_num=1",
                               "--vm_manager=qemu_cli", "--gpu_mode=guest_swiftshader",
                               "--start_webrtc=true", "--vsock_guest_cid=16",
                               "--cpus=2", "--memory_mb=3072", "--report_anonymous_usage_stats=n"]
                        accel = os.environ.get("CF_LINUX_ACCEL", "kvm")
                        virt_wifi = os.environ.get("CF_KVM_VIRT_WIFI") == "1"
                        if virt_wifi and accel != "kvm":
                            raise ValueError("CF_KVM_VIRT_WIFI 需要 Linux KVM")
                        if accel == "tcg":
                            cmd.append(f"--qemu_binary_dir={ROOT / 'linux-tcg'}")
                            # 同架构的 TCG 也使用 Cuttlefish 为软件模拟提供的超时倍率。
                            cmd.append("--extra_bootconfig_args=androidboot.hw_timeout_multiplier=50")
                        elif accel != "kvm":
                            raise ValueError("CF_LINUX_ACCEL 需要 kvm 或 tcg")
                        elif os.environ.get("CF_KVM_HUGETLBFS"):
                            pages = Path(os.environ["CF_KVM_HUGETLBFS"])
                            if not pages.is_absolute() or not pages.is_dir() or not os.access(pages, os.W_OK):
                                raise ValueError("CF_KVM_HUGETLBFS 需要可写的绝对目录")
                            if d.run(["findmnt", "-n", "-o", "FSTYPE", "-T", pages]) != "hugetlbfs":
                                raise ValueError("CF_KVM_HUGETLBFS 须位于已挂载的 hugetlbfs")
                        if accel == "kvm" and (os.environ.get("CF_KVM_HUGETLBFS") or virt_wifi):
                            cmd.append(f"--qemu_binary_dir={ROOT / 'linux-kvm'}")
                        if virt_wifi:
                            cmd.append("--extra_bootconfig_args=androidboot.wifi_impl=virt_wifi")
                    # launch_cvd 派生的宿主进程会继承描述符，避免经捕获管道等待 EOF。
                    with (self.run / "cfctl-launch.log").open("ab") as out:
                        proc = subprocess.run(cmd, cwd=self.run, stdin=subprocess.DEVNULL,
                                              stdout=out, stderr=out, env=self.environment(),
                                              timeout=max(0.01, d.deadline - time.monotonic()))
                    if proc.returncode:
                        raise RuntimeError(f"launch_cvd 启动失败，见 {out.name}")
                    d.bind_cvd(CvdHost(self.run))
                return d.ready(wait=True)
            identity = d.status()
            if (identity["conflict"] or not identity["host_running"] or not identity["qemu"]
                    or not identity["owns_port"] or not identity.get("serial_matches")):
                raise RuntimeError(f"{d.name} 的 ADB 未就绪：{identity}")
            log = self.directory / "logs/kernel.log"
            with log.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                try:
                    d.adb("shell", "su", "0", "reboot", "-p", check=False, limit=30)
                except subprocess.TimeoutExpired:
                    # 关机可能已提交，继续等待本次内核关机记录。
                    pass
                data = b""
                while b"reboot: Power down" not in data:
                    if time.monotonic() >= d.deadline:
                        raise TimeoutError("本次来宾关机未收到 Power down，保留宿主")
                    data = data[-256:] + stream.read()
                    time.sleep(0.1)
            cmd = [self.run / "bin/stop_cvd"]
            env = self.environment()
            if platform.system() == "Darwin":
                cmd.append("--instance_num=1")
            else:
                env["CUTTLEFISH_CONFIG_FILE"] = str(self.config)
            d.run(cmd, env=env)
            while True:
                after = self.processes(d)
                if not after["host_running"] and not after["owns_port"] and not after["conflict"]:
                    return {"guest_shutdown": True, "host_running": False, "port_closed": True}
                if time.monotonic() >= d.deadline:
                    raise TimeoutError("等待 Cuttlefish 宿主停止超时")
                time.sleep(0.2)
