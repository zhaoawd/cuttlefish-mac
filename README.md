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

新启动的 Cocoa 窗口标题包含 ADB 端口。需要长期运行的自动化实例可在启动前设置 `CF_PHONE_DISPLAY=none`，例如 `CF_PHONE_DISPLAY=none python3 -B cfctl.py --device qa-one device start`。无窗口模式保留虚拟屏幕、ADB 截图、串口和 QMP。显示方式在 QEMU 启动时生效。再次启动同一写入层时，上一次非空串口日志保存在实例目录的 `boot.previous.log`。

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
| `ui set-text --desc 'Fixture input' 'hello world'` | 用 `--id`、`--desc` 或 `--focused` 替换普通 EditText 的内容，并逐字读回。中文和 emoji 需要下文的输入组件。 |
| `shell pm list packages -3`、`logs --lines 100` | 运行单条来宾命令或读取最近日志。 |
| `doctor` | 报告磁盘可用空间、ADB 和串口回显，查询新启动实例的 QMP 运行状态和块设备。原有进程继续运行时，缺少该接口会显示 `"qmp": null`。 |

全局参数放在子命令前，例如 `python3 -B cfctl.py --json --timeout 60 --device original device wait`。JSON 返回 `ok` 和 `result` 或 `error`，操作失败返回非零退出码。应用安装保留 Android 返回的签名或版本错误。

### Unicode 输入与 v2rayNG 离线回归

在带 Android SDK 和 JDK 的宿主构建一次输入组件，并安装到需要输入中文的实例。可将同一份 `text.apk` 复制到 Linux 节点。构建默认使用 Build Tools 36.1.0 和 android-36.1，签名密钥保存在构建目录。下面的输入命令用于已打开且获得焦点的普通搜索框。

```sh
bash android/text/build.sh
python3 -B cfctl.py --device darwin-1 app install .local-cf/text-helper/text.apk
python3 -B cfctl.py --device darwin-1 ui set-text --focused '北京 A座 302🙂'
```

包含中文、emoji 或其他键盘映射范围外字符时，组件通过 Android 的 `ACTION_SET_TEXT` 替换文本，CLI 随后从控件树读取实际值进行核对。英文、数字、空格和 `._@+-` 沿用原输入方式。命令要求唯一匹配，拒绝密码框。自绘控件和 WebView 的可编辑性取决于应用公开的辅助功能节点。

实机回归使用英文 Android 16 测试实例和用户提供的 v2rayNG 2.3.14。脚本先校验 APK 的 SHA-256。目标实例须已启动，且尚未安装 v2rayNG 和输入组件。脚本自行安装并在结束时卸载两者。它打开配置搜索、写入并替换中文、清空搜索框，再返回应用首页核对断开连接状态。每次使用新的输出目录。

```sh
python3 -B tests/real_app.py run --device darwin-1 \
  --apk apks/v2rayNG_2.3.14_universal.apk --helper .local-cf/text-helper/text.apk \
  --observe original --observe recovered --out .local-cf/real-app/darwin
```

Linux 节点使用同一条 `run` 命令，将设备改为 `linux-1`，传入节点上相同的两个 APK 和输出目录，并省略 Mac 专属的两个 `--observe` 参数。Linux 回归通过 QMP 检查 KVM 是否启用。将两端的结果放到同一宿主后，可用以下命令比较。

```sh
python3 -B tests/real_app.py compare .local-cf/real-app/darwin/result.json .local-cf/real-app/linux/result.json
```

每端输出 `result.json` 和输入后的 `search.png`。场景失败时，脚本另行采集 `failure.xml`、`failure.png` 和最近 160 条 logcat 记录。报告记录采集错误及测试包的卸载结果。

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

UI 操作超时时，CLI 将控件树和截图保存到独立目录。直启实例的路径在 `.local-cf/artifacts/` 下，Cuttlefish 实例的路径在 `$CF_RUN/artifacts/` 下。每个诊断文件默认最多额外等待 5 秒，Linux TCG 模式为 60 秒。JSON 返回 `artifacts` 和 `capture_errors`。API 返回可通过认证下载的文件路径，若来宾失联，结果中记录采集失败的原因。

在两个直启实例在线时，可用 `CF_TEST_STABILITY_SECONDS=3600 python3 -B -m unittest discover -s tests -p test_stability.py -v` 观测一小时。测试每隔约 30 秒检查两台设备的进程 PID、端口归属、ADB、开机标志、串口回显和 QMP。

### 全量回归

GitHub Actions 配置在 push、pull request 或手动触发时运行 macOS 和 Linux 离线测试。以下硬件回归需要已准备的本机环境。

在 Apple Silicon 上启动 `original` 和 `recovered`，正常关闭独立的 `darwin-1`。准备 Android SDK、JDK、含 WebSocket 依赖的 Python、`fastboot` 和设备树工具 `fdtput`，在仓库根目录执行下面的命令。入口启用所有实机用例，任一用例跳过或失败均返回非零。默认使用仓库内的设备 ZIP、解包脚本及 120 秒稳定性采样，可通过 `GUEST_ZIP`、`UNPACK_BOOTIMG`、`CF_TEST_WORKDIR`、`CF_FDTPUT` 和 `CF_TEST_STABILITY_SECONDS` 指定本机路径或时长。

```sh
.local-cf/cli-venv/bin/python -B tests/macos_hardware.py
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

正常关机使用实例 CLI。它核对 ADB 监听进程和来宾序列号，请求 Android 关机，等到本次内核日志出现 `reboot: Power down` 后停止宿主进程。

```sh
python3 -B cfctl.py --device darwin-1 device stop
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
git -C device/google/cuttlefish apply "$PROJECT_ROOT/patches/darwin-keyboard-listener.patch"
git -C external/swiftshader apply "$PROJECT_ROOT/patches/darwin-llvm-config.patch"
export PATH="/opt/homebrew/opt/gnu-tar/libexec/gnubin:$PATH"
export CLANG_DEFAULT_DEBUG_LEVEL=debug_level_0
export LLVM_BINDGEN_PREBUILTS_VERSION=clang-r536225
source build/envsetup.sh &&
  lunch aosp_cf_arm64_phone-bp2a-userdebug &&
  m out/host/darwin-x86/cvd-host_package.tar.gz
```

## Cuttlefish 实例和跨宿主回归

在 macOS 上用 `darwin-1` 选择 Darwin 宿主包的实例 1，在 ARM64 Linux 上用 `linux-1` 选择 Linux 宿主包的实例 1。两者都从 `CF_RUN` 下的 Cuttlefish 配置读取实例目录、端口和来宾序列号。应用操作还会检查监听进程归属和 `sys.boot_completed`。克隆与磁盘快照使用前文所述的直启磁盘布局。

```sh
export CF_RUN="$PWD/.local-cf/run-darwin"
python3 -B cfctl.py --device darwin-1 device start
python3 -B cfctl.py --device darwin-1 app install --launch demo.apk
python3 -B cfctl.py --device darwin-1 doctor
python3 -B cfctl.py --device darwin-1 device stop
```

`device stop` 请求 Android 关机，等到内核日志新增 `reboot: Power down` 才调用宿主停止程序，并检查进程及 ADB 端口。Darwin 的键盘套接字补丁见上面的构建命令。更新已有运行目录时，需要在实例停止后把重新构建的 `bin/run_cvd` 安装到该目录。

测试包在装有 Android SDK 和 JDK 的一端生成一次。向另一端提供同一个 ZIP、测试包目录及本仓库代码。输出目录须为新目录。各宿主分别安装与其架构匹配的宿主包，分别运行本机 ADB 服务。Linux 目录内只组装实例 1。

```sh
python3 -B tests/parity.py prepare --image "$GUEST_ZIP" --bundle "$BUNDLE"
python3 -B tests/parity.py run --device darwin-1 --bundle "$BUNDLE" \
  --image "$GUEST_ZIP" --out "$DARWIN_RESULT" --observe original --observe recovered
```

在已安装 Cuttlefish 宿主依赖的 Linux 节点解压宿主包和设备 ZIP 后执行以下命令。`CF_RUN` 指向解压目录，`aapt2` 或 `aapt` 需在 PATH 中。将两端生成的 `result.json` 放到同一台机器执行 `compare`。

```sh
export PATH="$CF_RUN/bin:$PATH"
python3 -B cfctl.py --device linux-1 device start
python3 -B tests/parity.py run --device linux-1 --bundle "$BUNDLE" \
  --image "$GUEST_ZIP" --out "$LINUX_RESULT"
python3 -B cfctl.py --device linux-1 device stop
python3 -B tests/parity.py compare "$DARWIN_RESULT/result.json" "$LINUX_RESULT/result.json"
```

回归检查前台应用、文本输入、控件点击、升级后数据、异签名升级拒绝和拆分 APK。报告包含镜像与四个 APK 的 SHA-256、来宾构建指纹、宿主可执行文件摘要和各步耗时。截图保存在结果目录。Darwin 启停和写入层持久化另有回归，可在两个直启实例运行、`darwin-1` 已停止时执行。

本机 Darwin/HVF、Linux ARM64/TCG 和使用 2 MiB 大页的 Linux ARM64/KVM 节点已通过这六项检查。三种配置使用 Android CI 构建 14781626，设备 ZIP、四个 APK 的摘要和运行中系统的构建指纹一致。每轮测试结束时均确认测试包已卸载，Darwin 测试前后，两个直启对照实例各自的应用列表和前台窗口一致。

```sh
CF_TEST_DARWIN_CYCLE=1 python3 -B -m unittest discover -s tests -p test_cvd.py -v
```

### 在 Apple Silicon 本机部署 Linux 对照节点

本机采用 Lima 2.3.0、Virtualization.framework 和 Debian 13 ARM64。模板请求 4 个虚拟 CPU、6 GiB 内存和 40 GiB 稀疏磁盘。Mac 须支持嵌套虚拟化。在节点内运行的准备脚本检查 KVM API，安装 Cuttlefish 宿主依赖，并将专用 Btrfs 文件系统挂载到 `$HOME/cvd-qa`。QEMU 组装原始磁盘会写入大量零数据，运行目录使用 Zstandard 压缩。设备 ZIP 和两个宿主包均应保存在版本库之外或被忽略的目录内。

```sh
limactl start --tty=false --name=cf-qa tests/lima-arm64.yaml
limactl shell cf-qa -- mkdir -p project/tests
limactl copy cfctl.py cvd_host.py phone.py prepare_phone.py instance_store.py cf_api.py cf-qa:project/
limactl copy tests/linux-node.sh tests/parity.py cf-qa:project/tests/
limactl copy -r linux-tcg cf-qa:project/
limactl copy -r linux-kvm cf-qa:project/
limactl copy "$LINUX_HOST_PACKAGE" cf-qa:host.tar.gz
limactl copy "$GUEST_ZIP" cf-qa:guest.zip
limactl copy -r "$BUNDLE" cf-qa:bundle
limactl shell cf-qa -- bash project/tests/linux-node.sh
```

本次 Linux 宿主包取自 Android CI 构建 14781626，SHA-256 为 `fd6fb6fdbd7451759c2b654d715d3cb8e27a9ec278a8e183957b5fdad7f2c521`。首次解包前验证归档。后续启动可复用该运行目录。外层 Linux 重启后，重新运行 `tests/linux-node.sh` 挂载已有卷，再进入启停流程。首次变更用户组后，使用有新组权限的登录会话。Lima 的 SSH 连接若仍复用旧用户组，可在节点中执行 `sudo -iu "$USER"` 后继续。[Android CI 宿主包](https://ci.android.com/builds/submitted/14781626/aosp_cf_arm64_only_phone-userdebug/latest/raw/cvd-host_package.tar.gz) · [Cuttlefish 宿主依赖](https://github.com/google/android-cuttlefish) · [Lima VZ](https://lima-vm.io/docs/config/vmtype/vz/)

```sh
# 在 Linux 节点内的用户主目录执行
printf '%s  %s\n' fd6fb6fdbd7451759c2b654d715d3cb8e27a9ec278a8e183957b5fdad7f2c521 host.tar.gz \
  e6bdd6ec3a3e20112bccc477a24b397fec3cb78e8aef5390bdddc8f12f77467b guest.zip | sha256sum -c -
export CF_RUN="$HOME/cvd-qa" CF_LINUX_ACCEL=tcg
tar -xzf host.tar.gz -C "$CF_RUN"
unzip -q guest.zip -d "$CF_RUN"
cd project
export GUEST_ZIP="$HOME/guest.zip" BUNDLE="$HOME/bundle" LINUX_RESULT="$HOME/parity-result"
```

本机 Linux 的外层 vsock CID 为 3，内层实例使用 CID 16。Linux 启动命令采用官方包自带的 QEMU、来宾 SwiftShader、2 个虚拟 CPU 和 3072 MiB 内存。两个宿主均按各自配置中的 ADB 端口寻址。

原生 Linux 宿主默认使用 KVM。本机 Lima 对照节点使用 `CF_LINUX_ACCEL=tcg`，由同一宿主包的 QEMU 执行软件模拟。切换模式前先正常关闭 Android。Linux 对照报告中的 `kvm.enabled` 来自运行中实例的 QMP 查询。

### 本机嵌套 KVM 的大页配置

本次 KVM 验证使用 6 个外层虚拟 CPU 和 10 GiB 内存，Android 使用 2 个虚拟 CPU 和 3072 MiB 内存。按上述模板创建新 KVM 节点时，可在 `limactl start` 中加入 `--cpus=6 --memory=10`。下面的准备命令在专用 Linux 节点内挂载用户可写的 hugetlbfs，并为 Android 预留至少 1536 个 2 MiB 大页（3 GiB）。该内存池供 Linux 节点使用，预留期间会减少普通内存。节点重启后需重新准备。

```sh
# 在已解包宿主包和设备镜像的 Linux 节点内执行
cd ~/project
bash tests/linux-node.sh --kvm-hugepages
export CF_RUN="$HOME/cvd-qa" CF_LINUX_ACCEL=kvm CF_KVM_HUGETLBFS="$HOME/kvm-pages"
export PATH="$CF_RUN/bin:$PATH"
python3 -B cfctl.py --device linux-1 device start
python3 -B tests/parity.py run --device linux-1 --image "$GUEST_ZIP" --bundle "$BUNDLE" --out "$LINUX_RESULT"
python3 -B cfctl.py --device linux-1 device stop
# 将两份报告放在同一台机器后执行
python3 -B tests/parity.py compare "$DARWIN_RESULT/result.json" "$LINUX_RESULT/result.json" --linux-accel kvm
```

本次验证保留的 `cf-qa` 节点中，仓库脚本位于 `/mnt/parity/project`，通过首启验证的运行目录为 `$HOME/cvd-qa/kvm-fresh`。复用时先执行准备脚本挂载卷，再将 `CF_RUN` 和 `PATH` 指向这个已有运行目录及其 `bin`。同一 KVM 实例重启后连续运行三轮，首次安装测试 APK 耗时 0.31–0.45 秒，`ui set-text` 耗时 6.28–6.38 秒。较早的一轮 TCG 对照分别为 25.19 秒和 73.04 秒。两种模式的外层资源和测试时间不同。

原失败现场中，`cppreopts` 进程耗时 310 秒并以状态 0 结束。包管理器的等待上限为 100 秒，尝试写入 `sys.cppreopt=timed-out` 时返回 `Broken pipe`，随后 `system_server` 退出。改用专用大页后，同一 Android 构建在新的运行目录完成首次启动，包管理器记录 `cppreopts took 1872 ms`。QMP 返回 `query-kvm.enabled=true`，Linux 的 `/proc/<QEMU PID>/status` 显示 `HugetlbPages: 3145728 kB`。这些结果验证了本机可用的配置，旧现场属性连接断开的具体原因仍待确认。TCG 节点继续用超时倍率 50 做软件模拟对照。其他项目曾在嵌套 ARM64 环境中观察到 RAM 映射方式影响耗时。[Firecracker 嵌套实验记录](https://github.com/openai/tart/issues/1340)

Meta 描述了 Mac 开发与 Linux CI 共用 Cuttlefish 虚拟设备的工作流。本仓库用 Android 16 phone 镜像对照 APK 和 UI 行为。下面列出已用同一测试程序在 Darwin/HVF 与 Linux/KVM 复验的范围。[Meta 技术文章](https://engineering.fb.com/2026/11/13/android/cuttlefish-on-mac/)

### 本地硬件任务和设备能力

在带 Android SDK 的机器上将 `tests/apk/build.sh` 的输出放入新目录。将其中的 `v1.apk`、同一份 `text.apk` 和 v2rayNG 2.3.14 放到两个宿主可读的位置。测试开始前专用 Cuttlefish 实例须已停止。Mac 的两个直启对照实例保持在线。Linux 使用上文的大页、`CF_RUN` 和 `PATH` 配置。`device start` 会使用当前用户的 ADB 密钥启动或复用本机 5037 服务。

```sh
mkdir -p .local-cf/media-fixture
bash tests/apk/build.sh .local-cf/media-fixture
python3 -B tests/host_acceptance.py --fixture .local-cf/media-fixture/v1.apk \
  --apk apks/v2rayNG_2.3.14_universal.apk --helper .local-cf/text-helper/text.apk \
  --out .local-cf/acceptance-run
```

同一入口负责启动、真实应用回归、设备能力回归和来宾正常关机。每次指定新的输出目录。`result.json` 记录启停结果，`app/result.json` 和 `platform/result.json` 记录各断言。设备能力回归使用 `dev.cuttlefish.cfctl.fixture`，结束时卸载测试包。相机测试只读取图像尺寸和亮度平面的字节数。

| 项目 | 已验证的操作 | 验证范围 |
| --- | --- | --- |
| 图形 | 向 EGL 离屏表面绘制两层不同深度的三角形，读回中心像素 RGB 为 0、255、0 | 来宾 SwiftShader 软件渲染 |
| 音频 | 向 AudioTrack 写入 16000 帧静音 PCM，播放游标到达第 16000 帧 | Android 播放路径 |
| 相机 | 使用 Camera2 从相机 0 获取一帧 160 × 120 的 YUV 图像 | Android 来宾相机 |
| Wi-Fi、蓝牙 | 切换适配器状态并恢复初始值 | 两端的适配器状态读回 |
| Wi-Fi 数据 | 关联开放的虚拟 AP `VirtWifi`，在 `wlan0` 上向宿主发送 64 字节请求并逐字节核对 64 字节响应 | Android 应用到本地宿主的 TCP 双向传输，使用 QEMU SLIRP 网卡 |
| 蜂窝网络 | 模拟 SIM 为 `LOADED`，语音和数据均注册为 `IN_SERVICE` | 注册状态；实际通话和数据传输待验收 |
| Fastboot | 在独立、无磁盘的配套 U-Boot 上，用 Fastboot/UDP 读取 `is-userspace: no` | 单独运行 `test_fastboot.py`；刷写、解锁及 fastbootd 均在此用例范围之外 |

构建 14781626 的来宾 init 读取 `ro.boot.wifi_impl` 来启动 `setup_wifi`。Darwin 原配置提供了 `ro.boot.vendor.wifi_impl`，HAL 日志报告 `wlan0 (No such device)`。`cf.sh start` 现在同时传入 `androidboot.wifi_impl=virt_wifi`。重启后出现 `wlan0`，回归可重复启用和恢复 Wi-Fi。

硬件任务在 Linux/KVM 启动专用实例时启用 `CF_KVM_VIRT_WIFI=1`。该配置给来宾设置 `androidboot.wifi_impl=virt_wifi`，使用第三张 SLIRP 网卡提供 `VirtWifi`。普通 `device start` 仍使用原来的无线后端。单独复跑时，在实例已停止的情况下执行 `CF_KVM_VIRT_WIFI=1 python3 -B cfctl.py --device linux-1 device start`，然后执行 `python3 -B tests/platform_probe.py --device linux-1 --apk "$CF_FIXTURE_APK" --out "$OUT" --wifi-data`。Darwin 将设备名改为 `darwin-1`，使用现有的 SLIRP 启动配置。测试 APK 须从当前 `tests/apk` 重新构建，两端使用同一个文件。

收发测试在宿主的 `127.0.0.1` 随机端口监听，来宾通过 SLIRP 网关 `10.0.2.2` 访问。测试应用使用 Android Wi-Fi Network 的 SocketFactory，核对套接字源地址与 `wlan0` 的 IPv4 地址。测试结束时清理本次新增的网络配置，并核对已保存网络列表、关联状态和 Wi-Fi 开关。结果位于 `platform/result.json` 的 `checks.wifi_data`。该用例覆盖本地连通性，未测吞吐、物理 AP 或公网访问。

Linux 的 Recovery 和 Fastboot 可独立复验。前者只使用内核与 ramdisk，后者只使用 U-Boot；两者均无需挂载 Android 用户盘。Darwin 独立 U-Boot 的 Fastboot/TCP 试验中，来宾显示监听并回复 ARP，请求未完成 TCP 握手，原因未查明。Linux 的 `CF_TEST_WORKDIR` 须可写，`QEMU_BINARY_DIR` 指向宿主包内的 QEMU。

```sh
export QEMU_BINARY_DIR="$CF_RUN/bin/aarch64-linux-gnu/qemu"
CF_TEST_WORKDIR="$HOME" GUEST_ZIP="$GUEST_ZIP" UNPACK_BOOTIMG="$CF_RUN/bin/unpack_bootimg.py" \
  python3 -B -m unittest discover -s tests -p test_recovery.py -v
CF_TEST_FASTBOOT=1 python3 -B -m unittest discover -s tests -p test_fastboot.py -v
```

手动硬件工作流在默认分支运行同一 `host_acceptance.py` 入口。每个作业需要带 `self-hosted`、`ARM64`、`cuttlefish-lab` 和操作系统标签的专用 runner。runner 环境提供 `CF_LAB`（外置测试资源目录的绝对路径）、`CF_APP_APK`、`CF_TEXT_APK`、`CF_FIXTURE_APK`，以及该宿主的 `CF_RUN`、PATH 和 KVM 配置。Mac 和 Linux 须预置相同的 APK。工作流只归档每端的三个 JSON 验收报告，保留三天。比较任务核对应用断言、来宾指纹、测试包摘要、Mac 对照实例、七项设备检查和正常关机。两端的结果目录也可在本机用 `python3 -B tests/compare_hardware.py DARWIN_DIR LINUX_DIR` 比较。远端 runner 注册和实际调度尚待验收。

当前设备 ZIP 包含 11 个镜像与说明文件。本地验收输入中没有与目标构建匹配的签名 OTA 包。运行中整机快照也有宿主端限制：本次 Darwin 所用 Cuttlefish 源码的 `ServerLoopImpl::SuspendGuest` 仅处理 crosvm，选择 QEMU 时返回不支持。已验收的快照能力为关机后的直启实例磁盘保存和恢复。
