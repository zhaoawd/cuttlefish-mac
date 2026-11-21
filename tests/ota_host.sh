#!/bin/bash
# 在独立目录中启停 OTA 验收实例 2。
set -euo pipefail
[[ $# == 2 && ( $1 == start || $1 == stop ) ]] || { echo "用法: $0 {start|stop} RUN_DIR" >&2; exit 2; }
root=$(cd "$(dirname "$0")/.." && pwd -P)
run=$(cd "$2" && pwd -P)
cd "$run"
unset CUTTLEFISH_CONFIG_FILE ADB_SERVER_SOCKET ANDROID_ADB_SERVER_ADDRESS ANDROID_ADB_SERVER_PORT
export ANDROID_HOST_OUT="$run" ANDROID_PRODUCT_OUT="$run" CUTTLEFISH_INSTANCE=2
export PATH="$run/bin:$PATH"
adb_client=(adb -s 127.0.0.1:6521)
config="$run/cuttlefish/assembly/cuttlefish_config.json"
os=$(uname -s)
require_closed_port() {
  python3 - <<'PY'
import socket, sys
with socket.socket() as sock:
    sock.settimeout(1)
    if sock.connect_ex(("127.0.0.1", 6521)) == 0:
        sys.exit("6521 仍在监听")
PY
}
check_config() {
  python3 - "$config" "$run" <<'PY'
import json, pathlib, sys
config = json.loads(pathlib.Path(sys.argv[1]).read_text())
if set(config["instances"]) != {"2"} or pathlib.Path(config["root_dir"]).resolve() != pathlib.Path(sys.argv[2]) / "cuttlefish":
    sys.exit("运行目录或实例号不符")
item = config["instances"]["2"]
if item["adb_host_port"] != 6521:
    sys.exit("ADB 端口不符")
print(item["serial_number"])
PY
}
if [[ $1 == start ]]; then
  [[ ! -f $config ]] || check_config >/dev/null
  [[ -x bin/launch_cvd && -s boot.img && -s vendor_boot.img ]]
  require_closed_port
  # ADB 服务沿用当前用户的密钥；宿主程序的 HOME 指向独立运行目录。
  adb -L tcp:localhost:5037 start-server
  opts=(--base_instance_num=2 --daemon --vm_manager=qemu_cli --gpu_mode=guest_swiftshader --report_anonymous_usage_stats=n)
  case $os in
    Darwin) opts+=(--qemu_binary_dir="${QEMU_BINARY_DIR:-$root/.local-cf/qemu-install/bin}"
                   --device_external_network=slirp --extra_bootconfig_args=androidboot.wifi_impl=virt_wifi
                   --vhost_user_vsock=true --start_webrtc=false) ;;
    Linux)  opts+=(--qemu_binary_dir="$root/linux-kvm" --vsock_guest_cid=17 --cpus=2 --memory_mb=3072 --start_webrtc=true) ;;
    *) exit 2 ;;
  esac
  env HOME="$run" bin/launch_cvd "${opts[@]}"
  serial=$(check_config)
  for ((i=0; i<180; i++)); do
    adb connect 127.0.0.1:6521 >/dev/null 2>&1 || true
    if [[ $("${adb_client[@]}" shell getprop sys.boot_completed 2>/dev/null || true) == 1 ]]; then
      [[ $("${adb_client[@]}" shell getprop ro.serialno) == "$serial" ]]
      exit
    fi
    sleep 2
  done
  echo "实例 2 未在时限内完成启动" >&2
  exit 1
fi
serial=$(check_config)
[[ $("${adb_client[@]}" shell getprop ro.serialno) == "$serial" ]]
kernel="$run/cuttlefish_runtime.2/logs/kernel.log"
offset=$(wc -c < "$kernel")
"${adb_client[@]}" shell su 0 reboot -p || true
for ((i=0; i<90; i++)); do
  if tail -c "+$((offset+1))" "$kernel" | grep -F 'reboot: Power down' >/dev/null; then
    if [[ $os == Darwin ]]; then
      env HOME="$run" bin/stop_cvd --instance_num=2
    else
      env HOME="$run" CUTTLEFISH_CONFIG_FILE="$config" bin/stop_cvd
    fi
    require_closed_port
    exit
  fi
  sleep 1
done
echo "未观察到本次内核关机记录" >&2
exit 1
