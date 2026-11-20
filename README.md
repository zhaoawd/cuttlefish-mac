# cuttlefish-mac

本机已通过 QEMU 直启和 Darwin `launch_cvd` 两条路径启动 Cuttlefish 的 Android 16 ARM64 phone，并在 Cocoa 窗口中验证设置首页。

## 启动 Android 手机

在本仓库根目录执行。`start` 复用本实例的写入层，`verify` 唤醒设备并打开设置，`stop` 通过来宾系统正常关机。

```sh
python3 -B phone.py start
python3 -B phone.py verify
adb -s 127.0.0.1:16521 shell
python3 -B phone.py stop
```

重启 Mac 后，若设备卷尚未挂载，在仓库根目录执行以下命令，再启动手机。

```sh
hdiutil attach "$PWD/.local-cf/aosp.sparsebundle" -mountpoint "$PWD/.local-cf/aosp-volume" -nobrowse
```

本实例使用 4 个虚拟 CPU、3072 MiB 内存、720 × 1280 的 virtio-gpu 屏幕、Cocoa 窗口和软件图形渲染。ADB 仅监听 Mac 回环地址。脚本通过 Cuttlefish 的 hvc1 控制台把本机 ADB 公钥加入 userdebug 来宾。设备基础镜像和用户写入层位于 `.local-cf/aosp-volume/phone/`。

这条启动路径直接装载 Android CI 的内核和 ramdisk。启动配置选择 F2FS userdata，并在第二块 GPT 磁盘中提供 FRP 分区。仅有 ADB 所需的本机网络转发。电话调制解调器和 Thread 网络缺少宿主后端，音频、相机与蓝牙未验收。

已有设备可直接使用。要从同一份 CI ZIP 新建设备目录，运行下列命令。准备脚本使用 AOSP 的 `avbtool.py`、`unpack_bootimg.py` 和本机的 `lz4`、`qemu-img`，临时裸盘和最终 QCOW2 会逐字节比较。默认 ADB 端口为 16521。需要同时启动一个独立实例时，用 `CF_ADB_PORT` 为它指定不同的本机端口。

```sh
export GUEST_ZIP="$PWD/.local-cf/aosp-volume/guest/aosp_cf_arm64_only_phone-img-14781626.zip"
export CF_PHONE="$PWD/.local-cf/aosp-volume/phone-new"
python3 -B prepare_phone.py "$GUEST_ZIP" "$CF_PHONE"
python3 -B phone.py start
```

## 已验证的 Recovery 启动

本机可用 Meta QEMU 和 HVF 启动 Cuttlefish 的 Android 16 Recovery，并通过回环地址使用 ADB。该入口直接载入设备 ZIP 中的内核、vendor ramdisk 和通用 ramdisk。宿主构建进度见 [TASKS.md](TASKS.md)。

设备镜像来自 Android CI 的 `aosp_cf_arm64_only_phone-userdebug` 构建 `14781626`。本地 ZIP 的 SHA-256 为 `e6bdd6ec3a3e20112bccc477a24b397fec3cb78e8aef5390bdddc8f12f77467b`。[Android CI 设备 ZIP](https://ci.android.com/builds/submitted/14781626/aosp_cf_arm64_only_phone-userdebug/latest/raw/aosp_cf_arm64_only_phone-img-14781626.zip)

```sh
export GUEST_ZIP="$PWD/.local-cf/aosp-volume/guest/aosp_cf_arm64_only_phone-img-14781626.zip"
export UNPACK_BOOTIMG="$PWD/.local-cf/aosp-volume/probe/unpack_bootimg.py"
python3 recovery.py "$GUEST_ZIP" --unpack-bootimg "$UNPACK_BOOTIMG" --adb-port 16520
```

待来宾串口出现 shell 提示符后，在串口内执行：

```sh
ifconfig eth0 10.0.2.15 netmask 255.255.255.0 up
setprop service.adb.tcp.port 5555
start adbd
```

在另一个 Mac 终端执行 `adb connect 127.0.0.1:16520`。用 `adb -s 127.0.0.1:16520 get-state` 检查 `recovery` 状态，用 `adb -s 127.0.0.1:16520 shell getprop ro.build.version.release` 读取版本。退出虚拟机时在串口内按 Ctrl-A 再按 X。

要重复执行相同的真实启动和 ADB 验证，可在设置上述两个环境变量后运行 `python3 -B -m unittest discover -s tests -v`。Recovery 测试使用临时回环端口、独立的 ADB 服务和临时密钥目录。设置 `CF_TEST_PHONE=1` 可同时运行正常系统测试，后者从 ZIP 创建独立设备，验证设置页并关机。

## Darwin launch_cvd 宿主入口

从仓库根目录执行。默认 QEMU 目录是 `.local-cf/qemu-install/bin`，可通过 `QEMU_BINARY_DIR` 指定其他安装目录。

```sh
./cf.sh hvf
```

该命令创建一台暂停的 ARM64 虚拟机，查询 QMP 状态后退出。成功时输出含有 `"status": "prelaunch"`。

本机的 Darwin 宿主包位于 `.local-cf/aosp-volume/aosp/out/host/darwin-x86/cvd-host_package.tar.gz`。`cf.sh` 默认复用 AOSP 卷外的 `.local-cf/run-darwin/`，可用 `CF_RUN` 指定其他目录。

首次启动需将 Darwin 宿主包和 ARM64 设备 ZIP 解压到同一个空目录。本次使用默认镜像参数时，首次启动实际占用约 24 GiB，主要来自磁盘组装时分配的区块。复用已有实例时可直接执行启动命令。

```sh
export CF_RUN="$PWD/.local-cf/run-darwin"
mkdir "$CF_RUN"
tar -xzf "$HOST_PACKAGE" -C "$CF_RUN"
unzip -q "$GUEST_ZIP" -d "$CF_RUN"
```

`HOST_PACKAGE` 和 `GUEST_ZIP` 分别为两个归档的本地路径。在 Mac 图形会话中执行以下命令。宿主需要绑定本机 Unix 套接字。验收通过回环地址连接 ADB，宿主包默认将转发端口 6520 绑定到所有 IPv4 接口。

```sh
./cf.sh start
./cf.sh verify
```

启动使用实例 1、QEMU、guest SwiftShader、slirp 和 vhost-user vsock。`verify` 等待 `sys.boot_completed=1`，检查 Android 版本和 ABI，然后通过 ADB 打开设置。如果 Cocoa 窗口仍显示锁屏，向上滑动后检查设置页。

正常关机时，先用 ADB 请求 Android 关机。在本次运行的 `$CF_RUN/cuttlefish/instances/cvd-1/logs/kernel.log` 中看到 `reboot: Power down` 后，再执行 `./cf.sh stop` 停止宿主进程。

```sh
"$CF_RUN/bin/adb" -s 127.0.0.1:6520 shell su 0 reboot -p
```

## 源码与构建

- QEMU 固定到 `5bcef396b9e1fed3da3f7c296dd3853ea1e0dc8a`，版本为 9.2.3。[Meta QEMU](https://github.com/facebookincubator/qemu-wearables/tree/5bcef396b9e1fed3da3f7c296dd3853ea1e0dc8a)
- 宿主补丁固定到 `1dd3aa239e02cce10d79d80d68823181a64ce078`。Meta 的精简清单包含 274 个项目，默认标签为 `android-16.0.0_r1`，其中 WebViewBootstrap 使用 `main`。[宿主构建说明](https://github.com/facebookincubator/cuttlefish-wearables/tree/1dd3aa239e02cce10d79d80d68823181a64ce078)

在固定版本 QEMU 源码的空构建目录中配置。需要 Python、Ninja、pkg-config、GLib、Pixman 和 Xcode 的 `Rez`、`SetFile`。所选源码包含 Meson wheel，Meson 可通过源码的 wrap 文件获取其余子项目。

```sh
"$QEMU_SRC/configure" --prefix="$QEMU_PREFIX" --target-list=aarch64-softmmu \
  --enable-hvf --enable-cocoa --enable-vhost-user --enable-slirp \
  --enable-tools --enable-fdt=internal --disable-docs
make -j8 && make install
```

本机的 `QEMU_PREFIX` 为仓库下的 `.local-cf/qemu-install`。构建时使用了 Python 3.13、项目内的 pkgconf 和 Ninja。dtc、libslirp、keycodemapdb 和两组浮点测试依赖使用固定版本 wrap 中记录的提交。

宿主源码应放在区分大小写的卷内。浅克隆时在空的 AOSP 目录执行以下命令，`PATCH_REPO` 指向固定提交的本地 Meta 补丁 Git 仓库。

```sh
repo init -u "$PATCH_REPO" -b 1dd3aa239e02cce10d79d80d68823181a64ce078 \
  -m minimal.xml --depth=1 --partial-clone --clone-filter=blob:none --no-clone-bundle
mkdir -p .repo/local_manifests
cp "$PROJECT_ROOT/manifests/darwin-sparse.xml" .repo/local_manifests/
repo sync -c -j6 --no-tags --fail-fast &&
  repo manifest -r -o aosp-base.xml &&
  python3 -B "$PROJECT_ROOT/apply_sparse.py"
```

`PROJECT_ROOT` 为本仓库的绝对路径。稀疏清单在首次检出前生效，只检出选定的 Clang 和 Rust 目录。需要支持 `sparse-path` 的 Android repo。Meta 的应用脚本会拒绝 Rust 的稀疏索引。本仓库的脚本检查 35 个项目的基线，单独检查 Rust 补丁所需的 `Android.bp`，再用 Meta 的 Git 辅助函数应用补丁。该脚本默认使用本机的目录布局。

本机的附加补丁选择现有的 SDK 26.5，并将仅提供 Linux 宿主变体的 SwiftShader 库及对应 ICD 清单限定在 Linux 宿主包。来宾使用设备镜像自带的 SwiftShader。Meta 的 Soong 补丁支持 SDK 26，并将工具路径改写到 `/var/select/developer_dir`。本机默认 SDK 为 27.0，该固定路径不存在。Command Line Tools 提供的 SDK 26.5 可通过 `DEVELOPER_DIR=/Library/Developer/CommandLineTools xcrun --sdk macosx26.5 --show-sdk-version` 验证。

```sh
git -C build/soong apply "$PROJECT_ROOT/patches/darwin-local-sdk.patch"
git -C device/google/cuttlefish apply "$PROJECT_ROOT/patches/darwin-guest-renderer.patch"
git -C external/swiftshader apply "$PROJECT_ROOT/patches/darwin-llvm-config.patch"
export PATH="/opt/homebrew/opt/gnu-tar/libexec/gnubin:$PATH"
export CLANG_DEFAULT_DEBUG_LEVEL=debug_level_0
export LLVM_BINDGEN_PREBUILTS_VERSION=clang-r536225
source build/envsetup.sh &&
  lunch aosp_cf_arm64_phone-bp2a-userdebug &&
  m out/host/darwin-x86/cvd-host_package.tar.gz
```
