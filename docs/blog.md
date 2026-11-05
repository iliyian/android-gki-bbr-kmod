# 在 Redmi K80 上用内核模块启用 BBR

设备：Redmi K80（zorn / 24117RK2CC）  
系统：HyperOS OS3.0.309.0.WOKCNXM / Android 16  
内核：`6.1.138-android14-11-g965475777129-mi`  
Root：SukiSU Ultra 4.3.0（LKM 模式）

结果：BBR 和 fq 已经在这台机器上跑起来，重启后自动恢复。没有刷内核，没有等小米开源，也没有出现 GKI 的闪屏问题。

这篇文章把整个过程记录下来：怎么确认内核没有 BBR、为什么普通 `insmod` 不行、怎么找到匹配的源码和编译器、怎么避免“加载就重启”，以及最后怎么把它做成开机自启的 KernelSU 模块。

## 先说说 BBR 是什么

BBR 是 Google 在 2016 年提出的 TCP 拥塞控制算法。常见的 cubic 靠丢包判断拥塞：网络一开始丢包，它就大幅降低发送速度。BBR 不这么判断，它估算链路能提供多少带宽、往返时间是多少，再按照这个模型控制发送节奏。

这个区别在弱网、高延迟、有一定随机丢包的链路上比较明显。BBR 通常能让发送端维持更高的吞吐，同时把队列压短，延迟不容易堆起来。它也不是“提速挂”：峰值带宽不会凭空变高，下载速度更多取决于服务器那边的拥塞控制和链路本身。

Android 内核不一定编了 BBR。很多厂商配置只保留 `reno` 和 `cubic`，省编译体积，也少一些行为变量。这台 K80 就是这种情况。

## 检查内核支持情况

先看当前可用的拥塞控制算法：

```sh
$ cat /proc/sys/net/ipv4/tcp_available_congestion_control
reno cubic

$ cat /proc/sys/net/ipv4/tcp_congestion_control
cubic
```

再看内核配置：

```sh
$ zcat /proc/config.gz | grep TCP_CONG
CONFIG_TCP_CONG_CUBIC=y
CONFIG_DEFAULT_TCP_CONG="cubic"
# CONFIG_TCP_CONG_ADVANCED is not set
```

`CONFIG_TCP_CONG_ADVANCED` 没开，BBR 根本没有参与编译。`/vendor/lib/modules` 里也找不到 `tcp_bbr.ko`，内核符号里同样没有 `bbr`。

也就是说，不是“有个开关没打开”，而是这个算法不在内核里。

## 为什么不能直接编内核

常规做法是编一个带 BBR 的完整内核，刷进 boot 分区。这里有两个问题。

第一，小米没有发布 `6.1.138-mi` 的源码。MiCode 上只有更早的 `bsp-zorn-v-oss`（6.1.68 / Android 15），没有 Android 16 对应的分支。

第二，刷通用 GKI 或第三方内核会破坏 HyperOS 的显示栈。这台机器试过通用 GKI 6.1.145 和第三方 zorn 6.1.176 内核，都能启动，但屏幕会闪，vendor 的 HWC/DRM 反复报 `COMMIT failed: 11`。当时看起来像是显示 vendor 模块和内核版本绑死，换内核就会出问题。

> **2026-11-05 更正**：这个判断只对了一半。闪屏不是“新内核一定不兼容”，而是 stable 6.1.141 引入的一个 DRM 校验和 zorn vendor `msm_drm` 的 `possible_clones` 冲突；回退该检查后 6.1.176 显示正常。详见文末“6.1.176：显示兼容性与双 BBR”。

所以完整内核这条路在当时看来走不通，但后来证明它是可修的，不是死路。

## 第三条路：运行时加载模块

完整内核不能换，但内核本身是支持模块的。如果能编出一个 `tcp_bbr.ko`，在运行时加载，就不需要碰 boot 分区，也不会影响显示栈。

问题是，普通 `insmod` 不会接受这个模块。

模块和内核之间有三道检查：

1. **vermagic**：模块编译时的内核版本字符串必须和运行中的内核一致；
2. **MODVERSIONS**：如果内核开了 `CONFIG_MODVERSIONS`，模块里每个外部符号都要带 CRC，内核会逐个校验；
3. **符号导出**：模块引用的符号必须在内核的导出表里。

这台机器的内核开了 `CONFIG_MODVERSIONS=y`，厂商又没有提供 `Module.symvers`，自己编的模块 CRC 对不上；vermagic 也会因为少了 `-mi` 后缀而不匹配。

直接 `insmod` 在第一道或第二道检查就会失败。

## ksud insmod 做的事

SukiSU 4.3.0 的 `ksud insmod` 不是简单调 `init_module`。它自带一个用户态模块加载器，大致流程是：

1. 读取 `.ko`，解析 ELF 里的未定义符号；
2. 通过内核符号查询接口（UAPI 6）或 `/proc/kallsyms` 找到每个符号的地址；
3. 把符号表项改写成 `SHN_ABS`，值设成真实地址；
4. 调用 `init_module`；
5. 如果内核因为 vermagic 不匹配拒绝加载，就从 kernel log 里读出期望的 vermagic，改写 `.ko` 里的字符串，再重试一次。

符号被改写成绝对地址后，内核加载模块时不会再走导出表和 CRC 校验，vermagic 也被改成了设备实际的值。这就是它能加载“来路不明”的 `.ko` 的原因。

手机上的 `vpnhide_kmod.ko` 是个现成的例子。它的 vermagic 写着 `6.1.166-dirty`，在这台 `6.1.138-mi` 上照样加载并工作。

但要注意，`ksud insmod` 只解决符号和版本检查。结构体布局不对、CFI 哈希不对，模块仍然会崩，而且可能是加载瞬间就重启，连日志都来不及留下。

## 第一关：源码必须是 ACK

第一版模块是用 kernel.org 的 stable 6.1.138 编的。编译没问题，但把设备 BTF 和模块 BTF 一对比，结构体尺寸全对不上：

| 结构体 | 设备 BTF | stable 6.1.138 | 差值 |
|---|---:|---:|---:|
| `sock` | 856 | 768 | 88 |
| `inet_sock` | 1088 | 984 | 104 |
| `inet_connection_sock` | 1536 | 1392 | 144 |
| `tcp_sock` | 2368 | 2200 | 168 |
| `module` | 1088 | 1024 | 64 |

内核版本号是 `6.1.138-android14-11-g965475777129-mi`。这里的信息不止“6.1.138”：

- `6.1.138` 是上游稳定版号；
- `-android14-11` 表示它是 Android Common Kernel（ACK）的 android14-6.1 分支；
- `-g965475777129` 是构建树的 commit；
- `-mi` 是小米自己的补丁。

ACK 不是 mainline。它包含 Android 需要的补丁，结构体里也多了一些字段：

- `android_kabi_reserved*`，由 `CONFIG_ANDROID_KABI_RESERVE` 控制；
- `android_oem_data1`，由 `CONFIG_ANDROID_VENDOR_OEM_DATA` 控制；
- `sk_wait_pending` 这类 ACK 回移字段；
- `struct module` 里的 `btf_data_size` / `btf_data`。

`tcp_sock` 整体偏移了 168 字节。BBR 读 `snd_cwnd`、`delivered`、`app_limited` 时会落在错误的位置。这种错误不一定会立刻崩，可能在某次重传或 RTT 采样时才触发。

所以源码要换成 ACK 自己的标签。我用的是 TUNA 镜像上的 GKI 发布标签：

```sh
git fetch --depth 1 origin refs/tags/android14-6.1.138_r00
git checkout FETCH_HEAD
```

验证一下版本：

```sh
head -5 Makefile
# VERSION = 6
# PATCHLEVEL = 1
# SUBLEVEL = 138
```

`uname -r` 里的 `g965475777129` 是小米构建树的 commit，不在公开 ACK 仓库里。拿这个 hash 去找源码是找不到的。

## 第二关：编译器和内核一致

设备配置里有这一行：

```
CONFIG_CC_VERSION_TEXT="Android (10087095, +pgo, +bolt, +lto, -mlgo,
  based on r487747c) clang version 17.0.2 ..."
```

内核还开了：

```
CONFIG_CFI_CLANG=y
# CONFIG_CFI_PERMISSIVE is not set
CONFIG_LTO_CLANG_THIN=y
```

BBR 的函数指针会通过 `struct tcp_congestion_ops` 被内核间接调用。kCFI 会在间接调用点检查目标函数入口的类型哈希。哈希不匹配时，因为 `CFI_PERMISSIVE` 没开，内核会直接 panic。

这意味着编译器不能随便选。最好用和内核完全相同的 AOSP clang `r487747c`（17.0.2）。

可以从 TUNA 的 `platform/prebuilts/clang/host/linux-x86` 仓库稀疏检出：

```sh
git clone --filter=blob:none --no-checkout --depth 1 \
  https://mirrors.tuna.tsinghua.edu.cn/git/AOSP/platform/prebuilts/clang/host/linux-x86.git clangpre
cd clangpre
git fetch --depth 1 origin android14-release
git sparse-checkout init --cone
git sparse-checkout set clang-r487747c
git checkout FETCH_HEAD
export PATH=$PWD/clang-r487747c/bin:$PATH
```

这个目录大约 2.3GB。

我额外比较过 clang 17.0.2 和 18.1.3 对 `tcp_congestion_ops` 那组函数原型生成的 kCFI 哈希，结果相同。但不同版本的 IR、ABI 细节仍可能有差异，能用内核同版本就用同版本。

## 第三关：struct module 少了 16 字节

这是整个过程里最麻烦的一处，也是“加载就重启”的真正原因。

为了绕开 BTF 编码需要本地 vmlinux 的问题，我一开始把这两个选项关掉了：

```
CONFIG_DEBUG_INFO_BTF
CONFIG_DEBUG_INFO_BTF_MODULES
```

模块编译成功，`ksud insmod` 也走到了加载阶段，然后手机直接重启。`/sys/fs/pstore` 是空的，`/proc/last_kmsg` 也没有，dmesg 里没有上一次启动的记录。

没有日志，只能从 ABI 入手。把设备 BTF 和模块 BTF 拉出来逐字段对比，看到：

```
struct module   设备: 1088 字节
                模块: 1024 字节
```

设备多出两个 8 字节字段：

```
btf_data_size   offset 840
btf_data        offset 848
```

它们由 `CONFIG_DEBUG_INFO_BTF_MODULES` 控制。模块里少了这 16 字节，内核仍然按自己的 `struct module` 布局读取模块的 `.gnu.linkonce.this_module`。`mod->init` 取到的是错误的指针，内核跳到无效地址，手机就重启了。

修复方法不是简单地重新打开 BTF。打开之后，编译模块会尝试生成 BTF，而这又需要本地编译出带 BTF 的 vmlinux，工作量很大。

实际做法是：让宏参与 C 编译，但跳过 make 里的 BTF 编码步骤。

```sh
make ARCH=arm64 LLVM=1 -j8 M="$MODDIR" \
    CONFIG_DEBUG_INFO_BTF= CONFIG_DEBUG_INFO_BTF_MODULES= modules
```

这样 `include/generated/autoconf.h` 里仍然定义了这两个宏，C 代码看到的 `struct module` 布局是正确的；而 make 命令行上的空变量会让 BTF 编码那一步不执行。

检查模块里的 section：

```sh
llvm-readelf -S tcp_bbr.ko | grep this_module
# 期望大小 0x440 (1088)
```

这一点很反直觉：**为了模块能加载，反而不能关掉 BTF 相关的配置**。配置决定结构体布局，构建步骤才决定要不要生成 BTF 数据，两者要分开处理。

## 其他构建坑

除了上面三个大头，构建过程中还踩了几个小坑。

### TRIM_UNUSED_KSYMS

设备配置里有：

```
CONFIG_TRIM_UNUSED_KSYMS=y
CONFIG_UNUSED_KSYMS_WHITELIST="abi_symbollist.raw"
```

源码树里没有 `abi_symbollist.raw`，`make prepare` 会直接报：

```
ERROR: '.../abi_symbollist.raw' whitelist file not found
```

处理方式是关掉裁剪：

```sh
scripts/config --disable TRIM_UNUSED_KSYMS
scripts/config --set-str UNUSED_KSYMS_WHITELIST ""
```

这不影响模块的 ABI。

### asm-offsets.h 缺失

`modules_prepare` 不一定会生成 `include/generated/asm-offsets.h`。缺少它时，编译会报：

```
clang: error: invalid value '' in 'mstack-protector-guard-offset='
```

补上：

```sh
make ARCH=arm64 LLVM=1 prepare
make ARCH=arm64 LLVM=1 arch/arm64/kernel/asm-offsets.s
```

### scripts/module.lds 缺失

链接模块时可能报：

```
没有规则可制作目标“scripts/module.lds”
```

补：

```sh
make ARCH=arm64 LLVM=1 scripts/module.lds
```

### 模块压缩

`CONFIG_MODULE_COMPRESS_*` 会生成 `.ko.zst` 或 `.ko.xz`，而 `ksud insmod` 需要原始 `.ko`。构建脚本里把压缩关掉了。

### modpost 的 undefined 警告

没有 `Module.symvers` 时，`modpost` 会输出一堆：

```
WARNING: modpost: "tcp_register_congestion_control" [.../tcp_bbr.ko] undefined!
```

这是正常的。符号会在加载时由 `ksud insmod` 通过 kallsyms 解析，不需要在构建阶段解决。

## 用 BTF 校验 ABI

模块能加载不等于 ABI 正确。比较可靠的做法是对比设备 BTF 和模块 BTF。

1. 从手机拉 `/sys/kernel/btf/vmlinux`；
2. 用 `pahole --btf_encode_detached` 从模块 DWARF 生成 BTF；
3. 两边用 `bpftool btf dump ... format raw` 导出；
4. 逐字段比较。

关键结构体最终要全部一致：

| 结构体 | 设备 | 模块 |
|---|---:|---:|
| `tcp_sock` | 2368 | 2368 |
| `tcp_congestion_ops` | 192 | 192 |
| `rate_sample` | 72 | 72 |
| `sock` | 856 | 856 |
| `inet_sock` | 1088 | 1088 |
| `inet_connection_sock` | 1536 | 1536 |
| `module` | 1088 | 1088 |

只要出现 `only-in-device`、`only-in-module` 或 `MISMATCH`，就不要加载。先检查源码、配置和编译器，不要用“加载后没崩”当 ABI 正确的证据。

仓库里的 `scripts/verify-layouts.py` 就是做这件事的。

## 加载和 qdisc

加载：

```sh
su -c 'ksud insmod /data/adb/modules/tcp_bbr_kmod/tcp_bbr.ko'
su -c 'sysctl -w net.ipv4.tcp_congestion_control=bbr'
```

确认模块注册成功：

```sh
su -c 'cat /proc/sys/net/ipv4/tcp_available_congestion_control'
# reno cubic bbr
```

厂商默认的 qdisc 是 `mq + pfifo_fast`。BBR 有内部 pacing，但配合 `fq` 效果更好：

```sh
su -c 'tc qdisc replace dev wlan0 root fq'
su -c 'tc qdisc replace dev rmnet_data1 root fq'
```

多队列设备上，`net.core.default_qdisc=fq` 不会覆盖 `mq` 的默认值，所以必须逐接口替换。

替换后并没有把硬件队列“压成单队列”。Linux 6.1 的 `qdisc_restart()` 仍然用 `skb_get_tx_queue(dev, skb)` 根据 `queue_mapping` 选择硬件队列。变化的是软件调度层：多个 TX 队列共享同一个 fq 实例，所有队列的 enqueue/dequeue 走同一把 qdisc 锁。

手机上这个代价可以接受。Wi-Fi 和蜂窝的速率远没到单 qdisc 锁成为瓶颈的量级，而 fq 的 pacing 和按流排队对 BBR 更有用。

最后把它做成 KernelSU 模块：

- `post-fs-data.sh`：尽早加载 `.ko`；
- `service.sh`：设置 `bbr` 和 `fq`；
- 后台看门狗：每 30 秒检查一次 qdisc，防止 netd 在网络切换时把 fq 重置回 `pfifo_fast`。

重启后确认过：默认 CC 是 bbr，相关接口的 qdisc 是 fq，看门狗正常挂在 init 下。

## 这套方案的效果

BBR 主要改善的是满载时的排队延迟，峰值吞吐没有稳定提升。体感上的变化更多来自响应速度，而不是测速数字。

如果目标是“下载更快”，换手机端拥塞控制通常收益有限，因为下载方向由服务器决定。上传、代理隧道、热点共享这类手机作为发送端的场景，BBR 的作用更直接。

### 怎么测的

2026-11-02 下午到晚上，在浙江联通 5G 上做了对比，晚上 22:42–22:56 又补了一轮晚高峰。设备是 Redmi K80，内核 6.1.138，模块就是仓库里的 `tcp_bbr.ko`（BBRv1）。室内校园环境，RSRP 一直在 -100 dBm 出头，SINR 在不同轮次从 -1 到 14 都有。

对照组只有两组：

| 组 | 拥塞控制 | qdisc |
|---|---|---|
| A | cubic | mq + pfifo_fast（厂商默认） |
| C | bbr | fq |

只改手机端，服务端不动。测速服务器三台：netcup Manassas（约 290 ms）、阿里云日本（约 110 ms）、阿里云北京（默认配置、未调参，约 70 ms）。测速服务端用的是 [iliyian/minimal-speedtest](https://github.com/iliyian/minimal-speedtest)。每轮新起 `curl` 连接，ABBA 交替，轮间 3 秒；下载 32 MB，上传 8 MB。测试时暂停模块的 30 秒 qdisc 看门狗，每轮用 `tc` 读回 qdisc、用 `ss` 确认 socket 真的用了当轮 CC。

下载方向客户端只能看到接收字节，`ss` 里的 `cwnd` / `delivery_rate` 是发送端字段，在手机上是冻结的；所以下载只看应用层吞吐、压载 ping 和网卡字节计数。

### 下载

三台服务器都没有测出可复现的差异：

| 服务器 | CUBIC | BBR | 配对中位差 | BBR 胜 |
|---|---:|---:|---:|---:|
| netcup Manassas | 31.50 Mbps | 30.45 Mbps | -0.7% | 3/6 |
| 阿里云日本 | 约 42.2 Mbps | 约 42.2 Mbps | 几何 +2.1% | 4/5 |
| 阿里云北京（正常信号） | 24.59 Mbps | 24.59 Mbps | -17.6% | 2/6 |
| 阿里云北京（晚高峰） | 20.45 Mbps | 22.75 Mbps | +2.2% | 3/6 |

北京正常信号那行两臂中位数完全相同，但 BBR 组有两轮掉到 8.6 / 10.5 Mbps，逐对差异从 -57% 到 +29%，方向并不稳定。晚高峰那行两臂中位只差 2.2%，逐对从 -54% 到 +236%，同样没有稳定方向。日本那台是剔除一轮信号骤降和一轮电话污染之后的数据。

北京还测到过一轮 SINR 0–6 的弱信号：BBR 组下载中位高 26%，但逐对从 -52% 到 +102%。这种条件下，结果主要取决于无线侧的重传和调度，不能当成 BBR 的收益。

原因不复杂：下载时手机是接收方，拥塞控制在服务端。手机端换 CC 改变不了服务端怎么发；它只能影响 ACK 的路径。想让下载真正吃到 BBR，得在服务器上开。

### 上传

上传方向一致得多。手机是发送端，CC 真正生效。BBR 的吞吐没有优势，但发送队列明显更短：

| 服务器 | CUBIC | BBR | 发送侧 RTT | cwnd |
|---|---:|---:|---:|---:|
| netcup Manassas | 4.97 Mbps | 3.52 Mbps | 866 → 670 ms | 1309 → 376 段 |
| 阿里云日本 | 6.92 Mbps | 6.97 Mbps | 443–683 → 190–214 ms | 923–1046 → 135–206 段 |
| 阿里云北京（正常信号） | 5.46 Mbps | 4.40 Mbps | 804 → 193 ms | 883 → 76 段 |
| 阿里云北京（晚高峰） | 1.66 Mbps | 1.10 Mbps | 3983 → 321 ms | 809 → 46 段 |

北京正常信号那组最直观：上传压载 ping 的 p95 从 237 ms 降到 64 ms，吞吐低约 15%。晚高峰更夸张：上传只有 1–2 Mbps，CUBIC 的发送侧 RTT 到 3.98 s、重传 4.09%，BBR 是 321 ms、0.99%；吞吐低约 12%，但延迟和丢包好很多。日本那组吞吐基本打平，延迟去掉 250–480 ms。netcup 因为 RTT 高、路径更拥塞，BBR 吞吐吃亏最多，但 RTT 还是从 866 ms 降到 670 ms。

这和 BBR 的工作方式对得上：它按估计带宽 pacing，不把缓冲区填满；CUBIC 靠丢包反馈加窗，容易把深缓冲的蜂窝上行塞满。吞吐和延迟在这条路上是取舍关系。

### 一次丢包事件

测试期间来了一通 3 秒的拒接电话。那一轮 BBR 的重传率升到 5.32%，发送开销 7.3%，吞吐从约 7 Mbps 掉到 3.9 Mbps。BBRv1 遇到丢包会退让，这次掉得很明显；弱信号或链路抖动时，它不一定比 CUBIC 好。

### 结论

放到这台手机上，BBR 的表现和它的设计目标一致：它不负责把峰值带宽拉高，而是把排队延迟压下来。

最直接的收益在上传。北京那台默认配置的服务器上，发送侧 RTT 从 804 ms 降到 193 ms，上传压载 ping 的 p95 从 237 ms 降到 64 ms，吞吐只低约 15%；晚高峰时 CUBIC 的 RTT 到 4 秒、重传 4%，BBR 是 0.3 秒、1%，吞吐只差约 12%；日本那台吞吐基本打平，延迟去掉 250–480 ms。视频通话、远程操作、热点共享这类场景，体感会比 CUBIC 更跟手。

下载方向没有可复现的提升，也没有稳定的退化。原因前面说过：下载由服务端 CC 决定，手机端改 BBR 本来就不该指望它提速；真正能让下载变快的是服务端那一侧的配置。

能在不改内核、不刷机的情况下拿到这个延迟收益，这套模块已经达到目标。如果你更在意上传峰值吞吐，临时切回 CUBIC 只是一条 `sysctl`；要长期保留 CUBIC 就禁用模块。如果在意响应速度和上传时的延迟，BBR 值得保留。

这些数据只对应 BBRv1、这条联通线路和这三台服务器；换网络、换服务端或换内核版本，结论可能不同。完整的测试方法、逐轮数据和限制条件在仓库里：

- 报告：[docs/bench/2026-11-02-bbr-vs-cubic.md](bench/2026-11-02-bbr-vs-cubic.md)
- 逐轮 CSV：[docs/bench/data/2026-11-02-runs.csv](bench/data/2026-11-02-runs.csv)
- 逐采样：[ss](bench/data/2026-11-02-samples-ss.csv) / [ping](bench/data/2026-11-02-samples-ping.csv)
- 仓库：https://github.com/iliyian/k80-zorn-bbr

## BBRv2 和 BBRv3

顺手评估过这两个版本。

**BBRv3 不能作为模块使用。** 它依赖新内核的核心字段和函数：

- `struct rate_sample` 新增 `tx_in_flight`、`is_ece`、`is_acking_tlp_retrans_seq`，这些值由核心 TCP 栈填充；
- `struct tcp_sock` 新增 `fast_ack_mode`、`tlp_orig_data_app_limited`；
- `tcp_plb_*`、`tcp_skb_tx_in_flight_is_suspicious` 等新函数；
- `ICSK_CA_PRIV_SIZE` 从 104 字节增加到 144 字节。

这些都要改内核头文件并重新编译整个内核，模块做不到。Google 的 v3 分支本身是一整棵内核树。

**2026-11-04 更新：BBRv3 已经在 ACK 6.1.138 上完成内核移植并真机验证。**
`bbr3` 注册成功、可切换，开机/音频/模块加载正常。移植过程、核心栈补丁、
BTF/CRC 验证和踩坑记录见 [`bbrv3/README.md`](https://github.com/iliyian/k80-zorn-bbr/tree/main/bbrv3)。
"不能作为模块" 这一条仍然成立——它是内核 patchset，不是 `.ko`。

**BBRv2 的情况不同。** 拿 `v2alpha-2022-08-28` 的 `tcp_bbr.c` 对着 ACK 6.1.138 编译，只需要删掉已废弃的 `.tso_segs` 字段；`sizeof(struct bbr)` 是 104 字节，刚好等于 6.1 的 `icsk_ca_priv`，`BUILD_BUG_ON` 能通过。它只使用 6.1 已有的字段，不需要改核心栈。

但 v2 是 2022 年的实验分支，没有进主线，手机上的表现也没有验证。它还需要改名为 `bbr2`，才能和现有的 `bbr` 并存。这个方向可以继续试，但这次没有部署。

## BBRv3 实测：延迟有收益，吞吐没有

2026-11-04 深夜到 11-05 凌晨，在已经移植好 `bbr3` 的同一台 K80 上补了一组 A/B。这次只做评测，内核和补丁都没有改动；网络换成了默认数据卡中国电信，测试时回落到 LTE（下载 RSRP −87 dBm / SNR 24，上传 −94 / SNR 17），不再是 11-02 那次的联通 5G。服务端仍是阿里云北京那台默认配置的测速机。

对照组和上次一致：

| 组 | 拥塞控制 | qdisc |
|---|---|---|
| A | cubic | mq + pfifo_fast（厂商默认） |
| C | bbr3 | fq |

每轮新起 `curl`，ABBA 交替，轮间 3 秒；下载 32 MiB × 6 对，上传 8 MiB × 4 对；测试时暂停 `bbr3_fq` 模块的看门狗，逐轮用 `tc` 读回 qdisc、用 `ss` 确认 socket 真的用了当轮 CC、检查路由没有经过 `tun`。用的是原版 `cc_bench.sh`，没有改 harness。

### 下载

| | cubic + mq | bbr3 + fq |
|---|---:|---:|
| 中位 | 28.9 Mbps | 32.8 Mbps |
| 平均 | 28.3 Mbps | 32.2 Mbps |
| 配对中位差 | — | +15.8%，4/6 胜 |

第 4 对 cubic 只有 14.8 Mbps（其余 28–39 Mbps），剔除后配对中位只剩 +2.9%，方向不稳。下载方向手机只是接收方，拥塞控制在服务端；客户端 bbr3 只影响 ACK，差异不能归因。

### 上传

吞吐没有稳定差异：

| 对次 | cubic | bbr3 | 差值 |
|---|---:|---:|---:|
| 1 | 2.11 Mbps | 2.04 Mbps | −3.5% |
| 2 | 2.72 Mbps | 3.18 Mbps | +17.1% |
| 3 | 1.60 Mbps | 2.10 Mbps | +31.3% |
| 4 | 3.56 Mbps | 1.97 Mbps | −44.6% |

配对中位 +6.8%，几何平均 −4.8%，2/4 胜；两臂中位是 2.41 vs 2.07 Mbps。吞吐没有可复现的收益。

队列指标则是完全分离：

| 指标 | cubic | bbr3 |
|---|---:|---:|
| 发送侧 RTT p50 | 1491 ms | **197 ms** |
| 发送侧 RTT p95 | 2683 ms | **388 ms** |
| 发送侧 RTT p99 | 3084 ms | **515 ms** |
| cwnd p50 | 779 段 | **56 段** |
| cwnd p95 | 805 段 | **69 段** |
| unacked p50 | 340 段 | **50 段** |
| 上传压载 ping p50 | 80.5 ms | **36.7 ms** |
| 上传压载 ping p95 | 237 ms | **135 ms** |
| 样本 RTT > 1 s | 48–95% | 0%（首轮除外） |

逐轮看，cubic 的 RTT p95 是 1.9–4.8 s，bbr3 是 0.30–0.42 s（首轮除外）；cwnd p95 是 747–1069 段 vs 57–76 段（首轮 234）。cubic 的 p95 基本等于它的上限，bbr3 的 p95 是探测周期把 cwnd 从 ~50 推到 ~70。

4 对的样本量很小。队列指标 4/4 完全分离：配对符号翻转精确检验 p=0.125（n=4 能取到的最小值），配对 t 检验 p=0.0005–0.011（假设正态）；吞吐的配对检验 p=0.81–0.88。非配对置换会给队列指标 0.029，但这次是 ABBA 配对设计，那个数偏乐观，不能拿来当结论。

### 和 BBRv1 比

把 11-02 的 BBRv1 数据（联通 5G，12 对上传）用同样的稳态口径重算：

| 指标 | BBRv1 | BBRv3 |
|---|---:|---:|
| 发送侧 RTT p50 | 198 ms | 197 ms |
| 发送侧 RTT p95 | 368 ms | 388 ms |
| 发送侧 RTT p99 | 419 ms | 515 ms |
| cwnd p95 | 125 段 | 69 段 |
| 吞吐 vs cubic | 几何 −10.2% | 几何 −4.8% |
| 重传 | 0.45–3.17%（晚高峰） | 0.28% |

两者在延迟和吞吐上没有可分辨的差异；v3 的 cwnd 更保守一些。但这是跨天、跨运营商、跨内核的比较，不能当成严格的 v1-vs-v3 A/B。

### 为什么没有额外收益

- 移植用的是 2025-03 的 v3 快照，PLB / ecn_low 被裁掉；在这台机器上它们本来就是死代码，剩下的行为和 v1 很接近。
- v3 相对 v1 的主要改进是丢包恢复、ECN、PLB。这次 TCP 重传两边合计都是 69 段 / 0.28%，没有 RTO、没有 SYN 重传、没有 ECN，这些机制没有工作可做。
- 瓶颈是 LTE 上行调度，只有 2–3.5 Mbps。两版的 cwnd 都足够填满上行，多出来的 cwnd 只会变成排队。
- 单流、干净链路，BBR 的公平性/竞争性优势也体现不出来。

### 几个测量坑

- 下载方向是结构性无效：手机是接收端，CC 在服务端，手机端换什么都没法提高下载吞吐。
- 4 对样本要用配对检验；非配对置换会把 p 算小到 0.029，但 ABBA 设计应该按配对来。
- `ping` 的 `icmp_seq` 从 1 开始。按 seq 0 计算会凭空多出 0.5–2.9% 丢包；这次实际 ICMP 丢包是 0。
- cubic 的深队列会污染紧接其后的 bbr3 轮。第一对 bbr3 的 p95 是 1546 ms，后面三轮是 300–415 ms；3 秒冷却不够，最好等队列排空或多留几秒。
- `ss` 的最后一帧可能没有 `retrans:` 字段，判断重传要用 `/proc/net/snmp` 的前后差值。
- 接口和 VPN 会让旧脚本解析到错误网卡；这次最后路由直连到 `rmnet_data5`，脚本自己解析对了才跑对。

### 结论

BBRv3 在这条链路上的收益是队列和尾延迟，不是吞吐：上传 RTT 从 1.5 s 压到 0.2 s，p95 从 2.7 s 压到 0.4 s，cwnd 从 ~780 段压到 ~56 段，代价是吞吐持平到略低。和 BBRv1 比没有可分辨的额外提升；v3 的丢包恢复/ECN 在这条 0.28% 重传的链路上没有触发条件。

要回答“v1 vs v3”只能把官方 BBRv1 编进同一内核做严格 A/B；要验证 v3 的丢包恢复，需要在弱信号、切换或竞争流下测。这两项这次都没有做。

## 开源仓库

构建脚本、KernelSU 模块、ABI 对比工具和完整的踩坑记录都在：

https://github.com/iliyian/k80-zorn-bbr

测试用的测速服务端在另一个仓库：[iliyian/minimal-speedtest](https://github.com/iliyian/minimal-speedtest)。

仓库里有：

- `scripts/bench/`：实测 harness 和分析脚本；
- 一键构建脚本：拉 ACK、拉 AOSP clang、读取设备配置、编译、打包；
- KernelSU 模块模板：加载、sysctl、fq、看门狗；
- `verify-layouts.py`：对比设备 BTF 和模块 BTF；
- `cfi-hash-test.sh`：比较不同 clang 的 kCFI 哈希；
- 踩坑文档和 K80 实测记录；
- K80 对应的预编译 `.ko` 和模块 zip。

预编译产物只适用于 `6.1.138-android14-11-g965475777129-mi`。其他设备或内核必须重新编译，并重新做 BTF 校验。6.1.176 内核现在可以正常显示和加载模块，但仓库里的预编译 `.ko` 仍然只针对 6.1.138；换到 6.1.176 需要按新内核重新验证或编译。

构建脚本大致这样用：

```sh
CONFIG_GZ=$PWD/config.gz ./scripts/build-bbr-module.sh
```

`config.gz` 从设备导出：

```sh
adb exec-out su -c 'cat /proc/config.gz' > config.gz
```

脚本默认从 TUNA 拉 ACK 标签和 AOSP clang，也可以用自己的本地路径。

## 注意事项

1. 版本串里带 `-androidXX` 的是 ACK，不是 mainline，源码要用对应的 ACK 标签。
2. 编译器看 `CONFIG_CC_VERSION_TEXT`。开了 CFI 就用同版本 clang。
3. 不要关闭 `CONFIG_DEBUG_INFO_BTF*`，它会改变 `struct module` 布局。
4. 加载前对比 BTF，不要用“没崩”当 ABI 正确的证据。
5. `ksud insmod` 能绕过符号版本和 vermagic，但绕不过 ABI 和 CFI。
6. fq 要逐接口替换，多队列设备的 `default_qdisc` 不会自动生效。
7. 系统 OTA 前先禁用模块。内核更新后 ABI 可能变化，需要按新版本重新编译。
8. 立即回退：`sysctl -w net.ipv4.tcp_congestion_control=cubic`；永久关闭：禁用或删除 KernelSU 模块后重启。

## 6.1.176：显示兼容性与双 BBR

2026-11-05 把 6.1.176 的闪屏定位到了具体补丁，而不是“通用内核不行”。

### 闪屏根因

- stable **6.1.141** 引入上游 `41b4b11da0215`（`drm: Add valid clones check`）。`drm_atomic_helper_check_modeset()` 开始要求同一个 CRTC 上所有 encoder 的 `possible_clones` 互相覆盖。
- zorn 的 vendor `msm_drm.ko`（6.1.68 编）在 **CRTC 203** 上同时挂了 DSI 和 Virtual 两个 encoder（`encoder_mask=0x6`），它们的 `possible_clones` 不互反。于是 `drm_atomic_check_only()` 返回 `-EINVAL`，SDM 每帧重试失败：

```
[drm:drm_atomic_helper_check_modeset] crtc203 failed valid clone check for mask 0x6
SDM: drmModeAtomicCommit failed with error 22 (Invalid argument). crtc=203
SDM: COMMIT failed: 11
HwcComposer: presentOrValidateDisplay 0 presentFence:-1
```

- 版本边界：**6.1.138 / 139 / 140 没有这个检查，6.1.141 起才有**。所以之前把 6.1.145/6.1.176 的闪屏归因为“6.1.138 是显示上限”是过度结论；真正原因是这一个 stable 补丁。
- 回退 `drm_atomic_check_valid_clones()` 及其调用后，6.1.176 显示正常，`COMMIT failed` / `drmModeAtomicCommit failed` 从每帧一条降到 0。用户实测视觉正常。

### 双 BBR

- 6.1.176 上同时编入 BBRv1（`bbr`）和 BBRv3（`bbr3`）。BBRv3 用独立的 `net/ipv4/tcp_bbr3.c`，注册名 `bbr3`；BBRv1 保留，只把 `min_tso_segs` 适配成新的 `tso_segs(sk, mss_now)` 回调。
- 默认值改为：

```
CONFIG_DEFAULT_TCP_CONG="bbr3"
CONFIG_DEFAULT_NET_SCH="fq"
```

- 真机结果：`tcp_available_congestion_control = reno bbr bbr3 cubic`，默认 `bbr3`，`default_qdisc=fq`；`bbr` / `bbr3` 都能切换，并各自跑通 HTTP 200。
- 显示、audio、模块加载（501 个）、KSU root 都正常；`Failed to insmod=0`。

### 代价与后续

- BBRv3 改 `struct rate_sample` / `tcp_skb_cb` / `tcp_congestion_ops` 后，genksyms 会给约 691 个导出符号生成新 CRC，stock vendor 模块的 `__versions` 对不上。当前测试内核在 `check_version()` 里放行不匹配；这是**全局放宽**，后续应该收紧成按符号的 stock CRC 白名单，或用 `__GENKSYMS__` 把新字段对 genksyms 隐藏。
- 下一步验证 ThinLTO（stock 也是 ThinLTO），目标是在 6.1.176 上替代 LTO_NONE；编译时间/内存峰值和实际性能会单独记录。
- 细节、patch 和构建配置见 [`gki176/README.md`](../gki176/README.md) 和 [`BBRV3_PHONE_JOURNAL.md`](../bbrv3/docs/BBRV3_PHONE_JOURNAL.md) 第 20–21 节。
