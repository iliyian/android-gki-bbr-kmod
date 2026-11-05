# K80 手机端 BBRv3 移植 Journal

- 日期：2026-11-04
- 设备：Redmi K80（zorn，24117RK2CC）
- 系统：HyperOS 3 `OS3.0.309.0.WOKCNXM` / Android 16
- 内核：`6.1.138-android14-11-g965475777129-mi`，KMI `android14-6.1`，A/B，bootloader 已解锁
- Root：SukiSU Ultra 4.3.0 LKM，patch `init_boot_a`；`boot_a` 为原厂
- 现状：手机端 BBRv1 已通过 `tcp_bbr.ko` + `ksud insmod` 启用；服务端 BBRv3 已完成。目标只剩手机端真 BBRv3。

---

## 1. TL;DR

1. **BBRv3 不能像 BBRv1 那样只做一个 `tcp_bbr.ko`。** 它不是单个算法文件，而是“算法 + TCP 核心栈补丁”的组合。
2. **模块路线已排除。** 即使补上 PLB/ecn_low/BPF，仍卡在 `rate_sample.tx_in_flight/lost`、`CA_EVENT_TLP_RECOVERY`、`ca_ops->skb_marked_lost`、`ICSK_CA_PRIV_SIZE 104→144` 等核心改动上；模块改不了这些结构体/调用点。
3. **Droidspaces 6.1.176 两个 build 都已实测闪屏。** nbkernel 736 条 `COMMIT failed: 11`/`presentFence:-1`，non-nbkernel 757 条。直接刷 zip 也是同一个内核，不用再试。
4. **闪屏根因不是 config/color mode/缺 Xiaomi 驱动。** `msm_drm` 是 vendor 模块；6.1.176 的 msm/DPU 行为与 6.1.138 vendor HWC 不匹配，DRM atomic commit 返回 `-EAGAIN`。**6.1.138 内核是对症的。**
5. **Google 官方 GKI 6.1.138 实测：显示修好了，但系统卡在 boot animation。** 根因是 audio HAL/AudioService：system_server main 线程卡在 `AudioManager.updateAudioPortCache` → `AudioPortEventHandler` → HIDL `openPrimaryDevice_7_1`；audioserver 反复死亡；`boot_completed` 永远不置 1。ADSP firmware 实际加载成功，不是 firmware 问题。
6. **当前正在 `ma` 上编 ACK 6.1.138 + 设备 `/proc/config.gz`**，重点补上 stock 有而 Google GKI `gki_defconfig` 没有的 `SND_SOC_COMPRESS`、`SND_SOC_TOPOLOGY` 等 audio 配置，验证 audio 卡住是不是 config 问题。
7. 如果 ACK device-config 仍卡 audio，则转 **Allay-OS 6.1.138 vendor 树**（zorn 特化）或等小米 6.1.138-mi 源码。
8. **2026-11-04 追加**：ShirkNeko 6.1.124 GKI 实测可开机，显示/audio/WiFi/相机/指纹/root 均正常；6.1.145 可开机、audio 正常但显示持续闪屏；Shirk 6.1.138 构建编译成功但卡 Redmi logo，pstore 无日志，疑似 SUSFS `fs/namespace.c` patch 不完整。详见第 14 节。
9. **2026-11-04 最终基线**：ACK/Google GKI **6.1.138** + `CONFIG_MODULE_SIG_PROTECT=n`、LTO_NONE、gki_defconfig，`fastboot boot` 实测显示/audio/相机/指纹/WiFi/5G/KSU root/BBR 全部正常，`boot_completed=1`。**audio 卡死根因确认为 GKI module protection**。~~6.1.138 是 zorn 的显示兼容上限~~ —— 该结论当时没有中间版本证据，已被 2026-11-05 的实验推翻；真正边界是 stable 6.1.140/6.1.141，详见第 20 节。
10. **2026-11-04 BBRv3 移植成功**：以 Google `google/bbr` v3（6.13.7）为唯一参考，自研回移到 ACK 6.1.138；`bbr3` 注册成功、可切换，开机/音频/模块加载正常。核心障碍不是算法，而是 genksyms CRC 误伤导致 vendor 模块拒绝加载；已用 `check_version()` 放行解决。详见第 19 节。
11. **2026-11-05 6.1.176 显示闪屏根因定位**：纯 ACK 6.1.176 能开机，模块/audio/root 正常，但显示持续闪屏。根因是 stable **6.1.141** 引入的 `drm_atomic_check_valid_clones()`（上游 `41b4b11da0215`）与 zorn vendor `msm_drm.ko`（6.1.68 编）的 DSI+Virtual encoder `possible_clones` 不兼容：`drm_atomic_check_only()` 对 CRTC 203 返回 `-EINVAL`，HWC 每帧重试失败。回退该检查后 6.1.176 显示恢复正常，用户确认视觉正常。详见第 20 节。
12. **2026-11-05 6.1.176 双 BBR + 默认 bbr3/fq**：在同一 6.1.176 基线上同时编入 BBRv1（`bbr`）和 BBRv3（`bbr3`），默认 `CONFIG_DEFAULT_TCP_CONG="bbr3"`、`CONFIG_DEFAULT_NET_SCH="fq"`。真机验证 `tcp_available=reno bbr bbr3 cubic`、默认 `bbr3`、`default_qdisc=fq`；`bbr`/`bbr3` 均可切换并实际跑通 HTTP；模块/audio/root/显示正常。详见第 21 节。
13. **2026-11-05 6.1.176 ThinLTO**：ThinLTO 构建并刷入验证，`-j8` 构建 24:44.91，cgroup 峰值 6.38 GB，无 OOM；`vmlinux.symvers` 与 LTO_NONE 全量 diff 为 0，模块 ABI 不变。开机/显示/audio/模块/BBR 正常，`boot_progress_enable_screen` 单次 15871 ms（LTO_NONE 基线 16022 ms），性能 A/B 未完成。详见第 22 节。

---

## 2. 背景与约束

### 2.1 已有 BBRv1 模块

- 源码：ACK `android14-6.1.138_r00`（commit `4894546`）
- 编译器：AOSP clang r487747c（17.0.2）
- 关键点：
  - ACK 的 `struct sock/tcp_sock/module` 含 `android_kabi_reserved*`、`sk_wait_pending`、`btf_data` 等字段，不能用 kernel.org stable。
  - `.config` 必须 `CONFIG_DEBUG_INFO_BTF{,_MODULES}=y`，否则 `struct module` 少 16 字节，`ksud insmod` 读错 `mod->init` 会重启。
  - 普通 `insmod` 不行（vermagic/CRC + `tcp_rate_skb_sent/delivered` 未导出）；`ksud insmod` 用 kallsyms 重写未定义符号。
- KSU 模块：`/data/adb/modules/tcp_bbr_kmod/`；开机 `post-fs-data` `ksud insmod`；`service.sh` 设 `bbr` + `fq`；30 秒 watchdog 防 netd 重置。
- 回退：`sysctl -w net.ipv4.tcp_congestion_control=cubic`；禁用/删除 KSU 模块后重启。

### 2.2 BBRv3 的现状

- 截至本次记录，BBRv3 **仍未进 mainline**（Linux 7.4-rc1 也没有）。
- Android ACK `android14-6.1`、`android15-6.6`、`android16-6.12` 都没有 BBRv3。
- 官方参考实现：`google/bbr` 的 `v3` 分支，基于 Linux 6.13.7。
- 第三方 Android 适配：`ahmed-alnassif/GKID-Kernels` 的 20-patch 系列；`byJoey/Actions-bbr-v3` 是 VPS 内核。

---

## 3. 为什么模块路线不可行

BBRv3 依赖以下核心 TCP 栈改动，模块无法完成：

1. **`struct rate_sample` 新字段**
   - `prior_lost`、`tx_in_flight`、`lost`、`is_acking_tlp_retrans_seq`
   - 必须由 `tcp_rate.c`/`tcp_input.c` 填充；模块拿到的 `rs` 是核心构造的，不能扩展。
2. **`struct tcp_skb_cb.tx` 新字段**
   - v3 需要把 `first_tx_mstamp/delivered_mstamp` 从 u64 缩成 u32，腾出 `in_flight`/`lost`。
   - 这是 `sk_buff` cb 布局，模块改不了；核心也不会填。
3. **`ca_ops->skb_marked_lost`**
   - 6.1 的 `tcp_congestion_ops` 没这个成员，`tcp_input.c` 也不会调用；v3 的 loss-probe/upper-bound 依赖它。
4. **`CA_EVENT_TLP_RECOVERY`**
   - 6.1 的 `enum tcp_ca_event` 没有，核心不会发。
5. **`ICSK_CA_PRIV_SIZE` 104 → 144**
   - v3 的 `struct bbr` 是 144 字节（含 `tcp_plb_state`），6.1 的 `icsk_ca_priv` 只有 104 字节。
   - 模块写 144 字节会踩坏相邻内存；动态分配只能解决“存得下”，解决不了 1–4。
6. **PLB / ecn_low / fast_ack_mode**
   - 也是 6.9/6.13 核心特性；stub 掉会改变行为。

### 3.1 实际编译验证

把 `google/bbr` v3 的 `tcp_bbr.c` 放到 ACK 6.1.138 模块环境：

- 第一次：`tcp_dctcp.h` 缺失（可从 ACK `net/ipv4/tcp_dctcp.h` 复制）。
- 补 PLB/ecn_low/BPF kfunc 后仍报：

```
use of undeclared identifier 'CA_EVENT_TLP_RECOVERY'
no member named 'lost' in 'struct rate_sample'
no member named 'tx_in_flight' in 'struct rate_sample'
call to undeclared function 'tcp_skb_tx_in_flight_is_suspicious'
no member named 'is_acking_tlp_retrans_seq' in 'struct rate_sample'
```

结论：`ksud` 能解决“符号没导出”，解决不了“结构体没字段/核心不填/核心不调用”。

### 3.2 核心补丁可移植性

对 ACK 6.1.138 实测 GKID 20-patch 系列：

- 03–15：可顺序干净应用；
- 16：`tcp_process_tlp_ack` 调用点一处上下文冲突；
- 18：`tcp_bbr.c` 建议整文件替换；
- 19：小补丁；
- PLB（01–02）和 ecn_low（17/20）可裁掉。

这证明全内核移植可行，不是模块可行。

---

## 4. Droidspaces / ShirkNeko 实测

### 4.1 已有产物

| 文件 | 内容 |
|---|---|
| `dl/new-boot-repacked.img` | 原厂 boot + ShirkNeko 6.1.145 kernel（SHA `40b6e580...`） |
| `dl/shirk-6.1.145-boot.img` | ShirkNeko 原始 boot（64MB） |
| `dl/droidspaces-repacked.img` | 原厂 boot + Droidspaces 6.1.176 nbkernel kernel（SHA `629f709c...`） |
| `dl/boot_a_current.img` | 当前原厂 boot_a 备份（SHA `a1a041eb...`，与 stock 一致） |
| `backup_init_boot_a_lkm.img` | SukiSU LKM 版 init_boot 备份 |

原厂 boot：

- 大小 `100,663,296` 字节；
- header v4，`kernel_size=36,481,536`；
- **`ramdisk_size=0`、cmdline 为空**；
- 所以 kernel-swap 只需替换 offset `0x1000` 起的 kernel 段并改 `kernel_size`。

### 4.2 闪屏证据

- `logs/logcat_newkernel.txt`（22:36）：810 条 `COMMIT failed`/`presentFence`（Shirk 6.1.145）。
- `logs/logcat_droid.txt`（22:46）：736 条（Droidspaces nbkernel 6.1.176）。
- `logs/logcat_droid_nonb.txt`（23:23）：757 条（Droidspaces non-nbkernel 6.1.176）。

2026-11-03 用 `fastboot boot` 零写入测试 non-nbkernel：

- kernel：`6.1.176-android14-11-g0b76321acf29-dirty-ab14529422`
- `boot_completed=1`
- root/BBRv1 仍在
- logcat：757 条 `SDM: COMMIT failed: 11` + `HwcComposer: presentFence:-1`
- 测完已重启回 stock，`boot_a` 未动。

### 4.3 直刷包确认

| zip | 内含 kernel SHA | 说明 |
|---|---|---|
| `droid-test/AnyKernel3_zorn_droidspaces.zip` | `e7f871d5...` | non-nbkernel build，未单独验证；直刷结果应与 kernel-swap 一致 |
| `zorn_build/droidspaces_ak3.zip` | `629f709c...` | nbkernel build，已实测闪屏 |

“直刷”和“kernel-swap”不是两套内核方案；决定闪不闪的是 zip 里的 `Image`。

---

## 5. 显示根因分析

### 5.1 已排除

- **不是 config**：stock vs Droidspaces 显示相关配置真正有值差异的只有 `CONFIG_MSM_HAB_DEFAULT_VMID`，与显示无关。
- **不是 color mode**：stock 和 custom 的 `mSupportedColorModes=[0, 7, 9]`、`NATIVE(0)/SRGB(7)/DISPLAY_P3(9)` 完全一致。
- **不是缺 Xiaomi 显示驱动**：custom dmesg 里 `mi_disp`、`msm_drm`、`mi_dsi_panel_parse_config`、`[xiaomi o11 42 02 0a cmd mode dsc dsi panel]` 都正常初始化。

### 5.2 真正差异

- stock：`COMMIT failed`/`presentFence` = **0**。
- custom：几百条，`COMMIT failed: 11` = `-EAGAIN`。
- `msm_drm` 不是内核内置，而是 **vendor 模块**：

```
/proc/modules:
msm_drm 3149824 42 - Live ...
```

它从 `/vendor/lib/modules` 加载，按 **6.1.138** 编译。

### 5.3 源码 diff

对比 `Allay-OS` 6.1.138 与 `cnmrlin` 6.1.176：

- `include/uapi/drm/msm_drm.h`：**完全一致**；
- `drivers/xiaomi/`：**完全一致**；
- `drivers/gpu/drm/msm/` 有一批文件不同：`dpu_encoder_phys_cmd.c`、`dpu_kms.c`、`dsi_host.c`、`msm_gem.c`、`adreno/*` 等；
- `drm_atomic_helper.c` 6.1.176 多了一个 `drm_atomic_check_valid_clones()` 检查。

结论：**6.1.176 的 msm/DPU 驱动行为与 6.1.138 vendor HWC 契约不一致**，HWC 的 atomic commit 拿到 `-EAGAIN`。换 6.1.138 内核是对症的。

---

## 6. Google GKI 6.1.138 实验

### 6.1 来源

- Google certified GKI：
  - `https://dl.google.com/android/gki/gki-certified-boot-android14-6.1-2025-06_r2.zip`
  - zip 14MB，内含 `boot-6.1.img` 64MB。
- 解包 kernel：
  - `6.1.138-android14-11-g109284bd58d4-ab13636361`
  - 与 ACK 树 commit `109284bd5` 同源。
- 用 `magiskboot` 把 kernel 换进原厂 boot（96MB，ramdisk_size=0），`fastboot boot` 零写入。

### 6.2 显示：成功

- HyperOS 开机动画正常显示；
- `msm_drm` 加载；
- logcat `COMMIT failed`/`presentFence` 约 0–1 条；
- `dumpsys SurfaceFlinger` color modes 正常 `[0,7,9]`；
- 截图：`logs/gki138-screen.png`。

**6.1.138 内核 + 原厂 6.1.138 `msm_drm.ko`，显示问题解决。**

### 6.3 启动：卡在 audio

- `boot_completed` 永远为空；
- `init.svc.bootanim=running`；
- system_server 被 watchdog 反复重启（ANR trace：`anr_2026-11-03_23-59-11-379`）；
- `main` 线程 stack：

```
"main" prio=5 tid=1 Blocked
  at android.media.AudioManager.updateAudioPortCache(AudioManager.java:8391)
  - waiting to lock <...> held by thread 179
  at ... AudioManager.listAudioDevicePorts(...)
  at ... AudioManager.registerAudioDeviceCallback(...)
  at com.android.server.audio.AudioGameEffect.<init>(...)
  at com.android.server.audio.AudioServiceStubImpl.startAudioGameEffect(...)
  at com.android.server.audio.AudioService.<init>(...)
  at SystemServer.startOtherServices(...)
```

- thread 179 `AudioPortEventHandler` 卡在：

```
AudioSystem.listAudioPorts
  -> HIDL openPrimaryDevice_7_1
```

- audioserver stack：

```
AudioFlinger::loadHwModule_ll
  -> DevicesFactoryHalHidl::openDevice
  -> BpHwDevicesFactory::openPrimaryDevice_7_1
```

- logcat：`AudioSystem-JNI ... -32`、`AS.AudioService: Audioserver died` 反复出现；
- `audioshell_system: waitForAudioPolicyService ... retry = 290`。

### 6.4 ADSP firmware 不是原因

- GKI dmesg 实际有：

```
ueventd: found /vendor/firmware_mnt/image/adsp.mdt
remoteproc ... Booting fw image adsp.mdt, size 8204
```

- stock 也有同样的 `-13`/`-2` 和 sysfs fallback；两边 ADSP firmware 都加载成功。
- 所以不是 ADSP/firmware 问题。

### 6.5 根因判断

- Google GKI 预编译用的是 `gki_defconfig`，不是设备 config；
- stock config 有而 Google GKI 没有的关键 audio 配置：
  - `CONFIG_SND_SOC_COMPRESS=y`
  - `CONFIG_SND_SOC_TOPOLOGY=y`
  - `CONFIG_SND_SOC_I2C_AND_SPI=y`
  - 以及一批 QCOM audio/Q6V5/GPR/SPF 相关配置；
- 结果：zorn 原厂 audio HAL 在纯 GKI + 缺配置的内核上卡住，audioserver 死亡，system_server 初始化 AudioService 时死锁，boot 永远不完成。
- 另有 `cnd`（WiFi QoS）Scudo invalid chunk state 崩溃，属于另一条 vendor userspace 问题，不是 boot 卡死主因。

### 6.6 恢复

- 已重启回 stock：`6.1.138-android14-11-g965475777129-mi`；
- `boot_completed=1`；
- `boot_a` 未动，数据安全。

---

## 7. 候选方案对比

| 方案 | 说明 | 结论 |
|---|---|---|
| Droidspaces 6.1.176 | 容器 GKI，非 zorn vendor | 已实测闪屏，排除 |
| ShirkNeko 6.1.145 | 通用 GKI + SukiSU/SUSFS/KPM | 已实测闪屏，排除 |
| Google GKI 6.1.138 | 纯 ACK，`gki_defconfig` | 显示成功，audio 卡死；需 device config |
| Emqo/GKI_SukiSU_6.1 | 6.1.138 GKI，内置 SukiSU/SUSFS/KPM，面向 houji | 第三方/ houji localversion/纯 GKI，audio 大概率同样卡；非首选 |
| Allay-OS 6.1.138 | zorn 特化 vendor 树，带 Xiaomi 驱动/模块 | 首选 vendor-tree 方案；构建复杂 |
| LineageOS `android_kernel_qcom_sm8650` | 仅 `lineage-22.2`，6.1.177，无 zorn config | 不适配，排除 |
| 小米 OSS `bsp-zorn-v-oss` | 6.1.68 / Android 15 | 太旧，只能当参考 |
| 小米 6.1.138-mi 源码 | 未公开 | 最理想，等/GPL 申请 |

---

## 8. 当前构建：ACK 6.1.138 + 设备 config（进行中）

### 8.1 环境

- 服务器 `ma.iliyian.com`：aarch64 Ubuntu 26.04，10 核，15GB RAM，235GB 空闲
- 已装：`clang-17.0.6`、`lld-17`、`pahole 1.31`、`bison/flex/libssl-dev/libelf-dev` 等
- 源码：`/root/ack138-server`，tag `android14-6.1-2025-06_r2`，commit `109284bd5`
- 设备 config：`/root/zorn.config`（来自手机 `/proc/config.gz`）
- 输出：`/root/gki-build`

### 8.2 配置结果

`olddefconfig` 成功，关键项：

```
CONFIG_SND_SOC_COMPRESS=y
CONFIG_SND_SOC_TOPOLOGY=y
CONFIG_CFI_CLANG=y
CONFIG_DEBUG_INFO_BTF=y
CONFIG_LOCALVERSION=""
```

### 8.3 构建中遇到的问题

1. **`abi_symbollist.raw` 缺失**
   - 报错：`ERROR: '/root/ack138-server/abi_symbollist.raw' whitelist file not found`
   - 原因：standalone ACK 没有 Android 构建系统生成的 GKI symbol list。
   - 处理：`scripts/config -d TRIM_UNUSED_KSYMS`。
   - 影响：保留所有导出符号，内核略大；不影响功能/性能/KMI/CRC；vendor 模块更容易加载。

2. **host `libbpf` const 警告**
   - 报错：`tools/lib/bpf/libbpf.c:10746: assigning to 'char *' from 'const char *' discards qualifiers [-Werror]`
   - 原因：clang-17 对 `strchr` const 更严格；只影响 host `resolve_btfids` 工具。
   - 处理：`sed -i '10746s/next_path = strchr/next_path = (char *)strchr/' tools/lib/bpf/libbpf.c`。

### 8.4 状态

- `make ARCH=arm64 LLVM=1 O=/root/gki-build -j8 Image` 已完成；
- 产物：`/root/gki-build/arch/arm64/boot/Image`；
- 大小 `36,936,192` 字节，SHA256 `fc963cb1ac984826f5dea9fd576f16305229492cbb2b34b71ba50ccb003dff36`；
- 版本：`6.1.138-dirty`（Ubuntu clang 17.0.6 / LLD 17.0.6）。

### 8.5 结果（2026-11-04 01:00）

- kernel-swap 进原厂 boot，`fastboot boot` 成功；
- `msm_drm` 加载，display errors **0**，显示正常；
- 但 `boot_completed` 仍为空，`bootanim=running`；
- `waitForAudioPolicyService Waiting for media.audio_policy retry = 47 → 73+`，audio HAL 仍然卡住；
- 结论：**device config 的 `SND_SOC_COMPRESS/SND_SOC_TOPOLOGY` 没有解决 audio**。这不是单纯 config 问题，而是 Xiaomi kernel/vendor HAL 依赖。
- 手机已回 stock `6.1.138-android14-11-g965475777129-mi`，`boot_completed=1`。

**纯 ACK + device config 这条路基本到顶了；下一步转 Allay 6.1.138 vendor 树或等小米 6.1.138-mi 源码。**

---

## 9. 风险与概率（主观估计）

| 目标 | 概率 |
|---|---|
| ACK 6.1.138 + device config 能进桌面 | 45–60% |
| 若 ACK 不行，Allay 6.1.138 vendor 树能开机 | 65–80% |
| 拿到可日常用的 6.1.138 基线（二选一） | 75–85% |
| 在可开机基线上加 BBRv3 编译/启动成功 | 75–85% |
| 手机端真 BBRv3 且日常可用 | 55–70% |
| 有小米 6.1.138-mi 源码时 | >90% |
| BBRv3 有明显上传性能收益 | 20–40%（下载仍由服务端决定） |

---

## 10. 下一步

1. 等 ACK `Image` 编完，压缩传到 PC。
2. `magiskboot` kernel-swap 进原厂 boot，`fastboot boot` 零写入。
3. 检查：
   - `uname -r`
   - `boot_completed`
   - `logcat | grep -E "COMMIT failed|presentFence|Audioserver died|openPrimaryDevice"`
   - WiFi/蜂窝/相机/音频/触摸/指纹/充电（先粗测，能进桌面再说）。
4. 如果进桌面：
   - 在 ACK 树上加 BBRv3 核心补丁（03–16、18–19，裁掉 PLB/ecn_low）；
   - 处理 `ICSK_CA_PRIV_SIZE`：动态分配 `struct bbr`，`icsk_ca_priv` 只放指针，避免移动 `tcp_sock` 字段；
   - 注册名用 `bbr3`，保留 BBRv1 做 A/B；
   - 重编、kernel-swap、`fastboot boot`，验证 `tcp_available_congestion_control` 有 `bbr3`、`ss -tin` 显示 `bbr3`。
5. 如果 audio 还卡：
   - 转 Allay 6.1.138 vendor 树（zorn 特化）；
   - 或等/申请小米 6.1.138-mi 源码。
6. OTA 前先 disable 模块/恢复 stock boot；升级后重编。

---

## 11. 关键文件索引

### 本地 PC

- `C:\Users\iliyian\gki-research\BBRV3_PHONE_JOURNAL.md`（本文）
- `C:\Users\iliyian\gki-research\android-gki-bbr-kmod\`（BBRv1 模块源码/文档）
- `C:\Users\iliyian\gki-research\dl\`
  - `boot_a_current.img`（原厂 boot_a 备份）
  - `droidspaces-repacked.img`（nbkernel 测试镜像）
  - `droidspaces-nonb-kernel-swap.img`（non-nbkernel 测试镜像）
  - `gki-6.1.138-boot.img`（Google GKI 测试 boot）
  - `gki-6.1.138-Image`（Google GKI 裸 kernel）
- `C:\Users\iliyian\gki-research\logs\`
  - `logcat_droid.txt`、`logcat_droid_nonb.txt`、`logcat_newkernel.txt`
  - `diag_stock/`、`diag_custom/`（dmesg/SurfaceFlinger/display/drm_modes）
  - `gki138-dmesg.txt`、`gki138-all.txt`、`gki138-anr.txt`、`gki138-screen.png`
- `C:\Users\iliyian\Downloads\zorn_images_OS3.0.309.0.WOKCNXM_...\images\`
  - `boot.img`、`init_boot.img`、`vbmeta.img`、`vendor_boot.img`

### 服务器 `ma`

- `/root/ack138-server`（ACK 6.1.138，commit `109284bd5`）
- `/root/gki-build`（构建目录）
- `/root/zorn.config`（设备 config）
- `/root/gki-build.log`（构建日志）
- `/root/allay138`（Allay 6.1.138 vendor 树，已 clone）
- `/root/cnmrlin176`（cnmrlin/Lineage 6.1.176，已 clone）

### 手机

- `/data/local/tmp/dsrepack/`：`magiskboot`、stock boot、repack 工具
- `/data/local/tmp/gki138/`：Google GKI kernel
- `/data/anr/`：system_server WDT/ANR trace
- `/vendor/firmware_mnt/image/adsp.mdt`：ADSP firmware（已确认加载成功）

---

## 12. 开放问题

1. ACK device config 编译出的 audio 是否正常？`SND_SOC_COMPRESS/TOPOLOGY` 是否足够？
2. 如果 audio 还卡，是缺 Xiaomi kernel 代码补丁，还是 vendor HAL 与纯 ACK 的行为不兼容？
3. Allay 6.1.138 vendor 树构建复杂度/能否用 plain make 出 Image？
4. BBRv3 的 `icsk_ca_priv` 动态分配方案在 GKI 上是否稳定？
5. 手机端 BBRv3 在蜂窝上传方向是否真的有收益？现有数据里 BBRv1 上传不如 cubic。
6. 小米是否会发布 6.1.138-mi 源码；可走 GPL 源码申请。

---

## 13. 安全/信任备注

- 第三方预编译内核（Droidspaces/Emqo/Shirk）存在供应链风险：内核 ring 0，预编译二进制未必等于源码。
- Google certified GKI 来源可信，但仍是 `gki_defconfig`，不代表设备 config。
- stock/ACK `CONFIG_MODULE_SIG_FORCE` 未开；`MODVERSIONS` symbol CRC 才是真正的兼容检查。
- 自定义内核会使 AVB 失效；OTA 会覆盖 boot，升级后需重刷/重编。
- 测试自定义内核时不要在主力环境登录高敏感账号；保留 stock boot/init_boot 备份。

---

## 14. 2026-11-04 追加：Shirk GKI 6.1.124 / 6.1.138 / 6.1.145 实测与构建

### 14.1 结论摘要

**已验证（有实测证据）：**

- **ShirkNeko 6.1.124 generic GKI 可以正常使用**：`uname -r=6.1.124-android14-11`，`boot_completed=1`；显示最终正常，WiFi 可连（11ax/1200Mbps/WPA3-SAE），相机服务识别 8 个 device，指纹 HAL 正常，audio 设备列表和 AudioFlinger 正常。
- **ShirkNeko 6.1.145**：`uname -r=6.1.145-android14-11`，`boot_completed=1`，audio 正常；显示持续闪屏（本次 1127 条 `COMMIT failed`/`presentFence`，journal 旧测试 810 条）。
- **纯 Google GKI 6.1.124**：显示正常，但 audio 失败（`AudioFlinger: getAudioPolicyConfig Line: 4484 Failed`，`waitForAudioPolicyService` 持续重试），`boot_completed` 为空。
- **Shirk 6.1.138 构建**：编译成功，产物 `6.1.138-android14-11`、AOSP clang r487747c、ThinLTO；刷入 boot_a 后卡 Redmi logo，pstore 无日志；已刷回 stock boot 恢复。
- **手机当前状态**：stock `6.1.138-android14-11-g965475777129-mi`，`boot_completed=1`；adb shell 为 root（`u:r:ksu:s0`，SukiSU LKM）；`kernelsu`、`tcp_bbr` 已加载；`tcp_available_congestion_control=reno bbr cubic`，当前 `bbr`。

**未确认 / 仅为假设：**

- 6.1.138 卡 logo 的具体原因未确认；pstore 为空。最可疑的是 SUSFS patch 在 `fs/namespace.c` 的 `Hunk #1 FAILED at 32`（1/10 hunks 失败），但未做进一步验证。
- “Shirk 的 AOSP clang + ThinLTO 与 stock vendor 模块 CFI 匹配”是解释 Google GKI 6.1.124 audio 失败、Shirk 6.1.124 audio 成功的假设；ACK 6.1.138 + device config 同样是 ThinLTO 但 audio 仍失败，所以 LTO 不是唯一因素。
- 6.1.138 的 `-Wno-error` 只是让编译通过，不代表运行时正确；卡 logo 可能就是被吞掉的 patch 问题。
- Google GKI 6.1.134（2025-05）已下载但未做启动测试。

### 14.2 ShirkNeko 6.1.124 实测

- 来源：`ShirkNeko/GKI_KernelSU_SUSFS` release `v2.1.0`，asset `android14-6.1.124-2025-02-boot.img`，SHA256 `2898118ab12caf074a600b22df12db1ec0101f9d20eb99dfe3d2d9bb1ae48e96`。
- 用 `magiskboot` 换进 stock boot，repack 后 SHA256 `3e7f11894f1f9db4ae76a165e4d7637ce25326f5291118fad07998682d1bae9f`；已 `fastboot flash boot` 到 boot_a。
- 结果：
  - `uname -r` = `6.1.124-android14-11`
  - `sys.boot_completed=1`
  - 显示：启动早期 363 条 `COMMIT failed`/`presentFence`，最后一条在 01:56:41，之后不再刷；01:57 截图正常显示设置页。
  - WiFi：连接 SSID `40`，11ax，1200Mbps，WPA3-SAE。
  - 相机：`dumpsys media.camera` 显示 8 个 device（3 个 normal）。
  - 指纹：HAL alive，4 个已录入。
  - audio：`dumpsys audio` 有 speaker/earpiece/BLE 设备，AudioFlinger 有 AudioOut tracks；未实际放音验证。
  - 内核内置 BBR：`tcp_available_congestion_control=reno bbr cubic`，当前 `cubic`；`lsmod` 没有 `tcp_bbr`，说明 BBR 是 Shirk config 的 `CONFIG_TCP_CONG_BBR=y` 内置，不是模块。
  - 内置 KSU：管理器显示 `Built-in / 工作中`，内核完整版本 `v4.1.3-b88403d2@builtin`，管理器 `v4.3.0 (41100-6)`；因此提示“需要更新内核”。2 个超级用户、7 个模块、SUSFS `v2.1.0`。
  - 注意：在该内核上 `adb shell` 是 uid 2000（不是 root）；`/data/adb/modules` 不可读，模块状态只能从 SukiSU 管理器 UI 看。
- 结论：**6.1.124 + Shirk config 是目前唯一“显示 + audio + root + 日常功能”都正常的组合**（audio 未实际放音，但 HAL/设备正常）。

### 14.3 纯 Google GKI 6.1.124 / 6.1.134 对照

- Google certified GKI：
  - `gki-certified-boot-android14-6.1-2025-02_r2.zip` = `6.1.124-android14-11-g8e604f58ac19-ab13165110`。
  - `gki-certified-boot-android14-6.1-2025-05_r1.zip` = `6.1.134-android14-11-g8dc7d7757edd-ab13492451`。
  - 没有 6.1.137 的 certified release；2025-06_r2 是 6.1.138（journal 6.2 已测）。
- 纯 Google GKI 6.1.124 实测：显示 2 条错误、正常；audio 失败；`boot_completed` 为空。
- 从 Image 内嵌 config 提取（`IKCFG_ST`）：
  - Google 6.1.124：`CONFIG_LTO_NONE=y`、`CONFIG_CFI_CLANG=y`、`CONFIG_RFKILL=m`、`CONFIG_SND_SOC_COMPRESS=y`、`CONFIG_SND_SOC_TOPOLOGY=y`、`CONFIG_SND_SOC_QCOM` 未启用。
  - Shirk 6.1.124：`CONFIG_LTO_CLANG_THIN=y`、`CONFIG_KSU=y`、`CONFIG_KSU_SUSFS=y`、`CONFIG_TCP_CONG_BBR=y`。
  - stock 6.1.138-mi：`CONFIG_LTO_CLANG_THIN=y`、`CONFIG_CFI_CLANG=y`。
- Google vs Shirk 6.1.124 config 相关差异只有约 20 项，主要是 LTO、KSU/SUSFS、BBR；audio/QCOM/NVMEM 配置相同。
- 假设：Shirk 的 AOSP clang r487747c + ThinLTO + CFI 与 stock vendor 模块的 ABI/CFI 更匹配；Google GKI 的 LTO_NONE 和 journal 中 ACK 的 Ubuntu clang 17.0.6 不匹配。但 ACK 6.1.138 + device config 也是 ThinLTO、audio 仍失败，所以该假设不能单独解释全部现象。

### 14.4 ShirkNeko 6.1.145 实测

- 来源：Shirk release `v2.1.0`，asset `android14-6.1.145-2025-09-boot.img`；用 `magiskboot` 换进 stock boot，`fastboot boot` 零写入。
- 结果：
  - `uname -r=6.1.145-android14-11`
  - `sys.boot_completed=1`
  - 显示：1127 条 `COMMIT failed`/`presentFence`，持续闪屏；journal 旧测试 `logs/logcat_newkernel.txt` 为 810 条。
  - audio：AudioFlinger 正常，有 AudioOut tracks、音量/通话参数；未实际放音验证。
- 结论：**能开机、audio 正常、显示不可用**；不能作为日常基线。

### 14.5 ShirkNeko 6.1.138 构建与失败

- Fork：`iliyian/GKI_KernelSU_SUSFS`（fork 自 `ShirkNeko/GKI_KernelSU_SUSFS`）。
- 工作流：`.github/workflows/kernel-build.yml`；参数：
  - `android_version=android14`、`kernel_version=6.1`、`sub_level=138`、`os_patch_level=2025-06`
  - `kernelsu_commit=e67b14e6bbbbd3e67f24df658a01d3e73a64159c`（tag `v4.3.0`）
  - `use_kpm=true`、`use_zram=false`、`BBG=false`、`supp_op=false`、`set_defbbr=false`
  - `make_release=true`、`send_telegram=false`
- 构建过程问题：
  1. 第一次 run（main HEAD）被取消，改为 pin `v4.3.0`。
  2. `fs/proc/task_mmu.c` 在 6.1.138 上被 SUSFS patch 后留下未使用的 `dentry` 变量、`bypass` label、`show_vma_header_prefix_fake` function，`-Werror` 直接失败。
  3. 在 fork 的 `kernel_builder.py` 的 `apply_task_mmu_fixes` 中给 `fs/proc/Makefile` 追加：
     `CFLAGS_task_mmu.o += -Wno-error -Wno-unused-variable -Wno-unused-label -Wno-unused-function`
     之后编译通过。
  4. 第一次 `git commit` 因 GPG 签名超时失败；重试后成功（commit `47a0ba2`）。
- 最终 run `41700802010` 成功（约 28 分钟，2026-11-03 19:10–19:39 UTC）。
- 产物：artifact `kernel-android14-6.1-138`，187,918,033 字节；`android14-6.1.138-2025-06-boot.img` SHA256 `017025e5aba2804c0ac953e1caf7d2613b394fac089ef083fa1caf25aaaf73a9`。
- 内核：`Linux version 6.1.138-android14-11`，AOSP clang r487747c/17.0.2，ThinLTO；`HEADER_VER 4`、`RAMDISK_SZ 0`、`CMDLINE` 空。
- repack 进 stock boot：`gki138new-repacked.img`，SHA256 `c56090e04587ecf4b206ac2dfa6616d8d2151fc648b66c2614b88f1ca7e0b64b`。
- 刷入 boot_a 后：**卡 Redmi logo**；无 adb/fastboot；手动进 fastboot 后用 stock boot `dl/boot_a_current.img`（SHA256 `a1a041eb90226266244844c45adf650f665e930f622cc1db56afb95aba38affd`）恢复；恢复后 stock `6.1.138-mi`、`boot_completed=1`、root/BBR 正常。
- 失败日志证据：
  - 构建日志明确有 `fs/namespace.c` `Hunk #1 FAILED at 32`、`1 out of 10 hunks FAILED -- saving rejects to file fs/namespace.c.rej`。
  - 构建脚本用 `patch ... check=False`，失败没有中止。
  - `fs/namespace.c` 是 VFS/挂载核心；patch 不完整是早期卡 logo 的合理嫌疑，但**未确认**。
  - pstore `/sys/fs/pstore` 为空，没留下 panic 日志。

### 14.6 为什么 Shirk 没有 6.1.138

- Shirk 的预定义矩阵只包含 6.1 的 `78 / 90 / 99 / 124 / 145`，没有 138；release 资产里也没有 138。
- 可能原因：
  - 6.1.138 上 SUSFS/KSU patch 存在冲突（本次实测 `task_mmu.c` 警告 + `fs/namespace.c` hunk 失败）；
  - 6.1.145 已覆盖“较新 6.1”的需求，维护者没有单独验证 138；
  - 维护者只发布自己测过的组合。
- 结论：**“没有 138”更像是未验证/未覆盖，不是确认的故意行为**；但对我们来说这是一个风险信号，说明该 sublevel 的 patch 兼容性没有保障。

### 14.7 Allay / Xiaomi 6.1.68 / 预编译 zorn 仓库评估

- **Allay-OS**：
  - GitHub org `Allay-OS`，2 个公开 repo，无公开成员；`android_kernel_xiaomi_sm8650` 为 6.1.138、`lineage-23.2`、zorn/POCO F7 Pro，14 个 commit，约 2.0GB。
  - 含 `modules/qcom/opensource/audio-kernel`、`display-drivers`、`zorn.bzl`、`zorn_GKI.config`、`modules.list.msm.zorn`；**没有 zorn DTS**（`arch/arm64/boot/dts/vendor` 也不存在）。
  - star 0、fork 1（`idontknowcurrently/...`），创建于 2026-09-11，社区验证很少；用户决定降低优先级。
- **小米 6.1.68 OSS**：
  - `MiCode/Xiaomi_Kernel_OpenSource` 分支 `bsp-zorn-v-oss`，6.1.68，最后提交 2025-01-14，“REDMI K80 Android V”，基于 Qualcomm `LA.VENDOR.14.3.0.r1-14500-r1.0.r1_00042.0`，配置 `zorn_GKI.config`。
  - 公开 repo 不完整：`arch/arm64/boot/dts/vendor` 是指向 `qcom/proprietary/devicetree` 的符号链接，公开树里没有该目录；`techpack/audio` 也不在 repo；`build.config.msm.zorn` 继承 `msm-kernel/build.config.msm.pineapple`，属于多仓库工作区。
  - 结论：只能作为**参考/供体**，不是可直接 `make` 出整机的树；与当前 6.1.138-mi vendor 也不匹配。
- **预编译 zorn 仓库**：
  - `Sakura-Devices/device_xiaomi_zorn-kernel`（branch `16.2`）、`phhgsi/device_xiaomi_zorn-kernel`、`TheXPerienceProject/...` 等，包含 `kernel`、`dtb/`、`dtbo.img`、`system_dlkm/`、`vendor_dlkm/`、`Module.symvers`、`System.map`。
  - Sakura 的 `zorn_prebuilt/sakura_kernel` 版本字符串实测为 **6.1.118-android14-11**，不是 6.1.138；可作为 DTB/vendor 模块/符号参考，但来源为第三方预编译产物。
- **SukiSU Ultra**：org 只有管理器、`SukiSU_patch`、`SukiSU_KernelPatch_patch`、Website 等；release 只有 APK、`ksud`、`ksuinit`、按 KMI 分的 LKM，没有 kernel 树。K80 当前的 root 是 stock 6.1.138-mi + LKM，不是 SukiSU 自有内核。
- **WildKernels**：`WildKernels/GKI_KernelSU_SUSFS` 是通用 Google GKI（release 只有 6.1.157 等通用包），不支持 zorn；与 Droidspaces/Shirk 同类，不能解决 zorn vendor 兼容。
- **KyuharuTE/Droidspaces-Kernel-ForK80**：其 release `Image` SHA256 `629f709c...`（nbkernel）和 `e7f871d5...`（non-nbkernel）正是 journal 4.3 已测的两个 build；两者都闪屏。repo README 也声明“尚未进行真机启动或刷写验证”；我们的实测就是那次验证。

### 14.8 SukiSU / root / 模块状态

- stock 6.1.138-mi + SukiSU LKM（init_boot）：
  - `adb shell` 为 `uid=0(root)`，SELinux context `u:r:ksu:s0`；`/system/bin/su` 可用。
  - `lsmod` 可见 `kernelsu` 和 `tcp_bbr`；`/proc/modules` 同样可见。
  - BBR：`tcp_available_congestion_control=reno cubic bbr`，当前 `bbr`，`tcp_bbr` 模块已加载。
- Shirk 6.1.124（内置 KSU）：
  - `adb shell` 是 uid 2000，不是 root；`/data/adb/ksu/bin/su` 存在但 shell 无权限。
  - SukiSU 管理器显示内置 KSU `v4.1.3-b88403d2@builtin`，管理器 `v4.3.0 (41100-6)`；因此“需要更新内核”。
  - 2 个超级用户、7 个模块、SUSFS `v2.1.0 (SUSFS Inline Hook)`；NeoZygisk 显示“需要重启”。
  - 管理器可见模块（部分）：`bindhosts` v2.1.5（status: ready）、`BBR TCP Congestion Control` 6.1.138-r00（开关开，但 6.1.124 上 `lsmod` 没有 `tcp_bbr`，实际未加载；内核自带 BBR）、`Google Photos Unlimited Backup` v5（开）。其余 4 个因 `/data/adb/modules` 权限无法用 adb 读取。
- 构建脚本注意事项：
  - `--ksu-version Stable/Dev` 在 `add_kernelsu()` 里**没有被使用**；不指定 `kernelsu_commit` 时永远拉 `SukiSU-Ultra/SukiSU-Ultra` 的 `main` 分支 `kernel/setup.sh`。
  - 要固定内核侧 KSU，必须用 `--ksu-commit` / `kernelsu_commit`；管理器 APK 是独立组件，可另外升级到 41140。

### 14.9 附：安全中心 fraud_detection_data.db（与 BBRv3 无关）

- 文件：`/data/misc/user/0/securitycenter/fraud_detection_data.db`，49KB SQLite；已拉取到 `dl/securitycenter/fraud_detection_data.db` 备份。
- 表：`screenshot`（22 条时间戳，无图片）、`install_app`（15 条安装记录：包名/来源/版本/时间/内部标签，部分有 MD5）、`call_forwarding`（1 条关闭事件）、`fraud_phone`/`fraud_url`/`voip_record`/`screen_share`/`airplane_mode`（均为空）。
- 处理：已 force-stop 安全中心，将 DB truncate 为 0 字节，`chmod 000` + `chattr +i`；PC 有备份。安全中心进程仍能运行，暂未观察到持续报错。
- 同目录 `cta` 是明文 JSON 权限审计（12 个系统应用 + 敏感权限 + 时间戳）；`fire_wall_*.db` 不是 SQLite，是混淆/加密的默认防火墙规则，三份 122 字节文件 MD5 相同（`b453e5d6...`），roaming 为 10 字节；未逆出具体规则。
- 这部分是本次 session 的旁支调查，记录在此仅供完整性；不涉及内核/BBRv3。

### 14.10 当前手机状态与下一步

- 当前：stock `6.1.138-android14-11-g965475777129-mi`，`boot_completed=1`；adb root（LKM），`kernelsu`/`tcp_bbr` 已加载，BBR 当前生效；模块和 SUSFS 保持原样。
- 候选路线：
  1. **6.1.124 + KSU v4.3.0（推荐）**：用同一 Shirk 构建系统，`sub_level=124`、`os_patch=2025-02`、`kernelsu_commit=e67b14e6...`；6.1.124 已验证显示/audio/root/日常功能正常，只差 KSU 版本对齐。拿到后可作为日常基线，再 patch BBRv3。
  2. **修 6.1.138 的 namespace hunk**：复现 `fs/namespace.c.rej`，手动补 hunk #1，重编并 `fastboot boot` 验证；如果成功，可拿到更新的 6.1.138 基线。风险是还可能存在其他 6.1.138 特有 patch 冲突。
  3. **6.1.145**：能开机、audio 正常，但显示持续闪屏，不适合日常；除非先修显示。
- BBRv3 仍然放在“可开机基线之后”：
  - 先用 6.1.124 或修好的 6.1.138；
  - `struct rate_sample`/`tcp_skb_cb`/`CA_EVENT_TLP_RECOVERY`/`ca_ops->skb_marked_lost` 按 GKID 03–16、18–19；
  - `ICSK_CA_PRIV_SIZE` 优先确认去掉 PLB/ecn_low 后 `sizeof(struct bbr)` 是否 ≤104；否则动态分配 + `icsk_ca_priv` 指针；
  - 禁用旧的 `tcp_bbr.ko` 模块，避免与新 `tcp_congestion_ops` 冲突；
  - 单独分支/构建，保留基线 boot.img，A/B 测试。

### 14.11 本次新增文件

- `dl/shirk124/`：6.1.124 boot/repacked、`config-6.1.124.gz`、截图、SukiSU 管理器截图、`ui*.xml`。
- `dl/shirk145/`：6.1.145 boot/repacked。
- `dl/gki-google/`：Google GKI 2025-02（6.1.124）、2025-05（6.1.134）boot/Image、内嵌 config、repacked。
- `dl/gki138-build/`：Shirk 6.1.138 artifact、`Image-6.1.138`、`gki138new-repacked.img`。
- `dl/securitycenter/`：fraud DB 备份、`db_dump_utf8.txt`、firewall/cta 副本。
- `fork-gki/`：`iliyian/GKI_KernelSU_SUSFS` 本地 clone；构建修复 commit `47a0ba2`。
- GitHub Actions：
  - `41700802010`（6.1.138 最终成功构建）
  - `41698263884`（task_mmu 第一次失败）
  - `41695696422`（pin v4.3.0 第一次失败）
  - `41694994509`（main HEAD，已取消）

### 14.12 开放问题

1. 6.1.138 卡 Redmi logo 是否确实由 `fs/namespace.c` 的 failed hunk 导致？需要复现 `.rej` 并手动修复后重测。
2. 6.1.124 的 audio 是“HAL/设备正常”还是实际能出声？需要实际播放/通话验证。
3. Shirk 的 ThinLTO + AOSP clang 是否是 audio 正常的关键？需要做控制实验（例如用同 clang/LTO 构建 Google config，或修好 6.1.138 后再测）。
4. 6.1.124 + KSU v4.3.0 是否能消掉“需要更新内核”，并保持模块/SUSFS 正常？
5. BBRv3 去掉 PLB/ecn_low 后的 `sizeof(struct bbr)` 是否 ≤104；若否则动态分配的 softirq 分配/失败回退是否稳定。
6. 6.1.124 作为日常基线是否足够（版本较老，安全补丁落后）；是否值得继续追 6.1.138。

---

## 15. 2026-11-04：6.1.124 + KSU v4.3.0 GitHub Actions 构建

### 15.1 目标

沿用 14.10 的路线 1：用 Shirk 构建系统编 `android14-6.1.124`（os_patch `2025-02`），内核侧 KSU 固定到 `v4.3.0`（`e67b14e6bbbbd3e67f24df658a01d3e73a64159c`），消除“需要更新内核”的版本错配；6.1.124 已实测显示/audio/root/日常功能正常，是当前最优基线。

### 15.2 构建前对 fork 的调整（commit `4e3504e`）

- 把 6.1.138 专用的 `task_mmu.o` 警告绕过（`-Wno-error -Wno-unused-*`）收窄为 **仅 `sub_level == "138"` 生效**；6.1.124 回到上游原始编译路径，避免 `-Wno-error` 掩盖真实警告。
- 新增 `_check_rejects()`：SUSFS patch 应用后扫描 `common/**/*.rej`，有残留就 `RuntimeError` 中止构建。原脚本一直 `patch ... check=False`，6.1.138 的 `fs/namespace.c` hunk 失败就是这样被吞掉的（14.5）。
- 目的：如果 6.1.124 的 SUSFS patch 不能干净应用，这次构建会明确失败，而不是产出卡 logo 的 Image。

### 15.3 构建参数

| 参数 | 值 |
|---|---|
| android_version | `android14` |
| kernel_version | `6.1` |
| sub_level | `124` |
| os_patch_level | `2025-02` |
| kernelsu_commit | `e67b14e6bbbbd3e67f24df658a01d3e73a64159c`（v4.3.0） |
| use_kpm | `true` |
| use_zram / BBG / supp_op / set_defbbr | `false` |
| make_release | `true` |
| send_telegram | `false` |

- 第一次 run：`41769932536`（失败，见 15.4）
- 第二次 run：`41771255561`（pin SUSFS，进行中）

### 15.4 第一次构建失败：SUSFS v2.3.0 的 `.rej`（已定位）

- run `41769932536` 在 5m55s 失败，正是新加的 `.rej` 检查拦下的：
  - `fs/namespace.c` `Hunk #1 FAILED at 32`
  - `1 out of 10 hunks FAILED -- saving rejects to file fs/namespace.c.rej`
  - 被拒绝的 hunk 只是文件头的 `#include <linux/susfs_def.h>`、两个 extern 和 `CL_COPY_MNT_NS` 宏。
- **不是 ACK 6.1.124 的问题**：Shirk 自己的 6.1.124 CI（run `33081412158`，job `98549397769`，2026-08-27，SUSFS **v2.2.0**）日志里没有任何 `.rej`，patch 干净应用。
- **根因**：SUSFS commit `8a3d6fd`（2026-10-19，“Rebase susfs patches onto android14-6.1.176”）把 `fs/namespace.c` hunk #1 的上下文改成了 6.1.176 的布局，多了一行 `#include <trace/hooks/blk.h>`；6.1.124 / 6.1.138 的 `fs/namespace.c` 没有这一行，因此同一 hunk 在两个 sublevel 上都失败。**这也说明该 `.rej` 不是 6.1.138 独有的，14.5 把它当成 138 卡 logo 的主要嫌疑不成立（至少不是 138 特有）。**
- **处理**：第二次构建 pin `susfs_commit=92aab7f579e9fb440a43e387c13bff519af13462`：
  - 2026-10-14，SUSFS v2.3.0，**rebase 到 6.1.176 之前的最后一个版本**；
  - patch 的 `fs/namespace.c` hunk #1 上下文仍是 6.1.124 布局；
  - 含 2026-10-12 的 “Sync with the official KernelSU main repo”，与 KSU v4.3.0（2026-10-11 authored）时间最接近。

### 15.5 待验证

1. 第二次构建日志中 SUSFS patch 无 `.rej`、`task_mmu.c` 无 `-Werror` 失败。
2. 产物内核版本 `6.1.124-android14-11`，内置 KSU 显示 `v4.3.0`，SUSFS 正常。
3. `fastboot boot` 零写入验证：`boot_completed=1`、显示/audio/WiFi/相机/指纹、actual 放音。
4. 通过后再作为 BBRv3 的移植基线。

---

## 16. 2026-11-04：纯 GKI audio 卡死的根因定位

### 16.1 对照实验

在 zorn 上分别 `fastboot boot`：

- Shirk 6.1.124：`dl/shirk124/shirk124-repacked.img`
- Google GKI 6.1.124：`dl/gki-google/gki124-repacked.img`（纯 GKI，无 KSU/SUSFS/KPM）

抓 `/proc/modules`、`dmesg`、`/dev/snd`、`/proc/asound/cards`：

| | Shirk 6.1.124 | Google GKI 6.1.124 |
|---|---|---|
| `uname -r` | `6.1.124-android14-11` | `6.1.124-android14-11-g8e604f58ac19-ab13165110` |
| 模块数 | 497 | 442 |
| `/dev/snd` | `controlC0` + 50 个 PCM | 只有 `timer` |
| `/proc/asound/cards` | `pineapple-mtp-snd-card` | `no soundcards` |
| machine probe | 6.455s `Sound card ... registered` | 6.5s 起反复失败，9.6s 后仍失败 |
| `Failed to insmod ... Permission denied` | **0** | **54** |
| `boot_completed` | 1 | 空 |

### 16.2 模块加载差异：不是 SELinux

- GKI dmesg 有 54 条 `Failed to insmod '/system_dlkm/.../xxx.ko': Permission denied`；Shirk 0 条。
- **没有** `avc: denied { module_load }`；运行 `setenforce 0` 后 `getenforce=Permissive`，但 `insmod` 仍然 EACCES。
- 启动参数 `androidboot.selinux=permissive enforcing=0` 也不生效：`ro.boot.selinux=permissive`，但 `getenforce` 仍是 Enforcing（user build；config 里没有 `CONFIG_SECURITY_SELINUX_BOOTPARAM`）。
- `modules_disabled=0`，没有 lockdown，没有 `sig_enforce` sysfs。

真正机制在 android14-6.1 `kernel/module/main.c`：

```c
if (!mod->sig_ok && gki_is_module_protected_export(
            kernel_symbol_name(s))) {
    pr_err("%s: exports protected symbol %s\n",
           mod->name, kernel_symbol_name(s));
    return -EACCES;
}
```

- `CONFIG_MODULE_SIG_PROTECT=y`。
- Google certified GKI 有**完整的 KMI protected-export 符号表**；zorn stock 的 `system_dlkm` 模块是 6.1.138 编的，签名在 6.1.124 GKI 上验不过（`sig_ok=false`），且它们**导出**了受保护的 GKI 符号 → 直接 `-EACCES`。
- Shirk 的 standalone ACK 构建**没有生成 KMI 保护表**（`NR_UNPROTECTED_SYMBOLS == 0`，`gki_is_module_protected_export()` 直接返回 false）→ 同样的模块全部加载成功。

### 16.3 与 audio 卡死的关系

GKI 的失败点：

```
pineapple-asoc-snd ... populate_snd_card_dailinks: Unexpected number of WSAs, wsa_max_devs: 0
pineapple-asoc-snd ... msm_asoc_parse_soundcard_name: FAILED to get nvmem cell
msm_cdc_pinctrl_get_gpiodata: platform device not found!
lpass_cdc_va_macro_probe: failed to get swr pin state
lpass_cdc_tx_macro_probe: va-macro not registered yet, defer
lpass_cdc_rx_macro_probe: va-macro not registered yet, defer
<msm_asoc_machine_probe>: X, failed.
```

Shirk 也有早期同样的 machine probe 失败，但：

- `va-macro not registered yet, defer` 在 Shirk 是 **0 次**；
- 6.455s 声卡注册成功；
- GKI 是 **10 次 defer**，声卡永远不出现。

判断：54 个模块加载失败改变了 module init / driver probe 的顺序；GKI 上 `va-macro` 在 `msm_cdc_pinctrl` 设备就绪前 probe，永久 defer；Shirk 模块全部加载后顺序正确，声卡注册，audioserver 正常，boot 完成。**这是目前最具体、最有证据的差异；因果关系尚未用单变量构建最终证实。**

### 16.4 结论

- 纯 GKI / WildKernels 不行，**不是缺 config，不是 SELinux，也不是 KPM/SUSFS**。
- 根因是 **Google GKI 的 GKI module protection**：未通过签名校验的 stock `system_dlkm` 模块导出受保护 KMI 符号 → `-EACCES` → 模块加载顺序被破坏 → audio machine driver 依赖/probe 顺序失败 → 无 sound card → audioserver 死 → `boot_completed` 永远为空。
- Shirk 的 standalone ACK 构建天然没有 KMI 保护表，所以同样的模块能加载、audio 正常。这也是“用 Shirk 构建脚本自编”是正确路线的原因。
- 待办（低优先级）：KSU-only 构建 run `41777839255` 已成功产出；真机 boot 验证 KSU v4.3.0 + 旧 SUSFS/KPM 是否能启动。

---

## 17. 2026-11-04：放弃 Shirk 构建，转纯 Google ACK 的结论

### 17.1 根因确认到机制

- Google GKI 拒绝 54 个 stock `system_dlkm` 模块的代码在 android14-6.1 `kernel/module/main.c`：

```c
if (!mod->sig_ok && gki_is_module_protected_export(
            kernel_symbol_name(s))) {
    pr_err("%s: exports protected symbol %s\n", ...);
    return -EACCES;
}
```

- `CONFIG_MODULE_SIG_PROTECT=y`；Google certified GKI 带完整 KMI protected-export 表；zorn stock 模块是 6.1.138 编的，在 6.1.124 GKI 上 `sig_ok=false`，且导出受保护符号 → 直接 `-EACCES`。
- Shirk 的 standalone ACK 构建没有生成保护表（`NR_UNPROTECTED_SYMBOLS == 0`，`gki_is_module_protected_export()` 返回 false）→ 同样的模块全部加载。
- **SELinux 已排除**：`setenforce 0` 后 Permissive，`insmod` 仍 EACCES，且无 `avc: denied { module_load }`；`androidboot.selinux=permissive` 在 user build 上被 init 强制回 Enforcing（`CONFIG_SECURITY_SELINUX_BOOTPARAM` 未开）。
- 与 audio 的关系：GKI 上 `lpass_cdc_va_macro_probe: failed to get swr pin state` → `va-macro not registered yet, defer` **10 次**，`/proc/asound/cards` 为空；Shirk 上 defer **0 次**，6.455s 注册 `pineapple-mtp-snd-card`。模块加载失败 → probe 顺序变化是强证据，但尚未用单变量构建最终证明。

### 17.2 关键决定

- **不再基于 `ShirkNeko/GKI_KernelSU_SUSFS` 的 `kernel_builder.py`**；直接按 Google 官方流程构建纯 ACK。
- **不需要内置 KSU**：当前 root 是 init_boot 里的 SukiSU **LKM**，在 GKI 6.1.124 上实测能加载、`su` 可用（`kernelsu: no symbol version for module_layout` 只是警告）。
- 最小目标：**ACK 6.1.124 + `CONFIG_MODULE_SIG_PROTECT=n`**，无 KSU / SUSFS / KPM / ZRAM / BBG。

### 17.3 Google 官方构建命令

来自 source.android.com（Build kernels）与 `kernel/manifest`：

```bash
mkdir -p ~/gki-pure && cd ~/gki-pure
# 固定 6.1.124 用 -2025-02 分支；滚动分支是 common-android14-6.1
repo init -u https://android.googlesource.com/kernel/manifest -b common-android14-6.1-2025-02
repo sync -c -j$(nproc) --no-tags --fail-fast

cd common
echo 'CONFIG_MODULE_SIG_PROTECT=n' >> arch/arm64/configs/gki_defconfig
cd ..

tools/bazel run //common:kernel_aarch64_dist -- --dist_dir=out/dist
```

- 产物在 `out/dist/`：`Image`、`Image.gz`、`system_dlkm` 等。
- 若要 ThinLTO（Shirk 用的就是 thin）：`tools/bazel run --lto=thin //common:kernel_aarch64_dist -- --dist_dir=out/dist`；Google certified GKI 是 LTO_NONE，这是下一个待隔离变量。

### 17.4 待验证 / 风险

1. 关掉 module protection 后：54 个模块是否全部加载、`/dev/snd/controlC0` 是否出现、`boot_completed` 是否为 1。
2. 若声卡仍不注册，下一个变量是 LTO（ThinLTO vs LTO_NONE）或 driver probe 顺序。
3. 纯 GKI 不影响 `fastboot boot`；root 仍由 init_boot 的 LKM 提供。
4. **设备状态**：已回 stock `6.1.138-android14-11-g965475777129-mi`；本轮测试只用了 `fastboot boot`，`boot_a` 未改；stock boot 备份 `dl/boot_a_current.img`，SHA256 `a1a041eb90226266244844c45adf650f665e930f622cc1db56afb95aba38affd`。
5. KSU-only 构建 run `41777839255` 成功（30m7s），但仍是 Shirk 脚本 + SUSFS v2.1.0 + KPM on，低优先级；可作为对照。

### 17.5 前序结论修正

- 14.5 / 15 里把 `fs/namespace.c` 的 `.rej` 当成 6.1.138 卡 logo 的主要嫌疑不成立：该 hunk 在 6.1.124 上同样失败，根因是 SUSFS commit `8a3d6fd`（2026-10-19）把 patch rebase 到 6.1.176，上下文多了 `#include <trace/hooks/blk.h>`。用 `susfs_commit=92aab7f`（rebase 前）可干净应用。
- 6.1.138 卡 logo 的真正原因仍未确认；下一步纯 GKI 基线跑通后再回头处理。

---

## 18. 2026-11-04：Google GKI 6.1.138 + 关闭 module protection（成功基线）

### 18.1 结论

**成功。** ACK/Google GKI 6.1.138 + `CONFIG_MODULE_SIG_PROTECT=n`：

- `boot_completed=1`（adb 出现后 10 秒内）；
- 声卡 `pineapple-mtp-snd-card` 6.536s 注册，`/dev/snd/controlC0` + 50 个 PCM，`va-macro defer` 0 次；
- 用户实测 audio 播放正常；
- 显示正常，无持续闪屏；
- WiFi（SSID `40`）、5G、相机（8 devices）、指纹（HAL alive，4 枚）、KSU LKM root、BBR 均正常。

**第 8.5 节的旧结论「纯 ACK + 设备 config 也卡 audio，不是 config 问题」需要修正**：那次构建的 `.config` 同样是 `CONFIG_MODULE_SIG_PROTECT=y`，且生成了 8.7KB protected exports / 230KB unprotected symbols 表，protection 实际是生效的。audio 卡死的根因就是 module protection，与 config、Xiaomi 内核代码无关，第 16/17 节的判断成立。

### 18.2 构建

- 源码：`/root/ack138-server`，tag `android14-6.1-2025-06_r2`，commit `109284bd5`（即 6.1.138）。
- config：`arch/arm64/configs/gki_defconfig`（纯 Google GKI，不是设备 config）。
- 改动：
  - `scripts/config -d MODULE_SIG_PROTECT` → 关闭 module protection；
  - `CONFIG_LOCALVERSION="-android14-11"`，关闭 `LOCALVERSION_AUTO`；
  - 保留 `CONFIG_LTO_NONE=y`（与 Google certified GKI 一致）、`CFI_CLANG=y`、`MODVERSIONS=y`、`DEBUG_INFO_BTF=y`。
- 工具链：Ubuntu clang 17.0.6 / LLD 17.0.6 / pahole 1.31（`/usr/lib/llvm-17/bin` 加入 PATH，`LLVM=1`）。
- 构建目录：`/root/gki138-noprot`。
- 命令：

```bash
export PATH=/usr/lib/llvm-17/bin:$PATH
cd /root/ack138-server
make ARCH=arm64 LLVM=1 O=/root/gki138-noprot gki_defconfig
scripts/config --file /root/gki138-noprot/.config -d MODULE_SIG_PROTECT
scripts/config --file /root/gki138-noprot/.config --set-str LOCALVERSION "-android14-11"
scripts/config --file /root/gki138-noprot/.config -d LOCALVERSION_AUTO
make ARCH=arm64 LLVM=1 O=/root/gki138-noprot olddefconfig
make ARCH=arm64 LLVM=1 O=/root/gki138-noprot -j10 Image
```

- 结果：0 error；`Image` 35,367,424 字节，SHA256 `c805982923dbc1bb8634badea267c73cd1280c0e1d5bd9c970627ef31ecacdac`。
- banner：`Linux version 6.1.138-android14-11+ (root@iliyian-ma) (Ubuntu clang version 17.0.6 ...) #1 SMP PREEMPT`。
- 内嵌 config 验证：`# CONFIG_MODULE_SIG_PROTECT is not set`、`CONFIG_LTO_NONE=y`、`CONFIG_MODVERSIONS=y`。
- 未生成 `include/generated/gki_module_unprotected.h`，确认 protection 表未参与编译。

### 18.3 与 Google certified GKI config 对比

从 certified `gki-6.1.138-Image` 提取内嵌 config（`IKCFG_ST`），与本构建比较：**6004 个配置项，12 项不同**。

功能性差异只有 4 项：

| 配置 | 本构建 | certified GKI |
|---|---|---|
| `CONFIG_MODULE_SIG_PROTECT` | n | y |
| `CONFIG_TRIM_UNUSED_KSYMS` | n | y |
| `CONFIG_UNUSED_KSYMS_WHITELIST` | 无 | `"abi_symbollist.raw"` |
| `CONFIG_PAHOLE_HAS_LANG_EXCLUDE` | y | 无 |

其余 8 项为 `LOCALVERSION`/`LOCALVERSION_AUTO`/`MODULE_SCMVERSION` 和 clang/LLD/AS/pahole 版本字符串。`TRIM_UNUSED_KSYMS` 关闭是因为 standalone `make` 没有 `abi_symbollist.raw`；后果是保留全部导出符号，内核略大，vendor 模块更容易加载。

### 18.4 打包与测试

- 用服务器上的 `magiskboot` 把 `Image` 换进原厂 boot（`/root/stock-boot.img`，SHA256 与 PC `dl/boot_a_current.img` 一致 `a1a041eb...`）。
- 打包结果：header v4，`KERNEL_SZ 35367424`，`RAMDISK_SZ 0`，`CMDLINE` 空。
- 产物：`dl/gki138-noprot-boot.img`，SHA256 `b61b6e6bb373c5800bdbd340f77559fd13e822affef9f7643eaabdd27de2f86f`。
- 测试方式：`fastboot boot dl/gki138-noprot-boot.img`，零写入，`boot_a` 未改。

### 18.5 实测数据

| 项 | 结果 |
|---|---|
| `uname -r` | `6.1.138-android14-11+` |
| `sys.boot_completed` | 1 |
| 已加载模块 | 501 |
| `exports protected symbol` | 0 |
| `Failed to insmod` / module init 失败 | 0 |
| `Permission denied`（dmesg） | 26 条，但均为 init 的 chown/chmod/mkdir 失败，与模块加载无关 |
| 声卡 | `pineapple-mtp-snd-card`，6.536s 注册 |
| `va-macro not registered yet, defer` | 0 |
| `/dev/snd` | `controlC0` + 50 个 PCM |
| audioserver | running；排除 adbd 自身命令日志后，真实 `Audioserver died` = 0 |
| audio 实际放音 | 用户实测正常 |
| 显示 | NATIVE/SRGB/DISPLAY_P3 正常，当前 DISPLAY_P3；无持续闪屏 |
| HwcComposer | 241 条 `presentFence:-1` 但呈突发分布，集中在截图/界面切换时；用户确认视觉正常 |
| 相机 | 8 个 camera device |
| 指纹 | HAL alive，4 枚已录入 |
| WiFi | 开启后连上 SSID `40` |
| 蜂窝 | 5G，`rmnet_data2` 有地址 |
| root | KSU LKM 正常，`adb shell` 为 `u:r:ksu:s0` |
| BBR | `tcp_available_congestion_control=reno cubic bbr`，当前 `bbr`；`tcp_bbr` 模块已加载 |
| 备注 | `lb: Unknown symbol read_swap_cache_async (err -2)` 1 条，疑为可选 vendor 模块，未影响功能，待与 stock 对照 |

### 18.6 下一步

1. **以这棵树为基线移植 BBRv3**（真正目标）：
   - GKID 补丁 03–16、18–19，裁掉 PLB（01–02）和 ecn_low（17/20）；
   - `ICSK_CA_PRIV_SIZE` 先确认裁剪后 `sizeof(struct bbr) ≤ 104`，否则动态分配；
   - 注册名用 `bbr3`，保留 `bbr`/`cubic` 做 A/B；
   - 保持 `CONFIG_MODULE_SIG_PROTECT=n` 和本节的 gki_defconfig 基线。
2. 关掉旧的 `tcp_bbr.ko` KSU 模块（避免与内核内置 bbr 重复）。
3. 构建/测试流程复用：`make Image` → `magiskboot` 换 kernel → `fastboot boot` 零写入。
4. 可选：等 BBRv3 稳定后，再花一次构建验证 6.1.145 + protection off 的显示边界；预期仍闪屏，6.1.138 是 zorn 的显示兼容上限。（**2026-11-05 更正**：该预期错误；真正边界是 stable 6.1.140/6.1.141 引入的 valid clones check，回退后 6.1.176 显示正常，见第 20 节。）

### 18.7 新增产物

- `dl/gki138-noprot-boot.img`（测试 boot，SHA256 `b61b6e6b...`）
- `logs/gki138-noprot-screen.png`（测试内核下截图）
- 服务器：`/root/gki138-noprot/`（构建目录）、`/root/gki138-noprot-build.log`、`/root/gki138-noprot-boot.img`、`/root/gki138-noprot/embedded.config`

---

## 19. 2026-11-04：BBRv3 移植成功（自研 backport）

### 19.1 结论

**成功。** ACK/Google GKI **6.1.138** + 自研 BBRv3 回移，真机实测：

| 项 | 结果 |
|---|---|
| `uname -r` | `6.1.138-android14-11+` |
| `sys.boot_completed` | 1 |
| `tcp_available_congestion_control` | `reno bbr3 cubic` |
| 当前 CC | `bbr3`（`sysctl -w net.ipv4.tcp_congestion_control=bbr3` 成功） |
| 已加载模块 | 500 |
| `Failed to insmod` | 0 |
| `disagrees about version` | 0 |
| `exports protected symbol` | 0 |
| CRC 放行日志 | 1 条：`qcom_ipcc: tolerating symbol version mismatch for log_write_mmio` |
| 声卡 | `pineapple-mtp-snd-card` 6.529s 注册成功 |
| 用户确认 | 正常开机 |

这次没有使用 GKID 等第三方补丁，**唯一参考是 Google 官方 `google/bbr`**。

### 19.2 参考版本选择

`google/bbr` 的 BBRv3 快照（实测查到的分支）：

| 分支 | 基线 | 日期 | 形态 |
|---|---|---|---|
| `bbr-v3-2024-02-22-01` | 6.8.0 | 2024-02-22 | `tcp_bbr.c` 替换 BBRv1 |
| `v3`（本次采用） | **6.13.7** | 2025-03-18 | `tcp_bbr.c` 替换 BBRv1 |
| `bbr-v3-2026-09-16-01` | 7.1 开发线 | 2026-09-16 | 新增 `tcp_bbr3.c`，`CONFIG_TCP_CONG_BBR3`，注册名 `bbr3` |

- 采用 commit `90210de4b779d40496dee0b89081780eeddf2a60`（`v3`，基于 `v6.13.7`）。
- 2026-09 版是更晚的实现，已把 BBRv3 拆成独立文件并删除 ECN；它有真实的算法变化（DRAIN gain、undo state 等），本次先验证 2025 版，后续可再换。
- 2026-09 版仍然**不在 mainline**；vanilla 7.1 中 `tx_in_flight`、`skb_marked_lost`、`CA_EVENT_TLP_RECOVERY` 等均为 0，Google 仍自行维护完整 patchset。

### 19.3 核心栈补丁（9 个文件）

相对 ACK `109284bd5`（`android14-6.1-2025-06_r2`）：

1. `include/net/tcp.h`
   - `struct rate_sample`：新增 `prior_lost`、`tx_in_flight`、`lost`、`is_acking_tlp_retrans_seq`、`is_ece`；
   - `struct tcp_skb_cb`：`first_tx_mstamp/delivered_mstamp` u64→u32，新增 `in_flight:20` 和 `lost`（tx 区仍 24 字节，整个 cb 48→44）；
   - `tcp_congestion_ops`：`min_tso_segs` 改为 `tso_segs(sk, mss_now)`，新增 `skb_marked_lost`；
   - 新增 `CA_EVENT_TLP_RECOVERY`、`TCP_CONG_WANTS_CE_EVENTS`、`tcp_ca_wants_ce_events()`、`tcp_stamp32_us_delta()`、`tcp_skb_tx_in_flight_is_suspicious()`；
   - 新增 `TCP_ECN_LOW` / `TCP_ECN_ECT_PERMANENT` 定义（本配置下不置位）。
2. `include/linux/tcp.h`：`fast_ack_mode`、`tlp_orig_data_app_limited` 塞进原有 `dup_ack_counter:2,tlp_retrans:1,unused:5` 的空位，**`tcp_sock` 布局零变化**。
3. `include/uapi/linux/inet_diag.h`：`tcp_bbr_info` 扩展 + `enum tcp_bbr_phase`。
4. `net/ipv4/tcp_rate.c`：`tcp_set_tx_in_flight()`；记录 `tx.lost`；填 `rs->prior_lost/tx_in_flight`；发送/ACK 间隔改用 32 位时间戳差值。
5. `net/ipv4/tcp_input.c`：丢失时回调 `skb_marked_lost`；SACK shift 时调整 `tx.in_flight`；TLP ACK 发 `CA_EVENT_TLP_RECOVERY` 并标记 `is_acking_tlp_retrans_seq`；ACK 路径填 `is_ece`、调用 `tcp_rate_check_app_limited`；`fast_ack_mode` 分支。
6. `net/ipv4/tcp_output.c`：`TCP_ECN_ECT_PERMANENT`；`tcp_fragment` 调整 `tx.in_flight`；repair 路径记录 `tx.in_flight`；`tso_segs` 回调；loss probe 记录 `tlp_orig_data_app_limited`。
7. `net/ipv4/tcp_cong.c`、`net/ipv4/tcp.c`、`net/ipv4/tcp_timer.c`：`fast_ack_mode` 清零、app-limited 采样。
8. `kernel/module/version.c`：**CRC 不匹配时放行**（`check_version()` 的 `bad_version` 返回 1，保留 `pr_warn_once`）。

### 19.4 算法文件适配

`net/ipv4/tcp_bbr.c` 直接取自官方 v3 分支，改动仅四类：

1. 删除 `__bpf_kfunc` / BTF kfunc 注册（Android 不用）；
2. 删除 PLB 和 ecn_low 相关路径（本配置下是死代码：`bbr_can_use_ecn()` 依赖 `TCP_ECN_LOW`，而该 bit 只有 `RTAX_FEATURE_ECN_LOW` 路由特性才会置位）；
3. `cong_control` 签名适配 6.1 的 `(sk, rs)`（6.13 的 `ack/flag` 在函数体内未使用）；
4. 状态从内联改为动态分配 + 注册名 `bbr3`。

动态状态设计：

```c
struct bbr_sock_priv {
    struct bbr *state;
    struct sock *owner;
    u64 magic;
};
```

- `struct bbr` 即使去掉 PLB 仍是 144 字节，6.1 的 `ICSK_CA_PRIV_SIZE` 只有 104；
- 不能加大 `icsk_ca_priv`，否则 `tcp_sock` 所有字段平移 40 字节，破坏 vendor 模块 ABI 和 CRC；
- `bbr_init()` 用 `in_softirq() ? GFP_ATOMIC : GFP_KERNEL` 分配，`.release` 释放；
- `owner` 校验防止 `sk_clone_lock()` 复制 listener 的 `icsk_ca_priv` 后子 socket 共享/误释放；
- 分配失败时入口返回、连接退化但不崩溃。

### 19.5 静态验证

- **BTF 逐字段对比**（手机 stock `vmlinux.btf` vs 新内核 BTF）：
  - `tcp_sock` 2368 字节不变，所有既有字段偏移一致；仅 `unused:5` 变 `unused:3` + 两个新位；
  - `inet_connection_sock` 1536 字节完全一致；
  - `rate_sample` 72→88、`tcp_congestion_ops` 192→192（加成员后仍按 cacheline 对齐）、`tcp_skb_cb` 48→44（tx 区不变，union 对齐从 8 降到 4）。
- **QEMU（aarch64 virt，PL011 串口）**：core-only 内核 4.3 秒启动到 `VFS: Unable to mount root fs`，无任何 panic/BUG，证明核心补丁本身没有启动期问题。

### 19.6 遇到的问题与解决

1. **`cong_control` 签名**：6.1 是 `(sk, rs)`，6.13 是 `(sk, ack, flag, rs)`；官方函数体不使用 `ack/flag`，直接删形参。
   - 首次编译报 incompatible function pointer，编译器拦下。
2. **文件 CRLF**：Windows 上 Python 文本模式写回把 LF 变成 CRLF；改用 `newline=""` 读写后解决。
3. **pstore/ramoops 不可用**：`pmsg` 写入后正常重启即消失，说明这台机器的 ABL 每次启动都清 ramoops；崩溃日志拿不到。
4. **`fastboot boot` 被忽略**：该 bootloader 实际启动的是已刷入的分区；`fastboot boot` 自定义镜像后仍进 stock/已刷内核（uname 证明）。
5. **initramfs 调试镜像没跑**：UEFI 日志显示 `initrd size = 8388609 B`，bootloader 用的是 vendor_boot 的 initrd；往 boot.img 里加 ramdisk 不会被执行。
6. **CRC 误伤（本次最大坑）**：
   - 我们的头文件改动让 genksyms 对 **691 个导出符号**生成了不同的 CRC，包括 `skb_put`、`__alloc_skb` 这种 ABI 未变的符号；
   - `audio_pkt_dlkm.ko` 的 `__versions` 期望 `skb_put=0xac7a3881`（baseline 值），我们内核是 `0x385a34f9`；
   - `check_version()` 对不上返回 0，调用方 `-EINVAL`，模块加载失败；音频模块缺失 → AudioService/HAL 卡死 → 开机循环（与第 16/18 节 module protection 的表现同类）；
   - 不能直接 `CONFIG_MODVERSIONS=n`：vermagic 会少 `modversions` 标记，`same_magic()` 比较第一个空格之后的内容，模块会报 version magic 不匹配；
   - 最终保留 `MODVERSIONS=y`，只把 `bad_version` 的返回改成 1（等价 `insmod --force` 的 CRC 放行），vermagic/ksymtab 格式不变。
7. **构建目录脏状态**：中途 TaskStop 掉 make，留下 `llvm-ar: inode.o/trace_kprobe.o: No such file`；重跑 make 补齐。
8. **旧 `tcp_bbr.ko`**：`tcp_congestion_ops` 布局变了，旧模块不能加载；测试前已在 `/data/adb/modules/tcp_bbr_kmod/disable` 禁用，保持禁用。

### 19.7 真机验证命令与结果

```bash
uname -r                                  # 6.1.138-android14-11+
getprop sys.boot_completed                # 1
cat /proc/sys/net/ipv4/tcp_available_congestion_control
# reno bbr3 cubic
sysctl -w net.ipv4.tcp_congestion_control=bbr3
cat /proc/sys/net/ipv4/tcp_congestion_control   # bbr3
wc -l < /proc/modules                     # 500
dmesg | grep -c "tolerating symbol version mismatch"  # 1
dmesg | grep -c "Failed to insmod"        # 0
dmesg | grep -c "disagrees about version" # 0
dmesg | grep -c "exports protected symbol"# 0
dmesg | grep "pineapple-mtp-snd-card"     # 6.529s 声卡注册
```

### 19.8 产物与路径

**本地产物**：

- `dl/gki138-bbrv3-crc-boot.img`（测试 boot，SHA256 `8e6bacabedc9e347cc41a68a77dc9b630bbd6b6dffcb000adc8aa82784e28354`）
- 内核 `Image`：35,432,960 字节，SHA256 `43b72d3d06717e1df376abf1331d1ca784323914b4e53b5d49c41643ff9e5934`
- stock boot 备份：`dl/boot_a_current.img`（SHA256 `a1a041eb...`）
- 当前 `boot_a` 已刷入测试内核；`fastboot boot` 不可用，只能刷写；恢复用 stock 备份或 `gki138-noprot-boot.img`。

**服务器**：

- `/root/ack138-server`（分支 `bbrv3`，基于 `109284bd5`）
- `/root/gki138-bbrv3/`（构建目录）、`/root/gki138-bbrv3-crcpatch2.log`
- `/root/bbrv3-export/patches/`（0001 core、0002 algorithm、0003 CRC）
- `/root/gki138-bbrv3-crc-boot.img`

**GitHub**：`iliyian/k80-zorn-bbr`（原名 `android-gki-bbr-kmod`；BBRv3 patchset 在 `bbrv3/`，过程记录在 `bbrv3/docs/`）。

### 19.9 已知限制 / 风险

- `check_version()` 的放行是测试内核的妥协：所有模块的符号 CRC 不匹配都会被容忍；依据是 BTF 证明 vendor 模块用到的结构（`tcp_sock`/`inet_connection_sock`/`sk_buff`）布局未变。若后续真正改了这些结构，必须重新评估。
- 旧 `tcp_bbr.ko`（BBRv1）不能在这个内核上加载；如需 A/B，需要把官方 `tcp_bbr1.c` 一起编进来。
- ECN/PLB 被裁掉；在本机（无 `RTAX_FEATURE_ECN_LOW`）它们本来就是死代码，但严格说不是“完整 ECN 版 BBRv3”。
- 当前是 **2025-03 版** BBRv3；2026-09 版有行为更新（DRAIN gain、undo state 等），尚未移植。
- 实际吞吐/延迟已有两组数据：2026-11-02 的 BBRv1 vs cubic（联通 5G，三台服务器），以及 2026-11-04/05 的 BBRv3 vs cubic（电信 LTE）；链接见 §21.6。长时间稳定性、6.1.176 上的实际放音、相机/指纹回归仍未完成。

### 19.10 下一步

1. 实际流量测试：`bbr3` vs `cubic` 已完成一轮（2026-11-04/05，电信 LTE），`bbr` vs `cubic` 也有 2026-11-02 的联通 5G 数据；严格同内核的 `bbr3` vs `bbr` A/B 仍未做。数据见 §21.6 与 `docs/bench/`。
2. 验证音频实际放音、相机/指纹/WiFi 回归；
3. 视需要把算法换成 2026-09 `tcp_bbr3.c`（需要适配 `cwnd_event_tx_start` 等 7.1 API）；
4. 自定义注册名：`.name` 是普通字符串，`TCP_CA_NAME_MAX=16`，改成 `iliyian` 只需改一行并重编；若要把 BBRv1 也保留，可加入 `tcp_bbr1.c` 并以 `bbr` 注册。

---

## 20. 2026-11-05：6.1.176 显示闪屏根因与修复（stable 6.1.141 valid clones check）

### 20.1 结论

**6.1.138 不是 zorn 的显示兼容上限。** 纯 ACK 6.1.176 的闪屏是一个具体的、可回退的 stable 补丁导致的：

- stable **6.1.141** 引入上游提交 **`41b4b11da02157c7474caf41d56baae0e941d01a`**（"drm: Add valid clones check"，2024-12-17，Jessica Zhang）。
- 该提交在 `drm_atomic_helper_check_modeset()` 中新增 `drm_atomic_check_valid_clones()`：要求同一个 CRTC 上所有 encoder 的 `possible_clones` 互相覆盖。
- zorn 的 `msm_drm.ko`（vendor 模块，vermagic 6.1.68）在 **CRTC 203** 上同时挂 DSI 和 Virtual 两个 encoder（`encoder_mask=0x6`），其 `possible_clones` 不满足这个新检查。
- 结果：`drm_atomic_nonblocking_commit()` → `drm_atomic_check_only()` → `drm_atomic_helper_check_modeset()` 返回 `-EINVAL`，HWC 每帧重试失败，屏幕持续闪屏。
- 回退这一处检查后，6.1.176 显示恢复正常，用户确认视觉正常；atomic commit 失败日志从每帧一条降到 0。

版本边界：**6.1.138 / 139 / 140 没有该检查；6.1.141 及以后有**。

### 20.2 实验对象

- 源码：ACK `android14-6.1-2026-09`，HEAD `8bc4db3e2`，版本 `6.1.176`。
- 构建：`gki_defconfig`，`CONFIG_MODULE_SIG_PROTECT=n`，`CONFIG_LTO_NONE=y`，`CONFIG_MODVERSIONS=y`，`CONFIG_LOCALVERSION="-android14-11"`，Ubuntu clang 17.0.6。
- 工具链兼容：clang 17 编 6.1.176 的 `tools/bpf/resolve_btfids/libbpf` 时，`libbpf.c:10756` 的 `next_path = strchr(s, ':')` 触发 `-Wincompatible-pointer-types-discards-qualifiers`；加 `(char *)` cast 后通过。仅影响 host 工具。
- 刷写方式：`fastboot flash boot_a`（该 bootloader 忽略 `fastboot boot`）。
- 本构建**不带 BBRv3**，`tcp_available_congestion_control=reno cubic`。

### 20.3 现象与日志证据

6.1.176 原版镜像（`Image` SHA256 `0ff0f50b...`，boot `444064ff...`）：

- `sys.boot_completed=1`，501 个模块，`Failed to insmod=0`，声卡 `pineapple-mtp-snd-card` 正常注册，KSU LKM root 正常。
- 显示持续闪屏。logcat 里每帧重复：

```
SDM: DRMAtomicReq::Commit: drmModeAtomicCommit failed with error 22 (Invalid argument). crtc=203
SDM: HWDeviceDRM::AtomicCommit: AtomicCommit failed with error -22 crtc 203
SDM: DisplayBase::PerformCommit: COMMIT failed: 11
HwcComposer: presentOrValidateDisplay 0 presentFence:-1
```

- 打开 DRM debug（`echo 0x1f > /sys/module/drm/parameters/debug`）后拿到直接证据：

```
[drm:drm_atomic_helper_check_modeset] crtc203 failed valid clone check for mask 0x6
```

- kretprobe 定位（`/sys/kernel/tracing/kprobe_events`）：

```
katomic_check: (drm_atomic_nonblocking_commit+0x1c/0x90 <- drm_atomic_check_only) arg1=0xffffffea
kioctl:        (drm_ioctl_kernel+0xf8/0x184 <- drm_mode_atomic_ioctl)          arg1=0xffffffea
```

`0xffffffea` = `-22` = `-EINVAL`，说明失败发生在 `drm_atomic_check_only()` 内部，而不是 commit 后续阶段。

### 20.4 版本边界验证

对 stable 6.1.138–6.1.176 逐个读取 `drivers/gpu/drm/drm_atomic_helper.c`，检查是否包含 `drm_atomic_check_valid_clones`：

| 版本 | 是否包含 |
|---|---|
| 6.1.138 / 139 / 140 | 否 |
| 6.1.141 – 6.1.176 | 是 |

因此 6.1.145（Shirk）和 6.1.176（Droidspaces、本次纯 ACK）的闪屏是同一个原因；此前把它们归因为“6.1.138 显示上限”是错误结论。

### 20.5 修复

在 6.1.176 上精确回退 `drm_atomic_check_valid_clones()` 及其调用，等价恢复 6.1.140 行为：

- 删除 `drivers/gpu/drm/drm_atomic_helper.c` 中的 `static int drm_atomic_check_valid_clones(...)` 函数；
- 删除 `drm_atomic_helper_check_modeset()` 中对应的调用：

```c
ret = drm_atomic_check_valid_clones(state, crtc);
if (ret != 0)
    return ret;
```

补丁：`/root/gki176-patches/0001-revert-valid-clones-check.patch`（本地副本 `gki176-patches/0001-revert-valid-clones-check.patch`）。

重新编译只重编 `drm_atomic_helper.o` 并重链，Image SHA256 `841b819e...`，boot `d34718c8...`。

### 20.6 修复后验证

| 项 | 结果 |
|---|---|
| `uname -r` | `6.1.176-android14-11+` |
| `sys.boot_completed` | 1 |
| 模块数 | 501 |
| `Failed to insmod` | 0 |
| `valid clone check` dmesg | 0 |
| `drmModeAtomicCommit failed` | 0（清 logcat 后唤醒/滑动复测） |
| `COMMIT failed` | 0 |
| `presentFence:-1` | 163 条，但 6.1.138 正常基线也有类似突发（§18.5 记录 241 条，视觉正常），单独不作为闪屏判据 |
| 声卡 | `pineapple-mtp-snd-card`，`/dev/snd` 正常 |
| root | KSU LKM，`u:r:ksu:s0` |
| 视觉 | 用户确认正常 |
| WiFi / 相机 / 指纹 | 本轮未逐一复测（WiFi 当时关闭）；留待回归 |

### 20.7 风险与后续

- 回退的是 DRM core 的**校验**，不是显示功能；该检查用于发现 encoder clone mask 不合法，zorn 的 vendor `msm_drm.ko` 因为 DSI + Virtual encoder 共享 CRTC 而天然满足不了它。对这条 vendor 栈来说是新检查的 false positive。
- 回退后等价于 6.1.140 行为；6.1.138–140 本来就没有这个检查。风险是如果将来有驱动真的依赖该检查阻止非法 clone，可能重新暴露上游原本想修的问题；对本机 vendor HWC 场景未观察到。
- 下一步：
  1. 在 6.1.176 + revert 基线上移植 BBRv3（现有 6.1.138 patchset 需重新适配/验证 CRC）；
  2. 复测 WiFi、相机、指纹、实际放音；
  3. 长稳和吞吐测试；
  4. 如果只想避开该 bug 且不追新，6.1.140 是最后一个无需 patch 的 6.1 版本（未实测，按代码边界推断）。

### 20.8 产物与路径

**服务器**：

- `/root/ack176-shallow`：ACK 6.1.176 worktree（含 revert + libbpf cast）
- `/root/gki176-noprot/`：构建目录；原版 Image `0ff0f50b...`，revert 版 Image `841b819e...`
- `/root/gki176-patches/0001-revert-valid-clones-check.patch`
- `/root/gki176-clonefix-boot.img`（SHA256 `d34718c8...`）

**本地**：

- `dl/gki176-noprot-boot.img`（原版，`444064ff...`）
- `dl/gki176-clonefix-boot.img`（修复版，`d34718c8...`）
- `gki176-patches/0001-revert-valid-clones-check.patch`
- `logs/gki176/dmesg.txt`、`logcat.txt`（原版失败日志）、`clonefix-logcat.txt`（修复后日志）
- `dl/msm_drm.ko`（从手机拉取的 vendor 显示模块，6.1.68 vermagic）

---

## 21. 2026-11-05：6.1.176 双 BBR（bbr + bbr3）+ 默认 bbr3 / fq

### 21.1 结论

在 6.1.176 + valid-clones revert 基线上，**BBRv1 和 BBRv3 同时编入内核**：

- BBRv1：保留 stock `net/ipv4/tcp_bbr.c`，注册名 `bbr`；
- BBRv3：新增 `net/ipv4/tcp_bbr3.c`（由 6.1.138 上已适配的 v3 算法复制而来），注册名 `bbr3`；
- 默认拥塞控制：`CONFIG_DEFAULT_TCP_CONG="bbr3"`；
- 默认 qdisc：`CONFIG_DEFAULT_NET_SCH="fq"`（打开 `NET_SCH_DEFAULT` + `DEFAULT_FQ`）；
- 真机验证：`tcp_available_congestion_control = reno bbr bbr3 cubic`，当前 `bbr3`，`default_qdisc = fq`；两个算法都能切换并实际建立 HTTP 连接。

### 21.2 实现细节

1. **core patch**：`/root/bbrv3-export/patches/0001-tcp-core-bbrv3.patch` 应用到 6.1.176，全部 hunk 成功，0 reject；只有 stable 偏移和 1 个 fuzz 1。
   - fuzz 1 位于 `net/ipv4/tcp_input.c` 的 `tcp_process_tlp_ack(sk, ack, flag, &rs)`，6.1.176 在附近多了 `tcp_in_ack_event(sk, flag)`，最终代码正确。
2. **BBRv3 算法**：把 6.1.138 分支的 `net/ipv4/tcp_bbr.c` 复制为 `net/ipv4/tcp_bbr3.c`，并把模块内静态符号重命名为 `bbr3_register` / `bbr3_unregister` / `tcp_bbr3_cong_ops`；`.name = "bbr3"` 不变。
3. **BBRv1 适配**：core patch 把 `tcp_congestion_ops.min_tso_segs(sk)` 改成 `tso_segs(sk, mss_now)`，stock BBRv1 不兼容。已在 `tcp_bbr.c` 新增 `bbr_tso_segs()`（复刻 `tcp_tso_autosize()`，保留 BBRv1 按 pacing rate 取 1/2 的最小值），并把 ops 成员改为 `.tso_segs`。
4. **Kconfig / Makefile**：
   - 新增 `config TCP_CONG_BBR3`、`config DEFAULT_BBR3`、`default "bbr3" if DEFAULT_BBR3`；
   - `obj-$(CONFIG_TCP_CONG_BBR3) += tcp_bbr3.o`。
5. **config**：
   - `CONFIG_TCP_CONG_BBR=y`、`CONFIG_TCP_CONG_BBR3=y`；
   - `CONFIG_DEFAULT_BBR3=y`、`CONFIG_DEFAULT_TCP_CONG="bbr3"`；
   - `CONFIG_NET_SCH_DEFAULT=y`、`CONFIG_DEFAULT_FQ=y`、`CONFIG_DEFAULT_NET_SCH="fq"`；
   - 继续保留 `CONFIG_MODULE_SIG_PROTECT=n` 和 CRC 放行 patch（见第 19/20 节）。
6. **旧模块**：`/data/adb/modules/tcp_bbr_kmod` 保持禁用；`bbr3_fq` KSU 模块继续在 post-fs-data/service 阶段兜底 `bbr3` + `fq`，并给 `rmnet_data*` / `wlan*` 挂 fq。

### 21.3 真机验证

| 项 | 结果 |
|---|---|
| `uname -r` | `6.1.176-android14-11+` |
| `sys.boot_completed` | 1 |
| `tcp_available_congestion_control` | `reno bbr bbr3 cubic` |
| `tcp_congestion_control` | `bbr3`（默认） |
| 切 `bbr` / 切回 `bbr3` | 均成功 |
| `net.core.default_qdisc` | `fq` |
| `rmnet_data0` qdisc | `fq` |
| 模块数 / `Failed to insmod` | 501 / 0 |
| CRC tolerance 日志 | 1 条（`tolerating symbol version mismatch`，与 6.1.138 基线一致） |
| 声卡 | `pineapple-mtp-snd-card` 正常 |
| root | KSU LKM，`u:r:ksu:s0` |
| 显示 atomic 失败 | `COMMIT failed=0`、`drmModeAtomicCommit failed=0`（唤醒+滑动复测） |
| 实际流量 | `curl https://www.baidu.com` 在 `bbr3` 和 `bbr` 下均返回 HTTP 200；`ss -tin` 分别显示 `bbr3` / `bbr` |
| 视觉 | 沿用第 20 节的显示回退；日志层面无 atomic 失败，用户刷入后已确认视觉正常 |

### 21.4 产物与路径

**服务器**：

- `/root/ack176-shallow`：6.1.176 源码树（valid-clones revert + BBRv3 core + dual BBR + libbpf cast）
- `/root/gki176-noprot/`：构建目录；Image SHA256 `263d14815f0d9f58a2e37dff0b8d67a58baa5ca8046f61f1eeb4b7532bb0c47f`
- `/root/gki176-bbr3-dual-boot.img`：SHA256 `827f8865666e709059230b76c640a1cc35cdea10d49f7253faf9935499440475`
- `/root/gki176-bbr3-dual-build.log`（0 error）
- `/root/bbrv3-export/patches/0001-tcp-core-bbrv3.patch`、`0003-module-tolerate-crc-mismatch.patch`
- `/root/gki176-patches/0001-revert-valid-clones-check.patch`

**本地**：

- `dl/gki176-bbr3-dual-boot.img`（`827f8865...`）
- `logs/gki176/dual-bbr-logcat.txt`
- `gki176-patches/`（回退 patch；core/CRC patch 见服务器路径）

### 21.5 风险与后续

- 双 BBR 本身不改 `tcp_sock` 布局；`rate_sample` / `tcp_skb_cb` 的变化与第 19 节一致。
- 模块 CRC 全局放行、`MODULE_SIG_PROTECT=n` 的安全取舍见第 19.9 / 20.7 节；不是后门，但属于有意的兼容性放宽。
- 待办：
  1. 吞吐/延迟：已完成 BBRv1 vs cubic（2026-11-02）和 BBRv3 vs cubic（2026-11-04/05）两组；严格同内核的 `bbr3` vs `bbr` A/B 仍未做。
  2. 长稳、发热、WiFi/相机/指纹、6.1.176 上的实际放音回归；
  3. 视需要移植 2026-09 版 `tcp_bbr3.c`（需要适配 `cwnd_event_tx_start` 等 7.1 API）。
  4. ThinLTO 已完成构建和首轮真机验证，见第 22 节；性能 A/B 待补。

### 21.6 网络基准补充（来自 docs/bench 与 blog）

**BBRv1 vs cubic（2026-11-02，联通 5G，三台服务器）**

- 下载方向没有可复现差异：netcup 31.50 vs 30.45 Mbps（配对中位 -0.7%）；日本约 42.2 vs 42.2 Mbps（几何 +2.1%）；北京正常信号 24.59 vs 24.59 Mbps（逐对 -57% 到 +29%，方向不稳）；晚高峰 20.45 vs 22.75 Mbps（+2.2%，逐对 -54% 到 +236%）。手机是接收端，CC 在服务端，客户端换 CC 只影响 ACK。
- 上传方向手机端 CC 真正生效：netcup 4.97 vs 3.52 Mbps；日本 6.92 vs 6.97 Mbps；北京正常 5.46 vs 4.40 Mbps（BBR 低约 15%）；晚高峰 1.66 vs 1.10 Mbps（BBR 低约 12%）。
- 队列相反：北京正常发送侧 RTT 804 → 193 ms，上传压载 ping p95 237 → 64 ms；晚高峰 CUBIC RTT 3.98 s / 重传 4.09%，BBR 321 ms / 0.99%；日本 RTT 443–683 → 190–214 ms；netcup 866 → 670 ms。
- 报告：`docs/bench/2026-11-02-bbr-vs-cubic.md`，逐轮 CSV 和逐采样数据在同目录。

**BBRv3 vs cubic（2026-11-04/05，电信 LTE）**

- 下载中位 28.9 vs 32.8 Mbps（+15.8%），但有一轮 cubic 只有 14.8 Mbps；剔除后配对中位只剩 +2.9%，方向不稳。
- 上传中位 2.41 vs 2.07 Mbps，几何平均 -4.8%，2/4 胜；没有可复现的吞吐收益。
- 队列指标 4/4 完全分离：上传 RTT 1.5 s → 0.2 s，p95 2.7 s → 0.4 s，cwnd ~780 → 56 段；p95 采样里 cubic 1.9–4.8 s，bbr3 0.30–0.42 s（首轮除外）。
- 详细表格、统计检验和测量坑见 blog.md 的“BBRv3 实测：延迟有收益，吞吐没有”一节；原始 harness/数据在本地 `bbr-bench-2026-11-04/`。

---

## 22. 2026-11-05：6.1.176 ThinLTO 构建与首轮验证

### 22.1 目标与配置

stock zorn 内核用 ThinLTO；这一节验证同一 6.1.176 + 双 BBR + 显示修复的树能否用 ThinLTO 替代 LTO_NONE，并记录构建成本。

- 源码：`/root/ack176-shallow`，commit `904cfac56`（含 valid-clones revert、BBRv3 core、双 BBR、libbpf cast）。
- 配置：`CONFIG_LTO_CLANG_THIN=y`、`# CONFIG_LTO_NONE is not set`、`CONFIG_LTO_CLANG_FULL` 关闭；`CONFIG_CFI_CLANG=y`、`CONFIG_MODVERSIONS=y`、`CONFIG_MODULE_SIG_PROTECT=n`；`CONFIG_DEFAULT_TCP_CONG="bbr3"`、`CONFIG_DEFAULT_NET_SCH="fq"`。
- 工具链：Ubuntu clang 17.0.6（ACK 官方 pin 是 AOSP `r487747c` / 17.0.2）。
- 构建脚本：`/root/build176_thinlto.sh`；输出目录 `/root/gki176-thinlto`；`make -j8 Image`。
- 内存统计：cgroup v2 `/sys/fs/cgroup/gki-thinlto` 的 `memory.peak` / `memory.swap.peak`。

### 22.2 构建数据

| 项 | 结果 |
|---|---|
| wall time | **24:44.91** |
| user / sys | 9358.30 s / 1004.97 s |
| CPU 占用 | 697% |
| `time -v` max RSS | 6,124,300 KiB（约 5.84 GiB） |
| cgroup `memory.peak` | 6,381,436,928 B（约 5.94 GiB） |
| `memory.swap.peak` | 1,368,064 B（约 1.3 MiB） |
| `memory.events` | `oom=0`、`oom_kill=0` |
| Image | 36,760,064 B，SHA256 `59fdfaabfb4bb94239a3b2d1b851be3bcb6bc75a105d83d8296528c34514d559` |

对比 LTO_NONE：Image 35,645,952 B → 36,760,064 B，增加 1,114,112 B（约 3.1%）。LTO_NONE 那次没有精确的 `time`/峰值记录，不能拿来直接比构建耗时。

### 22.3 ABI 一致性

`/root/gki176-noprot/vmlinux.symvers` 与 `/root/gki176-thinlto/vmlinux.symvers` 全量 diff（去掉注释行后排序）为 **0 行**：

```
module_layout  0xea759d7f  （两边一致）
skb_put        0x385a34f9  （两边一致）
__alloc_skb    0x3a1c9278  （两边一致）
```

结论：ThinLTO 只改代码生成，不改 genksyms 计算的符号 CRC；模块 ABI 与 LTO_NONE 相同，stock vendor 模块的加载路径不需要额外变化。

### 22.4 真机验证

| 项 | 结果 |
|---|---|
| `uname -r` | `6.1.176-android14-11+` |
| `/proc/config.gz` | `CONFIG_LTO_CLANG_THIN=y`、`CONFIG_CFI_CLANG=y` |
| `sys.boot_completed` | 1 |
| 模块数 / `Failed to insmod` | 501 / 0 |
| `tcp_available_congestion_control` | `reno bbr bbr3 cubic` |
| `tcp_congestion_control` | `bbr3` |
| `net.core.default_qdisc` | `fq` |
| 声卡 | `pineapple-mtp-snd-card` 正常 |
| root | KSU LKM，`u:r:ksu:s0` |
| 显示 | `COMMIT failed=0`、`drmModeAtomicCommit failed=0`（唤醒+滑动复测） |

开机进度（单次样本）：

| 指标 | LTO_NONE 基线 | ThinLTO |
|---|---:|---:|
| `boot_progress_enable_screen` | 16022 ms | 15871 ms |
| `sf_stop_bootanim` | 17730 ms | 17447 ms |

分别约 -0.9% 和 -1.6%，但只有单次样本，且系统刚刷完、后台任务不同；不能据此认为 ThinLTO 有稳定性能收益。CPU/存储/UI 的 A/B 还没做。

### 22.5 产物与发布

- boot：`/root/gki176-thinlto-boot.img`，SHA256 `6637585575048818a2276dd68796ed09094536e013bf2ebcc122517d8221a02b`（由 stock boot repack，`RAMDISK_SZ=0`、`CMDLINE` 空）。
- 本地：`dl/gki176-thinlto-boot.img`；Image 在 `release-v0.2.0/Image-6.1.176-ThinLTO`（服务器 `/root/gki176-thinlto/arch/arm64/boot/Image`）。
- GitHub release：`v0.2.0-6.1.176`，包含 LTO_NONE/ThinLTO 两个 boot、两个 Image 和 `SHA256SUMS`：
  <https://github.com/iliyian/k80-zorn-bbr/releases/tag/v0.2.0-6.1.176>

### 22.6 判断

- ThinLTO 的构建成本可接受：10 核 15 GiB + 8 GiB swap 上 `-j8` 约 25 分钟，峰值约 6 GiB，没有触发 OOM。
- ABI 和模块加载没有问题，显示/audio/BBR 回归正常。
- 性能收益目前只有一次开机样本的 ~1%，量级和“ThinLTO 通常 1%–5%”的预期一致，但还不足以定论；下一步需要多轮开机、`simpleperf`、存储和 UI 的 A/B，或者再考虑 PGO/AutoFDO。
- 如果后续没有稳定收益，保留 LTO_NONE 作为默认、ThinLTO 作为“和 stock 同款”的可选变体更稳妥。
