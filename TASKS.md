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
- [ ] 远端 CI 执行、自托管 runner 注册与实际调度尚未验收。手动工作流及本地硬件入口已有本机验证记录。

## 真实 APK 与 Unicode 输入

- [x] 用 v2rayNG 2.3.14 的本地配置搜索框验证按焦点选择 EditText、输入中文及 emoji、替换、清空、拒绝非输入控件，并检查返回应用首页后的状态。
- [x] Darwin/HVF 实测通过，测试前后 original 和 recovered 的应用列表及前台窗口一致。首次使用系统返回键的失败记录包含控件树、截图和 logcat。最终流程使用应用内返回按钮并检查所属包名。
- [x] Linux ARM64/KVM 完成相同场景，QMP 报告 KVM 已启用。两端应用 APK 与输入组件的 SHA-256、Android 构建指纹及搜索断言一致，均已卸载测试包。cf-tcg 的应用列表摘要和前台窗口复核一致。两端报告分别位于 `.local-cf/real-app/darwin-release/result.json` 和 `.local-cf/real-app/linux-release/result.json`。

## 本地硬件验收与待办

- [x] 修复 Cuttlefish 冷启动依赖手动启动 ADB 的问题。cf-qa 初始 5037 无监听，启动后仅监听 127.0.0.1，重复启动、APK 搜索和正常关机通过。
- [x] 修复 14781626 的 Darwin Wi-Fi 初始化参数。原始日志显示 HAL 未找到 wlan0，修复后 init 执行 setup_wifi，六项设备能力回归通过。
- [x] 两端实测同一份测试 APK 的深度缓冲渲染、AudioTrack 静音播放、Camera2 一帧 YUV、Wi-Fi 与蓝牙状态切换恢复、模拟 SIM 注册。两端报告位于 `.local-cf/local-acceptance/job-{darwin,linux}/platform/result.json`。
- [x] 无磁盘 Recovery 在 Darwin/HVF 和 Linux/KVM 通过 ADB 核验。配套 U-Boot 在两端均通过 Fastboot/UDP 只读查询 `is-userspace: no`。独立测试日志位于 `.local-cf/local-acceptance/*-fastboot-release.log`。
- [x] 串行硬件入口在两端完成启动、真实应用回归、设备检查和正常关机。两端报告经本地比较通过；actionlint 检查两份工作流通过。Mac 全量 unittest 的 26 项零跳过，新增的第 27 项 Fastboot 在同机单独通过。
- [x] U-Boot 设置 `serverip=10.0.2.2` 后，Darwin/HVF 与 Linux/KVM 均在 SLIRP 回环转发上通过 Fastboot/TCP 和 UDP 查询。Linux 独立 TAP 的 IPv4、IPv6 TCP 也通过。原厂 Recovery 从临时 misc 分区读取 `boot-fastboot` 后，在两端返回 `is-userspace: yes`。
- [ ] Darwin Cuttlefish 的运行中整机快照尚不支持。宿主挂起路径要求 crosvm，正在运行的实例 2 的 QMP `query-migrate` 报告 virtio-sound、vhost-user-vsock、vhost-user-input 不可迁移，两个后端缺少 LOG_SHMFD。证据在 `.local-cf/radio-acceptance/qmp-migration.json`。
- [x] Darwin/HVF 与本机 Linux/KVM 在 VirtWifi 上完成本地 TCP 双向收发。APK 使用 Wi-Fi Network，双方校验独立的 64 字节随机数据并核对 wlan0 源地址。每端恢复原有 Wi-Fi 开关、关联状态和已保存网络列表。正式报告位于 `.local-cf/wifi-data/job-{darwin,linux}/platform/result.json`。Linux 验收显式选择 virt_wifi，原有 mac80211_hwsim 模式仍用于常规启动。
- [x] 修复 Darwin 宿主的 RIL 默认地址被空值覆盖的问题。修复前来宾蜂窝 IPv4 为 `255.255.255.255/31`；修复后为 `10.0.2.15/24`，Linux TAP 来宾为 `192.168.97.2/30`。两端分别通过明确绑定蜂窝 Network 的双向 TCP 校验。
- [x] 两端使用本地调制解调器控制套接字发起模拟来电。来宾经历空闲、振铃、摘机和空闲，模拟对端收到 Android 接听响应。语音媒体质量未测。
- [x] 两端通过系统设置与临时 Bumble 对端配对，在要求加密的 GATT 特征上写入并读回 16 个随机字节，随后取消配对。Wi-Fi 通过 1 MiB 双向逐字节校验、耗时采样和公网 HTTPS 204 检查。两端十项能力报告位于 `.local-cf/radio-acceptance/{darwin,linux}-verified/result.json`。
- [x] 更新串行硬件任务，比较九项离线设备检查、真实 APK、隔离和正常关机。报告位于 `.local-cf/radio-acceptance/job-{darwin,linux}/`。
- [x] 在独立 Darwin/HVF 和 Linux/KVM 实例 2 实测官方 14781626 → 14818820 A/B 更新、重启前取消更新、从 B 槽启动和快照合并。合并后普通 14781626 包返回 `kPayloadTimestampError (51)`，活动槽保持 B。两端报告位于 `.local-cf/radio-acceptance/ota-official-release/` 和 `.local-cf/radio-acceptance/linux-ota/`。
- [x] Darwin 冷恢复后实际启动 14781626 A 槽，更新前写入的测试标记一致。Linux 在 14818820 B 槽的冷恢复测试中撤销了关机前对标记的修改。两个流程均保留了恢复前的写入层。
- [x] macOS 完整实机套件运行 30 项，全部通过。随后增加的第 31 项离线回归复现默认 netsim.ini 指向旧进程的情况，验证按启动器记录选择端点、拒绝其他来宾。新版离线套件 22 项通过，9 项按硬件条件跳过。
