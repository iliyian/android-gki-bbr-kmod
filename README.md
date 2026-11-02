# android-gki-bbr-kmod

在 **不更换内核、不刷 GKI、不需要厂商内核源码** 的前提下，给 Android GKI 设备加载自编译的 `tcp_bbr.ko`，启用 BBR 拥塞控制，并配合 `fq` qdisc。

> 已在 **Redmi K80 (zorn / 24117RK2CC)**，HyperOS 3 / Android 16，内核 `6.1.138-android14-11-g965475777129-mi` 上实测通过并开机自启。

---

## 为什么需要这个

很多国产 GKI 内核把 `CONFIG_TCP_CONG_ADVANCED` 关掉，只保留 `reno`/`cubic`：

```
$ cat /proc/sys/net/ipv4/tcp_available_congestion_control
reno cubic
```

常规做法是编译带 BBR 的完整内核，但：

- 厂商没有发布对应版本源码（本项目设备是小米 6.1.138-mi，未开源）；
- 直接刷通用 GKI / 第三方内核会出现显示栈不兼容（本设备表现为闪屏）。

本项目换了个思路：**只编一个 `tcp_bbr.ko` 模块，运行时加载**。不动 boot 分区，不换内核，因此没有闪屏风险，最坏情况重启即恢复。

## 原理（为什么普通 insmod 不行）

1. 内核没有编 BBR，也没有可用的 `tcp_bbr.ko`；
2. 模块必须与内核的 **结构体布局**、**vermagic**、**MODVERSIONS CRC** 完全匹配；
3. 厂商没有公开源码 / `Module.symvers`，无法生成匹配的 CRC；
4. SukiSU Ultra（KernelSU 系）的 `ksud insmod` 自带用户态加载器：
   - 读取 `.ko` 的全部未定义符号；
   - 通过内核符号查询 / `/proc/kallsyms` 取得真实地址；
   - 把符号表改写成绝对地址后再 `init_module`；
   - vermagic 不匹配时从 kernel log 读出期望值、改写 `.ko` 重试。

于是：**符号版本、导出表、vermagic 都不再是阻碍**，真正需要保证的只剩 **ABI（结构体布局）** 和 **CFI 兼容性**。

```
┌────────────────────┐        ┌─────────────────────────────┐
│ tcp_bbr.ko          │        │ 运行中的厂商 GKI 内核        │
│ (ACK 6.1.138 源码)  │        │ 6.1.138-android14-...-mi    │
│ AOSP clang r487747c│        │                             │
└────────┬───────────┘        └──────────────┬──────────────┘
         │  ksud insmod                     │
         │  kallsyms 解析 + 符号重写         │
         ▼                                  ▼
   tcp_register_congestion_control()  →  /proc/sys/net/ipv4/
                                          tcp_available_congestion_control
                                          = reno cubic bbr
```

## 快速使用（仅适用于已验证的 K80 内核）

`prebuilt/tcp_bbr_kmod.zip` 是针对以下内核编译的，**不要**在其他内核上直接使用：

```
Linux 6.1.138-android14-11-g965475777129-mi
HyperOS OS3.0.309.0.WOKCNXM / Android 16 / Redmi K80 (zorn)
```

安装：

1. 确保已用 SukiSU / KernelSU 获取 root（LKM 模式即可，无需 KPM）；
2. 在 KernelSU 管理器里安装 `prebuilt/tcp_bbr_kmod.zip`，重启；
3. 验证：

```sh
su -c 'cat /proc/sys/net/ipv4/tcp_congestion_control'   # bbr
su -c 'tc qdisc show dev wlan0 | head -1'                # qdisc fq
su -c 'ss -tin | grep -m1 bbr'                           # bbr: 字段
```

模块会在 `post-fs-data` 加载 `.ko`，`service.sh` 设置 `bbr`、`fq`，并有一个 30 秒看门狗防止 netd 重置 qdisc。

## 从源码构建

前置：Linux（WSL2 可用）、约 10GB 磁盘、能访问 TUNA 镜像。

```sh
# 依赖
sudo apt install -y build-essential git curl python3 bc flex bison \
    libssl-dev libelf-dev kmod dwarves linux-tools-generic

# 从手机导出内核配置（或手动指定 CONFIG_GZ=/path/to/config.gz）
adb exec-out su -c 'cat /proc/config.gz' > config.gz

CONFIG_GZ=$PWD/config.gz ./scripts/build-bbr-module.sh
```

脚本会：

1. 从 TUNA 浅拉 ACK 标签 `android14-6.1.138_r00`（不是 kernel.org stable）；
2. 稀疏检出 AOSP clang `r487747c`（与内核 `CC_VERSION_TEXT` 一致）；
3. 用设备 `config.gz` 生成配置，打开 `TCP_CONG_BBR=m`；
4. 编译出 `tcp_bbr.ko`；
5. 生成可直接安装的 KernelSU 模块 zip。

构建完成后建议做 ABI 校验，见 [docs/verification.md](docs/verification.md)。

## 关键坑（完整版见 docs/pitfalls.md）

| # | 坑 | 后果 |
|---|---|---|
| 1 | 用 kernel.org 的 stable 6.1.138 | ACK 多了 `android_kabi_reserved*`、`sk_wait_pending`、`btf_data` 等字段，`sock/tcp_sock/module` 布局差 88~168 字节 |
| 2 | 关闭 `CONFIG_DEBUG_INFO_BTF_MODULES` | `struct module` 少 16 字节，内核读错 `mod->init`，**加载即重启** |
| 3 | 编译器与内核不一致 | kCFI 类型哈希可能不匹配，间接调用触发 CFI panic；本机内核为 AOSP clang `r487747c` |
| 4 | 直接用 `insmod` | vermagic / MODVERSIONS CRC 检查失败 |
| 5 | 改完 ABI 不校验 | 字段偏移错误通常不是报错，而是随机崩溃 |

## 验证结果（K80）

| 结构体 | 设备 BTF | 模块 BTF |
|---|---|---|
| `tcp_sock` | 2368 | 2368 |
| `tcp_congestion_ops` | 192 | 192 |
| `rate_sample` | 72 | 72 |
| `sock` | 856 | 856 |
| `inet_sock` | 1088 | 1088 |
| `inet_connection_sock` | 1536 | 1536 |
| `module` | 1088 | 1088 |

运行状态：

```
tcp_congestion_control          : bbr
tcp_available_congestion_control: reno cubic bbr
net.core.default_qdisc          : fq
wlan0 / rmnet_data* / rmnet_ipa0: qdisc fq
```

## 性能实测（BBR vs CUBIC）

2026-11-02 在浙江联通 5G（室内，某高校）的三台不同线路服务器上做了手机端 A/B 对比（cubic+mq/pfifo_fast vs bbr+fq）：下载、上传、压载延迟。

主要结论：

- **下载**：手机端切 BBR / 改 qdisc 没有可归因影响（下载方向的拥塞控制在服务端）。
- **上传**：BBR 的主要收益是降低排队延迟；在深缓冲蜂窝上行上，吞吐不一定优于 CUBIC，遇到丢包还会退让。

完整方法、三台不同 RTT 服务器的结果与局限见 [docs/bench/2026-11-02-bbr-vs-cubic.md](docs/bench/2026-11-02-bbr-vs-cubic.md)，逐轮脱敏数据见 [CSV](docs/bench/data/2026-11-02-runs.csv)。

## 回退

```sh
# 立即切回 cubic（不卸载模块）
su -c 'sysctl -w net.ipv4.tcp_congestion_control=cubic'

# 永久移除
# KernelSU 管理器禁用/删除 tcp_bbr_kmod，然后重启
```

## 兼容性 / 注意事项

- 本项目的方法适用于任何 **有 KernelSU 系 root + 内核未编 BBR + 厂商未开源** 的 GKI 设备；
- 但 `prebuilt/` 里的 `.ko` 只对上述 K80 内核有效，其他设备/内核必须重新编译；
- 系统 OTA / 内核升级后 ABI 可能变化，**升级前先禁用模块**，升级后按新版本重编；
- `fq` 会替换厂商默认的 `mq/pfifo_fast` qdisc；如果遇到网络异常，可先去掉 `service.sh` 里的 fq 部分；
- 模块加载后 `lsmod` 的引用计数不为 0 是正常的（每个使用 BBR 的 socket 都会持有引用）。

## 许可证

内核模块来源于 Linux 内核（ACK `android14-6.1.138_r00`）的 `net/ipv4/tcp_bbr.c`，按 **GPL-2.0** 分发；脚本与文档同为 GPL-2.0。预编译二进制对应的源码可由构建脚本按标签获取，未做修改。

## 免责声明

修改内核运行时行为存在风险。本项目仅在你自行承担风险的前提下提供，作者不对设备损坏、数据丢失或网络异常负责。务必保留可恢复手段，并在升级系统前禁用模块。
