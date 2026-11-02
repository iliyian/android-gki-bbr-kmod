"""Parse raw cc_bench.sh artifacts and print per-phase summaries.

Set BENCH_DATA to the directory containing <tag>.* files.
"""
import os, re, glob, statistics as st, json, math

BASE = os.environ.get("BENCH_DATA", "./data")

def rd(tag, ext, default=None):
    p = os.path.join(BASE, f"{tag}.{ext}")
    if not os.path.exists(p):
        return default
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return f.read()

def num(x):
    try:
        return float(x)
    except Exception:
        return None

def parse_kv_line(line):
    d = {}
    for part in line.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            d[k] = v
    return d

def parse_rate(s):
    if s is None:
        return None
    s = s.strip()
    m = re.match(r"^([0-9.]+)\s*([KMG]?)bps$", s)
    if not m:
        m2 = re.match(r"^([0-9.]+)\s*([KMG]?)(?:bit|B)/s$", s)
        if not m2:
            return num(s)
        m = m2
    v = float(m.group(1))
    u = m.group(2)
    mult = {"": 1, "K": 1e3, "M": 1e6, "G": 1e9}[u]
    return v * mult

def parse_snmp(text):
    out = {}
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("Tcp:") and i + 1 < len(lines):
            keys = line.split()[1:]
            vals = lines[i + 1].split()[1:]
            if len(keys) == len(vals):
                out = {k: int(v) for k, v in zip(keys, vals)}
            break
    return out

def parse_netstat(text):
    out = {}
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("TcpExt:") and i + 1 < len(lines):
            keys = line.split()[1:]
            vals = lines[i + 1].split()[1:]
            if len(keys) == len(vals):
                out = {k: int(v) for k, v in zip(keys, vals)}
            return out
    return out

SS_FIELDS = ["cwnd", "rto", "rtt", "rttvar", "snd_ssthresh", "bytes_acked", "bytes_received",
             "segs_out", "segs_in", "data_segs_out", "data_segs_in", "unacked", "lost",
             "minrtt", "rcv_rtt", "rcv_space", "busy", "lastsnd", "lastrcv", "lastack"]

def parse_ss_line(line):
    d = {"raw": line}
    m = re.search(r"\b(bbr|cubic|reno)\b", line)
    d["cc"] = m.group(1) if m else None
    for f in SS_FIELDS:
        m = re.search(rf"\b{f}:([0-9.]+)", line)
        if m:
            d[f] = float(m.group(1))
    m = re.search(r"retrans:(\d+)/(\d+)", line)
    if m:
        d["retrans_now"] = int(m.group(1))
        d["retrans_total"] = int(m.group(2))
    m = re.search(r"lost:(\d+)", line)
    if m:
        d["lost"] = int(m.group(1))
    for f in ("delivery_rate", "pacing_rate"):
        m = re.search(rf"\b{f}[:\s]+([0-9.]+[KMG]?bps)", line)
        if m:
            d[f] = parse_rate(m.group(1))
    m = re.search(r"\bsend ([0-9.]+[KMG]?bps)", line)
    if m:
        d["send"] = parse_rate(m.group(1))
    m = re.search(r"bbr:\(bw:([^,]+)", line)
    if m:
        d["bbr_bw"] = parse_rate(m.group(1))
    m = re.search(r"pacing_gain:([0-9.]+)", line)
    if m:
        d["pacing_gain"] = float(m.group(1))
    m = re.search(r"mss:(\d+)", line)
    if m:
        d["mss"] = int(m.group(1))
    return d

def parse_ss_file(tag):
    text = rd(tag, "ss", "") or ""
    samples, cur, sock = [], None, None
    for line in text.splitlines():
        if line.startswith("==="):
            if cur:
                samples.append(cur)
            cur = {"ts": line[4:].strip(), "socks": []}
            sock = None
            continue
        if cur is None:
            continue
        if re.match(r"^(ESTAB|SYN-SENT|CLOSE-WAIT|FIN-WAIT)", line):
            sock = None
            if ":3333" in line and line.startswith("ESTAB"):
                sock = parse_ss_line("")
                fld = line.split()
                sock["peer"] = fld[-1]
                sock["local"] = fld[-2]
                cur["socks"].append(sock)
        elif line.startswith("\t") and sock is not None:
            info = parse_ss_line(line)
            sock.update({k: v for k, v in info.items() if k != "raw"})
    if cur:
        samples.append(cur)
    return samples

def ss_summary(tag):
    samples = parse_ss_file(tag)
    series = {}
    for s in samples:
        for sk in s["socks"]:
            sk["total"] = max(sk.get("bytes_received") or 0, sk.get("bytes_acked") or 0)
            key = sk.get("local") or sk.get("peer")
            series.setdefault(key, []).append(sk)
    if not series:
        return {}
    finals = [max(v, key=lambda x: x.get("total", 0)) for v in series.values()]

    def steady(field, summ="median"):
        vals = []
        for socks in series.values():
            fin = max(x.get("total", 0) for x in socks)
            vs = [x[field] for x in socks if x.get(field) is not None and x.get("total", 0) >= 0.4 * fin]
            if vs:
                vals.append(st.median(vs))
        if not vals:
            return None
        return sum(vals) if summ == "sum" else st.median(vals)

    return {
        "n_socks": len(series),
        "finals": finals,
        "cwnd_segs_med": steady("cwnd"),
        "cwnd_bytes_med": (steady("cwnd") * (finals[0].get("mss") or 1348)) if steady("cwnd") else None,
        "rtt_ms_med": steady("rtt"),
        "minrtt_ms_med": steady("minrtt"),
        "delivery_mbps_med": (steady("delivery_rate", "sum") or 0) / 1e6 or None,
        "pacing_mbps_med": (steady("pacing_rate", "sum") or 0) / 1e6 or None,
        "retrans_total_sum": sum((x.get("retrans_total") or 0) for x in finals),
        "lost_sum": sum((x.get("lost") or 0) for x in finals),
        "bytes_acked_sum": sum((x.get("bytes_acked") or 0) for x in finals),
        "cc_seen": [x.get("cc") for x in finals],
    }

def throughput_series(tag):
    """Per-interval received/sent throughput (Mbps) from ss byte counters over time."""
    samples = parse_ss_file(tag)
    snaps = []
    for s in samples:
        try:
            ts = float(str(s["ts"]).split()[0])
        except Exception:
            continue
        d = {}
        for sk in s["socks"]:
            key = sk.get("local") or sk.get("peer")
            d[key] = max(sk.get("bytes_received") or 0, sk.get("bytes_acked") or 0)
        snaps.append((ts, d))
    if len(snaps) < 3:
        return []
    # monotonic cumulative totals (sockets that close keep their last value)
    cum = {}
    out = []
    t0 = snaps[0][0]
    prev_ts = snaps[0][0]
    for k, v in snaps[0][1].items():
        cum[k] = max(cum.get(k, 0), v)
    prev_sum = sum(cum.values())
    for ts, d in snaps[1:]:
        for k, v in d.items():
            cum[k] = max(cum.get(k, 0), v)
        cur_sum = sum(cum.values())
        dt = ts - prev_ts
        if 0.12 < dt < 3.0:
            out.append((ts - t0, (cur_sum - prev_sum) * 8 / dt / 1e6))
        prev_sum = cur_sum
        prev_ts = ts
    return out

def rate_stats(ser):
    if len(ser) < 3:
        return {}
    end = ser[-1][0]
    mid = [r for e, r in ser if 1.0 <= e <= max(2.0, end - 1.0)]
    if len(mid) < 2:
        mid = [r for e, r in ser]
    mean = st.mean(mid)
    return {
        "rate_med": st.median(mid),
        "rate_p10": st.quantiles(mid, n=10)[0] if len(mid) >= 10 else min(mid),
        "rate_p90": st.quantiles(mid, n=10)[-1] if len(mid) >= 10 else max(mid),
        "rate_cv": (st.stdev(mid) / mean) if len(mid) > 1 and mean else None,
        "rate_first2": st.mean([r for e, r in ser if e <= 2.0]) if any(e <= 2 for e, _ in ser) else None,
        "rate_last5": st.mean([r for e, r in ser if e >= end - 5.0]) if any(e >= end - 5 for e, _ in ser) else None,
    }

def parse_signal(tag):
    t = rd(tag, "signal", "") or ""
    d = {}
    for k in ("ssRsrp", "ssRsrq", "ssSinr"):
        m = re.search(rf"{k}\s*=\s*(-?\d+)", t)
        if m:
            d[k] = int(m.group(1))
    return d

def parse_ping(tag):
    text = rd(tag, "ping", "") or ""
    times = [float(x) for x in re.findall(r"time[=<]([0-9.]+)\s*ms", text)]
    loss = None
    m = re.search(r"(\d+(?:\.\d+)?)%\s*packet loss", text)
    if m:
        loss = float(m.group(1))
    if not times:
        return {"n": 0, "loss_pct": loss}
    times.sort()
    def pct(p):
        k = max(0, min(len(times) - 1, int(math.ceil(p * len(times))) - 1))
        return times[k]
    return {"n": len(times), "loss_pct": loss, "min": min(times), "p50": st.median(times),
            "p95": pct(0.95), "max": max(times), "mean": st.mean(times)}

def iface_delta(tag):
    def val(ext):
        t = rd(tag, ext)
        try:
            return int((t or "").strip())
        except Exception:
            return None
    rx0, rx1, tx0, tx1 = val("rx0"), val("rx1"), val("tx0"), val("tx1")
    return {
        "rx_delta": (rx1 - rx0) if None not in (rx0, rx1) else None,
        "tx_delta": (tx1 - tx0) if None not in (tx0, tx1) else None,
    }

def tcp_deltas(tag):
    s0 = parse_snmp(rd(tag, "snmp0", ""))
    s1 = parse_snmp(rd(tag, "snmp1", ""))
    out = {}
    for k in ("OutSegs", "RetransSegs", "InSegs", "InErrs", "EstabResets"):
        if k in s0 and k in s1:
            out["d_" + k] = s1[k] - s0[k]
    n0 = parse_netstat(rd(tag, "netstat0", ""))
    n1 = parse_netstat(rd(tag, "netstat1", ""))
    for k in ("TCPLostRetransmit", "TCPSpuriousRTOs", "TCPTimeouts", "TCPDSACKRecv",
              "TCPDSACKOfoRecv", "TCPRetransFail", "TCPFastRetrans", "TCPSlowStartRetrans"):
        if k in n0 and k in n1:
            out["d_" + k] = n1[k] - n0[k]
    if out.get("d_OutSegs"):
        out["retrans_pct"] = 100.0 * out.get("d_RetransSegs", 0) / out["d_OutSegs"]
    return out

def run_info(tag):
    line = (rd(tag, "result", "") or "").strip().splitlines()
    first = line[0] if line else ""
    kv = parse_kv_line(first)
    mode = kv.get("MODE") or ("multi" if "multi" in tag else
                              "ul" if re.search(r"(^|_)ul\d", tag) else "dl")
    info = {"tag": tag, "mode": mode, "cc_set": tag.rsplit("_", 1)[-1]}
    for k in ("BYTES", "SPEED", "TOTAL", "CONNECT", "TLS", "TTFB", "UP", "UPSPEED"):
        if k in kv:
            info[k] = num(kv[k])
    info["HTTP"] = kv.get("HTTP")
    if mode == "multi":
        parts = []
        total_bytes = 0
        for i in range(1, 5):
            txt = rd(tag, f"result{i}", "") or ""
            if not txt.strip():
                continue
            kv2 = parse_kv_line(txt.strip().splitlines()[0])
            if kv2.get("BYTES"):
                total_bytes += int(kv2["BYTES"])
            parts.append({"bytes": num(kv2.get("BYTES")), "speed": num(kv2.get("SPEED")),
                          "total": num(kv2.get("TOTAL")), "ttfb": num(kv2.get("TTFB"))})
        try:
            t0 = float((rd(tag, "t0", "") or "").strip())
            t1 = float((rd(tag, "t1", "") or "").strip())
            wall = t1 - t0
        except Exception:
            wall = max([p["total"] or 0 for p in parts]) if parts else None
        info["streams"] = parts
        info["wall"] = wall
        info["BYTES"] = total_bytes
        info["SPEED"] = (total_bytes / wall) if wall else None
        info["TOTAL"] = wall
    elif mode == "ul":
        info["BYTES"] = info.get("UP") or info.get("BYTES")
        info["SPEED"] = info.get("UPSPEED") or info.get("SPEED")
    t0 = num((rd(tag, "t0", "") or "").strip())
    t1 = num((rd(tag, "t1", "") or "").strip())
    if t0 and t1:
        info["wall"] = t1 - t0
    if info.get("TOTAL") and info.get("TTFB") is not None and info["TOTAL"] > info["TTFB"]:
        info["body_mbps"] = (info["BYTES"] or 0) * 8 / (info["TOTAL"] - info["TTFB"]) / 1e6
    info["mbps"] = (info.get("SPEED") or 0) * 8 / 1e6 if info.get("SPEED") else None
    info.update(ss_summary(tag))
    info["rate_series"] = throughput_series(tag)
    info.update(rate_stats(info["rate_series"]))
    info["ping_load"] = parse_ping(tag)
    info["signal"] = parse_signal(tag)
    info["iface"] = iface_delta(tag)
    info["tcp"] = tcp_deltas(tag)
    info["cc_after"] = (rd(tag, "cc", "") or "").strip()
    route_txt = (rd(tag, "route0", "") or "") + (rd(tag, "route1", "") or "")
    info["direct"] = (None if not route_txt.strip() else ("dev tun" not in route_txt))
    up = info.get("UP") or 0
    if info["iface"].get("tx_delta") and up:
        info["tx_overhead_pct"] = 100.0 * (info["iface"]["tx_delta"] / up - 1)
    dl = info.get("BYTES") or 0
    if info["iface"].get("rx_delta") and dl:
        info["rx_overhead_pct"] = 100.0 * (info["iface"]["rx_delta"] / dl - 1)
    return info

def fmt(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"

def phase_runs(prefix):
    tags = sorted({os.path.basename(p).split(".")[0] for p in glob.glob(os.path.join(BASE, prefix + "*.result"))})
    return [run_info(t) for t in tags if re.match(rf"^{re.escape(prefix)}\d+_(cubic|bbr)$", t)]

def summarize(runs, label):
    out = {"phase": label, "runs": len(runs)}
    for cc in ("cubic", "bbr"):
        rr = [r for r in runs if r["cc_set"] == cc]
        if not rr:
            continue
        sp = [r["mbps"] for r in rr if r.get("mbps")]
        body = [r["body_mbps"] for r in rr if r.get("body_mbps")]
        out[cc] = {
            "n": len(rr),
            "mbps_mean": st.mean(sp) if sp else None,
            "mbps_median": st.median(sp) if sp else None,
            "mbps_stdev": st.stdev(sp) if len(sp) > 1 else None,
            "mbps_min": min(sp) if sp else None,
            "mbps_max": max(sp) if sp else None,
            "body_mbps_median": st.median(body) if body else None,
            "ttfb_median": st.median([r["TTFB"] for r in rr if r.get("TTFB") is not None]) if any(r.get("TTFB") for r in rr) else None,
            "tls_median": st.median([r["TLS"] for r in rr if r.get("TLS") is not None]) if any(r.get("TLS") for r in rr) else None,
            "retrans_pct_median": st.median([r["tcp"].get("retrans_pct") for r in rr if r["tcp"].get("retrans_pct") is not None]) if any(r["tcp"].get("retrans_pct") is not None for r in rr) else None,
            "ping_n_median": st.median([r["ping_load"].get("n") for r in rr]) if rr else None,
            "ping_loss_median": st.median([r["ping_load"].get("loss_pct") for r in rr if r["ping_load"].get("loss_pct") is not None]) if any(r["ping_load"].get("loss_pct") is not None for r in rr) else None,
            "ping_p50_median": st.median([r["ping_load"].get("p50") for r in rr if r["ping_load"].get("p50") is not None]) if any(r["ping_load"].get("p50") is not None for r in rr) else None,
            "ping_p95_median": st.median([r["ping_load"].get("p95") for r in rr if r["ping_load"].get("p95") is not None]) if any(r["ping_load"].get("p95") is not None for r in rr) else None,
            "ping_max": max([r["ping_load"].get("max") for r in rr if r["ping_load"].get("max") is not None], default=None),
            "rate_med_median": st.median([r["rate_med"] for r in rr if r.get("rate_med")]) if any(r.get("rate_med") is not None for r in rr) else None,
            "rate_cv_median": st.median([r["rate_cv"] for r in rr if r.get("rate_cv") is not None]) if any(r.get("rate_cv") is not None for r in rr) else None,
            "rate_p10_median": st.median([r["rate_p10"] for r in rr if r.get("rate_p10") is not None]) if any(r.get("rate_p10") is not None for r in rr) else None,
            "rate_p90_median": st.median([r["rate_p90"] for r in rr if r.get("rate_p90") is not None]) if any(r.get("rate_p90") is not None for r in rr) else None,
            "rate_first2_median": st.median([r["rate_first2"] for r in rr if r.get("rate_first2") is not None]) if any(r.get("rate_first2") is not None for r in rr) else None,
            "rate_last5_median": st.median([r["rate_last5"] for r in rr if r.get("rate_last5") is not None]) if any(r.get("rate_last5") is not None for r in rr) else None,
            "lost_sum_median": st.median([r.get("lost_sum") or 0 for r in rr]) if rr else None,
            "rsrp_median": st.median([r["signal"]["ssRsrp"] for r in rr if r.get("signal", {}).get("ssRsrp") is not None]) if any(r.get("signal", {}).get("ssRsrp") is not None for r in rr) else None,
            "rsrp_min": min([r["signal"]["ssRsrp"] for r in rr if r.get("signal", {}).get("ssRsrp") is not None], default=None),
            "rsrp_max": max([r["signal"]["ssRsrp"] for r in rr if r.get("signal", {}).get("ssRsrp") is not None], default=None),
            "sinr_median": st.median([r["signal"]["ssSinr"] for r in rr if r.get("signal", {}).get("ssSinr") is not None]) if any(r.get("signal", {}).get("ssSinr") is not None for r in rr) else None,
            "delivery_mbps_median": st.median([r["delivery_mbps_med"] for r in rr if r.get("delivery_mbps_med")]) if any(r.get("delivery_mbps_med") for r in rr) else None,
            "cwnd_segs_median": st.median([r["cwnd_segs_med"] for r in rr if r.get("cwnd_segs_med")]) if any(r.get("cwnd_segs_med") for r in rr) else None,
            "rtt_ms_median": st.median([r["rtt_ms_med"] for r in rr if r.get("rtt_ms_med")]) if any(r.get("rtt_ms_med") for r in rr) else None,
            "rx_overhead_pct_median": st.median([r.get("rx_overhead_pct") for r in rr if r.get("rx_overhead_pct") is not None]) if any(r.get("rx_overhead_pct") is not None for r in rr) else None,
            "tx_overhead_pct_median": st.median([r.get("tx_overhead_pct") for r in rr if r.get("tx_overhead_pct") is not None]) if any(r.get("tx_overhead_pct") is not None for r in rr) else None,
        }
    return out

def paired(runs, prefix):
    pairs = {}
    for r in runs:
        m = re.match(rf"^{prefix}(\d+)_(cubic|bbr)$", r["tag"])
        if not m:
            continue
        pairs.setdefault(int(m.group(1)), {})[m.group(2)] = r
    rows = []
    for p in sorted(pairs):
        d = pairs[p]
        if "cubic" in d and "bbr" in d:
            c, b = d["cubic"]["mbps"], d["bbr"]["mbps"]
            if c and b:
                rows.append({"pair": p, "cubic": c, "bbr": b, "delta_pct": 100 * (b - c) / c})
    if rows:
        ds = [x["delta_pct"] for x in rows]
        return {"rows": rows, "median_delta_pct": st.median(ds),
                "geomean_ratio": math.exp(st.mean([math.log(x["bbr"] / x["cubic"]) for x in rows]))}
    return {"rows": rows}

if __name__ == "__main__":
    phases = [("r2bulk", "bulk"), ("r2multi", "multi"), ("r2ul", "ul")]
    result = {"summaries": [], "paired": {}}
    for prefix, label in phases:
        runs = [r for r in phase_runs(prefix) if r.get("direct") is not False]
        s = summarize(runs, label)
        result["summaries"].append(s)
        result["paired"][label] = paired(runs, prefix)
        print(f"\n=== {label} (n={len(runs)} runs) ===")
        for cc in ("cubic", "bbr"):
            if cc in s:
                d = s[cc]
                print(f"{cc:5s} n={d['n']} mbps median={fmt(d['mbps_median'])} mean={fmt(d['mbps_mean'])} sd={fmt(d['mbps_stdev'])} "
                      f"[{fmt(d['mbps_min'])}-{fmt(d['mbps_max'])}] body_med={fmt(d['body_mbps_median'])} "
                      f"retr%={fmt(d['retrans_pct_median'])} ping_n={fmt(d['ping_n_median'],0)} ping_loss%={fmt(d['ping_loss_median'],1)} "
                      f"ping50={fmt(d['ping_p50_median'],1)} ping95={fmt(d['ping_p95_median'],1)} "
                      f"deliv={fmt(d['delivery_mbps_median'])} cwnd={fmt(d['cwnd_segs_median'],1)} rtt={fmt(d['rtt_ms_median'],1)} "
                      f"rxOH%={fmt(d['rx_overhead_pct_median'],1)} txOH%={fmt(d['tx_overhead_pct_median'],1)} "
                      f"RSRP={fmt(d['rsrp_median'],0)}[{fmt(d['rsrp_min'],0)}..{fmt(d['rsrp_max'],0)}] SINR={fmt(d['sinr_median'],0)}\n"
                      f"      sample-rate med={fmt(d['rate_med_median'])} p10={fmt(d['rate_p10_median'])} p90={fmt(d['rate_p90_median'])} "
                      f"cv={fmt(d['rate_cv_median'],2)} first2s={fmt(d['rate_first2_median'])} last5s={fmt(d['rate_last5_median'])} lost={fmt(d['lost_sum_median'],0)}")
        pr = result["paired"][label]
        if pr.get("rows"):
            print(f"paired median delta={pr['median_delta_pct']:+.1f}%  geomean ratio BBR/CUBIC={pr['geomean_ratio']:.3f}")
            for row in pr["rows"]:
                print(f"  pair{row['pair']}: cubic {row['cubic']:.2f} vs bbr {row['bbr']:.2f} Mbps -> {row['delta_pct']:+.1f}%")
    print("\n=== SUMMARY (r2 only, direct runs) ===")
    pooled = {}
    for label, pfs in (("bulk", ["r2bulk"]), ("multi", ["r2multi"]), ("ul", ["r2ul"])):
        runs, rows = [], []
        for pf in pfs:
            rr = [r for r in phase_runs(pf) if r.get("direct") is not False]
            runs += rr
            rows += paired(rr, pf)["rows"]
        s = summarize(runs, label)
        pooled[label] = {"summary": s, "pairs": rows}
        print(f"\n--- {label} pooled (n={len(runs)}) ---")
        for cc in ("cubic", "bbr"):
            if cc in s:
                d = s[cc]
                print(f"{cc:5s} n={d['n']} mbps median={fmt(d['mbps_median'])} mean={fmt(d['mbps_mean'])} "
                      f"sd={fmt(d['mbps_stdev'])} [{fmt(d['mbps_min'])}-{fmt(d['mbps_max'])}] "
                      f"retr%={fmt(d['retrans_pct_median'])} ping50={fmt(d['ping_p50_median'],1)} "
                      f"ping95={fmt(d['ping_p95_median'],1)} rate_med={fmt(d['rate_med_median'])} "
                      f"rxOH%={fmt(d['rx_overhead_pct_median'],1)} txOH%={fmt(d['tx_overhead_pct_median'],1)}")
        if rows:
            ds = [x["delta_pct"] for x in rows]
            print(f"paired n={len(rows)} median delta={st.median(ds):+.1f}%  "
                  f"geomean ratio={math.exp(st.mean([math.log(x['bbr']/x['cubic']) for x in rows])):.3f}  "
                  f"bbr wins {sum(1 for x in rows if x['delta_pct']>0)}/{len(rows)}")
    result["pooled"] = pooled

    print("\n--- per-run detail ---")
    for prefix, label in phases:
        for r in phase_runs(prefix):
            pl = r["ping_load"]
            extra = ""
            if r["mode"] == "multi":
                extra = " streams=[" + ",".join(f"{fmt(x['speed']*8/1e6,1)}Mbps" for x in r.get("streams", [])) + "]"
            print(f"{r['tag']:14s} {r['mode']:5s} {fmt(r['mbps']):>6s} Mbps wall={fmt(r.get('wall'),1):>5s}s "
                  f"ttfb={fmt(r.get('TTFB'),2):>5s}s cwnd={fmt(r.get('cwnd_segs_med'),0):>5s} deliv={fmt(r.get('delivery_mbps_med'))} "
                  f"rtt={fmt(r.get('rtt_ms_med'),0)} smpmed={fmt(r.get('rate_med'))} cv={fmt(r.get('rate_cv'),2)} "
                  f"ping(n={pl.get('n')},p50={fmt(pl.get('p50'),0)},p95={fmt(pl.get('p95'),0)},"
                  f"max={fmt(pl.get('max'),0)}) retr%={fmt(r['tcp'].get('retrans_pct'),2)} "
                  f"rxOH%={fmt(r.get('rx_overhead_pct'),1)} txOH%={fmt(r.get('tx_overhead_pct'),1)} "
                  f"cc={r.get('cc_seen')} direct={r.get('direct')}{extra}")

    with open(os.environ.get("BENCH_OUT", "results.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print("\nwrote results.json")
