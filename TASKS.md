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
