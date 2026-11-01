# 从源码构建

本文是手动构建步骤，和 `scripts/build-bbr-module.sh` 做的事情一致。理解每一歩有助于排查问题。

## 0. 环境

- Linux（WSL2 Ubuntu 24.04 实测）
- 约 10GB 可用磁盘（ACK 源码 + clang + 构建产物）
- 网络能访问 TUNA 镜像（或自备 ACK 源码 / AOSP clang）
- 设备已 root，能 `adb exec-out su -c 'cat /proc/config.gz'`

依赖：

```sh
sudo apt install -y build-essential git curl python3 bc flex bison \
    libssl-dev libelf-dev kmod dwarves linux-tools-generic
```

> `pahole` 来自 `dwarves`，用于 BTF 校验；`linux-tools-generic` 提供能直接运行的 `bpftool`
>（WSL 自带的内核版本通常没有匹配的 linux-tools 包，调用 `/usr/lib/linux-tools/*/bpftool`）。

## 1. 获取 ACK 源码（不是 kernel.org stable）

```sh
export WORK=$HOME/.cache/android-gki-bbr
export ACK_TAG=android14-6.1.138_r00
export ACK_DIR=$WORK/ack-$ACK_TAG

mkdir -p "$ACK_DIR"
git -C "$ACK_DIR" init -q
git -C "$ACK_DIR" remote add origin \
    https://mirrors.tuna.tsinghua.edu.cn/git/AOSP/kernel/common.git
git -C "$ACK_DIR" fetch --depth 1 origin "refs/tags/$ACK_TAG"
git -C "$ACK_DIR" checkout -q FETCH_HEAD
```

验证：

```sh
head -5 "$ACK_DIR/Makefile"      # VERSION=6 PATCHLEVEL=1 SUBLEVEL=138
grep -n android_kabi_reserved "$ACK_DIR/include/linux/android_kabi.h"
grep -n sk_wait_pending "$ACK_DIR/include/net/sock.h"
```

## 2. 获取与内核一致的 AOSP clang

从设备配置或 `uname` 侧了解编译器版本（本项目是 `r487747c` / 17.0.2）：

```sh
export CLANG_DIR=$WORK/clang-r487747c
mkdir -p "$CLANG_DIR"
git -C "$CLANG_DIR" init -q
git -C "$CLANG_DIR" remote add origin \
    https://mirrors.tuna.tsinghua.edu.cn/git/AOSP/platform/prebuilts/clang/host/linux-x86.git
git -C "$CLANG_DIR" fetch --depth 1 origin android14-release
git -C "$CLANG_DIR" sparse-checkout init --cone
git -C "$CLANG_DIR" sparse-checkout set clang-r487747c
git -C "$CLANG_DIR" checkout -q FETCH_HEAD
export PATH="$CLANG_DIR/bin:$PATH"
clang --version | head -1
```

> 这一步会下载约 2.3GB。

## 3. 使用设备配置

```sh
adb exec-out su -c 'cat /proc/config.gz' > "$WORK/config.gz"
zcat "$WORK/config.gz" > "$ACK_DIR/.config"
```

## 4. 调整配置

```sh
cd "$ACK_DIR"
scripts/config --disable WERROR
scripts/config --enable TCP_CONG_ADVANCED
scripts/config --module TCP_CONG_BBR
scripts/config --disable TRIM_UNUSED_KSYMS
scripts/config --set-str UNUSED_KSYMS_WHITELIST ""
scripts/config --disable MODULE_COMPRESS_ZSTD
scripts/config --disable MODULE_COMPRESS_XZ
scripts/config --disable MODULE_COMPRESS_GZIP
make ARCH=arm64 LLVM=1 olddefconfig
```

**不要关闭** `CONFIG_DEBUG_INFO_BTF` / `CONFIG_DEBUG_INFO_BTF_MODULES`。设备是什么就保持什么；它们会影响 `struct module` 的布局。

确认：

```sh
grep -E '^CONFIG_(TCP_CONG_BBR|DEBUG_INFO_BTF|DEBUG_INFO_BTF_MODULES|LTO_CLANG|CFI_CLANG|MODVERSIONS)=' .config
```

## 5. 准备构建树

```sh
make ARCH=arm64 LLVM=1 -j$(nproc) prepare
make ARCH=arm64 LLVM=1 arch/arm64/kernel/asm-offsets.s
make ARCH=arm64 LLVM=1 scripts/module.lds
```

## 6. 编译模块

```sh
export MOD_DIR=$WORK/module-$ACK_TAG
rm -rf "$MOD_DIR"; mkdir -p "$MOD_DIR"
cp net/ipv4/tcp_bbr.c "$MOD_DIR/"
printf 'obj-m := tcp_bbr.o\n' > "$MOD_DIR/Makefile"

make ARCH=arm64 LLVM=1 -j$(nproc) M="$MOD_DIR" \
    CONFIG_DEBUG_INFO_BTF= CONFIG_DEBUG_INFO_BTF_MODULES= modules
```

产物：`$MOD_DIR/tcp_bbr.ko`。

检查：

```sh
llvm-nm "$MOD_DIR/tcp_bbr.ko" | grep -E '__this_module|init_module'
llvm-readelf -S "$MOD_DIR/tcp_bbr.ko" | grep this_module   # 期望 0x440
```

## 7. 安装

把 `tcp_bbr.ko` 和 `ksu-module/` 下的脚本打包成 zip，用 KernelSU 管理器安装，或手动：

```sh
adb push tcp_bbr.ko /data/local/tmp/
adb shell su -c 'ksud insmod /data/local/tmp/tcp_bbr.ko'
adb shell su -c 'sysctl -w net.ipv4.tcp_congestion_control=bbr'
```

验证：

```sh
adb shell su -c 'cat /proc/sys/net/ipv4/tcp_available_congestion_control'
# reno cubic bbr
adb shell su -c 'ss -tin | grep -m1 bbr'
```

## 常见构建错误

| 错误 | 处理 |
|---|---|
| `abi_symbollist.raw whitelist file not found` | 关闭 `TRIM_UNUSED_KSYMS`（见第 4 步） |
| `invalid value '' in '-mstack-protector-guard-offset='` | 生成 `arch/arm64/kernel/asm-offsets.s`（见第 5 步） |
| `没有规则可制作目标 scripts/module.lds` | `make scripts/module.lds` |
| `modpost: ... undefined!` | 正常。符号由 `ksud insmod` 在加载时通过 kallsyms 解析 |
| `Module.symvers is missing` | 正常。不要用普通 `insmod` |
