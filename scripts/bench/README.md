# 手机端 BBR / CUBIC 实测脚本

这里放的是 2026-11-02 那组测试实际用到的 harness 和分析脚本，已经去掉服务器地址、令牌和本机路径。报告和逐轮结果在 [`docs/bench/`](../../docs/bench/)。

## 依赖

- 手机：已 root（本机是 SukiSU），有 `curl`、`ss`、`tc`、`ping`、`dumpsys`；
- 电脑：`adb`；
- 分析：Python 3（只用标准库）。

脚本里的 qdisc watchdog 路径按本仓库的 KernelSU 模块写的：

```
/data/adb/modules/tcp_bbr_kmod/service.sh
```

换模块或换目录时要改 `run_qdisc.sh` 里的查找逻辑。

## 快速开始

先设置两个环境变量，脚本本身不含任何地址或令牌：

```sh
export SPEEDTEST_URL='https://your-server.example:3333/<token>'
export SERVER_IP='203.0.113.10'
```

把设备端 harness 推上去：

```sh
adb push scripts/bench/cc_bench.sh /data/local/tmp/cc_bench.sh
adb shell chmod 755 /data/local/tmp/cc_bench.sh
```

跑一组 A/C 对比（A = `cubic + mq/pfifo_fast`，C = `bbr + fq`）：

```sh
./scripts/bench/run_qdisc.sh myrun
```

结果会写到手机的 `/data/local/tmp/ccbench/<tag>.*`。拉回来分析：

```sh
mkdir -p ./data
adb pull /data/local/tmp/ccbench/. ./data
BENCH_DATA=./data python3 scripts/bench/analyze_qdisc.py myrun
```

`analyze_qdisc.py` 会打印两组的中位/平均吞吐、RTT、cwnd、压载 ping，并生成 `results_qdisc_myrun.json`。

## 文件

| 文件 | 用途 |
|---|---|
| `cc_bench.sh` | 设备端单轮 harness：设 CC/qdisc、下载/上传/多流、采样 `ss`/ping/信号/计数 |
| `run_qdisc.sh` | 电脑端 A/C 套件：ABBA 配对、暂停 watchdog、逐轮校验、结束后恢复 |
| `sig_sample.sh` | 设备端信号采样，每 2 秒打印一次 RSRP/SINR |
| `analyze.py` | 解析 `<tag>.*` 的公共库，也能跑 bulk/multi/ul 的通用汇总 |
| `analyze_qdisc.py` | A/C 套件分析（本报告用的就是它） |
| `export_runs_csv.py` | 把 2026-11-02 的原始结果导出成 `docs/bench/data/2026-11-02-runs.csv`；标签结构是那次测试专用的，换测试要改循环 |
| `export_samples_csv.py` | 把原始 `ss`/`ping` 展开成逐采样 CSV：`2026-11-02-samples-ss.csv` 和 `2026-11-02-samples-ping.csv` |

## 注意事项

- 脚本会临时改 `rmnet_data3` 的 root qdisc，并暂停 BBR 模块的 watchdog。`run_qdisc.sh` 里有 `trap`，正常退出或 Ctrl-C 会恢复 `fq + bbr`；如果 adb 中途断掉，重启手机会回到模块默认状态。
- `mq_pfifo` 模式会先把 `net.core.default_qdisc` 设成 `pfifo_fast`，再建 `mq`，这样 31 个叶子才是 pfifo_fast。结束后会恢复 `fq`。
- HTTPS 用了 `-k`：测试服务器常用 IP 或自签证书。有合法证书就删掉 `curl -k` 里的 `-k`。
- `cc_bench.sh` 的参数顺序：
  ```
  cc_bench.sh <cc> <tag> <dl|ul|multi> [bytes] [fq|mq_pfifo] <url> <server_ip>
  ```
- 下载方向的 `ss` 字段（`cwnd`、`delivery_rate`、`pacing_rate`）是发送端字段，接收端会冻结；分析脚本对下载只使用字节计数、应用层吞吐和 ping。
- 这些脚本只负责测量，不代表任何特定网络或服务器的结论。换运营商、换服务端或换内核版本，结果都可能不同。
