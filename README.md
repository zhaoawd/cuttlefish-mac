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

## 按实例安装 APK 和操作界面

在仓库根目录运行 `python3 -B cfctl.py`。内置的 `original` 对应原写入层和端口 16521，`recovered` 对应卷外恢复副本和端口 16522。命令使用本机 5037 上的 ADB 服务。设备操作须显式指定实例名，`device list` 列出内置实例和本机克隆。操作前会核对 QEMU 进程、监听端口和来宾启动状态。

```sh
python3 -B cfctl.py device list
python3 -B cfctl.py --device original device wait
python3 -B cfctl.py --device original app install --launch ./demo.apk
python3 -B cfctl.py --device original ui tree
python3 -B cfctl.py --device original ui click --id com.example.demo:id/continue_button
python3 -B cfctl.py --device original ui wait --text '完成'
python3 -B cfctl.py --device original screen capture result.png
```

| 命令 | 用法 |
| --- | --- |
| `device status`、`device wait` | 分别查询状态和等待系统启动。等待时只连接所选实例的 ADB 端口。 |
| `app install [--launch] base.apk [split.apk ...]` | 安装或保留数据升级。多个 APK 属于同一应用时一起传入，base APK 放在首位。`--launch` 用 SDK Build Tools 的 `aapt2` 读取包名。 |
| `app list`、`app launch PACKAGE`、`app uninstall PACKAGE` | 列出软件包、启动桌面入口、卸载应用。 |
| `input tap X Y`、`input swipe X1 Y1 X2 Y2`、`input key KEYCODE_BACK` | 坐标以 Android 截图像素为单位。 |
| `input text 'hello world'` | 向当前焦点输入文本，字符支持范围取决于 Android 的 `input text`。 |
| `ui find`、`ui wait`、`ui click` | 按 `--id`、`--text` 或 `--desc` 精确匹配 UI Automator 节点。点击要求唯一匹配。 |
| `ui set-text --desc 'Fixture input' 'hello world'` | 替换普通 EditText 的内容并读回验证。支持英文字母、数字、空格及 `._@+-`。 |
| `shell pm list packages -3`、`logs --lines 100` | 运行单条来宾命令或读取最近日志。 |
| `doctor` | 报告磁盘可用空间、ADB 和串口回显，查询新启动实例的 QMP 运行状态和块设备。原有进程继续运行时，缺少该接口会显示 `"qmp": null`。 |

全局参数放在子命令前，例如 `python3 -B cfctl.py --json --timeout 60 --device original device wait`。JSON 返回 `ok` 和 `result` 或 `error`，操作失败返回非零退出码。应用安装保留 Android 返回的签名或版本错误。

双实例回归需要两个已就绪的直启实例、Android SDK 和 JDK。默认使用 Build Tools 36.1.0 和 android-36.1，可用 `ANDROID_HOME`、`CF_BUILD_TOOLS`、`CF_PLATFORM` 和 `JAVA_HOME` 指定。测试会在临时目录生成并签名测试 APK，安装到 original 后清理，核对 recovered 的应用列表和前台窗口。运行命令为 `CF_TEST_CLI=1 python3 -B -m unittest discover -s tests -p test_cli.py -v`。

QMP 故障回归使用已解包的 Android 内核和 ramdisk 启动独立的 HVF 实例。测试只挂接临时裸盘，用 QEMU 的 `blkdebug` 注入读错误，并检查 `query-status` 和 `query-block`。运行命令为 `CF_TEST_QMP=1 python3 -B -m unittest discover -s tests -p test_qmp.py -v`。

### 克隆和快照

从运行中的设备创建干净的测试实例时使用 `--clean`。它复制内核、ramdisk 和基础镜像，为副本新建 userdata 写入层和 FRP 磁盘。要保留已安装的应用和数据，先让源实例正常关机，再执行 `device clone NAME`。同一 APFS 卷上的文件优先采用写时复制。

```sh
python3 -B cfctl.py --device recovered device clone qa-one --clean
python3 -B cfctl.py --device qa-one device start
python3 -B cfctl.py --device qa-one app install --launch ./demo.apk
python3 -B cfctl.py --device qa-one device stop
python3 -B cfctl.py --device qa-one snapshot save installed
python3 -B cfctl.py --device qa-one device clone qa-two
python3 -B cfctl.py --device qa-two device start
```

克隆目录默认为 `.local-cf/instances/NAME/`。可通过 `CF_INSTANCE_HOME` 指定存储目录，后续 CLI 和 API 服务使用相同设置。工具从 16523 开始选择未分配的本机 ADB 端口，也可在克隆时指定 `--port`。任务可以并行操作不同实例。每个副本有独立的磁盘、控制套接字、端口和日志。

关机状态下可保存或恢复磁盘快照。快照包括内核、ramdisk、系统镜像、userdata 写入层和 FRP 磁盘。恢复时先复制并检查磁盘，再一次切换下次启动使用的磁盘目录。恢复后需要重新启动来宾。首次恢复会保留实例根目录中的初始磁盘文件。快照只保存磁盘状态。以下命令接续上例，操作已经关机的 `qa-one`。

```sh
python3 -B cfctl.py --device qa-one snapshot list
python3 -B cfctl.py --device qa-one snapshot restore installed
python3 -B cfctl.py --device qa-one device start
```

任务结束后可用 `snapshot delete NAME` 删除指定快照。对已经关机的克隆执行 `device delete` 会删除该实例及其快照。此命令仅接受由 `device clone` 创建的实例。

### HTTP 和 WebSocket API

服务在前台运行，只监听本机回环地址。HTTP 使用 Python 标准库。需要 WebSocket 时，在已安装 `websockets==15.0.1` 的 Python 环境中增加 `--ws-port 8766`。端口设为 0 时由系统分配，服务在启动时打印地址。外部客户端可通过 SSH 端口转发接入。

```sh
export CF_API_TOKEN="$(openssl rand -hex 32)"
python3 -B cfctl.py api serve --port 8765
```

客户端与服务端使用同一 token。HTTP 接口和 WebSocket 握手均通过 `Authorization: Bearer` 请求头认证。下面的命令先把 APK 上传到服务端，再安装到已启动的 `qa-one`，最后下载截图。

```sh
api=http://127.0.0.1:8765
auth="Authorization: Bearer $CF_API_TOKEN"
curl -fsS -H "$auth" -T ./demo.apk "$api/v1/files/demo.apk"
curl -fsS -H "$auth" -H 'Content-Type: application/json' "$api/v1/command" \
  -d '{"device":"qa-one","args":["app","install","--launch","demo.apk"]}'
curl -fsS -H "$auth" -H 'Content-Type: application/json' "$api/v1/command" \
  -d '{"device":"qa-one","args":["screen","capture","qa-one.png"]}'
curl -fsS -H "$auth" "$api/v1/files/qa-one.png" -o qa-one.png
```

`POST /v1/command` 和 WebSocket 的 `/v1/command` 接受相同的 JSON 对象。`args` 为 CLI 子命令及其参数数组，`device` 指定实例，`timeout` 为可选秒数。WebSocket 每条文本消息执行一次命令，按发送顺序返回 JSON。操作成功返回 `ok`、`result`，操作失败返回 `ok: false`、`error`。HTTP 对执行失败返回 422。API 文件位于 `.local-cf/api-files/`，可用 `CF_API_FILES` 指定专用目录。并行任务应为上传文件和截图选择不同文件名。

UI 操作超时时，CLI 在 `.local-cf/artifacts/` 的独立目录保存最近一次控件树和截图。每个诊断文件最多额外等待 5 秒。JSON 返回 `artifacts` 和 `capture_errors`。API 返回可通过认证下载的文件路径，若来宾失联，结果中记录采集失败的原因。

### 全量回归

在 `original` 和 `recovered` 已启动、Android SDK 和设备 ZIP 已就绪时，从仓库根目录执行。批量测试从基础镜像创建临时设备，验证并发 UI 流程、超时附件、ADB 离线诊断、磁盘快照恢复和克隆数据隔离。全新设备测试默认在宿主卷的 `.local-cf/` 组装临时磁盘，也可用 `CF_TEST_WORKDIR` 指定其他已有目录。WebSocket 回归需要前述依赖。

```sh
export GUEST_ZIP="$PWD/.local-cf/aosp-volume/guest/aosp_cf_arm64_only_phone-img-14781626.zip"
export UNPACK_BOOTIMG="$PWD/.local-cf/aosp-volume/probe/unpack_bootimg.py"
CF_TEST_CLI=1 CF_TEST_BATCH=1 CF_TEST_WS=1 CF_TEST_PHONE=1 CF_TEST_QMP=1 \
  python3 -B -m unittest discover -s tests -v
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
