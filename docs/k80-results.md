# Redmi K80 实测记录

## 设备

```
型号        Redmi K80 (zorn / 24117RK2CC)
系统        HyperOS OS3.0.309.0.WOKCNXM / Android 16
内核        Linux 6.1.138-android14-11-g965475777129-mi
Root        SukiSU Ultra 4.3.0（LKM，init_boot_a），UAPI 6
KMI         android14-6.1
Bootloader  已解锁，A/B
```

内核原始状态：

```
$ zcat /proc/config.gz | grep TCP_CONG
CONFIG_TCP_CONG_CUBIC=y
CONFIG_DEFAULT_TCP_CONG="cubic"
# CONFIG_TCP_CONG_ADVANCED is not set

$ cat /proc/sys/net/ipv4/tcp_available_congestion_control
reno cubic

$ ls /vendor/lib/modules | grep -i bbr     # 无
$ zcat /proc/config.gz | grep -i bbr       # 无
```

即内核完全没有编译 BBR。

## 构建参数

| 项 | 值 |
|---|---|
| 源码 | ACK tag `android14-6.1.138_r00`（commit `4894546`） |
| 镜像 | `https://mirrors.tuna.tsinghua.edu.cn/git/AOSP/kernel/common.git` |
| 编译器 | AOSP clang `r487747c`（17.0.2） |
| 配置 | 设备 `/proc/config.gz` 原样 |
| 关键改动 | `TCP_CONG_ADVANCED=y`、`TCP_CONG_BBR=m` |
| 构建时覆盖 | `CONFIG_DEBUG_INFO_BTF= CONFIG_DEBUG_INFO_BTF_MODULES=`（仅跳过 BTF 编码） |

生成的模块：

```
vermagic=6.1.138-g4894546596ee SMP preempt mod_unload modversions aarch64
```

（设备实际 vermagic 由 `ksud insmod` 自动改写为
`6.1.138-android14-11-g965475777129-mi ...`）

## ABI 校验结果

| 结构体 | 设备 | 模块 |
|---|---:|---:|
| `tcp_sock` | 2368 | 2368 |
| `tcp_congestion_ops` | 192 | 192 |
| `rate_sample` | 72 | 72 |
| `sock` | 856 | 856 |
| `inet_sock` | 1088 | 1088 |
| `inet_connection_sock` | 1536 | 1536 |
| `module` | 1088 | 1088 |

对比 stable 6.1.138 时的差异（用于说明为什么必须用 ACK）：

| 结构体 | 设备 | stable | 差 |
|---|---:|---:|---:|
| `sock` | 856 | 768 | 88 |
| `inet_sock` | 1088 | 984 | 104 |
| `inet_connection_sock` | 1536 | 1392 | 144 |
| `tcp_sock` | 2368 | 2200 | 168 |
| `module` | 1088 | 1024 | 64 |

## 运行结果

```
$ cat /proc/sys/net/ipv4/tcp_congestion_control
bbr

$ cat /proc/sys/net/ipv4/tcp_available_congestion_control
reno cubic bbr

$ lsmod | grep tcp_bbr
tcp_bbr                24576  84

$ ss -tin | grep -m1 bbr
    bbr rto:4000 ... bbr:(bw:0bps,mrtt:0) ...
```

qdisc：

```
wlan0        qdisc fq
rmnet_data0  qdisc fq
rmnet_data1  qdisc fq
rmnet_ipa0   qdisc fq
```

真实流量：

```
$ curl -m 10 -s -o /dev/null -w '%{http_code} %{speed_download}\n' https://www.baidu.com
200 2592
```

重启后自动生效，`service.sh` 的看门狗进程（PPID 1）持续维护 qdisc：

```
$ cat /data/local/tmp/tcp_bbr_kmod.log
Loaded kernel module: /data/adb/modules/tcp_bbr_kmod/tcp_bbr.ko
net.ipv4.tcp_congestion_control = bbr
net.core.default_qdisc = fq
```

## 失败记录

1. 用 stable 6.1.138 源码：所有 `tcp_sock` 字段偏移错位（+168B），未加载。
2. 关闭 `CONFIG_DEBUG_INFO_BTF_MODULES`：`struct module` 1024 vs 1088，`ksud insmod` 瞬间重启（无 pstore 日志）。
3. 修复后：加载成功，运行稳定，重启自启验证通过。

## 其他尝试（未采用）

- 通用 GKI 6.1.145（SukiSU 4.1.3 + SUSFS + KPM）：能启动，但 HyperOS 3 显示栈闪屏（vendor HWC/DRM `COMMIT failed: 11`）。
- zorn 专用 6.1.176 树：同样闪屏。
- KPM 方案：LKM 模式下 `ksud kpm list` 返回 `EOPNOTSUPP`（需要编译期 `CONFIG_KPM`）。
