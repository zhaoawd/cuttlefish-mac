#!/usr/bin/env python3
"""在 macOS 上直接用 Meta QEMU 启动已准备好的 Cuttlefish phone。"""

import argparse
import base64
import fcntl
import os
from pathlib import Path
import re
import stat
import subprocess
import time


ROOT = Path(__file__).resolve().parent
PHONE = Path(os.environ.get("CF_PHONE", ROOT / ".local-cf/aosp-volume/phone")).resolve()
QEMU = Path(os.environ.get("QEMU_BINARY_DIR", ROOT / ".local-cf/qemu-install/bin"))
ADB_PORT = int(os.environ.get("CF_ADB_PORT", 16521))
if not 1 <= ADB_PORT <= 65535:
    raise ValueError("CF_ADB_PORT 必须在 1 到 65535 之间。")
SERIAL = f"127.0.0.1:{ADB_PORT}"


def adb(*args, connect=False, check=True):
    prefix = ["adb"] if connect else ["adb", "-s", SERIAL]
    result = subprocess.run(prefix + list(args), capture_output=True, text=True, timeout=20)
    if check and result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return result.stdout.strip()


def disk_directory(directory):
    active = directory / ".active-disks"
    if active.is_symlink():
        target = active.resolve(strict=True)
        if target.parent != directory / ".disk-sets":
            raise ValueError(f"无效的磁盘目录：{target}")
        return target
    return directory


def start_vm(console_input, boot_log):
    disks = disk_directory(PHONE)
    for name in ("kernel", "initrd", "userdata-overlay.qcow2", "persistent.raw", "system.qcow2"):
        if not (disks / name).is_file():
            raise RuntimeError(f"缺少 {disks / name}，请先准备设备镜像。")
    (PHONE / ".control").mkdir(mode=0o700, exist_ok=True)
    args = [
        str(QEMU / "qemu-system-aarch64"), "-name", "Cuttlefish Android 16",
        "-machine", "virt,accel=hvf,gic-version=3", "-cpu", "host", "-smp", "4", "-m", "3072",
        "-nodefaults", "-display", "cocoa", "-g", "720x1280",
        "-monitor", "none", "-qmp", "unix:.control/qmp.sock,server=on,wait=off",
        "-pidfile", str(PHONE / "qemu.pid"),
        "-serial", "null", "-no-reboot",
        "-kernel", str(disks / "kernel"), "-initrd", str(disks / "initrd"),
        "-append", "console=ttyAMA0 earlycon=pl011,mmio32,0x09000000 printk.devkmsg=on init=/init bootconfig panic=-1",
        "-device", "virtio-gpu-pci,xres=720,yres=1280", "-device", "virtio-rng-pci",
        "-device", "virtio-keyboard-pci", "-device", "virtio-tablet-pci",
        "-drive", f"if=none,id=os,format=qcow2,file={disks}/userdata-overlay.qcow2",
        "-device", "virtio-blk-pci,drive=os",
        "-drive", f"if=none,id=persist,format=raw,file={disks}/persistent.raw",
        "-device", "virtio-blk-pci,drive=persist",
        "-nic", f"user,model=virtio-net-pci,restrict=on,hostfwd=tcp:{SERIAL}-10.0.2.15:5555",
        "-device", "virtio-serial-pci,id=virtser,max_ports=31",
    ]
    for n in range(17):
        backend = "stdio,id=hvc1" if n == 1 else f"null,id=hvc{n}"
        args.extend(["-chardev", backend, "-device", f"virtconsole,chardev=hvc{n},bus=virtser.0"])
    with (PHONE / "qemu.log").open("ab") as log:
        return subprocess.Popen(args, cwd=PHONE, stdin=console_input, stdout=boot_log,
                                stderr=log, start_new_session=True)


def vm_running(directory=PHONE):
    pidfile = directory / "qemu.pid"
    if not pidfile.is_file():
        return False
    pid = pidfile.read_text().strip()
    if not pid.isdecimal():
        return False
    state = subprocess.run(["ps", "-o", "command=", "-p", pid], capture_output=True, text=True)
    return state.returncode == 0 and "qemu-system-aarch64" in state.stdout and str(disk_directory(directory) / "persistent.raw") in state.stdout


def launch_and_connect():
    fifo = PHONE / "console.in"
    if not fifo.exists():
        os.mkfifo(fifo, 0o600)
    if not stat.S_ISFIFO(fifo.stat().st_mode):
        raise RuntimeError(f"{fifo} 应为命名管道。")
    with os.fdopen(os.open(fifo, os.O_RDWR), "wb", buffering=0) as console:
        # QEMU 的标准输入固定从本实例的管道读取，可在 ADB 就绪后关闭控制端。
        boot = PHONE / "boot.log"
        vm = None
        if not vm_running():
            with boot.open("wb") as output:
                vm = start_vm(console, output)
        with boot.open("rb") as output:
            output.seek(0, os.SEEK_END)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if vm is not None and vm.poll() is not None:
                    raise RuntimeError(f"QEMU 启动失败，日志见 {PHONE / 'qemu.log'}")
                console.write(b"\necho CFREADY$(getprop sys.boot_completed)\n")
                time.sleep(1)
                if re.search(rb"(?:^|\n)CFREADY1\r*\n", output.read()):
                    break
            else:
                raise RuntimeError(f"等待 sys.boot_completed 超时，日志见 {boot}")
        config = ["ip link set buried_eth0 up", "ip addr replace 10.0.2.15/24 dev buried_eth0",
                  "ip route get 10.0.2.2 >/dev/null 2>&1 || ip rule add pref 9999 to 10.0.2.0/24 lookup main"]
        pub = Path.home() / ".android/adbkey.pub"
        if pub.is_file():
            # 只把开发机的公钥加入本次 userdebug 来宾。
            key = pub.read_text().split()[0]
            base64.b64decode(key, validate=True)
            keys = "/data/misc/adb/adb_keys"
            config.extend([f"grep -qsF {key} {keys} || echo {key} >> {keys}",
                           f"chown system:shell {keys}", f"chmod 640 {keys}", f"restorecon {keys}"])
        config.extend(["setprop service.adb.tcp.port 5555", "setprop ctl.restart adbd"])
        console.write(("su 0 sh -c '" + "; ".join(config) + "'\n").encode())
        for _ in range(45):
            adb("connect", SERIAL, connect=True, check=False)
            if adb("shell", "getprop", "sys.boot_completed", check=False) == "1":
                print(f"Android 16 已启动，ADB 设备为 {SERIAL}")
                return
            time.sleep(1)
    raise RuntimeError("Android 已启动，ADB 尚未就绪。请查看 Cocoa 窗口中的 ADB 授权提示。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "verify", "stop"])
    action = parser.parse_args().action
    if action == "start":
        if not PHONE.is_dir():
            parser.error(f"缺少设备目录 {PHONE}，请先挂载 AOSP 卷或组装镜像。")
        with (PHONE / "control.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if adb("shell", "getprop", "ro.serialno", check=False) == "CUTTLEFISH-DIRECT":
                if not vm_running():
                    parser.error(f"{SERIAL} 已被其他实例使用。")
                print(f"ADB 已连接 {SERIAL}")
                return
            launch_and_connect()
    elif not vm_running():
        parser.error(f"未找到设备目录 {PHONE} 对应的运行中实例。")
    elif action == "stop":
        if adb("shell", "getprop", "ro.serialno") != "CUTTLEFISH-DIRECT":
            parser.error("当前 ADB 设备的序列号与本实例不符。")
        adb("shell", "su", "0", "reboot", "-p", check=False)
        for _ in range(60):
            if not (PHONE / "qemu.pid").exists():
                print("Android 已关机。")
                return
            time.sleep(1)
        raise RuntimeError("等待本实例关机超时。")
    else:
        for prop, expected in [("sys.boot_completed", "1"), ("ro.build.version.release", "16"),
                               ("ro.product.cpu.abi", "arm64-v8a"), ("ro.hardware", "cutf_cvm")]:
            value = adb("shell", "getprop", prop)
            print(f"{prop}={value}")
            if value != expected:
                raise SystemExit(1)
        adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        adb("shell", "wm", "dismiss-keyguard")
        result = adb("shell", "am", "start", "-W", "-a", "android.settings.SETTINGS")
        print(result)
        if "Status: ok" not in result:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
