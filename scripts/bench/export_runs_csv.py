"""Example: turn 2026-11-02 raw runs into the sanitized CSV used by docs/bench.

Tag layout is specific to that run; adjust the loops for your own suite.
Set BENCH_DATA and OUT_CSV.
"""
import os, sys, csv
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze as A

OUT = os.environ.get("OUT_CSV", "runs.csv")

def clean_sig(v):
    return "" if v in (2147483647, -2147483648) else v

def row(server, rtt, scenario, phase, tag, arm, cc, qdisc, payload, used=True, note=""):
    r = A.run_info(tag)
    up = r["mode"] == "ul"
    return {
        "server": server,
        "rtt_ms_approx": rtt,
        "scenario": scenario,
        "phase": phase,
        "tag": tag,
        "arm": arm,
        "cc": cc,
        "qdisc": qdisc,
        "payload_mb": payload,
        "used": "yes" if used else "no",
        "note": note,
        "throughput_mbps": round(r["mbps"], 2) if r.get("mbps") is not None else "",
        "ttfb_s": round(r["TTFB"], 3) if r.get("TTFB") is not None else "",
        "connect_s": round(r["CONNECT"], 3) if r.get("CONNECT") is not None else "",
        "ping_p50_ms": round(r["ping_load"].get("p50"), 0) if r["ping_load"].get("p50") is not None else "",
        "ping_p95_ms": round(r["ping_load"].get("p95"), 0) if r["ping_load"].get("p95") is not None else "",
        "ping_samples": r["ping_load"].get("n", ""),
        "cwnd_segs": round(r["cwnd_segs_med"], 0) if up and r.get("cwnd_segs_med") is not None else "",
        "tcp_rtt_ms": round(r["rtt_ms_med"], 0) if up and r.get("rtt_ms_med") is not None else "",
        "delivery_mbps": round(r["delivery_mbps_med"], 2) if up and r.get("delivery_mbps_med") is not None else "",
        "retrans_pct": round(r["tcp"]["retrans_pct"], 2) if up and r["tcp"].get("retrans_pct") is not None else "",
        "rx_overhead_pct": round(r["rx_overhead_pct"], 1) if r.get("rx_overhead_pct") is not None else "",
        "tx_overhead_pct": round(r["tx_overhead_pct"], 1) if up and r.get("tx_overhead_pct") is not None else "",
        "rsrp_dbm": clean_sig(r["signal"].get("ssRsrp", "")),
        "sinr_db": clean_sig(r["signal"].get("ssSinr", "")),
        "direct": "yes" if r.get("direct") is not False else "no",
    }

rows = []

# Server A: netcup Manassas, ~290 ms
for p in range(1, 7):
    for arm in ("A", "C"):
        rows.append(row("A", 290, "normal", "download-single", f"qbulk{p}_{arm}", arm,
                        "cubic" if arm == "A" else "bbr",
                        "mq/pfifo_fast" if arm == "A" else "fq", 32))
for p in range(1, 5):
    for arm in ("A", "C"):
        rows.append(row("A", 290, "normal", "upload", f"qul{p}_{arm}", arm,
                        "cubic" if arm == "A" else "bbr",
                        "mq/pfifo_fast" if arm == "A" else "fq", 8))

# Server B: Aliyun Japan, ~110 ms
for p in range(1, 7):
    for arm in ("A", "C"):
        tag = f"s3bulk{p}_{arm}"
        used, note = True, ""
        if tag == "s3bulk1_A":
            used, note = False, "signal dip: RSRP -108 dBm"
        rows.append(row("B", 110, "normal", "download-single", tag, arm,
                        "cubic" if arm == "A" else "bbr",
                        "mq/pfifo_fast" if arm == "A" else "fq", 32, used, note))
for p in range(1, 5):
    for arm in ("A", "C"):
        tag = f"s3ul{p}_{arm}"
        used, note = True, ""
        if tag == "s3ul3_A":
            note = "call overlapped run tail; metrics normal"
        if tag == "s3ul3_C":
            used, note = False, "3 s rejected call; retrans 5.3%, tx overhead 7.3%"
        rows.append(row("B", 110, "normal", "upload", tag, arm,
                        "cubic" if arm == "A" else "bbr",
                        "mq/pfifo_fast" if arm == "A" else "fq", 8, used, note))

# Server C: Aliyun Beijing (default config), ~70 ms
# bj3 = good signal (SINR 9-14), bj2 = weak signal supplement (SINR 0-6)
for p in range(1, 7):
    for arm in ("A", "C"):
        rows.append(row("C", 70, "good-signal", "download-single", f"bj3bulk{p}_{arm}", arm,
                        "cubic" if arm == "A" else "bbr",
                        "mq/pfifo_fast" if arm == "A" else "fq", 32))
for p in range(1, 5):
    for arm in ("A", "C"):
        rows.append(row("C", 70, "good-signal", "upload", f"bj3ul{p}_{arm}", arm,
                        "cubic" if arm == "A" else "bbr",
                        "mq/pfifo_fast" if arm == "A" else "fq", 8))

for p in range(1, 7):
    for arm in ("A", "C"):
        rows.append(row("C", 70, "weak-signal", "download-single", f"bj2bulk{p}_{arm}", arm,
                        "cubic" if arm == "A" else "bbr",
                        "mq/pfifo_fast" if arm == "A" else "fq", 32))
for p in range(1, 5):
    for arm in ("A", "C"):
        rows.append(row("C", 70, "weak-signal", "upload", f"bj2ul{p}_{arm}", arm,
                        "cubic" if arm == "A" else "bbr",
                        "mq/pfifo_fast" if arm == "A" else "fq", 8))

outdir = os.path.dirname(OUT)
if outdir:
    os.makedirs(outdir, exist_ok=True)
with open(OUT, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
print("wrote", OUT, len(rows), "rows")
