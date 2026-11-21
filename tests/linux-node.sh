#!/bin/bash
# 在专用 Debian ARM64 测试节点内执行。使用独立运行目录保存镜像和写入层。
set -euo pipefail
if [[ $# -gt 1 || (${1:-} != "" && ${1:-} != --kvm-hugepages) || $(uname -sm) != 'Linux aarch64' ]]; then
  echo "请在专用 Debian ARM64 节点中运行 bash tests/linux-node.sh [--kvm-hugepages]" >&2
  exit 2
fi
target="$HOME/cvd-qa"
volume="$HOME/cf-validation.btrfs"

if ! dpkg-query -W -f='${Status}' cuttlefish-base 2>/dev/null | grep -q 'install ok installed'; then
  sudo install -d /etc/apt/keyrings
  sudo apt-get update
  sudo apt-get install -y --no-install-recommends ca-certificates curl
  curl -fsSL https://us-apt.pkg.dev/doc/repo-signing-key.gpg | sudo tee /etc/apt/keyrings/cuttlefish.asc >/dev/null
  echo 'deb [signed-by=/etc/apt/keyrings/cuttlefish.asc] https://us-apt.pkg.dev/projects/android-cuttlefish-artifacts android-cuttlefish main' |
    sudo tee /etc/apt/sources.list.d/cuttlefish.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y --no-install-recommends cuttlefish-base
fi
sudo apt-get install -y --no-install-recommends aapt adb btrfs-progs lsof unzip
sudo usermod -aG kvm,cvdnetwork "$USER"
sudo python3 -c 'import fcntl, os; assert fcntl.ioctl(os.open("/dev/kvm", os.O_RDWR), 0xae00) == 12'
mkdir -p "$target"
if ! mountpoint -q "$target"; then
  [[ -z $(ls -A "$target") ]] || { echo "挂载点已有内容: $target" >&2; exit 1; }
  if [[ ! -e "$volume" ]]; then
    # 新建稀疏文件。压缩组合磁盘中的零数据，避免占用等量的 Mac 磁盘空间。
    (set -C; : > "$volume")
    truncate -s 32G "$volume"
    mkfs.btrfs -q -m single "$volume"
  fi
  sudo mount -o loop,compress-force=zstd:1 "$volume" "$target"
  sudo chown "$USER:$(id -gn)" "$target"
fi
source_device=$(findmnt -rn -o SOURCE --mountpoint "$target")
if ! sudo losetup -j "$volume" -n -O NAME | grep -Fxq "$source_device"; then
  echo "运行目录挂载自其他块设备: $target" >&2
  exit 1
fi
printf 'CF_RUN=%s\n' "$target"

if [[ ${1:-} == --kvm-hugepages ]]; then
  pages="$HOME/kvm-pages"
  pool=/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
  mkdir -p "$pages"
  if ! mountpoint -q "$pages"; then
    [[ -z $(ls -A "$pages") ]] || { echo "挂载点已有内容: $pages" >&2; exit 1; }
    sudo mount -t hugetlbfs -o "pagesize=2M,uid=$(id -u),gid=$(id -g),mode=0700" none "$pages"
  fi
  [[ $(findmnt -rn -o FSTYPE --mountpoint "$pages") == hugetlbfs && $(stat -fc %S "$pages") == 2097152 && -w $pages ]] ||
    { echo "需要可写的 2 MiB hugetlbfs: $pages" >&2; exit 1; }
  if (( $(cat "$pool") < 1536 )); then
    echo 1536 | sudo tee "$pool" >/dev/null
  fi
  (( $(cat "$pool") >= 1536 )) || { echo "未能预留 3072 MiB 大页内存" >&2; exit 1; }
  printf 'CF_KVM_HUGETLBFS=%s\n' "$pages"
fi
