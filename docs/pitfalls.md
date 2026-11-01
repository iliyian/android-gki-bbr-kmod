# 踩坑记录

本文记录从“想给 K80 开 BBR”到“稳定开机自启”之间实际踩到的坑。很多问题不会报错，只会表现为随机重启或网络异常，因此建议完整阅读。

测试环境：

```
设备      Redmi K80 (zorn / 24117RK2CC)
系统      HyperOS 3 / Android 16
内核      6.1.138-android14-11-g965475777129-mi
Root      SukiSU Ultra 4.3.0（LKM 模式，UAPI 6）
KMI       android14-6.1
```

---

## 坑 1：不能用 kernel.org 的 stable 6.1.138

版本号里的 `-android14-11-g965475777129` 说明它是 **Android Common Kernel（ACK）**，不是 mainline；`-mi` 是小米补丁。

先看实测差异（设备 BTF vs 用 stable 源码 + 设备 config 编译）：

| 结构体 | 设备 | stable 6.1.138 | 差异 |
|---|---:|---:|---:|
| `sock` | 856 | 768 | 88 |
| `inet_sock` | 1088 | 984 | 104 |
| `inet_connection_sock` | 1536 | 1392 | 144 |
| `tcp_sock` | 2368 | 2200 | 168 |
| `module` | 1088 | 1024 | 64 |

原因是 ACK 在结构体里加了字段：

- `android_kabi_reserved1..N`（`CONFIG_ANDROID_KABI_RESERVE`）
- `android_oem_data1`（`CONFIG_ANDROID_VENDOR_OEM_DATA`）
- `sk_wait_pending` 等 ACK 回移字段
- `struct module` 的 `btf_data_size` / `btf_data`（见坑 2）

这些字段会让 BBR 访问的 `tcp_sock` 字段整体错位。加载后可能不立刻崩溃，但在收发数据、重传、RTT 采样时随机 panic。

**正确做法：** 使用 ACK 对应标签，例如：

```
android14-6.1.138_r00  (commit 4894546, TUNA AOSP 镜像可拉)
```

> 注意：`uname -r` 里的 `g965475777129` 是小米构建树的 commit，不在公开 ACK 仓库里；不要试图按这个 hash 去找源码。

---

## 坑 2：`CONFIG_DEBUG_INFO_BTF_MODULES` 与 `struct module`（最致命）

**现象：** `ksud insmod` 成功打印前，手机直接重启；pstore 为空，dmesg 没有上次启动日志。

**根因：** 为了绕开“BTF 需要 vmlinux”，最初把 `CONFIG_DEBUG_INFO_BTF` / `CONFIG_DEBUG_INFO_BTF_MODULES` 关了。结果：

```
struct module  设备: 1088 字节
               模块: 1024 字节
```

设备多出两个字段：

```
btf_data_size   offset 840
btf_data        offset 848
```

它们由 `CONFIG_DEBUG_INFO_BTF_MODULES` 控制。少了 16 字节后，内核按自己的布局读取模块的 `.gnu.linkonce.this_module`，`mod->init` 取到的是错误指针，跳到垃圾地址 → 立即重启。

**正确做法：**

- `.config` 里 **保留** `CONFIG_DEBUG_INFO_BTF=y` 和 `CONFIG_DEBUG_INFO_BTF_MODULES=y`（设备原本就是 y）；
- 编译模块时用 make 命令行变量关掉 BTF 编码/解析步骤（它们依赖本地 `vmlinux`，我们没编）：

```sh
make ARCH=arm64 LLVM=1 -j8 M="$MODDIR" \
    CONFIG_DEBUG_INFO_BTF= CONFIG_DEBUG_INFO_BTF_MODULES= modules
```

关键点：`include/generated/autoconf.h` 仍然定义这两个宏（C 代码看到它们，`struct module` 布局正确），而 Makefile 里的 BTF 生成步骤被空变量跳过。

**校验：**

```sh
llvm-readelf -S tcp_bbr.ko | grep this_module
# 期望大小 0x440 (1088)
```

---

## 坑 3：kCFI 与编译器版本

设备：

```
CONFIG_CFI_CLANG=y
# CONFIG_CFI_PERMISSIVE is not set   ← CFI 失败是致命的
CONFIG_LTO_CLANG_THIN=y
CONFIG_CC_VERSION_TEXT="Android (10087095, +pgo, +bolt, +lto, -mlgo,
  based on r487747c) clang version 17.0.2 ..."
```

BBR 的函数指针会通过 `struct tcp_congestion_ops` 被内核间接调用，kCFI 会检查函数入口的类型哈希。若模块编译器与内核不一致，理论上可能触发：

```
CFI failure (target: ...)
```

**做法：**

1. 用与内核相同的 AOSP clang：`r487747c`（17.0.2）；
2. 从 TUNA 的 `platform/prebuilts/clang/host/linux-x86` 仓库 `android14-release` 分支稀疏检出：

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

3. 验证哈希一致性（可选）：

```sh
./scripts/cfi-hash-test.sh
```

本项目实测 clang 17.0.2 与 clang 18.1.3 对 `tcp_congestion_ops` 的 7 个原型生成完全相同的 kCFI 哈希；即便如此，仍建议使用内核同版本编译器，避免其他 IR/ABI 差异。

---

## 坑 4：普通 `insmod` 不行，用 `ksud insmod`

直接 `insmod` 会失败：

- vermagic 不匹配（模块是我们自己编的，没有 `-mi` 后缀）；
- `CONFIG_MODVERSIONS=y`，但厂商没有提供 `Module.symvers`，CRC 对不上；
- 部分符号可能未导出。

SukiSU / KernelSU 的 `ksud insmod` 不是简单 insmod。它的用户态加载器（`userspace/ksuinit/src/lib.rs`）会：

1. 解析 ELF 的全部未定义符号；
2. 通过 `KSU_IOCTL_LOOKUP_SYMBOLS`（UAPI 6）或 `/proc/kallsyms` 取地址；
3. 把符号表项改写为 `SHN_ABS` + 真实地址，内核加载时不再走导出表/CRC 检查；
4. `init_module` 失败且日志提示 vermagic 不符时，从 kmsg 提取期望值、原地替换 `.ko` 的 vermagic 后重试。

所以加载命令是：

```sh
su -c 'ksud insmod /data/adb/modules/tcp_bbr_kmod/tcp_bbr.ko'
```

成功输出：`Loaded kernel module: ...`

**注意：** 这并不能绕过 ABI 和 CFI。结构体布局不对，依然会崩（见坑 1、2）。

---

## 坑 5：不要为了 BBR 去刷通用 GKI 内核

本设备实测：

- 通用 GKI 6.1.145（带 SukiSU/SUSFS/KPM）：能启动，但 HyperOS 3 显示栈闪屏，vendor HWC/DRM 反复 `COMMIT failed: 11`；
- 其它 zorn 专用树（6.1.176）同样闪屏。

原因是显示栈依赖厂商 `6.1.138-mi` 的 vendor 模块/接口。**只加载一个 `.ko` 不碰显示栈，是风险最低的路径。**

---

## 坑 6：构建配置里的雷

### 6.1 `TRIM_UNUSED_KSYMS`

设备 config 里：

```
CONFIG_TRIM_UNUSED_KSYMS=y
CONFIG_UNUSED_KSYMS_WHITELIST="abi_symbollist.raw"
```

源码树里没有 `abi_symbollist.raw`，`make prepare` 会报：

```
ERROR: '.../abi_symbollist.raw' whitelist file not found
```

处理：

```sh
scripts/config --disable TRIM_UNUSED_KSYMS
scripts/config --set-str UNUSED_KSYMS_WHITELIST ""
```

这不影响模块 ABI。

### 6.2 模块压缩

`CONFIG_MODULE_COMPRESS_*` 会产出 `.ko.zst`/`.ko.xz`，`ksud insmod` 需要原始 `.ko`，建议关闭。

### 6.3 `include/generated/asm-offsets.h` 缺失

`modules_prepare` 不一定生成它，缺少时编译报：

```
clang: error: invalid value '' in 'mstack-protector-guard-offset='
```

补：

```sh
make ARCH=arm64 LLVM=1 prepare
make ARCH=arm64 LLVM=1 arch/arm64/kernel/asm-offsets.s
```

### 6.4 `scripts/module.lds` 缺失

```
没有规则可制作目标“scripts/module.lds”
```

补：

```sh
make ARCH=arm64 LLVM=1 scripts/module.lds
```

### 6.5 `WERROR`

设备 config 有 `CONFIG_WERROR=y`。换编译器后可能因新警告中断，构建脚本里已关闭：

```sh
scripts/config --disable WERROR
```

---

## 坑 7：fq 与 netd

厂商默认 qdisc：

```
wlan0      : mq + pfifo_fast
rmnet_data : mq + pfifo_fast
```

BBR 依赖 pacing。虽然内核有内部 pacing，但配合 `fq` 效果更好。

```sh
tc qdisc replace dev wlan0 root fq
tc qdisc replace dev rmnet_data1 root fq
```

注意：

- `mq` 设备的子队列不能直接挂 fq，必须替换 **root**；
- `net.core.default_qdisc=fq` 对多队列设备不生效（内核仍默认 `mq`），要逐接口 `tc replace`；
- Android netd 在网络切换时可能重建 qdisc，本项目用 30 秒看门狗重新挂上；
- 替换后建议跑一次真实下载确认网络正常。

---

## 坑 8：如何确认 ABI 真的对

不要“加载没崩就算对”。正确姿势：

1. 从手机拉 `/sys/kernel/btf/vmlinux`；
2. 用 `pahole --btf_encode_detached` 从模块 DWARF 生成 BTF；
3. 两边 `bpftool btf dump ... format raw`；
4. 用 `scripts/verify-layouts.py` 对比关键结构体。

务必全部一致：

```
tcp_sock                  2368
tcp_congestion_ops         192
rate_sample                 72
sock                       856
inet_sock                 1088
inet_connection_sock      1536
module                    1088
```

详见 [verification.md](verification.md)。

---

## 坑 9：系统 OTA / 内核升级

模块与内核强绑定。HyperOS 更新内核后：

- vermagic 变化（ksud 可处理）；
- **结构体布局可能变化**（不能自动处理）；
- 新内核可能已经内置 BBR。

**升级前先禁用 `tcp_bbr_kmod`**，升级后重新构建并用 BTF 校验。

---

## 其他小坑

- `lsmod` 里 `tcp_bbr` 的引用计数不为 0 是正常的：每个使用 BBR 的 socket 都持有模块引用。
- panic 后 `/sys/fs/pstore` 可能是空的；不要指望一定能拿到 last_kmsg。定位主要靠 ABI 对比和逐步二分。
- Windows 下用 Git Bash 调 adb 时注意 `MSYS_NO_PATHCONV=1`，否则 `/data/...` 会被改写成本地路径。
- 设备 `CONFIG_KALLSYMS_ALL=y`，`ksud` 的符号解析更完整；若其他设备关闭该选项，部分非导出符号可能无法解析。
