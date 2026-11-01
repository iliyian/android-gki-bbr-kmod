# 预编译产物

**仅适用于：**

```
Linux 6.1.138-android14-11-g965475777129-mi
Redmi K80 (zorn / 24117RK2CC)
HyperOS OS3.0.309.0.WOKCNXM / Android 16
```

其他内核/设备请按 [../docs/build.md](../docs/build.md) 重新编译，并完成
[ABI 校验](../docs/verification.md)。**直接拿这个 .ko 到别的内核上加载可能瞬间重启。**

## 文件

| 文件 | 说明 | SHA-256 |
|---|---|---|
| `tcp_bbr.ko` | 内核模块 | `9232a82db3129a91f65ca4ee062fc794cf0dc4b82e806c62822db66392902fba` |
| `tcp_bbr_kmod.zip` | KernelSU 模块（含 ko + 脚本） | `b6327c0da7bd4f9a678e89a614ca5814793c7e05302b91e51feb6d5532145f35` |

模块信息：

```
name=tcp_bbr
license=Dual BSD/GPL
vermagic=6.1.138-g4894546596ee SMP preempt mod_unload modversions aarch64
```

> 加载时 `ksud insmod` 会把 vermagic 自动改写为设备实际值。

## 安装

1. KernelSU / SukiSU 管理器 → 模块 → 从本地安装 `tcp_bbr_kmod.zip`；
2. 重启；
3. 验证：

```sh
su -c 'cat /proc/sys/net/ipv4/tcp_congestion_control'   # bbr
su -c 'tc qdisc show dev wlan0 | head -1'                # qdisc fq
```

二进制对应源码为 ACK `android14-6.1.138_r00` 中未修改的 `net/ipv4/tcp_bbr.c`，
按 GPL-2.0 分发；构建方法见仓库脚本。
