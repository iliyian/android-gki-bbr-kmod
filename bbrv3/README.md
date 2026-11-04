# BBRv3 for Android GKI 6.1.138 (Redmi K80 / zorn)

在 **ACK/Google GKI 6.1.138** 上移植 Google 官方 **BBRv3**，保留 `android14-6.1` KMI，
不替换 vendor 内核、不依赖第三方 BBRv3 补丁。

> 状态：已在 Redmi K80（zorn / 24117RK2CC，HyperOS 3 / Android 16）真机验证。
> `uname -r` = `6.1.138-android14-11+`，`boot_completed=1`，
> `tcp_available_congestion_control` = `reno bbr3 cubic`，可切换 `bbr3`，音频正常。

---

## 1. 参考实现

- 仓库：`https://github.com/google/bbr`
- 分支：`v3`，commit `90210de4b779d40496dee0b89081780eeddf2a60`
- 基线：Linux **6.13.7**（该分支的 Makefile）
- 适配目标：ACK `android14-6.1-2025-06_r2`，commit `109284bd5`
  （`6.1.138-android14-11`）

注意：

- BBRv3 **不在 mainline**；截至 2026-11，vanilla 7.1 里也没有
  `rate_sample.tx_in_flight`、`ca_ops.skb_marked_lost`、`CA_EVENT_TLP_RECOVERY`
  等前置设施。Google 自己也是维护一套 patchset。
- Google 后续还有 `bbr-v3-2026-09-16-01`（7.1 开发线），把 BBRv3 拆成
  `tcp_bbr3.c` 并删除了 ECN。本 patchset 基于 2025-03 的 `v3`，算法行为与其一致；
  2026 版可作为后续升级目标。

## 2. 这不是一个模块

BBRv3 = **算法 + 核心 TCP 栈改动**。以下机制必须由核心提供，模块无法实现：

| 依赖 | 作用 |
|---|---|
| `rate_sample.prior_lost / tx_in_flight / lost / is_acking_tlp_retrans_seq` | BBRv3 的 inflight/loss 模型 |
| `tcp_skb_cb.tx.in_flight / lost` | 每个 skb 发送时的在途/丢包快照 |
| `ca_ops.skb_marked_lost` | 核心在标记 skb 丢失时回调 CA |
| `CA_EVENT_TLP_RECOVERY` | TLP 探针救回丢失时的通知 |
| `ca_ops.tso_segs(sk, mss_now)` | BBRv3 自定义 TSO/GSO 预算 |
| `fast_ack_mode` | 接收端快速 ACK |
| `tlp_orig_data_app_limited` | TLP 重传时的 app-limited 快照 |

## 3. 目录

```
bbrv3/
├── README.md
├── patches/
│   ├── 0001-tcp-core-bbrv3.patch              # 核心 TCP 栈（9 个文件）
│   ├── 0002-tcp-bbr-v3-algorithm.patch        # net/ipv4/tcp_bbr.c（BBRv3 算法）
│   └── 0003-module-tolerate-crc-mismatch.patch# 模块 CRC 不匹配放行（测试内核）
├── config/
│   └── bbrv3.fragment                         # 相对 gki_defconfig 的配置片段
├── scripts/
│   ├── apply-patches.sh
│   └── build-bbrv3-kernel.sh
├── tools/
│   ├── btf_dump.py          # 原始 BTF 结构体/偏移导出
│   ├── compare_btf.py       # 对比 stock 与新内核的结构体布局
│   ├── crc_query.py         # 读取 vmlinux __kcrctab 的符号 CRC
│   ├── ko_versions.py       # 读取 vendor .ko 的 __versions 期望 CRC
│   ├── stage_bbrv3.py       # 从 google/bbr v3 生成适配后的 tcp_bbr.c
│   └── patch_version.py     # 应用 0003（check_version 放行）
└── docs/
    └── BBRV3_PHONE_JOURNAL.md  # 完整移植过程、验证与问题记录
```

## 4. 应用与构建

```bash
# 1. 准备 ACK 树
git clone <ack android14-6.1 树> ack
cd ack
git checkout 109284bd5        # android14-6.1-2025-06_r2

# 2. 应用 patchset
../bbrv3/scripts/apply-patches.sh .

# 3. 构建
../bbrv3/scripts/build-bbrv3-kernel.sh . /path/to/out
# 产物：/path/to/out/arch/arm64/boot/Image
```

工具链（与已验证构建一致）：

- Ubuntu clang 17.0.6 / LLD 17.0.6 / pahole 1.31
- `PATH=/usr/lib/llvm-17/bin:$PATH`
- `ARCH=arm64 LLVM=1`

## 5. 配置

`config/bbrv3.fragment`（相对 `arch/arm64/configs/gki_defconfig`）：

```
CONFIG_TCP_CONG_ADVANCED=y
CONFIG_TCP_CONG_CUBIC=y
CONFIG_TCP_CONG_BBR=y
CONFIG_DEFAULT_CUBIC=y
# CONFIG_DEFAULT_BBR is not set
CONFIG_MODVERSIONS=y
# CONFIG_MODULE_SIG_PROTECT is not set
CONFIG_LTO_NONE=y
CONFIG_LOCALVERSION="-android14-11"
# CONFIG_LOCALVERSION_AUTO is not set
```

- 默认 CC 仍是 `cubic`，不改变日常行为；需要时手动切 `bbr3`。
- `MODULE_SIG_PROTECT=n` 是自编 GKI 在 zorn 上让原厂 `system_dlkm` 模块加载的前提
  （见 journal 第 16/18 节）。
- `MODVERSIONS=y` 必须保留：vendor 模块的 vermagic 里带 `modversions`，
  关掉会导致 `version magic ... should be ...`。

## 6. 关键设计

### 6.1 动态 BBR 状态

6.1 的 `ICSK_CA_PRIV_SIZE` 只有 104 字节，而 `struct bbr` 即使去掉 PLB 仍有 144 字节。
不能加大 `icsk_ca_priv`，否则 `tcp_sock` 所有字段平移，破坏 vendor 模块 ABI 和 CRC。

方案：

```c
struct bbr_sock_priv {
    struct bbr *state;   /* kmalloc 的 144 字节状态 */
    struct sock *owner;  /* 防止 accept 克隆共享状态 */
    u64 magic;
};
```

`bbr_init()` 在 `in_softirq()` 时用 `GFP_ATOMIC`，否则 `GFP_KERNEL`；
`.release` 释放。分配失败时入口返回，连接退化但不会崩溃。

### 6.2 `tcp_sock` 布局零变化

`fast_ack_mode` 和 `tlp_orig_data_app_limited` 放进原有的
`dup_ack_counter:2, tlp_retrans:1, unused:5` 空位，只把 `unused` 从 5 改成 3。
BTF 对比确认 `sizeof(struct tcp_sock)=2368` 和所有既有字段偏移完全不变。

### 6.3 `tcp_skb_cb`

tx 区仍是 24 字节：两个 u64 时间戳换成 u32，新增 `in_flight:20` 和 `lost`。
整个结构体 48→44 字节，仍远小于 `sk_buff.cb[48]`，TCP 之外无人使用。

### 6.4 PLB / ECN

按 journal 的决定裁掉。原因不是“省事”，而是在本机配置下它们是死代码：

- `bbr_can_use_ecn()` 要求 `TCP_ECN_LOW`；
- 该 bit 只有 per-route `RTAX_FEATURE_ECN_LOW` 才会置位，Android 不设置；
- 因此 `bbr_update_ecn_alpha()` 永远返回 -1，PLB 状态机不会执行。

Google 2026-09 版更是直接把 ECN 删除了。

### 6.5 模块 CRC 放行（重要）

我们的头文件改动会让 genksyms 对 **691 个导出符号**生成不同的 CRC，
包括 `skb_put`、`__alloc_skb` 这种实际 ABI 未变的符号。原厂 vendor 模块的
`__versions` 仍然记录 stock CRC，`check_version()` 不匹配时返回 0，模块加载失败
（本机上表现为 `audio_pkt_dlkm.ko` 等加载失败 → 音频 HAL 卡死 → 开机循环）。

`0003` 只改 `kernel/module/version.c` 的 `bad_version` 分支：

```c
bad_version:
    pr_warn_once("%s: tolerating symbol version mismatch for %s\n", ...);
    return 1;
```

保留 `MODVERSIONS=y`（vermagic / ksymtab 格式不变），只放行 CRC 差异，
等价于老内核的 `insmod --force`。依据是 BTF 证明 vendor 模块实际用到的结构
（`tcp_sock` / `inet_connection_sock` / `sk_buff`）布局未变。

> 这是测试内核的妥协。若用于产品化，应重新编译 vendor 模块或严格保持 KMI。

## 7. 已验证

| 项 | 结果 |
|---|---|
| `uname -r` | `6.1.138-android14-11+` |
| `boot_completed` | 1 |
| `tcp_available_congestion_control` | `reno bbr3 cubic` |
| 当前 CC | `bbr3` |
| 已加载模块 | 500 |
| `Failed to insmod` | 0 |
| `disagrees about version` | 0 |
| `exports protected symbol` | 0 |
| CRC 放行日志 | 1 条（`qcom_ipcc: log_write_mmio`） |
| 音频 | `pineapple-mtp-snd-card` 6.529s 注册；用户确认开机正常 |

构建产物（本次测试）：

- `Image` 35,432,960 字节
  SHA256 `43b72d3d06717e1df376abf1331d1ca784323914b4e53b5d49c41643ff9e5934`
- boot 镜像 SHA256
  `8e6bacabedc9e347cc41a68a77dc9b630bbd6b6dffcb000adc8aa82784e28354`

## 8. 已知限制

- 旧 `tcp_bbr.ko`（BBRv1 模块）不能加载：`tcp_congestion_ops` 布局已变；
  如需 BBRv1 A/B，应把官方 `tcp_bbr1.c` 一起编译并以 `bbr` 注册。
- 当前是 2025-03 版 BBRv3；2026-09 版有 DRAIN/undo 行为更新，尚未移植。
- 尚未做真实吞吐/延迟、长稳和实际放音验证。
- `check_version()` 放行是全局的；若后续改动 `tcp_sock`/`sk_buff` 等核心结构，
  必须重新评估。

## 9. 自定义注册名

`.name` 是普通字符串，上限 `TCP_CA_NAME_MAX`（6.1 为 16）：

```c
/* net/ipv4/tcp_bbr.c */
.name = "bbr3",   /* 改成 "iliyian" 等即可 */
```

改完重编、重刷即可；`sysctl net.ipv4.tcp_congestion_control=<name>` 使用新名字。
名字只影响用户态可见标识和 per-route `RTAX_CC_ALGO` 的 key，不影响算法。

## 10. 参考与许可

- 算法：`google/bbr` v3 分支（Dual BSD/GPL），见文件头。
- 核心补丁：从 Google 6.13.7→v3 的 diff 逐条回移到 ACK 6.1.138。
- 移植过程、失败尝试和验证数据：`docs/BBRV3_PHONE_JOURNAL.md`。
