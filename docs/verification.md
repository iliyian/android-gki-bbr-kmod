# ABI 与运行时验证

模块能加载不等于 ABI 正确。字段偏移错误往往不会立即报错，而是在特定网络路径上崩溃。请务必完成这里的检查。

## 1. 拉取设备 BTF

```sh
adb pull /sys/kernel/btf/vmlinux ./vmlinux.btf
```

需要设备内核开启 `CONFIG_DEBUG_INFO_BTF=y`（本项目设备为 y）。

## 2. 从模块 DWARF 生成 BTF

构建时虽然跳过了 BTF 编码步骤，但 DWARF 仍在（`CONFIG_DEBUG_INFO=y`）：

```sh
pahole --btf_encode_detached=tcp_bbr.btf tcp_bbr.ko
```

## 3. 两边 dump 成同一种文本格式

```sh
BPFTOOL=$(ls /usr/lib/linux-tools/*/bpftool 2>/dev/null | head -1)
[ -x "$BPFTOOL" ] || BPFTOOL=bpftool

"$BPFTOOL" btf dump file vmlinux.btf format raw > device.btf.txt
"$BPFTOOL" btf dump file tcp_bbr.btf format raw > module.btf.txt
```

## 4. 对比

```sh
python3 scripts/verify-layouts.py device.btf.txt module.btf.txt \
    tcp_sock tcp_congestion_ops rate_sample sock inet_sock \
    inet_connection_sock module
```

期望输出（本项目实测）：

```
=== tcp_sock: dev size=2368 mod size=2368 OK
=== tcp_congestion_ops: dev size=192 mod size=192 OK
=== rate_sample: dev size=72 mod size=72 OK
=== sock: dev size=856 mod size=856 OK
=== inet_sock: dev size=1088 mod size=1088 OK
=== inet_connection_sock: dev size=1536 mod size=1536 OK
=== module: dev size=1088 mod size=1088 OK
```

只要出现 `only-in-device` / `only-in-module` / `MISMATCH`，就**不要加载**，先检查源码/配置/编译器。

## 5. kCFI 哈希一致性（可选）

```sh
CC1=/path/to/clang-r487747c/bin/clang CC2=$(command -v clang) \
    ./scripts/cfi-hash-test.sh
```

脚本会分别用两个 clang 编译同一组 `tcp_congestion_ops` 函数原型，并打印 `!kcfi_type` 元数据。本机实测 clang 17.0.2 与 18.1.3 输出完全相同；仍建议使用与内核一致的编译器。

## 6. 加载后验证

```sh
# 模块已注册
su -c 'cat /proc/sys/net/ipv4/tcp_available_congestion_control'   # reno cubic bbr
su -c 'lsmod | grep tcp_bbr'

# 默认 CC
su -c 'sysctl -w net.ipv4.tcp_congestion_control=bbr'
su -c 'cat /proc/sys/net/ipv4/tcp_congestion_control'             # bbr

# 新连接实际使用 BBR
su -c 'ss -tin | grep -m1 bbr'                                    # 含 bbr:

# qdisc
su -c 'tc qdisc show dev wlan0 | head -1'                         # qdisc fq

# 真实流量
su -c 'curl -m 10 -s -o /dev/null -w "%{http_code}\n" https://www.baidu.com'
```

观察 `dmesg` 是否出现 `CFI failure`、`Unable to handle kernel` 等。

## 7. 开机自启验证

重启后：

```sh
su -c 'getprop sys.boot_completed'                                # 1
su -c 'cat /proc/sys/net/ipv4/tcp_congestion_control'             # bbr
su -c 'tc qdisc show dev wlan0 | head -1'                         # qdisc fq
su -c 'ps -A -o PID,PPID,ARGS | grep service.sh'                  # 看门狗进程
su -c 'cat /data/local/tmp/tcp_bbr_kmod.log'
```

## 8. 回退

```sh
su -c 'sysctl -w net.ipv4.tcp_congestion_control=cubic'
# 永久关闭：KernelSU 管理器禁用/删除 tcp_bbr_kmod，然后重启
```
