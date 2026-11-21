# Android 16 本机启动验证

- [x] 在本机编译 Meta QEMU 9.2.3。验证 HVF、Cocoa、slirp 和 vhost-user 均被编入。
- [x] 用 HVF 创建虚拟机并读取 QMP 状态。加载 AOSP ARM64 Cuttlefish U-Boot，串口打印版本并识别 512 MiB 内存。
- [x] Meta 的 19 项离线测试通过。在线补丁检查覆盖 35 个项目、130 个补丁。
- [x] 在仓库内创建上限 74 GiB 的区分大小写 APFS 稀疏卷。连同卷外工具和构建产物，本次实验总预算为 80 GiB。
- [x] 获取 Android CI 构建 14781626 的 ARM64 设备 ZIP，验证 11 个条目的 CRC，并记录 SHA-256。该目标的 build.prop 标明 Android 16、API 36、BP4A.251205.006。
- [x] 使用实际 ZIP 启动 Android Recovery 并执行 ADB 回归。状态为 `recovery`，来宾版本为 `16`，硬件名为 `cutf_cvm`，ABI 为 `arm64-v8a`。
- [x] Meta QEMU/HVF 加载 Cuttlefish 正常系统。Cocoa 已创建可见窗口，ADB 报告 Android 16、arm64-v8a、cutf_cvm、sys.boot_completed=1。设置首页截图为 720 × 1280。
- [x] 保存镜像组装入口和启动脚本。正常关机后用同一写入层重启，再次从 ADB 打开设置。
- [x] 完成 AOSP 的 274 项目精简同步并导出修订清单。lunch 报告 HOST_OS=darwin。
- [x] 应用并核对 35 个项目的 130 个 Meta 补丁。独立临时卷内目录的全新设备首次启动和 Recovery 两项回归通过。
- [x] 构建 Darwin 宿主包。本机 SDK 26.5 的 Soong 引导测试通过，已补全五个工作树并生成 Ninja 构建图。宿主包目标为 `out/host/darwin-x86/cvd-host_package.tar.gz`；本机的 `hosttar` 会引用没有生成规则的 `darwin-arm64` 路径。
- [x] 编译验证已通过 Rust bindgen 阶段。SwiftShader 附带的 LLVM 16 在 Darwin 上使用其 configs/darwin/include；宿主打包使用已有 GNU tar。
- [x] 使用 Darwin `launch_cvd` 启动实例。旧卷仅剩 125 MiB，复制 `super.img` 至少还需约 668 MiB，APFS 全量预分配返回 `ENOSPC`。在 AOSP 卷外的独立目录 `.local-cf/run-darwin/` 组装成功。
- [x] Darwin 实例的 ADB 报告 Android 16、`arm64-v8a` 和 `sys.boot_completed=1`。在 Cocoa 窗口中观察到设置首页。由 ADB 请求来宾关机，内核报告 `reboot: Power down`，QMP 报告 `guest-shutdown`，随后停止宿主进程并确认端口 6520 关闭。
- [x] 回收已关机实例的组合磁盘全零区块，SHA-256 校验一致。运行目录实际占用从约 23.6 GiB 降至约 8.8 GiB，复用该目录重启后再次通过 ADB 和设置页验证。验收记录见 `.local-cf/logs/darwin-*.log`，Cocoa 截图为 `.local-cf/logs/darwin-cocoa-settings.png`。

构建产物、源码和日志位于 `.local-cf/`。AOSP 卷的挂载点为 `.local-cf/aosp-volume/`。查看同步日志用 `.local-cf/logs/repo-sparse.log`，查看卷内剩余空间用 `df -h .local-cf/aosp-volume`。

## Android 实例 CLI

- [x] 按名称选择直启实例，分别报告 QEMU、ADB 和系统启动状态。
- [x] 支持单个和拆分 APK 安装、保留数据升级、启动、应用列表和卸载。
- [x] 支持输入、截图、日志、shell，并用测试 APK 核对恢复副本的应用列表和前台窗口。
- [x] 支持按控件查询、等待、点击和替换文本。独立测试实例已验证 QMP 运行状态、暂停和块设备读错误。
- [x] P1 验证同一 UI 流程在两个独立实例上运行。超时可下载控件树和截图；断开所选实例的 ADB 后仍能查询 QMP 和串口。
- [x] P2 提供经过 Bearer token 认证的本机 HTTP 和 WebSocket 命令接口，复用 CLI 的实例路由和执行逻辑。HTTP 支持上传 APK、下载截图和诊断文件。
- [x] P2 提供干净克隆、关机后保留应用数据的克隆、磁盘快照保存与恢复。两个临时副本并行运行，各自使用独立的 ADB 端口和写入层。测试结束后可删除快照和已关机的克隆。

## Cuttlefish 编排与跨宿主对照

- [x] 核对 Darwin 运行配置、实例注册记录、进程树、ADB 监听者和设备序列号。
- [x] 生成一次性共享测试包，固定 Android CI 14781626 的设备 ZIP 和四个 APK 的 SHA-256。
- [x] 用相同命令验证 Darwin 两次正常启停、应用升级、UI、对照实例隔离和重启后的数据。
- [x] 在 Apple Silicon 上部署 Debian ARM64 节点，验证 KVM API 12、QEMU/KVM、内外层独立 vsock CID 和 ADB。嵌套 KVM 首启时 `system_server` 退出，该测试节点已正常关机。
- [x] 同一 Linux 宿主包通过 QEMU/TCG 启动 Android 16，验证正常关机、仓库 CLI 重启和来宾超时倍率 50。
- [x] Darwin/HVF 与 Linux ARM64/TCG 使用同一设备 ZIP 和四个 APK。两端的构建指纹和六项应用断言一致，均已验证测试包卸载。报告分别位于 `.local-cf/parity/darwin-matched/result.json` 和 `.local-cf/parity/linux/result.json`。

## 遗留问题复验

- [x] 核对两台直启实例的退出记录。00:14:23 和 00:14:33 的 Cocoa `terminate:` 均收到 `NSTerminateNow`。退出请求的发起方仍未确定。两台实例重新启动后，08:21 的进程检查显示各自 PID 已持续存在超过 8 小时。
- [x] 在独立实例上验证 Cocoa、无窗口启动、设置页、截图、上一次串口日志保留和正常关机。双实例 PID、ADB、串口和 QMP 的 120 秒定时采样通过。
- [x] 在独立 Linux 运行目录复验嵌套 KVM 首次启动。使用 2 MiB hugetlbfs 映射 3072 MiB 来宾 RAM 后，同一 CI 镜像完成首启、六项应用回归和正常关机。仓库 CLI 重启后连续三轮对照通过，报告在 `.local-cf/residual/kvm-release*.json`。
- [x] 添加 macOS/Linux 离线测试工作流，并在两种宿主本地运行同一测试命令。Mac 启用各项实机条件后的 22 项回归全部通过，记录位于 `.local-cf/residual/macos-full.log`。
- [ ] 远端 CI 运行和硬件端到端任务调度尚未验收。当前提交仅保留在本地。
