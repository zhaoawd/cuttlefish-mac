#!/bin/bash
set -euo pipefail

root=$(cd "$(dirname "$0")" && pwd -P)
qbin=${QEMU_BINARY_DIR:-"$root/.local-cf/qemu-install/bin"}
run=${CF_RUN:-"$root/.local-cf/run-darwin"}
serial=127.0.0.1:6520

fail() { echo "$*" >&2; exit 1; }

case ${1:-} in
  hvf)
    [[ -x "$qbin/qemu-system-aarch64" ]] || fail "缺少 $qbin/qemu-system-aarch64"
    printf '%s\n' '{"execute":"qmp_capabilities"}' '{"execute":"query-status"}' '{"execute":"quit"}' |
      "$qbin/qemu-system-aarch64" -machine virt,accel=hvf -cpu host -m 256M \
        -S -nodefaults -no-user-config -display none -serial none -qmp stdio
    ;;
  start)
    [[ -d "$run" ]] || fail "缺少运行目录 ${run}，请先解包 Darwin 宿主包和 ARM64 设备镜像。"
    qbin=$(cd "$qbin" && pwd -P)
    cd "$run"
    for tool in bin/launch_cvd bin/assemble_cvd bin/run_cvd bin/vhost_device_vsock \
        bin/cf_vhost_user_input "$qbin/qemu-system-aarch64" "$qbin/qemu-img"; do
      [[ -x "$tool" ]] || fail "缺少可执行文件 $tool"
    done
    file -Lb bin/assemble_cvd | grep -q Mach-O || fail "assemble_cvd 需要 Darwin 宿主包。"
    for img in boot.img vendor_boot.img etc/bootloader_aarch64/bootloader.qemu; do
      [[ -s "$img" ]] || fail "缺少 $img"
    done
    exec env HOME="$PWD" ANDROID_HOST_OUT="$PWD" ANDROID_PRODUCT_OUT="$PWD" \
      ./bin/launch_cvd --base_instance_num=1 --daemon --vm_manager=qemu_cli \
      --qemu_binary_dir="$qbin" --gpu_mode=guest_swiftshader --device_external_network=slirp \
      --vhost_user_vsock=true --start_webrtc=false --report_anonymous_usage_stats=n
    ;;
  verify)
    adb="$run/bin/adb"
    if [[ ! -x "$adb" ]]; then adb=$(command -v adb) || fail "缺少 adb"; fi
    "$adb" connect "$serial"
    for ((attempt=0; attempt<120; attempt++)); do
      if [[ $("$adb" -s "$serial" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r') == 1 ]]; then
        abi=$("$adb" -s "$serial" shell getprop ro.product.cpu.abi | tr -d '\r')
        release=$("$adb" -s "$serial" shell getprop ro.build.version.release | tr -d '\r')
        printf 'sys.boot_completed=1\nro.product.cpu.abi=%s\nro.build.version.release=%s\n' "$abi" "$release"
        [[ "$abi" == arm64-v8a && "$release" == 16 ]] || fail "设备与 Android 16 ARM64 目标不符。"
        "$adb" -s "$serial" shell input keyevent KEYCODE_WAKEUP
        "$adb" -s "$serial" shell wm dismiss-keyguard
        settings=$("$adb" -s "$serial" shell am start -W -a android.settings.SETTINGS | tr -d '\r')
        printf '%s\n' "$settings"
        [[ "$settings" == *"Status: ok"* && "$settings" == *"Activity: com.android.settings/"* ]] ||
          fail "ADB 未确认已打开 Android 设置。"
        exit 0
      fi
      sleep 2
      "$adb" connect "$serial" >/dev/null 2>&1 || true
    done
    fail "等待 $serial 启动超时，请检查 $run/cuttlefish/instances/cvd-1/logs。"
    ;;
  stop)
    cd "$run"
    exec env HOME="$PWD" ANDROID_HOST_OUT="$PWD" ./bin/stop_cvd --instance_num=1
    ;;
  *) echo "用法: $0 {hvf|start|verify|stop}" >&2; exit 2 ;;
esac
